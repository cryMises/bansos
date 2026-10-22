"""Database, validation, verification, import, and export tests."""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bansos import db, models, verify  # noqa: E402
from _support import BansosTestCase, sample_resource  # noqa: E402


class SchemaTests(BansosTestCase):
    def test_initialize_creates_schema_and_is_idempotent(self):
        self.assertTrue(self.db_path.is_file())
        self.assertTrue(self.initialize_result.created)
        self.assertEqual(self.initialize_result.applied_versions, (1,))

        second = self.database.initialize()
        self.assertFalse(second.created)
        self.assertEqual(second.applied_versions, ())
        self.assertEqual(second.schema_version, 1)

        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("resources", tables)
            self.assertIn("verification_events", tables)
            self.assertIn("schema_migrations", tables)
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            self.assertEqual(version, 1)
            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            self.assertIn("ux_resources_provider_name", indexes)
            self.assertIn("ux_resources_source_url", indexes)

    def test_schema_rejects_verified_without_evidence(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            now = models.to_iso_z(models.utc_now())
            row = models.validate_new_resource(sample_resource())
            row["verification_status"] = "verified"
            row["last_verified_at"] = now
            row["verification_evidence"] = ""
            columns = ", ".join(row)
            placeholders = ", ".join("?" for _ in row)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    f"INSERT INTO resources ({columns}) VALUES ({placeholders})",
                    list(row.values()),
                )

    def test_uninitialized_database_is_rejected(self):
        missing_path = Path(self.tempdir.name) / "missing.db"
        missing = db.Database(missing_path)
        with self.assertRaises(models.DatabaseNotInitializedError):
            missing.list_all()

    def test_operations_on_plain_file_are_rejected(self):
        plain = Path(self.tempdir.name) / "plain.db"
        plain.write_text("not a database", encoding="utf-8")
        database = db.Database(plain)
        with self.assertRaises(models.BansosError):
            database.initialize()


class CrudTests(BansosTestCase):
    def test_add_and_get_roundtrip(self):
        resource = self.database.add_resource(
            sample_resource(
                source_url="https://Example.COM/compute/?plan=free#fragment",
                official_terms_url="https://example.com/terms",
            )
        )
        self.assertEqual(resource.id, 1)
        self.assertEqual(resource.name, "Example Compute")
        self.assertEqual(resource.verification_status, "unverified")
        self.assertIsNone(resource.last_verified_at)
        self.assertEqual(resource.verification_evidence, "")
        self.assertEqual(resource.normalized_provider, "example provider")
        self.assertEqual(
            resource.normalized_source_url,
            "https://example.com/compute?plan=free",
        )
        self.assertTrue(resource.first_seen_at.endswith("Z"))

        fetched = self.database.get_resource(1)
        self.assertEqual(fetched, resource)
        public = fetched.to_public_dict()
        self.assertNotIn("normalized_name", public)
        self.assertEqual(public["source_url"], resource.source_url)

    def test_required_field_and_malformed_input_validation(self):
        cases = [
            {"name": ""},
            {"provider": ""},
            {"source_url": ""},
            {"source_url": "ftp://example.com/offer"},
            {"source_url": "https://example.com/offer?x=1", "category": "nonsense"},
            {"offer_type": "maybe_free"},
            {"payment_method_required": "sometimes"},
            {"source_url": "https://user:secret@example.com/offer"},
            {"first_seen_at": "not-a-date"},
            {"name": "bad\nname"},
            {"description": "bad\x1bescape"},
            {"notes": None},
            {"unknown_field": "x"},
            {"name": "x" * 201},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(models.ValidationError):
                    self.database.add_resource(sample_resource(**overrides))
        self.assertEqual(self.database.list_all(), [])

    def test_duplicate_detection_uses_normalized_identity(self):
        first = self.database.add_resource(sample_resource())

        with self.assertRaises(models.DuplicateError) as caught:
            self.database.add_resource(
                sample_resource(
                    name="Completely Different Offer",
                    provider="Different Provider",
                    source_url="https://EXAMPLE.com/compute/#other",
                )
            )
        self.assertEqual(caught.exception.existing_ids, [first.id])

        with self.assertRaises(models.DuplicateError):
            self.database.add_resource(
                sample_resource(
                    name="EXAMPLE   COMPUTE",
                    provider="Example Provider LLC",
                    source_url="https://example.com/other-offer",
                )
            )

        second = self.database.add_resource(
            sample_resource(
                name="Another Example Service",
                provider="Another Provider",
                source_url="https://example.org/another",
            )
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(self.database.list_all()), 2)

    def test_update_and_duplicate_protection(self):
        first = self.database.add_resource(sample_resource())
        updated = self.database.update_resource(
            first.id,
            {"name": "Updated Compute", "description": "Updated description."},
        )
        self.assertEqual(updated.id, first.id)
        self.assertEqual(updated.name, "Updated Compute")
        self.assertEqual(updated.normalized_name, "updated compute")
        self.assertEqual(updated.description, "Updated description.")

        second = self.database.add_resource(
            sample_resource(
                name="Second Service",
                provider="Second Provider",
                source_url="https://example.net/second",
            )
        )
        with self.assertRaises(models.DuplicateError):
            self.database.update_resource(
                first.id, {"source_url": second.source_url}
            )
        unchanged = self.database.get_resource(first.id)
        self.assertEqual(unchanged.source_url, "https://example.com/compute")

        with self.assertRaises(models.ValidationError):
            self.database.update_resource(first.id, {"name": ""})
        with self.assertRaises(models.ValidationError):
            self.database.update_resource(first.id, {"verification_status": "verified"})
        self.assertEqual(self.database.update_resource(first.id, {}), unchanged)

    def test_delete_is_cascading_and_requires_an_existing_record(self):
        resource = self.database.add_resource(sample_resource())
        self.database.record_verification(
            resource.id,
            "verified",
            "Manual evidence checked for this test.",
            checked_at=models.utc_now() - timedelta(days=1),
        )
        with sqlite3.connect(self.db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM verification_events"
            ).fetchone()[0]
            self.assertEqual(count, 1)

        deleted = self.database.delete_resource(resource.id)
        self.assertEqual(deleted.id, resource.id)
        with self.assertRaises(models.NotFoundError):
            self.database.get_resource(resource.id)
        with self.assertRaises(models.NotFoundError):
            self.database.delete_resource(resource.id)
        with sqlite3.connect(self.db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM verification_events"
            ).fetchone()[0]
            self.assertEqual(count, 0)

    def test_sql_injection_text_is_stored_and_queried_safely(self):
        malicious = "Robert'); DROP TABLE resources; --"
        resource = self.database.add_resource(
            sample_resource(
                name=malicious,
                provider="Injection Provider; --",
                description="Description with ' OR 1=1; --",
                notes="note with DROP TABLE verification_events; --",
                source_url="https://example.com/injection?q=%27%3B--",
            )
        )
        from bansos import search

        results = self.database.query_resources(
            search.ResourceQuery(text="'; DROP TABLE resources; --")
        )
        self.assertEqual(results, [])
        results = self.database.query_resources(search.ResourceQuery(text="DROP TABLE"))
        self.assertEqual([item.id for item in results], [resource.id])
        self.database.record_verification(
            resource.id,
            "verified",
            "Evidence ' OR 1=1; --",
            checked_at=models.utc_now() - timedelta(days=1),
        )
        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("resources", tables)
            self.assertIn("verification_events", tables)
        self.assertEqual(len(self.database.list_all()), 1)

    def test_expired_and_first_seen_timestamps_are_normalized(self):
        now = models.utc_now()
        expiration = (now - timedelta(days=1)).isoformat()
        first_seen = (now - timedelta(days=30)).date().isoformat()
        resource = self.database.add_resource(
            sample_resource(expiration_at=expiration, first_seen_at=first_seen)
        )
        self.assertTrue(resource.first_seen_at.startswith((now - timedelta(days=30)).date().isoformat()))
        self.assertTrue(resource.expiration_at.endswith("Z"))
        state = verify.effective_state(resource, now=now)
        self.assertEqual(state.code, "expired")
        self.assertTrue(state.needs_review)


class VerificationTests(BansosTestCase):
    def setUp(self):
        super().setUp()
        self.now = models.utc_now()
        self.resource = self.database.add_resource(sample_resource())

    def test_evidence_is_mandatory_and_dates_cannot_be_future(self):
        with self.assertRaises(models.ValidationError):
            self.database.record_verification(
                self.resource.id, "verified", "   ", checked_at=self.now
            )
        with self.assertRaises(models.ValidationError):
            self.database.record_verification(
                self.resource.id,
                "verified",
                "Manual evidence.",
                checked_at=self.now + timedelta(days=1),
                now=self.now,
            )
        with self.assertRaises(models.ValidationError):
            self.database.record_verification(
                self.resource.id, "unverified", "Evidence", checked_at=self.now
            )
        self.assertEqual(self.database.verification_events(self.resource.id), [])

    def test_verification_updates_state_and_records_history(self):
        checked = self.now - timedelta(days=4)
        resource, event = self.database.record_verification(
            self.resource.id,
            "verified",
            "Manual check of the official terms page.",
            checked_at=checked,
            now=self.now,
        )
        self.assertEqual(resource.verification_status, "verified")
        self.assertEqual(resource.last_verified_at, models.to_iso_z(checked))
        self.assertEqual(
            resource.verification_evidence,
            "Manual check of the official terms page.",
        )
        self.assertEqual(event.status, "verified")
        self.assertEqual(event.resource_id, self.resource.id)
        state = verify.effective_state(resource, now=self.now)
        self.assertEqual(state.code, "verified")
        self.assertFalse(state.needs_review)

        later = self.now - timedelta(days=1)
        resource2, event2 = self.database.record_verification(
            self.resource.id,
            "disputed",
            "A second manual check found conflicting terms.",
            checked_at=later,
            now=self.now,
        )
        self.assertEqual(resource2.verification_status, "disputed")
        self.assertEqual(event2.checked_at, models.to_iso_z(later))
        self.assertEqual(len(self.database.verification_events(self.resource.id)), 2)
        self.assertEqual(verify.effective_state(resource2, now=self.now).code, "disputed")

        with self.assertRaises(models.ValidationError):
            self.database.record_verification(
                self.resource.id,
                "verified",
                "An older check must not replace the latest one.",
                checked_at=checked,
                now=self.now,
            )

    def test_stale_report_and_expiration(self):
        old = self.database.add_resource(
            sample_resource(
                name="Old Check",
                provider="Provider A",
                source_url="https://example.com/old",
            )
        )
        fresh = self.database.add_resource(
            sample_resource(
                name="Fresh Check",
                provider="Provider B",
                source_url="https://example.com/fresh",
            )
        )
        unverified = self.database.add_resource(
            sample_resource(
                name="Never Checked",
                provider="Provider C",
                source_url="https://example.com/never",
            )
        )
        expired = self.database.add_resource(
            sample_resource(
                name="Expired Offer",
                provider="Provider D",
                source_url="https://example.com/expired",
                expiration_at=(self.now - timedelta(days=1)).isoformat(),
            )
        )

        old, _ = self.database.record_verification(
            old.id,
            "verified",
            "Old evidence.",
            checked_at=self.now - timedelta(days=200),
            now=self.now,
        )
        self.database.record_verification(
            fresh.id,
            "verified",
            "Fresh evidence.",
            checked_at=self.now - timedelta(days=3),
            now=self.now,
        )

        stale_ids = {
            item.id
            for item in self.database.list_stale(days=90, now=self.now)
        }
        self.assertEqual(
            stale_ids,
            {self.resource.id, old.id, unverified.id, expired.id},
        )
        self.assertNotIn(fresh.id, stale_ids)

        old_state = verify.effective_state(old, now=self.now, stale_days=90)
        self.assertEqual(old_state.code, "stale")
        self.assertTrue(old_state.needs_review)
        expired_state = verify.effective_state(expired, now=self.now)
        self.assertEqual(expired_state.code, "expired")

        wide_ids = {
            item.id
            for item in self.database.list_stale(days=400, now=self.now)
        }
        self.assertEqual(
            wide_ids, {self.resource.id, unverified.id, expired.id}
        )
        with self.assertRaises(models.ValidationError):
            self.database.list_stale(days=-1, now=self.now)

    def test_stats_counts(self):
        second = self.database.add_resource(
            sample_resource(
                name="Second Offer",
                provider="Another Provider",
                category="web-hosting",
                offer_type="always_free",
                source_url="https://example.com/second",
                expiration_at=(self.now - timedelta(days=1)).isoformat(),
            )
        )
        self.database.record_verification(
            self.resource.id,
            "verified",
            "Manual verification evidence.",
            checked_at=self.now - timedelta(days=2),
            now=self.now,
        )
        stats = self.database.stats(now=self.now)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_category"]["vps-cloud"], 1)
        self.assertEqual(stats["by_category"]["web-hosting"], 1)
        self.assertEqual(stats["by_offer_type"]["free_tier"], 1)
        self.assertEqual(stats["by_offer_type"]["always_free"], 1)
        self.assertEqual(stats["by_verification_status"]["verified"], 1)
        self.assertEqual(stats["by_verification_status"]["unverified"], 1)
        self.assertEqual(stats["date_expired"], 1)
        self.assertEqual(stats["with_verification_evidence"], 1)
        self.assertEqual(stats["needs_reverification"], 1)
        self.assertIsNotNone(second.id)


class ImportExportTests(BansosTestCase):
    def _second_database(self) -> db.Database:
        other = db.Database(Path(self.tempdir.name) / "second.db")
        other.initialize()
        return other

    def test_export_import_roundtrip_preserves_research_fields(self):
        now = models.utc_now()
        verified = self.database.add_resource(
            sample_resource(source_url="https://example.com/verified")
        )
        unverified = self.database.add_resource(
            sample_resource(
                name="Unverified Offer",
                provider="Another Provider",
                source_url="https://example.com/unverified",
            )
        )
        self.database.record_verification(
            verified.id,
            "verified",
            "Official terms manually reviewed for roundtrip test.",
            checked_at=now - timedelta(days=2),
            now=now,
        )
        exported = self.database.export_records()
        self.assertEqual(len(exported), 2)

        other = self._second_database()
        result = other.import_records(exported)
        self.assertEqual((result.inserted, result.updated, result.skipped), (2, 0, 0))
        imported = {
            item.source_url: item for item in other.list_all()
        }
        old = imported["https://example.com/verified"]
        self.assertEqual(old.verification_status, "verified")
        self.assertEqual(
            old.verification_evidence,
            "Official terms manually reviewed for roundtrip test.",
        )
        self.assertEqual(
            old.last_verified_at, models.to_iso_z(now - timedelta(days=2))
        )
        self.assertEqual(old.category, "vps-cloud")
        self.assertEqual(old.first_seen_at, verified.first_seen_at)
        self.assertEqual(len(other.verification_events(old.id)), 1)
        self.assertEqual(
            imported["https://example.com/unverified"].verification_status,
            "unverified",
        )
        self.assertEqual(unverified.name, "Unverified Offer")

        # Importing the same data again is idempotent under the default mode.
        again = other.import_records(exported)
        self.assertEqual((again.inserted, again.updated, again.skipped), (0, 0, 2))

    def test_import_duplicate_modes_and_dry_run(self):
        exported = [self.database.add_resource(sample_resource()).to_public_dict()]

        skip = self.database.import_records(exported, on_duplicate="skip")
        self.assertEqual((skip.inserted, skip.updated, skip.skipped), (0, 0, 1))

        changed = dict(exported[0])
        changed["notes"] = "Updated via import."
        updated = self.database.import_records([changed], on_duplicate="update")
        self.assertEqual((updated.inserted, updated.updated, updated.skipped), (0, 1, 0))
        self.assertEqual(self.database.list_all()[0].notes, "Updated via import.")

        with self.assertRaises(models.DuplicateError):
            self.database.import_records([changed], on_duplicate="error")

        new = dict(exported[0])
        new["name"] = "Dry Run Offer"
        new["provider"] = "Dry Run Provider"
        new["source_url"] = "https://example.com/dry-run"
        result = self.database.import_records([new], dry_run=True)
        self.assertEqual((result.inserted, result.updated, result.skipped), (1, 0, 0))
        self.assertEqual(len(self.database.list_all()), 1)

        result = self.database.import_records([new])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(len(self.database.list_all()), 2)

    def test_import_validates_every_record_before_writing(self):
        good = sample_resource(
            name="Good Import",
            provider="Import Provider",
            source_url="https://example.com/good",
        )
        bad = sample_resource(
            name="Bad Import",
            provider="Import Provider",
            source_url="not-a-url",
        )
        with self.assertRaises(models.ValidationError):
            self.database.import_records([good, bad])
        self.assertEqual(self.database.list_all(), [])

    def test_import_rejects_unverified_claim_without_evidence(self):
        record = sample_resource(
            name="Unsupported Claim",
            provider="Import Provider",
            source_url="https://example.com/unsupported",
        )
        record["verification_status"] = "verified"
        record["last_verified_at"] = models.to_iso_z(
            models.utc_now() - timedelta(days=1)
        )
        record["verification_evidence"] = ""
        with self.assertRaises(models.ValidationError):
            self.database.import_records([record])
        self.assertEqual(self.database.list_all(), [])

        record["verification_status"] = "unverified"
        record["last_verified_at"] = models.to_iso_z(
            models.utc_now() - timedelta(days=1)
        )
        record["verification_evidence"] = "Evidence must not accompany unverified."
        with self.assertRaises(models.ValidationError):
            self.database.import_records([record])
        self.assertEqual(self.database.list_all(), [])

        record["last_verified_at"] = None
        record["verification_evidence"] = ""
        result = self.database.import_records([record])
        self.assertEqual(result.inserted, 1)
        self.assertEqual(self.database.list_all()[0].verification_status, "unverified")

    def test_import_rejects_unknown_fields_and_malformed_documents(self):
        record = sample_resource()
        record["unexpected"] = "value"
        with self.assertRaises(models.ValidationError):
            self.database.import_records([record])
        with self.assertRaises(models.ValidationError):
            self.database.import_records("not a list")
        with self.assertRaises(models.ValidationError):
            self.database.import_records([1, 2])
        with self.assertRaises(models.ValidationError):
            db.parse_import_document({"resources": "nope"})
        with self.assertRaises(models.ValidationError):
            db.parse_import_document({"unrelated": []})
        wrapped = db.parse_import_document(
            {"format": "bansos-resources", "resources": [sample_resource()]}
        )
        self.assertEqual(len(wrapped), 1)

    def test_import_handling_for_ambiguous_duplicates(self):
        first = self.database.add_resource(
            sample_resource(
                name="Name One",
                provider="Shared Provider",
                source_url="https://example.com/one",
            )
        )
        self.database.add_resource(
            sample_resource(
                name="Name Two",
                provider="Shared Provider",
                source_url="https://example.com/two",
            )
        )
        incoming = sample_resource(
            name="Name One",
            provider="Shared Provider",
            source_url="https://example.com/two",
        )
        # This incoming identity matches two records: same provider+name as
        # #first and same source URL as the second record.
        with self.assertRaises(models.ConflictError):
            self.database.import_records([incoming], on_duplicate="update")
        self.assertIsNotNone(first.id)

    def test_import_file_loading_errors(self):
        bad = Path(self.tempdir.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(models.ValidationError):
            db.load_import_file(bad)

        good = Path(self.tempdir.name) / "good.json"
        good.write_text(json.dumps([sample_resource()]), encoding="utf-8")
        loaded = db.load_import_file(good)
        self.assertEqual(len(loaded), 1)

        with self.assertRaises(models.ValidationError):
            db.load_import_file(good, max_bytes=5)
        missing = Path(self.tempdir.name) / "missing.json"
        with self.assertRaises(models.ValidationError):
            db.load_import_file(missing)

    def test_export_and_import_of_expired_and_unverified_records(self):
        now = models.utc_now()
        expired = self.database.add_resource(
            sample_resource(
                name="Expired Import",
                provider="Import Provider",
                source_url="https://example.com/expired-import",
                expiration_at=(now - timedelta(days=1)).isoformat(),
            )
        )
        exported = self.database.export_records()
        other = self._second_database()
        other.import_records(exported)
        imported = other.list_all()[0]
        self.assertEqual(imported.name, "Expired Import")
        self.assertEqual(other.stats(now=now)["date_expired"], 1)
        self.assertEqual(imported.verification_status, "unverified")


if __name__ == "__main__":
    unittest.main()
