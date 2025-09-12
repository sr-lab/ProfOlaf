import csv
import os
import pandas as pd
from typing import Dict, Any
from conference_similarity_search import similarity_score

TITLE_COL = "standard_name"
 
def load_core_table(file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Load the core table from a CSV file.
    """
    df = pd.read_csv(file_path)
    return df

def search_core_table(query: str, table: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """ Search the core table for a query. First for an exact match, then for a fuzzy match."""
    exact_match = table[table[TITLE_COL] == query]
    if not exact_match.empty:
        return exact_match.to_dict(orient="records")
    else:
        candidates = []
        for title in table[TITLE_COL]:
            score = similarity_score(query, title)
            candidates.append((title, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates