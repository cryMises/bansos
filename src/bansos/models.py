"""Domain constants, data types, validation, and normalization for BANSOS.

This module has no database or command-line dependencies so it can be reused
and tested independently.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORIES = (
    "vps-cloud",
    "web-hosting",
    "domains-dns",
    "ai-apis",
    "databases-storage",
    "dev-tools-ci",
    "app-deployment",
    "education-benefits",
    "open-source-services",
    "other",
)

CATEGORY_LABELS = {
    "vps-cloud": "VPS and cloud computing",
    "web-hosting": "Free web and static hosting",
    "domains-dns": "Domains, subdomains, and DNS",
    "ai-apis": "AI APIs and free model access",
    "databases-storage": "Managed databases and storage",
    "dev-tools-ci": "Developer tools and CI/CD",
    "app-deployment": "App deployment platforms",
    "education-benefits": "Student and education benefits",
    "open-source-services": "Open-source software and services",
    "other": "Other",
}

OFFER_TYPES = (
    "always_free",
    "free_tier",
    "trial",
    "promotional_credit",
    "educational_benefit",
    "open_source",
    "unknown",
)

PAYMENT_CHOICES = ("yes", "no", "unknown")

VERIFICATION_STATUSES = (
    "unverified",
    "verified",
    "stale",
    "expired",
    "disputed",
)

# Fields shown in the CLI and written to exports. Internal normalization
# columns are deliberately excluded.
PUBLIC_FIELDS = (
    "id",
    "name",
    "provider",
    "category",
    "description",
    "offer_type",
    "pricing_notes",
    "included_limits",
    "eligibility_requirements",
    "payment_method_required",
    "country_availability",
    "source_url",
    "official_terms_url",
    "first_seen_at",
    "last_verified_at",
    "expiration_at",
    "verification_status",
    "verification_evidence",
    "notes",
    "created_at",
    "updated_at",
)

# Fields accepted from callers when creating a record. Verification is
# intentionally separate: new resources always start unverified.
RESOURCE_INPUT_FIELDS = (
    "name",
    "provider",
    "category",
    "description",
    "offer_type",
    "pricing_notes",
    "included_limits",
    "eligibility_requirements",
    "payment_method_required",
    "country_availability",
    "source_url",
    "official_terms_url",
    "first_seen_at",
    "expiration_at",
    "notes",
)

EDITABLE_FIELDS = RESOURCE_INPUT_FIELDS

# Normalized keys are stored alongside the public fields for duplicate checks.
RESOURCE_COLUMNS = PUBLIC_FIELDS + (
    "normalized_name",
    "normalized_provider",
    "normalized_source_url",
)

MAX_LENGTHS = {
    "name": 200,
    "provider": 200,
    "category": 50,
    "description": 5_000,
    "offer_type": 50,
    "pricing_notes": 5_000,
    "included_limits": 5_000,
    "eligibility_requirements": 5_000,
    "payment_method_required": 20,
    "country_availability": 200,
    "source_url": 2_048,
    "official_terms_url": 2_048,
    "verification_evidence": 10_000,
    "notes": 10_000,
}

# Fields where newlines and tabs are meaningful.
MULTILINE_FIELDS = {
    "description",
    "pricing_notes",
    "included_limits",
    "eligibility_requirements",
    "verification_evidence",
    "notes",
}

LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "gmbh",
    "co",
    "company",
}

_LEGAL_SUFFIX_RE = re.compile(
    r"(?:\s+(?:" + "|".join(sorted(LEGAL_SUFFIXES)) + r"))+$",
    re.IGNORECASE,
)
_PUNCTUATION_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BansosError(Exception):
    """Base class for expected, user-facing BANSOS errors."""


class ValidationError(BansosError):
    """Input or imported data failed validation."""


class NotFoundError(BansosError):
    """A requested record does not exist."""


class DuplicateError(BansosError):
    """A resource conflicts with an existing record."""

    def __init__(self, message: str, existing_ids: Optional[list[int]] = None):
        super().__init__(message)
        self.existing_ids = existing_ids or []


class ConflictError(BansosError):
    """An operation conflicts with existing state."""


class DatabaseNotInitializedError(BansosError):
    """The database file is missing or does not contain the expected schema."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resource:
    id: int
    name: str
    provider: str
    category: str
    description: str
    offer_type: str
    pricing_notes: str
    included_limits: str
    eligibility_requirements: str
    payment_method_required: str
    country_availability: str
    source_url: str
    official_terms_url: str
    first_seen_at: str
    last_verified_at: Optional[str]
    expiration_at: Optional[str]
    verification_status: str
    verification_evidence: str
    notes: str
    created_at: str
    updated_at: str
    normalized_name: str
    normalized_provider: str
    normalized_source_url: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Resource":
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})

    def to_public_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in PUBLIC_FIELDS}

    def display_name(self) -> str:
        return f"#{self.id} {self.name}"


@dataclass(frozen=True)
class VerificationEvent:
    id: int
    resource_id: int
    checked_at: str
    status: str
    evidence: str
    created_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "VerificationEvent":
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "checked_at": self.checked_at,
            "status": self.status,
            "evidence": self.evidence,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Time helpers (all persisted timestamps are UTC with a trailing Z)
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime; naive values are treated as UTC."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def to_iso_z(value: datetime) -> str:
    value = as_utc(value)
    return value.isoformat().replace("+00:00", "Z")


def parse_timestamp(
    value: Any,
    field: str = "timestamp",
    *,
    required: bool = False,
) -> Optional[datetime]:
    """Parse an ISO 8601 date or datetime and return an aware UTC datetime.

    Date-only values are interpreted as midnight UTC. Naive datetimes are
    treated as UTC. Fractional seconds are truncated.
    """

    if value is None or value == "":
        if required:
            raise ValidationError(f"{field} is required")
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            if required:
                raise ValidationError(f"{field} is required")
            return None
        try:
            if _DATE_ONLY_RE.match(raw):
                day = date.fromisoformat(raw)
                parsed = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            else:
                # fromisoformat() did not accept a trailing Z before 3.11.
                if raw.endswith(("Z", "z")):
                    raw = raw[:-1] + "+00:00"
                parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(
                f"{field} must be an ISO 8601 date or datetime, e.g. 2026-01-31 or "
                f"2026-01-31T12:00:00Z"
            ) from exc
    else:
        raise ValidationError(f"{field} must be a string containing an ISO 8601 timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Text, choice, and URL validation
# ---------------------------------------------------------------------------


def normalize_text(
    value: Any,
    field: str,
    *,
    required: bool = False,
    max_length: Optional[int] = None,
    multiline: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    text = unicodedata.normalize("NFC", value).strip()
    if required and not text:
        raise ValidationError(f"{field} is required")
    limit = max_length if max_length is not None else MAX_LENGTHS.get(field, 10_000)
    if len(text) > limit:
        raise ValidationError(f"{field} must be at most {limit} characters")
    for character in text:
        category = unicodedata.category(character)
        if category in {"Cs"}:
            raise ValidationError(f"{field} contains an invalid Unicode surrogate")
        if category in {"Cc", "Cf"}:
            if multiline and character in {"\n", "\t"}:
                continue
            raise ValidationError(f"{field} contains a disallowed control character")
        if not multiline and character in {"\n", "\t"}:
            raise ValidationError(f"{field} must be a single line")
    return text


def normalize_choice(value: Any, field: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    normalized = value.strip().lower()
    if normalized not in choices:
        raise ValidationError(
            f"{field} must be one of: {', '.join(choices)} (got {value!r})"
        )
    return normalized


def validate_url(value: Any, field: str, *, required: bool = False) -> str:
    text = normalize_text(value, field, required=required, multiline=False)
    if not text:
        return ""
    if any(character.isspace() for character in text):
        raise ValidationError(f"{field} must not contain whitespace")
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid URL") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValidationError(f"{field} must use http:// or https://")
    if not parts.netloc or not hostname:
        raise ValidationError(f"{field} must include a host name")
    if parts.username is not None or parts.password is not None:
        raise ValidationError(f"{field} must not contain embedded credentials")
    if port is not None and not (0 < port < 65_536):
        raise ValidationError(f"{field} contains an invalid port")
    return text


# ---------------------------------------------------------------------------
# Duplicate-key normalization
# ---------------------------------------------------------------------------


def _normalized_words(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    if not normalized:
        raise ValidationError("resource identity fields must not normalize to empty")
    return normalized


def normalize_name(value: str) -> str:
    return _normalized_words(value)


def normalize_provider(value: str) -> str:
    normalized = _normalized_words(value)
    stripped = _LEGAL_SUFFIX_RE.sub("", normalized).strip()
    return stripped or normalized


def normalize_source_url(url: str) -> str:
    """Build a stable duplicate-detection key without making network requests."""

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower().rstrip(".")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parts.port
    except ValueError as exc:
        raise ValidationError("source_url contains an invalid port") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname
    if port is not None and not default_port:
        netloc = f"{hostname}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    # Fragments are ignored for identity; query strings are preserved because
    # they can select different offers or terms.
    return urlunsplit((scheme, netloc, path, parts.query, ""))


# ---------------------------------------------------------------------------
# Resource validation
# ---------------------------------------------------------------------------


def _clean_required_text(data: Mapping[str, Any], field: str, *, multiline: bool = False) -> str:
    return normalize_text(
        data.get(field, ""),
        field,
        required=True,
        multiline=multiline,
    )


def _clean_optional_text(data: Mapping[str, Any], field: str, *, multiline: bool = False) -> str:
    return normalize_text(
        data.get(field, ""),
        field,
        required=False,
        multiline=multiline,
    )


def validate_new_resource(
    data: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Validate untrusted input and return a complete insertable record.

    The caller must not provide verification fields; new records are always
    unverified. Imported verification metadata is handled separately.
    """

    if not isinstance(data, Mapping):
        raise ValidationError("resource must be an object")

    unknown = sorted(set(data) - set(RESOURCE_INPUT_FIELDS))
    if unknown:
        raise ValidationError(f"unknown resource field(s): {', '.join(unknown)}")

    now_value = now or utc_now()
    now_iso = to_iso_z(now_value)

    name = _clean_required_text(data, "name")
    provider = _clean_required_text(data, "provider")
    category = normalize_choice(data.get("category", ""), "category", CATEGORIES)
    description = _clean_optional_text(data, "description", multiline=True)
    offer_type = normalize_choice(
        data.get("offer_type") or "unknown", "offer_type", OFFER_TYPES
    )
    pricing_notes = _clean_optional_text(data, "pricing_notes", multiline=True)
    included_limits = _clean_optional_text(data, "included_limits", multiline=True)
    eligibility = _clean_optional_text(
        data, "eligibility_requirements", multiline=True
    )
    payment = normalize_choice(
        data.get("payment_method_required") or "unknown",
        "payment_method_required",
        PAYMENT_CHOICES,
    )
    country = (_clean_optional_text(data, "country_availability") or "unknown")
    source_url = validate_url(data.get("source_url", ""), "source_url", required=True)
    terms_url = validate_url(
        data.get("official_terms_url", ""), "official_terms_url", required=False
    )
    first_seen = parse_timestamp(
        data.get("first_seen_at"), "first_seen_at", required=False
    ) or now_value
    expiration = parse_timestamp(
        data.get("expiration_at"), "expiration_at", required=False
    )
    notes = _clean_optional_text(data, "notes", multiline=True)

    try:
        normalized_provider = normalize_provider(provider)
        normalized_name = normalize_name(name)
        normalized_source_url = normalize_source_url(source_url)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError(f"could not normalize resource identity: {exc}") from exc

    return {
        "name": name,
        "provider": provider,
        "category": category,
        "description": description,
        "offer_type": offer_type,
        "pricing_notes": pricing_notes,
        "included_limits": included_limits,
        "eligibility_requirements": eligibility,
        "payment_method_required": payment,
        "country_availability": country,
        "source_url": source_url,
        "official_terms_url": terms_url,
        "first_seen_at": to_iso_z(first_seen),
        "last_verified_at": None,
        "expiration_at": to_iso_z(expiration) if expiration else None,
        "verification_status": "unverified",
        "verification_evidence": "",
        "notes": notes,
        "created_at": now_iso,
        "updated_at": now_iso,
        "normalized_name": normalized_name,
        "normalized_provider": normalized_provider,
        "normalized_source_url": normalized_source_url,
    }


def validate_update_fields(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a partial edit. Verification changes are not permitted here."""

    if not isinstance(changes, Mapping):
        raise ValidationError("changes must be an object")
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValidationError(f"unknown or non-editable field(s): {', '.join(unknown)}")
    if not changes:
        return {}

    cleaned: dict[str, Any] = {}

    for field in ("name", "provider"):
        if field in changes:
            cleaned[field] = _clean_required_text(changes, field)
    if "category" in changes:
        cleaned["category"] = normalize_choice(changes["category"], "category", CATEGORIES)
    for field in (
        "description",
        "pricing_notes",
        "included_limits",
        "eligibility_requirements",
        "notes",
    ):
        if field in changes:
            cleaned[field] = _clean_optional_text(changes, field, multiline=True)
    if "offer_type" in changes:
        cleaned["offer_type"] = normalize_choice(
            changes["offer_type"], "offer_type", OFFER_TYPES
        )
    if "payment_method_required" in changes:
        cleaned["payment_method_required"] = normalize_choice(
            changes["payment_method_required"],
            "payment_method_required",
            PAYMENT_CHOICES,
        )
    if "country_availability" in changes:
        cleaned["country_availability"] = (
            _clean_optional_text(changes, "country_availability") or "unknown"
        )
    if "source_url" in changes:
        cleaned["source_url"] = validate_url(
            changes["source_url"], "source_url", required=True
        )
    if "official_terms_url" in changes:
        cleaned["official_terms_url"] = validate_url(
            changes["official_terms_url"], "official_terms_url", required=False
        )
    if "first_seen_at" in changes:
        parsed = parse_timestamp(
            changes["first_seen_at"], "first_seen_at", required=True
        )
        cleaned["first_seen_at"] = to_iso_z(parsed)
    if "expiration_at" in changes:
        parsed = parse_timestamp(
            changes["expiration_at"], "expiration_at", required=False
        )
        cleaned["expiration_at"] = to_iso_z(parsed) if parsed else None

    if "name" in cleaned:
        cleaned["normalized_name"] = normalize_name(cleaned["name"])
    if "provider" in cleaned:
        cleaned["normalized_provider"] = normalize_provider(cleaned["provider"])
    if "source_url" in cleaned:
        cleaned["normalized_source_url"] = normalize_source_url(cleaned["source_url"])

    return cleaned
