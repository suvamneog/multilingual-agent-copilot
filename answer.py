"""Question -> grounded answer with clause citations, or a refusal.

Pipeline
  1. detect language (English, Hindi in Devanagari, Hinglish in Roman script)
  2. route: out of scope (premium, hospital lists, claim status) -> refuse without guessing
            comparison across policies -> answered from the verified fact table (compare.py)
            otherwise single policy
  3. retrieve top k clauses for the chosen policy, expand to the rest of each clause
  4. generate from those clauses only (generate.py backends), citing [n]
  5. check every number in the answer against the cited text; block the answer if one is missing
  6. return answer + clause cards (quoted sentence, page, section)

ask_stream() yields events so the API can stream tokens:
  {"type": "meta", ...} {"type": "token", "text": ...} {"type": "final", ...}
"""
import json
import os
import re
import threading
import time
from typing import Iterator

from config import CLAUSE_CHARS, MAX_CLAUSES, NEIGHBOURS, RETRIEVE_K, ROOT
from generate import ExtractiveGenerator, Generator, get_generator

# Generation runs at temperature 0, so a stored final is the answer the model would give again.
# ingest.py drops a policy's entries when it re-indexes that wording.
CACHE_PATH = ROOT / "data" / "answer_cache.json"
_CACHE_LOCK = threading.Lock()
_CACHE: dict | None = None


def _cache_key(question: str, policy_id: str, backend: str) -> str:
    q = re.sub(r"[^a-z0-9\u0900-\u097f]+", " ", question.lower()).strip()
    return f"{backend}|{policy_id}|{q}"


def _cache_load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _CACHE = {}
    return _CACHE


# COPILOT_NO_CACHE=1: every answer comes live from the model (story tests, model comparisons)
NO_CACHE = os.environ.get("COPILOT_NO_CACHE") == "1"


def cached_final(question: str, policy_id: str, backend: str) -> dict | None:
    if NO_CACHE:
        return None
    with _CACHE_LOCK:
        hit = _cache_load().get(_cache_key(question, policy_id, backend))
    return dict(hit, cached=True) if hit else None


def _cache_put(question: str, policy_id: str, backend: str, final: dict) -> None:
    if NO_CACHE or final.get("status") not in ("answered", "not_stated", "blocked", "refused"):
        return
    with _CACHE_LOCK:
        cache = _cache_load()
        cache[_cache_key(question, policy_id, backend)] = final
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_PATH)


def drop_cached(policy_id: str) -> int:
    """Forget every stored answer for one policy (its index changed)."""
    with _CACHE_LOCK:
        cache = _cache_load()
        gone = [k for k in cache if k.split("|", 2)[1] == policy_id]
        for k in gone:
            del cache[k]
        if gone:
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return len(gone)

MIN_SCORE = 0.40  # lowest answerable top score was 0.458 in eval_retrieval; traps overlap, so the LLM and router also gate

NOT_STATED = {
    "en": "This is not stated in the policy document.",
    "hi": "यह जानकारी पॉलिसी दस्तावेज़ में नहीं दी गई है।",
    "hinglish": "Yeh jaankari policy document mein nahi di gayi hai.",
}
OUT_OF_SCOPE = {
    "premium": {
        "en": "Premium amounts are not part of the policy wording, so I cannot quote them. Please use the insurer's premium calculator or quote system.",
        "hi": "प्रीमियम की राशि पॉलिसी वर्डिंग में नहीं होती, इसलिए मैं इसे नहीं बता सकता। कृपया बीमा कंपनी का प्रीमियम कैलकुलेटर देखें।",
        "hinglish": "Premium ki amount policy wording mein nahi hoti, isliye main yeh nahi bata sakta. Insurer ka premium calculator ya quote system use karein.",
    },
    "hospitals": {
        "en": "The list of network hospitals is not in the policy document. It is maintained by the insurer or TPA and changes over time.",
        "hi": "नेटवर्क अस्पतालों की सूची पॉलिसी दस्तावेज़ में नहीं है। यह बीमा कंपनी या TPA के पास रहती है।",
        "hinglish": "Network hospitals ki list policy document mein nahi hai. Yeh insurer ya TPA ke paas hoti hai aur badalti rehti hai.",
    },
    "claim_status": {
        "en": "I cannot check claim status. I only answer from the policy wording.",
        "hi": "मैं क्लेम की स्थिति नहीं देख सकता। मैं केवल पॉलिसी वर्डिंग से जवाब देता हूँ।",
        "hinglish": "Main claim status check nahi kar sakta. Main sirf policy wording se jawab deta hoon.",
    },
}
BLOCKED_NOTE = {
    "en": "I could not verify a figure in the draft answer against the policy text, so here are the relevant clauses as written.",
    "hi": "उत्तर का एक आंकड़ा पॉलिसी के पाठ से मेल नहीं खाया, इसलिए यहाँ संबंधित क्लॉज़ जैसे लिखे हैं वैसे दिए गए हैं।",
    "hinglish": "Draft answer ka ek figure policy text se match nahi hua, isliye relevant clauses jaise likhe hain waise de raha hoon.",
}
EXTRACTIVE_NOTE = {
    "en": "Here is what the policy says:",
    "hi": "पॉलिसी में यह लिखा है:",
    "hinglish": "Policy mein yeh likha hai:",
}

HINGLISH_WORDS = {"kya", "hai", "hain", "ke", "ki", "ka", "liye", "mein", "kitna", "kitne", "kaunsi", "kaun",
                  "nahi", "nahin", "milta", "milega", "bhi", "par", "yeh", "ye", "kab", "kaise", "agar", "toh",
                  "jata", "jaata", "hota", "hoti", "lagta", "lagti", "wala", "wali", "kisi", "sakte", "chahiye", "mera",
                  "meri", "mere", "batao", "bataiye"}

ROUTES = [
    ("premium", re.compile(r"premium\s*(for|of|kitna|kitni|amount|kya hoga|kya hai)|how much .*premium|premium .*(year|lakh|sum insured|age)|प्रीमियम|quote for", re.I)),
    ("hospitals", re.compile(r"(which|list of|kaun\s?se|kaunse|konse)\s.*hospitals?|hospitals?\s+(in|near|list)|network hospital list|अस्पतालों की सूची", re.I)),
    ("claim_status", re.compile(r"claim status|status of (my )?claim|claim ka status", re.I)),
]
COMPARE_RE = re.compile(r"\bcompare\b|\bcomparison\b|which polic(y|ies)|kaun\s?si polic|kaunsi polic|konsi polic|sabse (accha|achha|best)|\bvs\.?\b|versus|कौन सी पॉलिसी", re.I)


def detect_lang(q: str) -> str:
    if re.search(r"[ऀ-ॿ]", q):
        return "hi"
    words = set(re.findall(r"[a-z]+", q.lower()))
    return "hinglish" if len(words & HINGLISH_WORDS) >= 2 else "en"


def route(q: str) -> str:
    for name, rx in ROUTES:
        if rx.search(q):
            return name
    if COMPARE_RE.search(q):
        return "compare"
    return "single"


SYSTEM_PROMPT = """You help Indian insurance agents answer customer questions about a health insurance policy.
You are given numbered clauses from the policy wording. Rules:
1. Answer ONLY from the clauses. Do not use outside knowledge and do not guess.
2. After each sentence, cite the clause numbers it comes from in square brackets, like [1] or [2][3].
3. Copy every number, percentage, amount and time period exactly as written in the clauses.
4. If the clauses do not answer the question, reply with exactly: NOT_STATED
   Never conclude that something does not exist just because no clause mentions it.
5. Write 2 to 4 short sentences an agent can say to a customer. Include conditions and exceptions that change the answer.
   For a named disease, check every clause: if it appears in a specific waiting period list AND the pre-existing disease rule could apply, state both periods and when each applies.
   If the named disease (for example diabetes) is NOT listed in a specific waiting list, answer from the general Pre-Existing Disease (PED) waiting period and say it applies when that condition was present before the policy. Do not reply NOT_STATED only because the disease name is missing from the clause text.
   Ignore retrieved clauses that are about unrelated benefits (such as wellness points, reward scores or fitness discounts) when the question is about waiting periods, room rent or coverage limits.
6. Clauses under an "Exclusions" part list what the policy does NOT pay for. A treatment listed there is not covered (unless the clause itself states an exception).
   Clauses under a "Definition" part only define words; they do not by themselves grant cover.
   An appendix "List of Expenses Generally Excluded" is a list of non-medical items such as consumables, not the policy's main exclusions.
7. Never use words like "lifetime", "guaranteed", "always", "all hospitals" or "no limit" unless that exact idea is written in a clause.
8. Write in the language you are told to use. Keep terms like "waiting period", "sum insured" and "pre-existing disease" in English.

Example
Clauses:
[1] (page 4, 4 Exclusions > 4.1 Pre-existing diseases) All pre-existing diseases ... until 48 months of continuous coverage has elapsed.
[2] (page 5, 4 Exclusions > 4.3 Specific waiting period) ii. Two years waiting period ... c. Hernia ...
Question: Hernia ka waiting period kitna hai?
Language: Hinglish (Hindi words in Roman script)
Answer: Hernia par 2 saal (two years) ka specific waiting period hai [2]. Agar hernia policy lene se pehle se hai, toh woh pre-existing disease hai aur 48 months of continuous coverage tak cover nahi hoga [1]."""

LANG_LINE = {
    "en": "English",
    "hi": "Hindi in Devanagari script",
    "hinglish": "Hinglish (Hindi words in Roman script)",
}


SUBHEAD_RE = re.compile(r"\b([ivx]{1,4})\.\s+([A-Z][A-Za-z ]{3,50}?(?:period|cover|limit|benefit)s?)\b")


def list_notes(question: str, text: str) -> list[str]:
    """For list clauses laid out in columns, say which sub-heading a matched item sits under.

    e.g. 'Diabetes' in clause 4.3 sits under 'ii. Two years waiting period'. Extracted text of
    two column lists interleaves items, which small models misread.
    """
    from retrieve import STOP
    notes = []
    for w in sorted({w for w in re.findall(r"[A-Za-z]{5,}", question) if w.lower() not in STOP}):
        m = re.search(rf"\b[a-z]\.\s+([^.]*?\b{re.escape(w)}\w*[^.]*?)(?=\s+[a-z]\.\s|$)", text, re.I)
        if not m:
            continue
        heads = [h for h in SUBHEAD_RE.finditer(text) if h.start() < m.start()]
        if heads:
            h = heads[-1]
            notes.append(f'Note: "{m.group(1).strip()[:60]}" is listed under "{h.group(1)}. {h.group(2)}".')
    return notes


def build_prompt(question: str, clauses: list[dict], product: str, lang: str = "en") -> str:
    parts = [f"Policy: {product}", "", "Clauses:"]
    for i, c in enumerate(clauses, 1):
        part = c.get("part") or ""
        tag = f"{part} > {c['section']}" if part and part != c["section"] else c["section"]
        notes = list_notes(question, c["text"])
        text = c["text"] if len(c["text"]) <= CLAUSE_CHARS else c["text"][:CLAUSE_CHARS].rsplit(" ", 1)[0] + " ..."
        parts.append(f"[{i}] (page {c['printed_page']}, {tag})\n{text}" + ("\n" + "\n".join(notes) if notes else ""))
    parts +=["", f"Question: {question}", f"Language: {LANG_LINE[lang]}", "Answer:"]
    return "\n".join(parts)


# ---------- number check ----------

NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
             "nine": "9", "ten": "10", "twelve": "12", "fifteen": "15", "twenty": "20", "thirty": "30",
             "forty": "40", "forty-five": "45", "sixty": "60", "ninety": "90", "hundred": "100"}
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    t = text.translate(DEV_DIGITS).lower()
    for w, d in NUM_WORDS.items():
        t = re.sub(rf"\b{w}\b", d, t)
    out = set()
    for m in NUM_RE.findall(t):
        n = m.replace(",", "").rstrip(".")
        if n:
            out.add(n.lstrip("0") or "0")
    return out


def check_numbers(answer: str, sources: list[str]) -> list[str]:
    """Numbers in the answer that appear in none of the cited sources."""
    body = re.sub(r"\[\d+\]", " ", answer)
    ans = _numbers(body)
    src = set()
    for s in sources:
        src |= _numbers(s)
    return sorted(n for n in ans if n not in src)


# ---------- quoting ----------

def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def _clip(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " ..."


def best_sentence(chunk_text: str, answer: str, question: str) -> str:
    """The sentence (or pair of sentences) in the clause that best supports the answer."""
    # split at real sentence ends, not list markers like "a." or "ii."
    sents = [s.strip() for s in re.split(r"(?<=[a-z0-9)%]{3}[.;])\s+(?=[A-Z(\d])|\n", chunk_text) if s.strip()]
    windows = [s for s in sents if len(s) >= 40]
    windows += [sents[i] + " " + sents[i + 1] for i in range(len(sents) - 1)]
    windows = [w for w in windows if 40 <= len(w) <= 500] or [chunk_text]
    target = _tokens(answer) | _tokens(question)
    target_nums = _numbers(answer)
    def score(s):
        return (len(_tokens(s) & target) + 3 * len(_numbers(s) & target_nums), -len(s))
    best = max(windows, key=score)
    if best is chunk_text or len(best) > 400:
        # long list with no sentence breaks: centre a window on the most specific matching term
        low = chunk_text.lower()
        terms = sorted(target_nums, key=len, reverse=True) + sorted(target, key=len, reverse=True)
        pos = next((low.find(t) for t in terms if len(t) > 3 and low.find(t) >= 0), -1)
        if pos >= 0:
            a = max(0, pos - 150)
            a = chunk_text.rfind(" ", 0, a) + 1 if a else 0
            snippet = chunk_text[a:a + 320].rsplit(" ", 1)[0]
            return ("... " if a else "") + snippet + " ..."
    if score(best)[0] == 0:  # nothing overlaps (e.g. Hindi question, no answer): show the clause opening
        return _clip(chunk_text, 350)
    return best if len(best) <= 400 else best[:400].rsplit(" ", 1)[0] + " ..."


def card(c: dict, n: int, answer: str, question: str) -> dict:
    return {"n": n, "page": c["page"], "printed_page": c["printed_page"], "section": c["section"],
            "quote": best_sentence(c["text"], answer, question), "text": c["text"], "score": c.get("score"),
            "policy_id": c["policy_id"], "product": c.get("product", "")}


# ---------- pipeline ----------

def _expand_query(question: str) -> str:
    """Hybrid RAG: add English policy terms so Hinglish agent questions still hit the clause."""
    ql = question.lower()
    extras: list[str] = []
    if any(w in ql for w in ("waiting", "intezaar", "ped", "diabetes", "sugar", "pre-existing", "preexisting", "bp", "hypertension")):
        extras.append("pre-existing disease PED waiting period Code Excl")
    if any(w in ql for w in ("room", "rent", "kamra", "icu", "bed")):
        extras.append("room rent limit ICU charges")
    if any(w in ql for w in ("entry", "umar", "age", "senior")):
        extras.append("maximum entry age years")
    if any(w in ql for w in ("cataract", "motiyabind")):
        extras.append("cataract sub-limit waiting")
    if any(w in ql for w in ("ayush", "ayurveda", "homeopathy")):
        extras.append("AYUSH treatment inpatient")
    if any(w in ql for w in ("robotic", "robot", "surgery")):
        extras.append("robotic surgery modern treatment")
    if not extras:
        return question
    return f"{question}\n{' '.join(extras)}"


# "is X covered?" in English, Hinglish and Hindi
COVER_Q = re.compile(r"\bcover(?:ed|s|age)?\b|\bpayable\b|\bpay for\b|\binclud|\bexclud|\bmilega\b|\bmilta\b|\bmilti\b|"
                     r"कवर|शामिल", re.I)
TOPIC_STOP = {"covered", "cover", "coverage", "policy", "insurance", "health", "there", "which", "what", "about",
              "under", "does", "milega", "milta", "milti", "kitna", "kitni", "hoga", "plan", "treatment", "expenses",
              "expense", "included", "charges", "charge", "amount", "limit", "costs", "bills", "hospital",
              "hospitalization", "hospitalisation", "surgery", "surgeries", "room", "rent", "claim", "kharcha", "paisa",
              # pre-existing conditions: answered by the PED waiting period, not by an exclusion that merely names
              # them as a criterion ("bariatric surgery ... uncontrolled type 2 diabetes")
              "diabetes", "diabetic", "sugar", "hypertension", "pressure", "thyroid", "asthma", "heart", "cardiac"}
# Hindi / Hinglish topic words -> the English word the wording uses
TOPIC_ALIAS = {"मातृत्व": "maternity", "प्रसव": "maternity", "डिलीवरी": "maternity", "delivery": "maternity",
               "pregnancy": "maternity", "garbh": "maternity", "गर्भ": "maternity", "बांझपन": "infertility",
               "मोतियाबिंद": "cataract", "motiyabind": "cataract", "dant": "dental", "दांत": "dental",
               "मोटापा": "obesity", "motapa": "obesity", "शराब": "alcohol", "sharab": "alcohol"}
# brochure / add-on text: cover that applies only if the customer buys the optional add-on
ADDON_RE = re.compile(r"\badd[- ]?ons?\b|\boptional (?:cover|benefit|add)|\brider\b|on payment of (?:an )?(?:additional|extra) premium|"
                      r"if opted|if (?:the )?(?:insured|policyholder|you) (?:has |have )?opted", re.I)


def topic_words(question: str) -> list[str]:
    ql = question.lower()
    out = [TOPIC_ALIAS[k] for k in TOPIC_ALIAS if k in ql]
    out += [w for w in re.findall(r"[A-Za-z]{5,}", question) if w.lower() not in TOPIC_STOP and w.lower() not in TOPIC_ALIAS]
    out += re.findall(r"\b[A-Z]{3,5}\b", question)  # acronyms: IVF, OPD, HIV
    return list(dict.fromkeys(out))


ADDON_EXCL = {
    "en": ("The base policy does not cover {topic}: it is listed under exclusions [{e}]. "
           "It is available only with an optional add-on, which you must buy separately: “{q}” [{a}]."),
    "hi": ("बेस पॉलिसी में {topic} कवर नहीं है: यह exclusions में लिखा है [{e}]। "
           "यह सिर्फ वैकल्पिक ऐड-ऑन के साथ मिलता है, जो अलग से खरीदना होगा: “{q}” [{a}]।"),
    "hinglish": ("Base policy mein {topic} cover nahi hai: yeh exclusions mein likha hai [{e}]. "
                 "Yeh sirf optional add-on ke saath milta hai, jo alag se kharidna hoga: “{q}” [{a}]."),
}


def addon_vs_exclusion(question: str, clauses: list[dict]) -> tuple[str, int, int, str] | None:
    """A coverage question where the base wording excludes the topic and an add-on covers it.
    Small models read the add-on as base cover, so this case is answered from a fixed template."""
    if not COVER_Q.search(question):
        return None
    for t in topic_words(question):
        tl = t.lower()
        e = next((i for i, c in enumerate(clauses, 1) if c.get("exclusion") and tl in c["text"].lower()), None)
        a = next((i for i, c in enumerate(clauses, 1)
                  if not c.get("exclusion") and ADDON_RE.search(c["text"]) and tl in c["text"].lower()), None)
        if e is None:
            continue
        if a is None:
            # an add-on line may name the topic without the word "add-on" in the same chunk (brochure tables)
            a = next((i for i, c in enumerate(clauses, 1) if not c.get("exclusion") and tl in c["text"].lower()
                      and re.search(r"\bcovers?\b", c["text"], re.I) and re.search(r"add[- ]?on", c.get("section", "") + c["text"], re.I)), None)
        if a is not None:
            return t, e, a, tl
    return None


def _retrieve(question: str, policy_id: str) -> list[dict]:
    from retrieve import expand_sections, hybrid, keyword_hits
    q = _expand_query(question)
    hits = hybrid(q, policy_id, RETRIEVE_K)
    # keyword pass uses the original agent words plus the English expansions
    kw = keyword_hits(q, policy_id, {h["id"] for h in hits})
    for k in kw:
        k["score"] = hits[0]["score"] if hits else 0.5
    hits = hits[:1] + kw + hits[1:]  # keyword matches right after the best semantic hit
    if NEIGHBOURS:
        hits = expand_sections(hits, max_extra=2)
    return hits[:MAX_CLAUSES]


def _addon_case(question: str, policy_id: str, clauses: list[dict]) -> list[dict] | None:
    """For "is X covered?": the retrieved clauses plus any exclusion naming X, flagged.
    Returned only when the template case applies (base excludes X and an add-on covers it);
    otherwise None, and the model sees exactly the clauses it always saw."""
    if not COVER_Q.search(question):
        return None
    from retrieve import exclusion_hits, is_exclusion_for
    topics = topic_words(question)
    if not topics:
        return None
    pool = [dict(c) for c in clauses]
    for c in pool:
        if any(is_exclusion_for(c["text"], c, t) for t in topics):
            c["exclusion"] = True
    pool += exclusion_hits(topics, policy_id, {c["id"] for c in pool})
    return pool if addon_vs_exclusion(question, pool) else None


def _extractive(question, lang, clauses, note=None) -> tuple[str, list[dict]]:
    direct = [c for c in clauses if not c.get("expanded_from")] or clauses
    top = sorted(direct, key=lambda c: -(c.get("score") or 0))[:2]
    cards = [card(c, i, "", question) for i, c in enumerate(top, 1)]
    lines = [note or EXTRACTIVE_NOTE[lang]]
    for cd in cards:
        lines.append(f"“{cd['quote']}” [{cd['n']}]")
    return "\n".join(lines), cards


def ask_stream(question: str, policy_id: str, gen: Generator | None = None) -> Iterator[dict]:
    gen = gen or get_generator()
    t0 = time.time()
    hit = cached_final(question, policy_id, gen.name)
    if hit:
        yield {"type": "meta", "lang": hit.get("lang"), "route": "cache", "backend": gen.name}
        yield {**hit, "ms": int((time.time() - t0) * 1000)}
        return
    for ev in _ask_stream(question, policy_id, gen):
        if ev["type"] == "final":
            _cache_put(question, policy_id, gen.name, ev)
        yield ev


def _ask_stream(question: str, policy_id: str, gen: Generator) -> Iterator[dict]:
    t0 = time.time()
    lang = detect_lang(question)
    r = route(question)
    yield {"type": "meta", "lang": lang, "route": r, "backend": gen.name}

    if r in OUT_OF_SCOPE:
        yield {"type": "final", "status": "refused", "reason": r, "answer": OUT_OF_SCOPE[r][lang],
               "citations": [], "lang": lang, "backend": gen.name, "ms": int((time.time() - t0) * 1000)}
        return
    if r == "compare":
        from compare import compare_answer
        yield {"type": "final", **compare_answer(question, lang), "lang": lang, "backend": "fact_table",
               "ms": int((time.time() - t0) * 1000)}
        return

    # a topic the wording never mentions ("robotic" in Optima Secure V08) is "not written" — no model needed
    absent = topic_absent(question, policy_id)
    if absent:
        yield {"type": "final", "status": "not_stated", "answer": NOT_STATED[lang], "citations": [], "lang": lang,
               "backend": "wording_index", "rule": "topic_absent", "absent": absent,
               "ms": int((time.time() - t0) * 1000)}
        return

    clauses = _retrieve(question, policy_id)
    top = max((c["score"] for c in clauses), default=0)
    t_ret = int((time.time() - t0) * 1000)
    if not clauses or top < MIN_SCORE:
        yield {"type": "final", "status": "not_stated", "answer": NOT_STATED[lang], "citations": [],
               "lang": lang, "backend": gen.name, "top_score": top, "ms": t_ret}
        return

    if isinstance(gen, ExtractiveGenerator):
        ans, cards = _extractive(question, lang, clauses)
        yield {"type": "final", "status": "answered", "answer": ans, "citations": cards, "lang": lang,
               "backend": gen.name, "top_score": top, "ms": int((time.time() - t0) * 1000)}
        return

    pool = _addon_case(question, policy_id, clauses)
    if pool:
        topic, e, a, tl = addon_vs_exclusion(question, pool)
        aq = best_sentence(pool[a - 1]["text"], tl, question)
        ans = ADDON_EXCL[lang].format(topic=topic, e=e, a=a, q=aq.strip(" .…"))
        cards = [card(pool[i - 1], i, ans, question) for i in sorted({e, a})]
        yield {"type": "final", "status": "answered", "answer": ans, "citations": cards, "lang": lang,
               "backend": gen.name, "top_score": top, "rule": "addon_vs_exclusion", "ms": int((time.time() - t0) * 1000)}
        return

    wl = waitlist_case(question, clauses, policy_id)
    if wl:
        topic, n, c = wl
        ans = WAITLIST_ANS[lang].format(topic=topic[:1].upper() + topic[1:], n=n, i=1)
        c = {**c, "policy_id": c.get("policy_id") or policy_id, "product": c.get("product", "")}
        yield {"type": "final", "status": "answered", "answer": ans, "citations": [card(c, 1, ans, question)],
               "lang": lang, "backend": gen.name, "top_score": top, "rule": "waiting_list",
               "ms": int((time.time() - t0) * 1000)}
        return

    product = clauses[0].get("product", "")
    prompt = build_prompt(question, clauses, product, lang)
    buf, first_tok = "", None
    for tok in gen.stream(SYSTEM_PROMPT, prompt):
        if first_tok is None:
            first_tok = int((time.time() - t0) * 1000)
        buf += tok
        if not buf.lstrip().startswith("NOT_STATED"[: len(buf.lstrip())]):  # do not stream the sentinel
            yield {"type": "token", "text": tok}
    answer = buf.strip()
    ms = int((time.time() - t0) * 1000)
    common = {"lang": lang, "backend": gen.name, "top_score": top, "ms": ms, "first_token_ms": first_tok,
              "retrieval_ms": t_ret}

    # the sentinel can also leak mid-answer ("AYUSH treatment is NOT_STATED in the provided clauses [1]")
    if not answer or "NOT_STATED" in answer:
        # Small models sometimes refuse a named-disease waiting question even when the
        # general PED clause answers it. Recover with a grounded extractive reply.
        ql = question.lower()
        waiting_q = any(w in ql for w in ("waiting", "intezaar", "ped", "diabetes", "sugar", "pre-existing", "preexisting"))
        ped_hits = [
            c for c in clauses
            if re.search(r"pre[- ]?existing|PED|excluded until", c.get("text", "") + " " + (c.get("section") or ""), re.I)
        ]
        if waiting_q and ped_hits:
            ans, cards = _extractive(
                question,
                lang,
                ped_hits,
                note={
                    "en": "From the pre-existing disease waiting period in this wording:",
                    "hi": "इस पॉलिसी की पूर्व-विद्यमान बीमारी प्रतीक्षा अवधि से:",
                    "hinglish": "Is policy ke pre-existing disease waiting period se:",
                }[lang],
            )
            yield {"type": "final", "status": "answered", "answer": ans, "citations": cards, **common}
            return
        yield {"type": "final", "status": "not_stated", "answer": NOT_STATED[lang], "citations": [], **common}
        return

    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer) if 0 < int(n) <= len(clauses)})
    if not cited:  # model forgot to cite: attach the clauses that best overlap the answer
        ranked = sorted(range(len(clauses)), key=lambda i: -len(_tokens(clauses[i]["text"]) & _tokens(answer)))
        cited = [i + 1 for i in ranked[:2]]

    # verification: the cited clauses must be about the question, and every figure must sit in them
    v = verify(answer, cited, clauses, question, policy_id)
    if v["status"] == "not_stated":
        yield {"type": "final", "status": "not_stated", "answer": NOT_STATED[lang], "citations": [],
               "verify": v, **common}
        return
    answer, cited = v["answer"], v["cited"]

    answer, cited = addon_guard(answer, cited, clauses, lang)
    cards = [card(clauses[i - 1], i, answer, question) for i in cited]
    yield {"type": "final", "status": "answered", "answer": answer, "citations": cards, **common}


# ---------- verification after generation ----------

REL_STOP = {"covered", "cover", "coverage", "covers", "policy", "insurance", "health", "there", "which", "what", "about",
            "under", "does", "milega", "milta", "milti", "kitna", "kitni", "kitne", "hoga", "hogi", "plan", "treatment",
            "expenses", "expense", "included", "charges", "amount", "limit", "this", "that", "with", "from", "have",
            "will", "would", "can", "could", "should", "covered?", "kya", "hai", "hain", "mein", "ka", "ki", "ke",
            "liye", "agar", "toh", "aur", "the", "and", "for", "any", "are", "how", "much", "many", "when", "where",
            "surgery", "surgeries", "operation", "procedure", "claim", "pay", "paid", "payable", "get", "my", "me",
            "mother", "father", "wife", "husband", "customer", "is", "it", "in", "of", "on", "a", "an", "to", "be",
            "need", "needs", "want", "wants", "please", "tell", "batao", "bataiye", "policy?"}
DAY1_ANS = re.compile(r"\bday\s*(?:1|one)\b|\bfirst day\b|from the (?:very )?start|from inception|immediately|"
                      r"without any waiting|no waiting period|pehle din|पहले दिन", re.I)
DAY1_SRC = re.compile(r"\bday\s*(?:1|one)\b|first day|from (?:the )?inception|from the first|without any waiting|"
                      r"no waiting period|waiting period shall not apply|from the commencement", re.I)


def relevance_words(question: str) -> list[str]:
    ql = question.lower()
    out = [TOPIC_ALIAS[k] for k in TOPIC_ALIAS if k in ql]
    out += [w for w in re.findall(r"[a-z]{4,}", ql) if w not in REL_STOP and w not in TOPIC_ALIAS]
    out += [w.lower() for w in re.findall(r"\b[A-Z]{3,5}\b", question)]
    return list(dict.fromkeys(out))


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 5 and w.endswith("s") else w


PED_SENT = re.compile(r"pre-?\s?existing|\bPED\b", re.I)
WAIT_SENT = re.compile(r"(?:specified|specific|listed)[^.]{0,40}(?:disease|illness|condition|procedure)|waiting list", re.I)


def _wording_waits(policy_id: str | None) -> dict:
    """The PED and listed-illness waits read from this product's wording (wording.py), with plan variants."""
    if not policy_id:
        return {}
    try:
        from wording import all_facts
        f = all_facts([policy_id]).get(policy_id, {}).get("facts", {})
    except Exception:
        return {}
    out = {}
    for k, key in (("ped", "ped_wait_months"), ("spec", "specified_disease_wait_months")):
        g = f.get(key, {})
        if g.get("stated"):
            vals = {int(g["value"])} | {int(x) for x in re.findall(r"(\d{1,2}) months", g.get("note") or "")}
            out[k] = vals
    return out


def _day1_supported(pool: str, words: list[str]) -> bool:
    """A day-1 claim needs a day-1 phrase in the cited text close to the question's topic (within 300 chars),
    not just anywhere in some clause about something else."""
    low = pool.lower()
    for m in DAY1_SRC.finditer(pool):
        win = low[max(0, m.start() - 300): m.end() + 300]
        if not words or any(_stem(w) in win for w in words):
            return True
    return False


def verify(answer: str, cited: list[int], clauses: list[dict], question: str, policy_id: str | None = None) -> dict:
    """Drop what the cited clauses do not support; refuse when the clauses are not about the question.

    1. relevance: at least one topic word of the question appears in a cited clause (else not stated;
       e.g. a heart-surgery question must not be answered from Home Care or piles/hernia clauses)
    2. figures: each sentence's numbers (amounts, waits, dates, pages) must be in the clauses it cites
    3. "covered from day 1" needs a clause that says so
    """
    src = lambda i: clauses[i - 1]["text"] + " " + (clauses[i - 1].get("section") or "")
    words = relevance_words(question)
    if words:
        body = " ".join(src(i) for i in cited).lower()
        if not any(_stem(w) in body for w in words):
            return {"status": "not_stated", "reason": f"cited clauses do not mention: {', '.join(words)}"}
    waits = _wording_waits(policy_id)
    kept, dropped = [], []
    for s in re.split(r"(?<=[.!?।])\s+", answer.strip()):
        if not s.strip():
            continue
        refs = [int(n) for n in re.findall(r"\[(\d+)\]", s) if 0 < int(n) <= len(clauses)] or cited
        pool = " ".join(src(i) for i in refs)
        bad = sorted(n for n in _numbers(re.sub(r"\[\d+\]", " ", s)) if n not in _numbers(pool))
        if bad:  # citation repair: only to a clause that holds the figure AND is about the question's topic
            holder = [i for i in range(1, len(clauses) + 1) if all(n in _numbers(src(i)) for n in bad)
                      and (not words or any(_stem(w) in src(i).lower() for w in words))]
            if holder:
                cited = sorted(set(cited) | {holder[0]})
                bad = []
        # a wait stated as the PED / listed-illness wait must be the one this product's wording states
        months = {int(x) for x in re.findall(r"(\d{1,2})[\s-]*(?:\(\s*\w+\s*\)\s*)?months?\b", s)}
        if months and PED_SENT.search(s) and waits.get("ped") and not months & waits["ped"]:
            bad.append("PED wait " + "/".join(map(str, sorted(months))))
        elif months and WAIT_SENT.search(s) and waits.get("spec") and not months & waits["spec"]:
            bad.append("listed-illness wait " + "/".join(map(str, sorted(months))))
        if bad or (DAY1_ANS.search(s) and not _day1_supported(pool, words)):
            dropped.append(s)
            continue
        kept.append(s)
    if not kept:
        return {"status": "not_stated", "reason": "no sentence survived the figure check", "dropped": dropped}
    out = re.sub(r"^(?:However|But|Also|Lekin|Par|लेकिन),?\s+(\w)", lambda m: m.group(1).upper(), " ".join(kept))
    return {"status": "answered", "answer": out, "cited": cited, "dropped": dropped}


WAITLIST_ANS = {
    "en": "{topic} is not a permanent exclusion: it is in the listed-illness waiting list, so it is covered after {n} months of continuous cover [{i}].",
    "hi": "{topic} स्थायी exclusion नहीं है: यह waiting list में है, इसलिए {n} महीने की लगातार कवरेज के बाद कवर है [{i}]।",
    "hinglish": "{topic} permanent exclusion nahi hai: yeh waiting list mein hai, isliye {n} months continuous cover ke baad cover hai [{i}].",
}
WAITLIST_RE = re.compile(r"listed conditions|specified disease|specific waiting|named ailment|Excl\s*0?2\b|"
                         r"waiting period[^.]{0,80}?\d+\s*months", re.I)


def topic_absent(question: str, policy_id: str) -> list[str] | None:
    """The question's topic words when none of them appears anywhere in this product's indexed wording."""
    words = [w for w in relevance_words(question) if len(w) >= 4]
    if not words or not policy_id:
        return None
    from retrieve import _load
    col = _load()[1]
    for w in words:
        for form in {w, w.capitalize(), w.upper(), _stem(w)}:
            if col.get(where={"policy_id": policy_id}, where_document={"$contains": form}, limit=1)["ids"]:
                return None
    return words


WAIT_FACT_TOPIC = [  # topic in the question -> the wording fact (wording.py) that already read its waiting list
    (re.compile(r"(?:knee|hip|joint)\s*replacement|replacement of (?:the )?(?:knee|hip|joint)|ghutne ka operation", re.I),
     "joint_replacement", "Joint replacement"),
]


def waitlist_case(question: str, clauses: list[dict], policy_id: str | None = None) -> tuple[str, int, dict] | None:
    """A coverage question whose topic sits in a waiting list (e.g. joint replacement, 24 months), not in a
    permanent exclusion. Small models read the "Waiting period and exclusions" chapter as "not covered".
    Looks at the retrieved clauses, the product's wording facts, then a keyword search of its clauses
    (with the page before, where a list's heading often sits). Returns (topic, months, clause)."""
    if not COVER_Q.search(question):
        return None
    from retrieve import is_exclusion_for
    if policy_id:
        from wording import all_facts
        f = all_facts([policy_id]).get(policy_id, {}).get("facts", {})
        for rx, field, name in WAIT_FACT_TOPIC:
            g = f.get(field, {})
            if rx.search(question) and g.get("stated") and g.get("months"):
                c = {"text": g["quote"], "page": g["pdf_page"], "printed_page": g.get("printed_page"),
                     "section": g.get("section") or "Waiting list", "policy_id": policy_id, "product": ""}
                return name, g["months"], c
    cands = list(clauses)
    if policy_id:
        from retrieve import _load
        col = _load()[1]
        for t in relevance_words(question)[:3]:
            for form in {t, t.capitalize()}:
                res = col.get(where={"policy_id": policy_id}, where_document={"$contains": form}, limit=6,
                              include=["documents", "metadatas"])
                for doc, m in zip(res["documents"], res["metadatas"]):
                    prev = col.get(where={"$and": [{"policy_id": policy_id}, {"page": m["page"] - 1}]}, include=["documents"])
                    cands.append({"text": doc, "before": " ".join(prev["documents"]), "id": None, **m})
    for t in relevance_words(question):
        for c in cands:
            txt = c["text"]
            pos = txt.lower().find(t)
            if pos < 0:
                continue
            before = (c.get("before") or "")[-2500:] + " " + txt[max(0, pos - 2500):pos]
            if not WAITLIST_RE.search(before) or is_exclusion_for(txt, c, t):
                continue
            ms = [int(x) for x in re.findall(r"(\d{2})\s*months", before[-1600:]) if 6 <= int(x) <= 48]
            if ms:
                return t, ms[-1], c
    return None


ADDON_WORDS = re.compile(r"add[- ]?on|optional|ऐड[- ]?ऑन|वैकल्पिक", re.I)
ADDON_NOTE = {
    "en": "This cover comes only with the optional add-on, not the base policy [{n}].",
    "hi": "यह कवर सिर्फ वैकल्पिक ऐड-ऑन के साथ है, बेस पॉलिसी में नहीं [{n}]।",
    "hinglish": "Yeh cover sirf optional add-on ke saath hai, base policy mein nahi [{n}].",
}
def addon_guard(answer: str, cited: list[int], clauses: list[dict], lang: str) -> tuple[str, list[int]]:
    """The model must not sell an add-on as base cover. When it cites an add-on clause without
    saying so, one fixed sentence (with its citation) is added; the model's text is never removed."""
    addon = [i for i in cited if ADDON_RE.search(clauses[i - 1]["text"])]
    if addon and not ADDON_WORDS.search(answer):
        answer = f"{answer} {ADDON_NOTE[lang].format(n=addon[0])}"
    return answer, cited


def ask(question: str, policy_id: str, gen: Generator | None = None) -> dict:
    final = {}
    for ev in ask_stream(question, policy_id, gen):
        if ev["type"] == "final":
            final = ev
    return final


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--policy", default="national_mediclaim")
    ap.add_argument("--backend")
    a = ap.parse_args()
    res = ask(a.question, a.policy, get_generator(a.backend))
    print(json.dumps({k: v for k, v in res.items() if k != "citations"}, ensure_ascii=False, indent=2))
    for c in res.get("citations", []):
        print(f"  [{c['n']}] p{c['printed_page']} (pdf {c['page']}) {c['section']}: {c['quote']}")
