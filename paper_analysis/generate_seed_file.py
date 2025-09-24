import json
import os

with open("analysis_conf.json", "r") as f:
    analysis_conf = json.load(f)

def generate_seed_file():
    topic = ""
    topics = []
    while topic.strip() != "q":
        topic = input("Enter the topic: ")
        if topic.strip() == "q":
            break
        explanation = input("Enter a brief explanation for the topic: ")
        topics.append((topic, explanation))
    return topics

if not os.path.exists(analysis_conf["output_path"]):
    os.makedirs(analysis_conf["output_path"])


with open(f"{analysis_conf['output_path']}/{analysis_conf['seed_file']}", "w") as f:
    topics = generate_seed_file()
    for topic, explanation in topics:
        f.write(f"[1] {topic}: {explanation}\n")