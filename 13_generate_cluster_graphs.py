import os
import json
import argparse

from rich import print

from utils.pipeline.cluster_graph_utils import generate_cluster_graphs

COLOR_START = "[bold magenta]"
COLOR_END = "[/bold magenta]"


def format_color_string(string: str):
    return f"{COLOR_START}{string}{COLOR_END}"


with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate one citation/reference graph per topic cluster"
    )
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument(
        '--cluster_file',
        help='path to the cluster_keywords.json produced by the clustering step',
        type=str,
        default='cluster_keywords.json',
    )
    parser.add_argument(
        '--output_dir',
        help='directory where the per-cluster graph files are written',
        type=str,
        default=os.path.join('graphs', 'clusters'),
    )
    parser.add_argument(
        '--code_prefix',
        help="only build graphs for clusters whose code is <prefix><digits>, "
             "e.g. 'T' selects T01..T15 (default). Use 'S', 'U', etc. for others.",
        type=str,
        default='T',
    )
    parser.add_argument(
        '--source_graph',
        help='an existing full accepted-graph GraphML to slice per cluster (no '
             'API calls). Defaults to graphs/citation_graph_accepted.graphml.',
        type=str,
        default=os.path.join('graphs', 'citation_graph_accepted.graphml'),
    )
    parser.add_argument(
        '--rebuild',
        help='rebuild the full accepted graph from the database/API instead of '
             'reusing --source_graph (slow; needs Semantic Scholar/OpenAlex).',
        action='store_true',
    )
    parser.add_argument(
        '--api_cache_path',
        help='when rebuilding, reuse this references cache so fewer API calls '
             'are needed (defaults to graphs/citation_graph_references_cache.json)',
        type=str,
        default=os.path.join('graphs', 'citation_graph_references_cache.json'),
    )
    parser.add_argument(
        '--no_openalex',
        help='skip the OpenAlex cross-fill (faster; fewer edges)',
        action='store_true',
    )
    parser.add_argument(
        '--references_only',
        help='only query the /references endpoint when reconstructing edges',
        action='store_true',
    )
    parser.add_argument(
        '--formats',
        help='comma-separated output formats',
        type=str,
        default='graphml,gexf,json,html',
    )
    parser.add_argument(
        '--physics',
        help='enable in-browser physics in the HTML view',
        action='store_true',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    print(format_color_string("Building per-cluster citation/reference graphs..."))
    results = generate_cluster_graphs(
        db_path=args.db_path,
        cluster_file=args.cluster_file,
        output_dir=args.output_dir,
        code_prefix=args.code_prefix,
        source_graph_path=None if args.rebuild else args.source_graph,
        edge_source="api",
        formats=formats,
        api_cache_path=args.api_cache_path,
        augment_openalex=not args.no_openalex,
        use_citations=not args.references_only,
        use_physics=args.physics,
        log=lambda msg: print(msg),
    )

    print(format_color_string(f"Done. Generated graphs for {len(results)} clusters:"))
    for code, outputs in results.items():
        html = outputs.get("html", "")
        print(f"  {code:6s} {os.path.abspath(html) if html else ''}")


if __name__ == "__main__":
    main()
