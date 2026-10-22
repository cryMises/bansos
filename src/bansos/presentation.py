"""Terminal presentation helpers for BANSOS.

This module deliberately uses only the Python standard library. Colour is
enabled only for an interactive terminal (or when explicitly requested with
``BANSOS_COLOR=always``), and box-drawing characters fall back to ASCII when
the output encoding cannot represent them.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_RESET = "\x1b[0m"

_STYLE_CODES = {
    "reset": "0",
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "bright_black": "90",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_blue": "94",
    "bright_magenta": "95",
    "bright_cyan": "96",
}

# (unicode symbol, ASCII symbol, colour style)
_STATE_META = {
    "verified": ("●", "+", "green"),
    "unverified": ("○", "o", "cyan"),
    "stale": ("◐", "~", "yellow"),
    "expired": ("✖", "x", "red"),
    "disputed": ("◆", "!", "magenta"),
}

_BAR_FILLED = "█"
_BAR_EMPTY = "░"


class Theme:
    """Colour, glyph, and width settings for one CLI invocation."""

    def __init__(self, *, color: bool, unicode: bool, width: int) -> None:
        self.color = color
        self.unicode = unicode
        self.width = width

    def paint(self, text: Any, style: str = "") -> str:
        text = str(text)
        if not self.color or not style:
            return text
        codes = []
        for token in style.split():
            code = _STYLE_CODES.get(token)
            if code is not None:
                codes.append(code)
        if not codes:
            return text
        return "\x1b[" + ";".join(codes) + "m" + text + _RESET

    def bold(self, text: Any) -> str:
        return self.paint(text, "bold")

    def dim(self, text: Any) -> str:
        return self.paint(text, "dim")

    def total_width(self, maximum: int = 110) -> int:
        return max(40, min(self.width, maximum))

    @property
    def h(self) -> str:
        return "─" if self.unicode else "-"

    @property
    def v(self) -> str:
        return "│" if self.unicode else "|"

    @property
    def bar_filled(self) -> str:
        return _BAR_FILLED if self.unicode else "#"

    @property
    def bar_empty(self) -> str:
        return _BAR_EMPTY if self.unicode else "-"

    @property
    def arrow(self) -> str:
        return "↳" if self.unicode else "->"

    @property
    def dot(self) -> str:
        return "·" if self.unicode else "-"


_THEME: Optional[Theme] = None


def _stdout_isatty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _stdout_encoding() -> str:
    return getattr(sys.stdout, "encoding", None) or "ascii"


def _supports_unicode() -> bool:
    if os.environ.get("BANSOS_ASCII", "").strip().lower() in {"1", "true", "yes"}:
        return False
    sample = "╭─│╰╮╯█░…●○◐✖◆"
    try:
        sample.encode(_stdout_encoding())
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _terminal_width() -> int:
    override = os.environ.get("BANSOS_WIDTH")
    if override:
        try:
            return max(40, min(200, int(override)))
        except ValueError:
            pass
    try:
        columns = shutil.get_terminal_size((80, 24)).columns
    except (OSError, ValueError):
        columns = 80
    return max(40, min(160, columns))


def _color_requested() -> bool:
    mode = os.environ.get("BANSOS_COLOR", "auto").strip().lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("TERM", "").strip().lower() == "dumb":
        return False
    return _stdout_isatty()


def configure() -> Theme:
    """Re-detect terminal capabilities and return the active theme."""

    global _THEME
    _THEME = Theme(
        color=_color_requested(),
        unicode=_supports_unicode(),
        width=_terminal_width(),
    )
    return _THEME


def theme() -> Theme:
    return _THEME if _THEME is not None else configure()


# ---------------------------------------------------------------------------
# Width-aware text helpers
# ---------------------------------------------------------------------------


def strip_ansi(text: Any) -> str:
    return _ANSI_RE.sub("", str(text))


def _char_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 2
    return 1


def visible_width(text: Any) -> int:
    return sum(_char_width(character) for character in strip_ansi(text))


def shorten(text: Any, width: int, ellipsis: Optional[str] = None) -> str:
    """Collapse whitespace and truncate to *width* display columns."""

    value = " ".join(
        str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ").split()
    )
    if width <= 0:
        return ""
    if visible_width(value) <= width:
        return value
    marker = ellipsis if ellipsis is not None else ("…" if theme().unicode else "...")
    if visible_width(marker) >= width:
        return value[:width]
    target = width - visible_width(marker)
    kept = []
    used = 0
    for character in value:
        step = _char_width(character)
        if used + step > target:
            break
        kept.append(character)
        used += step
    return "".join(kept) + marker


def wrap_text(text: Any, width: int) -> list[str]:
    """Wrap text to *width* columns while preserving explicit line breaks."""

    width = max(1, width)
    value = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    lines: list[str] = []
    for paragraph in value.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        wrapped = textwrap.wrap(
            paragraph,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=True,
            drop_whitespace=True,
        )
        lines.extend(wrapped or [""])
    return lines or [""]


def _pad(text: str, width: int, align: str = "left") -> str:
    if visible_width(text) > width:
        text = shorten(text, width, ellipsis="")
    padding = max(0, width - visible_width(text))
    if align == "right":
        return " " * padding + text
    if align == "center":
        left = padding // 2
        return " " * left + text + " " * (padding - left)
    return text + " " * padding


def rule(title: str = "", *, width: Optional[int] = None) -> str:
    current = theme()
    total = max(20, min(width or current.width, 110))
    if not title:
        return current.paint(current.h * total, "dim")
    label = f" {title.upper()} "
    if visible_width(label) >= total:
        label = shorten(label, max(1, total - 2), ellipsis="")
        return current.paint(label, "bold")
    fill = max(0, total - 2 - visible_width(label))
    return (
        current.paint(current.h * 2, "dim")
        + current.paint(label, "bold")
        + current.paint(current.h * fill, "dim")
    )


# ---------------------------------------------------------------------------
# Table primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    text: Any = ""
    style: str = ""


@dataclass(frozen=True)
class Column:
    header: str
    align: str = "left"
    min_width: int = 3
    max_width: Optional[int] = None
    flex: bool = False
    wrap: bool = False


def _cell_text(value: Any) -> str:
    if isinstance(value, Cell):
        return "" if value.text is None else str(value.text)
    return "" if value is None else str(value)


def _cell_style(value: Any) -> str:
    return value.style if isinstance(value, Cell) else ""


def _border(
    left: str,
    middle: str,
    right: str,
    widths: Sequence[int],
    padding: int,
) -> str:
    current = theme()
    segments = [current.h * (width + padding * 2) for width in widths]
    return current.paint(left + middle.join(segments) + right, "dim")


def render_table(
    columns: Sequence[Column],
    rows: Sequence[Sequence[Any]],
    *,
    width: Optional[int] = None,
    header: bool = True,
    expand: bool = True,
    row_separator: bool = False,
    padding: int = 1,
) -> str:
    """Render a responsive box table.

    The returned string has no trailing newline. Numeric columns can be
    right-aligned; long text cells are shortened with an ellipsis unless the
    column is marked ``wrap=True``.
    """

    current = theme()
    if not columns:
        return ""
    table_width = max(12, min(width or current.width, 140))
    overhead = (len(columns) + 1) + 2 * padding * len(columns)
    available = max(len(columns) * 3, table_width - overhead)

    natural: list[int] = []
    for index, column in enumerate(columns):
        widths = [visible_width(column.header)] if header else []
        for row in rows:
            value = row[index] if index < len(row) else ""
            for line in _cell_text(value).split("\n"):
                widths.append(visible_width(line))
        value = max(widths or [column.min_width])
        if column.max_width is not None:
            value = min(value, column.max_width)
        natural.append(max(1, value))

    minimums = [
        max(1, min(column.min_width, natural[index]))
        for index, column in enumerate(columns)
    ]
    widths = natural[:]

    if sum(widths) < available and expand:
        flexible = [index for index, column in enumerate(columns) if column.flex]
        slack = available - sum(widths)
        while slack > 0 and flexible:
            progressed = False
            for index in flexible:
                if slack <= 0:
                    break
                maximum = columns[index].max_width
                if maximum is None or widths[index] < maximum:
                    widths[index] += 1
                    slack -= 1
                    progressed = True
            if not progressed:
                break
    elif sum(widths) > available:
        while sum(widths) > available:
            candidates = [
                index for index, value in enumerate(widths) if value > minimums[index]
            ]
            if not candidates:
                break
            chosen = max(
                candidates,
                key=lambda index: (
                    widths[index] - minimums[index],
                    1 if columns[index].flex else 0,
                ),
            )
            widths[chosen] -= 1

    def line_for(left: str, middle: str, right: str) -> str:
        return _border(left, middle, right, widths, padding)

    def render_row(values: Sequence[Any], *, header_row: bool = False) -> list[str]:
        prepared: list[list[str]] = []
        for index, column in enumerate(columns):
            value = values[index] if index < len(values) else ""
            if column.wrap:
                prepared.append(wrap_text(_cell_text(value), widths[index]))
            else:
                prepared.append([shorten(_cell_text(value), widths[index])])
        height = max(len(lines) for lines in prepared) if prepared else 1
        output: list[str] = []
        for line_index in range(height):
            cells: list[str] = []
            for index, column in enumerate(columns):
                lines = prepared[index]
                text = lines[line_index] if line_index < len(lines) else ""
                padded = _pad(text, widths[index], column.align)
                if header_row:
                    style = "bold"
                else:
                    style = _cell_style(
                        values[index] if index < len(values) else ""
                    )
                cells.append(current.paint(padded, style))
            cells_text = current.paint(current.v, "dim")
            cells_text += current.paint(current.v, "dim").join(
                (" " * padding) + cell + (" " * padding) for cell in cells
            )
            cells_text += current.paint(current.v, "dim")
            output.append(cells_text)
        return output

    lines: list[str] = []
    if current.unicode:
        lines.append(line_for("╭", "┬", "╮"))
    else:
        lines.append(line_for("+", "+", "+"))
    if header:
        lines.extend(render_row([column.header for column in columns], header_row=True))
        if current.unicode:
            lines.append(line_for("├", "┼", "┤"))
        else:
            lines.append(line_for("+", "+", "+"))
    for row_index, row in enumerate(rows):
        lines.extend(render_row(row))
        if row_separator and row_index < len(rows) - 1:
            if current.unicode:
                lines.append(line_for("├", "┼", "┤"))
            else:
                lines.append(line_for("+", "+", "+"))
    if current.unicode:
        lines.append(line_for("╰", "┴", "╯"))
    else:
        lines.append(line_for("+", "+", "+"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Panels, state badges, and charts
# ---------------------------------------------------------------------------


def panel(
    lines: Sequence[Any],
    *,
    title: str = "",
    width: Optional[int] = None,
    border_style: str = "dim",
) -> str:
    """Render a rounded panel with optional title and wrapped content."""

    current = theme()
    total = max(20, min(width or current.width, 110))
    inner = total - 4
    content: list[Tuple[str, str]] = []
    for value in lines:
        style = _cell_style(value)
        for line in wrap_text(_cell_text(value), inner):
            content.append((line, style))
    if not content:
        content = [("", "")]

    if current.unicode:
        top_left, top_right, bottom_left, bottom_right = "╭", "╮", "╰", "╯"
        horizontal = current.h
    else:
        top_left, top_right, bottom_left, bottom_right = "+", "+", "+", "+"
        horizontal = "-"

    border_width = total - 2
    if title:
        label = f" {title} "
        if visible_width(label) > border_width - 1:
            label = shorten(label, max(1, border_width - 1), ellipsis="")
        fill = max(0, border_width - 1 - visible_width(label))
        top = (
            current.paint(top_left + horizontal, border_style)
            + current.paint(label, "bold")
            + current.paint(horizontal * fill + top_right, border_style)
        )
    else:
        top = current.paint(
            top_left + horizontal * border_width + top_right, border_style
        )

    rendered = [top]
    for line, style in content:
        padded = _pad(line, inner)
        rendered.append(
            current.paint(current.v, border_style)
            + " "
            + current.paint(padded, style)
            + " "
            + current.paint(current.v, border_style)
        )
    rendered.append(
        current.paint(
            bottom_left + horizontal * border_width + bottom_right, border_style
        )
    )
    return "\n".join(rendered)


def state_style(code: str) -> str:
    return _STATE_META.get(code, ("", "", "dim"))[2]


def state_cell(code: str, label: Optional[str] = None) -> Cell:
    current = theme()
    symbol, ascii_symbol, style = _STATE_META.get(
        code, ("?", "?", "dim")
    )
    mark = symbol if current.unicode else ascii_symbol
    text = (label or code).upper()
    return Cell(f"{mark} {text}", style)


def state_legend(width: Optional[int] = None) -> str:
    current = theme()
    limit = max(20, min(width or current.width, 110))
    entries = []
    for code in ("verified", "unverified", "stale", "expired", "disputed"):
        symbol, ascii_symbol, style = _STATE_META[code]
        mark = symbol if current.unicode else ascii_symbol
        entries.append(current.paint(f"{mark} {code}", style))

    lines: list[str] = []
    prefix = current.dim("States  ")
    current_line = prefix
    for entry in entries:
        separator = "" if current_line == prefix else "   "
        candidate = current_line + separator + entry
        if visible_width(candidate) > limit and current_line != prefix:
            lines.append(current_line)
            current_line = " " * visible_width(prefix) + entry
        else:
            current_line = candidate
    lines.append(current_line)
    return "\n".join(lines)


def mark_ok() -> str:
    return theme().paint("✔" if theme().unicode else "+", "green bold")


def mark_warn() -> str:
    return theme().paint("⚠" if theme().unicode else "!", "yellow bold")


def mark_error() -> str:
    return theme().paint("✖" if theme().unicode else "x", "red bold")


def bar(value: int, maximum: int, width: int, style: str = "cyan") -> str:
    current = theme()
    width = max(1, width)
    if maximum <= 0 or value <= 0:
        filled = 0
    else:
        filled = int(round(width * value / maximum))
        filled = max(1, min(width, filled))
    return current.paint(current.bar_filled * filled, style) + current.paint(
        current.bar_empty * (width - filled), "dim"
    )


@dataclass(frozen=True)
class Segment:
    label: str
    count: int
    style: str = "cyan"
    char: Optional[str] = None


def stacked_bar(segments: Sequence[Segment], width: int) -> str:
    current = theme()
    width = max(1, width)
    total = sum(max(0, segment.count) for segment in segments)
    if total <= 0:
        return current.paint(current.bar_empty * width, "dim")

    exact = [width * max(0, segment.count) / total for segment in segments]
    allocations = [int(value) for value in exact]
    remainder = width - sum(allocations)
    order = sorted(
        range(len(segments)),
        key=lambda index: (exact[index] - allocations[index], segments[index].count),
        reverse=True,
    )
    for index in order:
        if remainder <= 0:
            break
        allocations[index] += 1
        remainder -= 1

    parts = []
    for segment, count in zip(segments, allocations):
        if count <= 0:
            continue
        character = segment.char or (current.bar_filled if current.unicode else "#")
        parts.append(current.paint(character * count, segment.style))
    return "".join(parts)


def stacked_legend(
    segments: Sequence[Segment], width: Optional[int] = None
) -> str:
    current = theme()
    limit = max(20, min(width or current.width, 110))
    entries = []
    for segment in segments:
        if segment.count <= 0:
            continue
        entries.append(
            current.paint(str(segment.count), f"{segment.style} bold")
            + " "
            + segment.label
        )

    lines: list[str] = []
    current_line = ""
    separator = current.dim(f" {current.dot} ")
    for entry in entries:
        candidate = (
            entry
            if not current_line
            else current_line + separator + entry
        )
        if visible_width(candidate) > limit and current_line:
            lines.append(current_line)
            current_line = entry
        else:
            current_line = candidate
    if current_line:
        lines.append(current_line)
    return "\n".join(lines)


def bar_chart(
    values: Iterable[Tuple[str, int]],
    *,
    total: Optional[int] = None,
    width: Optional[int] = None,
    fill_style: str = "cyan",
    color_map: Optional[Mapping[str, str]] = None,
) -> str:
    current = theme()
    items = [(str(label), max(0, int(count))) for label, count in values]
    if not items:
        return current.dim("No data.")
    maximum = max(count for _, count in items)
    if total is None:
        total = sum(count for _, count in items)
    line_width = max(24, min(width or current.width, 110))
    label_width = min(28, max(12, max(visible_width(label) for label, _ in items)))
    count_width = max(3, len(str(maximum)))
    percent_width = 6
    bar_width = line_width - label_width - count_width - percent_width - 4
    bar_width = max(8, min(36, bar_width))

    lines = []
    for label, count in items:
        percentage = (100.0 * count / total) if total else 0.0
        style = (color_map or {}).get(label, fill_style)
        graph = bar(count, maximum, bar_width, style)
        lines.append(
            _pad(shorten(label, label_width), label_width)
            + " "
            + current.paint(str(count).rjust(count_width), "bold")
            + "  "
            + graph
            + " "
            + current.dim(f"{percentage:5.1f}%")
        )
    return "\n".join(lines)


def metric_table(
    items: Sequence[Tuple[str, str]], *, width: Optional[int] = None
) -> str:
    current = theme()
    total_width = width or current.width
    if total_width < 60:
        columns = [
            Column("Metric", min_width=10, flex=True),
            Column("Value", min_width=8, flex=True),
        ]
        rows = [
            [Cell(label.upper(), "dim"), Cell(value, "bold")]
            for label, value in items
        ]
        return render_table(
            columns,
            rows,
            width=total_width,
            header=False,
            expand=True,
        )

    columns = [
        Column(label, min_width=min(10, max(4, len(label))), flex=True)
        for label, _ in items
    ]
    labels = [Cell(label.upper(), "dim") for label, _ in items]
    values = [Cell(value, "bold") for _, value in items]
    return render_table(
        columns,
        [labels, values],
        width=total_width,
        header=False,
        expand=True,
    )
