"""UNIDO — SAP SuccessFactors career site (UNIDO does not use Oracle Recruiting Cloud).

Public site: https://careers.unido.org/search/   (job pages: https://careers.unido.org/job/<slug>/<id>/)
There is no JSON API; the site's RSS feed (/services/rss/job/) answered "Query execution failed"
in Oct 2026, so we read the server-rendered results table, 25 rows per page via &startrow=N.
The "locationsearch" box does not filter (it returns every job), so the whole list is scanned
(16 open jobs in Oct 2026 -> one request).

  * Rome     -> the "Location" column, e.g. "Rome, Italy" (ITPO Italy).
  * P-3      -> the "Grade" column ("P3", "P4", "G5", "NOD", "ISA-P3", ...). "ISA-xx" rows are
                consultancies (Individual Service Agreements at a P-equivalent level), not posts,
                so they are left out unless INCLUDE_ISA is set.
  * deadline -> the "Application Deadline" column, "04-Nov-2026".
Column positions are looked up by their header labels on every run; a renamed column raises.
"""
import datetime as dt
import html
import json
import re
import time
import urllib.parse

from scraper.common import get, is_p3, is_rome, job

AGENCY = "UNIDO"

SITE = "https://careers.unido.org"
SEARCH = SITE + "/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow={}"
MAX_PAGES = 20
DELAY = 0.5
INCLUDE_ISA = False

_ROW = re.compile(r'<tr class="data-row[^"]*">(.*?)</tr>', re.S)
_HEADER = re.compile(r'<th[^>]*\bid="(hdr\w+)"[^>]*>(.*?)</th>', re.S)
_CELL = re.compile(r'<td[^>]*\bheaders="(hdr\w+)"[^>]*>(.*?)</td>', re.S)
_TOTAL = re.compile(r"Results\s*(?:<[^>]+>\s*)*(\d+)\s*[-–]\s*(\d+)\s*(?:<[^>]+>\s*)*of\s*(?:<[^>]+>\s*)*(\d+)")
_LINK = re.compile(r'<a\b(?=[^>]*class="jobTitle-link")[^>]*href="(/job/[^"]+?/(\d+)/?)"[^>]*>(.*?)</a>', re.S)


def _clean(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def _columns(page):
    """{'Title': 'hdrTitle', 'Location': 'hdrLocation', 'Grade': 'hdrFacility', ...}"""
    cols = {_clean(label).lower(): hid for hid, label in _HEADER.findall(page)}
    need = {"title": "title", "location": "location", "grade": "grade", "deadline": "application deadline"}
    missing = [label for label in need.values() if label not in cols]
    if missing:
        raise RuntimeError(f"UNIDO: results table lost column(s) {missing}; headers now {sorted(cols)}")
    return {k: cols[label] for k, label in need.items()}


def list_rows():
    rows, total = {}, None
    for n in range(MAX_PAGES):
        if n:
            time.sleep(DELAY)
        page = get(SEARCH.format(len(rows)))
        m = _TOTAL.search(page)
        if not m:
            raise RuntimeError("UNIDO: no 'Results x – y of N' label on the search page (layout changed?)")
        total = int(m.group(3))
        cols = _columns(page)
        found = _ROW.findall(page)
        before = len(rows)
        for r in found:
            cells = {hid: body for hid, body in _CELL.findall(r)}
            link = _LINK.search(r)
            if not link:
                raise RuntimeError("UNIDO: result row without a /job/<slug>/<id>/ link")
            path, jid, title = link.groups()
            rows[jid] = {
                "id": jid,
                "url": SITE + urllib.parse.quote(html.unescape(path), safe="/%"),  # the site resolves /job/<any slug>/<id>/
                **{k: _clean(cells.get(hid, "")) for k, hid in cols.items()},
                "title": _clean(title),  # the title cell also holds phone-only copies of other columns
            }
        if len(rows) == before or len(rows) >= total:  # empty or repeated page: stop
            break
    if not total:
        raise RuntimeError("UNIDO: search reports 0 open jobs (site or query changed?)")
    if len(rows) < total:
        raise RuntimeError(f"UNIDO: paging got {len(rows)} of {total} jobs")
    return list(rows.values())


def _deadline(s):
    try:
        return dt.datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    out = []
    for r in list_rows():
        if not is_rome(r["location"]) or not is_p3(r["grade"]):
            continue
        if not INCLUDE_ISA and re.match(r"\s*ISA\b", r["grade"], re.I):
            continue
        out.append(job(AGENCY, r["id"], r["title"], re.sub(r"\bP\s*-?\s*(\d)", r"P-\1", r["grade"]),
                       r["location"], _deadline(r["deadline"]), r["url"]))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
