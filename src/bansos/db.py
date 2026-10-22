"""SQLite persistence for BANSOS.

All SQL is parameterized. Connections are short-lived, foreign keys are
enabled, and writes use explicit transactions.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence, Union

from . import models, search, verify

SCHEMA_VERSION = 1
MAX_IMPORT_RECORDS = 5_000

_MIGRATION_1 = (
    """
    CREATE TABLE resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
        provider TEXT NOT NULL CHECK (length(trim(provider)) BETWEEN 1 AND 200),
        category TEXT NOT NULL CHECK (
            category IN (
                'vps-cloud', 'web-hosting', 'domains-dns', 'ai-apis',
                'databases-storage', 'dev-tools-ci', 'app-deployment',
                'education-benefits', 'open-source-services', 'other'
            )
        ),
        description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 5000),
        offer_type TEXT NOT NULL DEFAULT 'unknown' CHECK (
            offer_type IN (
                'always_free', 'free_tier', 'trial', 'promotional_credit',
                'educational_benefit', 'open_source', 'unknown'
            )
        ),
        pricing_notes TEXT NOT NULL DEFAULT '' CHECK (length(pricing_notes) <= 5000),
        included_limits TEXT NOT NULL DEFAULT '' CHECK (length(included_limits) <= 5000),
        eligibility_requirements TEXT NOT NULL DEFAULT ''
            CHECK (length(eligibility_requirements) <= 5000),
        payment_method_required TEXT NOT NULL DEFAULT 'unknown'
            CHECK (payment_method_required IN ('yes', 'no', 'unknown')),
        country_availability TEXT NOT NULL DEFAULT 'unknown'
            CHECK (length(country_availability) <= 200),
        source_url TEXT NOT NULL CHECK (length(source_url) BETWEEN 1 AND 2048),
        official_terms_url TEXT NOT NULL DEFAULT ''
            CHECK (length(official_terms_url) <= 2048),
        first_seen_at TEXT NOT NULL CHECK (length(trim(first_seen_at)) > 0),
        last_verified_at TEXT,
        expiration_at TEXT,
        verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (
            verification_status IN (
                'unverified', 'verified', 'stale', 'expired', 'disputed'
            )
        ),
        verification_evidence TEXT NOT NULL DEFAULT ''
            CHECK (length(verification_evidence) <= 10000),
        notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 10000),
        normalized_name TEXT NOT NULL CHECK (length(normalized_name) > 0),
        normalized_provider TEXT NOT NULL CHECK (length(normalized_provider) > 0),
        normalized_source_url TEXT NOT NULL CHECK (length(normalized_source_url) > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (length(trim(created_at)) > 0),
        CHECK (length(trim(updated_at)) > 0),
        CHECK (
            verification_status = 'unverified'
            OR (
                last_verified_at IS NOT NULL
                AND length(trim(last_verified_at)) > 0
                AND length(trim(verification_evidence)) > 0
            )
        )
    )
    """,
    "CREATE UNIQUE INDEX ux_resources_provider_name "
    "ON resources(normalized_provider, normalized_name)",
    "CREATE UNIQUE INDEX ux_resources_source_url "
    "ON resources(normalized_source_url)",
    "CREATE INDEX ix_resources_category ON resources(category)",
    "CREATE INDEX ix_resources_offer_type ON resources(offer_type)",
    "CREATE INDEX ix_resources_verification_status "
    "ON resources(verification_status)",
    "CREATE INDEX ix_resources_last_verified_at ON resources(last_verified_at)",
    "CREATE INDEX ix_resources_expiration_at ON resources(expiration_at)",
    "CREATE INDEX ix_resources_normalized_provider "
    "ON resources(normalized_provider)",
    """
    CREATE TABLE verification_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_id INTEGER NOT NULL
            REFERENCES resources(id) ON DELETE CASCADE,
        checked_at TEXT NOT NULL CHECK (length(trim(checked_at)) > 0),
        status TEXT NOT NULL CHECK (
            status IN ('verified', 'stale', 'expired', 'disputed')
        ),
        evidence TEXT NOT NULL CHECK (length(trim(evidence)) > 0),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_verification_events_resource "
    "ON verification_events(resource_id, checked_at DESC, id DESC)",
)

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, _MIGRATION_1),)


@dataclass(frozen=True)
class InitializeResult:
    path: Path
    created: bool
    applied_versions: tuple[int, ...]
    schema_version: int


@dataclass(frozen=True)
class ImportResult:
    inserted: int
    updated: int
    skipped: int


@dataclass(frozen=True)
class _PreparedImport:
    data: dict[str, Any]
    has_verification: bool


class Database:
    """A small SQLite repository. Instantiate with a filesystem path."""

    def __init__(self, path: Union[Path, str]):
        if not isinstance(path, (str, Path)):
            raise models.ValidationError("database path must be a string or Path")
        raw = str(path).strip()
        if not raw:
            raise models.ValidationError("database path must not be empty")
        self.path = Path(raw).expanduser()

    # ------------------------------------------------------------------
    # Connection and schema management
    # ------------------------------------------------------------------

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        # SQLite's built-in lower() only handles ASCII. This deterministic
        # function provides Unicode-aware case-insensitive search without a
        # third-party dependency.
        connection.create_function(
            "bansos_casefold",
            1,
            lambda value: value.casefold() if isinstance(value, str) else value,
            deterministic=True,
        )
        return connection

    def _version(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if row is None:
            return 0
        version_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        return int(version_row["version"]) if version_row is not None else 0

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.exists():
            raise models.DatabaseNotInitializedError(
                f"database not found at {self.path}; run 'bansos init' first"
            )
        connection = self._new_connection()
        try:
            version = self._version(connection)
            if version == 0:
                raise models.DatabaseNotInitializedError(
                    f"{self.path} is not an initialized BANSOS database; "
                    "run 'bansos init' first"
                )
            if version < SCHEMA_VERSION:
                raise models.DatabaseNotInitializedError(
                    f"database schema is version {version}, but this BANSOS "
                    f"version expects {SCHEMA_VERSION}; run 'bansos init' to migrate"
                )
            if version > SCHEMA_VERSION:
                raise models.ConflictError(
                    f"database schema version {version} is newer than this "
                    f"application supports ({SCHEMA_VERSION})"
                )
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> InitializeResult:
        """Create or migrate the database. Safe to run repeatedly."""

        existed = self.path.exists()
        parent = self.path.parent
        parent_existed = parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            try:
                parent.chmod(0o700)
            except OSError:
                pass

        connection = self._new_connection()
        try:
            # The default rollback journal is the most portable choice for
            # Android/Termux filesystems and keeps backups easy to reason about.
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

            current = self._version(connection)
            if current > SCHEMA_VERSION:
                raise models.ConflictError(
                    f"database schema version {current} is newer than this "
                    f"application supports ({SCHEMA_VERSION})"
                )

            applied: list[int] = []
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (version, models.to_iso_z(models.utc_now())),
                    )
                    connection.execute(f"PRAGMA user_version = {int(version)}")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                applied.append(version)

            version = self._version(connection)
            if version != SCHEMA_VERSION:
                raise models.ConflictError(
                    f"database is at schema version {version}, expected {SCHEMA_VERSION}"
                )
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
            return InitializeResult(
                path=self.path,
                created=not existed,
                applied_versions=tuple(applied),
                schema_version=version,
            )
        except sqlite3.DatabaseError as exc:
            raise models.BansosError(
                f"could not initialize database at {self.path}: {exc}"
            ) from exc
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_resource(row: sqlite3.Row) -> models.Resource:
        return models.Resource.from_row(row)

    @staticmethod
    def _check_id(resource_id: Any) -> int:
        if isinstance(resource_id, bool) or not isinstance(resource_id, int):
            raise models.ValidationError("resource ID must be an integer")
        if resource_id < 1:
            raise models.ValidationError("resource ID must be a positive integer")
        return resource_id

    def _get_resource(
        self, connection: sqlite3.Connection, resource_id: int
    ) -> models.Resource:
        row = connection.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            raise models.NotFoundError(f"resource #{resource_id} does not exist")
        return self._row_to_resource(row)

    def _find_duplicates(
        self,
        connection: sqlite3.Connection,
        normalized_provider: str,
        normalized_name: str,
        normalized_source_url: str,
        *,
        exclude_id: Optional[int] = None,
    ) -> list[models.Resource]:
        sql = (
            "SELECT * FROM resources WHERE "
            "((normalized_provider = ? AND normalized_name = ?) "
            "OR normalized_source_url = ?)"
        )
        parameters: list[Any] = [
            normalized_provider,
            normalized_name,
            normalized_source_url,
        ]
        if exclude_id is not None:
            sql += " AND id != ?"
            parameters.append(exclude_id)
        sql += " ORDER BY id"
        return [
            self._row_to_resource(row)
            for row in connection.execute(sql, parameters).fetchall()
        ]

    @staticmethod
    def _duplicate_error(duplicates: Sequence[models.Resource]) -> models.DuplicateError:
        details = "; ".join(
            f"#{item.id} {item.name} / {item.provider}" for item in duplicates
        )
        return models.DuplicateError(
            "duplicate resource detected (same normalized provider+name or source "
            f"URL): {details} | use 'bansos show ID' or 'bansos edit ID'",
            [item.id for item in duplicates],
        )

    @staticmethod
    def _insert_prepared(
        connection: sqlite3.Connection, data: Mapping[str, Any]
    ) -> int:
        columns = list(data.keys())
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        try:
            cursor = connection.execute(
                f"INSERT INTO resources ({column_sql}) VALUES ({placeholders})",
                [data[column] for column in columns],
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise models.DuplicateError(
                    "duplicate resource detected while saving the record"
                ) from exc
            raise models.ValidationError(
                f"database rejected the resource: {exc}"
            ) from exc
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_verification_event(
        connection: sqlite3.Connection,
        resource_id: int,
        checked_at: str,
        status: str,
        evidence: str,
        created_at: str,
    ) -> Optional[int]:
        existing = connection.execute(
            "SELECT id FROM verification_events "
            "WHERE resource_id = ? AND checked_at = ? AND status = ? AND evidence = ?",
            (resource_id, checked_at, status, evidence),
        ).fetchone()
        if existing is not None:
            return None
        cursor = connection.execute(
            "INSERT INTO verification_events"
            "(resource_id, checked_at, status, evidence, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (resource_id, checked_at, status, evidence, created_at),
        )
        return int(cursor.lastrowid)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_resource(self, data: Mapping[str, Any]) -> models.Resource:
        validated = models.validate_new_resource(data)
        with self._transaction() as connection:
            duplicates = self._find_duplicates(
                connection,
                validated["normalized_provider"],
                validated["normalized_name"],
                validated["normalized_source_url"],
            )
            if duplicates:
                raise self._duplicate_error(duplicates)
            resource_id = self._insert_prepared(connection, validated)
            return self._get_resource(connection, resource_id)

    def get_resource(self, resource_id: int) -> models.Resource:
        checked_id = self._check_id(resource_id)
        with self._connect() as connection:
            return self._get_resource(connection, checked_id)

    def update_resource(
        self, resource_id: int, changes: Mapping[str, Any]
    ) -> models.Resource:
        checked_id = self._check_id(resource_id)
        cleaned = models.validate_update_fields(changes)
        with self._transaction() as connection:
            existing = self._get_resource(connection, checked_id)
            if not cleaned:
                return existing
            updated = {
                "name": cleaned.get("name", existing.name),
                "provider": cleaned.get("provider", existing.provider),
                "source_url": cleaned.get("source_url", existing.source_url),
            }
            updated["normalized_name"] = cleaned.get(
                "normalized_name", models.normalize_name(updated["name"])
            )
            updated["normalized_provider"] = cleaned.get(
                "normalized_provider", models.normalize_provider(updated["provider"])
            )
            updated["normalized_source_url"] = cleaned.get(
                "normalized_source_url",
                models.normalize_source_url(updated["source_url"]),
            )
            duplicates = self._find_duplicates(
                connection,
                updated["normalized_provider"],
                updated["normalized_name"],
                updated["normalized_source_url"],
                exclude_id=checked_id,
            )
            if duplicates:
                raise self._duplicate_error(duplicates)

            assignments = dict(cleaned)
            assignments["updated_at"] = models.to_iso_z(models.utc_now())
            columns = list(assignments.keys())
            set_sql = ", ".join(f"{column} = ?" for column in columns)
            try:
                connection.execute(
                    f"UPDATE resources SET {set_sql} WHERE id = ?",
                    [assignments[column] for column in columns] + [checked_id],
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise models.DuplicateError(
                        "update would create a duplicate resource"
                    ) from exc
                raise models.ValidationError(
                    f"database rejected the update: {exc}"
                ) from exc
            return self._get_resource(connection, checked_id)

    def delete_resource(self, resource_id: int) -> models.Resource:
        checked_id = self._check_id(resource_id)
        with self._transaction() as connection:
            resource = self._get_resource(connection, checked_id)
            connection.execute("DELETE FROM resources WHERE id = ?", (checked_id,))
            return resource

    # ------------------------------------------------------------------
    # Queries and reporting
    # ------------------------------------------------------------------

    def query_resources(
        self, query: Optional[search.ResourceQuery] = None
    ) -> list[models.Resource]:
        query = query or search.ResourceQuery()
        where, parameters = search.build_where(query)
        limit = search.normalize_limit(query.limit)
        sql = "SELECT * FROM resources" + where + " ORDER BY name COLLATE NOCASE, id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters = parameters + [limit]
        with self._connect() as connection:
            return [
                self._row_to_resource(row)
                for row in connection.execute(sql, parameters).fetchall()
            ]

    def list_all(self) -> list[models.Resource]:
        return self.query_resources()

    def list_stale(
        self,
        *,
        days: int = verify.DEFAULT_STALE_DAYS,
        now: Optional[datetime] = None,
    ) -> list[models.Resource]:
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise models.ValidationError("days must be a non-negative integer")
        now_value = now or models.utc_now()
        now_iso = models.to_iso_z(now_value)
        cutoff = models.to_iso_z(now_value - timedelta(days=days))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM resources
                WHERE last_verified_at IS NULL
                   OR last_verified_at < ?
                   OR verification_status IN (
                        'unverified', 'stale', 'expired', 'disputed'
                   )
                   OR (expiration_at IS NOT NULL AND expiration_at <= ?)
                ORDER BY name COLLATE NOCASE, id
                """,
                (cutoff, now_iso),
            ).fetchall()
        resources = [self._row_to_resource(row) for row in rows]
        return [
            resource
            for resource in resources
            if verify.needs_reverification(
                resource, now=now_value, stale_days=days
            )
        ]

    def stats(
        self,
        *,
        days: int = verify.DEFAULT_STALE_DAYS,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise models.ValidationError("days must be a non-negative integer")
        now_value = now or models.utc_now()
        now_iso = models.to_iso_z(now_value)

        def counts(column: str, choices: Sequence[str]) -> dict[str, int]:
            result = {choice: 0 for choice in choices}
            for row in connection.execute(
                f"SELECT {column} AS value, COUNT(*) AS total "
                f"FROM resources GROUP BY {column}"
            ).fetchall():
                result[str(row["value"])] = int(row["total"])
            return result

        with self._connect() as connection:
            total_row = connection.execute(
                "SELECT COUNT(*) AS total FROM resources"
            ).fetchone()
            total = int(total_row["total"]) if total_row else 0
            date_expired_row = connection.execute(
                "SELECT COUNT(*) AS total FROM resources "
                "WHERE expiration_at IS NOT NULL AND expiration_at <= ?",
                (now_iso,),
            ).fetchone()
            date_expired = int(date_expired_row["total"]) if date_expired_row else 0
            with_evidence_row = connection.execute(
                "SELECT COUNT(*) AS total FROM resources "
                "WHERE verification_status != 'unverified' "
                "AND length(trim(verification_evidence)) > 0"
            ).fetchone()
            with_evidence = (
                int(with_evidence_row["total"]) if with_evidence_row else 0
            )
            by_category = counts("category", models.CATEGORIES)
            by_offer_type = counts("offer_type", models.OFFER_TYPES)
            by_verification_status = counts(
                "verification_status", models.VERIFICATION_STATUSES
            )
            by_payment = counts("payment_method_required", models.PAYMENT_CHOICES)

        return {
            "total": total,
            "by_category": by_category,
            "by_offer_type": by_offer_type,
            "by_verification_status": by_verification_status,
            "by_payment_method_required": by_payment,
            "date_expired": date_expired,
            "with_verification_evidence": with_evidence,
            "needs_reverification": len(self.list_stale(days=days, now=now_value)),
            "stale_days": days,
            "generated_at": now_iso,
        }

    # ------------------------------------------------------------------
    # Verification tracking
    # ------------------------------------------------------------------

    def record_verification(
        self,
        resource_id: int,
        status: str,
        evidence: str,
        checked_at: Any = None,
        *,
        now: Optional[datetime] = None,
    ) -> tuple[models.Resource, models.VerificationEvent]:
        checked_id = self._check_id(resource_id)
        now_value = now or models.utc_now()
        with self._transaction() as connection:
            existing = self._get_resource(connection, checked_id)
            verification = verify.validate_verification(
                status,
                evidence,
                checked_at,
                now=now_value,
                previous_checked_at=existing.last_verified_at,
            )
            updated_at = models.to_iso_z(now_value)
            connection.execute(
                "UPDATE resources SET verification_status = ?, "
                "verification_evidence = ?, last_verified_at = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    verification["verification_status"],
                    verification["verification_evidence"],
                    verification["last_verified_at"],
                    updated_at,
                    checked_id,
                ),
            )
            cursor = connection.execute(
                "INSERT INTO verification_events"
                "(resource_id, checked_at, status, evidence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    checked_id,
                    verification["last_verified_at"],
                    verification["verification_status"],
                    verification["verification_evidence"],
                    updated_at,
                ),
            )
            event_id = int(cursor.lastrowid)
            event = connection.execute(
                "SELECT * FROM verification_events WHERE id = ?", (event_id,)
            ).fetchone()
            return (
                self._get_resource(connection, checked_id),
                models.VerificationEvent.from_row(event),
            )

    def verification_events(self, resource_id: int) -> list[models.VerificationEvent]:
        checked_id = self._check_id(resource_id)
        with self._connect() as connection:
            # Distinguish a missing record from a record with no history.
            self._get_resource(connection, checked_id)
            rows = connection.execute(
                "SELECT * FROM verification_events WHERE resource_id = ? "
                "ORDER BY checked_at DESC, id DESC",
                (checked_id,),
            ).fetchall()
            return [models.VerificationEvent.from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Import and export
    # ------------------------------------------------------------------

    def export_records(
        self, query: Optional[search.ResourceQuery] = None
    ) -> list[dict[str, Any]]:
        query = search.ResourceQuery(
            text=query.text if query else None,
            category=query.category if query else None,
            offer_type=query.offer_type if query else None,
            eligibility=query.eligibility if query else None,
            verification_status=query.verification_status if query else None,
            payment_method_required=(
                query.payment_method_required if query else None
            ),
            country=query.country if query else None,
            limit=None,
        )
        return [resource.to_public_dict() for resource in self.query_resources(query)]

    def _prepare_import_record(
        self, raw: Any, index: int, now: datetime
    ) -> _PreparedImport:
        prefix = f"record {index}"
        if not isinstance(raw, Mapping):
            raise models.ValidationError(f"{prefix} must be a JSON object")
        allowed = set(models.PUBLIC_FIELDS)
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise models.ValidationError(
                f"{prefix} has unknown field(s): {', '.join(unknown)}"
            )
        resource_input = {
            key: value
            for key, value in raw.items()
            if key in models.RESOURCE_INPUT_FIELDS
        }
        try:
            cleaned = models.validate_new_resource(resource_input, now=now)
            verification = verify.validate_import_verification(raw, now=now)
        except models.ValidationError as exc:
            raise models.ValidationError(f"{prefix}: {exc}") from exc

        cleaned.update(verification)
        has_verification = cleaned["verification_status"] != "unverified"
        return _PreparedImport(data=cleaned, has_verification=has_verification)

    def import_records(
        self,
        records: Any,
        *,
        on_duplicate: str = "skip",
        dry_run: bool = False,
        now: Optional[datetime] = None,
    ) -> ImportResult:
        """Validate and import complete resource objects.

        Modes: skip existing duplicates, update a single matching duplicate, or
        error. Every record is validated before any row is changed.
        """

        if on_duplicate not in {"skip", "update", "error"}:
            raise models.ValidationError(
                "on_duplicate must be one of: skip, update, error"
            )
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise models.ValidationError("import data must be a list of resource objects")
        if len(records) > MAX_IMPORT_RECORDS:
            raise models.ValidationError(
                f"import may contain at most {MAX_IMPORT_RECORDS} records"
            )
        now_value = models.as_utc(now or models.utc_now())
        prepared = [
            self._prepare_import_record(raw, index, now_value)
            for index, raw in enumerate(records, start=1)
        ]

        inserted = updated = skipped = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for item in prepared:
                    data = item.data
                    duplicates = self._find_duplicates(
                        connection,
                        data["normalized_provider"],
                        data["normalized_name"],
                        data["normalized_source_url"],
                    )
                    if duplicates:
                        if on_duplicate == "error":
                            raise self._duplicate_error(duplicates)
                        if on_duplicate == "skip":
                            skipped += 1
                            continue
                        if len(duplicates) > 1:
                            raise models.ConflictError(
                                "import matches more than one existing resource ("
                                + ", ".join(f"#{item.id}" for item in duplicates)
                                + "); resolve the duplicate manually before updating"
                            )
                        self._apply_import_update(
                            connection, duplicates[0], data, now_value
                        )
                        updated += 1
                        continue

                    resource_id = self._insert_prepared(connection, data)
                    if item.has_verification:
                        self._insert_verification_event(
                            connection,
                            resource_id,
                            data["last_verified_at"],
                            data["verification_status"],
                            data["verification_evidence"],
                            models.to_iso_z(now_value),
                        )
                    inserted += 1

                if dry_run:
                    connection.rollback()
                else:
                    connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return ImportResult(inserted=inserted, updated=updated, skipped=skipped)

    def _apply_import_update(
        self,
        connection: sqlite3.Connection,
        existing: models.Resource,
        data: Mapping[str, Any],
        now_value: datetime,
    ) -> None:
        fields = [
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
            "normalized_name",
            "normalized_provider",
            "normalized_source_url",
        ]
        assignments = {field: data[field] for field in fields}
        assignments["updated_at"] = models.to_iso_z(now_value)
        columns = list(assignments.keys())
        try:
            connection.execute(
                "UPDATE resources SET "
                + ", ".join(f"{column} = ?" for column in columns)
                + " WHERE id = ?",
                [assignments[column] for column in columns] + [existing.id],
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise models.DuplicateError(
                    "import update would create a duplicate resource"
                ) from exc
            raise models.ValidationError(
                f"database rejected the imported update: {exc}"
            ) from exc

        if data["verification_status"] != "unverified":
            self._insert_verification_event(
                connection,
                existing.id,
                data["last_verified_at"],
                data["verification_status"],
                data["verification_evidence"],
                models.to_iso_z(now_value),
            )


def parse_import_document(document: Any) -> list[Mapping[str, Any]]:
    """Extract records from a JSON array or a BANSOS export wrapper."""

    if isinstance(document, list):
        records = document
    elif isinstance(document, Mapping) and "resources" in document:
        records = document["resources"]
        if not isinstance(records, list):
            raise models.ValidationError("import 'resources' must be a list")
    else:
        raise models.ValidationError(
            "import JSON must be a list of records or an object with a 'resources' list"
        )
    if not all(isinstance(item, Mapping) for item in records):
        raise models.ValidationError("every imported record must be a JSON object")
    if len(records) > MAX_IMPORT_RECORDS:
        raise models.ValidationError(
            f"import may contain at most {MAX_IMPORT_RECORDS} records"
        )
    return records


def load_import_file(path: Path, *, max_bytes: int = 10 * 1024 * 1024) -> Any:
    """Read and parse untrusted JSON with a size limit and clear errors."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise models.ValidationError(f"cannot read import file {path}: {exc}") from exc
    if size > max_bytes:
        raise models.ValidationError(
            f"import file is larger than {max_bytes // (1024 * 1024)} MiB"
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise models.ValidationError(f"cannot read import file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise models.ValidationError(
            f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
