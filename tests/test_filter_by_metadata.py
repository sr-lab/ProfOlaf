"""Tests for utils/pipeline/filter_by_metadata_utils.py (non-interactive paths)."""
import pytest

from conftest import make_article
from utils.db_management import SelectionStage
from utils.pipeline import filter_by_metadata_utils as fm

RANKED_BIBTEX = "@inproceedings{k, title={T}, booktitle={ICSE}, year={2022}}"
UNRANKED_BIBTEX = "@inproceedings{k, title={T}, booktitle={RandomWorkshop}, year={2022}}"


class TestPreprintDetection:
    @pytest.mark.parametrize("venue", ["arXiv", "CoRR", "SSRN preprint", "bioRxiv"])
    def test_preprint_venues(self, venue):
        is_pre, reason = fm.is_preprint_publication(venue=venue)
        assert is_pre is True and reason

    @pytest.mark.parametrize("url", [
        "https://arxiv.org/abs/2401.00001",
        "https://openreview.net/forum?id=abc",
        "https://ssrn.com/abstract=123",
    ])
    def test_preprint_urls(self, url):
        is_pre, _ = fm.is_preprint_publication(eprint_url=url)
        assert is_pre is True

    def test_arxiv_bibtex(self):
        bib = "@article{x, title={T}, archivePrefix={arXiv}, eprint={2401.00001}}"
        assert fm.is_preprint_publication(bibtex=bib)[0] is True

    def test_normal_conference_not_preprint(self):
        is_pre, reason = fm.is_preprint_publication(
            venue="International Conference on Software Engineering",
            bibtex=RANKED_BIBTEX,
            eprint_url="https://doi.org/10.1145/xxxx",
        )
        assert is_pre is False and reason is None


class TestYearValidity:
    # search_conf.json has start_year=2020, end_year=2024
    def test_in_range(self):
        assert fm.is_year_valid("2022") is True
        assert fm.is_year_valid(2022) is True  # robust to int input

    def test_out_of_range(self):
        assert fm.is_year_valid("2019") is False
        assert fm.is_year_valid("2026") is False

    def test_boundaries_inclusive(self):
        assert fm.is_year_valid("2020") is True
        assert fm.is_year_valid("2024") is True


class TestLanguageDetection:
    def test_english_kept(self):
        assert fm.is_english_text("A Large Scale Empirical Study of Automated Software Testing") is True

    def test_confident_non_english_rejected(self):
        # A clearly Portuguese sentence, long enough to detect confidently.
        pt = "Este artigo apresenta um estudo empírico sobre engenharia de software e testes automatizados em sistemas distribuídos de larga escala"
        assert fm.is_english_text(pt) is False

    def test_empty_is_conservatively_kept(self):
        """Never discard just because there's nothing / too little to detect."""
        assert fm.is_english_text("") is True
        assert fm.is_english_text("   ") is True

    def test_undetectable_is_kept(self):
        # Symbols/numbers raise LangDetectException internally -> keep.
        assert fm.is_english_text("123 :: ### 42") is True

    def test_deterministic(self):
        title = "Neural Architecture Search for Efficient Transformers"
        assert fm.is_english_text(title) == fm.is_english_text(title)


class TestNonInteractiveMode:
    def test_unknown_year_assume_keep(self):
        # year 0 (unknown) normally prompts; non-interactive assume_keep -> True
        assert fm.is_year_valid("0", interactive=False, assume_keep=True) is True

    def test_unknown_year_assume_discard(self):
        assert fm.is_year_valid("0", interactive=False, assume_keep=False) is False

    def test_no_download_url_assume_keep(self):
        assert fm.is_downloadable("", interactive=False, assume_keep=True) is True
        assert fm.is_downloadable("", interactive=False, assume_keep=False) is False

    def test_filter_elements_non_interactive_discards_unknowns(self, db):
        # Article with a ranked venue but an UNKNOWN year -> year check is undecidable.
        db.insert_conf_rank_data([("ICSE", "A*")])
        db.insert_iteration_data([make_article(
            "U", iteration=1,
            title="A Study With An Unknown Publication Year In Its Metadata",
            venue="International Conference on Software Engineering",
            bibtex="@inproceedings{k, title={T}, booktitle={ICSE}}",  # no year field
            pub_year=0, eprint_url="https://doi.org/10.1/x",
        )])
        # Non-interactive + assume discard -> the unknown-year article is filtered out,
        # with no prompt (would hang if it tried to read stdin).
        fm.filter_elements(db, 1, False, False, False, False,
                           reject_preprints=True, interactive=False, assume_keep=False)
        row = db.get_iteration_data(iteration=1, id="U")[0]
        assert str(row.year_filtered_out) in ("1", "True")
        assert str(row.selected) == str(SelectionStage.NOT_SELECTED.value)


class TestVenueCheck:
    def test_empty_bibtex_returns_false(self, db):
        assert fm.automated_check_venue_and_peer_reviewed("", db) is False

    def test_ranked_venue_true(self, db):
        db.insert_conf_rank_data([("ICSE", "A*")])
        assert fm.automated_check_venue_and_peer_reviewed(RANKED_BIBTEX, db) is True

    def test_unranked_venue_false(self, db):
        db.insert_conf_rank_data([("RandomWorkshop", "Z")])
        assert fm.automated_check_venue_and_peer_reviewed(UNRANKED_BIBTEX, db) is False

    def test_unknown_venue_returns_none(self, db):
        # Not present in conf_rank -> caller must fall back to asking the user.
        assert fm.automated_check_venue_and_peer_reviewed(RANKED_BIBTEX, db) is None


class TestFilterElements:
    def test_rejects_preprint(self, db):
        db.insert_iteration_data([make_article(
            "P", iteration=1, title="A Transformer Approach to Program Repair",
            venue="arXiv", bibtex="@misc{x, title={T}}",
            eprint_url="https://arxiv.org/abs/2401.0001",
        )])
        fm.filter_elements(db, 1, False, False, False, False, reject_preprints=True)
        row = db.get_iteration_data(iteration=1, id="P")[0]
        assert str(row.venue_filtered_out) in ("1", "True")
        assert str(row.selected) == str(SelectionStage.NOT_SELECTED.value)

    def test_approves_valid_article(self, db):
        db.insert_conf_rank_data([("ICSE", "A*")])
        db.insert_iteration_data([make_article(
            "V", iteration=1,
            title="A Large Scale Empirical Study of Software Testing Practices",
            venue="International Conference on Software Engineering",
            bibtex=RANKED_BIBTEX, pub_year=2022,
            eprint_url="https://doi.org/10.1145/abcd",
        )])
        fm.filter_elements(db, 1, False, False, False, False, reject_preprints=True)
        row = db.get_iteration_data(iteration=1, id="V")[0]
        assert str(row.selected) == str(SelectionStage.METADATA_APPROVED.value)
