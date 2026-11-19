"""Tests for the non-interactive logic in utils/pipeline/solve_disagreements.py:
the keep-value normalizer, annotation merging, consensus auto-settling, and the
single-rater fast path. The interactive TUI/`input()` paths are not exercised.
"""
import pytest

from conftest import make_article
from utils.db_management import SelectionStage
from utils.pipeline import solve_disagreements as sd

TITLE = SelectionStage.TITLE_APPROVED  # .value == 2


class TestKeepValue:
    @pytest.mark.parametrize("v", [1, True, "1"])
    def test_truthy(self, v):
        assert sd._keep_value(v) is True

    @pytest.mark.parametrize("v", [0, False, "0", "", None])
    def test_falsy(self, v):
        assert sd._keep_value(v) is False


class TestGatherAnnotationsDefault:
    def test_merges_unique_preserving_order(self):
        rows = [
            {"Techniques": "LLM, RAG"},
            {"Techniques": "RAG; Fine-tuning"},
            {"Techniques": ""},
        ]
        out = sd._gather_annotations_default(rows, ["Techniques"])
        assert out["Techniques"] == "LLM, RAG, Fine-tuning"

    def test_missing_field_is_empty(self):
        out = sd._gather_annotations_default([{"Other": "x"}], ["Techniques"])
        assert out["Techniques"] == ""


class TestSettleAgreements:
    def _article(self, db, aid):
        db.insert_iteration_data([make_article(aid, iteration=1, selected=SelectionStage.METADATA_APPROVED)])

    def _title_vote(self, db, aid, rater, keep):
        db.insert_screening_data(aid, rater, 1, keep, "reason", screening_phase="title", title=f"t-{aid}")

    def test_unanimous_keep_is_settled_and_accepted(self, db):
        self._article(db, "X")
        self._title_vote(db, "X", "alice", True)
        self._title_vote(db, "X", "bob", True)
        sd.settle_agreements(1, db, TITLE, raters=["alice", "bob"])
        assert db.get_keep_title("X", 1) is True
        row = db.get_iteration_data(iteration=1, id="X")[0]
        assert str(row.selected) == str(TITLE.value)
        # screening rows now marked title_settled
        settled = db.get_screening_data(1, title_settled=True, content_settled=False)
        assert {r["id"] for r in settled} == {"X"}

    def test_unanimous_reject_drops_back_to_previous_stage(self, db):
        self._article(db, "Y")
        self._title_vote(db, "Y", "alice", False)
        self._title_vote(db, "Y", "bob", False)
        sd.settle_agreements(1, db, TITLE, raters=["alice", "bob"])
        assert db.get_keep_title("Y", 1) is False
        row = db.get_iteration_data(iteration=1, id="Y")[0]
        # reject at title stage -> stays at the previous stage (METADATA_APPROVED)
        assert str(row.selected) == str(TITLE.value - 1)

    def test_genuine_disagreement_is_not_settled(self, db):
        self._article(db, "W")
        self._title_vote(db, "W", "alice", True)
        self._title_vote(db, "W", "bob", False)
        sd.settle_agreements(1, db, TITLE, raters=["alice", "bob"])
        # Still unsettled; iterations stage untouched (stays at METADATA_APPROVED).
        # Note: get_keep_title can't distinguish "undecided" from "reject" (both map
        # to False), so we assert on the screening settled-state and the stage instead.
        unsettled = db.get_screening_data(1, title_settled=False, content_settled=False)
        assert "W" in {r["id"] for r in unsettled}
        row = db.get_iteration_data(iteration=1, id="W")[0]
        assert str(row.selected) == str(SelectionStage.METADATA_APPROVED.value)


class TestSingleRaterFastPath:
    def test_single_rater_auto_settles_without_interaction(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, selected=SelectionStage.METADATA_APPROVED),
            make_article("B", iteration=1, selected=SelectionStage.METADATA_APPROVED),
        ])
        db.insert_screening_data("A", "solo", 1, True, "keep", screening_phase="title", title="t-A")
        db.insert_screening_data("B", "solo", 1, False, "drop", screening_phase="title", title="t-B")
        # Only one rater -> no disagreements -> no input() required.
        sd.solve_disagreements(1, db, TITLE)
        assert db.get_keep_title("A", 1) is True
        assert db.get_keep_title("B", 1) is False
        assert str(db.get_iteration_data(iteration=1, id="A")[0].selected) == str(TITLE.value)
        assert str(db.get_iteration_data(iteration=1, id="B")[0].selected) == str(TITLE.value - 1)
