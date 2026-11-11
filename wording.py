"""Facts read from the policy wording itself: retrieve the clause, then parse it.

Nothing here is typed by hand and nothing comes from web/data/catalogue.json. For every
product and every field:

  1. hybrid search (BGE-M3 + BM25) over that product's clauses only, with a field query;
  2. the field parser reads the retrieved pages in rank order and returns the first clause
     that states the value, with its verbatim quote, PDF page, printed page and rule number;
  3. if the retrieved pages do not state it, the rest of that product's indexed clauses are
     read; if nothing states it, the field is "not stated" with the reason. Never a guess.

The page text is rebuilt from the indexed chunks (data/chroma), so a re-ingest of a wording
changes these facts on the next build (the cache key is the index fingerprint).

    python wording.py                 # build all, print a coverage table
    python wording.py qi_hdfc_ergo_optima_secure_plus_plan   # one product, full detail
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
import threading
from pathlib import Path

from config import ROOT

sys.path.insert(0, str(ROOT / "ingest"))
import build_facts as bf  # noqa: E402  (parsers only; its catalogue writer is never called)

CACHE = ROOT / "data" / "wording_facts.json"
VERSION = 22  # bump when a parser changes, so cached facts are rebuilt
_LOCK = threading.Lock()
_MEM: dict | None = None

# field -> retrieval query (the clause we want to read)
QUERIES = {
    "ped_wait_months": "pre-existing disease waiting period expenses excluded until the expiry of months of continuous coverage Code Excl01",
    "specified_disease_wait_months": "specified disease procedure listed conditions waiting period excluded until expiry months Code Excl02",
    "initial_wait": "30 days waiting period first policy commencement date excluded except accident Code Excl03",
    "entry_age_max": "eligibility entry age minimum maximum years adult",
    "room_rent": "room rent limit room category single private room per day at actuals",
    "maternity": "maternity expenses childbirth delivery caesarean waiting period months exclusion Code Excl18",
    "joint_replacement": "joint replacement knee replacement surgery waiting period listed conditions",
    "sum_insured": "sum insured options available lakh crore",
    "ayush": "AYUSH treatment in-patient Ayurveda Unani Siddha Homeopathy sum insured",
    "cataract": "cataract limit per eye waiting period",
    "robotic_surgery": "robotic surgery modern treatment methods advancement",
    "exclusions": "permanent exclusions Code Excl expenses not covered",
}


# ── page text rebuilt from the index ───────────────────────────────────

def _col():
    from retrieve import _load
    return _load()[1]


def index_pages(policy_id: str) -> tuple[list[dict], int | None]:
    """[{page, text}] for one policy, rebuilt from its indexed chunks, and the printed-page offset."""
    res = _col().get(where={"policy_id": policy_id}, include=["documents", "metadatas"])
    by = collections.defaultdict(list)
    offs = collections.Counter()
    for doc, m in zip(res["documents"], res["metadatas"]):
        by[m["page"]].append((m.get("chunk_idx", 0), doc))
        if m.get("printed_page") is not None:
            offs[m["printed_page"] - m["page"]] += 1
    pages = [{"page": pg, "text": "\n".join(d for _, d in sorted(v))} for pg, v in sorted(by.items())]
    off = offs.most_common(1)[0][0] if offs else None
    return pages, off


def text_pages(policy_id: str) -> list[dict]:
    """The same wording as extracted by pdftotext (data/pages), which keeps column reading order.
    Used when the indexed text of a two-column page is interleaved and a clause cannot be read."""
    import csv
    for man in ("manifest.csv", "quickinsure_manifest.csv"):
        path = ROOT / "data" / "policies" / man
        if not path.exists():
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if r.get("policy_id") == policy_id and r.get("file"):
                f = ROOT / "data" / "pages" / (Path(r["file"]).stem + ".json")
                if f.exists():
                    return [{"page": p["page"], "text": p["text"]} for p in json.loads(f.read_text(encoding="utf-8"))["pages"]]
    return []


def retrieved_order(policy_id: str, field: str, pages: list[dict], k: int = 10) -> tuple[list[dict], set[int]]:
    """Pages in retrieval rank for this field, then the rest. Returns (ordered pages, retrieved page set)."""
    from retrieve import hybrid
    hits = hybrid(QUERIES[field], policy_id, k)
    order, seen = [], set()
    for h in hits:
        if h["page"] not in seen:
            seen.add(h["page"])
            order.append(h["page"])
    by = {p["page"]: p for p in pages}
    first = [by[n] for n in order if n in by]
    rest = [p for p in pages if p["page"] not in seen]
    return first + rest, seen


# ── extra parsers (fields build_facts does not read) ──────────────────

AGE_MORE = [
    re.compile(r"maximum entry age[^.]{0,60}?(\d{2})\s*years", re.I),
    re.compile(r"entry age[^.]{0,80}?(?:18|eighteen)\s*(?:years|yrs)?\s*(?:to|-|–|and)\s*(\d{2})\s*(?:years|yrs)", re.I),
    re.compile(r"adults?[^.]{0,40}?(?:18|eighteen)\s*(?:years|yrs)?\s*(?:to|-|–)\s*(\d{2})\s*(?:years|yrs)", re.I),
    re.compile(r"entry age[^.]{0,40}?(?:up\s?to|upto|maximum of)\s*(\d{2})\s*years", re.I),
    re.compile(r"(?:maximum|max\.?)\s*(?:age|entry age)\s*(?:at entry)?\s*[:|]?\s*(\d{2})\s*(?:years|yrs)", re.I),
]
NO_LIMIT = re.compile(r"no (?:limit on |upper |bar on )?maximum entry(?: age)?|no maximum entry age(?: limit)?|"
                      r"maximum entry age[^.]{0,40}?(?:no limit|no bar|lifelong|no upper)|no upper (?:entry )?age limit|"
                      r"no maximum age (?:limit )?(?:for|at) entry|"
                      # benefit tables: "Maximum Entry Age | Child - 25 years ... Adult – No limit",
                      # "Entry Age (Adult) | Minimum - 18 Years, Maximum - No capping"
                      r"maximum entry age\s*\|.{0,120}?adult\s*[–-]\s*no (?:limit|capping)|"
                      r"ent\w* age \(adult\)\s*\|[^|]{0,40}?maximum\s*[-–]\s*no (?:limit|capping)", re.I)


def entry_age(pages, off):
    f = bf.entry_age(pages, off)
    if f["stated"]:
        return f
    for p in pages:
        t = bf.squash(p["text"])
        m = NO_LIMIT.search(t)
        if m:
            q, a = bf.window(t, m.start(), m.end(), before=60, max_len=260)
            return bf.fact(pages, p["page"], t, q, a, "No upper limit", "text", off,
                           "The wording sets no maximum entry age for adults.")
        for rx in AGE_MORE:
            for m in rx.finditer(t):
                n = int(m.group(1))
                ctx = t[max(0, m.start() - 80):m.end() + 40]
                if not (45 <= n <= 99) or re.search(r"child|dependent|renewal|exit|cease|son|daughter", ctx, re.I):
                    continue
                q, a = bf.window(t, m.start(), m.end(), before=60, max_len=260)
                return bf.fact(pages, p["page"], t, q, a, n, "years", off)
    return bf.missing("Maximum entry age is not stated in this wording.")


PED_CODE = re.compile(r"Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?1\b", re.I)
PED_TABLE = re.compile(r"pre-?\s?existing (?:diseases?|conditions?|illness(?:es)?)\s*(?:\(PED\))?\s*[:|–-]\s*(\d{1,2})\s*months", re.I)
PED_LOOSE = re.compile(r"pre-?\s?existing (?:diseases?|conditions?)[^.]{0,220}?(\d{1,2})\s*(?:\(\s*\w+\s*\)\s*)?months of continuous", re.I)
LOOKBACK = re.compile(r"within\s+\d+\s*months\s+(?:prior|before)|prior to (?:the )?(?:effective date|first policy)|"
                      r"\bmeans any condition", re.I)


def ped(pages, off):
    """build_facts' parser first; then the Code-Excl01 clause, a summary table, or a looser sentence.
    The PED definition's look-back period ("within 48 months prior") is never read as the wait."""
    # one candidate per page, in retrieval order; an optional cover ("if opted ... the exclusions shall
    # read as follows") is set aside, and between base-plan variants (24 / 48) the longer is used
    cands = []
    for p in pages:
        g = bf.ped([p], off)
        if not g["stated"]:
            continue
        t = bf.squash(p["text"])
        pos = t.find(g["quote"][:40])
        # the optional-cover heading ("Buy Back of Pre-Existing Disease Waiting Period") can open the page
        # well before its "exclusions shall read as follows" clause, so the whole page above counts
        optional = bool(bf.OPTIONAL_CTX.search(t[max(0, pos - 2500):max(pos, 0)])) if pos >= 0 else False
        cands.append((optional, g))
    optional_only = None
    if cands and all(o for o, _ in cands):
        optional_only, cands = cands[0][1], []  # try the fallbacks for the base clause first
    if cands:
        base = [g for o, g in cands if not o]
        vals = sorted({g["value"] for g in base})
        best = next(g for g in base if g["value"] == vals[-1])
        if len(vals) > 1:
            best["note"] = (f"This wording states {', '.join(map(str, vals))} months for different plans. "
                            f"The longest, {vals[-1]} months, is used for ranking; check the plan in the schedule.")
        return best
    f = bf.missing("No pre-existing disease waiting clause found in this wording.")
    for rx in (PED_CODE, PED_TABLE, PED_LOOSE):
        for p in pages:
            t = bf.squash(p["text"])
            for m in rx.finditer(t):
                if rx is PED_CODE:
                    seg = t[m.end():m.end() + 520]
                    cut = bf.SEG_END.search(seg) or re.search(r"Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?2", seg, re.I)
                    seg = seg[:cut.start()] if cut else seg
                    ms = [x for x in bf.months_in(seg) if 6 <= x <= 60]
                    if not ms:  # two columns interleaved: "expiry of 36 of management for Kidney ... months of continuous"
                        m2 = re.search(r"expiry of (\d{2})\b.{0,90}?months of continuous", seg)
                        ms = [int(m2.group(1))] if m2 and 6 <= int(m2.group(1)) <= 60 else []
                    end = m.end() + len(seg)
                else:
                    ms = [int(m.group(1))] if 6 <= int(m.group(1)) <= 60 else []
                    seg, end = m.group(0), m.end()
                if not ms or LOOKBACK.search(t[max(0, m.start() - 80):end]):
                    continue
                if bf.OPTIONAL_CTX.search(t[max(0, m.start() - 900):m.start()]):
                    continue
                q, a = bf.window(t, m.start(), min(end, m.start() + 400), before=10, max_len=420)
                note = (f"Depends on the plan chosen: {', '.join(map(str, sorted(set(ms))))} months. "
                        "The longest is used for ranking.") if len(set(ms)) > 1 else None
                return bf.fact(pages, p["page"], t, q, a, max(ms), "months", off, note)
    if optional_only:
        # the standard Code-Excl01 sentence is the base exclusion even when an optional cover is described
        # higher up the same page; only a quote that itself rewrites the wait ("if opted", "shall read as follows")
        # is the optional cover
        if re.search(r"Excl\s*0?1", optional_only.get("section") or "", re.I) and \
           not re.search(r"read as follows|if opted|opted for|optional|buy\s*back|in lieu", optional_only.get("quote") or "", re.I):
            return optional_only
        optional_only["note"] = "Only an optional cover's wait was found; the base wait is not readable in this wording."
        optional_only["optional"] = True
        return optional_only
    return f


INITIAL_RE = re.compile(r"(?:within|during) (?:the )?(?:first )?(?:30|thirty) days (?:from|of) (?:the )?(?:first )?(?:policy )?"
                        r"(?:commencement|inception|start)|first (?:30|thirty) days|initial waiting period of (?:30|thirty) days", re.I)


def initial_wait(pages, off):
    for p in pages:
        t = bf.squash(p["text"])
        m = INITIAL_RE.search(t)
        if m:
            q, a = bf.window(t, m.start(), m.end(), before=120, max_len=300)
            return bf.fact(pages, p["page"], t, q, a, 30, "days", off)
    return bf.missing("No 30-day initial waiting clause found in this wording.")


ADDON_RE = re.compile(r"\badd[- ]?ons?\b|optional (?:cover|benefit)|\bif opted\b|opted for|on payment of (?:an )?(?:additional|extra) premium", re.I)
MAT_RE = re.compile(r"\bmaternity\b|childbirth|child birth|delivery of (?:a|the) (?:baby|child)", re.I)
EXCL_NEAR = re.compile(r"Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?(?:1[0-9]|[4-9]|[2-9]\d)\b|shall be excluded|are excluded|is excluded|"
                       r"not covered|shall not (?:be )?(?:liable|covered|payable)", re.I)


_N = r"(?:\d{1,2}|one|two|three|four|six|nine|twelve|twenty[- ]four|thirty[- ]six|forty[- ]eight|seventy[- ]two)"
MAT_WAIT = re.compile(rf"(?:waiting period of|until|after|for a period of|expiry of|covered for)\s*({_N}\s*(?:\(\s*\w+\s*\)\s*)?(?:months|years?))|"
                      rf"\[?({_N}\s*(?:months|years?))\s*(?:waiting period|of continuous)", re.I)
WORD_MONTHS = {"one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "nine": 9, "twelve": 12, "twenty four": 24,
               "twenty-four": 24, "thirty six": 36, "thirty-six": 36, "forty eight": 48, "forty-eight": 48,
               "seventy two": 72, "seventy-two": 72}
OPT_LOOSE = re.compile(r"\(\s*optional\b|optional\s+(?:benefit|cover)|benefit[^.]{0,40}has been opted|if opted|add[- ]?on", re.I)


def _mat_months(win: str) -> list[int]:
    """Month counts written as a maternity wait ("waiting period of 24 months", "[Thirty six months waiting period]"),
    not stray counts from a neighbouring column ("Continuous Coverage for more than twelve months")."""
    out = []
    for m in MAT_WAIT.finditer(win):
        s = (m.group(1) or m.group(2)).lower()
        n = re.match(r"\d+", s)
        v = int(n.group(0)) if n else next((v for w, v in sorted(WORD_MONTHS.items(), key=lambda x: -len(x[0])) if s.startswith(w)), None)
        if v and re.search(r"years?$", s):
            v *= 12
        if v and 6 <= v <= 72 and not re.search(r"more than\s*$", win[max(0, m.start() - 12):m.start()], re.I):
            out.append(v)
    return out


def maternity(pages, off):
    """covered after N months (base) / only with an add-on or optional cover / excluded / not stated."""
    excl = cover = addon = None
    for p in pages:
        t = bf.squash(p["text"])
        for m in MAT_RE.finditer(t):
            win = t[m.start():m.end() + 220]  # the wait follows the word ("Maternity ... until 24 months")
            q, a = bf.window(t, m.start(), m.end(), before=120, max_len=320)
            if re.search(r"\bmeans\b|is defined", q, re.I):
                continue
            months = _mat_months(win)
            is_excl = bool(EXCL_NEAR.search(t[max(0, m.start() - 160):m.end() + 200])) and not months
            is_addon = bool(ADDON_RE.search(t[max(0, m.start() - 500):m.end() + 300]) or
                            OPT_LOOSE.search(t[max(0, m.start() - 500):m.end() + 300]))
            if is_excl and excl is None:
                excl = (p["page"], t, q, a)
            elif months and is_addon and addon is None:
                addon = (p["page"], t, q, a, max(months))
            elif months and not is_addon and cover is None:
                cover = (p["page"], t, q, a, max(months))
    if cover:
        pg, t, q, a, n = cover
        return bf.fact(pages, pg, t, q, a, f"Covered after a {n}-month waiting period", "text", off,
                       kind="covered", months=n)
    if addon:
        pg, t, q, a, n = addon
        note = "The base policy excludes maternity." if excl else None
        return bf.fact(pages, pg, t, q, a, f"Only with an optional cover / add-on, after {n} months", "text", off, note,
                       kind="addon", months=n)
    if excl:
        pg, t, q, a = excl
        return bf.fact(pages, pg, t, q, a, "Excluded — maternity is not covered", "text", off, kind="excluded", months=None)
    return bf.missing("Maternity cover is not written in this wording.")


JOINT_RE = re.compile(r"joint replacement|knee replacement|replacement of (?:the )?(?:knee|hip|joint)|arthroplasty", re.I)


SPEC_CODE = re.compile(r"Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?2\b", re.I)
SPEC_TABLE = re.compile(r"speci(?:fic|fied) (?:disease[s/ ]*(?:procedure)?\s*)?waiting period\s*[:|–-]\s*(\d{1,2})\s*months", re.I)


def specified(pages, off):
    """build_facts' parser first; then the Code-Excl02 clause or a summary-table line."""
    f = bf.specified(pages, off)
    if f["stated"]:
        return f
    for rx in (SPEC_CODE, SPEC_TABLE):
        for p in pages:
            t = bf.squash(p["text"])
            for m in rx.finditer(t):
                if rx is SPEC_CODE:
                    seg = t[m.end():m.end() + 420]
                    cut = re.search(r"Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?3|List of", seg, re.I)
                    seg = seg[:cut.start()] if cut else seg
                    ms = [x for x in bf.months_in(seg) if 12 <= x <= 48]
                    end = m.end() + len(seg)
                else:
                    ms = [int(m.group(1))] if 12 <= int(m.group(1)) <= 48 else []
                    end = m.end()
                if not ms:
                    continue
                q, a = bf.window(t, m.start(), min(end, m.start() + 380), before=10, max_len=400)
                note = (f"The list has more than one wait: {', '.join(map(str, sorted(set(ms))))} months; "
                        "check the item.") if len(set(ms)) > 1 else None
                return bf.fact(pages, p["page"], t, q, a, max(ms), "months", off, note)
    return f


def joint_replacement(pages, off, spec):
    """Is joint replacement in the specified-disease waiting list, and for how long."""
    for p in pages:
        t = bf.squash(p["text"])
        for m in JOINT_RE.finditer(t):
            before = t[max(0, m.start() - 2500):m.start()]
            near = t[max(0, m.start() - 300):m.end() + 200]
            if re.search(r"\bmeans\b", near[:200], re.I):
                continue
            listed = re.search(r"listed conditions|specified disease|specific (?:disease|waiting)|named ailment|Excl\s*0?2|waiting period", before, re.I)
            if not listed:
                continue
            own = [x for x in bf.months_in(near) if 12 <= x <= 72]
            months = max(own) if own else (spec["value"] if spec.get("stated") else None)
            q, a = bf.window(t, m.start(), m.end(), before=80, max_len=240)
            note = None if own else "The waiting period is the one for the whole specified-disease list."
            return bf.fact(pages, p["page"], t, q, a, f"In the waiting list: {months} months" if months else "In the waiting list",
                           "text", off, note, months=months, kind="waiting_list")
    return bf.missing("Joint replacement is not named in a waiting list in this wording.")


SI_RE = re.compile(r"sum insured[^.]{0,160}?((?:(?:Rs\.?|₹|INR)\s*)?\d[\d,.]*\s*(?:lakhs?|lacs?|crores?|L\b|Cr\b)[^.]{0,160})", re.I)
AMT = re.compile(r"(\d[\d,.]*)\s*(lakhs?|lacs?|crores?|L\b|Cr\b)", re.I)


def sum_insured(pages, off):
    for p in pages:
        t = bf.squash(p["text"])
        for m in SI_RE.finditer(t):
            amts = AMT.findall(m.group(1))
            if len(amts) < 2 or not re.search(r"option|available|plan|range|from|choose", t[max(0, m.start() - 60):m.end()], re.I):
                continue
            vals = []
            for n, unit in amts:
                try:
                    v = float(n.replace(",", ""))
                except ValueError:
                    continue
                vals.append(v * (100 if unit.lower().startswith(("crore", "cr")) else 1))
            if not vals:
                continue
            q, a = bf.window(t, m.start(), m.end(), before=40, max_len=300)
            lo, hi = min(vals), max(vals)
            fmt = lambda v: f"₹{v / 100:g} crore" if v >= 100 else f"₹{v:g} lakh"
            return bf.fact(pages, p["page"], t, q, a, f"{fmt(lo)} to {fmt(hi)}" if lo != hi else fmt(lo), "text", off,
                           "Options as written; the chosen one is in the Policy Schedule.")
    return bf.missing("Sum insured options are not stated in this wording.")


EXCL_ITEM = re.compile(r"([A-Z][A-Za-z ,/&()'’\-]{3,70}?)\s*[:–\-]?\s*Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?(\d{1,2})", re.I)


def exclusions(pages, off):
    """Named permanent exclusions (Code-Excl04 and above) with their pages."""
    items, first = {}, None
    for p in pages:
        t = bf.squash(p["text"])
        for m in EXCL_ITEM.finditer(t):
            code = int(m.group(2))
            if code < 4 or code in items:
                continue
            name = re.split(r"\b(?:[a-z]\.|[ivx]+\.)\s", m.group(1).strip(" .:-–"))[-1].strip()
            name = re.sub(r"^(?:\d+\.|[a-z]\)|[A-Z]\))\s*", "", name)[:60]
            items[code] = {"code": f"Excl{code:02d}", "name": name, "pdf_page": p["page"],
                           "printed_page": p["page"] + off if off is not None else None}
            if first is None:
                q, a = bf.window(t, m.start(), m.end(), before=20, max_len=200)
                first = (p["page"], t, q, a)
    if not items:
        return bf.missing("No coded exclusion list found in this wording.")
    pg, t, q, a = first
    lst = [items[k] for k in sorted(items)]
    return bf.fact(pages, pg, t, q, a, f"{len(lst)} coded exclusions", "list", off, items=lst)


def uin(pages):
    counts = collections.Counter(bf.UIN_RE.findall(" ".join(p["text"] for p in pages)))
    if not counts:
        return bf.missing("UIN not found in this wording.")
    u = counts.most_common(1)[0][0]
    ver = re.search(r"V(\d{3})\d{3}$", u)
    return {"value": u, "version": f"V{int(ver.group(1)):02d}" if ver else None, "stated": True,
            "note": "Read from the wording text."}


def room_rent(pages, off):
    """The first retrieved page that states the room limit in a sentence; a benefit table
    ("| Room Rent | Covered | Covered |") only when no sentence states it."""
    found = []
    for i, p in enumerate(pages):
        g = bf.room_rent([p], off)
        q = g.get("quote") or ""
        if not g.get("stated") or re.search(r"proportion|admitted in a room that exceeds", q, re.I) or not re.search(r"\broom", q, re.I):
            continue
        table = (g.get("quote") or "").count("|") >= 3
        # a stated limit (at actuals, a rupee cap, a share of SI) beats "as per the schedule"; prose beats a table
        rank = {"no_cap": 0, "per_day": 0, "pct_si": 0, "si_band": 1, "depends": 2, "schedule": 3}.get(g.get("room_class"), 3)
        found.append((table, rank, i, g))
    if not found:
        return bf.missing("Room rent is set in the Policy Schedule; the wording gives no per-day figure.")
    return min(found, key=lambda x: (x[0], x[1], x[2]))[3]


# ── rule number for a quote ────────────────────────────────────────────

RULE_CODE = re.compile(r"(?:([A-Z][A-Za-z/&’' -]{3,60}?)\s*[:\-–]\s*)?\(?Code\s*[-–—]?\s*Ex[ce][l1I]\.?\s*0?(\d{1,2})\)?", re.I)
RULE_NUM = re.compile(r"(?:Section\s+)?\b((?:[A-Z]\s?[-.]\s?)?\d{1,2}(?:\.\d{1,2}){1,3}(?:\.?[a-z]\b)?|[A-Z]\.\d{1,2}(?:\.\d{1,2})*)\.?\s+"
                      r"([A-Z][A-Za-z/&’' -]{3,50}?)(?=[:.|]|\s{2}|\s(?:The|We|This|Expenses|In|If)\b)")


def rule_label(text: str, pos: int) -> str | None:
    """'Pre-Existing Diseases (Code-Excl01)', '1.4 AYUSH Treatment', '1.27 Eligibility' — the heading or code
    that governs the quoted line, read from the text just before it."""
    near = text[max(0, pos - 420):pos + 30]
    code = None
    for m in RULE_CODE.finditer(near):
        name = (m.group(1) or "").strip(" .:-–")
        name = re.split(r"\b(?:[a-z]|[ivx]{1,4})[.)]\s", name)[-1].strip()
        code = (f"{name} (Code-Excl{int(m.group(2)):02d})" if 3 <= len(name) <= 50 else f"Code-Excl{int(m.group(2)):02d}")
    num = None
    for m in RULE_NUM.finditer(text[max(0, pos - 900):pos + 30]):
        head = m.group(2).strip()
        if not re.search(r"^(?:Years?|Months?|Days?|Lakhs?|Page|Rs)\b", head, re.I):
            num = f"{m.group(1).replace(' ', '')} {head}"
    if code and num and code.split(" (")[0].lower() not in num.lower():
        return f"{num} · {code}"
    return code or num


# ── one product ────────────────────────────────────────────────────────

def _safe(fn, *a):
    try:
        return fn(*a)
    except AssertionError as e:  # a quote that does not sit on its page is never shipped
        return bf.missing(f"Parser quote failed its page check: {e}")


def build(policy_id: str) -> dict:
    pages, off = index_pages(policy_id)
    if not pages:
        return {"policy_id": policy_id, "facts": {}, "error": "not in the index"}
    f, retrieved = {}, {}
    order = {}
    for field in QUERIES:
        order[field], retrieved[field] = retrieved_order(policy_id, field, pages)
    f["ped_wait_months"] = _safe(ped, order["ped_wait_months"], off)
    f["specified_disease_wait_months"] = _safe(specified, order["specified_disease_wait_months"], off)
    f["initial_wait"] = _safe(initial_wait, order["initial_wait"], off)
    f["entry_age_max"] = _safe(entry_age, order["entry_age_max"], off)
    f["room_rent"] = _safe(room_rent, order["room_rent"], off)
    f["maternity"] = _safe(maternity, order["maternity"], off)
    f["joint_replacement"] = _safe(joint_replacement, order["joint_replacement"], off, f["specified_disease_wait_months"])
    f["sum_insured"] = _safe(sum_insured, order["sum_insured"], off)
    f["ayush"] = _safe(bf.ayush, order["ayush"], off)
    f["cataract"] = _safe(bf.cataract, order["cataract"], off)
    f["robotic_surgery"] = _safe(bf.robotic, order["robotic_surgery"], off)
    f["exclusions"] = _safe(exclusions, order["exclusions"], off)
    # a clause the interleaved index text could not be read from: same retrieval order, pdftotext page text
    alt = None
    for k, fn in (("ped_wait_months", ped), ("specified_disease_wait_months", specified), ("initial_wait", initial_wait),
                  ("entry_age_max", entry_age), ("room_rent", bf.room_rent), ("maternity", maternity)):
        if f[k].get("stated"):
            continue
        if alt is None:
            tp = {p["page"]: p for p in text_pages(policy_id)}
            alt = {kk: [tp[p["page"]] for p in order[kk] if p["page"] in tp] for kk in order}
        if alt.get(k):
            g = _safe(fn, alt[k], off)
            if g.get("stated"):
                g["text_source"] = "pdftotext page text (indexed text interleaved)"
                f[k] = g
    if (d := bf.condition_carveout(order["ped_wait_months"], off)):
        f["diabetes_wait"] = d
    f["premium_inr"] = bf.missing("Premium is not written in this wording. Money is not compared. "
                                  "Premium comes from the insurer rate card after the health check.")
    by_page = {p["page"]: bf.squash(p["text"]) for p in pages}
    alt_pages = None
    for k, v in f.items():
        if not (isinstance(v, dict) and v.get("stated") and v.get("pdf_page") is not None):
            continue
        if k in retrieved:  # was the stating clause one of the retrieved ones, or found in the wider scan
            v["retrieved"] = v["pdf_page"] in retrieved[k]
        t = by_page.get(v["pdf_page"], "")
        pos = t.find((v.get("quote") or "")[:50])
        if pos < 0:
            if alt_pages is None:
                alt_pages = {p["page"]: bf.squash(p["text"]) for p in text_pages(policy_id)}
            t = alt_pages.get(v["pdf_page"], "")
            pos = t.find((v.get("quote") or "")[:50])
        if pos >= 0 and (lab := rule_label(t, pos)):
            v["section"] = lab
    # an item quoted from inside a waiting list (a table row) takes the list's own rule
    s = f.get("specified_disease_wait_months", {})
    for k in ("cataract", "joint_replacement"):
        v = f.get(k, {})
        if v.get("stated") and s.get("stated") and re.search(r"waiting list", str(v.get("value")), re.I) \
                and abs((v.get("pdf_page") or 0) - (s.get("pdf_page") or 0)) <= 1 and not v.get("section"):
            v["section"] = s.get("section")
    return {"policy_id": policy_id, "uin": uin(pages), "page_offset": off, "facts": f}


# ── cache keyed by the index fingerprint ───────────────────────────────

def _fingerprint() -> str:
    res = _col().get(include=["metadatas"])
    c = collections.Counter(m["policy_id"] for m in res["metadatas"])
    return hashlib.sha1(json.dumps([VERSION, sorted(c.items())]).encode()).hexdigest()[:16]


def all_facts(policy_ids: list[str], rebuild: bool = False) -> dict:
    """{policy_id: build(...)} for the given ids, from cache when the index has not changed."""
    global _MEM
    with _LOCK:
        fp = _fingerprint()
        if _MEM is None and CACHE.exists() and not rebuild:
            try:
                _MEM = json.loads(CACHE.read_text(encoding="utf-8"))
            except ValueError:
                _MEM = None
        if not _MEM or _MEM.get("fingerprint") != fp or rebuild:
            _MEM = {"fingerprint": fp, "products": {}}
        missing = [p for p in policy_ids if p not in _MEM["products"]]
        for pid in missing:
            _MEM["products"][pid] = build(pid)
        if missing:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(_MEM, ensure_ascii=False), encoding="utf-8")
        return {p: _MEM["products"][p] for p in policy_ids if p in _MEM["products"]}


def forget(policy_id: str) -> None:
    """Drop one product's facts (its wording was re-ingested)."""
    with _LOCK:
        if _MEM and policy_id in _MEM.get("products", {}):
            del _MEM["products"][policy_id]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        print(json.dumps(build(sys.argv[1]), ensure_ascii=False, indent=1))
        raise SystemExit
    from products import registry
    reg = registry()
    facts = all_facts([p["policy_id"] for p in reg], rebuild="--rebuild" in sys.argv)
    fields = ["ped_wait_months", "specified_disease_wait_months", "initial_wait", "entry_age_max", "room_rent",
              "maternity", "joint_replacement", "sum_insured", "ayush", "cataract", "robotic_surgery", "exclusions"]
    print(f"{'field':32} stated/{len(reg)}")
    for k in fields:
        n = sum(1 for p in reg if facts[p["policy_id"]]["facts"].get(k, {}).get("stated"))
        print(f"{k:32} {n}")
