"""FAO (Food and Agriculture Organization, HQ Rome) - Oracle Taleo career section "fao_external".

How it works (tested live from the sandbox, 2026-10-30):
  1. POST /careersection/rest/jobboard/searchjobs  - the JSON endpoint the Taleo search page itself calls.
     No session/cookie/CSRF needed. 25 rows per page; we page until totalCount. Each row gives
     title, job number (contestNo), job type ("Staff position" / "Non-staff opportunities" / "Internship"),
     requisition type ("Professional", "General Service", "PSA", ...), location ("Italy-Rome") and closure date.
     It does NOT give the grade.
     The search only returns requisitions that exist in the requested language, and some FAO posts are
     Spanish- or French-only, so we search en, es and fr_FR and merge by job number (English wins).
  2. For every Rome row that isn't obviously non-staff / General Service, GET the public job page
     jobdetail.ftl?job=<number>, whose embedded data list (fillList 'descRequisition') holds "Grade Level"
     (e.g. "P-3", "P-4", "G-2"). Keep it if the grade is P-3 (ranges like "P-3/P-4" count).

FAO headquarters posts use the location "Italy-Rome". FAO's www.fao.org/employment/vacancies page just
redirects to this Taleo section, so this is the primary source.
"""
import json
import re
import sys
import time

from scraper.common import get, is_p3, is_rome, job, post_json

AGENCY = "FAO"

BASE = "https://jobs.fao.org"
PORTAL = "8105120163"  # Taleo portal number of career section "fao_external" (from jobsearch.ftl)
SEARCH_URL = BASE + "/careersection/rest/jobboard/searchjobs?lang={lang}&portal=" + PORTAL
DETAIL_URL = BASE + "/careersection/fao_external/jobdetail.ftl?job={no}&lang={lang}"
LANGS = ("en", "es", "fr_FR")
# Taleo renders dates in the caller's time zone; closing time is 23:59 Rome time, so ask for Rome time
# (+01:00 is fine all year: in summer it shows 22:59 of the same day).
SEARCH_HEADERS = {"tz": "GMT+01:00", "tzname": "Europe/Rome", "X-Requested-With": "XMLHttpRequest"}
PAUSE = 1.0     # seconds between requests: be polite
MAX_PAGES = 40  # 1000 requisitions per language: far above FAO's ~100, stops a runaway loop

# Rows we never need to open: labels in en / es / fr. Anything not matched is checked on its detail page,
# so an unknown or renamed label only costs a request, never a missed job.
_NOT_STAFF_P = re.compile(
    r"non-staff|\bintern(ship)?s?\b|volunteer|consultant|\bPSA\b|\bNPP\b|general service|"
    r"trabajo temporal|pasant[ií]a|voluntari|consultor|servicios? generales?|"
    r"travail temporaire|\bstages?\b|volontaire|services? g[ée]n[ée]raux",
    re.I)
_HQ = re.compile(r"\b(headquarters|HQ)\b", re.I)
# A grade value as Taleo stores it: "P-3", "P-3/P-4", "G-2", "D-1", "P3 - P4".
_GRADE = re.compile(r"[A-Z]{1,4}\s*-?\s*\d{1,2}(?:\s*(?:/|-|–|,|or|to)\s*[A-Z]{1,4}\s*-?\s*\d{1,2})*")
_JS_STR = re.compile(r"'((?:[^'\\]|\\.)*)'")
_DESC_LIST = re.compile(r"fillList\(\s*'requisitionDescriptionInterface'\s*,\s*'descRequisition'\s*,\s*\[(.*?)\]\s*\)",
                        re.S)
_DATE = re.compile(r"(\d{1,2})/([^/\s,]+)/(\d{4})")
_MONTHS = {
    "jan": 1, "janv": 1, "ene": 1, "feb": 2, "fev": 2, "fév": 2, "févr": 2, "fevr": 2, "mar": 3, "mars": 3,
    "apr": 4, "abr": 4, "avr": 4, "may": 5, "mai": 5, "jun": 6, "juin": 6, "jul": 7, "juil": 7,
    "aug": 8, "ago": 8, "aoû": 8, "août": 8, "aou": 8, "aout": 8, "sep": 9, "sept": 9, "set": 9,
    "oct": 10, "nov": 11, "dec": 12, "déc": 12, "dic": 12,
}


def _search_body(page):
    empty = lambda *ids: [{"id": i, "selectedValues": []} for i in ids]  # noqa: E731
    return {
        "multilineEnabled": True,
        "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
        "fieldData": {"fields": {"KEYWORD": "", "LOCATION": "", "CATEGORY": ""}, "valid": True},
        "filterSelectionParam": {"searchFilterSelections": empty(
            "POSTING_DATE", "LOCATION", "JOB_FIELD", "JOB_TYPE", "JOB_SCHEDULE", "JOB_LEVEL")},
        "advancedSearchFiltersSelectionParam": {"searchFilterSelections": empty(
            "ORGANIZATION", "LOCATION", "JOB_FIELD", "JOB_NUMBER", "URGENT_JOB", "EMPLOYEE_STATUS",
            "STUDY_LEVEL", "WILL_TRAVEL", "JOB_SHIFT")},
        "pageNo": page,
    }


def _search(lang):
    """Every requisition posted in `lang`: list of raw Taleo rows."""
    rows, page, total = [], 1, None
    while True:
        data = post_json(SEARCH_URL.format(lang=lang), _search_body(page), SEARCH_HEADERS)
        time.sleep(PAUSE)
        if not isinstance(data, dict) or "requisitionList" not in data or "pagingData" not in data:
            raise RuntimeError(f"FAO Taleo search ({lang}): unexpected answer {str(data)[:200]}")
        if data.get("careerSectionUnAvailable"):
            raise RuntimeError("FAO Taleo says the career section is unavailable")
        total = int(data["pagingData"]["totalCount"])
        batch = data["requisitionList"]
        rows += batch
        if not batch or len(rows) >= total:
            break
        page += 1
        if page > MAX_PAGES:
            raise RuntimeError(f"FAO Taleo search ({lang}): more than {MAX_PAGES} pages, giving up")
    if len(rows) < total:
        raise RuntimeError(f"FAO Taleo search ({lang}): got {len(rows)} of {total} rows")
    return rows


def _row_fields(row):
    """(number, title, locations, closing date text, other text) from one search row."""
    cols = row.get("column")
    no = str(row.get("contestNo") or "").strip()
    if not no or not isinstance(cols, list) or len(cols) < 3:
        raise RuntimeError(f"FAO Taleo search: unexpected row {str(row)[:200]}")
    title_idx = row.get("linkedColumn", 0) or 0
    title = cols[title_idx] or ""
    loc_idx = set(row.get("locationsColumns") or [])
    locations = []
    for i in loc_idx:
        try:
            v = json.loads(cols[i])
            locations += v if isinstance(v, list) else [str(v)]
        except (ValueError, IndexError, TypeError):
            locations.append(str(cols[i]) if i < len(cols) else "")
    # The closure date is the last column that has a date *and* a time ("18/Nov/2026, 11:59:00 PM").
    closing = next((c for c in reversed(cols) if isinstance(c, str) and _DATE.search(c) and ":" in c), "")
    # Job type, requisition type, dates: everything but the title and locations.
    other = " | ".join(str(c) for i, c in enumerate(cols) if i not in loc_idx and i != title_idx)
    return no, title.strip(), [str(x) for x in locations], closing, other


def _iso_date(text):
    m = _DATE.search(text or "")
    if not m:
        return ""
    day, mon, year = m.groups()
    mon = _MONTHS.get(mon.lower().strip("."))
    return f"{year}-{mon:02d}-{int(day):02d}" if mon else ""


def _display_location(locations):
    """'Italy-Rome' -> 'Rome, Italy' (Taleo writes Country-City)."""
    for loc in locations:
        if is_rome(loc) or _HQ.search(loc):
            country, _, city = loc.partition("-")
            return f"{city.strip()}, {country.strip()}" if city and is_rome(city) else "Rome, Italy"
    return "Rome, Italy"


def _detail_values(no, lang):
    """The data list of a job page: title, dates, unit, job type, requisition type, grade, location, ..."""
    page = get(DETAIL_URL.format(no=no, lang=lang))
    time.sleep(PAUSE)
    m = _DESC_LIST.search(page)
    if not m:
        return None
    return [re.sub(r"\\(.)", r"\1", s).strip() for s in _JS_STR.findall(m.group(1))]


def _grade(values):
    """The 'Grade Level' value: the short field that looks like a grade (P-4, G-2, D-1, P-3/P-4 ...)."""
    for v in values:
        if len(v) <= 25 and _GRADE.fullmatch(v):
            return v
    return ""


def fetch():
    # 1. All open requisitions, English first; Spanish/French-only ones added after.
    rows = {}
    for lang in LANGS:
        for r in _search(lang):
            no, title, locations, closing, other = _row_fields(r)
            rows.setdefault(no, (lang, title, locations, closing, other))
    if not rows:
        raise RuntimeError("FAO Taleo search returned no requisitions at all")  # FAO always has some

    # 2. Rome (or HQ) rows that might be P staff.
    candidates = [(no, *v) for no, v in rows.items()
                  if any(is_rome(x) or _HQ.search(x) for x in v[2]) and not _NOT_STAFF_P.search(v[4])]

    # 3. Read the grade on each candidate's page.
    out, parsed, graded = [], 0, 0
    for no, lang, title, locations, closing, other in candidates:
        values = _detail_values(no, lang)
        if values is None:
            print(f"FAO: job {no}: no data on its page, skipped", file=sys.stderr)
            continue
        parsed += 1
        grade = _grade(values)
        graded += bool(grade)
        if not (is_p3(grade) or (not grade and is_p3(title))):
            continue
        out.append(job(AGENCY, no, title, grade or "P-3", _display_location(locations), _iso_date(closing),
                       DETAIL_URL.format(no=no, lang=lang)))
    if candidates and not parsed:
        raise RuntimeError(f"FAO: none of {len(candidates)} job pages could be read: page format changed?")
    if parsed >= 3 and not graded:
        raise RuntimeError(f"FAO: no grade found on any of {parsed} job pages: page format changed?")
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
