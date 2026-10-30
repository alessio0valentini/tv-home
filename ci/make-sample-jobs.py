"""Writes sample jobs feeds for the smoke test, with deadlines counted from today:
many.json (matching and non-matching jobs, every kind of deadline), one.json and empty.json.

    python3 ci/make-sample-jobs.py <directory>

QR codes need segno (pip install segno); without it the detail panel shows the link only.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scraper.jobs import qr_rows  # noqa: E402

today = dt.date.today()


def job(n, agency, title, days, new=False):
    url = f"https://example.org/jobs/{agency.lower().replace(' ', '-')}/{n}"
    j = {"id": f"S-{n}", "agency": agency, "title": title, "grade": "P-3", "location": "Rome, Italy",
         "deadline": (today + dt.timedelta(days=days)).isoformat() if days is not None else "",
         "url": url, "source": "sample", "first_seen": today.isoformat() if new else ""}
    qr = qr_rows(url)
    if qr:
        j["qr"] = qr
    return j


def feed(jobs):
    return {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "count": len(jobs),
            "sources": {f"site{i}": {"ok": True, "count": 0} for i in range(11)}, "jobs": jobs}


# Titles with one of the keywords (innovation, transformation, change, management, strategy) and
# without; the card must show only the first kind.
MATCHING = [
    job(1, "FAO", "Innovation Officer (Digital Agriculture)", 0, new=True),
    job(2, "WFP", "Change Management Specialist", 1),
    job(3, "Department of Economic and Social Affairs", "Strategic Planning Officer", 4),
    job(4, "IFAD", "Programme Management Officer, Digital Transformation and Corporate Strategy for Rural Finance", 20),
    job(5, "UNDP", "Strategy and Partnerships Specialist", None),
]
OTHERS = [
    job(6, "FAO", "Investigator", 3),
    job(7, "WFP", "Climate Change Officer", 5),
    job(8, "UNICEF", "Supply Officer", 9),
]

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
for name, jobs in {"many": MATCHING + OTHERS, "one": [MATCHING[3]] + OTHERS, "empty": OTHERS}.items():
    with open(os.path.join(out, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(feed(jobs), f, ensure_ascii=False)
print("sample feeds in", out)
