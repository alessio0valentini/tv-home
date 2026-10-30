"""Reader for Oracle Recruiting Cloud (ORC) "Candidate Experience" job sites.

Used by undp.py. One paged list call returns every open requisition together with
its primary and secondary locations; only Rome hits get a detail call, which holds
the grade (a requisition flex field on UN tenants, sometimes only in the title)
and the posting end date.

Scanning the whole site is deliberate: the location facets come back truncated to
the top 10 and keyword search is fuzzy, while a full scan costs a few requests
(UNDP: ~130 open jobs in Oct 2026 -> 2 list calls at 100 per page).

  list:    GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
               ?onlyData=true&expand=requisitionList.secondaryLocations
               &finder=findReqs;siteNumber=<site>,limit=100,offset=<n>,sortBy=POSTING_DATES_DESC
  details: GET .../recruitingCEJobRequisitionDetails?onlyData=true&expand=all
               &finder=ById;Id="<id>",siteNumber=<site>
  public:  https://<host>/hcmUI/CandidateExperience/en/sites/<site>/job/<id>
"""
import html
import re
import time
import urllib.parse
from datetime import datetime, timedelta

from scraper.common import get_json, is_p3, is_rome, job

PAGE = 100
MAX_PAGES = 30
DELAY = 0.5  # seconds between requests

_P_GRADE = re.compile(r"(?<![A-Za-z])P\s*-?\s*\d(?:\s*/\s*P?\s*-?\s*\d)?(?!\d)")
_GRADE_IN_TEXT = re.compile(r"\b(?:grade|level)\s*[:\-]?\s*(P\s*-?\s*\d(?:\s*/\s*P?\s*-?\s*\d)?)(?!\d)", re.I)


def deadline_from_utc(stamp):
    """'2026-11-02T04:59:00+00:00' -> '2026-11-01'.

    UN closing times are 23:59 New York time stored in UTC. Shifting by -5h gives the
    calendar day printed on the posting; end-of-day stamps from zones east of New York
    land later in the same UTC day, so they keep their date."""
    if not stamp:
        return ""
    t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if t.tzinfo is None:
        return t.date().isoformat()
    return (t - timedelta(hours=5)).date().isoformat()


def _api(host, resource, finder, expand):
    q = urllib.parse.quote(finder, safe=";=,")
    return f"https://{host}/hcmRestApi/resources/latest/{resource}?onlyData=true&expand={expand}&finder={q}"


def list_requisitions(host, site):
    """Every open requisition on the site (dicts straight from the API). Raises if paging looks wrong."""
    seen, total = {}, None
    for page in range(MAX_PAGES):
        if page:
            time.sleep(DELAY)
        finder = f"findReqs;siteNumber={site},limit={PAGE},offset={page * PAGE},sortBy=POSTING_DATES_DESC"
        data = get_json(_api(host, "recruitingCEJobRequisitions", finder, "requisitionList.secondaryLocations"))
        try:
            search = data["items"][0]
            total = int(search["TotalJobsCount"])
            reqs = search["requisitionList"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise RuntimeError(f"ORC {host}/{site}: unexpected list answer ({e!r})") from e
        before = len(seen)
        for r in reqs:
            seen[r["Id"]] = r
        if len(seen) == before or len(seen) >= total:  # empty or repeated page: stop
            break
    if not total:
        raise RuntimeError(f"ORC {host}/{site}: site reports 0 open jobs (siteNumber or API changed?)")
    if len(seen) < total:
        raise RuntimeError(f"ORC {host}/{site}: paging got {len(seen)} of {total} jobs")
    return list(seen.values())


def requisition_details(host, site, rid):
    data = get_json(_api(host, "recruitingCEJobRequisitionDetails", f'ById;Id="{rid}",siteNumber={site}', "all"))
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"ORC {host}/{site}: no details for requisition {rid}")
    return items[0]


def locations(req):
    """Primary + secondary location names of a list entry. KeyError if ORC renames the field."""
    locs = [req["PrimaryLocation"] or ""]
    locs += [s.get("Name") or "" for s in req.get("secondaryLocations") or []]
    return [l for l in locs if l]


def flex(details, prompt):
    for f in details.get("requisitionFlexFields") or []:
        if (f.get("Prompt") or "").strip().lower() == prompt.lower():
            return (f.get("Value") or "").strip()
    return ""


def grade(details, title):
    """The structured grade (flex field 'Grade' or JobGrade) or, failing that, a 'Grade: P-3' line
    in the description; a P-range in the title (e.g. 'P3/P4') wins when it includes P-3."""
    structured = flex(details, "Grade") or (details.get("JobGrade") or "").strip()
    m = _P_GRADE.search(title or "")
    in_title = m.group(0) if m else ""
    in_text = ""
    if not structured:
        text = " ".join(details.get(k) or "" for k in ("ExternalDescriptionStr", "ShortDescriptionStr"))
        m = _GRADE_IN_TEXT.search(html.unescape(re.sub(r"<[^>]+>", " ", text)))
        in_text = m.group(1) if m else ""
    cands = [g for g in (structured, in_title, in_text) if g]
    return next((g for g in cands if is_p3(g)), cands[0] if cands else "")


def pretty_grade(g):
    return re.sub(r"(?<![A-Za-z])P\s*-?\s*(\d)", r"P-\1", g)


def fetch_rome_p3(agency, host, site):
    out = []
    for req in list_requisitions(host, site):
        rome_locs = [l for l in locations(req) if is_rome(l)]
        if not rome_locs:
            continue
        time.sleep(DELAY)
        d = requisition_details(host, site, req["Id"])
        title = d.get("Title") or req.get("Title") or ""
        g = grade(d, title)
        if not is_p3(g):
            continue
        out.append(job(
            flex(d, "Agency") or agency,
            req["Id"],
            title,
            pretty_grade(g),
            rome_locs[0],
            deadline_from_utc(d.get("ExternalPostedEndDate")),
            f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req['Id']}",
        ))
    return out
