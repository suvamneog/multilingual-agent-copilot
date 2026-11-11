"""Product master list: who sells what, under which name, and whether it is still on sale.

Only identity and status come from web/data/catalogue.json (name, insurer, variant, UIN,
status, withdrawn date, replacement). Those are product-master records, not policy terms,
and a wording does not say when the insurer stopped selling it. Every policy term
(waits, ages, room rent, maternity, exclusions ...) comes from wording.py instead.
"""
from __future__ import annotations

import json
from functools import lru_cache

from config import ROOT

MASTER_FIELDS = ("id", "policy_id", "insurer", "name", "variant", "status", "withdrawn_on", "replaced_by",
                 "stop_reason", "uin", "source_url")


@lru_cache(maxsize=1)
def _catalogue() -> dict:
    return json.loads((ROOT / "web" / "data" / "catalogue.json").read_text(encoding="utf-8"))


def registry() -> list[dict]:
    out = []
    for p in _catalogue()["products"]:
        r = {k: p.get(k) for k in MASTER_FIELDS}
        r["policy_id"] = r["policy_id"] or p["id"].replace("-", "_")
        out.append(r)
    return out


def by_id() -> dict[str, dict]:
    return {p["id"]: p for p in registry()}


def label(p: dict) -> str:
    return f"{p['insurer']} {p['name']}{(' ' + p['variant']) if p.get('variant') else ''}"
