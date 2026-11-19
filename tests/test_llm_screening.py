"""Tests for utils/pipeline/llm_screening.py with the LLM boundary mocked."""
import os

import pytest

from conftest import make_article
from utils.db_management import SelectionStage
from utils.pipeline import llm_screening as ls


class TestScreeningResultModel:
    def test_base_fields(self):
        m = ls.BaseScreeningResult(title="T", keep=True, reason="r")
        assert m.keep is True

    def test_dynamic_annotation_fields(self):
        Model = ls.update_screening_result_class({"Scope": "the scope", "Techniques": "the techniques"})
        inst = Model(title="T", keep=False, reason="r", Scope="broad", Techniques="LLM")
        assert inst.Scope == "broad" and inst.Techniques == "LLM"


class TestProcessApiKey:
    def test_none_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        assert ls.process_api_key(None) == "env-key"

    def test_literal_key_passthrough(self):
        assert ls.process_api_key("sk-literal") == "sk-literal"

    def test_txt_file_is_read(self, tmp_path):
        p = tmp_path / "key.txt"
        p.write_text("  file-key\n")
        assert ls.process_api_key(str(p)) == "file-key"


class TestGetArticlesFromDb:
    def test_title_stage_returns_metadata_approved(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, selected=SelectionStage.METADATA_APPROVED),
            make_article("B", iteration=1, selected=SelectionStage.TITLE_APPROVED),  # already past title
            make_article("C", iteration=1, selected=SelectionStage.NOT_SELECTED),
        ])
        out = ls.get_articles_from_db(db.db_path, 1, stage="title")
        ids = {a.id for a in out}
        assert "A" in ids and "B" not in ids  # B is already title-approved -> excluded

    def test_content_stage_returns_title_approved(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, selected=SelectionStage.TITLE_APPROVED),
            make_article("B", iteration=1, selected=SelectionStage.METADATA_APPROVED),
        ])
        out = ls.get_articles_from_db(db.db_path, 1, stage="content")
        assert {a.id for a in out} == {"A"}


class TestSystemContentPromptConstant:
    def test_constant_exists(self):
        """Regression: screen_papers referenced SYSTEM_CONTENT_PROMPT_FILE but the
        module only defined SYSTEM_PROMPT_FILE, so default content screening raised
        NameError."""
        assert hasattr(ls, "SYSTEM_CONTENT_PROMPT_FILE")
        assert ls.SYSTEM_CONTENT_PROMPT_FILE.endswith("system_content_screening.txt")


class TestScreenPapersWithFakeLLM:
    def test_title_screening_writes_results(self, db, tmp_path, monkeypatch):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Paper A", selected=SelectionStage.METADATA_APPROVED),
        ])
        # Explicit prompt files so we don't depend on the prompts/ folder.
        sys_prompt = tmp_path / "sys.txt"; sys_prompt.write_text("You are a screener.")
        user_prompt = tmp_path / "user.txt"; user_prompt.write_text("Title: {title}\nTopic: {topic}\n{content}")

        captured = {}

        def fake_ask_model(system_prompt, user_prompt, model, api_key, temperature, annotations=None):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return {"title": "Paper A", "keep": True, "reason": "clearly relevant"}

        monkeypatch.setattr(ls, "ask_model", fake_ask_model)

        results = ls.screen_papers(
            rater_id="llm", topic="code generation", db_path=db.db_path, iteration=1,
            stage="title", api_key="dummy",
            system_prompt_file=str(sys_prompt), user_prompt_file=str(user_prompt),
        )
        assert len(results) == 1
        # The LLM saw the formatted prompt
        assert "Paper A" in captured["user"] and "code generation" in captured["user"]
        # ...and the decision was written to the screening table
        rows = db.get_screening_rows_by_rater("llm", 1)
        assert len(rows) == 1 and rows[0]["keep_title"] in (1, True)
        assert rows[0]["reason_title"] == "clearly relevant"
