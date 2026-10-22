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

from . import __version__, db, models, search, seed, verify

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
    print(f"error: {message}", file=sys.stderr)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _shorten(value: Any, width: int) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split())
    if len(text) > width:
        if width <= 3:
            return text[:width]
        return text[: width - 3] + "..."
    return text


def _print_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    widths: Sequence[int],
) -> None:
    def render(values: Sequence[Any]) -> str:
        cells = []
        for value, width in zip(values, widths):
            cells.append(_shorten(value, width).ljust(width))
        return "  ".join(cells).rstrip()

    print(render(headers))
    print("  ".join("-" * width for width in widths).rstrip())
    for row in rows:
        print(render(row))


def _resource_rows(resources: Sequence[models.Resource]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for resource in resources:
        state = verify.effective_state(resource)
        rows.append(
            [
                resource.id,
                state.label,
                resource.name,
                resource.provider,
                (resource.last_verified_at or "never")[:10],
            ]
        )
    return rows


def _print_resources(resources: Sequence[models.Resource]) -> None:
    _print_table(
        ("ID", "STATE", "NAME", "PROVIDER", "CHECKED"),
        _resource_rows(resources),
        (4, 10, 28, 20, 10),
    )


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
    resource: models.Resource, state: verify.ResearchState
) -> None:
    if state.code == "unverified":
        print(
            "\nWARNING: this record has no verification evidence. "
            "Use 'bansos verify ID --evidence ...' after manual research."
        )
    elif state.code == "expired":
        print(f"\nWARNING: {state.detail}. Verify before relying on this record.")
    elif state.code == "stale":
        print(f"\nWARNING: {state.detail}. Re-check the official source.")
    elif state.code == "disputed":
        print("\nWARNING: the verification evidence is marked disputed.")


def _print_detail_field(label: str, value: Any, width: int = 24) -> None:
    text = "" if value is None else str(value)
    lines = text.splitlines() or [""]
    prefix = f"{label + ':':<{width}}"
    print(f"{prefix} {lines[0] if lines[0] else '-'}")
    for line in lines[1:]:
        print(f"{'':<{width}} {line}")


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

    print(f"BANSOS resource #{resource.id}")
    print("=" * 32)
    _print_detail_field(
        "Name",
        resource.name,
    )
    _print_detail_field("Provider", resource.provider)
    _print_detail_field(
        "Category",
        f"{resource.category} ({models.CATEGORY_LABELS.get(resource.category, 'unknown')})",
    )
    _print_detail_field("Offer type", resource.offer_type)
    _print_detail_field("Payment required", resource.payment_method_required)
    _print_detail_field("Country availability", resource.country_availability)
    _print_detail_field("Description", resource.description)
    _print_detail_field("Pricing notes", resource.pricing_notes)
    _print_detail_field("Included limits", resource.included_limits)
    _print_detail_field("Eligibility", resource.eligibility_requirements)
    _print_detail_field("Source URL", resource.source_url)
    _print_detail_field("Official terms URL", resource.official_terms_url)
    _print_detail_field("First seen", resource.first_seen_at)
    _print_detail_field("Last verified", resource.last_verified_at or "never")
    _print_detail_field("Expiration", resource.expiration_at or "none recorded")
    _print_detail_field("Verification status", resource.verification_status)
    _print_detail_field("Research state", f"{state.label} - {state.detail}")
    _print_detail_field("Verification evidence", resource.verification_evidence)
    _print_detail_field("Research notes", resource.notes)
    _print_detail_field("Created", resource.created_at)
    _print_detail_field("Updated", resource.updated_at)

    _print_verification_warning(resource, state)

    if events:
        print("\nVerification history:")
        for event in events:
            print(
                f"- {event.checked_at}  {event.status.upper()}  "
                f"(recorded {event.created_at})"
            )
            for line in event.evidence.splitlines():
                print(f"    {line}")
    else:
        print("\nVerification history: none")
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
    if result.created:
        print(f"Initialized BANSOS database at {result.path}")
    else:
        print(f"BANSOS database ready at {result.path}")
    print(f"Schema version: {result.schema_version}")
    if result.applied_versions:
        print(
            "Applied migration(s): "
            + ", ".join(str(version) for version in result.applied_versions)
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
    print(f"Added resource #{resource.id}: {resource.name} ({resource.provider})")
    print(
        "Recorded as unverified. After manual research, run "
        f"'bansos verify {resource.id} --evidence ...'."
    )
    return EXIT_OK


def _cmd_list(database: db.Database, args: argparse.Namespace) -> int:
    limit = search.normalize_limit(args.limit)
    resources = database.query_resources(search.ResourceQuery(limit=limit))
    if args.json_output:
        _print_json([resource.to_public_dict() for resource in resources])
        return EXIT_OK
    if not resources:
        print("No resources found. Add one with 'bansos add'.")
        return EXIT_OK
    _print_resources(resources)
    if limit is not None and len(resources) == limit:
        print(
            f"\nShowing up to {limit} record(s). Use --limit 0 to show all."
        )
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
        print(f"No resources matched {text!r}.")
        return EXIT_OK
    print(f"Found {len(resources)} matching resource(s):\n")
    _print_resources(resources)
    return EXIT_OK


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
        print("No resources matched the filters.")
        return EXIT_OK
    print(f"Found {len(resources)} matching resource(s):\n")
    _print_resources(resources)
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
        display = _shorten(current, 60) if current not in (None, "") else "-"
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
    print(f"Updated resource #{updated.id}: {updated.name} ({updated.provider})")
    print("Verification fields were not changed. Use 'bansos verify ID' for those.")
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
    print(f"Deleted resource #{deleted.id}: {deleted.name}")
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
    print(
        f"Recorded {event.status.upper()} verification for resource "
        f"#{resource.id} on {event.checked_at}."
    )
    print(
        "Manual evidence recorded. BANSOS did not fetch the URL or independently "
        "confirm that the offer is available."
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
    if not stale:
        print(
            f"No resources need re-verification under the {args.days}-day threshold."
        )
        return EXIT_OK
    print(
        f"{len(stale)} resource(s) need re-verification "
        f"({args.days}-day threshold):\n"
    )
    rows = []
    for resource in selected:
        state = states[resource.id]
        rows.append(
            [
                resource.id,
                resource.name,
                resource.provider,
                (resource.last_verified_at or "never")[:10],
                f"{state.label}: {state.detail}",
            ]
        )
    _print_table(
        ("ID", "NAME", "PROVIDER", "CHECKED", "REASON"),
        rows,
        (4, 28, 20, 10, 38),
    )
    if limit is not None and len(stale) > limit:
        print(
            f"\nShowing {limit} of {len(stale)} record(s). Use --limit 0 for all."
        )
    return EXIT_OK


def _cmd_stats(database: db.Database, args: argparse.Namespace) -> int:
    data = database.stats(days=args.days)
    if args.json_output:
        _print_json(data)
        return EXIT_OK

    print("BANSOS statistics")
    print("=================")
    print(f"Database generated at: {data['generated_at']}")
    print(f"Total resources: {data['total']}")
    print(
        f"Needs re-verification (>{data['stale_days']} days, never verified, "
        f"stale, expired, or disputed): {data['needs_reverification']}"
    )
    print(f"Date-expired records: {data['date_expired']}")
    print(f"Records with verification evidence: {data['with_verification_evidence']}")

    def section(title: str, values: Mapping[str, int]) -> None:
        print(f"\n{title}:")
        for name, count in values.items():
            print(f"  {name:<28} {count}")

    section("By category", data["by_category"])
    section("By offer type", data["by_offer_type"])
    section("By stored verification status", data["by_verification_status"])
    section("By payment method requirement", data["by_payment_method_required"])
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
    print(f"Exported {len(records)} resource(s) to {path} ({fmt}).")
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
        f"{prefix}: {result.inserted} inserted, {result.updated} updated, "
        f"{result.skipped} skipped."
    )
    if args.dry_run:
        print("Rerun without --dry-run to apply these changes.")
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
        f"Seed complete: {result.inserted} inserted, {result.updated} updated, "
        f"{result.skipped} skipped (already present)."
    )
    print("Demo records are fictional and unverified. Remove them with 'bansos delete ID --yes'.")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
