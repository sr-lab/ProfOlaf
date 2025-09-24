import json

def generate_articles_path():
    articles_path = ""
    while articles_path.strip() == "":
        articles_path = input("Enter the path to store the downloaded articles: ")
    return articles_path

def generate_csv_path():
    csv_path = ""
    while csv_path.strip() == "":
        csv_path = input("Enter the path to the csv file: ")
    return csv_path
        
def generate_seed_path():
    seed_file = ""
    while seed_file.strip() == "":
        seed_file = input("Enter the name of the seed file (md file stored in the prompts folder): ")
    return seed_file

def generate_output_path():
    output_path = ""
    while output_path.strip() == "":
        output_path = input("Enter the path to store the output: ")
    return output_path

def generate_topics_file():
    topics_file = ""
    while topics_file.strip() == "":
        topics_file = input("Enter the name of the topics file: ")
    return topics_file


def generate_search_conf():
    return {
        "articles_folder": generate_articles_path(),
        "csv_path": generate_csv_path(),
        "seed_file": generate_seed_path(),
        "output_path": generate_output_path(),
        "topics_file": generate_topics_file()
    }


if __name__ == "__main__":
    analysis_conf = generate_search_conf()
    with open("analysis_conf.json", "w") as f:
        json.dump(analysis_conf, f, indent=4)