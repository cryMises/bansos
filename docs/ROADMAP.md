# BANSOS Roadmap

## Current status (v0.1.0)

### Phase 1 — completed

- SQLite initialization with versioned migrations
- Resource model, required-field and URL validation, normalized duplicate keys
- `init`, `add`, `list`, `show`, and `search`
- Interactive add plus scriptable options
- Default user-writable database path with `--db` / `BANSOS_DB` override
- Local-only operation with zero runtime dependencies

### Phase 2 — completed

- Structured `filter` for category, offer type, eligibility, stored verification
  status, payment requirement, and country
- Safe `edit` with duplicate protection
- `delete` with explicit interactive confirmation or `--yes`
- Manual verification evidence, dates, and append-only events
- Derived stale/expired/disputed research states
- `stale` reporting with configurable age threshold
- `stats` for category, offer-type, payment, verification, expiration, and
  re-verification totals

### Phase 3 — completed

- Lossless-enough JSON export of current resource fields and verification state
- CSV export with spreadsheet-formula mitigation
- Transactional JSON import with whole-document validation, size limits, unknown
  field rejection, all-or-nothing writes, `skip`/`update`/`error` duplicate modes,
  and `--dry-run`
- Clearly fictional, example.com-only demo seed data
- 54 standard-library tests with no network or real external services
- Responsive terminal presentation: box tables, KPI dashboard, bar charts, and
  ASCII/no-colour fallbacks without adding runtime dependencies
- README, schema documentation, roadmap, `.gitignore`, and optional packaging

### Phase 4 — not started (optional)

- Local web dashboard
- Optional research integrations that never become mandatory

## Guiding constraints for future work

1. Local-first by default: no account, hosted backend, telemetry, or mandatory
   network access.
2. No automatic claim that an offer is available. Any URL fetching feature must be
   opt-in, clearly separate from evidence, and must not equate HTTP success with a
   valid free offer.
3. No credential storage, CAPTCHA bypass, referral abuse, or quota circumvention.
4. Preserve `unknown`; do not guess missing facts.
5. Keep the standard library path working. Any advanced dependency must be optional
   and justified for Android/Termux.
6. Backward-compatible schema migrations; never rewrite an applied migration.
7. Evidence requirements remain enforced at both application and database levels.

## Candidate work

### Research quality

- [ ] Full verification-event history in JSON export/import, with explicit
      provenance and idempotent event handling
- [ ] Configurable verification threshold via a local config file or flags on
      list/show (stale/stats already accept `--days`)
- [ ] Automated warning when fact-bearing fields are edited after verification
      (for example, suggest a new verification rather than silently invalidating it)
- [ ] Research checklists per category (terms, payment method, renewal, regional
      restrictions) stored as templates, not claims
- [ ] Tags and saved searches for large catalogs
- [ ] Comparison view for two or more resources

### Operations and safety

- [ ] First-class `backup` and `restore` commands using SQLite's backup API
- [ ] Export manifests/checksums and optional explicit-history export
- [ ] Optional encryption-at-rest design with a clear threat model and no mandatory
      cryptography dependency
- [ ] More granular import conflict reporting before writes
- [ ] VACUUM/checkup command with clear warnings
- [ ] Additional schema migrations for richer country/eligibility modeling if
      real use demonstrates a need

### Android/Termux

- [ ] Document and test a native Termux workflow on a recent Android release
- [ ] Test on a filesystem with constrained locking behavior and add graceful
      diagnostics
- [ ] Verify terminal width/readability on small screens (tables and detail views
      now adapt to the detected width, down to a 40-column layout)
- [ ] Optional Termux:Widget shortcuts for common read-only commands

### Optional local dashboard

- [ ] Read-mostly local dashboard using the standard library where practical
- [ ] Bind to loopback only by default; no authentication means no network exposure
- [ ] Reuse the same validation and database layer as the CLI
- [ ] Clearly mark unverified/stale/expired records
- [ ] Never add scraping or account automation

### Explicit non-goals for now

- User accounts, cloud sync, or mandatory web hosting
- Automatic scraping, CAPTCHA solving, or referral farming
- Storing card numbers, API keys, passwords, or access tokens
- Guaranteeing that a provider's offer will remain available
- Treating import or HTTP success as verification evidence

## Release checklist

Before tagging a release:

1. Run `python -m unittest discover -s tests -v` with no failures.
2. Run a fresh-database CLI smoke test using `--db` in a temporary directory.
3. Test initialization against a copy of the previous schema version.
4. Verify `.gitignore` still excludes databases, journals, secrets, and exports.
5. Update README, schema docs, and this roadmap.
6. Confirm no mandatory network call or third-party runtime dependency was added.
