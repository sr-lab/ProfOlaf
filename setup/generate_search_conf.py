import json

from argparse import ArgumentParser

def generate_year_interval():
    while True:
        start_year = int(input("Enter the starting year: "))
        end_year = int(input("Enter the ending year: "))
        if start_year > end_year or start_year <= 0 or end_year <= 0:
            print("Starting year must be less than ending year. Please try again.")
        else:
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

def generate_search_method():
    while True:
        search_method = input("Enter the search method (google scholar (gs) or semantic scholar (ss)): ")
        if search_method == "":
            continue
        elif search_method in ["gs", "ss", "google scholar", "semantic scholar"]:
            return "google_scholar" if search_method in ["gs", "google scholar"] else "semantic_scholar"
        else:
            print("Invalid search method. Please enter google_scholar or semantic_scholar.")

def generate_search_conf(args):
    years_valid = (args.start_year and args.end_year) and args.start_year < args.end_year
    start_year, end_year = args.start_year, args.end_year if years_valid else generate_year_interval()
    venue_list = args.venue_rank_list if args.venue_rank_list else generate_venue_rank()
    proxy_key = args.proxy_key if args.proxy_key is not None else generate_proxy_key()
    initial_file = args.initial_file if args.initial_file else generate_initial_file()
    db_path = args.db_path if args.db_path else generate_db_path()
    csv_path = args.csv_path if args.csv_path else generate_csv_path()
    search_method = args.search_method if args.search_method else generate_search_method()

    return {
        "start_year": start_year,
        "end_year": end_year,
        "venue_rank_list": venue_list,
        "proxy_key": proxy_key,
        "initial_file": initial_file,
        "db_path": db_path,
        "csv_path": csv_path
    }


def parse_args():
    parser = ArgumentParser(description='Generate search configuration')
    parser.add_argument('--start_year', type=int, default=0)
    parser.add_argument('--end_year', type=int, default=0)
    parser.add_argument(
        '--venue_rank_list', 
        type=str, 
        nargs='+', 
        choices=["A*", "A", "B", "C", "D", "Q1", "Q2", "Q3", "Q4"], 
        default=[]
    )
    parser.add_argument('--proxy_key', type=str, default=None)
    parser.add_argument('--initial_file', type=str, default="")
    parser.add_argument('--db_path', type=str, default="")
    parser.add_argument('--csv_path', type=str, default="")
    parser.add_argument(
        '--search_method', 
        type=str, 
        choices=["google_scholar", "semantic_scholar"], 
        default=""
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    search_conf = generate_search_conf(args)
    with open("search_conf.json", "w") as f:
        json.dump(search_conf, f, indent=4)