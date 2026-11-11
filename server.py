"""FastAPI service for the Agent Copilot prototype.

Run:  python server.py          then open http://localhost:8000
Env:  COPILOT_BACKEND=mlx|ollama|claude|extractive   (default from config.GEN_BACKEND)

Endpoints
  GET  /api/policies          loaded policies (+ agent uploads)
  GET  /api/status            backend and warm-up state
  POST /api/ask               {"question", "policy_id"} -> server-sent events (meta, token, final)
  GET  /api/compare?q=...     comparison from the fact table
  POST /api/upload            multipart PDF -> hybrid RAG index (target <10s when warm)
  GET  /api/uploads           list agent-uploaded wordings
  GET  /pdf/{policy_id}       the policy PDF (cards link to #page=N)
"""
import csv
import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import MANIFEST, POLICY_DIR, ROOT
from generate import get_generator

app = FastAPI(title="Claviq Agent Copilot (prototype)")

# One worker thread owns the embedding model and the LLM (MLX is not thread safe across threads).
WORKER = ThreadPoolExecutor(max_workers=1)
STATE = {"ready": False, "backend": None, "error": None}
GEN = {"gen": None}


def _manifest_policies() -> list[dict]:
    rows = list(csv.DictReader(open(MANIFEST, encoding="utf-8")))
    return [{"policy_id": r["policy_id"], "insurer": r["insurer"], "product": r["product"], "source": "catalogue"}
            for r in rows if (POLICY_DIR / r["file"]).exists()]


def _policies() -> list[dict]:
    from upload_index import list_uploads
    out = _manifest_policies()
    for u in list_uploads():
        out.append({
            "policy_id": u["policy_id"],
            "insurer": u.get("insurer") or "Uploaded",
            "product": u.get("product") or u.get("filename"),
            "source": "upload",
            "n_pages": u.get("n_pages"),
            "ms": u.get("ms"),
        })
    return out


def _warm():
    try:
        gen = get_generator()
        GEN["gen"] = gen
        STATE["backend"] = gen.name
        from retrieve import search
        pols = _manifest_policies()
        if pols:
            search("room rent limit", pols[0]["policy_id"], 1)  # loads bge-m3 and Chroma
        if hasattr(gen, "_load"):
            gen._load()
            for _ in gen.stream("Reply with OK.", "OK?"):
                break
        _wording_payload()  # read every wording's facts once (cached on disk by index fingerprint)
        STATE["ready"] = True
    except Exception as e:  # surface in /api/status rather than crash
        STATE["error"] = repr(e)
        print("[warm] failed:", e)


@app.on_event("startup")
def startup():
    WORKER.submit(_warm)


class Ask(BaseModel):
    question: str
    policy_id: str


@app.get("/api/policies")
def policies():
    return _policies()


@app.get("/api/status")
def status():
    return STATE


@app.get("/api/uploads")
def uploads():
    from upload_index import list_uploads, product_card
    return [product_card(u) for u in list_uploads()]


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    product: str | None = Form(None),
    insurer: str | None = Form(None),
):
    """Agent drops a policy PDF. Hybrid-index it into Chroma and return a product card.

    Runs on the same worker that owns the embedding model so it shares the warm bge-m3.
    Target: under 10 seconds once the server is ready.
    """
    if not STATE["ready"]:
        raise HTTPException(503, "Models still warming up — try again in a few seconds.")
    name = file.filename or "wording.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF policy wording.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")

    result: dict = {}
    err: list = []

    def job():
        try:
            from upload_index import ingest_upload, product_card
            meta = ingest_upload(
                data,
                name,
                product=product,
                insurer=(insurer or "Uploaded").strip() or "Uploaded",
            )
            result["meta"] = meta
            result["product"] = product_card(meta)
        except Exception as e:
            err.append(e)

    fut = WORKER.submit(job)
    fut.result()  # block this request until the index write finishes
    if err:
        raise HTTPException(400, str(err[0]))
    meta = result["meta"]
    return {
        "ok": True,
        "under_10s": meta["ms"] < 10000,
        "ms": meta["ms"],
        "policy_id": meta["policy_id"],
        "n_pages": meta["n_pages"],
        "n_chunks": meta["n_chunks"],
        "product": result["product"],
        "message": (
            f"Scanned {meta['n_pages']} pages into hybrid RAG in {meta['ms']} ms. "
            "Ask in English, Hindi or Hinglish — answers cite page and clause."
        ),
    }


def _audit(req: Ask, ev: dict) -> None:
    try:
        log_path = ROOT / "data" / "audit.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "policy_id": req.policy_id,
                "question": req.question.strip(),
                "status": ev.get("status"),
                "backend": ev.get("backend"),
                "ms": ev.get("ms"),
                "cached": bool(ev.get("cached")),
                "citations": [
                    {"page": c.get("page"), "printed_page": c.get("printed_page"), "section": c.get("section")}
                    for c in (ev.get("citations") or [])
                ],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


@app.post("/api/ask")
def ask(req: Ask):
    if not req.question.strip():
        raise HTTPException(400, "empty question")
    sse = lambda ev: f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    if GEN["gen"]:
        from answer import cached_final
        hit = cached_final(req.question.strip(), req.policy_id, GEN["gen"].name)
        if hit:
            hit["ms"] = 0
            _audit(req, hit)
            meta = {"type": "meta", "lang": hit.get("lang"), "route": "cache", "backend": GEN["gen"].name}
            return StreamingResponse(iter([sse(meta), sse(hit)]), media_type="text/event-stream", headers=headers)

    q: queue.Queue = queue.Queue()
    done = object()

    def job():
        from answer import ask_stream
        try:
            for ev in ask_stream(req.question.strip(), req.policy_id, GEN["gen"] or get_generator()):
                if ev.get("type") == "final":
                    _audit(req, ev)
                q.put(ev)
        except Exception as e:
            q.put({"type": "final", "status": "error", "answer": f"Something went wrong: {e}", "citations": []})
        finally:
            q.put(done)

    WORKER.submit(job)

    def events():
        while True:
            ev = q.get()
            if ev is done:
                break
            yield sse(ev)

    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


_KITS: dict = {}


def _wording_payload() -> dict:
    """Per product: the facts read from its wording (wording.py) and its close kit (papers from the wording).
    The chat uses these in place of the catalogue's fact table."""
    import sys as _sys
    from products import registry
    from wording import all_facts, text_pages
    _sys.path.insert(0, str(ROOT / "ingest"))
    from build_kit import kit_for
    reg = registry()
    facts = all_facts([p["policy_id"] for p in reg])
    out = {}
    for p in reg:
        w = facts.get(p["policy_id"], {})
        if p["policy_id"] not in _KITS:
            pages = text_pages(p["policy_id"])
            _KITS[p["policy_id"]] = kit_for(pages, w.get("page_offset")) if pages else {}
        out[p["id"]] = {"facts": w.get("facts", {}), "close_kit": _KITS[p["policy_id"]],
                        "uin_from_wording": (w.get("uin") or {}).get("value")}
    return out


@app.get("/api/wording-facts")
def wording_facts():
    if not STATE["ready"]:
        raise HTTPException(503, "Models still warming up.")
    return WORKER.submit(_wording_payload).result()


class Need(BaseModel):
    text: str
    mine: list[str] = []


@app.post("/api/recommend")
def recommend(req: Need):
    """Need-first ranking from the wording (rank.py): parse the need, then rank on it."""
    if not req.text.strip():
        raise HTTPException(400, "empty need")
    from rank import recommend as _rec
    return WORKER.submit(_rec, req.text.strip(), req.mine).result()


@app.get("/api/premium")
def premium(product: str, age: int | None = None, city: str | None = None, family: str = "1A"):
    """Base premium from the insurer's official rate chart (premium.py). Never from the wording."""
    from premium import lookup
    return lookup(product, age, city, family)


@app.get("/api/compare")
def compare(q: str):
    from answer import detect_lang
    from compare import compare_answer
    return compare_answer(q, detect_lang(q))


def _pdf_file(policy_id: str):
    from upload_index import pdf_path_for
    up = pdf_path_for(policy_id)
    if up is not None:
        return up
    for r in csv.DictReader(open(MANIFEST, encoding="utf-8")):
        if r["policy_id"] == policy_id:
            return POLICY_DIR / r["file"]
    raise HTTPException(404)


@app.get("/pdf/{policy_id}")
def pdf(policy_id: str):
    f = _pdf_file(policy_id)
    return FileResponse(f, media_type="application/pdf", filename=f.name)


# ---------- citation viewer: the cited PDF page with the exact quoted line highlighted ----------

def _quote_hits(page, quote: str):
    """Rectangles on the page that hold the quote. Quotes can be cut or interleaved (two-column pages), so the
    quote is searched in overlapping 6-word pieces; every piece found on the page is highlighted."""
    import re as _re
    words = _re.findall(r"[\w’'/%.,()-]+", (quote or "").replace("…", " ").replace("...", " "))
    words = [w.strip(".,") for w in words if w.strip(".,")]
    if not words:
        return []
    # 1. the whole quote as one phrase (PyMuPDF matches across line breaks)
    whole = page.search_for(" ".join(words), quads=False)
    if whole:
        return whole
    # 2. pieces, kept only inside ONE passage: anchored on the first piece found, limited to the quote's length,
    #    so a similar sentence in the next clause is never highlighted
    pieces = []
    for n in (6, 4):
        for i in range(0, max(1, len(words) - n + 1), 2 if n == 6 else 1):
            piece = " ".join(words[i:i + n])
            if len(piece) >= 12:
                pieces.append((i, piece))
    found = []
    for i, piece in pieces:
        for r in page.search_for(piece):
            found.append((i, r))
    if not found:
        return []
    found.sort(key=lambda t: (t[0], t[1].y0))
    anchor = found[0][1]
    span = 16 * (len(" ".join(words)) / 55 + 2)          # about one text line per 55 characters, plus slack
    hits = []
    for _, r in found:
        if anchor.y0 - 3 <= r.y0 <= anchor.y0 + span and \
           not any(abs(r.y0 - h.y0) < 1 and abs(r.x0 - h.x0) < 1 for h in hits):
            hits.append(r)
    return hits


def _clause_heading(page, y: float) -> str | None:
    """The numbered clause heading nearest above the highlight, e.g. '3.1.1.1 Pre-Existing Disease- Excl 01'."""
    import re as _re
    best = None
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            t = " ".join(s["text"] for s in line["spans"]).strip()
            y0 = line["bbox"][1]
            if y0 <= y + 1 and _re.match(r"^(\d+(\.\d+)+|[IVX]+\.|[A-Z]\.)\s+\S", t) and len(t) < 120:
                if best is None or y0 > best[0]:
                    best = (y0, t)
    return best[1] if best else None


def _cite_render(policy_id: str, page_no: int, quote: str):
    import fitz
    doc = fitz.open(_pdf_file(policy_id))
    try:
        if not 1 <= page_no <= doc.page_count:
            raise HTTPException(404, "page out of range")
        page = doc[page_no - 1]
        hits = _quote_hits(page, quote)
        for r in hits:
            a = page.add_highlight_annot(r)
            a.set_colors(stroke=(1, 0.86, 0.2)); a.update()
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True)
        first = min((r.y0 for r in hits), default=None)
        heading = _clause_heading(page, first) if first is not None else None
        return pix.tobytes("png"), len(hits), (first / page.rect.height if first is not None else None), heading
    finally:
        doc.close()


@app.get("/api/cite-heading")
def cite_heading(policy_id: str, page: int, q: str = ""):
    """The numbered clause heading above the quoted line on that page, and whether the line was found."""
    import fitz
    doc = fitz.open(_pdf_file(policy_id))
    try:
        if not 1 <= page <= doc.page_count:
            return {"found": False, "heading": None}
        pg = doc[page - 1]
        hits = _quote_hits(pg, q)
        y = min((r.y0 for r in hits), default=None)
        return {"found": bool(hits), "heading": _clause_heading(pg, y) if y is not None else None}
    finally:
        doc.close()


@app.get("/cite/{policy_id}/page.png")
def cite_png(policy_id: str, page: int, q: str = ""):
    from fastapi.responses import Response
    png = _cite_render(policy_id, page, q)[0]
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-cache"})


@app.get("/cite/{policy_id}")
def cite(policy_id: str, page: int, q: str = "", s: str = "", product: str = ""):
    """A page an agent can open from any citation: product, rule, PDF page, the exact line, and the real PDF
    page with that line highlighted. Links to the full PDF at the same page."""
    from html import escape as h
    from urllib.parse import quote as uq
    from fastapi.responses import HTMLResponse
    _, n, top, heading = _cite_render(policy_id, page, q)
    s = heading or s   # the numbered clause heading read from the page beats a coarse section label
    img = f"/cite/{uq(policy_id)}/page.png?page={page}&q={uq(q)}&v=2"
    found = (f"The quoted line is highlighted in yellow on PDF page {page}." if n
             else f"The exact words could not be matched on the page image (the PDF text layer may be split); "
                  f"read the quote above against PDF page {page}.")
    mark = f'<div id="hit" style="position:absolute;left:0;top:{top * 100:.2f}%"></div>' if top is not None else ""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PDF page {page} · {h(product or policy_id)}</title>
<style>:root{{--bg:#f4f6f9;--ink:#13202b;--mut:#5a6b7b;--line:#d9e0e7;--acc:#0b4f9c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1720;--ink:#e6edf3;--mut:#9fb0c0;--line:#2a3a4a;--acc:#6fb1ff}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;z-index:2}}
h1{{font-size:16px;margin:0 0 4px}} .m{{color:var(--mut);font-size:13px}} a{{color:var(--acc)}}
blockquote{{margin:8px 0 0;padding:8px 12px;border-left:4px solid #f5c518;background:rgba(245,197,24,.12)}}
.pg{{position:relative;max-width:900px;margin:16px auto;padding:0 16px}} .pg img{{width:100%;border:1px solid var(--line);background:#fff}}</style></head>
<body><header><h1>{h(product or policy_id)} — PDF page {page}{(' · ' + h(s)) if s else ''}</h1>
<div class="m">{h(found)} <a href="/pdf/{uq(policy_id)}#page={page}" target="_blank" rel="noopener">Open the full PDF at page {page} ↗</a></div>
{f'<blockquote>“{h(q)}”</blockquote>' if q else ''}</header>
<div class="pg">{mark}<img src="{img}" alt="PDF page {page} with the quoted line highlighted"></div>
<script>addEventListener('load',()=>{{const m=document.getElementById('hit');if(m)window.scrollTo(0,m.getBoundingClientRect().top+scrollY-document.querySelector('header').offsetHeight-60)}})</script>
</body></html>""")


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
