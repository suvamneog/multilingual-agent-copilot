"""Need-first ranking from the wording: parse the customer's need, then rank on that need.

Every number the ranking uses (PED wait, maternity wait, joint-replacement wait, entry age,
room rent, 30-day wait) comes from wording.py, i.e. from the retrieved clause of that product,
with its page and rule number. Nothing comes from the catalogue's fact table.

Need types, in priority order:
  maternity  -> maternity wait in the base policy; excluded or add-on-only plans miss
  surgery    -> the planned surgery's wait (joint replacement list) and PED if the condition is
                already there; compared with the surgery date
  condition  -> PED wait (diabetes, BP, heart, cancer history, thyroid, knee pain ...)
  healthy    -> no PED wait applies; entry age and the 30-day wait decide, then room rent
Negation is a filter: "no diabetes", "diabetes nahi hai", "no BP", "no disease" remove the condition.
"""
from __future__ import annotations

import re
from datetime import date

from products import label, registry
from wording import all_facts

# ── need parsing ───────────────────────────────────────────────────────

RELATION = {
    "mother": ["mother", "mothr", "moter", "mom", "mummy", "mumma", "maa", "ma", "mata", "amma", "मम्मी", "माँ", "मां", "माता"],
    "father": ["father", "dad", "papa", "pitaji", "pita", "पापा", "पिता"],
    "wife": ["wife", "biwi", "patni", "पत्नी"],
    "husband": ["husband", "pati", "पति"],
    "son": ["son", "beta", "बेटा"],
    "daughter": ["daughter", "beti", "बेटी"],
    "self": ["myself", "mujhe", "mere liye", "khud", "i am", "i'm", "main"],
}
CONDITION = {
    "diabetes": ["diabetes", "diabetic", "diabetis", "diabities", "diabeties", "diabtes", "sugar", "shugar", "madhumeh",
                 "शुगर", "मधुमेह", "डायबिटीज"],
    "high BP": ["bp", "b.p", "blood pressure", "hypertension", "बीपी"],
    "heart": ["heart", "cardiac", "bypass", "angioplasty", "stent", "dil ki", "हृदय"],
    "cancer": ["cancer", "tumour", "tumor", "chemo", "chemotherapy", "कैंसर"],
    "thyroid": ["thyroid"],
    "asthma": ["asthma", "दमा"],
    "kidney": ["kidney", "dialysis"],
    "knee / joint pain": ["knee", "knees", "ghutna", "ghutne", "ghutno", "ghutnon", "joint pain", "arthritis",
                          "gathiya", "घुटन", "जोड़"],
}
GENERIC_NONE = re.compile(r"\bno (?:disease|illness|medical (?:history|condition)|health (?:issue|problem)s?|pre-?existing)|"
                          r"\bhealthy\b|\bfit\b|koi bimari nahi|koi bimaari nahi|bimari nahi|कोई बीमारी नहीं|स्वस्थ", re.I)
NEG_BEFORE = re.compile(r"(?:\bno|\bnot|\bwithout|\bnever|\bna|\bnahi|\bnahin|\bkoi|नहीं|ना)\s*(?:\w+\s*){0,2}$", re.I)
NEG_AFTER = re.compile(r"^\s*(?:\w+\s*){0,2}?(?:nahi|nahin|nhi|नहीं|ni\b|bilkul nahi)", re.I)
LOW_COST = re.compile(r"\b(?:cheap|cheaper|cheapest|affordable|low (?:premium|cost|budget)|budget friendly|sasta|sasti|saste|"
                      r"kam premium|premium kam|pese kam|paise kam|kam pese|kam paise|kam lage|kam kharcha)\b|"
                      r"\b(?:premium|price|cost)\b[^.?!]{0,25}\b(?:low|less|lower|kam|cheap)\b|"
                      r"\bbudget\b[^.?!]{0,15}\b(?:limited|tight|low|kam)\b|सस्ता|पैसे कम", re.I)
MATERNITY = re.compile(r"\bbaby\b|pregnan|maternity|delivery|garbh|bachcha|bachha|child ?birth|बच्चा|गर्भ|प्रसव|मातृत्व", re.I)
SURGERY = re.compile(r"(knee|hip|joint)\s*replacement|replacement (?:of|surgery)|cataract|hernia|bypass surgery|"
                     r"surgery|operation|ghutne ka operation", re.I)
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "twelve": 12, "ek": 1, "do": 2, "teen": 3, "chaar": 4, "char": 4, "paanch": 5, "chhe": 6, "che": 6}


def _has(text: str, words: list[str]) -> list[re.Match]:
    out = []
    for w in words:
        rx = re.compile(rf"(?<![a-z]){re.escape(w)}(?![a-z])", re.I) if re.fullmatch(r"[a-z .']+", w) else re.compile(re.escape(w))
        out += list(rx.finditer(text))
    return out


def _negated(text: str, m: re.Match) -> bool:
    before = text[max(0, m.start() - 24):m.start()]
    after = text[m.end():m.end() + 30]
    if NEG_BEFORE.search(before):
        return True
    return bool(NEG_AFTER.search(after)) and not re.match(r"\s*(?:ki|ka|ke)\b", after, re.I)


def _months_from_now(text: str, start: int) -> int | None:
    """'in 6 months', '6 mahine mein', 'next year', 'agle saal', 'in 2 years'."""
    tail = text[start:start + 80]
    if re.search(r"next year|agle saal|agle sal|अगले साल", tail + text[max(0, start - 40):start], re.I):
        return 12
    m = re.search(r"(?:in|within|after)?\s*(\d{1,2}|" + "|".join(WORDNUM) + r")\s*(months?|mahine|mahina|mahino|years?|saal|sal)", tail, re.I)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else WORDNUM[m.group(1).lower()]
    return n * 12 if re.match(r"years?|saal|sal", m.group(2), re.I) else n


def parse_need(text: str) -> dict:
    t = f" {text.lower()} "
    need = {"text": text, "relation": None, "age": None, "conditions": [], "absent": [], "low_cost": False,
            "budget_max": None, "room_rent_min": None, "maternity": None, "surgery": None, "healthy": False}
    for rel, words in RELATION.items():
        if _has(t, words):
            need["relation"] = rel
            break
    m = re.search(r"(\d{2})\s*(?:years?|yrs?|yr|saal|sal|साल|y/o|year old)|(?:age|umar|umr|aged)\s*(?:is|hai|:)?\s*(\d{2})|"
                  r"(?:mother|father|mom|dad|wife|husband|maa|ma|papa|mummy|i am|i'm)\D{0,12}(\d{2})\b", t, re.I)
    if m:
        a = int(next(g for g in m.groups() if g))
        if 18 <= a <= 99:
            need["age"] = a
    for cond, words in CONDITION.items():
        hits = _has(t, words)
        if not hits:
            continue
        if all(_negated(t, h) for h in hits):
            need["absent"].append(cond)
        else:
            need["conditions"].append(cond)
    if GENERIC_NONE.search(t) and not need["conditions"]:
        need["healthy"] = True
    need["low_cost"] = bool(LOW_COST.search(t))
    for mm in re.finditer(r"(\d[\d,]*)\s*(k|thousand|hazaar|hazar|lakh)?", t):
        v = int(mm.group(1).replace(",", "")) * (1000 if (mm.group(2) or "").lower() in ("k", "thousand", "hazaar", "hazar")
                                                  else 100000 if (mm.group(2) or "").lower() == "lakh" else 1)
        before = t[max(0, mm.start() - 30):mm.start()]
        if v >= 1000 and re.search(r"room|rent|kamra", before):
            need["room_rent_min"] = v
        elif v >= 1000 and re.search(r"budget|premium|afford|under|within|upto|up to|tak", before):
            need["budget_max"] = v
    mat = MATERNITY.search(t)
    if mat and not _negated(t, mat):
        need["maternity"] = {"in_months": _months_from_now(t, mat.start() - 40 if mat.start() > 40 else 0) or
                             _months_from_now(t, mat.end())}
    sur = SURGERY.search(t)
    if sur and not need["maternity"]:
        kind = "joint replacement" if re.search(r"replacement|ghutne ka operation", sur.group(0), re.I) or \
            re.search(r"knee|ghutn|joint|hip", t[max(0, sur.start() - 40):sur.end() + 10], re.I) and \
            re.search(r"replacement|operation", t, re.I) else sur.group(0).lower()
        if re.search(r"bypass", sur.group(0), re.I) and re.search(r"\bago\b|pehle|years? back|ho chuki|done", t, re.I):
            sur = None  # a past bypass is history (heart, handled as a condition), not a planned surgery
        if sur:
            need["surgery"] = {"kind": kind, "in_months": _months_from_now(t, sur.start())}
            if kind == "joint replacement" and "knee / joint pain" not in need["conditions"] and \
                    "knee / joint pain" not in need["absent"]:
                need["conditions"].append("knee / joint pain")  # needing a replacement means the joint problem exists
    if not need["conditions"] and not need["maternity"] and not need["surgery"] and need["absent"]:
        need["healthy"] = True
    need["kind"] = ("maternity" if need["maternity"] else "surgery" if need["surgery"]
                    else "condition" if need["conditions"] else "healthy")
    need["signals"] = sum(bool(x) for x in (need["relation"], need["age"], need["conditions"] or need["absent"],
                                            need["maternity"], need["surgery"], need["low_cost"], need["healthy"]))
    return need


# ── ranking ────────────────────────────────────────────────────────────

def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    yy, mm = d.year + y, m + 1
    import calendar
    return date(yy, mm, min(d.day, calendar.monthrange(yy, mm)[1]))


def fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def cite(f: dict) -> dict | None:
    if not f or not f.get("stated"):
        return None
    return {"pdf_page": f.get("pdf_page"), "printed_page": f.get("printed_page"), "section": f.get("section"),
            "quote": f.get("quote")}


def page_label(f: dict) -> str:
    if not f or f.get("pdf_page") is None:
        return "page not recorded"
    return f"PDF page {f['pdf_page']}"


def assess(p: dict, facts: dict, need: dict, today: date) -> dict:
    f = facts
    fails, cannot, notes = [], [], []
    key = None          # months until the needed cover starts (lower is better)
    basis = None        # the fact the key comes from (for the citation)
    if p.get("status") == "withdrawn":
        fails.append("Stopped for new sales" + (f" — sell {p['replaced_by_label']} instead" if p.get("replaced_by_label") else ""))
    # age
    age_ok = None
    if need["age"] is not None:
        a = f.get("entry_age_max", {})
        if a.get("stated") and isinstance(a.get("value"), int):
            age_ok = need["age"] <= a["value"]
            if not age_ok:
                fails.append(f"Entry age stops at {a['value']}; customer is {need['age']} ({page_label(a)})")
        elif a.get("stated"):
            age_ok = True
        else:
            cannot.append("entry age is not written in this wording")
    # need-specific wait
    kind = need["kind"]
    if kind == "maternity":
        m = f.get("maternity", {})
        if not m.get("stated"):
            fails.append("Maternity is not written in this wording")
        elif m.get("kind") == "excluded":
            fails.append(f"Maternity is excluded in the base policy ({m.get('section') or 'exclusions'}, {page_label(m)})")
        elif m.get("kind") == "addon":
            fails.append(f"Maternity only with an optional add-on, {m.get('months')} months ({page_label(m)})")
        else:
            key, basis = m.get("months"), m
    elif kind == "surgery":
        j = f.get("joint_replacement", {}) if need["surgery"]["kind"] == "joint replacement" else {}
        ped = f.get("ped_wait_months", {})
        waits = []
        if j.get("stated") and j.get("months"):
            waits.append((j["months"], j))
        elif need["surgery"]["kind"] == "joint replacement":
            s = f.get("specified_disease_wait_months", {})
            if s.get("stated"):
                waits.append((s["value"], s))
                notes.append("joint replacement is not named in the waiting list; the general specified-disease wait is shown")
        if need["conditions"] and ped.get("stated"):
            waits.append((ped["value"], ped))  # the knee problem already exists: PED applies too
        if not waits:
            cannot.append("the wait for this surgery is not written in this wording")
        else:
            key, basis = max(waits, key=lambda w: w[0])
    elif kind == "condition":
        carve = f.get("diabetes_wait", {})
        if carve.get("stated") and set(need["conditions"]) <= set(carve.get("conditions") or []):
            key, basis = carve.get("months", 0), carve
        else:
            ped = f.get("ped_wait_months", {})
            if ped.get("stated"):
                key, basis = ped["value"], ped
            else:
                cannot.append("pre-existing disease wait is not written in this wording")
    else:  # healthy: no PED applies; the 30-day wait is the first gate
        iw = f.get("initial_wait", {})
        key, basis = (1 if iw.get("stated") else None), iw if iw.get("stated") else None
    # room rent
    if need["room_rent_min"]:
        r = f.get("room_rent", {})
        if not r.get("stated"):
            cannot.append("room rent limit is not written in this wording")
        elif r.get("max_per_day") and r["max_per_day"] < need["room_rent_min"]:
            fails.append(f"Room rent capped at ₹{r['max_per_day']:,}/day ({page_label(r)})")
        elif r.get("room_class") not in ("no_cap", "per_day"):
            cannot.append("room rent depends on the plan in the Policy Schedule")
    cover = add_months(today, key) if key is not None else None
    return {"product": p, "fails": fails, "cannot": cannot, "notes": notes, "key": key, "basis": basis,
            "cover_date": cover, "age_ok": age_ok, "eligible": not fails and key is not None}


def _why_best(a: dict, need: dict, lang: str) -> str:
    k, b = a["key"], a["basis"]
    kind = need["kind"]
    age = " Entry age is confirmed from the wording." if a["age_ok"] else ""
    if kind == "maternity":
        return f"Shortest maternity wait in the base policy among plans that cover it — {k} months ({page_label(b)}).{age}"
    if kind == "surgery":
        return f"Earliest cover for the {need['surgery']['kind']} — {k} months ({page_label(b)}).{age}"
    if kind == "condition":
        if a["age_ok"]:
            return f"Shortest wait among plans where entry age is confirmed from the wording — {k} months ({page_label(b)})."
        return f"Shortest pre-existing wait of the plans that fit — {k} months ({page_label(b)})."
    return "No pre-existing wait applies; only the 30-day initial wait" + (f" ({page_label(b)})" if b else "") + "." + age


def recommend(text: str, mine: list[str] | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    need = parse_need(text)
    from answer import other_line
    if other_line(text):   # gate 1: never rank health plans for a car / life / travel question
        return {"need": need, "status": "out_of_scope", "cards": [], "lines": []}
    if not need["signals"] and not need.get("room_rent_min") and not need.get("ped_max_months"):
        return {"need": need, "status": "need_unclear", "cards": [], "lines": []}   # gate 2: ask, do not guess
    reg = registry()
    byid = {p["id"]: p for p in reg}
    for p in reg:
        p["replaced_by_label"] = label(byid[p["replaced_by"]]) if p.get("replaced_by") in byid else None
    pool = [p for p in reg if not str(p["policy_id"]).startswith("upload_")]
    facts = all_facts([p["policy_id"] for p in pool])
    scope = "all"
    mine = [m for m in (mine or []) if any(p["insurer"] == m for p in pool)]
    assessed = [assess(p, facts[p["policy_id"]]["facts"], need, today) for p in pool]
    if mine:
        own = [a for a in assessed if a["product"]["insurer"] in mine and a["eligible"]]
        if own:
            assessed, scope = [a for a in assessed if a["product"]["insurer"] in mine], "mine"
        else:
            scope = "mine-none"
    ok = [a for a in assessed if a["eligible"]]
    # an unknown entry age only matters where entry limits bite (45+); a 28-year-old is not held back by it
    age_gate = need["age"] is not None and need["age"] >= 45
    healthy = need["kind"] == "healthy"

    def order(a):
        f = facts[a["product"]["policy_id"]]["facts"]
        tie = ()
        if healthy:  # no PED wait applies: shorter listed-illness wait, then an uncapped room, decide
            s = f.get("specified_disease_wait_months", {})
            r = f.get("room_rent", {})
            tie = (s.get("value") if s.get("stated") else 99, 0 if r.get("room_class") == "no_cap" else 1)
        return (0 if (a["age_ok"] or not age_gate) else 1, a["key"], *tie, len(a["cannot"]), a["product"]["id"])
    ok.sort(key=order)
    best = ok[0] if ok else None
    second = ok[1] if len(ok) > 1 else None
    watch = None
    youth = re.compile(r"young|yuva|youth|junior", re.I)
    if best and best["age_ok"] and age_gate:
        cands = [a for a in ok if not a["age_ok"] and a["key"] is not None and a["key"] <= best["key"] - 12
                 and not (need["age"] > 40 and youth.search(label(a["product"])))]  # a youth plan is never floated to a 40+ buyer
        cands.sort(key=lambda a: (a["key"], a["product"]["id"]))  # deterministic: no rotation between turns
        watch = cands[0] if cands else None
    target = (need["maternity"] or {}).get("in_months") if need["kind"] == "maternity" else \
        (need["surgery"] or {}).get("in_months") if need["kind"] == "surgery" else None
    in_time = None
    if target is not None:
        in_time = [a for a in ok if a["key"] <= target]
    top = {id(x) for x in (best, second, watch) if x}
    groups: dict[str, list[str]] = {}
    for a in assessed:
        if id(a) in top:
            continue
        if a["fails"]:
            reason = a["fails"][0]
        elif a["key"] is None:
            reason = "Not ranked — " + (a["cannot"][0] if a["cannot"] else "the needed wait is not written")
        elif best and not a["age_ok"] and best["age_ok"] and age_gate:
            reason = f"Not ranked — entry age is not written in the wording, so age {need['age']} cannot be confirmed"
        elif best and a["key"] > best["key"]:
            reason = f"{a['key']}-month wait — {a['key'] - best['key']} months longer than the best"
        else:
            reason = "Fits too, but ranks lower on the same wait"
        groups.setdefault(reason, []).append(label(a["product"]))
    out = {
        "need": {k: v for k, v in need.items() if k != "text"},
        "scope": scope,
        "label_mode": "options" if need["low_cost"] else "best",
        "money_note": need["low_cost"] or need["budget_max"] is not None,
        "target_months": target,
        "none_in_time": target is not None and not in_time,
        "best": _card(best, need, 1), "second": _card(second, need, 2), "watch": _card(watch, need, 0),
        "others": [{"reason": r, "products": ps} for r, ps in groups.items()],
        "checked": len(pool), "today": today.isoformat(),
    }
    out["lines"] = {lang: lines(out, need, lang) for lang in ("en", "hinglish", "hi")}
    return out


def _card(a: dict | None, need: dict, rank: int) -> dict | None:
    if not a:
        return None
    p = a["product"]
    b = a["basis"] or {}
    return {"id": p["id"], "policy_id": p["policy_id"], "label": label(p), "insurer": p["insurer"], "name": p["name"],
            "variant": p.get("variant"), "uin": p.get("uin"), "months": a["key"],
            "cover_date": fmt_date(a["cover_date"]) if a["cover_date"] else None,
            "why": _why_best(a, need, "en") if rank == 1 else _why_other(a, rank),
            "age_ok": a["age_ok"], "cannot": a["cannot"], "notes": a["notes"],
            "cite": {"page": page_label(b), "pdf_page": b.get("pdf_page"), "printed_page": b.get("printed_page"),
                     "section": b.get("section"), "quote": b.get("quote")} if b else None}


def _why_other(a: dict, rank: int) -> str:
    b = a["basis"] or {}
    if rank == 0:
        return (f"{a['key']}-month wait ({page_label(b)}), but its wording does not state the entry age — "
                "confirm it in the prospectus before you sell it.")
    extra = " Entry age confirmed from the wording." if a["age_ok"] else " Entry age is not written in the wording."
    return f"{a['key']}-month wait ({page_label(b)}).{extra}"


WHO = {"en": {"mother": "your mother", "father": "your father", "wife": "your wife", "husband": "your husband",
              "son": "your son", "daughter": "your daughter", "self": "you"},
       "hinglish": {"mother": "mummy", "father": "papa", "wife": "wife", "husband": "pati", "son": "bete",
                    "daughter": "beti", "self": "aap"},
       "hi": {"mother": "माँ", "father": "पिता", "wife": "पत्नी", "husband": "पति", "son": "बेटे", "daughter": "बेटी", "self": "आप"}}


def lines(r: dict, need: dict, lang: str) -> list[str]:
    who = WHO[lang].get(need["relation"] or "", {"en": "the customer", "hinglish": "customer", "hi": "ग्राहक"}[lang])
    age = f" ({need['age']})" if need["age"] else ""
    b, s, opt = r["best"], r["second"], r["label_mode"] == "options"
    L = []
    T = {
        "en": {"yes": f"Yes — there is a plan that can cover {who}{age}.", "no": f"No plan in the {r['checked']} wordings fits {who}{age} on what was asked.",
               "best": "Best", "opt1": "Option 1", "second": "Second", "opt2": "Option 2",
               "money": "Money was not compared: plans are ranked on waiting period, not price. Each card shows its premium where one is loaded.",
               "abs": "Not counted (you said there is none): ", "healthy": "No pre-existing condition, so no pre-existing wait applies — only the 30-day initial wait for illness (accidents are covered from day 1).",
               "late": "No plan covers it in time.", "age_only": "Ranked only plans where entry age is confirmed from the wording."},
        "hinglish": {"yes": f"Haan — {who}{age} ke liye plan mil sakta hai.", "no": f"{r['checked']} wordings mein {who}{age} ke liye koi plan fit nahi hua.",
                     "best": "Sabse behtar", "opt1": "Option 1", "second": "Doosra", "opt2": "Option 2",
                     "money": "Paisa compare nahi kiya: ranking waiting period par hai, price par nahi. Har card par premium neeche diya hai, jahan figure load hai.",
                     "abs": "Count nahi kiya (aapne kaha nahi hai): ", "healthy": "Koi pre-existing bimari nahi, isliye PED wait nahi lagega — sirf illness ke liye 30 din ka initial wait (accident day 1 se cover).",
                     "late": "Koi plan time par cover nahi karta.", "age_only": "Sirf wahi plans rank kiye jinki wording mein entry age confirm hai."},
        "hi": {"yes": f"हाँ — {who}{age} के लिए प्लान मिल सकता है।", "no": f"{r['checked']} वर्डिंग में {who}{age} के लिए कोई प्लान फिट नहीं हुआ।",
               "best": "सबसे अच्छा", "opt1": "विकल्प 1", "second": "दूसरा", "opt2": "विकल्प 2",
               "money": "पैसे की तुलना नहीं की गई: रैंकिंग वेटिंग पीरियड पर है, कीमत पर नहीं। जहाँ प्रीमियम लोड है, वह हर कार्ड पर नीचे दिया है।",
               "abs": "गिना नहीं (आपने कहा नहीं है): ", "healthy": "कोई पुरानी बीमारी नहीं, इसलिए PED वेट नहीं — सिर्फ बीमारी के लिए 30 दिन का शुरुआती वेट (दुर्घटना पहले दिन से कवर)।",
               "late": "कोई प्लान समय पर कवर नहीं करता।", "age_only": "सिर्फ वही प्लान रैंक किए जिनकी वर्डिंग में एंट्री उम्र कन्फ़र्म है।"},
    }[lang]
    L.append(T["yes"] if b else T["no"])
    if need["absent"]:
        L.append(T["abs"] + ", ".join(need["absent"]) + ".")
    kind = need["kind"]
    if kind == "healthy":
        L.append(T["healthy"])
    if b:
        head = T["opt1"] if opt else T["best"]
        what = {"maternity": "maternity", "surgery": need["surgery"]["kind"] if need["surgery"] else "",
                "condition": "pre-existing", "healthy": "initial"}[kind]
        if kind == "healthy":
            L.append(f"{head}: {b['label']}.")
        else:
            L.append(f"{head}: {b['label']} — {what} wait {b['months']} months, cover from {b['cover_date']} ({b['cite']['page']}).")
        if kind == "condition" and b["age_ok"]:
            L.append(T["age_only"])
        for c in need["conditions"]:
            if kind != "condition" and c != "knee / joint pain":
                continue
            msg = {
                "cancer": {"en": f"Cancer treated before counts as a pre-existing disease: covered from {b['cover_date']} if declared in the proposal, not before.",
                           "hinglish": f"Pehle hua cancer pre-existing disease hai: proposal mein declare karne par {b['cover_date']} se cover, usse pehle nahi.",
                           "hi": f"पहले हुआ कैंसर पुरानी बीमारी है: प्रपोज़ल में बताने पर {b['cover_date']} से कवर, उससे पहले नहीं।"},
                "heart": {"en": f"The past bypass and BP are pre-existing: heart treatment or surgery is covered from {b['cover_date']}, not from day 1.",
                          "hinglish": f"Purana bypass aur BP pre-existing hai: heart ka ilaaj ya surgery {b['cover_date']} se cover, day 1 se nahi.",
                          "hi": f"पुराना बाईपास और बीपी पुरानी बीमारी है: दिल का इलाज या सर्जरी {b['cover_date']} से कवर, पहले दिन से नहीं।"},
                "knee / joint pain": {"en": "Knee pain already there counts as pre-existing; a joint replacement also has its own listed-illness wait.",
                                      "hinglish": "Ghutne ka dard pehle se hai toh pre-existing hai; joint replacement ki alag listed wait bhi hoti hai.",
                                      "hi": "घुटने का दर्द पहले से है तो पुरानी बीमारी है; जॉइंट रिप्लेसमेंट का अलग वेट भी होता है।"},
            }.get(c)
            if msg:
                L.append(msg[lang])
    if s:
        head = T["opt2"] if opt else T["second"]
        L.append(f"{head}: {s['label']}" + (f" — {s['months']} months, from {s['cover_date']} ({s['cite']['page']})." if kind != "healthy" else "."))
    if r["target_months"] is not None:
        if r["none_in_time"]:
            earliest = b["cover_date"] if b else None
            due = fmt_date(add_months(date.fromisoformat(r["today"]), r["target_months"]))
            msg = {"en": f"{T['late']} Needed by about {due}; the earliest cover in these wordings starts {earliest or 'never (none covers it)'}.",
                   "hinglish": f"{T['late']} Zarurat lagbhag {due} tak; sabse jaldi cover {earliest or 'kisi mein nahi'} se.",
                   "hi": f"{T['late']} ज़रूरत लगभग {due} तक; सबसे जल्दी कवर {earliest or 'किसी में नहीं'} से।"}[lang]
            L.append(msg)
    if need["room_rent_min"]:
        rr = f"₹{need['room_rent_min']:,}"
        L.append({"en": f"Room rent of {rr}+ was checked where the wording states a room limit; plans whose wording leaves it to the Policy Schedule are marked as not confirmed.",
                  "hinglish": f"Room rent {rr}+ wahan check kiya jahan wording mein room limit likhi hai; jahan Policy Schedule par chhoda hai, wahan confirm nahi.",
                  "hi": f"रूम रेंट {rr}+ वहाँ चेक किया जहाँ वर्डिंग में रूम लिमिट लिखी है; जहाँ पॉलिसी शेड्यूल पर छोड़ा है, वहाँ कन्फ़र्म नहीं।"}[lang])
    if r["money_note"]:
        L.append(T["money"])
    return L


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    r = recommend(" ".join(sys.argv[1:]))
    print(json.dumps({k: r[k] for k in ("need", "best", "second", "watch", "none_in_time")}, ensure_ascii=False, indent=1))
    print("\n".join(r["lines"]["en"]))
