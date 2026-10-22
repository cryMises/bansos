"""Verification rules and derived research-state calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional

from . import models

DEFAULT_STALE_DAYS = 90
VERIFYABLE_STATUSES = ("verified", "stale", "expired", "disputed")


@dataclass(frozen=True)
class ResearchState:
    code: str
    label: str
    detail: str
    needs_review: bool
    days_since_verified: Optional[int] = None


def _get(resource: Any, field: str) -> Any:
    if isinstance(resource, Mapping):
        return resource.get(field)
    return getattr(resource, field)


def validate_verification(
    status: Any,
    evidence: Any,
    checked_at: Any = None,
    *,
    now: Optional[datetime] = None,
    previous_checked_at: Optional[str] = None,
) -> dict[str, str]:
    """Validate a manual verification entry.

    Evidence is mandatory. A future check date is rejected. A check date older
    than the record's current last-verified date is rejected so the latest
    evidence cannot silently move backwards.
    """

    normalized_status = models.normalize_choice(
        status, "verification_status", VERIFYABLE_STATUSES
    )
    cleaned_evidence = models.normalize_text(
        evidence,
        "verification_evidence",
        required=True,
        multiline=True,
    )
    now_value = models.as_utc(now or models.utc_now())
    checked_value = models.parse_timestamp(checked_at, "checked_at", required=False)
    if checked_value is None:
        checked_value = now_value
    checked_value = models.as_utc(checked_value)
    if checked_value > now_value:
        raise models.ValidationError("checked_at cannot be in the future")
    if previous_checked_at:
        previous = models.parse_timestamp(
            previous_checked_at, "last_verified_at", required=False
        )
        if previous is not None and checked_value < models.as_utc(previous):
            raise models.ValidationError(
                "checked_at cannot be earlier than the existing last verification date"
            )
    return {
        "verification_status": normalized_status,
        "verification_evidence": cleaned_evidence,
        "last_verified_at": models.to_iso_z(checked_value),
    }


def validate_import_verification(
    record: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Validate verification fields from an untrusted import record."""

    status = record.get("verification_status")
    evidence = record.get("verification_evidence")
    checked_at = record.get("last_verified_at")

    if status is None:
        if evidence not in (None, "") or checked_at not in (None, ""):
            raise models.ValidationError(
                "verification evidence or date provided without verification_status"
            )
        return {
            "verification_status": "unverified",
            "verification_evidence": "",
            "last_verified_at": None,
        }

    normalized_status = models.normalize_choice(
        status, "verification_status", models.VERIFICATION_STATUSES
    )
    if normalized_status == "unverified":
        if evidence not in (None, "") or checked_at not in (None, ""):
            raise models.ValidationError(
                "unverified records must not include verification evidence or a date"
            )
        return {
            "verification_status": "unverified",
            "verification_evidence": "",
            "last_verified_at": None,
        }

    if checked_at in (None, ""):
        raise models.ValidationError(
            "last_verified_at is required when verification_status is not unverified"
        )
    return validate_verification(
        normalized_status,
        evidence,
        checked_at,
        now=now,
    )


def effective_state(
    resource: Any,
    *,
    now: Optional[datetime] = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> ResearchState:
    """Return a conservative, derived state for display and stale reporting.

    The stored verification_status remains the source of record; this state
    additionally accounts for a passed expiration date and an old check date.
    """

    if not isinstance(stale_days, int) or isinstance(stale_days, bool) or stale_days < 0:
        raise models.ValidationError("stale_days must be a non-negative integer")

    now_value = models.as_utc(now or models.utc_now())
    status = str(_get(resource, "verification_status") or "unverified")
    expiration_raw = _get(resource, "expiration_at")
    last_verified_raw = _get(resource, "last_verified_at")

    try:
        expiration = models.parse_timestamp(
            expiration_raw, "expiration_at", required=False
        )
    except models.ValidationError:
        expiration = None

    days_since: Optional[int] = None
    try:
        last_verified = models.parse_timestamp(
            last_verified_raw, "last_verified_at", required=False
        )
    except models.ValidationError:
        last_verified = None
    if last_verified is not None:
        days_since = max(0, (now_value - last_verified).days)

    if expiration is not None and expiration <= now_value:
        return ResearchState(
            code="expired",
            label="EXPIRED",
            detail=f"expiration date passed ({expiration_raw})",
            needs_review=True,
            days_since_verified=days_since,
        )

    if status == "disputed":
        return ResearchState(
            code="disputed",
            label="DISPUTED",
            detail="verification evidence is disputed",
            needs_review=True,
            days_since_verified=days_since,
        )

    if status == "unverified" or last_verified is None:
        return ResearchState(
            code="unverified",
            label="UNVERIFIED",
            detail="no verification evidence has been recorded",
            needs_review=True,
            days_since_verified=days_since,
        )

    if status == "expired":
        return ResearchState(
            code="expired",
            label="EXPIRED",
            detail="record is marked expired",
            needs_review=True,
            days_since_verified=days_since,
        )

    if status == "stale":
        return ResearchState(
            code="stale",
            label="STALE",
            detail="record is marked stale",
            needs_review=True,
            days_since_verified=days_since,
        )

    cutoff = now_value - timedelta(days=stale_days)
    if last_verified < cutoff:
        return ResearchState(
            code="stale",
            label="STALE",
            detail=(
                f"last checked {days_since} day(s) ago; older than {stale_days} days"
            ),
            needs_review=True,
            days_since_verified=days_since,
        )

    return ResearchState(
        code="verified",
        label="VERIFIED",
        detail=(
            f"last checked {days_since} day(s) ago"
            if days_since is not None
            else "verification recorded"
        ),
        needs_review=False,
        days_since_verified=days_since,
    )


def needs_reverification(
    resource: Any,
    *,
    now: Optional[datetime] = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> bool:
    return effective_state(resource, now=now, stale_days=stale_days).needs_review
