"""UNOPS (United Nations Office for Project Services) - Avature career site.

Public site: https://careers.unops.org/careersmarketplace/SearchJobs
(jobs.unops.org only 301-redirects there, and serves an incomplete TLS chain that Python rejects,
so we go straight to careers.unops.org.)

  1. List: GET .../SearchJobs/?jobRecordsPerPage=6&jobOffset=<n>. The page size is fixed at 6
     server-side (larger values are ignored), so ~15 pages for ~85 jobs. Each result shows
     "<span class="list-item-Duty Station">Copenhagen, Home based</span>" (all duty stations,
     city names only) and the total as aria-label="83 results". The site's filters are
     JS autocomplete widgets and the RSS feed (/SearchJobs/feed/) has only titles, capped at 20.
  2. For every result whose Duty Station says Rome: GET the JobDetail page, which has labelled
     fields "Duty Station(s)", "Contract Type", "Contract Level", "ICS Level", "Posting End Date".

  * P-3      -> "Contract Level" of staff posts, e.g. "IP P-3" (Contract Type "Staff - IP - FTA").
                Contractor levels ("IICA 2", "LICA 9") don't count even when "ICS Level" is ICS 10,
                the P-3-equivalent band.
  * deadline -> "Posting End Date" (e.g. "01-Nov-2026").
"""
import html
import json
import re
import time
from datetime import datetime

from scraper.common import get, is_p3, is_rome, job

AGENCY = "UNOPS"

SITE = "https://careers.unops.org/careersmarketplace"
LIST = SITE + "/SearchJobs/?jobRecordsPerPage={size}&jobOffset={offset}"
PAGE = 6            # fixed by the site
MAX_PAGES = 80      # safety stop (~480 jobs)
DELAY = 1.0

_ARTICLE = re.compile(r'<article class="article article--result')
_LINK = re.compile(r'href="(https://careers\.unops\.org/careersmarketplace/JobDetail/[^"/]+/(\d+))"')
_TITLE = re.compile(r'<a class="link"[^>]*>(.*?)</a>', re.S)
_LIST_FIELD = re.compile(r'<span class="list-item-([^"]+)">(.*?)</span>', re.S)
_TOTAL = re.compile(r'aria-label="(\d+) results?"')
_DETAIL_FIELD = re.compile(
    r'<div class="article__content__view__field__label[^"]*"[^>]*>(.*?)</div>\s*'
    r'<div class="article__content__view__field__value[^"]*"[^>]*>(.*?)</div>', re.S)


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _list_page(offset):
    page = get(LIST.format(size=PAGE, offset=offset))
    m = _TOTAL.search(page)
    if not m:
        raise RuntimeError(f"UNOPS: no result count on list page offset={offset} (site changed?)")
    rows = []
    for art in _ARTICLE.split(page)[1:]:
        link = _LINK.search(art)
        if not link:
            continue  # the "No jobs found" placeholder
        title = _TITLE.search(art)
        fields = {k: _text(v) for k, v in _LIST_FIELD.findall(art)}
        if "Duty Station" not in fields:
            raise RuntimeError(f"UNOPS: result {link.group(2)} has no Duty Station field (site changed?)")
        rows.append({"id": link.group(2), "url": link.group(1),
                     "title": _text(title.group(1)) if title else "", "duty": fields["Duty Station"]})
    return int(m.group(1)), rows


def _all_jobs():
    seen, total = {}, None
    for n in range(MAX_PAGES):
        if n:
            time.sleep(DELAY)
        total, rows = _list_page(n * PAGE)
        before = len(seen)
        for r in rows:
            seen[r["id"]] = r
        if len(seen) == before or len(seen) >= total:
            break
    if not total:
        raise RuntimeError("UNOPS: site reports 0 open jobs (site changed?)")
    if len(seen) < total - PAGE:  # a posting may close/open while paging; more than a page off is not that
        raise RuntimeError(f"UNOPS: paging got {len(seen)} of {total} jobs")
    return list(seen.values())


def _details(url):
    fields = {_text(k).rstrip(":"): _text(v) for k, v in _DETAIL_FIELD.findall(get(url))}
    if "Contract Level" not in fields and "Contract Type" not in fields:
        raise RuntimeError(f"UNOPS: no Contract Level/Type on {url} (site changed?)")
    return fields


def _deadline(text):
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    out = []
    for r in _all_jobs():
        if not is_rome(r["duty"]):
            continue
        time.sleep(DELAY)
        d = _details(r["url"])
        level = d.get("Contract Level", "")
        if not is_p3(level):
            continue
        grade = re.sub(r"(?<![A-Za-z])P\s*-?\s*(\d)", r"P-\1", re.sub(r"^\s*(IP|NP)\s+", "", level))
        duty = d.get("Duty Station(s)") or r["duty"]
        out.append(job(AGENCY, r["id"], d.get("Position Title") or r["title"], grade,
                       "Rome, Italy" if duty.strip().lower() in ("rome", "roma") else duty,
                       _deadline(d.get("Posting End Date", "")), r["url"]))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
