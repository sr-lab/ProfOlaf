"""
Build, export and visualize the citation/reference graph of a snowballing run.

Nodes are the articles stored in the ``iterations`` table; each node is
coloured by the iteration in which it was first discovered. Directed edges are
always oriented as *citing paper -> cited paper* (``A -> B`` means "A cites B",
equivalently "B is a reference of A").

Edges come from two sources (selectable via ``edge_source``):

* ``db``  - the ``citation_edges`` table, populated during snowballing. Each
  row is stored canonically as ``citing_id -> cited_id`` (with endpoint DOIs and
  the source provider), so no direction reinterpretation is needed here. Legacy
  ``snowball_edges`` rows, if any, are converted and folded in automatically.
* ``api`` - reconstructed by re-querying the Semantic Scholar ``/references``
  endpoint for every node and keeping only edges whose endpoints are both in
  the corpus. This recovers the full citation structure even for databases
  created before provenance was recorded.
* ``both`` - the union of the two (default).

Outputs: GraphML and GEXF (for Gephi/Cytoscape), a nodes/edges JSON, and a
self-contained interactive HTML visualization.
"""

import json
import os
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

import requests

from ..db_management import DBManager, SelectionStage
from ..article_search.article_search_method import (
    _semantic_scholar_get,
    _semantic_scholar_headers,
    _semantic_scholar_min_interval,
    SS_API_BASE,
)

OPENALEX_API = "https://api.openalex.org/works"
# Generic client name only; do not send the user's email to OpenAlex.
OPENALEX_HEADERS = {"User-Agent": "ProfOlaf-citation-graph/1.0"}


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

EDGE_SOURCES = ("db", "api", "both")

# Colour per iteration (index = iteration number). Cycles if there are more
# iterations than colours.
ITERATION_COLORS = [
    "#e4572e",  # 0 - seed      (warm red)
    "#2e86ab",  # 1             (blue)
    "#2a9d8f",  # 2             (teal)
    "#8338ec",  # 3             (purple)
    "#f4a261",  # 4             (orange)
    "#e9c46a",  # 5             (yellow)
    "#5a6c7d",  # 6             (slate)
]

# Edge colours by provenance / relation.
EDGE_COLOR_CITATION = "#1b7837"   # db: the child cites the seed
EDGE_COLOR_REFERENCE = "#762a83"  # db: the seed cites the child
EDGE_COLOR_API = "#b8b8b8"        # api-only reconstructed edge (Semantic Scholar)
EDGE_COLOR_OPENALEX = "#5ab4d6"   # edge recovered from OpenAlex

# Node border colours used to flag why a node has no visible edges.
BORDER_FILTERED = "#f39c12"       # isolated only because accepted-only hid its links
BORDER_DISCONNECTED = "#9aa0a6"   # no links to any corpus paper at all


def iteration_color(iteration: int) -> str:
    try:
        it = int(iteration)
    except (TypeError, ValueError):
        it = 0
    return ITERATION_COLORS[it % len(ITERATION_COLORS)]


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# Semantic Scholar reference lookup (with on-disk cache)
# ----------------------------------------------------------------------------

def _fetch_reference_ids(paper_id: str) -> List[str]:
    """Return the Semantic Scholar paperIds referenced by ``paper_id``.

    Only the ``paperId`` field is requested to keep payloads small. Returns an
    empty list on any error (missing paper, rate limit exhausted, etc.).
    """
    ref_ids: List[str] = []
    offset = 0
    limit = 1000
    while True:
        url = (
            f"{SS_API_BASE}/paper/{paper_id}/references"
            f"?fields=paperId&offset={offset}&limit={limit}"
        )
        try:
            response = _semantic_scholar_get(url)
        except Exception:
            break
        if response.status_code != 200:
            break
        data = response.json()
        batch = data.get("data", [])
        if not batch:
            break
        for item in batch:
            cited = item.get("citedPaper") or {}
            cited_id = cited.get("paperId")
            if cited_id:
                ref_ids.append(cited_id)
        if len(batch) < limit:
            break
        offset += limit
    return ref_ids


def _fetch_citation_ids(paper_id: str) -> List[str]:
    """Return the Semantic Scholar paperIds that cite ``paper_id``.

    Semantic Scholar stores each citation link on both papers (A's references
    and B's citations), but either side may be incomplete. Querying this
    direction as well as ``_fetch_reference_ids`` recovers edges missing from
    one side. Returns an empty list on any error.
    """
    cit_ids: List[str] = []
    offset = 0
    limit = 1000
    while True:
        url = (
            f"{SS_API_BASE}/paper/{paper_id}/citations"
            f"?fields=paperId&offset={offset}&limit={limit}"
        )
        try:
            response = _semantic_scholar_get(url)
        except Exception:
            break
        if response.status_code != 200:
            break
        data = response.json()
        batch = data.get("data", [])
        if not batch:
            break
        for item in batch:
            citing = item.get("citingPaper") or {}
            citing_id = citing.get("paperId")
            if citing_id:
                cit_ids.append(citing_id)
        if len(batch) < limit:
            break
        offset += limit
    return cit_ids


def _fetch_edge_endpoints(paper_id: str, direction: str = "references") -> List[Tuple[str, str]]:
    """Return ``[(other_paper_id, normalized_doi), ...]`` for ``paper_id``'s
    references (``direction='references'``) or citations (``'citations'``).

    Like :func:`_fetch_reference_ids`/:func:`_fetch_citation_ids` but also pulls
    each endpoint's DOI (via ``externalIds``) so reconstructed edges can be
    attached to a corpus node by DOI even when the endpoint's Semantic Scholar id
    is not itself in the corpus (e.g. a dropped duplicate twin)."""
    key = "citedPaper" if direction == "references" else "citingPaper"
    out: List[Tuple[str, str]] = []
    offset = 0
    limit = 1000
    while True:
        url = (
            f"{SS_API_BASE}/paper/{paper_id}/{direction}"
            f"?fields=paperId,externalIds&offset={offset}&limit={limit}"
        )
        try:
            response = _semantic_scholar_get(url)
        except Exception:
            break
        if response.status_code != 200:
            break
        batch = response.json().get("data", [])
        if not batch:
            break
        for item in batch:
            p = item.get(key) or {}
            pid = p.get("paperId")
            if not pid:
                continue
            doi = _normalize_doi((p.get("externalIds") or {}).get("DOI")) or ""
            out.append((pid, doi))
        if len(batch) < limit:
            break
        offset += limit
    return out


def fetch_reference_details(paper_id: str, direction: str = "references",
                            max_items: int = 1500, log=None) -> List[dict]:
    """Return the full reference (or citation) list of ``paper_id`` with titles.

    ``direction`` is ``"references"`` (papers this one cites) or ``"citations"``
    (papers that cite it). Each item is ``{id, title, year, venue, authors}``.
    Paginated; returns what it can on error.
    """
    key = "citedPaper" if direction == "references" else "citingPaper"
    items: List[dict] = []
    seen = set()
    offset = 0
    limit = 1000
    fields = "paperId,title,year,venue,authors"
    while len(items) < max_items:
        url = (
            f"{SS_API_BASE}/paper/{paper_id}/{direction}"
            f"?fields={fields}&offset={offset}&limit={limit}"
        )
        try:
            resp = _semantic_scholar_get(url)
        except Exception:
            break
        if resp.status_code != 200:
            break
        batch = resp.json().get("data", [])
        if not batch:
            break
        for it in batch:
            p = it.get(key) or {}
            pid = p.get("paperId")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            items.append({
                "id": pid,
                "title": p.get("title") or "",
                "year": p.get("year"),
                "venue": p.get("venue") or "",
                "authors": [a.get("name") for a in (p.get("authors") or []) if a.get("name")][:6],
            })
        if len(batch) < limit:
            break
        offset += limit
        if log:
            log(f"  ...fetched {len(items)} {direction} for {paper_id}")
    return items


def _normalize_doi(doi: Optional[str]) -> Optional[str]:
    """Return a bare, lower-cased DOI (no scheme/host) for joining across sources."""
    if not doi:
        return None
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def _fetch_dois_for_nodes(paper_ids: List[str], log=None) -> Dict[str, str]:
    """Return ``{paperId: doi}`` using the Semantic Scholar batch endpoint.

    The batch endpoint returns many papers per request (up to 500 ids), which
    keeps DOI lookup cheap even without an API key. Papers without a DOI are
    omitted.
    """
    result: Dict[str, str] = {}
    headers = _semantic_scholar_headers()
    min_interval = _semantic_scholar_min_interval()
    batch_size = 500
    for start in range(0, len(paper_ids), batch_size):
        chunk = paper_ids[start:start + batch_size]
        backoff = 1
        for attempt in range(8):
            try:
                resp = requests.post(
                    f"{SS_API_BASE}/paper/batch",
                    params={"fields": "externalIds"},
                    json={"ids": chunk},
                    headers=headers,
                    timeout=60,
                )
            except Exception:
                break
            if resp.status_code == 429:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if resp.status_code != 200:
                break
            for pid, item in zip(chunk, resp.json()):
                if not item:
                    continue
                doi = _normalize_doi((item.get("externalIds") or {}).get("DOI"))
                if doi:
                    result[pid] = doi
            time.sleep(min_interval)
            break
        if log:
            log(f"  ...resolved DOIs for {min(start + batch_size, len(paper_ids))}/{len(paper_ids)} papers")
    return result


def fetch_paper_metadata(ids, fields: str = "title,year,authors,venue", log=None) -> Dict[str, dict]:
    """Return ``{paperId: {title, year, authors, venue}}`` via the Semantic
    Scholar batch endpoint (up to 500 ids per request). Best-effort: ids that
    cannot be resolved are simply omitted.
    """
    result: Dict[str, dict] = {}
    ids = list(ids)
    if not ids:
        return result
    headers = _semantic_scholar_headers()
    min_interval = _semantic_scholar_min_interval()
    batch_size = 500
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        backoff = 1
        for attempt in range(5):
            try:
                resp = requests.post(
                    f"{SS_API_BASE}/paper/batch",
                    params={"fields": fields},
                    json={"ids": chunk},
                    headers=headers,
                    timeout=60,
                )
            except Exception:
                break
            if resp.status_code == 429:
                time.sleep(backoff)
                backoff = min(backoff * 2, 20)
                continue
            if resp.status_code != 200:
                break
            for pid, item in zip(chunk, resp.json()):
                if not item:
                    continue
                authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
                result[pid] = {
                    "title": item.get("title") or "",
                    "year": item.get("year"),
                    "venue": item.get("venue") or "",
                    "authors": authors[:6],
                }
            time.sleep(min_interval)
            break
        if log:
            log(f"  ...resolved metadata for {min(start + batch_size, len(ids))}/{len(ids)} papers")
    return result


def _fetch_openalex_referenced_works(dois: List[str], log=None, cancel_flag=None) -> Dict[str, dict]:
    """Query OpenAlex for the given DOIs.

    Returns ``{doi: {"openalex_id": <W...>, "referenced_works": [<W...>, ...]}}``.
    OpenAlex is queried in batches via the ``doi:`` filter (a few requests for a
    whole corpus). No API key is required and the user's email is not sent.
    """
    out: Dict[str, dict] = {}
    batch_size = 50
    for start in range(0, len(dois), batch_size):
        if cancel_flag is not None and cancel_flag.is_set():
            break
        chunk = dois[start:start + batch_size]
        doi_filter = "|".join(f"https://doi.org/{d}" for d in chunk)
        params = {
            "filter": f"doi:{doi_filter}",
            "select": "id,doi,referenced_works",
            "per-page": str(batch_size),
        }
        backoff = 1
        for attempt in range(6):
            try:
                resp = requests.get(OPENALEX_API, params=params, headers=OPENALEX_HEADERS, timeout=60)
            except Exception:
                break
            if resp.status_code == 429:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if resp.status_code != 200:
                break
            for work in resp.json().get("results", []):
                doi = _normalize_doi(work.get("doi"))
                if not doi:
                    continue
                out[doi] = {
                    "openalex_id": work.get("id"),
                    "referenced_works": work.get("referenced_works", []) or [],
                }
            break
        if log:
            log(f"  ...queried OpenAlex for {min(start + batch_size, len(dois))}/{len(dois)} DOIs")
    return out


def fetch_openalex_references(doi: Optional[str], log=None) -> List[dict]:
    """Return a paper's references from OpenAlex, resolved to titles.

    Looks the paper up by DOI, reads its ``referenced_works`` (OpenAlex W-ids),
    and batch-resolves those to ``{openalex_id, title, year, doi}``. Returns an
    empty list if the paper has no DOI or OpenAlex has no data.
    """
    if not doi:
        return []
    try:
        resp = requests.get(
            f"{OPENALEX_API}/https://doi.org/{doi}",
            params={"select": "id,title,referenced_works"},
            headers=OPENALEX_HEADERS, timeout=60,
        )
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    wids = [w.split("/")[-1] for w in (resp.json().get("referenced_works") or []) if w]
    out: List[dict] = []
    batch = 50
    for start in range(0, len(wids), batch):
        chunk = wids[start:start + batch]
        try:
            rr = requests.get(
                OPENALEX_API,
                params={"filter": f"openalex_id:{'|'.join(chunk)}",
                        "select": "id,title,publication_year,doi", "per-page": str(batch)},
                headers=OPENALEX_HEADERS, timeout=60,
            )
        except Exception:
            continue
        if rr.status_code != 200:
            continue
        for w in rr.json().get("results", []):
            out.append({
                "openalex_id": w.get("id"),
                "title": w.get("title") or "",
                "year": w.get("publication_year"),
                "doi": _normalize_doi(w.get("doi")),
            })
        if log:
            log(f"  ...resolved {min(start + batch, len(wids))}/{len(wids)} OpenAlex references")
    return out


def _norm_title_for_match(title: str) -> str:
    """Normalise a title to alphanumerics for substring matching in PDF text."""
    return re.sub(r"[^0-9a-z]+", "", (title or "").lower())


def _extract_pdf_text(path: str) -> str:
    """Return the concatenated text of a PDF, or '' on any failure."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:
        return ""


def augment_from_pdfs(graph, node_ids, pdf_dir, add_edge_fn,
                      cache_path=None, min_title_len=28, log=None, cancel_flag=None):
    """Recover references from the papers' PDFs and add them as edges.

    For each corpus paper that has a PDF (named ``<paperId>__<title>.pdf`` under
    ``pdf_dir``), extract the text and look for the titles of other corpus papers
    appearing in it: those are papers it cites. This recovers references that
    Semantic Scholar / OpenAlex lack (common for very new papers).

    ``add_edge_fn(src, dst, provenance)`` adds/merges a ``src -> dst`` edge.
    Matched paper ids per PDF are cached at ``cache_path``.
    """
    import glob

    # Map paperId -> pdf path (40-hex prefix in the file name).
    pdf_by_id = {}
    for p in glob.glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True):
        m = re.search(r"([0-9a-f]{40})__", os.path.basename(p))
        if m:
            pdf_by_id.setdefault(m.group(1), p)

    # Title index: normalised title -> node id (skip short or ambiguous titles).
    title_to_id = {}
    ambiguous = set()
    for n in node_ids:
        nt = _norm_title_for_match(graph.nodes[n].get("title", ""))
        if len(nt) < min_title_len:
            continue
        if nt in title_to_id and title_to_id[nt] != n:
            ambiguous.add(nt)
        else:
            title_to_id[nt] = n
    for nt in ambiguous:
        title_to_id.pop(nt, None)

    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    titles = list(title_to_id.items())
    with_pdf = [n for n in node_ids if n in pdf_by_id]
    _log = (lambda m: log(m)) if log else (lambda m: None)
    _log(f"Scanning {len(with_pdf)} PDFs for references to the other "
         f"{len(titles)} corpus titles...")

    added = 0
    processed = 0
    changed = False
    for n in with_pdf:
        if cancel_flag is not None and cancel_flag.is_set():
            _log("PDF augmentation cancelled by user.")
            break
        processed += 1
        if n in cache:
            matched = cache[n]
        else:
            text = _norm_title_for_match(_extract_pdf_text(pdf_by_id[n]))
            matched = [tid for nt, tid in titles if tid != n and nt in text]
            cache[n] = matched
            changed = True
            if processed % 25 == 0:
                _log(f"  ...scanned {processed}/{len(with_pdf)} PDFs")
        for dst in matched:
            added += add_edge_fn(n, dst, "pdf")

    if cache_path and changed:
        try:
            with open(cache_path, "w") as f:
                json.dump(cache, f)
        except Exception:
            pass
    _log(f"Added {added} new edges from PDF references.")
    return added


def _cache_entry(cache: dict, paper_id: str) -> dict:
    """Return the cache entry for ``paper_id`` as a dict.

    Supports the legacy cache format, where each value was a plain list of
    reference ids, by migrating it to ``{"references": [...]}``.
    """
    entry = cache.get(paper_id)
    if entry is None:
        return {}
    if isinstance(entry, list):  # legacy references-only format
        return {"references": entry}
    return entry


def _load_reference_cache(cache_path: Optional[str]) -> Dict[str, List[str]]:
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_reference_cache(cache_path: Optional[str], cache: Dict[str, List[str]]) -> None:
    if not cache_path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Graph construction
# ----------------------------------------------------------------------------

def _dup_surnames(authors) -> set:
    """Lowercase last-name set from an ArticleData.authors value (JSON string,
    list of {name}/str, or plain string)."""
    import json as _json
    out = []
    data = authors
    if isinstance(authors, str):
        try:
            data = _json.loads(authors)
        except Exception:
            data = [authors]
    if isinstance(data, dict):
        data = [data]
    for x in (data or []):
        nm = x.get("name") if isinstance(x, dict) else str(x)
        if nm:
            parts = re.sub(r"[^a-z ]", " ", nm.lower()).split()
            if parts:
                out.append(parts[-1])
    return {s for s in out if len(s) > 2}


def _dup_title_tokens(title: str) -> set:
    return {w for w in re.sub(r"[^0-9a-z ]", " ", (title or "").lower()).split() if len(w) > 3}


def find_duplicate_merge_map(articles, accepted_value, log=None):
    """Detect same-work duplicate records and return ``{alias_id: rep_id}``.

    Only *mixed* duplicates are merged: pairs where exactly one copy is
    content-approved. The accepted copy is always the representative, and two
    accepted papers are NEVER merged (that would collapse real nodes; prolific
    author groups publish many distinct papers with identical author lists).

    Same-work is decided from near-identical author sets plus a title signal
    (high similarity, one title a substring of the other, or — only when author
    sets are identical and years match — moderate similarity).
    """
    import itertools
    from difflib import SequenceMatcher
    from collections import defaultdict

    def _log(m):
        if log:
            log(m)

    recs = {}
    for a in articles:
        if not a.id or a.id in recs:
            continue
        recs[a.id] = dict(
            id=a.id,
            nt=re.sub(r"[^0-9a-z]", "", (a.title or "").lower()),
            sur=_dup_surnames(a.authors),
            tok=_dup_title_tokens(a.title),
            sel=_as_int(a.selected, -999),
            yr=str(a.pub_year or ""),
        )
    # Candidate pairs: share >= 2 surnames (skip ultra-common-surname blocks).
    idx = defaultdict(list)
    for i, r in recs.items():
        for s in r["sur"]:
            idx[s].append(i)
    cand = set()
    for s, ids in idx.items():
        if len(ids) > 60:
            continue
        for a, b in itertools.combinations(sorted(ids), 2):
            cand.add((a, b))

    def _jac(x, y):
        u = x | y
        return len(x & y) / len(u) if u else 0.0

    # alias -> (rep, score) keeping the best representative per alias.
    best = {}
    for a, b in cand:
        ra, rb = recs[a], recs[b]
        a_acc = ra["sel"] == accepted_value
        b_acc = rb["sel"] == accepted_value
        if a_acc == b_acc:
            continue  # need exactly one accepted (mixed) — never acc+acc or neither
        if ra["nt"] == rb["nt"]:
            samework = True
        else:
            aj = _jac(ra["sur"], rb["sur"])
            ts = SequenceMatcher(None, ra["nt"], rb["nt"]).ratio()
            shorter, longer = sorted([ra["nt"], rb["nt"]], key=len)
            substr = len(shorter) >= 15 and shorter in longer
            sameyear = bool(ra["yr"]) and ra["yr"] == rb["yr"]
            tok = len(ra["tok"] & rb["tok"])
            samework = (
                (aj >= 0.6 and (ts >= 0.55 or substr))
                or (substr and aj >= 0.5)
                or (aj >= 1.0 and sameyear and ts >= 0.45 and tok >= 5)
            )
        if not samework:
            continue
        rep, alias = (a, b) if a_acc else (b, a)
        score = (_jac(recs[alias]["sur"], recs[rep]["sur"]),
                 SequenceMatcher(None, recs[alias]["nt"], recs[rep]["nt"]).ratio())
        if alias not in best or score > best[alias][1]:
            best[alias] = (rep, score)

    alias_to_rep = {alias: rep for alias, (rep, _s) in best.items()}
    if alias_to_rep:
        _log(f"Duplicate detection: {len(alias_to_rep)} non-accepted twin(s) "
             f"map to {len(set(alias_to_rep.values()))} accepted representative(s).")
    return alias_to_rep


def build_citation_graph(
    db_path: str,
    edge_source: str = "both",
    api_cache_path: Optional[str] = None,
    accepted_only: bool = False,
    use_citations: bool = True,
    augment_openalex: bool = False,
    augment_pdfs: bool = False,
    pdf_dir: Optional[str] = None,
    merge_duplicates: bool = False,
    log: Optional[Callable[[str], None]] = None,
    cancel_flag=None,
):
    """Build and return a ``networkx.DiGraph`` for the given database.

    ``accepted_only`` restricts the graph to papers that survived content
    screening and were selected (``selected == CONTENT_APPROVED``); edges are
    then kept only between those papers.

    ``use_citations`` (API reconstruction only) also queries each paper's
    ``/citations`` endpoint and unions it with ``/references``. Because
    Semantic Scholar often populates only one side of a citation link, this
    recovers edges that a references-only pass misses, at the cost of roughly
    doubling the number of API calls.

    ``augment_openalex`` additionally cross-fills edges from OpenAlex: DOIs are
    resolved via the Semantic Scholar batch endpoint, then OpenAlex's
    ``referenced_works`` are mapped back onto the corpus, recovering links that
    Semantic Scholar is missing entirely (only for papers that have a DOI).

    ``log`` is an optional callable used for progress messages. ``cancel_flag``
    is an optional object with ``is_set()`` (e.g. ``threading.Event``) that
    aborts the (potentially long) API reconstruction loop.
    """
    if edge_source not in EDGE_SOURCES:
        raise ValueError(f"Invalid edge_source: {edge_source}. Must be one of {EDGE_SOURCES}")

    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError(
            "networkx is required to build the citation graph. "
            "Install it with `pip install networkx`."
        ) from e

    def _log(msg: str) -> None:
        if log:
            log(msg)

    db_manager = DBManager(db_path)

    # --- Nodes -------------------------------------------------------------
    articles = db_manager.get_iteration_data()
    accepted_value = SelectionStage.CONTENT_APPROVED.value
    # All corpus papers, regardless of the accepted-only filter. Used to tell
    # whether an isolated accepted node actually links to a paper that was
    # filtered out (cause 2) rather than being genuinely disconnected.
    all_corpus_ids = {a.id for a in articles if a.id}
    graph = nx.DiGraph()
    node_ids = set()
    for article in articles:
        if not article.id:
            continue
        if accepted_only and _as_int(article.selected, -999) != accepted_value:
            continue
        iteration = _as_int(article.iteration)
        node_ids.add(article.id)
        if graph.has_node(article.id):
            # Keep the earliest iteration in which the paper was discovered.
            if iteration < graph.nodes[article.id].get("iteration", iteration):
                graph.nodes[article.id]["iteration"] = iteration
                graph.nodes[article.id]["color"] = iteration_color(iteration)
            continue
        graph.add_node(
            article.id,
            label=(article.title or article.id)[:80],
            title=article.title or "",
            venue=article.venue or "",
            year=str(article.pub_year or ""),
            iteration=iteration,
            selected=_as_int(article.selected, 0),
            num_citations=_as_int(article.num_citations, 0),
            search_method=article.search_method or "",
            color=iteration_color(iteration),
        )
    _log(f"Loaded {graph.number_of_nodes()} nodes from the database"
         + (" (content-approved only)." if accepted_only else "."))

    # --- Duplicate-work merge map ------------------------------------------
    # Fold non-accepted preprint/conference twins into their accepted
    # representative so citation edges Semantic Scholar/OpenAlex attached to the
    # twin's id are not lost. Only mixed (one-accepted) pairs are merged.
    alias_to_rep = {}
    rep_to_aliases = {}
    if merge_duplicates:
        alias_to_rep = find_duplicate_merge_map(articles, accepted_value, log=_log)
        # Keep only aliases whose representative is actually a node and which are
        # not themselves nodes (avoid collapsing two retained nodes).
        alias_to_rep = {a: r for a, r in alias_to_rep.items()
                        if r in node_ids and a not in node_ids}
        for a, r in alias_to_rep.items():
            rep_to_aliases.setdefault(r, []).append(a)
        _log(f"Merging {len(alias_to_rep)} non-accepted twin(s) into "
             f"{len(rep_to_aliases)} accepted node(s).")

    def canon(pid: str) -> str:
        return alias_to_rep.get(pid, pid)

    # DOI -> retained node id. In accepted-only builds (and after a duplicate merge)
    # an edge may point at the id of a dropped twin; if that twin shares a DOI with a
    # retained node, we can still attach the edge to the node instead of losing it.
    doi_to_node: Dict[str, str] = {}
    for article in articles:
        if article.id in node_ids:
            nd = _normalize_doi(getattr(article, "doi", "") or "")
            if nd:
                doi_to_node.setdefault(nd, article.id)

    def resolve_endpoint(pid: str, doi: str) -> str:
        """Resolve a citation-edge endpoint to a retained node: apply the duplicate
        alias map, and if the id still is not a node, fall back to matching the
        edge's recorded DOI to a node (so edges survive duplicate merges)."""
        cp = canon(pid)
        if cp in node_ids:
            return cp
        nd = _normalize_doi(doi)
        if nd and nd in doi_to_node:
            return doi_to_node[nd]
        return cp

    # --- Edges from the citation-edge table --------------------------------
    db_edges_added = 0
    if edge_source in ("db", "both"):
        # Edges are stored canonically as citing -> cited, so no direction
        # reinterpretation is needed here.
        edges = db_manager.get_citation_edges()
        doi_resolved = 0
        for edge in edges:
            raw_src, raw_dst = canon(edge["citing_id"]), canon(edge["cited_id"])
            src = resolve_endpoint(edge["citing_id"], edge.get("citing_doi", ""))
            dst = resolve_endpoint(edge["cited_id"], edge.get("cited_doi", ""))
            if src not in node_ids or dst not in node_ids or src == dst:
                continue
            if src != raw_src or dst != raw_dst:
                doi_resolved += 1
            if graph.has_edge(src, dst):
                graph.edges[src, dst]["provenance"] = "db"
            else:
                graph.add_edge(
                    src, dst,
                    provenance="db",
                    source=edge.get("source", ""),
                    color=EDGE_COLOR_CITATION,
                )
                db_edges_added += 1
        _log(
            f"Added {db_edges_added} edges from the citation-edge table"
            + (f" ({doi_resolved} attached to a node by DOI across duplicates)." if doi_resolved else ".")
        )
        if edge_source == "db" and db_edges_added == 0:
            _log(
                "No recorded edges found. This database predates edge "
                "recording; use edge_source='api' or 'both' to reconstruct them."
            )

    # --- Edges reconstructed from the Semantic Scholar API -----------------
    if edge_source in ("api", "both"):
        cache = _load_reference_cache(api_cache_path)
        total = len(node_ids)
        api_edges_added = 0
        processed = 0

        def _relation_for_edge(src: str, dst: str) -> str:
            """Classify an edge oriented ``src -> dst`` (src cites dst) as a
            'citation' or 'reference' relative to the lower-iteration (more
            seed-ward) endpoint: an edge *out of* that node is a reference,
            an edge *into* it is a citation. This reproduces the recorded
            provenance convention (parent = lower iteration)."""
            it_src = _as_int(graph.nodes[src].get("iteration", 0))
            it_dst = _as_int(graph.nodes[dst].get("iteration", 0))
            return "reference" if it_src <= it_dst else "citation"

        def _direction_color(direction: str) -> str:
            return EDGE_COLOR_CITATION if direction == "citation" else EDGE_COLOR_REFERENCE

        def _add_api_edge(src: str, dst: str) -> int:
            """Add/corroborate a 'src cites dst' edge; return 1 if newly added."""
            if graph.has_edge(src, dst):
                if graph.edges[src, dst].get("provenance") == "db":
                    graph.edges[src, dst]["provenance"] = "both"
                return 0
            # Never create a reciprocal pair: if the opposite direction already
            # exists (two papers cannot normally cite each other), keep the
            # established one rather than adding a contradictory 2-cycle.
            if graph.has_edge(dst, src):
                return 0
            direction = _relation_for_edge(src, dst)
            graph.add_edge(src, dst, provenance="api", snowball_direction=direction,
                           color=_direction_color(direction))
            return 1

        # DOI for every endpoint id Semantic Scholar reports, so edges to a dropped
        # twin's id can be re-attached to the node sharing that DOI (same treatment
        # as the db-edge path). Accumulated across all papers.
        fetched_doi: Dict[str, str] = {}
        api_doi_resolved = 0

        directions = "references and citations" if use_citations else "references"
        _log(f"Reconstructing edges from Semantic Scholar ({directions}) for "
             f"{total} papers ({len(cache)} already cached)...")
        for paper_id in node_ids:
            if cancel_flag is not None and cancel_flag.is_set():
                _log("Reconstruction cancelled by user.")
                break
            processed += 1

            # Query the node itself and any non-accepted twins folded into it,
            # so edges stranded on a twin's Semantic Scholar id are recovered.
            refs, cits = set(), set()
            for qid in [paper_id] + rep_to_aliases.get(paper_id, []):
                entry = _cache_entry(cache, qid)
                changed = False
                if "references" not in entry:
                    pairs = _fetch_edge_endpoints(qid, "references")
                    entry["references"] = [pid for pid, _ in pairs]
                    entry["ref_dois"] = {pid: d for pid, d in pairs if d}
                    changed = True
                if use_citations and "citations" not in entry:
                    pairs = _fetch_edge_endpoints(qid, "citations")
                    entry["citations"] = [pid for pid, _ in pairs]
                    entry["cit_dois"] = {pid: d for pid, d in pairs if d}
                    changed = True
                if changed:
                    cache[qid] = entry
                refs.update(entry.get("references", []))
                cits.update(entry.get("citations", []) or [])
                endpoint_dois = {**entry.get("ref_dois", {}), **entry.get("cit_dois", {})}
                fetched_doi.update(endpoint_dois)
                # Opportunistically enrich DOI->node from endpoints that are nodes.
                for pid, d in endpoint_dois.items():
                    cp = canon(pid)
                    if d and cp in node_ids:
                        doi_to_node.setdefault(d, cp)
            if processed % 25 == 0:
                _save_reference_cache(api_cache_path, cache)
                _log(f"  ...{processed}/{total} papers queried")

            # paper_id cites each of its references (resolve twin ids by alias/DOI).
            for ref_id in refs:
                dst = resolve_endpoint(ref_id, fetched_doi.get(ref_id, ""))
                if dst in node_ids and dst != paper_id:
                    if dst != canon(ref_id):
                        api_doi_resolved += 1
                    api_edges_added += _add_api_edge(paper_id, dst)
            # Each citing paper cites paper_id.
            for cit_id in cits:
                src = resolve_endpoint(cit_id, fetched_doi.get(cit_id, ""))
                if src in node_ids and src != paper_id:
                    if src != canon(cit_id):
                        api_doi_resolved += 1
                    api_edges_added += _add_api_edge(src, paper_id)
        _save_reference_cache(api_cache_path, cache)
        _log(
            f"Added {api_edges_added} edges reconstructed from the API"
            + (f" ({api_doi_resolved} attached to a node by DOI across duplicates)." if api_doi_resolved else ".")
        )

        # --- Augment with OpenAlex (DOI-matched) ---------------------------
        if augment_openalex and not (cancel_flag is not None and cancel_flag.is_set()):
            _log("Augmenting edges from OpenAlex (DOI-matched)...")
            node_list = list(node_ids) + list(alias_to_rep.keys())
            doi_by_node = _fetch_dois_for_nodes(node_list, log=_log)
            _log(f"  Resolved DOIs for {len(doi_by_node)}/{len(node_list)} papers.")
            if doi_by_node:
                node_by_doi = {d: n for n, d in doi_by_node.items()}
                works = _fetch_openalex_referenced_works(
                    list(node_by_doi.keys()), log=_log, cancel_flag=cancel_flag
                )
                # Map OpenAlex work id -> our node id (only for corpus papers).
                oaid_to_node = {
                    info["openalex_id"]: node_by_doi[doi]
                    for doi, info in works.items()
                    if info.get("openalex_id") and doi in node_by_doi
                }
                oa_edges = 0
                oa_conflicts = 0
                for doi, info in works.items():
                    src = canon(node_by_doi.get(doi, ""))
                    if src not in node_ids:
                        continue
                    for ref_oaid in info.get("referenced_works", []):
                        dst = canon(oaid_to_node.get(ref_oaid, ""))
                        if dst not in node_ids or dst == src:
                            continue
                        if graph.has_edge(src, dst):
                            s = graph.edges[src, dst].get("provenance", "")
                            if "openalex" not in s:
                                graph.edges[src, dst]["provenance"] = (s + "+openalex") if s else "openalex"
                        elif graph.has_edge(dst, src):
                            # OpenAlex claims the opposite direction to an edge we
                            # already have (usually from Semantic Scholar). Keep the
                            # established one instead of forming a 2-cycle.
                            oa_conflicts += 1
                        else:
                            direction = _relation_for_edge(src, dst)
                            graph.add_edge(src, dst, provenance="openalex",
                                           snowball_direction=direction,
                                           color=_direction_color(direction))
                            oa_edges += 1
                _log(f"Added {oa_edges} new edges from OpenAlex.")
                if oa_conflicts:
                    _log(f"Skipped {oa_conflicts} OpenAlex edge(s) that contradicted an "
                         f"existing edge's direction (avoided 2-cycles).")
            else:
                _log("  No DOIs resolved; OpenAlex augmentation skipped.")

        # --- Augment from the papers' PDFs (recover references providers lack) ---
        if augment_pdfs and pdf_dir and not (cancel_flag is not None and cancel_flag.is_set()):
            def _add_reconstructed_edge(src, dst, provenance):
                if src == dst or not graph.has_node(src) or not graph.has_node(dst):
                    return 0
                if graph.has_edge(src, dst):
                    s = graph.edges[src, dst].get("provenance", "")
                    if provenance not in s:
                        graph.edges[src, dst]["provenance"] = (s + "+" + provenance) if s else provenance
                    return 0
                if graph.has_edge(dst, src):
                    return 0  # reverse exists; don't form a 2-cycle
                direction = _relation_for_edge(src, dst)
                graph.add_edge(src, dst, provenance=provenance,
                               snowball_direction=direction, color=_direction_color(direction))
                return 1

            pdf_cache = None
            if api_cache_path:
                pdf_cache = api_cache_path.replace("_references_cache.json", "_pdf_refs_cache.json")
                if pdf_cache == api_cache_path:
                    pdf_cache = api_cache_path + ".pdf_refs.json"
            augment_from_pdfs(graph, node_ids, pdf_dir, _add_reconstructed_edge,
                              cache_path=pdf_cache, log=_log, cancel_flag=cancel_flag)

        # --- Flag why accepted-only nodes are isolated (cause 1 vs 2) -------
        if accepted_only:
            # Merged twins are not "hidden filtered links" — they are folded in.
            non_accepted_corpus = (all_corpus_ids - node_ids) - set(alias_to_rep.keys())
            filtered_iso = 0
            disconnected_iso = 0
            for pid in node_ids:
                neigh = set()
                for qid in [pid] + rep_to_aliases.get(pid, []):
                    e = _cache_entry(cache, qid)
                    neigh |= set(e.get("references", [])) | set(e.get("citations", []) or [])
                neigh = {canon(x) for x in neigh}
                hidden = len(neigh & non_accepted_corpus)
                graph.nodes[pid]["hidden_links"] = hidden
                if graph.degree(pid) == 0:
                    if hidden > 0:
                        graph.nodes[pid]["isolation_cause"] = "filtered"
                        filtered_iso += 1
                    else:
                        graph.nodes[pid]["isolation_cause"] = "disconnected"
                        disconnected_iso += 1
                else:
                    graph.nodes[pid]["isolation_cause"] = ""
            _log(f"Isolated accepted nodes: {filtered_iso} only because accepted-only "
                 f"hid their links (orange border), {disconnected_iso} with no corpus "
                 f"links at all (grey border).")

    _log(f"Graph complete: {graph.number_of_nodes()} nodes, "
         f"{graph.number_of_edges()} edges.")
    return graph


# ----------------------------------------------------------------------------
# Exporters
# ----------------------------------------------------------------------------

def _sanitize_for_export(graph):
    """Return a copy whose attribute values are all GraphML/GEXF-safe scalars."""
    import networkx as nx

    clean = nx.DiGraph()
    for node, attrs in graph.nodes(data=True):
        clean.add_node(node, **{k: ("" if v is None else v) for k, v in attrs.items()})
    for u, v, attrs in graph.edges(data=True):
        clean.add_edge(u, v, **{k: ("" if val is None else val) for k, val in attrs.items()})
    return clean


def export_graphml(graph, path: str) -> str:
    import networkx as nx
    nx.write_graphml(_sanitize_for_export(graph), path)
    return path


def export_gexf(graph, path: str) -> str:
    import networkx as nx
    nx.write_gexf(_sanitize_for_export(graph), path)
    return path


def export_json(graph, path: str) -> str:
    """Write a simple nodes/edges JSON document."""
    data = {
        "nodes": [
            {"id": node, **attrs}
            for node, attrs in graph.nodes(data=True)
        ],
        "edges": [
            {"source": u, "target": v, **attrs}
            for u, v, attrs in graph.edges(data=True)
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def _legend_html(graph) -> str:
    """Build a small HTML legend listing the iterations present."""
    iterations = sorted({graph.nodes[n].get("iteration", 0) for n in graph.nodes})
    items = []
    for it in iterations:
        label = "Iteration 0 (seed)" if it == 0 else f"Iteration {it}"
        items.append(
            f'<div style="display:flex;align-items:center;margin:2px 0;">'
            f'<span style="width:14px;height:14px;border-radius:50%;background:{iteration_color(it)};'
            f'display:inline-block;margin-right:8px;"></span>{label}</div>'
        )
    edge_items = [
        ("Citation (points to the more central paper)", EDGE_COLOR_CITATION),
        ("Reference (points away from the more central paper)", EDGE_COLOR_REFERENCE),
    ]
    for label, color in edge_items:
        items.append(
            f'<div style="display:flex;align-items:center;margin:2px 0;">'
            f'<span style="width:14px;height:3px;background:{color};'
            f'display:inline-block;margin-right:8px;"></span>{label}</div>'
        )
    items.append(
        '<div style="margin:3px 0 2px 0;font-size:11px;color:#666;">'
        'Edge source (recorded / Semantic&nbsp;Scholar / OpenAlex) is shown on hover.</div>'
    )

    # Only show the isolation-cause key when such nodes exist (accepted-only runs).
    causes = {graph.nodes[n].get("isolation_cause", "") for n in graph.nodes}
    border_items = []
    if "filtered" in causes:
        border_items.append(("Isolated: links hidden by accepted-only filter", BORDER_FILTERED))
    if "disconnected" in causes:
        border_items.append(("Isolated: no links to any other paper shown", BORDER_DISCONNECTED))
    for label, color in border_items:
        items.append(
            f'<div style="display:flex;align-items:center;margin:2px 0;">'
            f'<span style="width:12px;height:12px;border-radius:50%;background:#fff;'
            f'border:3px solid {color};display:inline-block;margin-right:8px;"></span>{label}</div>'
        )
    return (
        '<div style="position:fixed;top:10px;left:10px;z-index:1000;'
        'background:rgba(255,255,255,0.95);border:1px solid #ccc;border-radius:8px;'
        'padding:10px 14px;font-family:sans-serif;font-size:13px;color:#222;'
        'box-shadow:0 2px 6px rgba(0,0,0,0.15);">'
        '<div style="font-weight:600;margin-bottom:6px;">Legend</div>'
        + "".join(items)
        + "</div>"
    )


def export_html(graph, path: str, use_physics: bool = False) -> str:
    """Render an interactive HTML visualization with pyvis.

    For large graphs physics is disabled and a static spring layout is baked in
    so the page renders instantly instead of simulating in the browser.
    """
    try:
        from pyvis.network import Network
    except ImportError as e:
        raise ImportError(
            "pyvis is required for the HTML visualization. "
            "Install it with `pip install pyvis`."
        ) from e
    import networkx as nx

    net = Network(
        height="100vh",
        width="100%",
        directed=True,
        notebook=False,
        cdn_resources="in_line",
        bgcolor="#ffffff",
        font_color="#222222",
    )

    large = graph.number_of_nodes() > 400
    positions = {}
    if not use_physics:
        # Bake a static layout so the browser does not have to simulate physics.
        try:
            positions = nx.spring_layout(graph, seed=42, k=None, iterations=50)
        except Exception:
            positions = {}

    for node, attrs in graph.nodes(data=True):
        iter_color = attrs.get("color", "#888888")
        cause = attrs.get("isolation_cause", "")
        hidden = _as_int(attrs.get("hidden_links", 0))
        hover = (
            f"<b>{attrs.get('title', '') or attrs.get('label', '')}</b><br>"
            f"Venue: {attrs.get('venue', '')}<br>"
            f"Year: {attrs.get('year', '')}<br>"
            f"Iteration: {attrs.get('iteration', '')}<br>"
            f"Citations: {attrs.get('num_citations', '')}"
        )
        if cause == "filtered":
            hover += (f"<br><b>Isolated:</b> {hidden} link(s) to papers removed by the "
                      f"accepted-only filter")
        elif cause == "disconnected":
            hover += "<br><b>Isolated:</b> no links to any other paper shown"

        # A coloured border flags *why* a node is isolated (accepted-only runs).
        if cause == "filtered":
            kwargs_color = {"background": iter_color, "border": BORDER_FILTERED}
        elif cause == "disconnected":
            kwargs_color = {"background": iter_color, "border": BORDER_DISCONNECTED}
        else:
            kwargs_color = iter_color
        kwargs = dict(
            # Labels are hidden on the canvas to avoid clutter; the title/metadata
            # is revealed in the hover tooltip instead.
            label=" ",
            title=hover,
            color=kwargs_color,
            value=max(1, _as_int(attrs.get("num_citations", 0))),
        )
        if cause in ("filtered", "disconnected"):
            kwargs["borderWidth"] = 4
        if positions and node in positions:
            x, y = positions[node]
            kwargs["x"] = float(x) * 1500
            kwargs["y"] = float(y) * 1500
            kwargs["physics"] = False
        net.add_node(node, **kwargs)

    for u, v, attrs in graph.edges(data=True):
        _dir = attrs.get("snowball_direction", "") or "—"
        _prov = attrs.get("provenance", "")
        net.add_edge(u, v, color=attrs.get("color", "#cccccc"),
                     title=f"{_dir} ({_prov})" if _prov else _dir)

    if use_physics:
        net.barnes_hut(spring_length=120, gravity=-8000)
    else:
        net.toggle_physics(False)

    # pyvis writes relative to cwd for some versions; use generate + write.
    html = net.generate_html(notebook=False)
    html = html.replace("</body>", _legend_html(graph) + "</body>")
    with open(path, "w") as f:
        f.write(html)
    return path


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------

DEFAULT_FORMATS = ("graphml", "gexf", "json", "html")


def generate_citation_graph(
    db_path: str,
    output_dir: str,
    edge_source: str = "both",
    formats=DEFAULT_FORMATS,
    basename: str = "citation_graph",
    api_cache_path: Optional[str] = None,
    accepted_only: bool = False,
    use_citations: bool = True,
    augment_openalex: bool = False,
    augment_pdfs: bool = False,
    pdf_dir: Optional[str] = None,
    merge_duplicates: bool = False,
    use_physics: bool = False,
    log: Optional[Callable[[str], None]] = None,
    cancel_flag=None,
) -> Dict[str, str]:
    """Build the graph and write the requested output files.

    Returns a mapping of format -> written file path.
    """
    os.makedirs(output_dir, exist_ok=True)
    if api_cache_path is None and edge_source in ("api", "both"):
        # The reference cache stores raw references per paper, so it is the same
        # regardless of the accepted-only filter. Strip an "_accepted" suffix
        # from the basename so accepted-only and full runs share one cache.
        cache_base = basename[:-len("_accepted")] if basename.endswith("_accepted") else basename
        api_cache_path = os.path.join(output_dir, f"{cache_base}_references_cache.json")

    graph = build_citation_graph(
        db_path,
        edge_source=edge_source,
        api_cache_path=api_cache_path,
        accepted_only=accepted_only,
        use_citations=use_citations,
        augment_openalex=augment_openalex,
        augment_pdfs=augment_pdfs,
        pdf_dir=pdf_dir,
        merge_duplicates=merge_duplicates,
        log=log,
        cancel_flag=cancel_flag,
    )

    outputs: Dict[str, str] = {}
    exporters = {
        "graphml": lambda p: export_graphml(graph, p),
        "gexf": lambda p: export_gexf(graph, p),
        "json": lambda p: export_json(graph, p),
        "html": lambda p: export_html(graph, p, use_physics=use_physics),
    }
    ext = {"graphml": "graphml", "gexf": "gexf", "json": "json", "html": "html"}
    for fmt in formats:
        fmt = fmt.lower()
        if fmt not in exporters:
            if log:
                log(f"Skipping unknown format: {fmt}")
            continue
        path = os.path.join(output_dir, f"{basename}.{ext[fmt]}")
        exporters[fmt](path)
        outputs[fmt] = path
        if log:
            log(f"Wrote {fmt.upper()} -> {path}")

    return outputs
