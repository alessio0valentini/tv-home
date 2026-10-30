"""Builds jobs.json: every open P-3 vacancy with duty station Rome, from the UN agencies' own job sites.

Run from the repository root:  python3 -m scraper.jobs
A GitHub Action runs it every morning and commits jobs.json; the TV's home screen reads that file.

Each module in scraper/sources has fetch() -> list of job dicts (see scraper/common.py).
A source that fails keeps its jobs from the last good run, so one broken site never empties the card.
"""
import datetime as dt
import importlib
import json
import os
import sys
import traceback

from scraper.common import is_p3, is_rome

# One module per agency (or group of agencies) in scraper/sources.
SOURCES = ["fao", "wfp", "ifad", "unsecretariat", "undp", "unhcr", "unido", "unicef", "iom", "ilo", "unops"]
OUT = "jobs.json"


def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    prev = load_previous()
    prev_jobs = prev.get("jobs", []) if prev else []
    first_seen = {j["id"]: j.get("first_seen", "") for j in prev_jobs}

    status, found = {}, []
    for name in SOURCES:
        try:
            try:
                mod = importlib.import_module(f"scraper.sources.{name}")
            except ModuleNotFoundError as e:
                if e.name == f"scraper.sources.{name}":
                    continue  # not written yet
                raise
            got = mod.fetch()
            # The modules filter already; this keeps a slip in one of them off the TV.
            got = [j for j in got if is_p3(j.get("grade", "")) or is_p3(j.get("title", ""))]
            got = [j for j in got if is_rome(j.get("location", "Rome"))]
            for j in got:
                j["source"] = name
            found += got
            status[name] = {"ok": True, "count": len(got)}
            print(f"{name:14} ok    {len(got)}")
        except Exception as e:  # keep yesterday's list for this source
            kept = [j for j in prev_jobs if j.get("source") == name]
            found += kept
            status[name] = {"ok": False, "count": len(kept), "error": f"{type(e).__name__}: {e}"[:300]}
            print(f"{name:14} FAIL  kept {len(kept)} from last run: {e}")
            traceback.print_exc(file=sys.stderr)

    if status and not any(s["ok"] for s in status.values()):
        print("Every source failed: leaving jobs.json as it was.", file=sys.stderr)
        return 1

    jobs, seen_ids, seen_titles = [], set(), set()
    for j in found:
        if j["deadline"] and j["deadline"] < today:
            continue  # closed
        key = (j["agency"].lower(), j["title"].lower(), j["deadline"])
        if j["id"] in seen_ids or key in seen_titles:
            continue
        seen_ids.add(j["id"])
        seen_titles.add(key)
        # On the very first run nothing is "new": everything was simply already open.
        j["first_seen"] = first_seen.get(j["id"], today if prev is not None else "")
        jobs.append(j)
    jobs.sort(key=lambda j: (j["deadline"] or "9999", j["agency"], j["title"]))

    out = {
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(jobs),
        "sources": status,
        "jobs": jobs,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(jobs)} P-3 jobs in Rome written to {OUT}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {len(jobs)} P-3 jobs in Rome\n\n| source | ok | jobs | error |\n|---|---|---|---|\n")
            for name, s in status.items():
                f.write(f"| {name} | {'✅' if s['ok'] else '❌'} | {s['count']} | {s.get('error', '')} |\n")
            f.write("\n")
            for j in jobs:
                f.write(f"- **{j['agency']}** {j['title']} — {j['deadline'] or 'no deadline'} — {j['url']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
