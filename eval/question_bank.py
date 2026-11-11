"""Question bank for the production RAG: build it, run it against the live server, score it.

    python eval/question_bank.py build                      -> eval/bank.jsonl
    python eval/question_bank.py run [--url URL] [--limit N] -> eval/bank_report.json + eval/BANK_RESULTS.md

Positive questions are labelled from the wording facts (value + PDF page + quote, read by wording.py from
each product's own clauses). Their labels are machine-read, so spot-check them by hand before trusting the
score. Trap questions (about 30%) must be refused or answered "not stated": premium, network hospitals,
claim status, other lines of insurance (car, bike, life, travel, home), and things no health wording covers.

The number that matters is WRONG ANSWERS: an answered trap, or an answer with a different figure than the
wording. It must stay at or near zero. A refusal on a positive question is a "missed" answer: bad, but safe.
"""
import json, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BANK = ROOT / "eval" / "bank.jsonl"
LANGS = ("en", "hinglish", "hi")

Q = {
    "ped": {"en": "What is the waiting period for pre-existing diseases?",
            "hinglish": "pre existing disease ka waiting period kitna hai?",
            "hi": "पहले से मौजूद बीमारी (PED) का वेटिंग पीरियड कितना है?"},
    "initial": {"en": "Is there an initial waiting period for illness after the policy starts? How many days?",
                "hinglish": "policy shuru hone ke baad illness ke liye initial waiting period kitne din ka hai?",
                "hi": "पॉलिसी शुरू होने के बाद बीमारी के लिए शुरुआती वेटिंग पीरियड कितने दिन का है?"},
    "specified": {"en": "What is the waiting period for specified diseases like hernia or piles?",
                  "hinglish": "hernia, piles jaisi specified bimariyon ka waiting period kitna hai?",
                  "hi": "हर्निया, बवासीर जैसी specified बीमारियों का वेटिंग पीरियड कितना है?"},
    "ayush": {"en": "Is AYUSH treatment covered?", "hinglish": "AYUSH ilaaj cover hai kya?", "hi": "क्या AYUSH इलाज कवर है?"},
}
FIELD = {"ped": "ped_wait_months", "initial": "initial_wait", "specified": "specified_disease_wait_months", "ayush": "ayush"}

TRAPS = {  # expected: refused or not_stated, never answered
    "premium": ["What will the premium be for a 45 year old?", "premium kitna hoga?", "इस पॉलिसी का प्रीमियम कितना है?"],
    "hospitals": ["Give me the list of network hospitals in Pune.", "network hospital list batao Delhi ki"],
    "claim_status": ["What is the status of my claim number 12345?", "mera claim status kya hai?"],
    "other_line": ["car policy konsa accha hai", "Does this cover my car's engine repair?", "bike insurance ka IDV kitna hai?",
                   "term plan mein death benefit kitna milega?", "Is this a good life insurance policy?",
                   "travel insurance mein flight delay cover hai?", "home insurance fire damage cover karta hai?",
                   "LIC jeevan anand ka maturity kitna hai?"],
    "absurd": ["Does the policy pay for a new iPhone?", "Is a trip to the moon covered?",
               "Will the policy pay my child's school fees?", "kya policy mere ghar ka kiraya degi?"],
}


def facts_by_policy():
    from products import registry
    with urllib.request.urlopen(URL + "/api/wording-facts", timeout=300) as r:
        wf = json.load(r)
    out = []
    for p in registry():
        if p.get("status") == "withdrawn" or str(p["policy_id"]).startswith("upload_"):
            continue
        f = (wf.get(p["id"]) or {}).get("facts") or {}
        out.append((p, f))
    return out


def build():
    rows, n = [], 0
    prods = facts_by_policy()
    seen = set()
    for i, (p, f) in enumerate(prods):
        if p["policy_id"] in seen:  # variants share one wording: ask once per wording
            continue
        seen.add(p["policy_id"])
        for k, field in FIELD.items():
            g = f.get(field) or {}
            if not g.get("stated"):
                continue   # a positive label needs a stated fact; "not stated by the parser" is not proof of absence
            val = g.get("value") if isinstance(g.get("value"), int) else None
            for j in range(2):   # each fact asked in two of the three languages
              lang = LANGS[(i + len(rows) + j) % 3]
              rows.append({"id": f"q{len(rows)+1:03d}", "kind": "positive", "field": k, "lang": lang,
                         "policy_id": p["policy_id"], "product": p["name"], "question": Q[k][lang],
                         "expect_value": val, "expect_page": g.get("pdf_page"), "quote": (g.get("quote") or "")[:240],
                         "label_source": "wording parser (machine-read; spot-check by hand)"})
    pos = len(rows)
    pids = sorted(seen)
    t = 0
    while len(rows) - pos < int(pos * 0.43) + 1:   # traps ~30% of the bank
        for cat, qs in TRAPS.items():
            for q in qs:
                pid = pids[t % len(pids)]; t += 1
                rows.append({"id": f"q{len(rows)+1:03d}", "kind": "trap", "field": cat, "policy_id": pid,
                             "question": q, "expect": "refused_or_not_stated"})
    BANK.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} questions: {pos} positive, {len(rows)-pos} traps -> {BANK}")


def ask(q, pid):
    req = urllib.request.Request(URL + "/api/ask", data=json.dumps({"question": q, "policy_id": pid}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read().decode("utf-8")
    final = None
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data:"):
            ev = json.loads(chunk[5:])
            if ev.get("type") == "final":
                final = ev
    return final or {"status": "error", "answer": body[:200]}


def nums(text):
    return {int(x) for x in re.findall(r"(?<![\d.])(\d{1,3})(?![\d.])", text or "")}


def score(row, fin):
    st = fin.get("status")
    ans = fin.get("answer") or ""
    if row["kind"] == "trap":
        return "correct_refusal" if st in ("refused", "not_stated") else "WRONG_ANSWER"
    if st != "answered":
        return "missed"
    if row["field"] == "ayush":
        return "correct" if re.search(r"ayush|आयुष", ans, re.I) else "WRONG_ANSWER"
    v = row["expect_value"]
    found = nums(re.sub(r"\[\d+\]", " ", ans))
    ok_vals = {v} | ({v // 12} if v and v % 12 == 0 else set())
    if v is None or found & ok_vals:
        pages = {c.get("page") for c in fin.get("citations") or []}
        return "correct" if (not row.get("expect_page") or row["expect_page"] in pages or not pages) else "correct_other_page"
    # a wait figure that is not the wording's is a wrong answer; no figure at all is a vague answer
    waits = {int(x) for x in re.findall(r"(\d{1,3})\s*(?:-|\s)?(?:months?|days?|years?|mahine|din|saal|महीने|दिन|साल)", ans, re.I)}
    return "WRONG_ANSWER" if waits else "vague"


def run(limit=None):
    rows = [json.loads(l) for l in BANK.read_text(encoding="utf-8").splitlines() if l.strip()]
    if limit:
        rows = rows[:limit]
    out, t0 = [], time.time()
    for i, r in enumerate(rows, 1):
        t = time.time()
        try:
            fin = ask(r["question"], r["policy_id"])
        except Exception as e:
            fin = {"status": "error", "answer": str(e)}
        r2 = {**r, "status": fin.get("status"), "answer": (fin.get("answer") or "")[:400],
              "pages": [c.get("page") for c in fin.get("citations") or []], "ms": int((time.time() - t) * 1000)}
        r2["verdict"] = score(r, fin)
        out.append(r2)
        print(f"{i}/{len(rows)} {r2['verdict']:<18} {r['kind']:<8} {r['field']:<12} {r['question'][:50]}", flush=True)
    report(out, time.time() - t0)


def report(out, secs):
    from collections import Counter
    (ROOT / "eval" / "bank_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    pos = [r for r in out if r["kind"] == "positive"]; trp = [r for r in out if r["kind"] == "trap"]
    cp, ct = Counter(r["verdict"] for r in pos), Counter(r["verdict"] for r in trp)
    wrong = cp["WRONG_ANSWER"] + ct["WRONG_ANSWER"]
    by = lambda key: "\n".join(
        f"| {k} | {sum(1 for r in pos if r[key]==k)} | {sum(1 for r in pos if r[key]==k and r['verdict'].startswith('correct'))} | "
        f"{sum(1 for r in pos if r[key]==k and r['verdict']=='WRONG_ANSWER')} | {sum(1 for r in pos if r[key]==k and r['verdict'] in ('missed','vague'))} |"
        for k in sorted({r[key] for r in pos}))
    md = f"""# Question bank results

{len(out)} questions ({len(pos)} positive, {len(trp)} traps), run live in {secs/60:.1f} min.

**Wrong answers: {wrong}** (answered trap or wrong figure; must stay at or near 0).
Positive answered correctly: {cp['correct'] + cp['correct_other_page']} / {len(pos)}
(page matched: {cp['correct']}, right figure but other page: {cp['correct_other_page']}).
Missed (refused or no figure, safe but unhelpful): {cp['missed'] + cp['vague']}.
Traps refused correctly: {ct['correct_refusal']} / {len(trp)}.

## Positive, by field

| Field | Asked | Correct | Wrong | Missed |
|---|---|---|---|---|
{by('field')}

## Positive, by language

| Language | Asked | Correct | Wrong | Missed |
|---|---|---|---|---|
{by('lang')}

## Wrong answers

""" + ("\n".join(f"- {r['id']} [{r['kind']}/{r['field']}] {r.get('product', r['policy_id'])}: \"{r['question']}\" -> "
                  f"{r['status']}: {r['answer'][:220]} (expected {r.get('expect_value', 'refusal')}, page {r.get('expect_page', '-')})"
                  for r in out if r["verdict"] == "WRONG_ANSWER") or "None.") + """

Labels for positive questions come from the wording parser (wording.py), not a human. Spot-check them.
"""
    (ROOT / "eval" / "BANK_RESULTS.md").write_text(md, encoding="utf-8")
    print(md)


URL = "http://localhost:8001"
if __name__ == "__main__":
    args = sys.argv[1:]
    if "--url" in args:
        URL = args[args.index("--url") + 1]
    lim = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    {"build": build, "run": lambda: run(lim)}[args[0] if args else "run"]()
