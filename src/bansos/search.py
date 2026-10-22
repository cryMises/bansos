"""Search and filter query construction for BANSOS.

Only fixed SQL fragments and bound parameters are produced here; user input is
never interpolated into SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import models

SEARCH_COLUMNS = ("name", "provider", "description", "notes")
MAX_SEARCH_TERMS = 10
MAX_SEARCH_TEXT = 200
MAX_RESULT_LIMIT = 100_000


@dataclass(frozen=True)
class ResourceQuery:
    """A combined text search and structured filter."""

    text: Optional[str] = None
    category: Optional[str] = None
    offer_type: Optional[str] = None
    eligibility: Optional[str] = None
    verification_status: Optional[str] = None
    payment_method_required: Optional[str] = None
    country: Optional[str] = None
    limit: Optional[int] = None


def _search_terms(text: Any) -> list[str]:
    if text is None:
        return []
    cleaned = models.normalize_text(
        text, "search query", required=False, multiline=False
    )
    if not cleaned:
        return []
    if len(cleaned) > MAX_SEARCH_TEXT:
        raise models.ValidationError(
            f"search query must be at most {MAX_SEARCH_TEXT} characters"
        )
    terms = cleaned.split()
    if len(terms) > MAX_SEARCH_TERMS:
        raise models.ValidationError(
            f"search query may contain at most {MAX_SEARCH_TERMS} terms"
        )
    return terms


def _optional_filter_text(value: Any, field: str) -> str:
    cleaned = models.normalize_text(value, field, required=False, multiline=False)
    if len(cleaned) > 200:
        raise models.ValidationError(f"{field} filter must be at most 200 characters")
    return cleaned


def normalize_limit(limit: Optional[int]) -> Optional[int]:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise models.ValidationError("limit must be an integer")
    if limit < 0:
        raise models.ValidationError("limit must be zero or greater")
    if limit > MAX_RESULT_LIMIT:
        raise models.ValidationError(f"limit must be at most {MAX_RESULT_LIMIT}")
    return None if limit == 0 else limit


def build_where(query: ResourceQuery) -> tuple[str, list[Any]]:
    """Return a WHERE clause and ordered bound parameters for a query."""

    if not isinstance(query, ResourceQuery):
        raise models.ValidationError("query must be a ResourceQuery")

    clauses: list[str] = []
    parameters: list[Any] = []

    for term in _search_terms(query.text):
        clauses.append(
            "("
            + " OR ".join(
                f"instr(bansos_casefold({column}), bansos_casefold(?)) > 0"
                for column in SEARCH_COLUMNS
            )
            + ")"
        )
        parameters.extend([term] * len(SEARCH_COLUMNS))

    if query.category is not None:
        category = models.normalize_choice(query.category, "category", models.CATEGORIES)
        clauses.append("category = ?")
        parameters.append(category)

    if query.offer_type is not None:
        offer_type = models.normalize_choice(
            query.offer_type, "offer_type", models.OFFER_TYPES
        )
        clauses.append("offer_type = ?")
        parameters.append(offer_type)

    if query.verification_status is not None:
        status = models.normalize_choice(
            query.verification_status,
            "verification_status",
            models.VERIFICATION_STATUSES,
        )
        clauses.append("verification_status = ?")
        parameters.append(status)

    if query.payment_method_required is not None:
        payment = models.normalize_choice(
            query.payment_method_required,
            "payment_method_required",
            models.PAYMENT_CHOICES,
        )
        clauses.append("payment_method_required = ?")
        parameters.append(payment)

    if query.eligibility is not None:
        eligibility = _optional_filter_text(query.eligibility, "eligibility")
        if eligibility:
            clauses.append(
                "instr(bansos_casefold(eligibility_requirements), "
                "bansos_casefold(?)) > 0"
            )
            parameters.append(eligibility)

    if query.country is not None:
        country = _optional_filter_text(query.country, "country")
        if country:
            clauses.append(
                "instr(bansos_casefold(country_availability), "
                "bansos_casefold(?)) > 0"
            )
            parameters.append(country)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, parameters


def has_filters(query: ResourceQuery) -> bool:
    if any(
        value is not None
        for value in (
            query.category,
            query.offer_type,
            query.verification_status,
            query.payment_method_required,
        )
    ):
        return True
    return any(
        isinstance(value, str) and bool(value.strip())
        for value in (query.text, query.eligibility, query.country)
    )
