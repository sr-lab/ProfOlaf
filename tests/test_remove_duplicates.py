"""Tests for utils/pipeline/remove_duplicates.py."""
from conftest import make_article
from utils.db_management import SelectionStage
from utils.pipeline import remove_duplicates as rd


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert rd.calculate_title_similarity("Deep Learning", "Deep Learning") == 1.0

    def test_case_and_whitespace_insensitive(self):
        assert rd.calculate_title_similarity("Deep Learning", "  deep learning ") == 1.0

    def test_dissimilar_titles(self):
        assert rd.calculate_title_similarity("Cats", "Quantum Chromodynamics") < 0.3


class TestFindDuplicateClusters:
    def test_detects_near_duplicate(self):
        arts = [
            make_article("A", title="A Survey of Code LLMs"),
            make_article("B", title="A Survey of Code LLMs."),  # trailing dot
        ]
        clusters = rd.find_duplicate_clusters(arts, 0.9)
        assert len(clusters) == 1
        assert {a.id for a in clusters[0]} == {"A", "B"}

    def test_ignores_dissimilar(self):
        arts = [
            make_article("A", title="A Survey of Code LLMs"),
            make_article("B", title="Formal Verification of Compilers"),
        ]
        assert rd.find_duplicate_clusters(arts, 0.9) == []

    def test_doi_match_beats_different_titles(self):
        """Same work under different titles (preprint vs conference) is caught by DOI."""
        arts = [
            make_article("A", title="Learning to Repair Programs", doi="10.1/XYZ"),
            make_article("B", title="Neural Program Repair via Learning", doi="https://doi.org/10.1/xyz"),
        ]
        clusters = rd.find_duplicate_clusters(arts, 0.95)
        assert len(clusters) == 1 and {a.id for a in clusters[0]} == {"A", "B"}

    def test_transitive_three_way_cluster(self):
        arts = [
            make_article("A", title="Deep Learning for Code Search"),
            make_article("B", title="Deep Learning for Code Search."),
            make_article("C", title="Deep Learning for Code Search!"),
        ]
        clusters = rd.find_duplicate_clusters(arts, 0.9)
        assert len(clusters) == 1 and {a.id for a in clusters[0]} == {"A", "B", "C"}


class TestAutoRemove:
    def _setup_pair(self, db, cit_a, cit_b):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Neural Code Search", num_citations=cit_a),
            make_article("B", iteration=1, title="Neural Code Search", num_citations=cit_b),
        ])

    def _duplicate_ids(self, db):
        rows = db.get_iteration_data(iteration=1)
        return {r.id for r in rows if str(r.selected) == str(SelectionStage.DUPLICATE.value)}

    def test_keeps_higher_citation_count(self, db):
        """Regression: num_citations is stored as TEXT and read back as a string,
        so the previous `>` comparison was lexicographic ("100" < "9"). The article
        with more citations (100) must be kept, and the 9-citation one removed."""
        self._setup_pair(db, 100, 9)
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        assert self._duplicate_ids(db) == {"B"}

    def test_keeps_higher_citation_count_reversed(self, db):
        self._setup_pair(db, 9, 100)
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        assert self._duplicate_ids(db) == {"A"}

    def test_tie_breaks_on_year(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Static Analysis", num_citations=5, pub_year=2023),
            make_article("B", iteration=1, title="Static Analysis", num_citations=5, pub_year=2019),
        ])
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        # equal citations -> keep the newer (2023); remove the older (2019)
        assert self._duplicate_ids(db) == {"B"}

    def test_no_duplicates_leaves_db_untouched(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Quantum Error Correction"),
            make_article("B", iteration=1, title="Build Systems at Scale"),
        ])
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        assert self._duplicate_ids(db) == set()

    def test_three_way_cluster_keeps_only_best(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Program Synthesis", num_citations=10),
            make_article("B", iteration=1, title="Program Synthesis.", num_citations=50),
            make_article("C", iteration=1, title="Program Synthesis!", num_citations=5),
        ])
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        # One pass removes both lower-cited copies, keeps the 50-citation one.
        assert self._duplicate_ids(db) == {"A", "C"}

    def test_doi_duplicate_auto_removed(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Preprint Title", doi="10.1/same", num_citations=3),
            make_article("B", iteration=1, title="Totally Different Conference Title",
                         doi="10.1/same", num_citations=40),
        ])
        rd.remove_duplicates(db, [1], similarity_threshold=0.95, auto_remove=True)
        assert self._duplicate_ids(db) == {"A"}  # keep the 40-citation version
