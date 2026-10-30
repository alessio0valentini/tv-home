"""IFAD (International Fund for Agricultural Development, HQ Rome) - Oracle PeopleSoft Candidate Gateway.

How it works (tested live from the sandbox, 2026-10-30; www.ifad.org/en/careers is blocked here but only
links to job.ifad.org anyway):
  1. GET the Candidate Gateway search-results page (Page=HRS_APP_SCHJOB_FL, SiteId=1000, the external
     site IFAD's own links use). PeopleSoft needs session cookies: the first hit answers 302 + Set-Cookie
     and sends you back to the same URL, so a cookie jar is all it takes (no form posts, no ICSID state).
     The server-rendered grid HRS_AGNT_RSLT_I lists every open posting: title, Job Opening ID, location
     (city in capitals, e.g. "ROME"), posted and closing date. No grade.
  2. For every Rome (or HQ / Italy / blank-location) row, GET the posting's public page
     (Page=HRS_APP_JBPST_FL&JobOpeningId=N&PostingSeq=1&SiteId=1000) and read its labelled fields:
     "Location", "Close Date (Midnight, Rome Time)" (MM/DD/YYYY) and "Grade" (e.g. "P-3", "NOB", "P-5").
     Keep it if the grade is P-3 (ranges like "P-3/P-4" count).

IFAD's only duty station in Italy is its Rome headquarters, so "Italy", "HQ" or an empty location are read as Rome.
"""
import html
import http.cookiejar
import json
import re
import ssl
import sys
import time
import urllib.request

from scraper.common import TIMEOUT, UA, is_p3, is_rome, job

AGENCY = "IFAD"

BASE = "https://job.ifad.org/psc/IFHRPRDE/CAREERS/JOBS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
RESULTS_URL = BASE + "?Page=HRS_APP_SCHJOB_FL&Action=U&FOCUS=Applicant&SiteId=1000"
DETAIL_URL = (BASE + "?Page=HRS_APP_JBPST_FL&JobOpeningId={id}&PostingSeq=1&SiteId=1000"
              "&languageCd=ENG&FOCUS=Applicant")
PAUSE = 1.0  # seconds between requests: be polite

_HQ_OR_ITALY = re.compile(r"\b(headquarters|hq|italy|italia)\b", re.I)
_ROWCOUNT = re.compile(r"id='win0divHRS_AGNT_RSLT_Irowcnt\$0'>(?:<[^>]+>)*\s*(\d+)\s+rows?", re.I)
# PeopleSoft formats dates by the browser locale: "11/11/2026" (en-US) or "2026/11/11" (plain "en").
_DATE_YMD = re.compile(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})")
_DATE_MDY = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")


class _Session:
    """GETs that keep PeopleSoft's session cookies (common.get doesn't keep cookies)."""

    def __init__(self):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, url):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*", "Accept-Language": "en-US,en;q=0.8"})
        with self.opener.open(req, timeout=TIMEOUT) as r:
            text = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
            final = r.geturl()
        time.sleep(PAUSE)
        return text, final


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _grid_column(page, field):
    """{row number: text} for one column of the results grid (span id='FIELD$N')."""
    return {int(n): _text(v) for n, v in
            re.findall(r"<span[^>]*\bid='%s\$(\d+)'[^>]*>(.*?)</span>" % re.escape(field), page, re.S)}


def _labelled_fields(page):
    """{label: value} for a Fluid page's read-only fields:
    <div id='win0divFIELDlbl'><span>Label</span></div><span class='ps_box-value' id='FIELD'>value</span>"""
    values = {fid: _text(v) for fid, v in
              re.findall(r"<span[^>]*class='ps_box-value'[^>]*\bid='([^']+)'[^>]*>(.*?)</span>", page, re.S)}
    out = {}
    for fid, label in re.findall(r"id='win0div([^']+?)lbl'[^>]*>\s*<span[^>]*>(.*?)</span>", page, re.S):
        if fid in values:
            out.setdefault(_text(label), values[fid])
    return out


def _field(fields, *prefixes):
    for label, value in fields.items():
        if any(label.lower().startswith(p) for p in prefixes):
            return value
    return ""


def _iso_date(text):
    """'11/11/2026' (MM/DD/YYYY, what en-US gets) or '2026/11/11' -> '2026-11-11'."""
    m = _DATE_YMD.search(text or "")
    if m:
        year, month, day = (int(x) for x in m.groups())
    else:
        m = _DATE_MDY.search(text or "")
        if not m:
            return ""
        month, day, year = (int(x) for x in m.groups())
        if month > 12:  # a DD/MM/YYYY locale after all
            month, day = day, month
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year}-{month:02d}-{day:02d}"


def _maybe_rome(location):
    return not location.strip() or is_rome(location) or bool(_HQ_OR_ITALY.search(location))


def fetch():
    s = _Session()
    page, _ = s.get(RESULTS_URL)
    if "HRS_AGNT_RSLT_I" not in page:
        raise RuntimeError("IFAD: job results grid not found (session or page format changed?)")

    ids = _grid_column(page, "HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID")
    titles = _grid_column(page, "SCH_JOB_TITLE")
    locs = _grid_column(page, "LOCATION")
    closes = _grid_column(page, "HRS_JO_PST_CLS_DT")
    m = _ROWCOUNT.search(page)
    if m and int(m.group(1)) != len(ids):
        # A PeopleSoft grid may load long lists in chunks; warn so a cut list doesn't go unnoticed.
        print(f"IFAD: grid says {m.group(1)} rows but {len(ids)} were readable", file=sys.stderr)
    if not ids and (not m or int(m.group(1)) > 0):
        raise RuntimeError("IFAD: results grid has no readable rows (page format changed?)")

    out, parsed, graded = [], 0, 0
    for n in sorted(ids):
        jid = ids[n]
        if not jid.isdigit() or not _maybe_rome(locs.get(n, "")):
            continue
        url = DETAIL_URL.format(id=jid)
        detail, _ = s.get(url)
        f = _labelled_fields(detail)
        # Vacancies show "ID" = the opening id; standing registers ("Register as a Consultant") show only
        # "Job Title". Anything else means the posting page did not open.
        if f.get("ID", jid) != jid or not (f.get("ID") or _field(f, "job title")):
            print(f"IFAD: posting {jid}: its page did not open (PostingSeq not 1?), skipped", file=sys.stderr)
            continue
        parsed += 1
        title = _field(f, "job title") or titles.get(n, "")
        grade = _field(f, "grade")
        graded += bool(grade)
        location = _field(f, "location") or locs.get(n, "")
        if not (is_p3(grade) or (not grade and is_p3(title))):
            continue
        if not _maybe_rome(location):
            continue
        deadline = _iso_date(_field(f, "close date")) or _iso_date(closes.get(n, ""))
        out.append(job(AGENCY, jid, title, grade or "P-3", "Rome, Italy", deadline, url))

    candidates = sum(1 for n in ids if ids[n].isdigit() and _maybe_rome(locs.get(n, "")))
    if candidates and not parsed:
        raise RuntimeError(f"IFAD: none of {candidates} posting pages could be read (page format changed?)")
    if parsed >= 3 and not graded:
        raise RuntimeError(f"IFAD: no grade found on any of {parsed} posting pages (page format changed?)")
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
