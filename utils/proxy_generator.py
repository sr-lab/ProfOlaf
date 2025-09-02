import os
from scholarly import scholarly
from scholarly import ProxyGenerator

def get_proxy(proxy_key: str = None):
    """
    Get a proxy for the scholarly library.
    
    Args:
        proxy_key: Either the actual API key or the name of an environment variable storing the key
    """
    try:
        pg = ProxyGenerator()
        api_key = None
        if proxy_key:
            env_value = os.getenv(proxy_key)
            if env_value:
                api_key = env_value
            else:
                api_key = proxy_key
                
        if api_key and pg.ScraperAPI(api_key):
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

