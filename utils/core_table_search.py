import csv
import os
import pandas as pd
from typing import Dict, Any
from utils.conference_similarity_search import similarity_score

TITLE_COL = "standard_name"
ACRONYM_COL = "acronym"
 
def load_core_table(file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Load the core table from a CSV file.
    """
    df = pd.read_csv(file_path)
    return df

def search_core_table(query: str, table: pd.DataFrame, acronym: bool = False) -> Dict[str, Dict[str, Any]]:
    """ Search the core table for a query. First for an exact match, then for a fuzzy match."""
    
    column = ACRONYM_COL if acronym else TITLE_COL
    
    exact_match = table[table[column] == query]
    if not exact_match.empty:
        return (exact_match.to_dict(orient="records")[0]["standard_name"], 1.0, exact_match.to_dict(orient="records")[0]["rank"])
    else:
        candidates = []
        for title in table[column]:
            score = similarity_score(query, title)
            # look for the value of the rank column in the title row
            full_title = table.loc[table[column] == title, "standard_name"].values[0]
            rank = table.loc[table[column] == title, "rank"].values[0]
            candidates.append((full_title, score, rank))
        candidates.sort(key=lambda x: x[1], reverse=True)
        # take top 5 candidates, but only if the score is greater than 0.5
        return [tuple(candidate) for candidate in candidates[0:5] if candidate[1] > 0.5]
    
print(search_core_table("FSE ", load_core_table("ranking_tables/core_table1.csv"), acronym=True))