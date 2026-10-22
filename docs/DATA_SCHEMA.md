# BANSOS Data Schema

**Schema version:** 1  
**Database engine:** SQLite 3 (Python standard library `sqlite3`)  
**Default file:** `$XDG_DATA_HOME/bansos/bansos.db` or
`~/.local/share/bansos/bansos.db`

All timestamps are stored as ISO 8601 UTC text with a trailing `Z` and second
resolution, for example `2026-10-22T12:30:00Z`. Dates given without a time are
treated as midnight UTC. Naive datetimes are also treated as UTC. Lexicographic
comparison is valid because storage is normalized.

The database is intentionally plain SQLite: no server, ORM, encrypted extension,
or external database dependency. All application queries use bound parameters.

## Tables

### `schema_migrations`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `version` | INTEGER | PRIMARY KEY | Monotonically increasing migration number |
| `applied_at` | TEXT | NOT NULL | UTC timestamp when applied |

Created by `bansos init` before migrations run. The application refuses to open a
database whose highest migration is newer than the running application.

### `resources`

| Column | Type | Constraints/default | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Stable, never reused |
| `name` | TEXT | NOT NULL, length 1–200 | Offer/resource name |
| `provider` | TEXT | NOT NULL, length 1–200 | Provider/organization |
| `category` | TEXT | NOT NULL, enum CHECK | See categories |
| `description` | TEXT | NOT NULL, default `''`, ≤ 5,000 | Short factual description |
| `offer_type` | TEXT | NOT NULL, default `unknown`, enum CHECK | Nature of offer |
| `pricing_notes` | TEXT | NOT NULL, default `''`, ≤ 5,000 | Pricing/billing observations |
| `included_limits` | TEXT | NOT NULL, default `''`, ≤ 5,000 | Quotas and included limits |
| `eligibility_requirements` | TEXT | NOT NULL, default `''`, ≤ 5,000 | Eligibility text |
| `payment_method_required` | TEXT | NOT NULL, default `unknown`, CHECK `yes/no/unknown` | Card/payment requirement |
| `country_availability` | TEXT | NOT NULL, default `unknown`, ≤ 200 | Free text |
| `source_url` | TEXT | NOT NULL, length 1–2,048 | Official offer/source URL |
| `official_terms_url` | TEXT | NOT NULL, default `''`, ≤ 2,048 | Official terms URL |
| `first_seen_at` | TEXT | NOT NULL, non-empty | ISO 8601 UTC |
| `last_verified_at` | TEXT | NULL | Date of the latest manual check |
| `expiration_at` | TEXT | NULL | Offer expiration/valid-through timestamp |
| `verification_status` | TEXT | NOT NULL, default `unverified`, enum CHECK | Stored status of record |
| `verification_evidence` | TEXT | NOT NULL, default `''`, ≤ 10,000 | Latest manual evidence |
| `notes` | TEXT | NOT NULL, default `''`, ≤ 10,000 | Research notes, not verified facts |
| `normalized_name` | TEXT | NOT NULL, non-empty | Internal duplicate key |
| `normalized_provider` | TEXT | NOT NULL, non-empty | Internal duplicate key |
| `normalized_source_url` | TEXT | NOT NULL, non-empty | Internal duplicate key |
| `created_at` | TEXT | NOT NULL, non-empty | ISO 8601 UTC |
| `updated_at` | TEXT | NOT NULL, non-empty | ISO 8601 UTC |

Additional table-level CHECK constraint:

```sql
CHECK (
    verification_status = 'unverified'
    OR (
        last_verified_at IS NOT NULL
        AND length(trim(last_verified_at)) > 0
        AND length(trim(verification_evidence)) > 0
    )
)
```

This means the database itself rejects `verified`, `stale`, `expired`, or
`disputed` without a check date and evidence. Application validation is stricter
and also rejects malformed timestamps, URLs, controls, and future verification
dates.

### `verification_events`

Append-only audit trail of manual verification actions.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Event ID |
| `resource_id` | INTEGER | NOT NULL, FK → `resources(id)` ON DELETE CASCADE | Record checked |
| `checked_at` | TEXT | NOT NULL, non-empty | Date checked, ISO UTC |
| `status` | TEXT | NOT NULL, CHECK `verified/stale/expired/disputed` | Outcome recorded |
| `evidence` | TEXT | NOT NULL, non-empty | What was checked and observed |
| `created_at` | TEXT | NOT NULL | When entered locally |

`verification_events` is not directly editable through the CLI. It records each
manual `bansos verify` call. Deleting a resource cascades to its events.

> Exports currently include the latest verification fields from `resources`, not
> the full event history. Importing such an export creates one verification event
> for a verified/stale/expired/disputed record if the evidence is valid.

## Enumerated values

### Categories

```text
vps-cloud
web-hosting
domains-dns
ai-apis
databases-storage
dev-tools-ci
app-deployment
education-benefits
open-source-services
other
```

### Offer types

```text
always_free
free_tier
trial
promotional_credit
educational_benefit
open_source
unknown
```

`unknown` is a deliberate first-class value. Missing research must not be guessed.

### Payment method requirement

```text
yes
no
unknown
```

### Stored verification statuses

```text
unverified
verified
stale
expired
disputed
```

## Indexes and duplicate identity

Unique indexes:

```sql
CREATE UNIQUE INDEX ux_resources_provider_name
    ON resources(normalized_provider, normalized_name);

CREATE UNIQUE INDEX ux_resources_source_url
    ON resources(normalized_source_url);
```

Non-unique indexes support category, offer type, stored verification status,
last-verified date, expiration date, and normalized provider queries.

Duplicate detection uses normalized identity, not raw text:

- Provider names are NFKC-normalized, case-folded, punctuation-collapsed, and
  have trailing legal suffixes (`Inc`, `LLC`, `Ltd`, `GmbH`, etc.) removed.
  Example: `Example Provider, Inc.` and `EXAMPLE PROVIDER LLC` normalize to
  `example provider`.
- Resource names are NFKC-normalized, case-folded, punctuation-collapsed, and
  whitespace-collapsed.
- Source URLs lower-case the scheme and host, drop default ports and fragments,
  normalize path slashes, and preserve the query string. Credentials, non-HTTP(S)
  schemes, whitespace, and invalid ports are rejected before storage.

The application treats either duplicate identity match as a duplicate and surfaces
existing record IDs. A database unique constraint is the final race-safe guard.

## Verification states and derived state

`verification_status` is the stored field. The CLI also derives a **research
state** for display:

1. If `expiration_at` is present and not in the future → `EXPIRED`.
2. Else, stored `disputed` → `DISPUTED`.
3. Else, stored `unverified` or missing verification date → `UNVERIFIED`.
4. Else, stored `expired` → `EXPIRED`.
5. Else, stored `stale` → `STALE`.
6. Else, if last verification is older than the threshold → `STALE`.
7. Else → `VERIFIED`.

The default threshold is 90 days. `bansos stale --days N` and
`bansos stats --days N` can use another non-negative threshold. Filtering by
`--status` uses the exact stored status, not the derived display state.

Evidence must describe an official source and what it said. A URL returning HTTP
200 is not sufficient evidence that an offer is currently available. BANSOS never
performs that check automatically.

## Transactions and concurrency

- Connections enable `PRAGMA foreign_keys = ON` and a 5-second busy timeout.
- Writes use `BEGIN IMMEDIATE` and explicit commit/rollback.
- Imports validate every record before writing and run in one transaction.
- The schema uses the SQLite rollback journal (`DELETE` mode), chosen for
  portability on Termux/Android filesystems and simple backups.
- One local CLI process is the intended usage. SQLite may tolerate multiple
  readers and a writer, but there is no application-level multi-user policy.

## Schema evolution

1. Back up the database.
2. Add a new numbered migration tuple in `src/bansos/db.py`; never rewrite a
   migration that has already been applied to a user's database.
3. Increment `SCHEMA_VERSION`.
4. Update models, exports, imports, docs, and tests for compatibility.
5. Run the full suite and test `bansos init` against a copy of an older database.

`bansos init` is idempotent and applies pending migrations. Other commands refuse
to run if the database schema is older than expected and instruct the user to run
`bansos init`. A database newer than the application is refused rather than
silently downgraded.

## Import/export representation

The JSON export wrapper is:

```json
{
  "format": "bansos-resources",
  "format_version": 1,
  "schema_version": 1,
  "exported_at": "2026-10-22T12:30:00Z",
  "count": 1,
  "resources": [
    {
      "id": 1,
      "name": "...",
      "provider": "...",
      "category": "web-hosting",
      "offer_type": "free_tier",
      "payment_method_required": "unknown",
      "source_url": "https://example.com/example",
      "first_seen_at": "2026-10-22T12:00:00Z",
      "last_verified_at": null,
      "expiration_at": null,
      "verification_status": "unverified",
      "verification_evidence": "",
      "notes": "...",
      "created_at": "2026-10-22T12:00:00Z",
      "updated_at": "2026-10-22T12:00:00Z"
    }
  ]
}
```

Import accepts this wrapper or a top-level JSON array. It validates the whole
document before writing. It ignores imported `id` for new records, preserves
`first_seen_at` and verification metadata after validation, and re-detects
duplicates using normalized keys. CSV export contains the same public fields but
is not an accepted import format because CSV lacks the validation guarantees of
the JSON workflow.

CSV export prefixes formula-like leading characters (`=`, `+`, `-`, `@`, tab,
carriage return) with an apostrophe to reduce spreadsheet formula injection risk.
Use JSON for lossless interchange.

## Backup

Stop BANSOS commands and copy the `.db` file, or use SQLite's backup command while
the application is not writing:

```sh
sqlite3 "$HOME/.local/share/bansos/bansos.db" \
  ".backup '$HOME/bansos-backups/bansos-$(date +%F).db'"
```

Because the journal mode is `DELETE`, there is normally no persistent WAL sidecar
to include. A transient `-journal` file may exist during a write; do not copy a
database mid-transaction. Backups contain plaintext notes and evidence.
