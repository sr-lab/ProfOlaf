from abc import ABC, abstractmethod
from typing import Optional, Dict
import requests
from utils.scimago_search import scimago_search
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup
from utils.conference_similarity_search import VenueMatch, similarity_score

class VenueRankSearchMethod(ABC):
    @abstractmethod
    def search(self, query: str):
        pass
    @abstractmethod
    def get_rank(self, query: str):
        pass


class ScimagoSearchMethod(VenueRankSearchMethod):
    SCIMAGO_BASE_URL = "https://www.scimagojr.com/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ScimagoScraper/1.0)"}
    
    def search(self, venue: str):
        if not venue or not venue.strip():
            raise ValueError("Venue is required")
        session = requests.Session()
        url = urljoin(self.SCIMAGO_BASE_URL, f"journalsearch.php?q={quote_plus(venue)}")
        r = session.get(url, headers=self.headers, timeout=30)
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
                abs_url = urljoin(self.SCIMAGO_BASE_URL, href)
                if abs_url in seen:
                    continue
                seen.add(abs_url)
                clean_title = self._extract_title(abs_url, session, self.headers)
                if not clean_title:
                    clean_title = text.strip()
                score = similarity_score(venue, clean_title)
                candidates.append(VenueMatch(title=clean_title, url=abs_url, sid=None, similarity_score=score))

        candidates.sort(key=lambda x: x.similarity_score, reverse=True)
        return candidates  
    
    def _extract_title(self, url: str, session: requests.Session, headers: Dict[str, str]):
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
    
    def get_rank(self, query: str):
        return scimago_search(query)