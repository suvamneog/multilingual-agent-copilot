"""Premium from the insurer's official rate chart (data/premiums/<product id>.json), never from the wording.

Each chart file is copied exactly from the insurer's published rate chart, with its URL, UIN, the date it was
checked, and whether GST is included. A lookup needs the age; the city picks the zone (if the city is not named
in the chart's zone list, the price range across zones is shown instead of a guess). The answer is the base
premium for 1 year before discounts, loadings and optional covers, and says so.
"""
import json, re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent / "data" / "premiums"


@lru_cache(maxsize=None)
def chart(product_id: str) -> dict | None:
    f = DIR / f"{product_id}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def available() -> list[str]:
    return sorted(p.stem for p in DIR.glob("*.json") if not p.stem.startswith("_"))


# the Quickinsure money chart: one fixed customer profile, figures copied from prospectus rate charts / quote pages
PROFILE_FILE = DIR / "_quickinsure-money-chart.json"
PLAN_TO_ID = {
    "Care Freedom": "care-health-care-freedom",
    "Niva ReAssure 2.0": "niva-reassure-2",
    "Star Family Health Optima": "star-family-health-optima",
    "Generali Central Health Absolute": "generali-central-health-absolute",
    "HDFC ERGO Optima Secure V08": "hdfc-ergo-optima-secure-v08-2025-26-wording",
    "Liberty General HealthPrime Connect": "liberty-general-healthprime-connect",
    "Aditya Birla Activ One": "aditya-birla-health-activ-one",
    "Care Supreme": "care-supreme",
}


@lru_cache(maxsize=None)
def money_chart() -> dict:
    return json.loads(PROFILE_FILE.read_text(encoding="utf-8")) if PROFILE_FILE.exists() else {}


def profile_quote(product_id: str) -> dict | None:
    m = money_chart()
    for row in m.get("premiums", []):
        if PLAN_TO_ID.get(row["plan"]) == product_id:
            out = {"profile": m["profile"], "checked": m["checked"], "plan": row["plan"],
                   "annual_premium_inr": row.get("annual_premium_inr"), "source": row.get("source"),
                   "uin": row.get("uin"), "notes": row.get("notes"), "found": row.get("annual_premium_inr") is not None}
            if row.get("uin_old"):
                out["warning"] = (f"This figure is from an older version (UIN {row['uin_old']}), not the current one. "
                                  "Verify with the insurer before quoting.")
            return out
    return None


def _band(label: str, age: int) -> bool:
    label = label.strip()
    m = re.match(r"(\d+)\s*\+$", label)
    if m:
        return age >= int(m.group(1))
    m = re.match(r"(\d+)\s*-\s*(\d+)$", label)
    return bool(m) and int(m.group(1)) <= age <= int(m.group(2))


def _num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def zone_for(c: dict, city: str | None) -> str | None:
    if not city:
        return None
    cl = city.lower()
    for z, places in c["zones"].items():
        for place in re.split(r",\s*", places):
            name = re.sub(r"\s*\(.*?\)", "", place).replace("NCR", "").replace("MMR", "").replace("(Urban)", "").strip().lower()
            if name and not name.startswith("rest of") and name != "roi" and re.search(r"\b" + re.escape(name) + r"\b", cl):
                return z
    # a city the chart does not list falls in "ROI" (rest of India) — only for places the chart cannot mean otherwise
    roi = next((z for z, places in c["zones"].items() if places.strip().upper() == "ROI"), None)
    states = r"assam|guwahati|bihar|odisha|jharkhand|chhattisgarh|madhya pradesh|bhopal|uttar pradesh|lucknow|kanpur|varanasi|" \
             r"rajasthan|jodhpur|udaipur|kerala|kochi|tamil nadu|coimbatore|madurai|west bengal|siliguri|punjab|jalandhar|" \
             r"himachal|shimla|jammu|srinagar|goa|panaji|andhra|tirupati|uttarakhand|meghalaya|shillong|tripura|manipur|nagaland|" \
             r"mizoram|arunachal|sikkim|bhubaneswar|ranchi|raipur"
    rest = " ".join(re.findall(r"rest of ([a-z]+)", " ".join(c["zones"].values()).lower()))
    m = re.search(states, cl)
    if roi and m and not any(w in m.group(0) for w in rest.split()):
        return roi
    return None


RULES = {
    "loading": "Standard healthy-person rate, before any loading for diabetes or another illness. Loading is decided "
               "by the insurer after the health check (usually 10-40%, up to 100-150% in some cases).",
    "gst": "GST on individual health insurance is 0% since 22 Sep 2025.",
    "not_wording": "Premium is not in any policy wording; this comes from the insurer's rate chart or quote page.",
}


def lookup(product_id: str, age: int | None, city: str | None = None, family: str = "1A") -> dict:
    out = _chart_lookup(product_id, age, city, family)
    pq = profile_quote(product_id)
    if pq:
        out["profile_quote"] = pq
    if out.get("available") or pq:
        out["rules"] = RULES
    return out


def _chart_lookup(product_id: str, age: int | None, city: str | None = None, family: str = "1A") -> dict:
    c = chart(product_id)
    if not c:
        return {"available": False}
    src = {k: c.get(k) for k in ("source", "source_url", "uin", "checked_on", "gst", "basis")}
    if age is None:
        return {"available": True, "need": "age", **src}
    zone = zone_for(c, city)
    tables = [t for t in c["tables"] if t["family"] == family and (zone is None or str(t["zone"]) == zone)]
    rows = []
    for t in tables:
        row = next((r for r in t["rows"] if _band(r[0], age)), None)
        if row:
            rows.append((t["zone"], row[0], {_num(h): _num(v) for h, v in zip(t["head"][1:], row[1:])}))
    if not rows:
        return {"available": True, "need": "age_out_of_chart", **src}
    sis = sorted(rows[0][2])
    pick = [s for s in (500000, 1000000, 2500000) if s in sis] or sis[:3]
    out = []
    for s in pick:
        vals = [r[2][s] for r in rows if s in r[2]]
        out.append({"sum_insured": s, "min": min(vals), "max": max(vals)})
    return {"available": True, "age": age, "age_band": rows[0][1], "family": family, "zone": zone,
            "zone_places": c["zones"].get(zone) if zone else None, "zones_shown": len(rows), "prices": out, **src}
