"""Integration test for the citation-graph builder reading canonical edges.

Uses edge_source='db', which is fully offline (nodes come from the corpus, edges
from the citation_edges table), so no network is touched.
"""
from conftest import make_article
from utils.db_management import SelectionStage
from utils.pipeline.citation_graph_utils import build_citation_graph


def _seed(db):
    db.insert_iteration_data([
        make_article("A", iteration=0, title="Paper A", selected=SelectionStage.CONTENT_APPROVED),
        make_article("B", iteration=1, title="Paper B", selected=SelectionStage.CONTENT_APPROVED),
        make_article("C", iteration=1, title="Paper C", selected=SelectionStage.CONTENT_APPROVED),
    ])


class TestBuildFromCanonicalEdges:
    def test_edges_are_directed_citing_to_cited(self, db):
        _seed(db)
        # A cites B, B cites C  (citing -> cited)
        db.insert_citation_edges([
            ("A", "B", "", "", "openalex", 1),
            ("B", "C", "", "", "openalex", 1),
        ])
        graph = build_citation_graph(db.db_path, edge_source="db")
        assert graph.number_of_nodes() == 3
        assert graph.has_edge("A", "B")
        assert graph.has_edge("B", "C")
        # orientation is preserved — no reverse edges
        assert not graph.has_edge("B", "A")
        assert not graph.has_edge("C", "B")

    def test_edges_to_missing_nodes_are_skipped(self, db):
        _seed(db)
        db.insert_citation_edges([
            ("A", "B", "", "", "openalex", 1),
            ("A", "Z", "", "", "openalex", 1),  # Z is not a corpus node
        ])
        graph = build_citation_graph(db.db_path, edge_source="db")
        assert graph.has_edge("A", "B")
        assert not graph.has_node("Z")


class TestDoiResolutionAcrossDuplicates:
    def test_accepted_only_resolves_edge_to_accepted_twin_by_doi(self, db):
        # Accepted conference version (node) and a rejected preprint twin (dropped in
        # accepted-only) that share a DOI.
        db.insert_iteration_data([
            make_article("CONF", iteration=1, title="The Paper (conference)",
                         doi="10.1/same", selected=SelectionStage.CONTENT_APPROVED),
            make_article("CITER", iteration=1, title="A Citing Paper",
                         selected=SelectionStage.CONTENT_APPROVED),
            make_article("PRE", iteration=1, title="The Paper (preprint)",
                         doi="10.1/same", selected=SelectionStage.NOT_SELECTED),  # rejected
        ])
        # The edge was recorded against the PREPRINT's id, but carries the shared DOI.
        db.insert_citation_edges([("CITER", "PRE", "", "10.1/same", "openalex", 1)])

        graph = build_citation_graph(db.db_path, edge_source="db", accepted_only=True)
        # PRE is not an accepted node...
        assert not graph.has_node("PRE")
        # ...but the edge is attached to the accepted twin CONF via the shared DOI.
        assert graph.has_edge("CITER", "CONF")

    def test_no_doi_match_leaves_edge_dropped(self, db):
        db.insert_iteration_data([
            make_article("CONF", iteration=1, title="The Paper", doi="10.1/conf",
                         selected=SelectionStage.CONTENT_APPROVED),
            make_article("CITER", iteration=1, title="Citer",
                         selected=SelectionStage.CONTENT_APPROVED),
        ])
        # Edge points at a non-node id with a DOI that matches no accepted node.
        db.insert_citation_edges([("CITER", "PRE", "", "10.1/other", "openalex", 1)])
        graph = build_citation_graph(db.db_path, edge_source="db", accepted_only=True)
        assert not graph.has_edge("CITER", "CONF")


class TestApiPathDoiResolution:
    def test_api_edge_resolved_to_accepted_twin_by_doi(self, db, monkeypatch):
        import utils.pipeline.citation_graph_utils as cg
        db.insert_iteration_data([
            make_article("CONF", iteration=0, title="The Paper (conference)",
                         doi="10.1/same", selected=SelectionStage.CONTENT_APPROVED),
            make_article("CITER", iteration=1, title="A Citing Paper",
                         selected=SelectionStage.CONTENT_APPROVED),
        ])

        # Semantic Scholar says CITER references paper id "PRE" (the rejected
        # preprint twin, not in the corpus) whose DOI matches the accepted CONF.
        def fake_endpoints(paper_id, direction="references"):
            if paper_id == "CITER" and direction == "references":
                return [("PRE", "10.1/same")]
            return []

        monkeypatch.setattr(cg, "_fetch_edge_endpoints", fake_endpoints)
        graph = cg.build_citation_graph(db.db_path, edge_source="api", accepted_only=True)
        # The reconstructed edge is attached to CONF via the shared DOI.
        assert graph.has_edge("CITER", "CONF")
        assert not graph.has_node("PRE")

    def test_api_edge_dropped_without_doi_match(self, db, monkeypatch):
        import utils.pipeline.citation_graph_utils as cg
        db.insert_iteration_data([
            make_article("CONF", iteration=0, title="The Paper", doi="10.1/same",
                         selected=SelectionStage.CONTENT_APPROVED),
            make_article("CITER", iteration=1, title="A Citing Paper",
                         selected=SelectionStage.CONTENT_APPROVED),
        ])

        def fake_endpoints(paper_id, direction="references"):
            if paper_id == "CITER" and direction == "references":
                return [("PRE", "10.1/different")]  # no matching node DOI
            return []

        monkeypatch.setattr(cg, "_fetch_edge_endpoints", fake_endpoints)
        graph = cg.build_citation_graph(db.db_path, edge_source="api", accepted_only=True)
        assert not graph.has_edge("CITER", "CONF")
