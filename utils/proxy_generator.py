import os
from scholarly import scholarly
from scholarly import ProxyGenerator

def get_proxy():
    """
    Get a proxy for the scholarly library.
    """
    try:
        pg = ProxyGenerator()

        if pg.ScraperAPI(os.getenv("SCRAPER_API_KEY")):
            scholarly.use_proxy(pg)
            print("Using ScraperAPI proxy")
        else:
            print("ScraperAPI proxy setup failed, trying without proxy...")
            # Try alternative proxy methods
            if pg.FreeProxies():
                scholarly.use_proxy(pg)
                print("Using free proxies")
            else:
                print("No proxy available, proceeding without proxy")
    except Exception as e:
        print(f"Proxy setup failed: {e}")
        print("Proceeding without proxy...")
        return None

    return pg

