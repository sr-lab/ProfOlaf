import json

def generate_year_interval():
    start_year = int(input("Enter the starting year: "))
    end_year = int(input("Enter the ending year: "))
    return start_year, end_year

def generate_venue_rank():
    venue_list = []
    while True:
        venue = input("Enter the accepted venue ranks (stops with empty input): ")
        if venue == "":
            break
        venue_list += venue.split(",")
    
    return [rank.strip() for rank in venue_list]

def generate_proxy_key():
    while True:
        proxy_key = input("Enter the proxy key (or the env variable name): ")
        if proxy_key == "":
            proxy_key = input("Proceed without proxy key? (y/n): ")
            if proxy_key == "y":
                return ""
            else:
                continue
        else:
            return proxy_key

def generate_initial_file():
    while True:
        initial_file = input("Enter the initial file: ")
        if initial_file == "":
            continue
        else:
            return initial_file

def generate_db_path():
    while True:
        db_path = input("Enter the db path: ")
        if db_path == "":
            continue
        else:
            return db_path

def generate_csv_path():
    while True:
        csv_path = input("Enter the path to the final csv file: ")
        if csv_path == "":
            continue
        else:
            return csv_path

def generate_search_conf():
    start_year, end_year = generate_year_interval()
    venue_list = generate_venue_rank()
    proxy_key = generate_proxy_key()
    initial_file = generate_initial_file()
    db_path = generate_db_path()
    csv_path = generate_csv_path()

    return {
        "start_year": start_year,
        "end_year": end_year,
        "venue_rank_list": venue_list,
        "proxy_key": proxy_key,
        "initial_file": initial_file,
        "db_path": db_path,
        "csv_path": csv_path
    }


if __name__ == "__main__":
    search_conf = generate_search_conf()
    with open("search_conf.json", "w") as f:
        json.dump(search_conf, f, indent=4)