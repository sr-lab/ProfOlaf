import os
import csv
import time
import requests
from bs4 import BeautifulSoup

URL = "https://people.iiti.ac.in/~artiwari/cseconflist.html"

def scrape_core_conferences():
    """
    Scrape all conference data from the CORE Computer Science Conference Rankings
    and save to a CSV file.
    """
    all_conferences = []
    
    print("Starting to scrape CORE Computer Science Conference Rankings...")
    
    try:
        # Make request to the page
        print(f"Fetching data from: {URL}")
        response = requests.get(URL, timeout=30)
        response.raise_for_status()
        
        # Parse HTML content
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the table containing conference data
        table = soup.find('table')
        
        if not table:
            print("No table found. Stopping.")
            return []
        
        # Find all rows in the table
        rows = table.find_all('tr')
        
        if not rows:
            print("No data rows found. Stopping.")
            return []
        
        print(f"Found {len(rows)} rows in the table")
        
        # Extract data from each row (skip header row)
        for i, row in enumerate(rows[1:], 1):  # Skip first row (header)
            cells = row.find_all('td')
            if len(cells) >= 3:  # Ensure we have enough columns
                conference_data = {
                    'acronym': cells[0].get_text(strip=True),
                    'standard_name': cells[1].get_text(strip=True),
                    'rank': cells[2].get_text(strip=True)
                }
                
                # Add any additional columns if they exist
                if len(cells) > 3:
                    conference_data['additional_info'] = [cell.get_text(strip=True) for cell in cells[3:]]
                
                all_conferences.append(conference_data)
        
        print(f"Scraping completed. Total conferences found: {len(all_conferences)}")
        
        # Save to CSV file
        output_file = 'core_conferences.csv'
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            if all_conferences:
                fieldnames = ['acronym', 'standard_name', 'rank']
                # Add additional fields if they exist
                if any('additional_info' in conf for conf in all_conferences):
                    fieldnames.append('additional_info')
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for conference in all_conferences:
                    # Convert additional_info list to string if it exists
                    if 'additional_info' in conference and isinstance(conference['additional_info'], list):
                        conference['additional_info'] = '; '.join(conference['additional_info'])
                    writer.writerow(conference)
        
        print(f"Data saved to {output_file}")
        return all_conferences
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return []
    except Exception as e:
        print(f"Error processing data: {e}")
        return []

if __name__ == "__main__":
    conferences = scrape_core_conferences()
