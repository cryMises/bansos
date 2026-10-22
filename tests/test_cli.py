"""End-to-end CLI tests (no subprocesses and no network)."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bansos import cli, models, verify  # noqa: E402
from _support import BansosTestCase, sample_resource  # noqa: E402


class CliTests(BansosTestCase):
    def setUp(self):
        super().setUp()
        self.noninteractive = mock.patch(
            "bansos.cli._stdin_is_interactive", return_value=False
        )
        self.noninteractive.start()
        self.addCleanup(self.noninteractive.stop)

    def _run_argv(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main([str(item) for item in argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def run_cli(self, *args, db_path=None):
        argv = ["--db", str(db_path or self.db_path), *args]
        return self._run_argv(argv)

    def _add_flags(self):
        return [
            "add",
            "--name",
            "CLI Example Service",
            "--provider",
            "CLI Provider, Inc.",
            "--category",
            "vps-cloud",
            "--description",
            "CLI test resource.",
            "--offer-type",
            "free_tier",
            "--source-url",
            "https://example.com/cli-service",
            "--official-terms-url",
            "https://example.com/cli-terms",
            "--payment-method-required",
            "unknown",
            "--country-availability",
            "Worldwide",
            "--eligibility-requirements",
            "Account required",
            "--notes",
            "CLI smoke note.",
        ]

    def test_add_list_show_search_filter_edit_and_exit_codes(self):
        code, out, err = self.run_cli(*self._add_flags())
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("Added resource #1", out)

        code, out, err = self.run_cli("list", "--json")
        self.assertEqual(code, cli.EXIT_OK, err)
        records = json.loads(out)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verification_status"], "unverified")

        code, out, err = self.run_cli("show", "1", "--json")
        self.assertEqual(code, cli.EXIT_OK, err)
        payload = json.loads(out)
        self.assertEqual(payload["resource"]["name"], "CLI Example Service")
        self.assertEqual(payload["research_state"]["code"], "unverified")
        self.assertEqual(payload["verification_events"], [])

        code, out, err = self.run_cli("search", "cli", "--json")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual(len(json.loads(out)), 1)

        code, out, err = self.run_cli(
            "filter", "--category", "vps-cloud", "--status", "unverified", "--json"
        )
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertEqual(len(json.loads(out)), 1)

        code, _, _ = self.run_cli("filter")
        self.assertEqual(code, cli.EXIT_USAGE)

        code, out, err = self.run_cli("edit", "1", "--notes", "Edited note.")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("Updated resource #1", out)
        code, out, _ = self.run_cli("show", "1", "--json")
        self.assertEqual(json.loads(out)["resource"]["notes"], "Edited note.")

        duplicate_flags = self._add_flags()
        duplicate_flags[2] = "Other CLI Service"
        duplicate_flags[4] = "Other Provider"
        code, _, err = self.run_cli(*duplicate_flags)
        self.assertEqual(code, cli.EXIT_CONFLICT, err)

        code, _, err = self.run_cli("add")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("no resource data supplied", err)

        invalid_flags = self._add_flags()
        invalid_flags[12] = "not a url"
        code, _, err = self.run_cli(*invalid_flags)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("source_url", err)

        code, _, _ = self.run_cli("show", "999")
        self.assertEqual(code, cli.EXIT_NOT_FOUND)

        code, _, _ = self.run_cli("list", "--limit", "-1")
        self.assertEqual(code, cli.EXIT_USAGE)

    def test_verify_stale_stats_delete_and_confirmation(self):
        code, _, err = self.run_cli(*self._add_flags())
        self.assertEqual(code, cli.EXIT_OK, err)
        old_date = (models.utc_now() - timedelta(days=100)).date().isoformat()

        code, out, err = self.run_cli(
            "verify",
            "1",
            "--evidence",
            "Manual review of the fictional CLI source.",
            "--checked-at",
            old_date,
        )
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("VERIFIED", out)

        code, out, err = self.run_cli("show", "1", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["resource"]["verification_status"], "verified")
        self.assertEqual(len(payload["verification_events"]), 1)
        self.assertEqual(payload["research_state"]["code"], "stale")

        code, out, err = self.run_cli("stale", "--days", "90", "--json")
        self.assertEqual(code, cli.EXIT_OK, err)
        stale = json.loads(out)
        self.assertEqual(stale["total_matching"], 1)
        self.assertEqual(stale["resources"][0]["research_state"], "STALE")

        code, out, err = self.run_cli("stats", "--json")
        self.assertEqual(code, cli.EXIT_OK, err)
        stats = json.loads(out)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["needs_reverification"], 1)
        self.assertEqual(stats["by_verification_status"]["verified"], 1)

        code, _, err = self.run_cli("delete", "1")
        self.assertEqual(code, cli.EXIT_CONFLICT, err)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(len(json.loads(out)), 1)

        code, out, err = self.run_cli("delete", "1", "--yes")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("Deleted resource #1", out)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(json.loads(out), [])
        code, _, _ = self.run_cli("show", "1")
        self.assertEqual(code, cli.EXIT_NOT_FOUND)

    def test_export_csv_json_and_import_modes(self):
        self.run_cli(*self._add_flags())
        second_flags = self._add_flags()
        second_flags[2] = "Second CLI Service"
        second_flags[4] = "Second CLI Provider"
        second_flags[12] = "https://example.com/cli-second"
        self.run_cli(*second_flags)
        self.run_cli(
            "verify",
            "1",
            "--evidence",
            "Export/import test evidence.",
            "--checked-at",
            (models.utc_now() - timedelta(days=1)).date().isoformat(),
        )

        json_path = Path(self.tempdir.name) / "catalog.json"
        code, out, err = self.run_cli("export", "--output", str(json_path))
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("Exported 2 resource(s)", out)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], "bansos-resources")
        self.assertEqual(payload["count"], 2)

        code, _, err = self.run_cli("export", "--output", str(json_path))
        self.assertEqual(code, cli.EXIT_CONFLICT, err)
        code, _, err = self.run_cli(
            "export", "--output", str(json_path), "--force"
        )
        self.assertEqual(code, cli.EXIT_OK, err)

        csv_path = Path(self.tempdir.name) / "catalog.csv"
        code, _, err = self.run_cli("export", "--output", str(csv_path))
        self.assertEqual(code, cli.EXIT_OK, err)
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, list(models.PUBLIC_FIELDS))
            rows = list(reader)
        self.assertEqual(len(rows), 2)
        self.assertIn("verification_evidence", rows[0])

        second_db = Path(self.tempdir.name) / "import-target.db"
        code, _, err = self.run_cli("init", db_path=second_db)
        self.assertEqual(code, cli.EXIT_OK, err)

        code, out, err = self.run_cli(
            "import", str(json_path), "--dry-run", db_path=second_db
        )
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("2 inserted", out)
        code, out, _ = self.run_cli("list", "--json", db_path=second_db)
        # The dry run must not write anything.
        self.assertEqual(json.loads(out), [])

        code, out, err = self.run_cli("import", str(json_path), db_path=second_db)
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("2 inserted", out)
        code, out, _ = self.run_cli("list", "--json", db_path=second_db)
        self.assertEqual(len(json.loads(out)), 2)

        code, out, err = self.run_cli("import", str(json_path), db_path=second_db)
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("2 skipped", out)

        malformed = Path(self.tempdir.name) / "malformed.json"
        malformed.write_text("{not valid json", encoding="utf-8")
        code, _, err = self.run_cli("import", str(malformed), db_path=second_db)
        self.assertEqual(code, cli.EXIT_USAGE, err)
        self.assertIn("invalid JSON", err)

    def test_interactive_edit_updates_only_supplied_fields(self):
        self.run_cli(*self._add_flags())
        # Prompts: name, provider, category, description, source URL, terms URL,
        # offer type, payment, country, eligibility, pricing, limits, first seen,
        # expiration, notes. Blank means keep the current value.
        inputs = [
            "",
            "",
            "",
            "Interactive edit description.",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
        with mock.patch(
            "bansos.cli._stdin_is_interactive", return_value=True
        ), mock.patch("builtins.input", side_effect=inputs):
            code, out, err = self.run_cli("edit", "1")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("Updated resource #1", out)
        code, out, _ = self.run_cli("show", "1", "--json")
        resource = json.loads(out)["resource"]
        self.assertEqual(resource["description"], "Interactive edit description.")
        self.assertEqual(resource["name"], "CLI Example Service")
        self.assertEqual(resource["source_url"], "https://example.com/cli-service")

    def test_import_from_stdin(self):
        record = sample_resource(
            name="Stdin Import",
            provider="Stdin Provider",
            source_url="https://example.com/stdin",
        )
        stdin = io.StringIO(json.dumps([record]))
        with mock.patch("sys.stdin", stdin):
            code, out, err = self.run_cli("import", "-")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("1 inserted", out)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(json.loads(out)[0]["name"], "Stdin Import")

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        flags = self._add_flags()
        flags[22] = '=HYPERLINK("https://example.com","click")'
        self.run_cli(*flags)
        csv_path = Path(self.tempdir.name) / "formula.csv"
        json_path = Path(self.tempdir.name) / "formula.json"
        code, _, err = self.run_cli("export", "--output", str(csv_path))
        self.assertEqual(code, cli.EXIT_OK, err)
        self.run_cli("export", "--output", str(json_path))
        with csv_path.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        self.assertTrue(row["notes"].startswith("'="))
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["resources"][0]["notes"].startswith("="))

    def test_seed_requires_confirmation_and_is_idempotent(self):
        code, _, err = self.run_cli("seed")
        self.assertEqual(code, cli.EXIT_CONFLICT)
        self.assertIn("--yes", err)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(json.loads(out), [])

        code, out, err = self.run_cli("seed", "--yes")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("2 inserted", out)
        code, out, _ = self.run_cli("list", "--json")
        records = json.loads(out)
        self.assertEqual(len(records), 2)
        self.assertTrue(all("[DEMO]" in record["name"] for record in records))
        self.assertTrue(all(record["verification_status"] == "unverified" for record in records))

        code, out, err = self.run_cli("seed", "--yes")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("2 skipped", out)

    def test_db_option_after_command_and_environment_variable(self):
        after_db = Path(self.tempdir.name) / "after.db"
        code, out, err = self._run_argv(["init", "--db", str(after_db)])
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertTrue(after_db.exists())

        flags = self._add_flags()
        flags.append("--db")
        flags.append(str(after_db))
        code, out, err = self._run_argv(flags)
        self.assertEqual(code, cli.EXIT_OK, err)
        code, out, _ = self._run_argv(["list", "--db", str(after_db), "--json"])
        self.assertEqual(len(json.loads(out)), 1)

        env_path = Path(self.tempdir.name) / "env.db"
        with mock.patch.dict(os.environ, {"BANSOS_DB": str(env_path)}):
            code, _, err = self._run_argv(["init"])
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertTrue(env_path.exists())

    def test_interactive_add_and_delete_confirmation(self):
        inputs = [
            "Interactive Host",
            "Interactive Provider",
            "2",
            "https://example.com/interactive",
            "Interactive description.",
            "",
            "",
            "",
            "",
            "",
        ]
        with mock.patch(
            "bansos.cli._stdin_is_interactive", return_value=True
        ), mock.patch("builtins.input", side_effect=inputs):
            code, out, err = self.run_cli("add")
        self.assertEqual(code, cli.EXIT_OK, err)
        self.assertIn("Added resource #1", out)
        code, out, _ = self.run_cli("show", "1", "--json")
        record = json.loads(out)["resource"]
        self.assertEqual(record["category"], "web-hosting")
        self.assertEqual(record["offer_type"], "unknown")
        self.assertEqual(record["payment_method_required"], "unknown")

        with mock.patch(
            "bansos.cli._stdin_is_interactive", return_value=True
        ), mock.patch("builtins.input", return_value="no"):
            code, _, err = self.run_cli("delete", "1")
        self.assertEqual(code, cli.EXIT_CANCELLED, err)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(len(json.loads(out)), 1)

        with mock.patch(
            "bansos.cli._stdin_is_interactive", return_value=True
        ), mock.patch("builtins.input", return_value="yes"):
            code, _, err = self.run_cli("delete", "1")
        self.assertEqual(code, cli.EXIT_OK, err)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(json.loads(out), [])


if __name__ == "__main__":
    unittest.main()
