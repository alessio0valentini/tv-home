"""UN Secretariat (careers.un.org, the Inspira job board) — the public JSON API the site itself calls.

# UNTESTED here: host blocked by sandbox proxy
# (Two early probes from the sandbox did get a 200 with the response shape used below:
#  {"status": 1, "message": "Success.", "data": {"list": [...], "count": 454}}, each item with
#  jobId, postingTitle, jobLevel, jl{code,name}, dutyStation[{code,description}], dept{code,name},
#  endDate "2026-11-10T04:59:59.000Z". Paging beyond itemPerPage=2 and a full run are untested.)

  POST https://careers.un.org/api/public/opening/jo/list/filteredV2/en
       {"filterConfig": {"keyword": ""},
        "pagination": {"page": <0-based>, "itemPerPage": 50, "sortBy": "startDate", "sortDirection": -1}}
  public page: https://careers.un.org/jobSearchDescription/<jobId>?language=en

The server-side duty-station / level filter keys are not documented anywhere public and could not
be checked, so we page through every opening (~450 in Oct 2026, incl. consultancies -> ~10 calls)
and filter here:
  * Rome     -> any dutyStation[].description is ROME (Inspira writes duty stations in capitals).
  * P-3      -> jobLevel / jl.code / jl.name, e.g. "P-3" (consultancies are "CON", so never match).
  * deadline -> endDate (23:59 New York time, stored in UTC) as a New York date.
  * agency   -> dept.name (the hiring entity), shortened for a few well-known ones; else "UN Secretariat".
"""
import json
import time
from datetime import datetime, timedelta, timezone

from scraper.common import is_p3, is_rome, job, post_json

AGENCY = "UN Secretariat"

API = "https://careers.un.org/api/public/opening/jo/list/filteredV2/en"
PUBLIC = "https://careers.un.org/jobSearchDescription/{}?language=en"
PAGE = 50
MAX_PAGES = 40
DELAY = 0.5

# Full dept.name -> the short name people use. Anything not listed keeps its full name.
SHORT = {
    "office for the coordination of humanitarian affairs": "OCHA",
    "united nations environment programme": "UNEP",
    "united nations office on drugs and crime": "UNODC",
    "office of the united nations high commissioner for human rights": "OHCHR",
    "department of safety and security": "UNDSS",
    "united nations human settlements programme": "UN-Habitat",
    "united nations conference on trade and development": "UNCTAD",
    "department of economic and social affairs": "UN DESA",
    "office of internal oversight services": "OIOS",
}


def deadline_from_utc(stamp):
    """'2026-11-10T04:59:59.000Z' -> '2026-11-09' (end of day New York, the date shown on the posting)."""
    if not stamp:
        return ""
    t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (t - timedelta(hours=5)).date().isoformat() if t.tzinfo else t.date().isoformat()


def list_openings():
    seen, count = {}, None
    for page in range(MAX_PAGES):
        if page:
            time.sleep(DELAY)
        body = {"filterConfig": {"keyword": ""},
                "pagination": {"page": page, "itemPerPage": PAGE, "sortBy": "startDate", "sortDirection": -1}}
        ans = post_json(API, body)
        try:
            if ans.get("status") != 1:
                raise ValueError(f"status {ans.get('status')!r}: {ans.get('message')!r}")
            items = ans["data"]["list"]
            count = int(ans["data"]["count"])
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"careers.un.org: unexpected answer ({e!r})") from e
        before = len(seen)
        for it in items:
            seen[it["jobId"]] = it
        if len(seen) == before or len(seen) >= count:  # empty or repeated page: stop
            break
    if not count:
        raise RuntimeError("careers.un.org: API reports 0 openings (request shape changed?)")
    if len(seen) < count:
        raise RuntimeError(f"careers.un.org: paging got {len(seen)} of {count} openings")
    if not any(it.get("dutyStation") for it in seen.values()):
        raise RuntimeError("careers.un.org: no opening carries 'dutyStation' any more (format changed?)")
    return list(seen.values())


def fetch():
    """Every currently open P-3 vacancy with duty station Rome."""
    out = []
    now = datetime.now(timezone.utc)
    for it in list_openings():
        rome = [d.get("description") or "" for d in it.get("dutyStation") or [] if is_rome(d.get("description"))]
        if not rome:
            continue
        jl = it.get("jl") or {}
        level = " ".join(x for x in (it.get("jobLevel"), jl.get("code"), jl.get("name")) if x)
        if not is_p3(level):
            continue
        end = it.get("endDate") or ""
        if end and datetime.fromisoformat(end.replace("Z", "+00:00")) < now:
            continue  # closed
        dept = ((it.get("dept") or {}).get("name") or "").strip()
        agency = SHORT.get(dept.lower(), dept) or AGENCY
        grade = it.get("jobLevel") or jl.get("code") or "P-3"
        out.append(job(agency, it["jobId"], it.get("postingTitle") or it.get("jobTitle"), grade,
                       f"{rome[0].title()}, Italy", deadline_from_utc(end), PUBLIC.format(it["jobId"])))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
