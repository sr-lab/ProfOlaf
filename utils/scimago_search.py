import sys, re, json, html, difflib
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple, Dict, Any
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
from conference_similarity_search import VenueMatch, similarity_score
SCIMAGO_BASE_URL = "https://www.scimagojr.com/"

@dataclass
class JournalRank:
    title: str
    sjr_year: Optional[int]
    sjr_value: Optional[float]
    quartile: Optional[str]
    url: str


def extract_title(url: str, session: requests.Session, headers: Dict[str, str]):
    try: 
        r = session.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.find("h1")
        if title:
            return title.get_text(strip=True)
        else:
            return ""
    except Exception as e:
        pass
    return ""

def scimago_search(venue: str, session: Optional[requests.Session] = None):
    if not venue or not venue.strip():
        raise ValueError("Venue is required")
    session = session or requests.Session()
    url = urljoin(SCIMAGO_BASE_URL, f"journalsearch.php?q={quote_plus(venue)}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ScimagoScraper/1.0)"}
    r = session.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    seen = set()
    candidates = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if not text or any(nav in text for nav in ("Scimago", "Home", "Help", "Country Rankings", "Journal Rankings")):
            continue
        if "journalsearch.php" in href and ("tip=sid" in href or "q=" in href):
            abs_url = urljoin(SCIMAGO_BASE_URL, href)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            clean_title = extract_title(abs_url, session, headers)
            if not clean_title:
                clean_title = text.strip()
            score = similarity_score(venue, clean_title)
            candidates.append(VenueMatch(title=clean_title, url=abs_url, sid=None, similarity_score=score))

    candidates.sort(key=lambda x: x.similarity_score, reverse=True)
    return candidates   
    

def parse_rank_from_detail(html_text: str) -> Tuple[Optional[int], Optional[float], Optional[str]]:
    """Parse the latest SJR year, value, and any inline quartile token."""
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text("\n", strip=True)

    m = re.search(r"SJR\s+(\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s+(Q[1-4])", text, flags=re.I)
    if m:
        return int(m.group(1)), float(m.group(2)), m.group(3).upper()

    sjr_year = None; sjr_val = None
    mblock = re.search(r"SJR\s*(.+?)(?:\n\n|\nTotal Documents|\nCitations per document|\n% International Collaboration|\Z)", text, flags=re.I|re.S)
    sjr_block = mblock.group(1) if mblock else None
    if not sjr_block:
        mstart = re.search(r"SJR", text, flags=re.I)
        if mstart: sjr_block = text[mstart.end(): mstart.end()+1200]
    if sjr_block:
        pairs = re.findall(r"\b((?:19|20)\d{2})\b\s+([0-9]+(?:\.[0-9]+)?)", sjr_block)
        if pairs:
            sjr_year = max(int(y) for y,_ in pairs)
            for y, v in pairs:
                if int(y) == sjr_year:
                    try: sjr_val = float(v)
                    except Exception: sjr_val = None
                    break

    qtoken = re.search(r"\bQ[1-4]\b", text, flags=re.I)
    quartile = qtoken.group(0).upper() if qtoken else None
    return sjr_year, sjr_val, quartile

def fetch_rank(url: str, session: requests.Session):
    session = session or requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ScimagoScraper/1.0)"}
    r = session.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    year, val, q = parse_rank_from_detail(r.text)
    soup = BeautifulSoup(r.text, "html.parser")

    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    else:
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else url
    return JournalRank(title=title, sjr_year=year, sjr_value=val, quartile=q, url=url)
 

def parse_categories_quartile(html_text: str) -> Dict[str, Dict[str, Any]]:
    """Extract per-category quartiles from the 'Quartiles' section of a SCImago page."""
    soup = BeautifulSoup(html_text, "html.parser")
    
    # First try to extract from JavaScript dataquartiles variable
    js_data_match = re.search(r'var dataquartiles = "([^"]+)"', html_text)
    if js_data_match:
        data_string = js_data_match.group(1)
        data: Dict[str, Dict[str, Any]] = {}
        
        lines = data_string.split('\\n')
        for line in lines[1:]:  # Skip header line
            if ';' in line:
                parts = line.split(';')
                if len(parts) >= 3:
                    category = parts[0].strip()
                    year = int(parts[1].strip())
                    quartile = parts[2].strip().upper()
                    
                    entry = {"year": year, "quartile": quartile}
                    bucket = data.setdefault(category, {"entries": []})
                    bucket["entries"].append(entry)
        order = {"Q1":1,"Q2":2,"Q3":3,"Q4":4}
        for cat, bucket in data.items():
            entries = sorted(bucket["entries"], key=lambda e: e["year"])
            bucket["entries"] = entries
            bucket["latest"] = entries[-1] if entries else None
            bucket["best_quartile"] = min(entries, key=lambda e: order.get(e["quartile"], 99))["quartile"] if entries else None
        
        return data
    

def find_scimago_rank(venue: str, min_similarity: float = 0.5):
    """
    Find the rank of the venue from Scimago.
    """
    session = session or requests.Session()
    candidates = scimago_search(venue, session)
    if not candidates:
        raise RuntimeError(f"No candidates found for {venue}")
    best = candidates[0]
    if best.similarity_score < min_similarity:
        for candidate in candidates[:5]:
            if candidate.similarity_score > min_similarity:
                best = candidate; break
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ScimagoScraper/1.0)"}
    r = session.get(best.url, headers=headers, timeout=30)
    r.raise_for_status()
    rank = fetch_rank(best.url, session)
    categories = parse_categories_quartile(r.text)

    current_year = rank.sjr_year
    if current_year is not None:
        for category, bucket in categories.items():
            if not bucket.get("entries"):
                continue
            by_year = {e["year"]: e for e in bucket["entries"] if "year" in e}
            bucket["current"] = by_year.get(current_year, bucket.get("latest"))
    else:
        for category, bucket in categories.items():
            bucket["current"] = bucket.get("latest")
    
    return best, rank, categories

    