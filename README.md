# BANSOS

**Local-first research catalog for legitimately free technology resources.**

BANSOS helps you discover, record, compare, and re-verify free technology offers
without depending on a hosted service, account, API key, or network connection.
It is a deliberately small Python 3 + SQLite command-line application designed to
run inside Ubuntu on Android via Termux `proot-distro`, and on ordinary Linux.

> **Research status:** BANSOS is a notebook, not an authority. A record marked
> `verified` means that **you recorded evidence and a date**. It does not mean
> BANSOS checked the provider, that the offer is currently available, or that a
> free tier will remain free.

## What it is for

The catalog is intended for categories such as:

- VPS and cloud computing
- Free web hosting and static hosting
- Domains, subdomains, and DNS
- AI APIs and free model access
- Managed databases and storage
- Developer tools and CI/CD
- App deployment platforms
- Student and education developer benefits
- Open-source software and free development services

The application deliberately distinguishes `always_free`, `free_tier`, `trial`,
`promotional_credit`, `educational_benefit`, `open_source`, and `unknown` offers.
It also records whether a payment method is required, because "free" offers that
need a card are materially different from offers that do not.

## Implementation status

Version **0.1.0** implements the core workflows:

- Phase 1: database initialization, resource model, `add`, `list`, `show`, `search`
- Phase 2: `filter`, `edit`, `delete`, `verify`, `stale`, `stats`
- Phase 3: CSV/JSON export, validated JSON import, duplicate handling, demo seed,
  tests, and documentation
- Phase 4 (local web dashboard and optional integrations) is **not implemented**

The current test suite has 42 standard-library tests. They run without internet
access and use temporary databases.

## Limitations and non-goals

- **No automatic web fetching or scraping.** Version 1 never opens a source URL.
- **No API keys, paid services, accounts, or external integrations required.**
- **No claim that a successful HTTP response proves an offer is available.** Users
  must read official sources manually and record what they checked.
- **Evidence quality is the user's responsibility.** Requiring evidence prevents
  accidental unsupported verification, not dishonest or careless evidence.
- **Single-user and unencrypted.** The database is a plain SQLite file. Do not
  store passwords, access tokens, card details, or API secrets in it.
- **No full event-history export.** Exports include the current verification
  evidence/date/status; verification history remains in the local database.
- The list/show derived state uses a 90-day re-verification threshold. The
  `stale` and `stats` commands accept a different `--days` value.
- No cloud sync, web dashboard, account system, or background checking.

## Requirements

- Python **3.9+** with the built-in `sqlite3` module
- SQLite 3 (included with Python)
- No third-party Python packages
- A user-writable home directory
- Android root is not required when using Termux with `proot-distro`

## Ubuntu on Android through Termux

These are user-level setup steps. BANSOS itself does not install system packages,
request root, or modify system configuration.

### 1. Enter Ubuntu from Termux

```sh
pkg update
pkg install proot-distro
proot-distro install ubuntu
proot-distro login ubuntu
```

Inside Ubuntu:

```sh
apt update
apt install -y python3 python3-venv git sqlite3
```

### 2. Put the project in your home directory

```sh
cd "$HOME"
# Copy or clone this project into $HOME/bansos, then:
cd bansos
python3 -m venv .venv
. .venv/bin/activate
```

No package installation is required to run BANSOS. The checkout launcher uses
only the standard library:

```sh
./bin/bansos --help
```

To use the shorter `bansos` command throughout your shell session:

```sh
alias bansos="$PWD/bin/bansos"
```

Or create a user-level symlink (no system directory is touched):

```sh
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/bin/bansos" "$HOME/.local/bin/bansos"
# Make sure $HOME/.local/bin is on PATH, then:
bansos --help
```

Alternatively, run the module directly:

```sh
PYTHONPATH=src python3 -m bansos --help
```

### 3. Optional pip installation

If you want the generated `bansos` console script and setuptools/build tooling is
already available, install the project into the virtual environment:

```sh
python -m pip install -e .
```

This step is optional. It may require network access for pip build tooling; the
checkout launcher avoids that entirely. BANSOS has no runtime dependencies.

### Native Termux alternative

The same code can run in native Termux without Ubuntu:

```sh
pkg install python git sqlite
cd "$HOME/bansos"
./bin/bansos --help
```

Use internal app storage for the database. Some Android shared-storage mounts have
locking or permission behavior that is unsuitable for SQLite databases.

## Quick start

Initialize the database:

```sh
bansos init
```

Add a resource interactively:

```sh
bansos add
```

Or supply fields non-interactively. The following is a **template with fictional
data** using the reserved `example.com` domain, not a research claim:

```sh
bansos add \
  --name "[EXAMPLE] Fictional Student VPS" \
  --provider "Example Provider (not a real service)" \
  --category vps-cloud \
  --description "Template record; replace with manually researched facts." \
  --offer-type educational_benefit \
  --pricing-notes "Describe billing and renewal terms here." \
  --included-limits "Describe quotas and limits here." \
  --eligibility-requirements "Describe verified eligibility rules here." \
  --payment-method-required unknown \
  --country-availability "Describe verified availability here." \
  --source-url "https://example.com/example-offer" \
  --official-terms-url "https://example.com/example-terms" \
  --notes "Template only. Record what you checked and when."
```

List, inspect, and search:

```sh
bansos list
bansos show 1
bansos search hosting student
bansos filter --category vps-cloud --status unverified
bansos filter --offer-type trial --payment yes
bansos filter --eligibility "student" --status unverified
```

Record a manual verification only after reading the official source:

```sh
bansos verify 1 \
  --evidence "Reviewed the official terms page manually on 2026-10-22; it stated the current quota and that a card is required." \
  --checked-at 2026-10-22

bansos stale --days 90
bansos stats
```

The application does **not** fetch the URL. The evidence field should describe the
specific official page and what it said. Keep observations in `--notes` and
verification observations in `--evidence`.

Edit and delete safely:

```sh
bansos edit 1 --pricing-notes "Updated from the current official page."
bansos delete 1        # asks for confirmation in a terminal
bansos delete 1 --yes  # explicit non-interactive deletion
```

Export and re-import:

```sh
mkdir -p exports
bansos export --format json --output exports/catalog.json
bansos export --format csv  --output exports/catalog.csv
bansos import exports/catalog.json --dry-run
bansos import exports/catalog.json --on-duplicate skip
```

`export` refuses to overwrite a file unless `--force` is supplied. Import accepts
a JSON array or the object produced by `export`. JSON is the lossless interchange
format; CSV is for review and spreadsheet use.

Load two clearly fictional demonstration records if desired:

```sh
bansos seed --yes
```

They use `example.com`, are prefixed `[DEMO]`, and are intentionally unverified.
They are not real offers.

## Command reference

| Command | Purpose |
| --- | --- |
| `bansos init` | Create or migrate the SQLite database |
| `bansos add` | Add a resource interactively or with options |
| `bansos list` | List saved resources |
| `bansos show ID` | Display all fields and verification history |
| `bansos search QUERY...` | Search names, providers, descriptions, and notes |
| `bansos filter` | Filter by category, offer type, eligibility, status, payment, country |
| `bansos edit ID` | Update fields without changing verification |
| `bansos delete ID` | Delete after confirmation, or with `--yes` |
| `bansos verify ID` | Record evidence, date checked, and verification status |
| `bansos stale` | Find unverified, stale, expired, or disputed records |
| `bansos stats` | Show category, offer-type, payment, and verification totals |
| `bansos export` | Export all or filtered records to CSV/JSON |
| `bansos import FILE` | Validate and import JSON records with duplicate handling |
| `bansos seed` | Optionally load clearly fictional demo records |

Use `bansos COMMAND --help` for all options.

### Duplicate handling on import

- `--on-duplicate skip` (default): skip records matching an existing normalized
  provider+name or normalized source URL.
- `--on-duplicate update`: replace fields on exactly one matching record. Ambiguous
  matches abort the import; resolve those manually.
- `--on-duplicate error`: abort if any duplicate exists.
- `--dry-run`: validate and compute counts, then roll back all writes.

All records are validated before the transaction writes anything. Invalid input
does not partially import.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Runtime, filesystem, schema, or database error |
| `2` | Validation or usage error |
| `3` | Resource not found |
| `4` | Duplicate or conflicting operation (including refused overwrite) |
| `5` | User cancelled an interactive operation |
| `130` | Interrupted with Ctrl+C |

## Database location and backups

Default location:

```text
$XDG_DATA_HOME/bansos/bansos.db
```

If `XDG_DATA_HOME` is unset:

```text
~/.local/share/bansos/bansos.db
```

Override it for a single command:

```sh
bansos --db ./data/catalog.db init
bansos --db ./data/catalog.db list
# The option is also accepted after the command:
bansos list --db ./data/catalog.db
```

Or set an environment variable:

```sh
export BANSOS_DB="$HOME/research/bansos.db"
bansos init
```

Precedence is `--db`, then `BANSOS_DB`, then the default. BANSOS creates missing
parent directories, uses a rollback journal (not WAL) for Termux portability, sets
the database file mode to `0600`, and makes a newly created database directory
private (`0700`). Exports are written atomically with mode `0600` where the
filesystem supports it.

Safe backup with the SQLite CLI:

```sh
mkdir -p "$HOME/bansos-backups"
sqlite3 "$HOME/.local/share/bansos/bansos.db" \
  ".backup '$HOME/bansos-backups/bansos-$(date +%F).db'"
chmod 600 "$HOME/bansos-backups/"*.db
```

Or copy the database while no BANSOS command is running:

```sh
cp "$HOME/.local/share/bansos/bansos.db" \
   "$HOME/bansos-backups/bansos-$(date +%F).db"
```

To restore, stop all BANSOS commands, copy the backup into place, and run
`bansos init` to verify or apply migrations. Back up before schema upgrades.
Backups contain all notes and evidence in plain text; protect them accordingly.

## Data model overview

Each resource has the fields requested for the research workflow, plus a latest
verification evidence field and normalized duplicate keys:

- identity: `name`, `provider`, `category`, `source_url`, `official_terms_url`
- offer facts: `description`, `offer_type`, `pricing_notes`, `included_limits`,
  `eligibility_requirements`, `payment_method_required`, `country_availability`
- research history: `first_seen_at`, `last_verified_at`, `expiration_at`,
  `verification_status`, `verification_evidence`, `notes`
- bookkeeping: `id`, `created_at`, `updated_at`, normalized internal keys
- separate append-only `verification_events` history for each manual check

All persisted timestamps are ISO 8601 UTC strings ending in `Z`. Date-only input
is treated as midnight UTC; naive datetimes are interpreted as UTC. Search and
filter values are always bound parameters.

Duplicate identity is enforced with two unique indexes:

- normalized provider + normalized resource name
- normalized source URL (scheme/host lowercased, default port and fragment ignored,
  trailing slash normalized, query preserved)

Normalization is conservative but may merge punctuation/case variants and legal
suffixes such as "Inc." or "LLC". If you need two genuinely distinct records that
normalize to the same identity, use distinct official source URLs and adjust the
name/provider enough to reflect the difference.

See [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md) for every column, constraint, index,
and migration rule.

### Verification states

Stored `verification_status` values:

- `unverified` — no verification evidence recorded
- `verified` — evidence and a check date were recorded
- `stale` — manually marked stale, or derived as older than the stale threshold
- `expired` — manually marked expired, or the recorded expiration date has passed
- `disputed` — evidence conflicts or the offer's status is contested

The CLI distinguishes the **stored** status from a derived **research state**.
List/show mark records `STALE` when the last check is older than 90 days and
`EXPIRED` when `expiration_at` has passed, even if the stored status is still
`verified`. The `list` table's `STATE` column and `show`'s "Research state" are
derived. Filters and statistics by "stored verification status" are explicit.

The SQLite schema also enforces that non-`unverified` records have both a
verification date and non-empty evidence. There is no command that marks a record
verified without evidence.

## Testing

BANSOS uses Python's standard `unittest` instead of requiring pytest. The test
suite covers schema initialization, CRUD, validation, duplicates, edits/deletes,
search/filtering, import/export, stale/verification rules, malformed input, SQL
injection attempts, and CLI exit codes.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Or, with the active environment:

```sh
python -m unittest discover -s tests -v
```

If pytest is already installed, it can also collect the suite:

```sh
pytest -q
```

No test requires internet access. Tests create temporary SQLite databases and
delete them afterwards.

## Security considerations

- **No credential fields.** Do not put passwords, tokens, private keys, or payment
  card data in descriptions, notes, or evidence. The URL validator also rejects
  embedded `user:password@` credentials.
- **Plain text at rest.** SQLite and exports are not encrypted. Use OS/device disk
  encryption and restrictive file permissions if the catalog is sensitive.
- **Local-only v1.** There is no server, account, telemetry, synchronization, or
  automatic outbound HTTP request.
- **Untrusted imports.** Import size is capped at 10 MiB and 5,000 records; fields,
  choices, timestamps, URLs, Unicode control/format characters, and unknown keys
  are validated. Imports are transactional and all records are pre-validated.
- **SQL injection resistance.** Every user value is bound as a parameter. Dynamic
  SQL identifiers come only from fixed application constants.
- **Spreadsheet formula mitigation.** CSV export prefixes cells that begin with
  `=`, `+`, `-`, `@`, tab, or carriage return with an apostrophe. Use JSON export
  when you need exact text. Treat any exported file as untrusted.
- **Terminal safety.** Control and invisible format characters are rejected in
  stored text and imported records, reducing terminal escape injection.
- **Deletion is logical only at the application level.** SQLite may retain data in
  free pages, filesystem snapshots, or backups. Use device encryption if secure
  erasure matters.
- **Backups and exports are sensitive.** They include research notes and evidence.

## Project layout

```text
src/bansos/
  __init__.py    version
  __main__.py    python -m bansos entry point
  cli.py         argparse CLI and presentation
  db.py          SQLite schema, migrations, repository, import/export
  models.py      constants, dataclasses, validation, normalization
  search.py      parameterized search/filter query construction
  verify.py      verification rules and derived research states
  seed.py        clearly fictional demo records
tests/            standard-library unittest suite
docs/             schema and roadmap
data/             local database location if you choose a project-local DB
exports/          generated exports (ignored by Git)
bin/bansos        no-install checkout launcher
```

Generated databases, journals, virtual environments, caches, secrets, and exports
are excluded by `.gitignore`. Version-control a file in `exports/` only
deliberately, for example with `git add -f`.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md). Phase 4 ideas include an optional local
web dashboard, configurable stale/expiry policy, full verification-history export,
tags and saved searches, and a first-class backup command. Any future integration
must remain optional; manual research is always supported.

## Contributing and research ethics

- Do not add invented providers, prices, quotas, eligibility rules, or source URLs.
- Do not treat a landing-page HTTP response as proof of a current offer.
- Do not attempt CAPTCHA bypass, account/referral abuse, or free-tier circumvention.
- Keep unverified claims clearly unverified; use `unknown` when research is incomplete.
- Record official sources and manual evidence rather than speculation.
