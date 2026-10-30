"""UNDP (also UNCDF / UNV posts on the same site) — Oracle Recruiting Cloud.

Public site: https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions
(jobs.undp.org redirects there). See oracle_orc.py for the API calls.

  * Rome     -> primary or secondary location of the requisition, e.g. "Rome, Italy"
                (UNDP Rome Centre for Climate Action and Energy Transition posts use it).
  * P-3      -> requisition flex field "Grade" (values like "P3", "P5", "IPSA-10", "NOB"), or a
                P-range in the title. IPSA/NPSA service contracts never count.
  * deadline -> ExternalPostedEndDate (23:59 New York, stored in UTC) as a New York date.
  * agency   -> flex field "Agency" ("UNDP", "UNCDF", ...), default "UNDP".
"""
import json

from scraper.sources.oracle_orc import fetch_rome_p3

AGENCY = "UNDP"
HOST = "estm.fa.em2.oraclecloud.com"
SITE = "CX_1"


def fetch():
    """Every currently open P-3 vacancy (incl. ranges like P-3/P-4) with duty station Rome."""
    return fetch_rome_p3(AGENCY, HOST, SITE)


if __name__ == "__main__":
    print(json.dumps(fetch(), indent=2, ensure_ascii=False))
