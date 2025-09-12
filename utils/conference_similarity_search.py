import sys, re, os, html, difflib
from dataclasses import dataclass
from typing import Optional

@dataclass
class VenueMatch:
    title: str
    url: str
    sid: Optional[str]
    similarity_score: float


def _normalize(s: str) -> str:
    """
    Normalize the string by removing special characters and converting to lowercase.
    """
    s = html.unescape(s or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def similarity_score(s1: str, s2: str) -> float:
    """
    Calculate the similarity score between two strings.
    """
    s1 = _normalize(s1)
    s2 = _normalize(s2)
    return difflib.SequenceMatcher(None, s1, s2).ratio()