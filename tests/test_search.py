"""Search, filter, limit, and query-injection tests."""

from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bansos import models, search  # noqa: E402
from _support import BansosTestCase, sample_resource  # noqa: E402


class SearchTests(BansosTestCase):
    def setUp(self):
        super().setUp()
        self.now = models.utc_now()
        self.static = self.database.add_resource(
            sample_resource(
                name="Example Static Pages",
                provider="Älpha Host, Inc.",
                category="web-hosting",
                description="Static site hosting with a custom domain.",
                offer_type="free_tier",
                eligibility_requirements="Students and educators",
                payment_method_required="no",
                country_availability="Worldwide",
                source_url="https://example.com/static",
                notes="Manual evidence pending; 100% fictional test data.",
            )
        )
        self.api = self.database.add_resource(
            sample_resource(
                name="Example Model API",
                provider="Beta Labs",
                category="ai-apis",
                description="API credit for language models.",
                offer_type="promotional_credit",
                eligibility_requirements="New accounts",
                payment_method_required="yes",
                country_availability="United States",
                source_url="https://example.com/api",
                notes="Promotional credits expire quickly.",
            )
        )
        self.postgres = self.database.add_resource(
            sample_resource(
                name="Example Managed Postgres",
                provider="Gamma Data",
                category="databases-storage",
                description="Managed PostgreSQL starter database.",
                offer_type="free_tier",
                eligibility_requirements="Open-source maintainers",
                payment_method_required="unknown",
                country_availability="Europe",
                source_url="https://example.com/postgres",
                notes="Open-source projects may apply.",
            )
        )
        self.database.record_verification(
            self.static.id,
            "verified",
            "Test evidence for the static hosting record.",
            checked_at=self.now - timedelta(days=5),
            now=self.now,
        )

    def _ids(self, **query_kwargs):
        return [item.id for item in self.database.query_resources(search.ResourceQuery(**query_kwargs))]

    def test_search_across_name_provider_description_and_notes(self):
        self.assertEqual(self._ids(text="static"), [self.static.id])
        self.assertEqual(self._ids(text="ÄLPHA"), [self.static.id])
        self.assertEqual(self._ids(text="BETA LABS"), [self.api.id])
        self.assertEqual(self._ids(text="postgresql"), [self.postgres.id])
        self.assertEqual(self._ids(text="expire quickly"), [self.api.id])

    def test_multi_term_search_requires_all_terms(self):
        self.assertEqual(self._ids(text="static custom"), [self.static.id])
        self.assertEqual(self._ids(text="api credit"), [self.api.id])
        self.assertEqual(self._ids(text="static postgresql"), [])
        self.assertEqual(self._ids(text="example model"), [self.api.id])

    def test_empty_and_no_result_searches(self):
        self.assertEqual(len(self._ids(text="")), 3)
        self.assertEqual(len(self._ids()), 3)
        self.assertEqual(self._ids(text="nonexistent-term"), [])

    def test_special_characters_are_literal_not_wildcards(self):
        self.assertEqual(self._ids(text="%"), [self.static.id])
        self.assertEqual(self._ids(text="_"), [])
        self.assertEqual(self._ids(text="'"), [])
        self.assertEqual(self._ids(text="static; DROP TABLE resources; --"), [])

    def test_query_validation(self):
        with self.assertRaises(models.ValidationError):
            self._ids(text="x" * 201)
        with self.assertRaises(models.ValidationError):
            self._ids(text=" ".join(f"term{i}" for i in range(11)))
        with self.assertRaises(models.ValidationError):
            self._ids(text="bad\nquery")
        with self.assertRaises(models.ValidationError):
            self._ids(limit=-1)
        with self.assertRaises(models.ValidationError):
            self._ids(limit=True)
        with self.assertRaises(models.ValidationError):
            self._ids(limit="5")

    def test_limits_and_deterministic_sorting(self):
        all_results = self.database.query_resources()
        self.assertEqual(
            [item.name for item in all_results],
            sorted(item.name for item in all_results),
        )
        self.assertEqual(len(self._ids(limit=2)), 2)
        self.assertEqual(len(self._ids(limit=0)), 3)
        self.assertEqual(
            self._ids(limit=0, text="example"),
            [self.postgres.id, self.api.id, self.static.id],
        )

    def test_structured_filters(self):
        self.assertEqual(self._ids(category="web-hosting"), [self.static.id])
        self.assertEqual(self._ids(offer_type="promotional_credit"), [self.api.id])
        self.assertEqual(self._ids(eligibility="educators"), [self.static.id])
        self.assertEqual(self._ids(verification_status="verified"), [self.static.id])
        self.assertEqual(
            set(self._ids(verification_status="unverified")),
            {self.api.id, self.postgres.id},
        )
        self.assertEqual(self._ids(payment_method_required="yes"), [self.api.id])
        self.assertEqual(self._ids(country="europe"), [self.postgres.id])
        self.assertEqual(
            self._ids(category="web-hosting", payment_method_required="no"),
            [self.static.id],
        )
        self.assertEqual(
            self._ids(category="ai-apis", payment_method_required="no"), []
        )

    def test_filter_combinations_with_text(self):
        self.assertEqual(
            self._ids(text="example", category="web-hosting"),
            [self.static.id],
        )
        self.assertEqual(
            self._ids(text="credit", category="web-hosting"),
            [],
        )

    def test_has_filters_ignores_blank_text_filters(self):
        self.assertTrue(search.has_filters(search.ResourceQuery(category="web-hosting")))
        self.assertTrue(search.has_filters(search.ResourceQuery(text="api")))
        self.assertFalse(search.has_filters(search.ResourceQuery(text="  ")))
        self.assertFalse(search.has_filters(search.ResourceQuery(eligibility="")))
        self.assertFalse(search.has_filters(search.ResourceQuery(limit=5)))

    def test_sql_injection_attempt_does_not_change_schema(self):
        results = self.database.query_resources(
            search.ResourceQuery(text="'; DROP TABLE resources; --")
        )
        self.assertEqual(results, [])
        self.assertEqual(len(self.database.list_all()), 3)
        results = self.database.query_resources(
            search.ResourceQuery(eligibility="' OR 1=1 --")
        )
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
