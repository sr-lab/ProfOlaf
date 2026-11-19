"""Tests for utils/pipeline/start_iteration_utils.py.

`snowball_direction_kwargs` (pure, with a tricky provider-name inversion) and
`get_articles` (provenance-edge recording vs. seen-title dedup) are tested with a
stub search method, so no network is involved.
"""
import pytest

from conftest import make_article
from utils.pipeline import start_iteration_utils as si


class TestSnowballDirectionKwargs:
    @pytest.mark.parametrize("direction,expected", [
        # forward = citations; provider uses inverted names (backwards == citations)
        ("forward", {"backwards": True, "forwards": False}),
        ("backward", {"backwards": False, "forwards": True}),
        ("both", {"backwards": True, "forwards": True}),
    ])
    def test_mapping(self, direction, expected):
        assert si.snowball_direction_kwargs(direction) == expected

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            si.snowball_direction_kwargs("sideways")


class FakeSearch:
    """Minimal stand-in for an ArticleSearch provider."""
    def __init__(self, mapping):
        self.mapping = mapping

    def get_snowballing_articles(self, citedby, iteration=0, **kwargs):
        return self.mapping.get(citedby, [])


class TestGetArticles:
    def test_records_canonical_edges_but_dedups_nodes(self, db):
        parent = make_article("P", title="Parent Paper", iteration=0, doi="10.1/parent")
        new_x = make_article("X", title="Brand New Paper", iteration=1, doi="10.1/x",
                             search_method="openalex")
        seen_y = make_article("Y", title="Already Seen Paper", iteration=1,
                              search_method="openalex")
        # Y's title is already known -> it should be filtered out as a node...
        db.insert_seen_titles_data([("already seen paper", "Y")])

        # X cites P (citation) -> edge X->P ; P cites Y (reference) -> edge P->Y
        search = FakeSearch({"P": [(new_x, "citation"), (seen_y, "reference")]})
        si.get_articles(1, [parent], db, search, snowball_direction="both")

        # BOTH edges recorded, oriented citing -> cited (no direction field needed).
        edges = {(e["citing_id"], e["cited_id"]) for e in db.get_citation_edges(iteration=1)}
        assert edges == {("X", "P"), ("P", "Y")}

        # Endpoint DOIs and source are carried through.
        by_pair = {(e["citing_id"], e["cited_id"]): e for e in db.get_citation_edges()}
        assert by_pair[("X", "P")]["citing_doi"] == "10.1/x"
        assert by_pair[("X", "P")]["cited_doi"] == "10.1/parent"
        assert by_pair[("X", "P")]["source"] == "openalex"

        # Only the genuinely new article is inserted as an iteration node.
        node_ids = {a.id for a in db.get_iteration_data(iteration=1)}
        assert node_ids == {"X"}
        assert db.get_seen_title("Brand New Paper") is not None

    def test_no_results_is_noop(self, db):
        parent = make_article("P", title="Parent", iteration=0)
        si.get_articles(1, [parent], db, FakeSearch({}), snowball_direction="both")
        assert db.get_iteration_data(iteration=1) == []
        assert db.get_citation_edges(iteration=1) == []
