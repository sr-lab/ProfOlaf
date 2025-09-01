#!/usr/bin/env python3

import csv
import os
import sys
import re
import pathlib
from urllib.parse import urljoin
import requests
import time

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def _looks_like_pdf(headers: dict, first_bytes: bytes) -> bool:
    ctype = headers.get("Content-Type", "").split(";")[0].strip().lower()
    return (ctype == "application/pdf") or first_bytes.startswith(b"%PDF")

def _extract_pdf_url(html: str, base_url: str) -> str | None:
    m = re.search(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\'>]+)', html, re.I)
    if m:
        return urljoin(base_url, m.group(1).strip())

    for tag in ("iframe", "embed", "a"):
        m = re.search(rf'<{tag}[^>]+(?:src|href)=["\']([^"\']+\.pdf[^"\']*)', html, re.I)
        if m:
            return urljoin(base_url, m.group(1).strip())

    m = re.search(r'(?:href|src)=["\']([^"\']*getPDF\.jsp[^"\']*)', html, re.I)
    if m:
        return urljoin(base_url, m.group(1).strip())

    m = re.search(r'href=["\']([^"\']+\.pdf[^"\']*)', html, re.I)
    if m:
        return urljoin(base_url, m.group(1).strip())

    return None

def download_pdf(url: str, output_path: str, timeout: int = 30) -> bool:
    """
    Downloads a PDF from a URL, handling meta-refresh and iframe redirects.
    Returns True on success, False on failure.
    """
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
        "Referer": url,
    }

    try:
        with requests.Session() as s:
            r = s.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            
            it = r.iter_content(chunk_size=8192)
            first = next(it, b"")
            
            if _looks_like_pdf(r.headers, first):
                with open(output_path, "wb") as f:
                    if first:
                        f.write(first)
                    for chunk in it:
                        if chunk:
                            f.write(chunk)
                return True

            r.close()
            r_html = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            r_html.raise_for_status()
            html = r_html.text

            pdf_url = _extract_pdf_url(html, r_html.url)
            if not pdf_url:
                print(f"  No PDF link found on page")
                return False

            print(f"  Found PDF URL: {pdf_url}")
            r2 = s.get(pdf_url, headers=headers, stream=True, timeout=timeout, allow_redirects=True)
            r2.raise_for_status()
            
            it2 = r2.iter_content(chunk_size=8192)
            first2 = next(it2, b"")
            
            if not _looks_like_pdf(r2.headers, first2):
                print(f"  Resolved link is not a PDF")
                return False

            with open(output_path, "wb") as f:
                if first2:
                    f.write(first2)
                for chunk in it2:
                    if chunk:
                        f.write(chunk)
            return True

    except Exception as e:
        print(f"  Error downloading {url}: {e}")
        return False

def is_valid_pdf(file_path):
    try:
        if not os.path.exists(file_path):
            return False
        
        file_size = os.path.getsize(file_path)
        if file_size < 100:
            return False
        
        with open(file_path, 'rb') as f:
            header = f.read(8)
            if header.startswith(b'%PDF-'):
                return True
            return False
    except Exception:
        return False

def main():
    if len(sys.argv) != 3:
        print("Usage: python download_articles.py <csv_file> <output_folder>")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    output_folder = sys.argv[2]
    
    if not os.path.exists(csv_file):
        print(f"CSV file not found: {csv_file}")
        sys.exit(1)
    
    pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)
    
    failed_downloads = []
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)      

        for row in reader:
            article_id = row.get('', '0')
            eprint_url = row.get('eprint_url', '').strip()
            
            if not eprint_url:
                continue
            
            output_file = os.path.join(output_folder, f"{article_id}.pdf")
            
            if os.path.exists(output_file):
                print(f"File already exists: {output_file}")
                continue
            
            print(f"Downloading {article_id}.pdf from {eprint_url}")
            
            if download_pdf(eprint_url, output_file):
                if is_valid_pdf(output_file):
                    print(f"Successfully downloaded and verified: {output_file}")
                    time.sleep(1)
                else:
                    print(f"Downloaded but invalid PDF: {output_file}")
                    os.remove(output_file)
                    failed_downloads.append((article_id, eprint_url))
            else:
                failed_downloads.append((article_id, eprint_url))
    
    if failed_downloads:
        print("\nFailed downloads:")
        for article_id, url in failed_downloads:
            print(f"\nID {article_id}: {url}")
            print(f"Please manually download and save as: {output_folder}/{article_id}.pdf")
            
            while True:
                response = input("Have you completed the download? (y/n): ").lower().strip()
                if response in ['y', 'yes']:
                    file_path = os.path.join(output_folder, f"{article_id}.pdf")
                    if os.path.exists(file_path) and is_valid_pdf(file_path):
                        print(f"✓ File {article_id}.pdf verified successfully!")
                        break
                    else:
                        if os.path.exists(file_path):
                            print(f"✗ File {article_id}.pdf exists but is not a valid PDF. Please ensure it's a valid PDF file.")
                        else:
                            print(f"✗ File {article_id}.pdf not found. Please ensure it's saved correctly.")
                        continue
                elif response in ['n', 'no']:
                    print("Please complete the download and try again.")
                    continue
                else:
                    print("Please answer with 'y' or 'n'.")
    
    print(f"\nDownload complete. Files saved to: {output_folder}")

if __name__ == "__main__":
    main()
