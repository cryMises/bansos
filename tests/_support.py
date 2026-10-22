"""Shared unittest helpers.

Each test module adds the src and tests directories to sys.path so the suite
runs with the standard library only:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bansos import db


def sample_resource(**overrides):
    data = {
        "name": "Example Compute",
        "provider": "Example Provider, Inc.",
        "category": "vps-cloud",
        "description": "A fictional resource used only by the automated tests.",
        "offer_type": "free_tier",
        "pricing_notes": "No real pricing is represented.",
        "included_limits": "No real quota is represented.",
        "eligibility_requirements": "Account required",
        "payment_method_required": "unknown",
        "country_availability": "Worldwide",
        "source_url": "https://example.com/compute",
        "official_terms_url": "https://example.com/terms",
        "notes": "Manual research note for testing.",
    }
    data.update(overrides)
    return data


class BansosTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="bansos-test-")
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "bansos-test.db"
        self.database = db.Database(self.db_path)
        self.initialize_result = self.database.initialize()

    @property
    def fixture(self):
        return sample_resource
