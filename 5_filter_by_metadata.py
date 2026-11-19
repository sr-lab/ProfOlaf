import sys
import os
import bibtexparser
import json
import argparse

from utils.db_management import (
    DBManager, 
    SelectionStage
)
from utils.pipeline.filter_by_metadata_utils import filter_elements
from langdetect import detect

from rich import print

COLOR_START = "[bold magenta]"
COLOR_END = "[/bold magenta]"

def format_color_string(string: str):
    return f"{COLOR_START}{string}{COLOR_END}"

with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(description='Filter by metadata')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    # Flags to disable different checks
    parser.add_argument('--disable_venue_check', help='disable venue check', action='store_true')
    parser.add_argument('--disable_year_check', help='disable year check', action='store_true')
    parser.add_argument('--disable_english_check', help='disable english check', action='store_true')
    parser.add_argument('--disable_download_check', help='disable download check', action='store_true')
    parser.add_argument(
        '--no-reject-preprints',
        help='do not auto-reject arXiv, CORR, SSRN, and similar preprints',
        action='store_true',
    )
    parser.add_argument(
        '--non-interactive',
        help='do not prompt for undecidable checks (unknown year/language/venue/download); use --assume instead',
        action='store_true',
    )
    parser.add_argument(
        '--assume',
        help='in non-interactive mode, how to resolve undecidable checks',
        choices=['keep', 'discard'],
        default='keep',
    )
    args = parser.parse_args()
    return args

def main():
    args = parse_args()
    db_manager = DBManager(args.db_path)
    reject_preprints = search_conf.get("reject_preprints", True) and not args.no_reject_preprints
    filter_elements(
        db_manager,
        args.iteration,
        args.disable_venue_check,
        args.disable_year_check,
        args.disable_english_check,
        args.disable_download_check,
        reject_preprints=reject_preprints,
        interactive=not args.non_interactive,
        assume_keep=(args.assume == 'keep'),
    )
        
if __name__ == "__main__":
    main()