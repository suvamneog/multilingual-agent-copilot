"""The seven need cases plus the mother story, run against rank.recommend (no browser).

    python eval/seven_cases.py
"""
import json
import sys

sys.path.insert(0, ".")
from rank import recommend  # noqa: E402

CASES = [
    ("1 Cancer", "My father is 58. He had cancer treatment 2 years ago, now he is fine. Will cancer be covered?"),
    ("2 Diabetes (Hinglish)", "meri ma 55 years ki hai. unhe diabetes hai. unke ghutne meh bohot jyada dard rehta hai. humne pehle insurance pehle kuch try kiya tha. but unme pre existing disease ka jo waiting period hota hai, woh 3 saal se jyada ka hota hai. esa kuch hai kya jisse mummy ko hum ek health insurance plan meh dal sake. Esa koi health insurance plan hai kya, jaha peh mummy ko dal sake aur pese bhi kam lage."),
    ("3 Heart", "My mother is 62. She had a bypass 3 years ago and takes BP medicine daily."),
    ("4 Senior healthy", "My father is 68, healthy, no diabetes, no BP, no disease."),
    ("5 Maternity", "My wife is 28. We are planning a baby next year."),
    ("6 Young healthy cheap", "I am 30, no disease, I want a cheap plan with low premium."),
    ("7 Knee replacement", "My mother is 60 and needs a knee replacement surgery in 6 months."),
]

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name, text in CASES:
        r = recommend(text)
        n = r["need"]
        print(f"\n=== {name}")
        print(f"need: kind={n['kind']} age={n['age']} rel={n['relation']} conditions={n['conditions']} absent={n['absent']} "
              f"maternity={n['maternity']} surgery={n['surgery']} low_cost={n['low_cost']}")
        for k in ("best", "second", "watch"):
            c = r[k]
            if c:
                print(f"  {k:6} {c['label']} | {c['months']} months | from {c['cover_date']} | {c['cite']['page'] if c['cite'] else ''} "
                      f"[{(c['cite'] or {}).get('section') or ''}]")
        lang = "hinglish" if name.startswith("2") else "en"
        for line in r["lines"][lang]:
            print("   >", line)
        print("  others:", "; ".join(f"{o['reason']} ({len(o['products'])})" for o in r["others"])[:600])
