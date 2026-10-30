"""UNICEF - PageUp career site behind an AWS WAF bot check.

Public site: https://jobs.unicef.org/en-us/listing/
UNICEF has real Rome P posts (Private Fundraising & Partnerships division, Rome Common Services
Centre), e.g. "Digital Communication Specialist (Web Editor), P3, Fixed-Term position, Rome, Italy".

ONE request per run: GET /en-us/listing/?page=1&page-items=500 returns every open job (283 in
Oct 2026) with title, country list (<span class="location">Italy</span>, sometimes
"Belgium, Denmark, ..., Italy, ...") and deadline. The site is behind an AWS WAF that answers
"HTTP 202, x-amzn-waf-action: challenge, empty body" once an IP makes more than a request or two
in a few minutes; the search page (/en-us/search/?location=Italy&pay-scale=P-3) and the RSS
feed were challenged every time. So everything is decided from the listing:

  * P-3      -> the grade in the title. UNICEF staff posts always carry it there
                ("Child Protection Specialist, P-3, Fixed Term Position, Kabul, Afghanistan, #00138035";
                ranges like "P-3/P-4" count). Consultancies and internships have no grade in the title,
                and they are never P-3 staff posts.
  * Rome     -> "Rome"/"Roma" in the title (staff titles always name the duty station) and Italy in
                the listing's country list or the title. A P-3 Italy post whose title names no city
                gets one detail call ("<b>Duty Station:</b> Rome<br>") - that is the only extra request.
  * deadline -> the listing's close date as printed (local time of the post), e.g. "13 Nov 2026".

A WAF challenge is retried twice (after 90 s and 180 s), then raised: it is a failure, never "0 jobs".
In the final live test the first try was challenged and the retry 90 s later went through.
"""
import html
import json
import re
import time
from datetime import datetime

from scraper.common import get, is_p3, is_rome, job

AGENCY = "UNICEF"

SITE = "https://jobs.unicef.org"
LISTING = SITE + "/en-us/listing/?page={page}&page-items={n}"
PAGE_ITEMS = 500
MAX_PAGES = 10
DELAY = 3            # seconds between requests (only used if >1 listing page or a detail call)
RETRY_WAITS = (90, 180)   # pauses before retrying a WAF challenge (live: 1st try challenged, 2nd ok)

_ITEM_SPLIT = re.compile(r'<div class="list-view--item">')
_LINK = re.compile(r'<a class="job-link" href="(/en-us/job/(\d+)/[^"]*)">(.*?)</a>', re.S)
_LOCATION = re.compile(r'<span class="location">(.*?)</span>', re.S)
_CLOSE = re.compile(r'<span class="close-date">\s*<time datetime="[^"]*">([^<]*)</time>')
_MORE = re.compile(r'class="more-link[^"]*"[^>]*>.*?<span class="count">(\d+)</span>', re.S)
# "P-3", "P3", "(P-3)", "P-3/P-4", "P2-P3"; not "P3E" or "P-30"
_P_GRADE = re.compile(r"(?<![A-Za-z])P\s*-?\s*\d(?:\s*(?:/|-|–|to|or)\s*P?\s*-?\s*\d)?(?![\dA-Za-z])", re.I)


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def _get(url, marker):
    """GET a page that must contain `marker`; retries (with pauses) while the WAF answers with its challenge."""
    for wait in RETRY_WAITS + (None,):
        body = get(url)
        if marker in body:
            return body
        if body.strip() and "awsWafCookieDomainList" not in body and "challenge.js" not in body:
            raise RuntimeError(f"UNICEF: {url} has no {marker!r} - page layout changed?")
        if wait is None:
            raise RuntimeError(f"UNICEF: {url} answered with the AWS WAF bot challenge "
                               f"(HTTP 202, empty body) {len(RETRY_WAITS) + 1} times")
        time.sleep(wait)


def _listing():
    jobs = {}
    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(DELAY)
        body = _get(LISTING.format(page=page, n=PAGE_ITEMS), 'class="list-view--item"')
        parsed = 0
        for block in _ITEM_SPLIT.split(body)[1:]:
            link, loc, close = _LINK.search(block), _LOCATION.search(block), _CLOSE.search(block)
            if not (link and loc):
                raise RuntimeError("UNICEF: listing item without job link/location - markup changed?")
            parsed += 1
            path, jid, title = link.groups()
            jobs.setdefault(jid, {"id": jid, "url": SITE + path, "title": _text(title),
                                  "countries": [c.strip() for c in _text(loc.group(1)).split(",")],
                                  "closes": _text(close.group(1)) if close else ""})
        if not parsed:
            raise RuntimeError(f"UNICEF: listing page {page} has no job items - markup changed?")
        more = _MORE.search(body)
        if not more or int(more.group(1)) == 0:
            return list(jobs.values())
    raise RuntimeError(f"UNICEF: still more jobs after {MAX_PAGES} listing pages")


def _date(text):
    m = re.search(r"\d{1,2} [A-Za-z]{3} \d{4}", text or "")
    try:
        return datetime.strptime(m.group(0), "%d %b %Y").date().isoformat() if m else ""
    except ValueError:
        return ""


def _field(page, label):
    m = re.search(r"<b>\s*{}\s*:?\s*</b>\s*(.*?)<br".format(re.escape(label)), page, re.S | re.I)
    return _text(m.group(1)) if m else ""


def _p3_grade(title):
    return next((g for g in _P_GRADE.findall(title) if is_p3(g)), "")


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    out = []
    for j in _listing():
        title = j["title"]
        in_italy = any(c.lower() == "italy" for c in j["countries"]) or "ital" in title.lower()
        grade = _p3_grade(title)
        if not (in_italy and grade):
            continue
        if not is_rome(title):
            if re.search(r"(?i)\b(florence|firenze|turin|torino|brindisi|venice|venezia|milan|milano|"
                         r"naples|napoli|trieste|bari|palermo|catania|bologna|genoa|genova)\b", title):
                continue  # duty station named, and it is not Rome
            time.sleep(DELAY)
            duty = _field(_get(j["url"], "job-externalJobNo"), "Duty Station")
            if not is_rome(duty):
                continue
        grade = re.sub(r"(?<![A-Za-z])P\s*-?\s*(\d)", r"P-\1", grade.upper())
        out.append(job(AGENCY, j["id"], title, grade, "Rome, Italy", _date(j["closes"]), j["url"]))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
