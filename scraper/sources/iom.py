"""IOM (International Organization for Migration) - Oracle Recruiting Cloud.

Public site: https://fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions
(the "Job vacancies" link on www.iom.int/work-with-us). careers.iom.int is not used: it is
unreachable from the dev sandbox and isn't what iom.int links to. API calls: see oracle_orc.py.

IOM's Coordination Office for the Mediterranean (COCF) is in Rome. Its postings often carry only
the country as PrimaryLocation ("Italy"); the city is only in the detail's workLocation
(TownOrCity "Rome", LocationName "Rome, COCF"). So:

  * candidates -> every list entry whose primary/secondary location or title says Rome, or whose
                  PrimaryLocationCountry is IT (a handful per day); each gets one detail call.
  * Rome     -> list locations, detail workLocation / otherWorkLocations (TownOrCity, LocationName).
                Not the free text: "home based, with occasional travel to Rome" is not a Rome post.
  * P-3      -> requisition flex field "Grade" ("P-3", "G-5", "NO-A", "UG"...), or a P-range in
                the title (oracle_orc.grade).
  * deadline -> list PostingEndDate (YYYY-MM-DD as shown on the site).
"""
import json
import time

from scraper.common import is_p3, is_rome, job
from scraper.sources.oracle_orc import (DELAY, deadline_from_utc, grade, list_requisitions, locations,
                                        pretty_grade, requisition_details)

AGENCY = "IOM"
HOST = "fa-evlj-saasfaprod1.fa.ocs.oraclecloud.com"
SITE = "CX_1001"


def _work_locations(details):
    """'Rome', 'Rome, COCF', ... from the detail's structured work locations."""
    out = []
    for loc in (details.get("workLocation") or []) + (details.get("otherWorkLocations") or []):
        out += [loc.get("TownOrCity") or "", loc.get("LocationName") or ""]
    return [l for l in out if l]


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    out = []
    for req in list_requisitions(HOST, SITE):
        locs = locations(req)
        if not (any(is_rome(l) for l in locs) or is_rome(req.get("Title"))
                or (req.get("PrimaryLocationCountry") or "").upper() == "IT"):
            continue
        time.sleep(DELAY)
        d = requisition_details(HOST, SITE, req["Id"])
        if "workLocation" not in d and "requisitionFlexFields" not in d:
            raise RuntimeError(f"IOM: detail for {req['Id']} has no workLocation/flex fields (API changed?)")
        if not any(is_rome(l) for l in locs + _work_locations(d)):
            continue
        title = d.get("Title") or req.get("Title") or ""
        g = grade(d, title)
        if not is_p3(g):
            continue
        out.append(job(AGENCY, req["Id"], title, pretty_grade(g), "Rome, Italy",
                       req.get("PostingEndDate") or deadline_from_utc(d.get("ExternalPostedEndDate")),
                       f"https://{HOST}/hcmUI/CandidateExperience/en/sites/{SITE}/job/{req['Id']}"))
    return out


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
