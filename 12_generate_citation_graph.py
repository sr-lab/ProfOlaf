import os
import json
import argparse

from rich import print

from utils.pipeline.citation_graph_utils import (
    generate_citation_graph,
    EDGE_SOURCES,
    DEFAULT_FORMATS,
)

COLOR_START = "[bold magenta]"
COLOR_END = "[/bold magenta]"


def format_color_string(string: str):
    return f"{COLOR_START}{string}{COLOR_END}"


with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate the citation/reference graph of a snowballing run"
    )
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument(
        '--edge_source',
        help=(
            "Where edges come from: 'db' uses the recorded snowball provenance, "
            "'api' reconstructs them from Semantic Scholar, 'both' unions them."
        ),
        type=str,
        default="both",
        choices=list(EDGE_SOURCES),
    )
    parser.add_argument(
        '--output_dir',
        help='directory where graph files are written',
        type=str,
        default='graphs',
    )
    parser.add_argument(
        '--basename',
        help='base filename for the generated files (without extension)',
        type=str,
        default='citation_graph',
    )
    parser.add_argument(
        '--formats',
        help='comma-separated output formats',
        type=str,
        default=",".join(DEFAULT_FORMATS),
    )
    parser.add_argument(
        '--accepted_only',
        help='only include papers that passed content screening and were selected (selected == CONTENT_APPROVED)',
        action='store_true',
    )
    parser.add_argument(
        '--references_only',
        help=('only query the /references endpoint when reconstructing edges. By default '
              'both /references and /citations are queried and unioned, which recovers edges '
              'Semantic Scholar lists on only one side (fewer isolated nodes) but roughly '
              'doubles the number of API calls.'),
        action='store_true',
    )
    parser.add_argument(
        '--openalex',
        help=('cross-fill edges from OpenAlex (DOI-matched) to recover links that Semantic '
              'Scholar is missing entirely. Only helps papers that have a DOI.'),
        action='store_true',
    )
    parser.add_argument(
        '--pdf_dir',
        help=('recover references by scanning the papers\' PDFs (named '
              '<paperId>__title.pdf) under this directory and matching corpus titles. '
              'Recovers edges for new papers that Semantic Scholar/OpenAlex lack.'),
        type=str,
        default=None,
    )
    parser.add_argument(
        '--merge_duplicates',
        help=('fold non-accepted preprint/conference twins (same work, different '
              'title) into their accepted representative, so citation edges that '
              'Semantic Scholar/OpenAlex attached to the twin record are not lost. '
              'Only ever merges a non-accepted copy into an accepted one.'),
        action='store_true',
    )
    parser.add_argument(
        '--physics',
        help='enable in-browser physics simulation in the HTML view (slow for large graphs)',
        action='store_true',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    basename = args.basename + ("_accepted" if args.accepted_only else "")

    print(format_color_string("Building citation/reference graph..."))
    outputs = generate_citation_graph(
        db_path=args.db_path,
        output_dir=args.output_dir,
        edge_source=args.edge_source,
        formats=formats,
        basename=basename,
        accepted_only=args.accepted_only,
        use_citations=not args.references_only,
        augment_openalex=args.openalex,
        augment_pdfs=bool(args.pdf_dir),
        pdf_dir=args.pdf_dir,
        merge_duplicates=args.merge_duplicates,
        use_physics=args.physics,
        log=lambda msg: print(msg),
    )

    print(format_color_string("Done. Generated files:"))
    for fmt, path in outputs.items():
        print(f"  {fmt.upper():8s} {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
