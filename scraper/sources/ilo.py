"""ILO (International Labour Organization) - SAP SuccessFactors Recruiting Marketing site.

Public site: https://jobs.ilo.org  (the ILO Office for Italy and San Marino is in Rome)
JSON API (the one the site's own search widget calls; no cookie / CSRF token needed):
  POST https://jobs.ilo.org/services/recruiting/v1/jobs
       {"locale": "en_GB", "pageNumber": n, "keywords": "", "location": "", "facetFilters": {}, ...}
  -> {"totalJobs": N, "jobSearchResult": [{"response": {id, unifiedStandardTitle,
      jobLocationShort ["Rome, Italy "], filter4 ["P3"] (= grade), unifiedStandardEnd "DD/MM/YYYY", ...}}]}

Each posting lives in exactly one language, and a search only returns postings in its own locale
(Oct 2026: 7 en_GB + 1 fr_FR + 4 es_ES), so every site locale is scanned. The whole site is only
a few dozen jobs, so we take everything and filter here rather than trusting the fuzzy
"location" search parameter.

  * Rome     -> jobLocationShort, e.g. "Rome, Italy" (common.is_rome).
  * P-3      -> filter4 (the grade, e.g. "P3", "NOB", "G6") or a P-range in the title.
  * deadline -> unifiedStandardEnd (DD/MM/YYYY, the date printed on the posting).
  * url      -> https://jobs.ilo.org/job/<urlTitle>/<id>-<locale>/
The older RSS feed (/services/rss/category/?catid=2842101) is capped at 10 items, so it is not used.
"""
import json
import re
import time
import urllib.parse

from scraper.common import is_p3, is_rome, job, post_json

AGENCY = "ILO"

SITE = "https://jobs.ilo.org"
API = SITE + "/services/recruiting/v1/jobs"
LOCALES = ("en_GB", "fr_FR", "es_ES")   # the site's language switcher
MAX_PAGES = 30
DELAY = 0.5


def _search(locale, page):
    body = {"locale": locale, "pageNumber": page, "sortBy": "", "keywords": "", "location": "",
            "facetFilters": {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "",
            "rcmCandidateId": ""}
    data = post_json(API, body)
    try:
        total = int(data["totalJobs"])
        rows = [r["response"] for r in data.get("jobSearchResult") or []]
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"ILO: unexpected search answer for {locale} page {page} ({e!r})") from e
    if total and page == 0 and not rows:
        raise RuntimeError(f"ILO: {locale} reports {total} jobs but returned none")
    return total, rows


def _all_jobs():
    """{id: (locale, row)} for every open posting; a posting shown in several locales is kept once."""
    out, grand_total = {}, 0
    for locale in LOCALES:
        seen = {}
        for page in range(MAX_PAGES):
            if out or seen or page:
                time.sleep(DELAY)
            total, rows = _search(locale, page)
            before = len(seen)
            for r in rows:
                seen[r["id"]] = r
            if len(seen) == before or len(seen) >= total:
                break
        if len(seen) < total:
            raise RuntimeError(f"ILO: paging got {len(seen)} of {total} jobs for {locale}")
        grand_total += total
        for jid, r in seen.items():
            out.setdefault(jid, (locale, r))
    if not grand_total:
        raise RuntimeError("ILO: 0 open jobs in every locale - API or site changed?")
    return out


def _deadline(ddmmyyyy):
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", ddmmyyyy or "")
    if not m:
        return ""
    d, mth, y = (int(x) for x in m.groups())
    return f"{y:04d}-{mth:02d}-{d:02d}"


def _pretty_grade(g):
    return re.sub(r"(?<![A-Za-z])P\s*-?\s*(\d)", r"P-\1", g)


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    out = []
    for jid, (locale, r) in _all_jobs().items():
        if "jobLocationShort" not in r or "filter4" not in r:
            raise RuntimeError(f"ILO: job {jid} has no jobLocationShort/filter4 field - API changed?")
        locs = [l.strip() for l in r.get("jobLocationShort") or [] if l and l.strip()]
        rome = [l for l in locs if is_rome(l)]
        if not rome:
            continue
        title = r.get("unifiedStandardTitle") or r.get("title") or ""
        grades = [g for g in r.get("filter4") or [] if g]
        grade = next((g for g in grades if is_p3(g)), "")
        if not grade:
            m = re.search(r"(?<![A-Za-z])P\s*-?\s*\d(?:\s*/\s*P?\s*-?\s*\d)?(?!\d)", title)
            grade = m.group(0) if m and is_p3(m.group(0)) else ""
        if not grade:
            continue
        slug = r.get("unifiedUrlTitle") or r.get("urlTitle") or urllib.parse.quote(title.replace(" ", "-"))
        out.append(job(AGENCY, jid, title, _pretty_grade(grade), rome[0],
                       _deadline(r.get("unifiedStandardEnd")), f"{SITE}/job/{slug}/{jid}-{locale}/"))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
