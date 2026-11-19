"""Tests for the non-interactive logic in utils/pipeline/screening.py.

The prompt_toolkit/`input()` paths (process_article, choose_elements, the index
TUIs) are not exercised; the pure helpers and the DB-writing functions are.
"""
from conftest import make_article
from utils.db_management import ArticleData, SelectionStage
from utils.pipeline import screening as sc

TITLE = SelectionStage.TITLE_APPROVED
CONTENT = SelectionStage.CONTENT_APPROVED


class TestStageHelpers:
    def test_correct_stage_for_unscreened_metadata_article(self):
        # selected comes back from the DB as a string; keep_title "" = undecided
        art = ArticleData(id="1", selected="1", keep_title="")
        assert sc.is_correct_article_stage(art, TITLE) is True

    def test_already_kept_is_not_correct_stage(self):
        art = ArticleData(id="1", selected="1", keep_title=1)
        assert sc.is_correct_article_stage(art, TITLE) is False

    def test_wrong_stage(self):
        # An article still at NOT_SELECTED (0) is not ready for TITLE screening
        art = ArticleData(id="1", selected="0", keep_title="")
        assert sc.is_correct_article_stage(art, TITLE) is False

    def test_is_annotations_to_fill(self):
        assert sc.is_annotations_to_fill(["Scope"], CONTENT) is True
        assert sc.is_annotations_to_fill([], CONTENT) is False
        assert sc.is_annotations_to_fill(["Scope"], TITLE) is False


class TestPreviousData:
    def test_previous_data_from_row_title(self):
        row = {"keep_title": 1, "reason_title": "relevant", "keep_content": None}
        out = sc._previous_data_from_row(row, TITLE, [])
        assert out == {"keep": True, "reason": "relevant"}

    def test_previous_data_from_row_includes_annotations(self):
        row = {"keep_content": 0, "reason_content": "off-topic", "Scope": "narrow"}
        out = sc._previous_data_from_row(row, CONTENT, ["Scope"])
        assert out["keep"] is False and out["reason"] == "off-topic" and out["Scope"] == "narrow"

    def test_previously_screened_from_db(self):
        existing = [{"id": "7"}]
        assert sc._previously_screened_article(make_article("7"), existing, None) is True
        assert sc._previously_screened_article(make_article("8"), existing, None) is False

    def test_previously_screened_from_current_run(self):
        assert sc._previously_screened_article(make_article("9"), [], {"9": {"keep": True}}) is True


class TestDecisionForArticle:
    def test_from_current_run(self):
        assert sc._decision_for_article("1", [], {"1": {"keep": True}}, "title") == "y"
        assert sc._decision_for_article("1", [], {"1": {"keep": False}}, "title") == "n"
        assert sc._decision_for_article("1", [], {"1": {"keep": None}}, "title") == "-"

    def test_from_existing_db_rows(self):
        existing = [{"id": "1", "keep_title": 1}, {"id": "2", "keep_title": 0}, {"id": "3", "keep_title": None}]
        assert sc._decision_for_article("1", existing, None, "title") == "y"
        assert sc._decision_for_article("2", existing, None, "title") == "n"
        assert sc._decision_for_article("3", existing, None, "title") == "-"
        assert sc._decision_for_article("missing", existing, None, "title") == "-"


class TestApplyAndUndo:
    def test_apply_keep_inserts_row(self, db):
        art = make_article("1", title="Paper One")
        sc.apply_decision(db, art, 1, "alice", "y", "good", screening_phase="title")
        rows = db.get_screening_rows_by_rater("alice", 1)
        assert len(rows) == 1 and rows[0]["keep_title"] in (1, True)
        assert rows[0]["reason_title"] == "good"

    def test_apply_skip_is_noop(self, db):
        art = make_article("1")
        sc.apply_decision(db, art, 1, "alice", "s", "", screening_phase="title")
        assert db.get_screening_rows_by_rater("alice", 1) == []

    def test_undo_decision_clears_rater_row(self, db):
        """Regression: undo_decision never forwarded `rater` to update_screening_data
        (which requires it), so it raised TypeError on any call."""
        art = make_article("1", title="Paper One")
        sc.apply_decision(db, art, 1, "alice", "y", "good", screening_phase="title")
        sc.undo_decision(db, art, 1, "alice", screening_phase="title")
        rows = db.get_screening_rows_by_rater("alice", 1)
        assert len(rows) == 1
        assert rows[0]["keep_title"] in (0, False)
        assert (rows[0]["reason_title"] or "") == ""
