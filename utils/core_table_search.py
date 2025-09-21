import csv
import os
import pandas as pd
from typing import Dict, Any
from utils.conference_similarity_search import similarity_score
from dataclasses import dataclass
TITLE_COL = "standard_name"
ACRONYM_COL = "acronym"
RANK_COL = "rank"

@dataclass
class CoreRank:
    title: str
    acronym: str
    similarity_score: float
    rank: str

def load_core_table(file_path: str) -> pd.DataFrame:
    """
    Load a CSV table from a file with flexible column handling.
    Automatically detects the structure and handles malformed rows.
    """
    import csv
    
    with open(file_path, 'r', encoding='utf-8') as file:
        reader = csv.reader(file)
        
        # Read the header to determine expected columns
        header = next(reader)
        expected_columns = [col.strip() for col in header]
        num_expected_cols = len(expected_columns)
        
        data = []
        malformed_rows = 0
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 since we skipped header
            # Clean and process the row
            cleaned_row = []
            
            # Take only the expected number of columns, pad with None if needed
            for i in range(num_expected_cols):
                if i < len(row):
                    # Clean the field - remove leading/trailing whitespace
                    field = str(row[i]).strip()
                    # Replace empty strings with None
                    if field == '' or field == 'nan':
                        field = None
                    cleaned_row.append(field)
                else:
                    cleaned_row.append(None)
            
            # Only add rows that have at least some data
            if any(field is not None for field in cleaned_row):
                data.append(cleaned_row)
            else:
                malformed_rows += 1
    
    # Create DataFrame with detected column names
    df = pd.DataFrame(data, columns=expected_columns)
    
    return df

def search_core_table(query: str, table: pd.DataFrame, acronym_search: bool = False, top_k: int = 5) -> list:
    """ 
    Search the core table for a query. First for an exact match, then for a fuzzy match.
    
    Args:
        query: The search term
        table: The DataFrame to search in
        acronym: Whether to search in acronym column (True) or title column (False)
        title_col: Name of the title column (auto-detected if None)
        acronym_col: Name of the acronym column (auto-detected if None)
        rank_col: Name of the rank column (auto-detected if None)
    """
    

    title_col = TITLE_COL 
    acronym_col = ACRONYM_COL 
    rank_col = RANK_COL
    
    # Determine which column to search in
    search_column = acronym_col if acronym_search and acronym_col else title_col
    
    if search_column is None:
        print(f"Warning: Could not find appropriate search column. Available columns: {table.columns.tolist()}")
        return []
    
    # Perform exact match search
    exact_match = table[table[search_column] == query]
    if not exact_match.empty:
        result_row = exact_match.iloc[0]
        title = result_row[title_col] if title_col else result_row[search_column]
        rank = result_row[rank_col] if rank_col else "Unknown"
        acronym = result_row[acronym_col] if acronym_col else ""
        return [CoreRank(title=title, acronym=acronym, similarity_score=1.0, rank=rank)]
    elif acronym_search:
        return []
    else:
        # Perform fuzzy search
        candidates = []
        for idx, row in table.iterrows():
            search_value = row[search_column]
            if pd.isna(search_value):
                continue
                
            score = similarity_score(query, str(search_value))
            title = row[title_col] if title_col else search_value
            rank = row[rank_col] if rank_col else "Unknown"
            acronym = row[acronym_col] if acronym_col else ""
            candidates.append(CoreRank(title=title, acronym=acronym, similarity_score=score, rank=rank))
        
        candidates.sort(key=lambda x: x.similarity_score, reverse=True)
        # Return top 5 candidates with score > 0.5
        return [candidate for candidate in candidates[:top_k] if candidate.similarity_score > 0.5]
    


# Example usage
if __name__ == "__main__":
    # Load the CSV file (works with any CSV structure)
    df = load_core_table("ranking_tables/core_table2.csv")
    
    # Example searches using auto-detected columns
    print("\nExample searches:")
    results = search_core_table("SIGCOMM", df, acronym=True)
    print(f"Search for 'SIGCOMM' acronym: {results[0] if results else 'No results'}")
    
    results = search_core_table("Computer Communication", df, acronym=False)
    print(f"Search for 'Computer Communication' in title: {results[0] if results else 'No results'}")
    
    # You can also specify column names explicitly if needed
    # results = search_core_table("SIGCOMM", df, acronym=True, 
    #                            title_col="standard_name", acronym_col="acronym", rank_col="rank")
