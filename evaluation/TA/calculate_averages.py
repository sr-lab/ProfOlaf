#!/usr/bin/env python3
"""
Script to calculate averages from two CSV files with rating data.
Reads two CSV files, calculates average for each rating column between files,
and then calculates final average across all rows.
"""

import pandas as pd
import numpy as np
import os

def main():
    # File paths
    file1 = "evaluation_qa_system_answers Bruno.csv"
    file2 = "evaluation_qa_system_answers Nuno.csv"
    
    # Check if files exist
    if not os.path.exists(file1):
        print(f"Error: {file1} not found")
        return
    if not os.path.exists(file2):
        print(f"Error: {file2} not found")
        return
    
    try:
        # Read both CSV files
        print("Reading CSV files...")
        df1 = pd.read_csv(file1)
        df2 = pd.read_csv(file2)
        
        print(f"File 1 ({file1}): {len(df1)} rows")
        print(f"File 2 ({file2}): {len(df2)} rows")
        
        # Identify the rating columns (columns 3-6, which should be the 4 rating columns)
        # Based on the CSV structure, the rating columns are at positions 2, 3, 4, 5 (0-indexed)
        rating_columns = df1.columns[2:6]  # Skip title and summary columns
        print(f"\nRating columns: {list(rating_columns)}")
        
        # Ensure both dataframes have the same number of rows
        if len(df1) != len(df2):
            print(f"Warning: Files have different number of rows ({len(df1)} vs {len(df2)})")
            min_rows = min(len(df1), len(df2))
            df1 = df1.iloc[:min_rows]
            df2 = df2.iloc[:min_rows]
            print(f"Using first {min_rows} rows from both files")
        
        # Calculate average between the two files for each rating column
        print("\nCalculating averages between files for each rating column...")
        column_averages = {}
        
        for col in rating_columns:
            # Convert to numeric, handling any non-numeric values
            col1_numeric = pd.to_numeric(df1[col], errors='coerce')
            col2_numeric = pd.to_numeric(df2[col], errors='coerce')
            
            # Calculate average between the two files for this column
            avg_between_files = (col1_numeric + col2_numeric) / 2
            column_averages[col] = avg_between_files
            
            print(f"{col}: {avg_between_files.mean():.3f} (average across all rows)")
        
        # Calculate final average for each column across all rows
        print("\nFinal averages across all rows:")
        print("=" * 50)
        
        final_averages = {}
        for col in rating_columns:
            final_avg = column_averages[col].mean()
            final_averages[col] = final_avg
            print(f"{col}: {final_avg:.3f}")
        
        # Calculate overall average across all rating columns
        overall_average = np.mean(list(final_averages.values()))
        print(f"\nOverall average across all rating columns: {overall_average:.3f}")
        
        # Display summary statistics
        print("\nSummary Statistics:")
        print("=" * 50)
        for col in rating_columns:
            col_data = column_averages[col]
            print(f"{col}:")
            print(f"  Mean: {col_data.mean():.3f}")
            print(f"  Std:  {col_data.std():.3f}")
            print(f"  Min:  {col_data.min():.3f}")
            print(f"  Max:  {col_data.max():.3f}")
            print()
        
    except Exception as e:
        print(f"Error processing files: {e}")

if __name__ == "__main__":
    main()
