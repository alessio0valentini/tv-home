"""WFP (World Food Programme) — Workday career site.

Public site: https://wd3.myworkdaysite.com/recruiting/wfp/job_openings
JSON API (the one the public site itself uses):
  POST {API}/jobs   body {"appliedFacets": {...}, "limit": <=20, "offset": n, "searchText": ""}
  GET  {API}{externalPath}   -> {"jobPostingInfo": {...}}

How we filter:
  * Rome   -> the "locations" facet value whose label is "Rome, Italy". The facet matches primary
              *and* additional locations of a posting.
  * P-3    -> the structured "Grade" facet (parameter "Management_Level", value "P-3"). WFP often
              does not repeat the grade in the description, so the facet is the reliable signal.
              Ranges: postings graded P-2 or P-4 in Rome are also checked, and kept if the title
              (or a "Grade:/Level:" line in the description) includes P-3, e.g. "P3/P4".
              Consultants (CST), service contracts (SC/SSA Lx), interns, NO-x and G-x have their
              own grade values, so they never match.
  * deadline -> "DEADLINE FOR APPLICATIONS <date>" at the top of the description; fallback is the
              posting's endDate - 1 day (WFP's endDate is consistently the day after the deadline).
Facet ids are looked up by label on every run (one cheap limit=1 query), not hard-coded.
"""
import datetime as dt
import html
import json
import re
import time

from scraper.common import get_json, is_p3, is_rome, job, post_json

AGENCY = "WFP"

SITE = "https://wd3.myworkdaysite.com"
API = SITE + "/wday/cxs/wfp/job_openings"
PUBLIC = SITE + "/recruiting/wfp/job_openings"
PAGE = 20            # Workday caps limit at 20
MAX_PAGES = 25       # safety stop for a runaway pager
DELAY = 0.5          # seconds between requests

LOCATION_FACET = "locations"
GRADE_FACET = "Management_Level"   # shown as "Grade" on the site

_P3_GRADE = re.compile(r"^\s*P\s*-?\s*3\s*$", re.I)              # facet value exactly "P-3"
_NEIGHBOUR_GRADE = re.compile(r"^\s*P\s*-?\s*[24]\s*$", re.I)   # P-2 / P-4 (possible P-3 ranges)
_GRADE_TOKEN = re.compile(
    r"(?<![A-Za-z])P\s*-?\s*(\d)(?:\s*(?:/|-|–|to|or)\s*P?\s*-?\s*(\d))?(?!\d)", re.I)
_DESC_GRADE = re.compile(r"(?i)\b(?:grade|level)\b\s*(?:[:\-–]|is)?\s*(P\s*-?\s*\d[^.;<]{0,15})")
_JR = re.compile(r"JR\d+")

_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DATE_PATTERNS = (
    ("dmy", re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})")),
    ("mdy", re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?!\d)(?:st|nd|rd|th)?,?\s+(\d{4})")),
    ("iso", re.compile(r"(\d{4})-(\d{2})-(\d{2})")),
    ("num", re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})")),   # UN style: day first
)


def _pause():
    time.sleep(DELAY)


def _search(applied, limit=PAGE, all_pages=True):
    """POST the jobs search. Returns (postings, first_page_json). Raises on unexpected shapes."""
    postings, first, offset = [], None, 0
    for _ in range(MAX_PAGES):
        d = post_json(API + "/jobs", {"appliedFacets": applied, "limit": limit,
                                      "offset": offset, "searchText": ""})
        if not isinstance(d, dict) or not isinstance(d.get("jobPostings"), list):
            raise RuntimeError(f"WFP: unexpected search answer: {str(d)[:200]}")
        if first is None:
            first = d
        page = d["jobPostings"]
        postings.extend(page)
        offset += len(page)
        # Workday only fills 'total' on the first page (later pages say 0).
        if not all_pages or not page or offset >= int(first.get("total") or 0):
            return postings, first
        _pause()
    raise RuntimeError("WFP: too many result pages")


def _facet_values(facets, param):
    """Find a facet by its facetParameter (facets can be nested in groups); None if absent."""
    for f in facets or []:
        if not isinstance(f, dict):
            continue
        if f.get("facetParameter") == param:
            return f.get("values") or []
        vals = f.get("values") or []
        if vals and isinstance(vals[0], dict) and "facetParameter" in vals[0]:
            found = _facet_values(vals, param)
            if found is not None:
                return found
    return None


def _text(html_str):
    t = re.sub(r"<[^>]+>", " ", html_str or "")
    return " ".join(html.unescape(t).split())


def _parse_date(s):
    """First date in s -> 'YYYY-MM-DD', or ''."""
    best = None
    for kind, rx in _DATE_PATTERNS:
        for m in rx.finditer(s):
            try:
                if kind == "dmy":
                    d, mon, y = int(m[1]), _MONTHS.get(m[2][:3].lower()), int(m[3])
                elif kind == "mdy":
                    mon, d, y = _MONTHS.get(m[1][:3].lower()), int(m[2]), int(m[3])
                elif kind == "iso":
                    y, mon, d = int(m[1]), int(m[2]), int(m[3])
                else:
                    d, mon, y = int(m[1]), int(m[2]), int(m[3])
                if not mon:
                    continue
                date = dt.date(y, mon, d)
            except ValueError:
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), date)
            break   # earliest match of this pattern is enough
    return best[1].isoformat() if best else ""


def _deadline(info, text):
    m = re.search(r"(?i)deadline|closing\s+date", text)
    if m:
        d = _parse_date(text[m.end():m.end() + 120])
        if d:
            return d
    end = info.get("endDate") or ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        return (dt.date.fromisoformat(end) - dt.timedelta(days=1)).isoformat()
    return ""


def _grade_label(text):
    """'P-3' or a range like 'P-3/P-4' if text mentions one containing 3; '' if no P-3 in text."""
    if not is_p3(text):
        return ""
    for m in _GRADE_TOKEN.finditer(text):
        a, b = m[1], m[2]
        if a == "3" or b == "3":
            lo, hi = sorted((a, b or a))
            return f"P-{lo}/P-{hi}" if lo != hi else "P-3"
    return "P-3"


def _jr(posting):
    for s in (posting.get("bulletFields") or []) + [posting.get("externalPath") or ""]:
        m = _JR.search(str(s))
        if m:
            return m.group(0)
    return ""


def _detail(posting):
    path = posting.get("externalPath") or ""
    if not path.startswith("/job/"):
        raise RuntimeError(f"WFP: posting without externalPath: {posting}")
    d = get_json(API + path)
    info = d.get("jobPostingInfo") if isinstance(d, dict) else None
    if not isinstance(info, dict) or not info.get("title"):
        raise RuntimeError(f"WFP: unexpected detail answer for {path}")
    return info


def _location(info):
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    rome = next((l for l in locs if is_rome(l) and "italy" in l.lower()), "Rome, Italy")
    others = len([l for l in locs if l and l != rome])
    return rome if not others else f"{rome} (+{others} other location{'s' if others > 1 else ''})"


def fetch():
    # 1) Facet ids by label (limit=1: we only want the facets).
    _, first = _search({}, limit=1, all_pages=False)
    if int(first.get("total") or 0) <= 0:
        raise RuntimeError("WFP: search reports zero open jobs worldwide — site or API changed?")
    loc_values = _facet_values(first.get("facets"), LOCATION_FACET)
    grade_values = _facet_values(first.get("facets"), GRADE_FACET)
    if not loc_values or grade_values is None:
        raise RuntimeError("WFP: 'locations' or 'Management_Level' (Grade) facet missing")

    rome_ids = [v["id"] for v in loc_values
                if is_rome(v.get("descriptor")) and "italy" in v.get("descriptor", "").lower()]
    if not rome_ids:
        return []   # facets are healthy, there is simply no open job in Rome today
    p3_ids = [v["id"] for v in grade_values if _P3_GRADE.match(v.get("descriptor") or "")]
    nb_ids = [v["id"] for v in grade_values if _NEIGHBOUR_GRADE.match(v.get("descriptor") or "")]

    # 2) Rome postings graded P-3 (sure) and P-2/P-4 (only if title/description says P-3 range).
    candidates = {}   # JR id -> (posting, grade label or None = decide from description)
    if p3_ids:
        _pause()
        posts, _ = _search({LOCATION_FACET: rome_ids, GRADE_FACET: p3_ids})
        for p in posts:
            candidates.setdefault(_jr(p) or p.get("externalPath"),
                                  (p, _grade_label(p.get("title")) or "P-3"))
    if nb_ids:
        _pause()
        posts, _ = _search({LOCATION_FACET: rome_ids, GRADE_FACET: nb_ids})
        for p in posts:
            label = _grade_label(p.get("title"))
            candidates.setdefault(_jr(p) or p.get("externalPath"), (p, label or None))

    # 3) Detail page per candidate: requisition id, deadline, public URL.
    today = dt.date.today().isoformat()
    out = []
    for key, (p, grade) in candidates.items():
        _pause()
        info = _detail(p)
        text = _text(info.get("jobDescription"))
        if grade is None:   # P-2/P-4 posting without grade in title: look for "Grade: P-3/P-4"
            m = _DESC_GRADE.search(text)
            grade = _grade_label(m.group(1)) if m else ""
            if not grade:
                continue
        if info.get("posted") is False:
            continue
        deadline = _deadline(info, text)
        if deadline and deadline < today:
            continue   # still listed but applications closed
        jid = info.get("jobReqId") or _jr(p) or key
        url = info.get("externalUrl") or PUBLIC + p["externalPath"]
        out.append(job(AGENCY, jid, info.get("title") or p.get("title"), grade,
                       _location(info), deadline, url))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
