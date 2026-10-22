# BANSOS Agent Instructions

## Mission

Maintain a trustworthy, local-first research catalog of legitimately free
technology resources. Use the internet to find and check offers, then update the
SQLite catalog through the BANSOS CLI. Never turn incomplete research into a
confident claim.

A resource marked `verified` means evidence was reviewed and recorded. It does
not mean BANSOS, the agent, or an HTTP request proved that the offer is currently
available.

## Project facts

- Python 3.9+ with the standard library only; do not add runtime dependencies.
- Run from the repository root with `./bin/bansos ...`. The shell alias `bansos`
  points to the same launcher when `~/.bashrc` has been loaded.
- Default database:
  `$XDG_DATA_HOME/bansos/bansos.db` or `~/.local/share/bansos/bansos.db`.
- If the user has chosen another database, honor it consistently with `--db PATH`
  or `BANSOS_DB`. Confirm the active database before bulk changes.
- Schema and behavior: `docs/DATA_SCHEMA.md`; setup and commands: `README.md`.
- After changing application code, run:

  ```sh
  .venv/bin/python -m unittest discover -s tests -v
  ```

  Research-only database updates do not require changing or testing code.

## Hard rules

1. **Do not invent providers, prices, quotas, eligibility rules, terms, source
   URLs, expiration dates, or availability.** If the source does not state it,
   leave the field empty or use `unknown`.
2. **Use official sources** for evidence: the provider's own offer, pricing,
   documentation, eligibility, FAQ, status, or terms pages.
3. **A search snippet is a lead, not evidence.** Open the actual page and verify
   the canonical domain and relevant text.
4. **Do not bypass logins, paywalls, CAPTCHAs, regional restrictions, or terms.**
   Do not create accounts or supply payment details to inspect an offer.
5. **No automatic verification by HTTP status.** The CLI never fetches URLs;
   `verify` records evidence supplied by a human or agent after reading sources.
6. **Duplicate before adding.** Search normalized names, providers, and source
   URLs. Edit an existing resource instead of adding a second copy.
7. **Use the CLI for database changes.** Do not mutate SQLite tables directly.
   The application enforces validation, duplicate detection, and evidence rules.
8. **Do not alter verification fields with `edit`.** They are intentionally
   changed only through `verify`.
9. **Confirm before destructive actions.** Do not delete, overwrite exports, or
   apply destructive imports without explicit user approval.
10. **Never store secrets.** Descriptions, notes, evidence, and URLs must not
    contain passwords, API keys, tokens, card numbers, or private credentials.
11. **Do not seed a real catalog unless asked.** Demo records are intentionally
    fictional and must never be treated as research evidence.
12. Use UTC ISO dates/timestamps for research dates and evidence.

## Internet research workflow

1. **Define the target.** Identify the provider and offer, and the category the
   user wants researched. Ask only when the target is genuinely ambiguous.
2. **Check the existing catalog first.**

   ```sh
   ./bin/bansos search PROVIDER_OR_OFFER
   ./bin/bansos filter --category CATEGORY --status unverified
   ./bin/bansos show ID
   ```

   Use `--json` when structured inspection is easier.
3. **Search for official pages.** Prefer queries that include the provider name,
   offer name, and terms such as `official pricing`, `free tier`, `eligibility`,
   `terms`, `FAQ`, or `limits`.
4. **Open and read the primary source.** Record:
   - the final official URL and page title
   - the access date (UTC)
   - the specific facts observed: price, quota, limits, eligibility, region,
     payment-method requirement, renewal/expiration behavior, and restrictions
   - any conflict between the offer page and terms/pricing documentation
5. **Cross-check when possible.** If the offer page and official terms disagree,
   record the conflict and leave the affected fields uncertain or mark the record
   disputed. Do not resolve conflicts by guessing.
6. **Prefer current evidence.** Check update dates, banners, status pages, and
   expiration language. A search result or archived copy is not proof of the
   current offer.
7. **Use secondary sources only as discovery leads**, not as sole evidence.
   Affiliate listicles, coupon sites, forum posts, and search snippets may help
   find the provider, but the official source must support the claim.
8. **Classify conservatively.**
   - `always_free`: free permanently or as the normal offering, per official source
   - `free_tier`: free allowance with paid upgrades
   - `trial`: time-limited trial
   - `promotional_credit`: time/quantity-limited credit
   - `educational_benefit`: eligibility-gated education benefit
   - `open_source`: open-source project benefit
   - `unknown`: the source does not establish the nature of the offer
9. **Payment method:** use `yes`, `no`, or `unknown` exactly as supported by the
   official source. Do not assume that a free offer needs no card.
10. **Stop instead of guessing** if the page requires authentication, is
    unavailable, has no official source, or contradicts itself. Record what was
    found, leave unverified fields as `unknown`, and report the limitation.

## Database update workflow

1. **Confirm the target database.** For a non-default database, use the same
   `--db` value for every command in the task.
2. **Deduplicate.** Search by provider, name, and source URL. Use BANSOS
   normalization behavior: case/punctuation variants and legal suffixes can be
   duplicates.
3. **Add or edit.**

   Use real researched values and a real official URL:

   ```sh
   ./bin/bansos add \
     --name "RESOURCE_NAME" \
     --provider "PROVIDER_NAME" \
     --category CATEGORY \
     --description "FACTUAL_DESCRIPTION" \
     --offer-type OFFER_TYPE \
     --pricing-notes "WHAT_THE_SOURCE_SAYS_ABOUT_PRICE" \
     --included-limits "WHAT_THE_SOURCE_SAYS_ABOUT_LIMITS" \
     --eligibility-requirements "WHAT_THE_SOURCE_SAYS_ABOUT_ELIGIBILITY" \
     --payment-method-required unknown \
     --country-availability "WHAT_THE_SOURCE_SAYS" \
     --source-url "OFFICIAL_URL" \
     --official-terms-url "OFFICIAL_TERMS_URL" \
     --first-seen-at "YYYY-MM-DD" \
     --notes "Researcher: AGENT. Accessed YYYY-MM-DD UTC. Official source reviewed; remaining uncertainty: ..."
   ```

   For an existing record:

   ```sh
   ./bin/bansos edit ID --pricing-notes "..." --notes "..."
   ```

   Do not pass `unknown` as if it were a researched fact; it means the source did
   not establish the value. Do not fill optional fields just to make a record look
   complete.
4. **Record verification only when evidence exists.**

   ```sh
   ./bin/bansos verify ID \
     --status verified \
     --evidence "Source: OFFICIAL_URL. Accessed YYYY-MM-DD UTC. Observed: SPECIFIC_FACTS_AND_LIMITATIONS." \
     --checked-at "YYYY-MM-DD"
   ```

   Evidence must be specific enough for a later researcher to understand what was
   checked. Include the official URL, access date, concrete observations, and any
   unresolved limitation. Paraphrase rather than copying large passages.
5. **Handle changes honestly.**
   - If material facts changed, re-check the official source and record fresh
     evidence in the same task.
   - If the offer is gone, record `expired` with evidence (and/or an expiration
     date) rather than leaving a stale "verified" impression.
   - If sources conflict, use `disputed` with evidence describing the conflict.
   - If re-checking cannot be completed, use `stale` with evidence explaining what
     remains unconfirmed.
6. **Inspect the result.**

   ```sh
   ./bin/bansos show ID
   ./bin/bansos stats
   ./bin/bansos stale --days 90
   ```

7. **Back up before bulk changes.** Before a large import, bulk edit, or repair,
   copy the closed database or use SQLite's `.backup`. Use `--dry-run` first for
   imports, and ask before `--on-duplicate update`, `--force`, or deletion.

## Evidence standard

Good evidence names the authoritative source, date, and observations:

```text
Source: https://OFFICIAL-DOMAIN/example-terms. Accessed 2026-10-22 UTC.
Observed: the free plan lists a monthly quota of N and requires a payment method;
the terms say the offer can change with notice. Regional eligibility was not
stated on the page.
```

Insufficient evidence:

- "Found on the internet."
- "Search result says free."
- "The URL loads."
- "Looks legit."
- A coupon/listicle page without the official source.
- A quota or price with no citation.

Never put a URL in evidence that was not actually opened and reviewed.

## Reporting back

At the end of a research/update task, report:

- resource IDs added or edited
- the official source URL(s) and access date
- fields that remain `unknown` or unverified
- conflicts, access limitations, or expired claims
- verification status and evidence recorded
- the database path used, especially if it was not the default

Do not describe a record as guaranteed or currently available merely because it
is marked verified. Keep the distinction between evidence, confidence, and
availability explicit.
