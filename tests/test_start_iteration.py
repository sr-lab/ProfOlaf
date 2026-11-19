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
    def test_records_all_edges_but_dedups_nodes(self, db):
        parent = make_article("P", title="Parent Paper", iteration=0)
        new_x = make_article("X", title="Brand New Paper", iteration=1)
        seen_y = make_article("Y", title="Already Seen Paper", iteration=1)
        # Y's title is already known -> it should be filtered out as a node...
        db.insert_seen_titles_data([("already seen paper", "Y")])

        search = FakeSearch({"P": [(new_x, "citation"), (seen_y, "reference")]})
        si.get_articles(1, [parent], db, search, snowball_direction="both")

        # ...but BOTH provenance edges are recorded (graph keeps all parent->child links).
        edges = {(e["parent_id"], e["child_id"], e["direction"]) for e in db.get_snowball_edges(iteration=1)}
        assert edges == {("P", "X", "citation"), ("P", "Y", "reference")}

        # Only the genuinely new article is inserted as an iteration node.
        node_ids = {a.id for a in db.get_iteration_data(iteration=1)}
        assert node_ids == {"X"}

        # The new article's title is now marked as seen.
        assert db.get_seen_title("Brand New Paper") is not None

    def test_no_results_is_noop(self, db):
        parent = make_article("P", title="Parent", iteration=0)
        si.get_articles(1, [parent], db, FakeSearch({}), snowball_direction="both")
        assert db.get_iteration_data(iteration=1) == []
        assert db.get_snowball_edges(iteration=1) == []
