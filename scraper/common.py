"""Small helpers shared by every source. Standard library only."""
import json
import re
import ssl
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 40
_ctx = ssl.create_default_context()


def get(url, headers=None):
    """GET a URL and return the body as text."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def get_json(url, headers=None):
    return json.loads(get(url, {"Accept": "application/json", **(headers or {})}))


def post_json(url, data, headers=None):
    """POST a JSON body and return the decoded JSON answer."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# "P3", "P-3", "P 3", "P-3/P-4" all count. "SP3", "P-30", "NO-3" don't.
_P3 = re.compile(r"(?<![A-Za-z])P\s*-?\s*3(?!\d)")


def is_p3(text):
    return bool(text) and bool(_P3.search(text))


# "Rome", "Roma", "Rome, Italy", "ITA-Rome" count; "Romania" and "Bucharest, Romania" don't.
_ROME = re.compile(r"\b(rome|roma)\b", re.I)


def is_rome(text):
    return bool(text) and bool(_ROME.search(text))


def job(agency, jid, title, grade, location, deadline, url):
    """The one shape every source returns."""
    return {
        "id": f"{agency}-{jid}",
        "agency": agency,
        "title": " ".join((title or "").split()),
        "grade": " ".join((grade or "P-3").split()),
        "location": " ".join((location or "Rome, Italy").split()),
        "deadline": deadline or "",
        "url": url,
    }
