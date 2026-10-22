"""Tests for the terminal presentation helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bansos import presentation  # noqa: E402


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self._original_theme = presentation._THEME
        self.addCleanup(setattr, presentation, "_THEME", self._original_theme)
        self._set_theme()

    def _set_theme(self, *, color=False, unicode=True, width=80):
        presentation._THEME = presentation.Theme(
            color=color, unicode=unicode, width=width
        )

    def test_visible_width_ignores_ansi_and_counts_wide_characters(self):
        self.assertEqual(presentation.visible_width("\x1b[31mred\x1b[0m"), 3)
        self.assertEqual(presentation.visible_width("界"), 2)
        self.assertEqual(presentation.visible_width("e\u0301"), 1)

    def test_shorten_uses_display_width_and_ellipsis(self):
        shortened = presentation.shorten("abcdefghij", 7)
        self.assertEqual(shortened, "abcdef…")
        self.assertLessEqual(presentation.visible_width(shortened), 7)

        self._set_theme(unicode=False)
        shortened = presentation.shorten("abcdefghij", 7)
        self.assertEqual(shortened, "abcd...")
        self.assertLessEqual(presentation.visible_width(shortened), 7)

    def test_wrap_text_preserves_blank_lines_and_breaks_long_words(self):
        lines = presentation.wrap_text(
            "one two three\n\nsupercalifragilistic", 8
        )
        self.assertIn("", lines)
        self.assertTrue(
            all(presentation.visible_width(line) <= 8 for line in lines)
        )
        self.assertEqual(lines[0], "one two")

    def test_render_table_fits_width_and_aligns_numeric_column(self):
        table = presentation.render_table(
            [
                presentation.Column("ID", align="right", min_width=3),
                presentation.Column("NAME", min_width=8, flex=True),
            ],
            [
                [7, "short"],
                [123, "a longer value that should be truncated"],
            ],
            width=30,
        )
        lines = table.splitlines()
        self.assertTrue(
            all(presentation.visible_width(line) <= 30 for line in lines)
        )
        self.assertIn("123", table)
        self.assertTrue(any("…" in line for line in lines))
        self.assertTrue(lines[0].startswith("╭"))
        self.assertTrue(lines[-1].startswith("╰"))

    def test_render_table_wraps_multiline_cells(self):
        table = presentation.render_table(
            [
                presentation.Column("ID"),
                presentation.Column(
                    "DETAIL", min_width=10, max_width=24, flex=True, wrap=True
                ),
            ],
            [[1, "first\n" + ("long reason " * 5)]],
            width=40,
        )
        lines = table.splitlines()
        self.assertTrue(
            all(presentation.visible_width(line) <= 40 for line in lines)
        )
        self.assertIn("first", table)
        self.assertIn("reason", table)
        self.assertGreater(len(lines), 5)

    def test_ascii_mode_uses_ascii_borders_and_symbols(self):
        self._set_theme(unicode=False)
        table = presentation.render_table(
            [presentation.Column("ID"), presentation.Column("NAME")],
            [[1, "one"]],
            width=30,
        )
        self.assertTrue(table.startswith("+"))
        self.assertNotIn("╭", table)
        self.assertEqual(
            presentation.state_cell("verified").text, "+ VERIFIED"
        )
        self.assertTrue(presentation.panel(["hello"], width=30).startswith("+"))

    def test_stacked_bar_allocates_exact_width(self):
        segments = [
            presentation.Segment("verified", 99, "green", "█"),
            presentation.Segment("disputed", 9, "magenta", "▓"),
        ]
        graph = presentation.stacked_bar(segments, 10)
        self.assertEqual(presentation.visible_width(graph), 10)
        self.assertEqual(graph.count("█"), 9)
        self.assertEqual(graph.count("▓"), 1)

        empty = presentation.stacked_bar(
            [presentation.Segment("none", 0, "dim", "░")], 10
        )
        self.assertEqual(presentation.visible_width(empty), 10)

    def test_bar_chart_scales_to_max_and_reports_share(self):
        chart = presentation.bar_chart(
            [("alpha", 5), ("beta", 10)], total=20, width=60
        )
        lines = chart.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(
            all(presentation.visible_width(line) <= 60 for line in lines)
        )
        self.assertIn("25.0%", lines[0])
        self.assertIn("50.0%", lines[1])
        self.assertGreater(lines[1].count("█"), lines[0].count("█"))

    def test_state_legend_wraps_to_width(self):
        legend = presentation.state_legend(40)
        lines = legend.splitlines()
        self.assertGreater(len(lines), 1)
        self.assertTrue(
            all(presentation.visible_width(line) <= 40 for line in lines)
        )
        self.assertIn("verified", legend)
        self.assertIn("disputed", legend)

    def test_metric_table_uses_two_columns_when_narrow(self):
        table = presentation.metric_table(
            [
                ("Resources", "108"),
                ("Verified", "99 · 92%"),
                ("Needs review", "9"),
                ("Evidence", "108 · 100%"),
            ],
            width=40,
        )
        lines = table.splitlines()
        self.assertTrue(
            all(presentation.visible_width(line) <= 40 for line in lines)
        )
        self.assertIn("RESOURCES", table)
        self.assertIn("NEEDS REVIEW", table)

    def test_painted_text_can_be_measured_and_stripped(self):
        self._set_theme(color=True)
        painted = presentation.theme().paint("hello", "green bold")
        self.assertIn("\x1b[", painted)
        self.assertEqual(presentation.visible_width(painted), 5)
        self.assertEqual(presentation.strip_ansi(painted), "hello")

    def test_colorize_help_paints_layout_without_changing_screen_text(self):
        help_text = (
            "usage: bansos [-h] COMMAND\n"
            "\n"
            "options:\n"
            "  -h, --help  show this help message and exit\n"
            "\n"
            "Examples:\n"
            "  bansos list\n"
        )
        self._set_theme(color=True)
        colored = presentation.colorize_help(help_text)
        self.assertIn("\x1b[", colored)
        self.assertEqual(presentation.strip_ansi(colored), help_text)

        self._set_theme(color=False)
        self.assertEqual(presentation.colorize_help(help_text), help_text)

    def test_configure_respects_environment_overrides(self):
        with mock.patch.dict(
            os.environ,
            {
                "BANSOS_COLOR": "always",
                "BANSOS_WIDTH": "64",
                "BANSOS_ASCII": "1",
            },
            clear=False,
        ):
            theme = presentation.configure()
        self.assertTrue(theme.color)
        self.assertEqual(theme.width, 64)
        self.assertFalse(theme.unicode)


if __name__ == "__main__":
    unittest.main()
