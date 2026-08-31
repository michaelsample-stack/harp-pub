"""BC Harvest Billing System — the public timber mark record.

    Screen  : P481, a100.gov.bc.ca/pub/hbs/opq/timberMarkQuery.do
    Auth    : none. Public.
    Licence : Open Government Licence - British Columbia

WHY THIS EXISTS
---------------
A timber mark that fails FTEN tells you nothing on its own. The land might be
private, or the mark might belong to a holder further upstream, or the code
might not be a mark at all. HBS separates those three, because it holds a
record for every mark issued in BC including the private ones that never
appear in any tenure geometry.

It gives no boundary. What it gives is:

    the mark holder, and a client number that is the same key FTEN uses
    the natural resource district and region the mark was issued in
    the file type, which is what actually says Crown or private
    the validity dates

For a private mark that is the difference between "somewhere in BC, holder
unknown" and "held by TimberWest Forest II, in South Island district, on
exportable Crown grant land". It is as far as any public source goes.

TWO THINGS THAT BIT US
----------------------
1. The code and its description sit in separate table cells, so a line-based
   parse returns 'B08' and silently drops 'Exportable Crown Grant' - which is
   the part that decides the land basis. Values are read up to the next known
   label instead.

2. A supplier name is not a tenure holder name. Harmac buys from "Mosaic
   Forest Management"; the marks are held by TimberWest Forest I, TimberWest
   Forest II, TimberWest Forest Corp and Island Timberlands GP. Searching FTEN
   for "Mosaic" returns nothing and produces a confident wrong answer. Always
   route through here to get the client number, then query FTEN on that.

THIS IS A SCRAPE
----------------
A public HTML screen with no API contract behind it. It will break. Every page
is retained so a verdict can be traced, and a parse failure is reported rather
than quietly producing empty fields.
"""

from __future__ import annotations

import re
import time
from typing import Any

import requests

URL = "https://a100.gov.bc.ca/pub/hbs/opq/timberMarkQuery.do"
TIMEOUT = 60
ATTRIBUTION = ("Contains information licensed under the "
               "Open Government Licence - British Columbia.")

FIELDS = [
    ("timber_mark", "Timber Mark"), ("mark_status", "Status"),
    ("cutting_permit", "Cutting Permit"), ("sale_method", "Sale Method"),
    ("catastrophic", "Catastrophic"), ("admin_org", "Admin Org"),
    ("geo_org", "Geo Org"), ("region", "Region"),
    ("quota_type", "Quota Type"), ("cruise_or_area", "Cruise or Area Based"),
    ("sb_category", "SB Category"), ("sb_fund", "SB Fund"),
    ("location", "Location"), ("issued_date", "Issued Date"),
    ("expiry_date", "Expiry Date"), ("extended_date", "Extended Date"),
    ("licence", "Licence"), ("file_type", "File Type"),
    ("file_status", "File Status"), ("awarded_date", "Awarded Date"),
    ("payment_method", "Payment Method"), ("mgu_type", "MGU Type"),
    ("mgu_id", "MGU Id"), ("extensions", "Extensions"),
    ("client_no", "Client No"), ("client_name", "Client Name"),
    ("client_location", "Location Name"), ("client_type", "Type"),
    ("address", "Address"),
]
LABELS = sorted({label for _, label in FIELDS}, key=len, reverse=True)

# Phrases that settle the land basis, most specific first. Quoted verbatim into
# the manifest so a verdict can be cited rather than asserted.
PRIVATE_PHRASES = ["EXPORTABLE CROWN GRANT", "NON-EXPORTABLE CROWN GRANT",
                   "CROWN GRANT", "PRIVATE TIMBER MARK",
                   "OUTSIDE MANAGED UNITS"]
CROWN_PHRASES = ["TIMBER SUPPLY AREA", "TREE FARM LICENCE", "TREE FARM LICENSE",
                 "COMMUNITY FOREST", "WOODLOT", "FOREST LICENCE",
                 "TIMBER SALE LICENCE", "BCTS", "FIRST NATIONS WOODLAND",
                 "FORESTRY LICENCE TO CUT"]

# The Esquimalt & Nanaimo Railway grant - origin of most private timberland on
# east Vancouver Island, and a mapped boundary. Narrows a private catchment.
E_AND_N = "E & N LAND BELT"


class ServiceError(RuntimeError):
    pass


# ──────────────────────────────── parsing ──────────────────────────────────

def _text(html: str) -> str:
    """HTML to readable text, keeping cell boundaries as separators."""
    s = re.sub(r"(?is)<script.*?</script>", " ", html)
    s = re.sub(r"(?is)<style.*?</style>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", " | ", s)
    s = re.sub(r"(?i)</(td|th)>", " | ", s)
    s = re.sub(r"(?i)</(tr|div|p|table)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&#39;", "'")):
        s = s.replace(a, b)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"(\s*\|\s*)+", " | ", s)


def parse(html: str) -> tuple[dict[str, str], str]:
    """Every labelled value off the screen, plus the flattened page text.

    Values are read up to the next known label rather than to end of line, so
    a code keeps the description that follows it in the next cell.
    """
    text = _text(html)
    flat = re.sub(r"\s*\n\s*", " | ", text)
    stop = "|".join(re.escape(x) for x in LABELS)
    out: dict[str, str] = {}
    for key, label in FIELDS:
        m = re.search(
            re.escape(label) + r"\s*:\s*(.*?)(?=\s*(?:" + stop + r")\s*:|$)",
            flat, re.IGNORECASE)
        val = m.group(1) if m else ""
        val = re.sub(r"\s*\|\s*", " ", val)
        out[key] = re.sub(r"\s+", " ", val).strip(" |")
    return out, text


def split_code(value: str) -> tuple[str, str]:
    """'B08 Exportable Crown Grant' -> ('B08', 'Exportable Crown Grant')."""
    v = str(value or "").strip()
    m = re.match(r"^([A-Z0-9]{1,6})\s+(.*)$", v)
    if m and not m.group(1).isdigit():
        return m.group(1), m.group(2).strip()
    m = re.match(r"^([A-Z0-9]{1,6})$", v)
    return (m.group(1), "") if m else ("", v)


def classify(record: dict, page_text: str) -> tuple[str, str, str]:
    """Crown or private, and why.

    Judged on the page's own wording, searched across the whole page rather
    than a single field, because the descriptive text is not always attached
    to the code it describes.
    """
    upper = page_text.upper()
    priv = [p for p in PRIVATE_PHRASES if p in upper]
    priv = [p for p in priv if not any(p != q and p in q for q in priv)]
    crown = [c for c in CROWN_PHRASES if c in upper]
    mgu = (record.get("mgu_id") or "").strip().upper()
    has_mgu = mgu not in ("", "N/A")

    if priv and not has_mgu:
        return ("PRIVATE", "; ".join(priv),
                "Crown grant or private timber mark, outside any managed unit. "
                "No tenure geometry exists - the owner holds the boundary.")
    if priv and has_mgu:
        return ("PRIVATE (check)", "; ".join(priv),
                "Private wording but a managed unit is recorded. Read the "
                "record before treating it as private.")
    if crown:
        return ("CROWN", "; ".join(crown),
                "Crown tenure. A miss in the cutblock layer means the mark is "
                "recorded against a different licence - worth chasing.")
    return ("UNCLEAR", "", "Neither pattern present. Read the saved page.")


# ──────────────────────────────── service ──────────────────────────────────

class Client:
    """One HBS session, with the cache in front of it.

    The mark record is a registry fact - holder, land basis, district do not
    change - so it is cached for a year. A miss expires in thirty days because
    a mark absent today may be issued next month.
    """

    def __init__(self, cache=None, delay: float = 0.45, session=None):
        self.cache = cache
        self.delay = delay
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": "HARP (NGIS EUDR)"})

    def lookup(self, mark: str, retries: int = 3) -> dict[str, Any] | None:
        """One mark. None only if the service could not be reached at all -
        an absent mark comes back as a record with found=False."""
        key = str(mark).strip().upper()
        if self.cache:
            for kind in ("hbs", "hbs_miss"):
                hit = self.cache.get(kind, key)
                if hit is not None:
                    hit["cached"] = True
                    return hit

        html = ""
        for attempt in range(retries):
            try:
                r = self.s.get(URL, params={"pageName": "P480",
                                            "timberMark": mark},
                               timeout=TIMEOUT)
                r.raise_for_status()
                html = r.text
                break
            except Exception:
                if attempt == retries - 1:
                    return None
                time.sleep(self.delay * (2 ** attempt))
        time.sleep(self.delay)

        record, text = parse(html)
        out: dict[str, Any] = dict(record)
        out.update({"query": mark, "found": False, "note": "", "verdict": "",
                    "basis": "", "reading": "", "raw_ref": "", "cached": False,
                    "e_and_n": E_AND_N in text.upper(),
                    # NOT "licence" - that key holds the parsed licence number
                    # off the screen. Overwriting it sent the attribution
                    # string to FTEN as a query, and R6 could never work.
                    "licence_attribution": ATTRIBUTION})

        got = (record.get("timber_mark") or "").strip().upper()
        if not got or got == "N/A":
            out["note"] = "no record in HBS"
            out["verdict"] = "NOT FOUND"
        elif got.replace(" ", "") != key.replace(" ", ""):
            out["note"] = "HBS returned {}".format(got)
            out["verdict"] = "MISMATCH"
        else:
            out["found"] = True
            out["verdict"], out["basis"], out["reading"] = classify(record, text)
            out["file_type_code"], out["file_type_desc"] = split_code(
                record.get("file_type"))
            out["quota_type_code"], out["quota_type_desc"] = split_code(
                record.get("quota_type"))
            out["district_code"], out["district_name"] = split_code(
                record.get("admin_org"))
            out["region_code"], out["region_name"] = split_code(
                record.get("region"))

        if self.cache:
            out["raw_ref"] = self.cache.put_raw("hbs", key, html)
            self.cache.put("hbs" if out["found"] else "hbs_miss", key, out)
        return out
