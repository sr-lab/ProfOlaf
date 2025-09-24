import argparse

from utils.db_management import DBManager

def check_iteration_and_stage(db_path: str):
    stages = ["Fetched", "Metadata Filtered", "Title Filtered", "Content Filtered"]
    db_manager = DBManager(db_path)
    current_iteration, max_selected = db_manager.check_current_iteration()
    print(f"Current iteration: {current_iteration}")
    print(f"Stage: {stages[max_selected]}")
    
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db_path", type=str, required=True)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    check_iteration_and_stage(args.db_path)