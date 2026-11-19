"""Per-cluster citation/reference graphs.

Given a ``cluster_keywords.json`` file (produced by the topic-clustering step)
and a snowballing database, this builds one citation/reference graph per
cluster: the sub-graph of the full citation graph induced by the papers that
belong to that cluster.

The cluster file identifies papers by the SHA-256 of their PDF bytes, while the
citation graph is keyed by Semantic Scholar paperId. The two are bridged through
the PDF file name, which embeds the Semantic Scholar paperId as a 40-hex prefix
(``<paperId>__<Title>.pdf``).
"""

import os
import re
import json
import datetime
from collections import Counter
from typing import Callable, Dict, List, Optional

MANIFEST_NAME = "clusters_manifest.json"

from utils.pipeline.citation_graph_utils import (
    build_citation_graph,
    export_graphml,
    export_gexf,
    export_json,
    export_html,
    fetch_paper_metadata,
    fetch_reference_details,
    fetch_openalex_references,
    _fetch_dois_for_nodes,
    _load_reference_cache,
    _cache_entry,
)
from utils.db_management import DBManager, SelectionStage

META_CACHE_NAME = "_paper_meta_cache.json"
LINKS_CACHE_NAME = "_paper_links_cache.json"


def _norm_title(title: str) -> str:
    """Normalise a title for matching: lowercase, alphanumeric only."""
    return re.sub(r"[^0-9a-z]+", "", (title or "").lower())

# 40-hex Semantic Scholar paperId embedded at the start of a PDF file name.
_SS_ID_RE = re.compile(r"([0-9a-f]{40})__")
# Splits "T01 — Title" on the em-dash or hyphen separator.
_CODE_SEP_RE = re.compile(r"\s[—-]\s")


def _extract_ss_id(path: str) -> Optional[str]:
    m = _SS_ID_RE.search(path or "")
    return m.group(1) if m else None


def _cluster_code_and_leaf(label: str):
    """Return (code, leaf_label) for a cluster label.

    Labels are hierarchical, e.g. ``"S1 — ... / T08 — Code driven ..."``; the
    cluster's own code/label is the final path component.
    """
    leaf = (label or "").split(" / ")[-1].strip()
    code = _CODE_SEP_RE.split(leaf, 1)[0].strip()
    return code, leaf


def _slug(text: str, maxlen: int = 50) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
    return text[:maxlen].strip("_")


def load_clusters(cluster_file: str) -> List[dict]:
    """Parse the cluster file into a list of cluster descriptors.

    Each descriptor has: ``code``, ``label`` (leaf), ``full_label``, ``depth``,
    ``ss_ids`` (set of Semantic Scholar paperIds), and ``n_papers`` (papers in
    the cluster that have a resolvable paperId).
    """
    with open(cluster_file, "r") as f:
        data = json.load(f)

    documents = data.get("documents", {})
    # sha256 -> Semantic Scholar paperId
    sha_to_ss: Dict[str, str] = {}
    for sha, info in documents.items():
        for p in info.get("paths", []) or []:
            ss = _extract_ss_id(p)
            if ss:
                sha_to_ss[sha] = ss
                break

    clusters = []
    for c in data.get("clusters", []):
        code, leaf = _cluster_code_and_leaf(c.get("label", ""))
        ss_ids = {sha_to_ss[s] for s in c.get("paper_ids", []) if s in sha_to_ss}
        clusters.append({
            "code": code,
            "label": leaf,
            "full_label": c.get("label", ""),
            "depth": c.get("depth"),
            "ss_ids": ss_ids,
            "n_papers": len(ss_ids),
        })
    return clusters


def select_clusters(clusters: List[dict], code_prefix: str = "T") -> List[dict]:
    """Keep clusters whose code is ``<prefix><digits>`` (e.g. ``T01``..``T15``)."""
    pat = re.compile(rf"{re.escape(code_prefix)}\d+$")
    return [c for c in clusters if pat.fullmatch(c["code"] or "")]


def _induced_subgraph(graph, ss_ids):
    """Return a copy of the sub-graph induced by ``ss_ids`` (only nodes present
    in ``graph``). Corpus-level isolation flags do not apply inside a cluster
    and are reset here; per-cluster flags are set by ``_annotate_cluster_nodes``.
    """
    present = [n for n in ss_ids if graph.has_node(n)]
    sub = graph.subgraph(present).copy()
    for n in sub.nodes:
        sub.nodes[n]["hidden_links"] = 0
        sub.nodes[n]["isolation_cause"] = ""
    return sub


def _annotate_cluster_nodes(sub, full_graph, cluster_ids, node_to_codes):
    """Flag, for each node in the induced sub-graph, its links to papers outside
    the cluster (in the full graph).

    Sets per node:
      * ``external_links``  - number of distinct papers outside this cluster that
        the node cites or is cited by (indicator that it bridges to other
        clusters, even though those inter-edges are not drawn).
      * ``external_detail`` - ``"T02:3;T05:1;other:1"`` breakdown by the
        cluster(s) each external neighbour belongs to (a neighbour in several
        clusters is counted under each; ``other`` = not in any selected cluster).
      * ``isolation_cause`` - ``"disconnected"`` only when the node has no link
        at all (neither inside the cluster nor to any other paper).
    """
    for n in sub.nodes:
        neigh = set(full_graph.successors(n)) | set(full_graph.predecessors(n))
        external = neigh - cluster_ids
        counts = Counter()
        for m in external:
            for code in (node_to_codes.get(m) or {"other"}):
                counts[code] += 1
        sub.nodes[n]["external_links"] = len(external)
        sub.nodes[n]["external_detail"] = ";".join(
            f"{code}:{cnt}" for code, cnt in sorted(counts.items())
        )
        within = sub.degree(n)
        if within == 0 and not external:
            sub.nodes[n]["isolation_cause"] = "disconnected"
        else:
            sub.nodes[n]["isolation_cause"] = ""


def generate_cluster_graphs(
    db_path: str,
    cluster_file: str,
    output_dir: str,
    code_prefix: str = "T",
    source_graph_path: Optional[str] = None,
    edge_source: str = "api",
    formats=("graphml", "gexf", "json", "html"),
    api_cache_path: Optional[str] = None,
    augment_openalex: bool = True,
    use_citations: bool = True,
    use_physics: bool = False,
    log: Optional[Callable[[str], None]] = None,
    cancel_flag=None,
) -> Dict[str, Dict[str, str]]:
    """Build one induced citation graph per selected cluster.

    If ``source_graph_path`` points to an existing GraphML file (the full
    accepted graph), it is loaded and sliced per cluster, which needs no API
    calls. Otherwise the full accepted graph is rebuilt from the database.

    Returns ``{cluster_code: {format: path}}``.
    """
    import networkx as nx

    def _log(msg: str) -> None:
        if log:
            log(msg)

    os.makedirs(output_dir, exist_ok=True)

    clusters = load_clusters(cluster_file)
    selected = select_clusters(clusters, code_prefix=code_prefix)
    if not selected:
        _log(f"No clusters matched prefix '{code_prefix}*'.")
        return {}
    _log(f"Selected {len(selected)} clusters: "
         + ", ".join(c["code"] for c in sorted(selected, key=lambda x: x["code"])))

    # Map every clustered paper -> the set of cluster codes it belongs to
    # (clusters may share papers), used to label cross-cluster connections.
    node_to_codes: Dict[str, set] = {}
    for c in selected:
        for ss in c["ss_ids"]:
            node_to_codes.setdefault(ss, set()).add(c["code"])

    # Every cluster is a slice of the full accepted graph. Prefer reusing an
    # already-generated GraphML (no API calls); rebuild only if asked to.
    if source_graph_path and os.path.exists(source_graph_path):
        _log(f"Loading the full accepted graph from {source_graph_path} ...")
        graph = nx.read_graphml(source_graph_path)
        _log(f"Loaded {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges.")
    else:
        if source_graph_path:
            _log(f"{source_graph_path} not found; rebuilding the full accepted graph.")
        else:
            _log("Building the full accepted citation graph (shared by all clusters)...")
        graph = build_citation_graph(
            db_path,
            edge_source=edge_source,
            api_cache_path=api_cache_path,
            accepted_only=True,
            use_citations=use_citations,
            augment_openalex=augment_openalex,
            log=log,
            cancel_flag=cancel_flag,
        )

    exporters = {
        "graphml": lambda g, p: export_graphml(g, p),
        "gexf": lambda g, p: export_gexf(g, p),
        "json": lambda g, p: export_json(g, p),
        "html": lambda g, p: export_html(g, p, use_physics=use_physics),
    }

    results: Dict[str, Dict[str, str]] = {}
    manifest_clusters: List[dict] = []
    for c in sorted(selected, key=lambda x: x["code"]):
        if cancel_flag is not None and cancel_flag.is_set():
            _log("Cluster graph generation cancelled by user.")
            break
        sub = _induced_subgraph(graph, c["ss_ids"])
        _annotate_cluster_nodes(sub, graph, c["ss_ids"], node_to_codes)
        missing = c["n_papers"] - sub.number_of_nodes()
        basename = f"{c['code']}_{_slug(_CODE_SEP_RE.split(c['label'], 1)[-1])}"
        note = f" ({missing} cluster papers not in the graph)" if missing else ""
        _log(f"[{c['code']}] {sub.number_of_nodes()} nodes, "
             f"{sub.number_of_edges()} edges{note}")

        outputs: Dict[str, str] = {}
        for fmt in formats:
            fmt = fmt.lower()
            if fmt not in exporters:
                _log(f"  Skipping unknown format: {fmt}")
                continue
            path = os.path.join(output_dir, f"{basename}.{fmt}")
            exporters[fmt](sub, path)
            outputs[fmt] = path
        results[c["code"]] = outputs
        manifest_clusters.append({
            "code": c["code"],
            "label": c["label"],
            "full_label": c["full_label"],
            "depth": c["depth"],
            "nodes": sub.number_of_nodes(),
            "edges": sub.number_of_edges(),
            "missing_papers": missing,
            "basename": basename,
            "files": {fmt: os.path.basename(p) for fmt, p in outputs.items()},
        })

    manifest_path = os.path.join(output_dir, MANIFEST_NAME)
    with open(manifest_path, "w") as f:
        json.dump({
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "code_prefix": code_prefix,
            "clusters": manifest_clusters,
        }, f, indent=2)

    _log(f"Done. Generated graphs for {len(results)} clusters in {output_dir}.")
    _log(f"Wrote manifest -> {manifest_path}")
    return results


# ---------------------------------------------------------------------------
# Per-paper references / citations (for verifying and repairing the graph)
# ---------------------------------------------------------------------------

def _load_meta_cache(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_meta_cache(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_accepted_corpus(db_path: str) -> Dict[str, dict]:
    """Return ``{paperId: metadata}`` for accepted (content-approved) papers,
    i.e. exactly the nodes that appear in the accepted citation graph."""
    dbm = DBManager(db_path)
    accepted = SelectionStage.CONTENT_APPROVED.value
    corpus: Dict[str, dict] = {}
    for a in dbm.get_iteration_data():
        if not a.id or a.id in corpus:
            continue
        try:
            sel = int(a.selected)
        except (TypeError, ValueError):
            sel = a.selected
        if sel != accepted:
            continue
        corpus[a.id] = {
            "title": a.title or "",
            "year": str(a.pub_year or ""),
            "venue": a.venue or "",
            "authors": a.authors or "",
        }
    return corpus


def get_paper_links(
    paper_id: str,
    db_path: str,
    cluster_file: str,
    cache_path: str,
    graph_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    code_prefix: str = "T",
    max_citations: int = 500,
    use_openalex: bool = True,
    refresh: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """Return the FULL reference and citation lists of ``paper_id`` for the panel.

    References come from two sources, merged by title: Semantic Scholar's
    ``/references`` and (``use_openalex``) OpenAlex's ``referenced_works``.
    Citations come from Semantic Scholar's ``/citations``. All are fetched with
    titles and cached on disk per paper.

    Each item is *marked* with whether it is present in the accepted graph:
    ``in_corpus`` (matches a graph node by id or by title) and ``linked`` (the
    graph actually has that edge). ``id_mismatch`` flags a corpus paper matched
    only by title (its provider id differs from the graph's) and ``linked``
    false marks a likely missing edge -- both are manual-repair candidates.
    Graph edges the providers do not list on this side are unioned in.

    ``refresh`` re-fetches from the providers instead of using the cache.
    """
    # Load the accepted graph: authority for corpus membership, metadata, edges,
    # and self-consistent regardless of which database the config points at.
    graph = None
    if graph_path and os.path.exists(graph_path):
        try:
            import networkx as nx
            graph = nx.read_graphml(graph_path)
        except Exception:
            graph = None

    if graph is not None:
        corpus = {
            n: {
                "title": (a.get("title") or a.get("label") or ""),
                "venue": a.get("venue") or "",
                "year": str(a.get("year") or ""),
                "authors": a.get("authors") or "",
            }
            for n, a in graph.nodes(data=True)
        }
    else:
        corpus = _load_accepted_corpus(db_path)
    corpus_ids = set(corpus.keys())

    succ, pred = set(), set()
    if graph is not None and graph.has_node(paper_id):
        succ = set(graph.successors(paper_id))
        pred = set(graph.predecessors(paper_id))

    # Fetch the full reference/citation lists with titles (cached per paper).
    links_dir = output_dir or os.path.dirname(cache_path) or "."
    links_cache_path = os.path.join(links_dir, LINKS_CACHE_NAME)
    links_cache = _load_meta_cache(links_cache_path)
    entry = links_cache.get(paper_id)
    fetched = False
    if entry is None or refresh or (use_openalex and "openalex_references" not in (entry or {})):
        refs = fetch_reference_details(paper_id, "references", log=log)
        cits = fetch_reference_details(paper_id, "citations", max_items=max_citations, log=log)
        oa_refs = []
        doi = None
        if use_openalex:
            doi = _fetch_dois_for_nodes([paper_id], log=log).get(paper_id)
            oa_refs = fetch_openalex_references(doi, log=log)
        entry = {"references": refs, "citations": cits,
                 "openalex_references": oa_refs, "doi": doi}
        links_cache[paper_id] = entry
        _save_meta_cache(links_cache_path, links_cache)
        fetched = True
    ref_list = entry.get("references", []) or []
    cit_list = entry.get("citations", []) or []
    oa_list = entry.get("openalex_references", []) or []

    selected = select_clusters(load_clusters(cluster_file), code_prefix=code_prefix)
    node_to_codes: Dict[str, set] = {}
    for c in selected:
        for ss in c["ss_ids"]:
            node_to_codes.setdefault(ss, set()).add(c["code"])

    # Corpus lookup by normalised title (to catch papers stored under a
    # different id than the provider's -> id mismatch / missing-edge repair).
    corpus_by_title: Dict[str, str] = {}
    for cid, m in corpus.items():
        nt = _norm_title(m["title"])
        if nt:
            corpus_by_title.setdefault(nt, cid)

    def _resolve_corpus(rid: str, title: str):
        if rid in corpus_ids:
            return rid, False
        nt = _norm_title(title)
        if nt and nt in corpus_by_title:
            return corpus_by_title[nt], True  # matched by title only
        return None, False

    def _unify(primary_lists, neighbours, is_reference):
        """Merge source lists (and graph neighbours) into one marked list."""
        unified: Dict[str, dict] = {}
        order: list = []

        def add(rid, title, year, venue, authors, source):
            nt = _norm_title(title)
            key = nt or ("id:" + (rid or ""))
            if key in unified:
                u = unified[key]
                u["sources"].add(source)
                if not u["title"] and title:
                    u["title"] = title
                if not u["year"] and year not in (None, ""):
                    u["year"] = year
                return
            unified[key] = {"id": rid, "title": title or "", "year": year,
                            "venue": venue or "", "authors": authors or "",
                            "sources": {source}}
            order.append(key)

        for items, source in primary_lists:
            for it in items:
                rid = it.get("id") or it.get("openalex_id") or ""
                authors = it.get("authors")
                authors = ", ".join(authors) if isinstance(authors, list) else (authors or "")
                add(rid, it.get("title", ""), it.get("year"), it.get("venue", ""), authors, source)
        for nid in sorted(neighbours):
            cm = corpus.get(nid, {})
            add(nid, cm.get("title", ""), cm.get("year"), cm.get("venue", ""),
                cm.get("authors", ""), "graph")

        out = []
        for key in order:
            u = unified[key]
            if u["id"] == paper_id:
                continue
            corpus_id, by_title = _resolve_corpus(u["id"], u["title"])
            in_corpus = corpus_id is not None
            if in_corpus and graph is not None:
                linked = graph.has_edge(paper_id, corpus_id) if is_reference \
                    else graph.has_edge(corpus_id, paper_id)
            else:
                linked = in_corpus and graph is None
            if in_corpus:
                cm = corpus[corpus_id]
                title = u["title"] or cm["title"]
                venue = u["venue"] or cm["venue"]
                year = u["year"] or cm["year"]
                authors = cm["authors"] or u["authors"]
                disp_id = corpus_id
            else:
                title, venue, year, authors, disp_id = \
                    u["title"], u["venue"], u["year"], u["authors"], u["id"]
            out.append({
                "id": disp_id,
                "title": title or "",
                "year": str(year) if year not in (None, "") else "",
                "authors": authors,
                "venue": venue or "",
                "in_corpus": in_corpus,
                "clusters": sorted(node_to_codes.get(corpus_id, [])) if in_corpus else [],
                "linked": linked,
                "id_mismatch": by_title,
                "sources": sorted(u["sources"]),
            })
        out.sort(key=lambda it: (not it["in_corpus"], (it["title"] or "~").lower()))
        return out

    references = _unify([(ref_list, "s2"), (oa_list, "openalex")], succ, True)
    citations = _unify([(cit_list, "s2")], pred, False)

    return {
        "found": True,
        "paper_id": paper_id,
        "fetched": fetched,
        "doi": entry.get("doi"),
        "references": references,
        "citations": citations,
        "counts": {
            "references_total": len(references),
            "references_in_corpus": sum(1 for it in references if it["in_corpus"]),
            "references_truncated": False,
            "citations_total": len(citations),
            "citations_in_corpus": sum(1 for it in citations if it["in_corpus"]),
            "citations_truncated": len(cit_list) >= max_citations,
        },
    }
