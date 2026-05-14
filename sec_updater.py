"""
SEC EDGAR 13F Auto-Updater
===========================
Fetches the latest 13F filing from Divisadero Street Capital
directly from SEC EDGAR and parses the holdings XML.

Runs on app startup and can be triggered via /api/refresh-holdings.
Caches results to avoid hammering the SEC.
"""

import os
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

# ── Configuration ────────────────────────────────────────────
CIK = "0001901865"  # Divisadero Street Capital Management, LP
SEC_BASE = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FILINGS_URL = f"https://efts.sec.gov/LATEST/search-index?q=%22{CIK}%22&dateRange=custom&startdt=2020-01-01&forms=13F-HR"
EDGAR_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
CACHE_FILE = Path(__file__).resolve().parent / ".holdings_cache.json"
CACHE_TTL_HOURS = 24  # Re-fetch at most once per day
USER_AGENT = "DivisaderoLookalike/1.0 (contact@example.com)"  # SEC requires a User-Agent

# Tickers to always exclude
EXCLUDED_TICKERS = {"APEI"}

# ── CUSIP → Ticker mapping (for common holdings) ────────────
# The 13F XML has CUSIPs but not tickers. We maintain a mapping
# for known holdings and fall back to the issuer name otherwise.
CUSIP_TO_TICKER = {
    "G8588X103": "SGHC",  "23834J201": "DAVE",  "15118V207": "CELH",
    "G4766E116": "INDV",  "G0260P102": "AS",    "146869102": "CVNA",
    "G8068L108": "SN",    "782011100": "RSI",   "302492103": "FLYW",
    "120076104": "BBW",   "78435P105": "SEZL",  "02913V103": "APEI",
    "90041L105": "TPB",   "15101Q207": "CLS",   "30260D103": "FIGS",
    "88339P101": "REAL",  "55024U109": "LITE",  "45245E109": "IMAX",
    "630402105": "NSSC",  "531914109": "LWAY",  "96924N100": "WLDN",
    "926400102": "VSCO",  "09352U108": "BLND",  "26142V105": "DKNG",
    "92847W103": "VITL",  "05463X106": "AXGN",  "91680M107": "UPST",
    "433000106": "HIMS",  "733245AD6": "PRCH",  "20464U100": "COMP",
    "68243Q106": "FLWS",  "88554D205": "DDD",   "00650F109": "ADPT",
    "00486H105": "ADTN",  "02080L102": "TKNO",  "04035M102": "ARHS",
    "04206A101": "ARLO",  "05380C102": "RCEL",  "07831C103": "BRBR",
    "08659B102": "BBNX",  "09075A108": "BVS",   "09624H208": "BXC",
    "15678C102": "CBLL",  "17306X102": "CTRN",  "24661P807": "DCTH",
    "25065K104": "DXLG",  "29357K103": "ENVA",  "302301106": "EZPW",
    "343389409": "FTK",   "35138V102": "FOXF",  "G3R239101": "GAMB",
    "38046C109": "GOGO",  "399473206": "GRPN",  "433313103": "HNGE",
    "45780L104": "INGN",  "46620W201": "JILL",  "479167108": "JOUT",
    "53216B104": "LFMD",  "53222K205": "LFVN",  "54738L109": "LOVE",
    "58450V104": "MAX",   "58470H101": "MED",   "63845R107": "EYE",
    "64049M209": "NEO",   "64111Q104": "NTGR",  "68170A108": "OMDH",
    "68280L101": "ONEW",  "71722W107": "PHAT",  "718172109": "PM",
    "74275G107": "PRTH",  "74340E103": "PGNY",  "74640Y106": "PRPL",
    "75321W103": "PACK",  "75960P104": "RELY",  "749527107": "REVG",
    "778296103": "ROST",  "81728J109": "SRTS",  "83125X103": "SNBR",
    "87043Q108": "SG",    "87305R109": "TTMI",  "88556E102": "TDUP",
    "974155103": "WING",  "989817101": "ZUMZ",  "98955K104": "ZVIA",
    "98980B103": "ZIP",   "G9572D103": "BULL",  "92346J108": "VCEL",
}


def _get_headers():
    """SEC EDGAR requires a descriptive User-Agent."""
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _load_cache():
    """Load cached holdings if fresh enough."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        cached_at = datetime.fromisoformat(cache.get("cached_at", "2000-01-01"))
        if datetime.now() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
            return cache
    except Exception:
        pass
    return None


def _save_cache(data):
    """Save holdings to cache file."""
    data["cached_at"] = datetime.now().isoformat()
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass  # Non-fatal — cache is optional


def fetch_latest_filing_url():
    """
    Find the most recent 13F-HR filing URL from EDGAR submissions API.
    Returns the URL to the infotable.xml file.
    """
    if not requests:
        return None

    # Step 1: Get the filing index
    url = f"https://data.sec.gov/submissions/CIK{CIK}.json"
    resp = requests.get(url, headers=_get_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Find the most recent 13F-HR filing
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])

    for i, form in enumerate(forms):
        if form == "13F-HR":
            accession = accessions[i].replace("-", "")
            accession_dashed = accessions[i]
            filing_date = dates[i]

            # Step 2: Get the filing index to find infotable.xml
            index_url = f"https://www.sec.gov/Archives/edgar/data/{CIK.lstrip('0')}/{accession}/{accession_dashed}-index.html"
            # The infotable.xml is typically the second document
            infotable_url = f"https://www.sec.gov/Archives/edgar/data/{CIK.lstrip('0')}/{accession}/infotable.xml"

            return {
                "infotable_url": infotable_url,
                "filing_date": filing_date,
                "accession": accessions[i],
            }

    return None


def parse_infotable_xml(xml_text):
    """
    Parse the 13F infotable XML and return a list of holdings.
    Combines shares + call options for the same issuer.
    """
    # Remove namespace for easier parsing
    xml_text = xml_text.replace('xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"', '')
    xml_text = xml_text.replace('xmlns:n1="http://www.sec.gov/edgar/document/thirteenf/informationtable"', '')

    root = ET.fromstring(xml_text)
    raw = {}

    for entry in root.findall(".//infoTable"):
        name = entry.findtext("nameOfIssuer", "").strip()
        cusip = entry.findtext("cusip", "").strip()
        # SEC 13F value is in whole dollars since Jan 2023
        # Store as value_k (thousands) to match our hardcoded data format
        value_dollars = int(entry.findtext("value", "0"))
        value_k = round(value_dollars / 1000)
        shares = int(entry.findtext(".//sshPrnamt", "0"))
        put_call = entry.findtext("putCall", "").strip()

        # Use CUSIP to get ticker, fallback to cusip itself
        ticker = CUSIP_TO_TICKER.get(cusip, cusip)

        # Combine entries with same ticker (shares + calls)
        if ticker in raw:
            raw[ticker]["value_k"] += value_k
            raw[ticker]["shares"] += shares
        else:
            raw[ticker] = {
                "ticker": ticker,
                "name": name,
                "cusip": cusip,
                "value_k": value_k,
                "shares": shares,
            }

    # Convert to list
    holdings = list(raw.values())
    holdings.sort(key=lambda h: h["value_k"], reverse=True)
    return holdings


def fetch_and_parse_holdings():
    """
    Full pipeline: fetch latest 13F from EDGAR, parse it, return top holdings.
    Returns None on failure (caller should fall back to hardcoded data).
    """
    if not requests:
        return None

    try:
        # Check cache first
        cache = _load_cache()
        if cache and "holdings" in cache:
            return cache

        # Fetch filing URL
        filing_info = fetch_latest_filing_url()
        if not filing_info:
            return None

        # Fetch the XML
        resp = requests.get(
            filing_info["infotable_url"],
            headers=_get_headers(),
            timeout=30,
        )
        resp.raise_for_status()

        # Parse
        all_holdings = parse_infotable_xml(resp.text)

        # Filter excluded tickers
        eligible = [h for h in all_holdings if h["ticker"] not in EXCLUDED_TICKERS]

        # Top 15
        top15 = eligible[:15]

        # Compute weights
        total_value = sum(h["value_k"] for h in top15)
        for h in top15:
            h["weight"] = round(h["value_k"] / total_value, 6) if total_value > 0 else 0

        result = {
            "holdings": top15,
            "all_holdings_count": len(all_holdings),
            "total_13f_value_k": sum(h["value_k"] for h in all_holdings),
            "top15_value_k": total_value,
            "filing_date": filing_info["filing_date"],
            "accession": filing_info["accession"],
            "source": "sec_edgar_live",
        }

        _save_cache(result)
        return result

    except Exception as e:
        print(f"[SEC Updater] Error fetching 13F: {e}")
        return None


if __name__ == "__main__":
    result = fetch_and_parse_holdings()
    if result:
        print(f"Filing date: {result['filing_date']}")
        print(f"Holdings: {result['all_holdings_count']}")
        print(f"\nTop 15 (excluding {EXCLUDED_TICKERS}):")
        for i, h in enumerate(result["holdings"], 1):
            print(f"  {i:2d}. {h['ticker']:6s} {h['name'][:35]:35s} ${h['value_k']:>12,}k  {h['weight']*100:.2f}%")
    else:
        print("Failed to fetch. Check network/SEC availability.")
