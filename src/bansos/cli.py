"""Command-line interface for BANSOS.

Presentation lives here; validation, persistence, and research-state rules live
in models.py, db.py, search.py, and verify.py.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import __version__, db, models, presentation as ui, search, seed, verify

PROG = "bansos"

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 4
EXIT_CANCELLED = 5

MAX_IMPORT_BYTES = 10 * 1024 * 1024

REQUIRED_ADD_FIELDS = ("name", "provider", "category", "source_url")

_EDIT_PROMPTS = (
    ("name", "Name"),
    ("provider", "Provider"),
    ("category", "Category"),
    ("description", "Description"),
    ("source_url", "Source URL"),
    ("official_terms_url", "Official terms URL"),
    ("offer_type", "Offer type"),
    ("payment_method_required", "Payment required"),
    ("country_availability", "Country availability"),
    ("eligibility_requirements", "Eligibility"),
    ("pricing_notes", "Pricing notes"),
    ("included_limits", "Included limits"),
    ("first_seen_at", "First seen at"),
    ("expiration_at", "Expiration at"),
    ("notes", "Notes"),
)


# ---------------------------------------------------------------------------
# Small presentation and input helpers
# ---------------------------------------------------------------------------


def default_db_path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "bansos" / "bansos.db"


def _resolve_db_path(args: argparse.Namespace) -> Path:
    raw = getattr(args, "db", None)
    if raw is None:
        raw = os.environ.get("BANSOS_DB")
    if raw is None:
        return default_db_path()
    if not isinstance(raw, str) or not raw.strip():
        raise models.ValidationError("--db path must not be empty")
    return Path(raw).expanduser()


def _error(message: str) -> None:
    print(f"{ui.mark_error()} error: {message}", file=sys.stderr)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _print_wrapped(text: str, *, width: int, style: str = "dim") -> None:
    current = ui.theme()
    for line in ui.wrap_text(text, width):
        print(current.paint(line, style) if style else line)


def _print_resources(
    resources: Sequence[models.Resource],
    *,
    title: str = "Catalog",
    subtitle: Optional[str] = None,
) -> None:
    current = ui.theme()
    width = current.total_width(110)
    rows: list[list[Any]] = []
    for resource in resources:
        state = verify.effective_state(resource)
        checked = (resource.last_verified_at or "never")[:10]
        rows.append(
            [
                resource.id,
                ui.state_cell(state.code, state.label),
                resource.name,
                resource.provider,
                resource.category,
                checked,
            ]
        )

    if width >= 108:
        columns = [
            ui.Column("ID", align="right", min_width=3),
            ui.Column("STATE", min_width=10),
            ui.Column("NAME", min_width=16, max_width=34, flex=True),
            ui.Column("PROVIDER", min_width=10, max_width=22, flex=True),
            ui.Column("CATEGORY", min_width=10, max_width=20, flex=True),
            ui.Column("CHECKED", min_width=10),
        ]
        selected = [[row[0], row[1], row[2], row[3], row[4], row[5]] for row in rows]
    elif width >= 76:
        columns = [
            ui.Column("ID", align="right", min_width=3),
            ui.Column("STATE", min_width=10),
            ui.Column("NAME", min_width=16, max_width=34, flex=True),
            ui.Column("PROVIDER", min_width=10, max_width=24, flex=True),
            ui.Column("CHECKED", min_width=10),
        ]
        selected = [[row[0], row[1], row[2], row[3], row[5]] for row in rows]
    elif width >= 60:
        columns = [
            ui.Column("ID", align="right", min_width=3),
            ui.Column("STATE", min_width=10),
            ui.Column("NAME", min_width=14, max_width=40, flex=True),
            ui.Column("CHECKED", min_width=10),
        ]
        selected = [[row[0], row[1], row[2], row[5]] for row in rows]
    else:
        columns = [
            ui.Column("ID", align="right", min_width=3),
            ui.Column("STATE", min_width=10),
            ui.Column("NAME", min_width=12, max_width=42, flex=True),
        ]
        selected = [[row[0], row[1], row[2]] for row in rows]

    print(ui.rule(title, width=width))
    if subtitle:
        _print_wrapped(subtitle, width=width)
    print(
        ui.render_table(
            columns,
            selected,
            width=width,
        )
    )
    if selected:
        print(ui.state_legend(width))


def _stdin_is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, OSError):
        return False


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise models.ValidationError("input ended before the command was complete") from exc


def _prompt_value(label: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    raw = _ask(f"{label}{suffix}: ").strip()
    if raw:
        return raw
    return default if default is not None else ""


def _prompt_required(label: str) -> str:
    while True:
        value = _prompt_value(label)
        if value:
            return value
        print("A value is required.")


def _prompt_choice(
    label: str, choices: Sequence[str], default: Optional[str] = None
) -> str:
    default_text = default or choices[0]
    while True:
        raw = _ask(f"{label} ({'/'.join(choices)}) [{default_text}]: ").strip()
        if not raw:
            raw = default_text
        normalized = raw.lower()
        if normalized in choices:
            return normalized
        print(f"Choose one of: {', '.join(choices)}")


def _prompt_category(default: Optional[str] = None) -> str:
    print("Categories:")
    for index, category in enumerate(models.CATEGORIES, start=1):
        current = " (current)" if category == default else ""
        print(
            f"  {index:2}. {category:<24} {models.CATEGORY_LABELS[category]}{current}"
        )
    suffix = f" [{default}]" if default else ""
    while True:
        raw = _ask(f"Category (name or number){suffix}: ").strip()
        if not raw and default:
            return default
        if raw.isdigit():
            number = int(raw)
            if 1 <= number <= len(models.CATEGORIES):
                return models.CATEGORIES[number - 1]
        if raw in models.CATEGORIES:
            return raw
        print(f"Choose a category name from the list, or enter 1-{len(models.CATEGORIES)}.")


def _print_verification_warning(
    state: verify.ResearchState,
    *,
    width: int,
) -> None:
    if state.code == "unverified":
        print()
        print(
            ui.panel(
                [
                    "No verification evidence is recorded.",
                    "Use 'bansos verify ID --evidence ...' after reading the "
                    "official source.",
                ],
                title="Unverified record",
                width=width,
                border_style="cyan",
            )
        )
    elif state.code == "expired":
        print()
        print(
            ui.panel(
                [
                    state.detail,
                    "Verify before relying on this record or its source links.",
                ],
                title="Expired record",
                width=width,
                border_style="red",
            )
        )
    elif state.code == "stale":
        print()
        print(
            ui.panel(
                [
                    state.detail,
                    "Re-check the official source before relying on this record.",
                ],
                title="Stale record",
                width=width,
                border_style="yellow",
            )
        )
    elif state.code == "disputed":
        print()
        print(
            ui.panel(
                [
                    "The recorded evidence is disputed.",
                    "Treat the details as contested until they are re-checked.",
                ],
                title="Disputed record",
                width=width,
                border_style="magenta",
            )
        )


def _print_detail_field(
    label: str,
    value: Any,
    *,
    width: int,
    label_width: int = 22,
    style: str = "",
) -> None:
    current = ui.theme()
    text = "" if value is None else str(value)
    if not text.strip():
        text = "-"
        style = "dim"
    available = width - label_width - 1
    if available < 24:
        print(current.dim(label))
        for line in ui.wrap_text(text, max(16, width - 2)):
            content = current.paint(line, style) if style else line
            print(f"  {content}")
        return
    lines = ui.wrap_text(text, available)
    prefix = current.dim(f"{label:<{label_width}}")
    for index, line in enumerate(lines):
        content = current.paint(line, style) if style else line
        if index == 0:
            print(f"{prefix} {content}")
        else:
            print(f"{'':<{label_width}} {content}")


def _show_resource(
    database: db.Database, resource: models.Resource, *, json_output: bool
) -> int:
    events = database.verification_events(resource.id)
    state = verify.effective_state(resource)
    if json_output:
        _print_json(
            {
                "resource": resource.to_public_dict(),
                "research_state": {
                    "code": state.code,
                    "label": state.label,
                    "detail": state.detail,
                    "needs_review": state.needs_review,
                    "days_since_verified": state.days_since_verified,
                },
                "verification_events": [event.to_public_dict() for event in events],
            }
        )
        return EXIT_OK

    current = ui.theme()
    width = current.total_width(100)
    subtitle = (
        f"{resource.provider} {current.dot} {resource.category} "
        f"{current.dot} {resource.offer_type}"
    )
    header = [
        ui.Cell(resource.name, "bold"),
        subtitle,
        ui.state_cell(state.code, state.label),
    ]
    print(
        ui.panel(
            header,
            title=f"BANSOS {current.dot} Resource #{resource.id}",
            width=width,
        )
    )
    _print_verification_warning(state, width=width)

    print()
    print(ui.rule("Overview", width=width))
    _print_detail_field("Provider", resource.provider, width=width)
    category_label = models.CATEGORY_LABELS.get(resource.category, "unknown")
    _print_detail_field(
        "Category",
        f"{resource.category} ({category_label})",
        width=width,
    )
    _print_detail_field("Offer type", resource.offer_type, width=width)
    _print_detail_field(
        "Payment required", resource.payment_method_required, width=width
    )
    _print_detail_field(
        "Country availability", resource.country_availability, width=width
    )

    print()
    print(ui.rule("Offer details", width=width))
    _print_detail_field("Description", resource.description, width=width)
    _print_detail_field("Pricing notes", resource.pricing_notes, width=width)
    _print_detail_field("Included limits", resource.included_limits, width=width)
    _print_detail_field("Eligibility", resource.eligibility_requirements, width=width)

    print()
    print(ui.rule("Research", width=width))
    _print_detail_field(
        "Source URL", resource.source_url, width=width, style="blue"
    )
    _print_detail_field(
        "Official terms URL",
        resource.official_terms_url or "none recorded",
        width=width,
        style="blue" if resource.official_terms_url else "dim",
    )
    _print_detail_field("First seen", resource.first_seen_at, width=width)
    _print_detail_field(
        "Last verified", resource.last_verified_at or "never", width=width
    )
    _print_detail_field(
        "Expiration", resource.expiration_at or "none recorded", width=width
    )
    _print_detail_field(
        "Verification status",
        resource.verification_status,
        width=width,
        style=ui.state_style(resource.verification_status),
    )
    _print_detail_field(
        "Research state",
        f"{state.label} {current.dot} {state.detail}",
        width=width,
        style=ui.state_style(state.code),
    )
    _print_detail_field("Research notes", resource.notes, width=width)

    print()
    print(ui.rule("Verification evidence", width=width))
    _print_detail_field(
        "Evidence",
        resource.verification_evidence or "none recorded",
        width=width,
    )

    print()
    print(ui.rule("Record", width=width))
    _print_detail_field("Created", resource.created_at, width=width)
    _print_detail_field("Updated", resource.updated_at, width=width)

    print()
    print(ui.rule("Verification history", width=width))
    if events:
        if width >= 64:
            columns = [
                ui.Column("CHECKED", min_width=10),
                ui.Column("STATUS", min_width=10),
                ui.Column("RECORDED", min_width=18, flex=True),
            ]
            rows = [
                [
                    event.checked_at,
                    ui.state_cell(event.status, event.status.upper()),
                    event.created_at,
                ]
                for event in events
            ]
        else:
            columns = [
                ui.Column(
                    "CHECKED", min_width=10, flex=True, wrap=True
                ),
                ui.Column("STATUS", min_width=10),
            ]
            rows = [
                [
                    f"{event.checked_at[:10]}\nrec {event.created_at[:10]}",
                    ui.state_cell(event.status, event.status.upper()),
                ]
                for event in events
            ]
        print(ui.render_table(columns, rows, width=width))
        earlier = events[1:]
        if earlier:
            print()
            print(current.dim("Earlier evidence"))
            for event in earlier:
                print(
                    current.bold(
                        f"{event.checked_at} {current.dot} {event.status.upper()}"
                    )
                )
                for line in ui.wrap_text(event.evidence, max(20, width - 2)):
                    print(f"  {line}")
                print()
    else:
        print(current.dim("No verification events recorded."))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _add_db_after_command(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )


def _add_resource_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", help="Resource or offer name")
    parser.add_argument("--provider", help="Provider or organization name")
    parser.add_argument(
        "--category", choices=models.CATEGORIES, help="Resource category"
    )
    parser.add_argument("--description", help="Short factual description")
    parser.add_argument(
        "--offer-type", choices=models.OFFER_TYPES, help="Nature of the offer"
    )
    parser.add_argument("--pricing-notes", help="Pricing and billing notes")
    parser.add_argument("--included-limits", help="Quotas and included limits")
    parser.add_argument(
        "--eligibility-requirements",
        "--eligibility",
        dest="eligibility_requirements",
        help="Eligibility requirements",
    )
    parser.add_argument(
        "--payment-method-required",
        "--payment",
        dest="payment_method_required",
        choices=models.PAYMENT_CHOICES,
        help="Whether a payment method is required",
    )
    parser.add_argument(
        "--country-availability", help="Country or region availability"
    )
    parser.add_argument(
        "--source-url", help="Official offer/source URL (required)"
    )
    parser.add_argument(
        "--official-terms-url", help="Official terms or documentation URL"
    )
    parser.add_argument(
        "--first-seen-at", help="ISO 8601 date/time first seen (default: now)"
    )
    parser.add_argument(
        "--expiration-at", help="ISO 8601 date/time the offer expires"
    )
    parser.add_argument("--notes", help="Research notes (not verified facts)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Local-first catalog for researching legitimately free technology "
            "resources. No network access is performed."
        ),
        epilog=(
            "Database path precedence: --db, then BANSOS_DB, then "
            "~/.local/share/bansos/bansos.db (or $XDG_DATA_HOME/bansos/bansos.db).\n\n"
            "Examples:\n"
            "  bansos init\n"
            "  bansos add\n"
            "  bansos search static hosting\n"
            "  bansos filter --category web-hosting --status unverified\n"
            "  bansos verify 1 --evidence 'Official terms checked manually on 2026-01-01'\n"
            "  bansos export --format json --output exports/catalog.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help=(
            "Database file path (also honored after the command; "
            "overrides BANSOS_DB)"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, title="commands", metavar="COMMAND"
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize or migrate the local database",
        description="Create the SQLite database and apply pending migrations.",
    )
    _add_db_after_command(init_parser)

    add_parser = subparsers.add_parser(
        "add",
        help="Add a resource interactively or with options",
        description=(
            "Add a resource. With no options in a terminal, a guided prompt is "
            "shown. In scripts, supply at least --name, --provider, --category, "
            "and --source-url."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_db_after_command(add_parser)
    _add_resource_options(add_parser)

    list_parser = subparsers.add_parser(
        "list", help="List saved resources", description="List saved resources."
    )
    _add_db_after_command(list_parser)
    list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows; 0 means all (default: 50)",
    )
    list_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output JSON"
    )

    show_parser = subparsers.add_parser(
        "show", help="Show complete resource details"
    )
    _add_db_after_command(show_parser)
    show_parser.add_argument("id", type=int, metavar="ID")
    show_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output JSON"
    )

    search_parser = subparsers.add_parser(
        "search",
        help="Search names, providers, descriptions, and notes",
        description=(
            "Case-insensitive substring search over names, providers, "
            "descriptions, and notes. Multiple terms must all match."
        ),
    )
    _add_db_after_command(search_parser)
    search_parser.add_argument("query", nargs="+", metavar="QUERY")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows; 0 means all (default: 50)",
    )
    search_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output JSON"
    )

    filter_parser = subparsers.add_parser(
        "filter",
        help="Filter resources by structured fields",
        description=(
            "Filter by category, offer type, eligibility text, stored "
            "verification status, payment requirement, and country text."
        ),
    )
    _add_db_after_command(filter_parser)
    filter_parser.add_argument("--category", choices=models.CATEGORIES)
    filter_parser.add_argument("--offer-type", choices=models.OFFER_TYPES)
    filter_parser.add_argument(
        "--eligibility", help="Substring match on eligibility requirements"
    )
    filter_parser.add_argument(
        "--status",
        dest="verification_status",
        choices=models.VERIFICATION_STATUSES,
        help="Exact stored verification status",
    )
    filter_parser.add_argument(
        "--payment",
        dest="payment_method_required",
        choices=models.PAYMENT_CHOICES,
        help="Payment method requirement",
    )
    filter_parser.add_argument(
        "--country", help="Substring match on country availability"
    )
    filter_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows; 0 means all (default: 50)",
    )
    filter_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output JSON"
    )

    edit_parser = subparsers.add_parser(
        "edit",
        help="Edit an existing resource",
        description=(
            "Update supplied fields. Verification fields are intentionally not "
            "editable here; use 'bansos verify ID'. Empty optional values can be "
            "cleared with an empty string."
        ),
    )
    _add_db_after_command(edit_parser)
    edit_parser.add_argument("id", type=int, metavar="ID")
    _add_resource_options(edit_parser)
    edit_parser.add_argument(
        "--clear-expiration",
        action="store_true",
        help="Remove the expiration timestamp",
    )
    edit_parser.add_argument(
        "--clear-official-terms-url",
        action="store_true",
        help="Remove the official terms URL",
    )

    delete_parser = subparsers.add_parser(
        "delete", help="Delete a resource after confirmation"
    )
    _add_db_after_command(delete_parser)
    delete_parser.add_argument("id", type=int, metavar="ID")
    delete_parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive confirmation"
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Record manual verification evidence and date",
        description=(
            "Record a manual verification. Evidence is mandatory. BANSOS does "
            "not fetch URLs or independently confirm offers."
        ),
    )
    _add_db_after_command(verify_parser)
    verify_parser.add_argument("id", type=int, metavar="ID")
    verify_parser.add_argument(
        "--status",
        choices=verify.VERIFYABLE_STATUSES,
        default="verified",
        help="Verification outcome (default: verified)",
    )
    verify_parser.add_argument(
        "--evidence",
        help="What was checked and what it showed (required in scripts)",
    )
    verify_parser.add_argument(
        "--checked-at",
        help="ISO 8601 date/time checked (default: now; cannot be in the future)",
    )

    stale_parser = subparsers.add_parser(
        "stale", help="Find resources needing re-verification"
    )
    _add_db_after_command(stale_parser)
    stale_parser.add_argument(
        "--days",
        type=int,
        default=verify.DEFAULT_STALE_DAYS,
        help=(
            "Flag records last checked more than this many days ago "
            f"(default: {verify.DEFAULT_STALE_DAYS})"
        ),
    )
    stale_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows; 0 means all (default: 50)",
    )
    stale_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output JSON"
    )

    stats_parser = subparsers.add_parser(
        "stats", help="Show category and verification statistics"
    )
    _add_db_after_command(stats_parser)
    stats_parser.add_argument(
        "--days",
        type=int,
        default=verify.DEFAULT_STALE_DAYS,
        help="Age threshold for needs-reverification statistics",
    )
    stats_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Output JSON"
    )

    export_parser = subparsers.add_parser(
        "export",
        help="Export selected resources to CSV or JSON",
        description=(
            "Export all resources or a filtered selection. Writes to stdout "
            "unless --output is supplied. Refuses to overwrite files unless "
            "--force is given."
        ),
    )
    _add_db_after_command(export_parser)
    export_parser.add_argument(
        "--format", choices=("csv", "json"), help="Export format (inferred from extension)"
    )
    export_parser.add_argument(
        "--output",
        metavar="PATH",
        help="Output file path; '-' or omission means stdout",
    )
    export_parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing output file"
    )
    export_parser.add_argument("--category", choices=models.CATEGORIES)
    export_parser.add_argument("--offer-type", choices=models.OFFER_TYPES)
    export_parser.add_argument(
        "--status",
        dest="verification_status",
        choices=models.VERIFICATION_STATUSES,
        help="Exact stored verification status",
    )
    export_parser.add_argument("--eligibility", help="Eligibility substring")
    export_parser.add_argument(
        "--payment",
        dest="payment_method_required",
        choices=models.PAYMENT_CHOICES,
    )
    export_parser.add_argument("--country", help="Country substring")

    import_parser = subparsers.add_parser(
        "import",
        help="Import validated JSON records",
        description=(
            "Import a JSON array or a BANSOS export. All records are validated "
            "before any changes are written. Duplicate records are detected by "
            "normalized provider+name and source URL."
        ),
    )
    _add_db_after_command(import_parser)
    import_parser.add_argument(
        "file", metavar="FILE", help="JSON file path, or '-' for stdin"
    )
    import_parser.add_argument(
        "--on-duplicate",
        choices=("skip", "update", "error"),
        default="skip",
        help="Handling for existing duplicates (default: skip)",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report, but roll back all writes",
    )

    seed_parser = subparsers.add_parser(
        "seed",
        help="Load clearly fictional demonstration records",
        description=(
            "Add two clearly labeled fictional records for exploring the UI. "
            "They are not real offers, are intentionally unverified, and use "
            "the reserved example.com domain."
        ),
    )
    _add_db_after_command(seed_parser)
    seed_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )

    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_init(path: Path) -> int:
    database = db.Database(path)
    result = database.initialize()
    current = ui.theme()
    title = "BANSOS initialized" if result.created else "BANSOS ready"
    lines = [
        f"Path: {result.path}",
        f"Schema version: {result.schema_version}",
    ]
    if result.created:
        lines.append("The database was created and is ready for research.")
    if result.applied_versions:
        lines.append(
            "Applied migration(s): "
            + ", ".join(str(version) for version in result.applied_versions)
        )
    print(
        ui.panel(
            lines,
            title=title,
            width=current.total_width(100),
            border_style="green" if result.created else "cyan",
        )
    )
    return EXIT_OK


def _guided_add() -> dict[str, Any]:
    print("Interactive resource entry. Press Ctrl+C to cancel.")
    print("Optional fields can be filled in later with 'bansos edit ID'.\n")
    values: dict[str, Any] = {}
    values["name"] = _prompt_required("Name")
    values["provider"] = _prompt_required("Provider")
    values["category"] = _prompt_category()
    values["source_url"] = _prompt_required("Official source URL")
    values["description"] = _prompt_value("Description (optional)")
    values["offer_type"] = _prompt_choice(
        "Offer type", models.OFFER_TYPES, default="unknown"
    )
    values["payment_method_required"] = _prompt_choice(
        "Payment method required", models.PAYMENT_CHOICES, default="unknown"
    )
    values["country_availability"] = _prompt_value(
        "Country availability", default="unknown"
    )
    values["eligibility_requirements"] = _prompt_value(
        "Eligibility requirements (optional)"
    )
    values["notes"] = _prompt_value("Research notes (optional)")
    return values


def _collect_add_values(args: argparse.Namespace) -> dict[str, Any]:
    supplied = {
        field: getattr(args, field, None) for field in models.RESOURCE_INPUT_FIELDS
    }
    if not any(value is not None for value in supplied.values()):
        if not _stdin_is_interactive():
            raise models.ValidationError(
                "no resource data supplied; provide --name, --provider, "
                "--category, and --source-url (plus any optional fields)"
            )
        return _guided_add()

    values = {field: value for field, value in supplied.items() if value is not None}
    missing = [field for field in REQUIRED_ADD_FIELDS if not values.get(field)]
    if missing:
        if not _stdin_is_interactive():
            flags = ", ".join("--" + field.replace("_", "-") for field in missing)
            raise models.ValidationError(f"missing required option(s): {flags}")
        for field in missing:
            if field == "category":
                values[field] = _prompt_category()
            else:
                values[field] = _prompt_required(field.replace("_", " ").title())
    return values


def _cmd_add(database: db.Database, args: argparse.Namespace) -> int:
    values = _collect_add_values(args)
    resource = database.add_resource(values)
    print(
        f"{ui.mark_ok()} Added resource #{resource.id}: "
        f"{resource.name} ({resource.provider})"
    )
    print(
        ui.theme().dim(
            "Recorded as unverified. After manual research, run "
            f"'bansos verify {resource.id} --evidence ...'."
        )
    )
    return EXIT_OK


def _cmd_list(database: db.Database, args: argparse.Namespace) -> int:
    limit = search.normalize_limit(args.limit)
    resources = database.query_resources(search.ResourceQuery(limit=limit))
    if args.json_output:
        _print_json([resource.to_public_dict() for resource in resources])
        return EXIT_OK
    if not resources:
        print(ui.theme().dim("No resources found.") + " Add one with 'bansos add'.")
        return EXIT_OK
    subtitle = f"{len(resources)} resource(s) shown"
    if limit is not None and len(resources) == limit:
        subtitle += f" {ui.theme().dot} limit {limit}; use --limit 0 for all"
    _print_resources(resources, title="Catalog", subtitle=subtitle)
    return EXIT_OK


def _cmd_show(database: db.Database, args: argparse.Namespace) -> int:
    resource = database.get_resource(args.id)
    return _show_resource(database, resource, json_output=args.json_output)


def _cmd_search(database: db.Database, args: argparse.Namespace) -> int:
    text = " ".join(args.query)
    limit = search.normalize_limit(args.limit)
    resources = database.query_resources(
        search.ResourceQuery(text=text, limit=limit)
    )
    if args.json_output:
        _print_json([resource.to_public_dict() for resource in resources])
        return EXIT_OK
    if not resources:
        print(f"{ui.mark_warn()} No resources matched {text!r}.")
        return EXIT_OK
    _print_resources(
        resources,
        title="Search results",
        subtitle=f'{len(resources)} match(es) for "{text}"',
    )
    return EXIT_OK


def _filter_subtitle(args: argparse.Namespace, count: int) -> str:
    parts = []
    if args.category:
        parts.append(f"category={args.category}")
    if args.offer_type:
        parts.append(f"offer={args.offer_type}")
    if args.eligibility:
        parts.append(f"eligibility~{ui.shorten(args.eligibility, 28)}")
    if args.verification_status:
        parts.append(f"status={args.verification_status}")
    if args.payment_method_required:
        parts.append(f"payment={args.payment_method_required}")
    if args.country:
        parts.append(f"country~{ui.shorten(args.country, 24)}")
    return f"{count} match(es) {ui.theme().dot} " + ", ".join(parts)


def _cmd_filter(database: db.Database, args: argparse.Namespace) -> int:
    query = search.ResourceQuery(
        category=args.category,
        offer_type=args.offer_type,
        eligibility=args.eligibility,
        verification_status=args.verification_status,
        payment_method_required=args.payment_method_required,
        country=args.country,
        limit=search.normalize_limit(args.limit),
    )
    if not search.has_filters(query):
        raise models.ValidationError(
            "filter requires at least one of --category, --offer-type, "
            "--eligibility, --status, --payment, or --country; "
            "use 'bansos list' to show everything"
        )
    resources = database.query_resources(query)
    if args.json_output:
        _print_json([resource.to_public_dict() for resource in resources])
        return EXIT_OK
    if not resources:
        print(f"{ui.mark_warn()} No resources matched the filters.")
        return EXIT_OK
    _print_resources(
        resources,
        title="Filtered catalog",
        subtitle=_filter_subtitle(args, len(resources)),
    )
    return EXIT_OK


def _changes_from_edit_args(args: argparse.Namespace) -> dict[str, Any]:
    changes = {
        field: getattr(args, field)
        for field in models.EDITABLE_FIELDS
        if getattr(args, field, None) is not None
    }
    if args.clear_expiration:
        changes["expiration_at"] = None
    if args.clear_official_terms_url:
        changes["official_terms_url"] = ""
    return changes


def _guided_edit(resource: models.Resource) -> dict[str, Any]:
    print(
        "Interactive edit. Press Enter to keep a value. "
        "Enter 'none' to clear an optional URL or timestamp.\n"
    )
    changes: dict[str, Any] = {}
    for field, label in _EDIT_PROMPTS:
        current = getattr(resource, field)
        display = ui.shorten(current, 60) if current not in (None, "") else "-"
        clear_hint = (
            " (or 'none' to clear)"
            if field in {"official_terms_url", "expiration_at"}
            else ""
        )
        raw = _ask(f"{label} [{display}]{clear_hint}: ").strip()
        if not raw:
            continue
        if field in {"official_terms_url", "expiration_at"} and raw.lower() in {
            "none",
            "clear",
            "-",
        }:
            changes[field] = "" if field == "official_terms_url" else None
        else:
            changes[field] = raw
    return changes


def _cmd_edit(database: db.Database, args: argparse.Namespace) -> int:
    resource = database.get_resource(args.id)
    changes = _changes_from_edit_args(args)
    if not changes:
        if not _stdin_is_interactive():
            raise models.ValidationError(
                "edit requires at least one field option in non-interactive "
                "mode; see 'bansos edit --help'"
            )
        changes = _guided_edit(resource)
    if not changes:
        print("No changes made.")
        return EXIT_OK
    updated = database.update_resource(resource.id, changes)
    print(
        f"{ui.mark_ok()} Updated resource #{updated.id}: "
        f"{updated.name} ({updated.provider})"
    )
    print(
        ui.theme().dim(
            "Verification fields were not changed. Use 'bansos verify ID' for those."
        )
    )
    return EXIT_OK


def _cmd_delete(database: db.Database, args: argparse.Namespace) -> int:
    resource = database.get_resource(args.id)
    if not args.yes:
        if not _stdin_is_interactive():
            raise models.ConflictError(
                "refusing to delete without confirmation; rerun with --yes "
                "in non-interactive mode"
            )
        print(
            f"Delete #{resource.id} {resource.name} ({resource.provider})?\n"
            f"Source: {resource.source_url}"
        )
        answer = _ask("Type 'yes' to permanently delete: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled. Nothing was deleted.")
            return EXIT_CANCELLED
    deleted = database.delete_resource(resource.id)
    print(f"{ui.mark_ok()} Deleted resource #{deleted.id}: {deleted.name}")
    return EXIT_OK


def _cmd_verify(database: db.Database, args: argparse.Namespace) -> int:
    evidence = args.evidence
    if evidence is None:
        if not _stdin_is_interactive():
            raise models.ValidationError(
                "--evidence is required in non-interactive mode; describe what "
                "you checked and what it showed"
            )
        evidence = _ask(
            "Verification evidence (what did you check, and what did it show?): "
        )
    resource, event = database.record_verification(
        args.id,
        args.status,
        evidence,
        args.checked_at,
    )
    status = ui.theme().paint(
        event.status.upper(), f"{ui.state_style(event.status)} bold"
    )
    print(
        f"{ui.mark_ok()} Recorded {status} verification for resource "
        f"#{resource.id} on {event.checked_at}."
    )
    print(
        ui.theme().dim(
            "Manual evidence recorded. BANSOS did not fetch the URL or independently "
            "confirm that the offer is available."
        )
    )
    return EXIT_OK


def _cmd_stale(database: db.Database, args: argparse.Namespace) -> int:
    limit = search.normalize_limit(args.limit)
    now = models.utc_now()
    stale = database.list_stale(days=args.days, now=now)
    selected = stale if limit is None else stale[:limit]
    states = {
        resource.id: verify.effective_state(
            resource, now=now, stale_days=args.days
        )
        for resource in selected
    }
    if args.json_output:
        _print_json(
            {
                "stale_days": args.days,
                "total_matching": len(stale),
                "count": len(selected),
                "resources": [
                    {
                        **resource.to_public_dict(),
                        "research_state": states[resource.id].label,
                        "review_reason": states[resource.id].detail,
                    }
                    for resource in selected
                ],
            }
        )
        return EXIT_OK
    current = ui.theme()
    width = current.total_width(100)
    if not stale:
        print(
            f"{ui.mark_ok()} No resources need re-verification under the "
            f"{args.days}-day threshold."
        )
        return EXIT_OK
    print(ui.rule("Re-verification queue", width=width))
    _print_wrapped(
        f"{len(stale)} record(s) {current.dot} {args.days}-day threshold",
        width=width,
    )
    rows = []
    for resource in selected:
        state = states[resource.id]
        checked = (resource.last_verified_at or "never")[:10]
        detail = (
            f"{resource.name}\n"
            f"{current.arrow} {resource.provider} {current.dot} {checked} "
            f"{current.dot} {state.detail}"
        )
        rows.append([resource.id, ui.state_cell(state.code, state.label), detail])
    columns = [
        ui.Column("ID", align="right", min_width=3),
        ui.Column("STATE", min_width=10),
        ui.Column("NAME", min_width=12, max_width=70, flex=True, wrap=True),
    ]
    print(
        ui.render_table(
            columns,
            rows,
            width=width,
            row_separator=True,
        )
    )
    print(ui.state_legend(width))
    if limit is not None and len(stale) > limit:
        print(
            current.dim(
                f"Showing {limit} of {len(stale)} record(s). Use --limit 0 for all."
            )
        )
    return EXIT_OK


def _cmd_stats(database: db.Database, args: argparse.Namespace) -> int:
    data = database.stats(days=args.days)
    if args.json_output:
        _print_json(data)
        return EXIT_OK

    current = ui.theme()
    width = current.total_width(110)
    total = int(data["total"])
    statuses = data["by_verification_status"]
    verified = int(statuses.get("verified", 0))
    needs_review = int(data["needs_reverification"])
    evidence = int(data["with_verification_evidence"])
    verified_pct = (100.0 * verified / total) if total else 0.0
    evidence_pct = (100.0 * evidence / total) if total else 0.0

    print(current.paint("BANSOS", "cyan bold") + " " + current.bold("CATALOG"))
    _print_wrapped(
        f"Generated {data['generated_at']} {current.dot} {total} resources",
        width=width,
    )
    print()
    print(
        ui.metric_table(
            [
                ("Resources", str(total)),
                ("Verified", f"{verified} {current.dot} {verified_pct:.0f}%"),
                ("Needs review", str(needs_review)),
                ("Evidence", f"{evidence} {current.dot} {evidence_pct:.0f}%"),
            ],
            width=width,
        )
    )

    print()
    print(ui.rule("Research status", width=width))
    segments = [
        ui.Segment(
            "verified",
            verified,
            "green",
            current.bar_filled,
        ),
        ui.Segment(
            "disputed",
            int(statuses.get("disputed", 0)),
            "magenta",
            "▓" if current.unicode else "%",
        ),
        ui.Segment(
            "stale",
            int(statuses.get("stale", 0)),
            "yellow",
            "▒" if current.unicode else "=",
        ),
        ui.Segment(
            "expired",
            int(statuses.get("expired", 0)),
            "red",
            "░" if current.unicode else "x",
        ),
        ui.Segment(
            "unverified",
            int(statuses.get("unverified", 0)),
            "cyan",
            "·" if current.unicode else ".",
        ),
    ]
    print(ui.stacked_bar(segments, min(48, max(20, width - 2))))
    legend = ui.stacked_legend(segments, width)
    if legend:
        print(legend)
    else:
        _print_wrapped("No records yet.", width=width)
    _print_wrapped(
        f"Date-expired records: {data['date_expired']} {current.dot} "
        f"stale threshold: {data['stale_days']} days",
        width=width,
    )

    def chart(
        title: str,
        values: Mapping[str, int],
        *,
        fill_style: str = "cyan",
        color_map: Optional[Mapping[str, str]] = None,
    ) -> None:
        print()
        print(ui.rule(title, width=width))
        ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
        print(
            ui.bar_chart(
                ordered,
                total=total,
                width=width,
                fill_style=fill_style,
                color_map=color_map,
            )
        )

    chart("Categories", data["by_category"])
    chart("Offer types", data["by_offer_type"], fill_style="blue")
    chart(
        "Verification status",
        data["by_verification_status"],
        color_map={code: ui.state_style(code) for code in models.VERIFICATION_STATUSES},
    )
    chart(
        "Payment requirement",
        data["by_payment_method_required"],
        color_map={"yes": "green", "no": "cyan", "unknown": "yellow"},
    )
    print()
    _print_wrapped(
        "Tip: use 'bansos filter --status disputed' to focus on contested records.",
        width=width,
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Export / import / seed
# ---------------------------------------------------------------------------


def _export_query(args: argparse.Namespace) -> search.ResourceQuery:
    return search.ResourceQuery(
        category=args.category,
        offer_type=args.offer_type,
        verification_status=args.verification_status,
        eligibility=args.eligibility,
        payment_method_required=args.payment_method_required,
        country=args.country,
        limit=None,
    )


def _csv_safe(value: Any) -> Any:
    """Neutralize spreadsheet formula prefixes in CSV cells.

    Import validation permits arbitrary text, so a note beginning with '='
    must not execute when the export is opened in a spreadsheet. JSON exports
    remain byte-for-byte lossless.
    """

    if isinstance(value, str) and value and value[0] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _csv_export(records: Sequence[Mapping[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=models.PUBLIC_FIELDS, extrasaction="raise"
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                field: _csv_safe(record.get(field))
                for field in models.PUBLIC_FIELDS
            }
        )
    return buffer.getvalue()


def _json_export(records: Sequence[Mapping[str, Any]]) -> str:
    payload = {
        "format": "bansos-resources",
        "format_version": 1,
        "schema_version": db.SCHEMA_VERSION,
        "exported_at": models.to_iso_z(models.utc_now()),
        "count": len(records),
        "resources": list(records),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=str(parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise models.BansosError(f"could not write export to {path}: {exc}") from exc


def _cmd_export(database: db.Database, args: argparse.Namespace) -> int:
    query = _export_query(args)
    records = database.export_records(query)

    output = args.output
    if output is None or output == "-":
        path = None
    else:
        path = Path(output).expanduser()

    fmt = args.format
    if fmt is None:
        if path is not None and path.suffix.lower() == ".csv":
            fmt = "csv"
        else:
            fmt = "json"

    text = _csv_export(records) if fmt == "csv" else _json_export(records)

    if path is None:
        sys.stdout.write(text)
        return EXIT_OK

    if path.exists() and not args.force:
        raise models.ConflictError(
            f"output file already exists: {path}; use --force to overwrite"
        )
    _atomic_write(path, text)
    print(
        f"{ui.mark_ok()} Exported {len(records)} resource(s) to {path} ({fmt})."
    )
    return EXIT_OK


def _read_import_input(source: str) -> list[Mapping[str, Any]]:
    if source == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            raw = buffer.read(MAX_IMPORT_BYTES + 1)
            if len(raw) > MAX_IMPORT_BYTES:
                raise models.ValidationError(
                    f"import input is larger than "
                    f"{MAX_IMPORT_BYTES // (1024 * 1024)} MiB"
                )
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise models.ValidationError(
                    f"import input on stdin is not valid UTF-8: {exc}"
                ) from exc
        else:
            text = sys.stdin.read(MAX_IMPORT_BYTES + 1)
            if len(text) > MAX_IMPORT_BYTES:
                raise models.ValidationError(
                    f"import input is larger than "
                    f"{MAX_IMPORT_BYTES // (1024 * 1024)} MiB"
                )
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise models.ValidationError(
                f"invalid JSON on stdin: line {exc.lineno}, column {exc.colno}: "
                f"{exc.msg}"
            ) from exc
    else:
        path = Path(source).expanduser()
        document = db.load_import_file(
            path, max_bytes=MAX_IMPORT_BYTES
        )
    return db.parse_import_document(document)


def _cmd_import(database: db.Database, args: argparse.Namespace) -> int:
    records = _read_import_input(args.file)
    result = database.import_records(
        records,
        on_duplicate=args.on_duplicate,
        dry_run=args.dry_run,
    )
    prefix = "Dry run (no changes written)" if args.dry_run else "Import complete"
    print(
        f"{ui.mark_ok()} {prefix}: {result.inserted} inserted, "
        f"{result.updated} updated, {result.skipped} skipped."
    )
    if args.dry_run:
        print(ui.theme().dim("Rerun without --dry-run to apply these changes."))
    return EXIT_OK


def _cmd_seed(database: db.Database, args: argparse.Namespace) -> int:
    if not args.yes:
        if not _stdin_is_interactive():
            raise models.ConflictError(
                "seed requires --yes in non-interactive mode"
            )
        print(
            "This adds two clearly fictional example.com records for exploring "
            "the UI. They are not real offers and are not research evidence."
        )
        answer = _ask("Continue? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled. Nothing was added.")
            return EXIT_CANCELLED
    result = database.import_records(list(seed.DEMO_RESOURCES), on_duplicate="skip")
    print(
        f"{ui.mark_ok()} Seed complete: {result.inserted} inserted, "
        f"{result.updated} updated, {result.skipped} skipped (already present)."
    )
    print(
        ui.theme().dim(
            "Demo records are fictional and unverified. "
            "Remove them with 'bansos delete ID --yes'."
        )
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ui.configure()
    try:
        path = _resolve_db_path(args)
        if args.command == "init":
            return _cmd_init(path)
        database = db.Database(path)
        handlers = {
            "add": _cmd_add,
            "list": _cmd_list,
            "show": _cmd_show,
            "search": _cmd_search,
            "filter": _cmd_filter,
            "edit": _cmd_edit,
            "delete": _cmd_delete,
            "verify": _cmd_verify,
            "stale": _cmd_stale,
            "stats": _cmd_stats,
            "export": _cmd_export,
            "import": _cmd_import,
            "seed": _cmd_seed,
        }
        handler = handlers.get(args.command)
        if handler is None:
            parser.error(f"unknown command: {args.command}")
        return int(handler(database, args))
    except models.DatabaseNotInitializedError as exc:
        _error(str(exc))
        return EXIT_RUNTIME
    except models.NotFoundError as exc:
        _error(str(exc))
        return EXIT_NOT_FOUND
    except models.DuplicateError as exc:
        _error(str(exc))
        return EXIT_CONFLICT
    except models.ConflictError as exc:
        _error(str(exc))
        return EXIT_CONFLICT
    except models.ValidationError as exc:
        _error(str(exc))
        return EXIT_USAGE
    except models.BansosError as exc:
        _error(str(exc))
        return EXIT_RUNTIME
    except BrokenPipeError:
        return EXIT_OK
    except sqlite3.Error as exc:
        _error(f"database error: {exc}")
        return EXIT_RUNTIME
    except OSError as exc:
        _error(f"filesystem error: {exc}")
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
