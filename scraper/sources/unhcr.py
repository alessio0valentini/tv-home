"""UNHCR — Workday career site (UNHCR does not use Oracle Recruiting Cloud).

Public site: https://unhcr.wd3.myworkdayjobs.com/External   (linked from unhcr.org/careers)
JSON API (the one the public site itself uses):
  POST {API}/jobs   body {"appliedFacets": {}, "limit": <=20, "offset": n, "searchText": ""}
  GET  {API}{externalPath}   -> {"jobPostingInfo": {...}}

How we filter:
  * Rome     -> the list's locationsText (e.g. "Rome, Italy"). Postings with several duty stations
                show "N Locations" there; those get a detail call and their location +
                additionalLocations are checked. The whole site is scanned (only ~10-300 postings,
                20 per call), so nothing depends on facet ids or fuzzy search.
  * P-3      -> UNHCR codes grades as PR1..PR5 / GS1..GS7 / NOA.. / DI1 (= D-1). The grade is read
                from the "Grade" line of the description, else from the list's bullet fields;
                PR3 -> "P-3". Ranges like "PR3/PR4" count.
  * deadline -> "Deadline for Applications <Month D, YYYY>" at the top of the description; fallback
                is jobPostingInfo.endDate - 1 day (Workday's endDate is the day the posting comes
                down, one day after the deadline: e.g. deadline 5 Nov, endDate 6 Nov).
"""
import datetime as dt
import html
import json
import re
import time

from scraper.common import get_json, is_p3, is_rome, job, post_json

AGENCY = "UNHCR"

SITE = "https://unhcr.wd3.myworkdayjobs.com"
API = SITE + "/wday/cxs/unhcr/External"
PUBLIC = SITE + "/External"
PAGE = 20          # Workday caps limit at 20
MAX_PAGES = 30     # safety stop for a runaway pager
DELAY = 0.5        # seconds between requests

_MULTI = re.compile(r"^\s*\d+\s+Locations?\s*$", re.I)
_GRADE_CODE = re.compile(r"^(?:PR|GS|NO|DI|P|G|D)[A-E\d](?:\s*/\s*(?:PR|GS|NO|DI|P|G|D)?[A-E\d])?$")
_GRADE_LINE = re.compile(r"(?:^|\n)\s*Grade\s*\n\s*([^\n]+)", re.I)
_DEADLINE_LINE = re.compile(r"Deadline for Applications\s*\n\s*([A-Za-z]+ \d{1,2}, \d{4})", re.I)


def _text(desc_html):
    t = re.sub(r"<[^>]+>", "\n", desc_html or "")
    return re.sub(r"\n\s*\n+", "\n", html.unescape(t))


def unhcr_grade(g):
    """'PR3' -> 'P-3', 'PR3/PR4' -> 'P-3/P-4'; other codes unchanged."""
    return re.sub(r"\bPR\s*-?\s*(\d)", r"P-\1", (g or "").strip())


def list_postings():
    seen, total = {}, None
    for page in range(MAX_PAGES):
        if page:
            time.sleep(DELAY)
        data = post_json(API + "/jobs", {"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE, "searchText": ""})
        if "jobPostings" not in data:
            raise RuntimeError(f"UNHCR Workday: unexpected list answer, keys {sorted(data)}")
        if total is None:  # Workday only fills "total" on the first page
            total = int(data.get("total") or 0)
        posts = data["jobPostings"]
        before = len(seen)
        for p in posts:
            seen[p["externalPath"]] = p
        if len(seen) == before or len(seen) >= total:  # empty or repeated page: stop
            break
    if not total:
        raise RuntimeError("UNHCR Workday: site reports 0 open postings (site renamed or API changed?)")
    if len(seen) < total:
        raise RuntimeError(f"UNHCR Workday: paging got {len(seen)} of {total} postings")
    return list(seen.values())


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    out = []
    for p in list_postings():
        loc_text = p.get("locationsText") or ""
        if not (is_rome(loc_text) or _MULTI.match(loc_text)):
            continue
        time.sleep(DELAY)
        info = get_json(API + p["externalPath"]).get("jobPostingInfo")
        if not info:
            raise RuntimeError(f"UNHCR Workday: no jobPostingInfo for {p['externalPath']}")
        locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
        rome = [l for l in locs if is_rome(l)]
        if not rome:
            continue

        text = _text(info.get("jobDescription"))
        m = _GRADE_LINE.search(text)
        cands = [m.group(1) if m else ""] + [b for b in p.get("bulletFields") or [] if _GRADE_CODE.match(b.strip())]
        grade = next((unhcr_grade(g) for g in cands if is_p3(unhcr_grade(g))), "")
        if not grade:
            continue

        m = _DEADLINE_LINE.search(text)
        if m:
            deadline = dt.datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        elif info.get("endDate"):
            deadline = (dt.date.fromisoformat(info["endDate"][:10]) - dt.timedelta(days=1)).isoformat()
        else:
            deadline = ""

        jid = (info.get("jobPostingId") or p["externalPath"]).rsplit("_", 1)[-1]  # e.g. JR2668926-1
        out.append(job(AGENCY, jid, info.get("title") or p.get("title"), grade, rome[0], deadline,
                       info.get("externalUrl") or PUBLIC + p["externalPath"]))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
