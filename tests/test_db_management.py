"""Characterization + regression tests for utils/db_management.py (the DB layer).

These pin down the behaviour of the central ``DBManager`` so the rest of the
backend can be refactored safely. Some tests assert *correct* behaviour that the
current code violates; those are marked with a ``BUG:`` note and double as
regression tests once the bug is fixed.
"""
import sqlite3

import pytest

from conftest import make_article
from utils.db_management import ArticleData, DBManager, SelectionStage


# --------------------------------------------------------------------------- #
# iterations table
# --------------------------------------------------------------------------- #
class TestIterations:
    def test_insert_and_roundtrip(self, db):
        arts = [make_article(i, iteration=1, title=f"T{i}") for i in (10, 20, 30)]
        db.insert_iteration_data(arts)
        rows = db.get_iteration_data(iteration=1)
        assert len(rows) == 3
        got = {r.id: r for r in rows}
        assert got["10"].title == "T10"
        assert got["20"].venue == "Some Conference"
        # pub_year stored as TEXT but should round-trip to the same value
        assert str(got["30"].pub_year) == "2022"

    def test_insert_does_not_mutate_caller_objects(self, db):
        """BUG: insert_iteration_data mutates the ArticleData objects it is given
        (enum -> int, None -> "") because it edits ``obj.__dict__`` in place."""
        art = make_article(1, iteration=1, selected=SelectionStage.METADATA_APPROVED)
        assert art.download_filtered_out is None
        db.insert_iteration_data([art])
        assert art.selected == SelectionStage.METADATA_APPROVED, (
            "insert should not downgrade the enum on the caller's object"
        )
        assert art.download_filtered_out is None, (
            "insert should not replace None fields with '' on the caller's object"
        )

    def test_selected_enum_is_persisted_as_value(self, db):
        art = make_article(1, iteration=1, selected=SelectionStage.CONTENT_APPROVED)
        db.insert_iteration_data([art])
        row = db.get_iteration_data(iteration=1, id="1")[0]
        assert str(row.selected) == str(SelectionStage.CONTENT_APPROVED.value)

    def test_update_iteration_data_single_article(self, db):
        db.insert_iteration_data([make_article(1, iteration=1)])
        db.update_iteration_data(1, "1", keep_title=True, selected=SelectionStage.TITLE_APPROVED.value)
        row = db.get_iteration_data(iteration=1, id="1")[0]
        assert db.get_keep_title("1", 1) is True
        assert str(row.selected) == str(SelectionStage.TITLE_APPROVED.value)

    def test_get_keep_title_and_content_roundtrip(self, db):
        db.insert_iteration_data([make_article(1, iteration=1)])
        db.update_iteration_data(1, "1", keep_title=False, keep_content=True)
        assert db.get_keep_title("1", 1) is False
        assert db.get_keep_content("1", 1) is True

    def test_get_keep_tri_state_undecided_vs_reject(self, db):
        """get_keep_* distinguishes undecided (None) from an explicit reject (False)."""
        db.insert_iteration_data([make_article(1, iteration=1)])
        # Freshly inserted, never screened -> undecided, NOT reject
        assert db.get_keep_title("1", 1) is None
        assert db.get_keep_content("1", 1) is None
        # No such row at all -> None
        assert db.get_keep_title("nope", 1) is None
        # Explicit decisions
        db.update_iteration_data(1, "1", keep_title=False)
        assert db.get_keep_title("1", 1) is False
        db.update_iteration_data(1, "1", keep_title=True)
        assert db.get_keep_title("1", 1) is True

    def test_query_operators(self, db):
        db.insert_iteration_data([
            make_article(1, iteration=1, bibtex="@x{...}"),
            make_article(2, iteration=1, bibtex=""),
        ])
        assert len(db.get_iteration_data(iteration=1, bibtex__not_empty=True)) == 1
        assert len(db.get_iteration_data(iteration=1, bibtex__empty=True)) == 1
        assert len(db.get_iteration_data(iteration=1, id__in=["1", "2"])) == 2
        assert len(db.get_iteration_data(iteration=1, id__ne="1")) == 1

    def test_check_current_iteration_single_digit(self, db):
        db.insert_iteration_data([make_article(1, iteration=0)])
        db.insert_iteration_data([make_article(2, iteration=1)])
        current, _max_selected, method = db.check_current_iteration()
        assert int(current) == 1
        assert method == "semantic_scholar"

    def test_check_current_iteration_double_digit(self, db):
        """BUG: iteration is stored as TEXT and MAX() compares lexicographically,
        so MAX('2','10') == '2'. Current iteration should be the numeric max."""
        db.insert_iteration_data([make_article(1, iteration=2)])
        db.insert_iteration_data([make_article(2, iteration=10)])
        current, _max_selected, _method = db.check_current_iteration()
        assert int(current) == 10

    def test_clear_unidentified_articles(self, db):
        db.insert_iteration_data([
            make_article("", iteration=1, title="no-id"),
            make_article(5, iteration=1),
        ])
        db.clear_unidentified_articles(1)
        rows = db.get_iteration_data(iteration=1)
        assert [r.id for r in rows] == ["5"]

    def test_delete_batch_iteration_data(self, db):
        db.insert_iteration_data([
            make_article(1, iteration=1, title="keep"),
            make_article(2, iteration=1, title="drop"),
        ])
        db.delete_batch_iteration_data(1, ["drop"])
        rows = db.get_iteration_data(iteration=1)
        assert [r.title for r in rows] == ["keep"]

    def test_update_batch_iteration_data(self, db):
        db.insert_iteration_data([
            make_article(1, iteration=1),
            make_article(2, iteration=1),
        ])
        db.update_batch_iteration_data(1, [("1", True, "keep_title"), ("2", False, "keep_title")])
        assert db.get_keep_title("1", 1) is True
        assert db.get_keep_title("2", 1) is False


# --------------------------------------------------------------------------- #
# screening table
# --------------------------------------------------------------------------- #
class TestScreening:
    def _title_vote(self, db, article_id, rater, keep, iteration=1, reason="r"):
        db.insert_screening_data(
            article_id, rater, iteration, keep, reason,
            settled=False, screening_phase="title", title=f"title-{article_id}",
        )

    def test_insert_and_read_single_vote(self, db):
        self._title_vote(db, "1", "alice", True)
        rows = db.get_screening_rows_by_rater("alice", 1)
        assert len(rows) == 1
        assert rows[0]["keep_title"] in (1, True)

    def test_conflict_updates_same_rater(self, db):
        self._title_vote(db, "1", "alice", True)
        self._title_vote(db, "1", "alice", False, reason="changed mind")
        rows = db.get_screening_rows_by_rater("alice", 1)
        assert len(rows) == 1, "same (id, rater) must upsert, not duplicate"
        assert rows[0]["keep_title"] in (0, False)
        assert rows[0]["reason_title"] == "changed mind"

    def test_agreements_all_keep(self, db):
        self._title_vote(db, "1", "alice", True)
        self._title_vote(db, "1", "bob", True)
        agree = db.get_agreements_screening_data(1, phase="title")
        assert {r["id"] for r in agree} == {"1"}

    def test_disagreement_detected(self, db):
        self._title_vote(db, "1", "alice", True)
        self._title_vote(db, "1", "bob", False)
        disagree = db.get_disagreements_screening_data(1, phase="title")
        assert {r["id"] for r in disagree} == {"1"}
        # and it must NOT show up as an agreement
        agree = db.get_agreements_screening_data(1, phase="title")
        assert "1" not in {r["id"] for r in agree}

    def test_partial_votes_not_agreement(self, db):
        """Only one of two raters has voted -> not yet a full agreement."""
        self._title_vote(db, "1", "alice", True)
        # bob has a row but no title decision yet
        db.insert_screening_data("1", "bob", 1, True, "", screening_phase="content", title="t")
        agree = db.get_agreements_screening_data(1, phase="title")
        assert "1" not in {r["id"] for r in agree}

    def test_get_screening_raters(self, db):
        self._title_vote(db, "1", "alice", True)
        self._title_vote(db, "2", "bob", True)
        assert db.get_screening_raters() == ["alice", "bob"]

    def test_settle_and_sync_to_iterations(self, db):
        db.insert_iteration_data([make_article(1, iteration=1)])
        self._title_vote(db, "1", "alice", True)
        self._title_vote(db, "1", "bob", True)
        db.settle_screening_data(1, "1", settled=True, phase="title")
        db.sync_iteration_from_settled_screening(1, "title", SelectionStage.TITLE_APPROVED)
        assert db.get_keep_title("1", 1) is True
        row = db.get_iteration_data(iteration=1, id="1")[0]
        assert str(row.selected) == str(SelectionStage.TITLE_APPROVED.value)

    def test_annotations_roundtrip(self, db):
        db.insert_annotations_data("1", 1, Scope="broad", Techniques="LLM")
        rows = db.get_annotations_data(1, "1")
        assert rows and rows[0]["Scope"] == "broad" and rows[0]["Techniques"] == "LLM"


# --------------------------------------------------------------------------- #
# seen_titles / conf_rank / snowball_edges / workflow_metadata
# --------------------------------------------------------------------------- #
class TestAuxTables:
    def test_seen_titles_lowercased(self, db):
        db.insert_seen_titles_data([("Mixed Case Title", "42")])
        assert db.get_seen_title("mixed case title") is not None
        assert db.get_seen_title("MIXED CASE TITLE") is not None

    def test_conf_rank_roundtrip(self, db):
        db.insert_conf_rank_data([("ICSE", "A*")])
        assert db.get_venue_rank_data("ICSE")[0] == "A*"

    def test_citation_edges_roundtrip_and_dedup(self, db):
        db.insert_citation_edges([("A", "B", "10.1/a", "10.1/b", "openalex", 1)])
        db.insert_citation_edges([("A", "B", "10.1/a", "10.1/b", "openalex", 1)])  # dup ignored
        edges = db.get_citation_edges(iteration=1)
        assert len(edges) == 1
        e = edges[0]
        assert (e["citing_id"], e["cited_id"]) == ("A", "B")
        assert e["citing_doi"] == "10.1/a" and e["source"] == "openalex"

    def test_citation_edges_skip_bad_endpoints(self, db):
        db.insert_citation_edges([
            ("A", "", "", "", "", 1),    # missing cited
            ("", "B", "", "", "", 1),    # missing citing
            ("C", "C", "", "", "", 1),   # self-loop
            ("A", "B", "", "", "", 1),   # valid
        ])
        assert {(e["citing_id"], e["cited_id"]) for e in db.get_citation_edges()} == {("A", "B")}

    def test_citation_edges_upsert_fills_missing_doi(self, db):
        db.insert_citation_edges([("A", "B", "", "", "snowball", 1)])     # no DOIs yet
        db.insert_citation_edges([("A", "B", "10.1/a", "10.1/b", "openalex", 1)])  # enrich
        e = db.get_citation_edges()[0]
        assert e["citing_doi"] == "10.1/a" and e["cited_doi"] == "10.1/b"

    def test_insert_snowball_edges_shim_is_canonical(self, db):
        # Legacy shim: 'citation' means child cites parent -> edge child->parent.
        db.insert_snowball_edges([("P", "C", "citation", 1)])
        db.insert_snowball_edges([("P", "R", "reference", 1)])  # parent cites R -> P->R
        edges = {(e["citing_id"], e["cited_id"]) for e in db.get_citation_edges()}
        assert edges == {("C", "P"), ("P", "R")}

    def test_build_citation_edge_orientation(self):
        from utils.db_management import build_citation_edge
        seed = make_article("S", doi="10.1/s")
        disc = make_article("D", doi="10.1/d", search_method="crossref")
        # citation: disc cites seed -> D -> S
        cit = build_citation_edge(seed, disc, "citation", 2)
        assert cit[:2] == ("D", "S") and cit[2] == "10.1/d" and cit[4] == "crossref"
        # reference: seed cites disc -> S -> D
        ref = build_citation_edge(seed, disc, "reference", 2)
        assert ref[:2] == ("S", "D")

    def test_legacy_snowball_edges_folded_in(self, db):
        # Simulate a pre-existing legacy snowball_edges table with a row.
        db.create_snowball_edges_table()
        db.cursor.execute(
            "INSERT INTO snowball_edges (parent_id, child_id, direction, iteration) VALUES (?,?,?,?)",
            ("P", "C", "citation", 1),
        )
        db.conn.commit()
        # get_citation_edges converts it (citation -> child cites parent -> C->P).
        edges = {(e["citing_id"], e["cited_id"]) for e in db.get_citation_edges()}
        assert ("C", "P") in edges

    def test_workflow_metadata(self, db):
        db.update_current_iteration(3)
        db.update_last_step("filter_by_metadata")
        assert db.get_current_iteration() == 3
        assert db.get_last_step() == "filter_by_metadata"


# --------------------------------------------------------------------------- #
# merge_databases
# --------------------------------------------------------------------------- #
class TestMerge:
    def test_merge_combines_screening_rows(self, db, make_db):
        db.insert_screening_data("1", "alice", 1, True, "ok", screening_phase="title", title="t1")
        other = make_db("other.db")
        other.insert_screening_data("1", "bob", 1, False, "no", screening_phase="title", title="t1")
        db.merge_databases(other)
        raters = {r for r in db.get_screening_raters()}
        assert raters == {"alice", "bob"}

    def test_merge_schema_mismatch_raises_valueerror(self, db, make_db):
        """Regression: on a column mismatch (current DB has a column the other
        lacks) merge_databases builds an error message with ``self.db_path`` /
        ``other_db.db_path``. Before the fix, __init__ never set db_path, so this
        raised AttributeError instead of the intended ValueError."""
        # ``db`` has annotations Scope + Techniques; ``other`` is missing Techniques,
        # so a column in the current DB is absent from the other -> mismatch path.
        other = make_db("mismatch.db", annots=["Scope"])
        with pytest.raises(ValueError):
            db.merge_databases(other)

    def test_merge_ignores_extra_columns_on_other(self, db, make_db):
        """Extra annotation columns on the *other* DB are silently ignored (common
        columns are a subset), so this is not treated as a mismatch."""
        other = make_db("extra.db", annots=["Scope", "Techniques", "ExtraColumn"])
        other.insert_screening_data("9", "zoe", 1, True, "ok", screening_phase="title", title="t")
        db.merge_databases(other)  # must not raise
        assert "zoe" in set(db.get_screening_raters())
