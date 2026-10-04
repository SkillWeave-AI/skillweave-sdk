# Execution contract set (SW-1510-CONTRACT)

The SDK freezes the integrated run / attempt / evidence / routing wire
contracts as a namespaced, versioned set under
`src/skillweave_sdk/schemas/execution/`. This document is the map: what each
contract owns, who owns it, how it fails closed, and how the SDK relates to the
runtimes that produce and consume it.

> The SDK owns the **contract**. It does **not** become a second workflow
> runtime. Core, ops-engine and the router keep their runtime ownership and
> consume these records through the SDK.

## The ten frozen contracts plus the record surface

| Contract | Owner | Producer | Purpose |
|---|---|---|---|
| `run-snapshot-v1` | sdk | run service | Durable run state at a cursor; references the shared run-state vocabulary |
| `attempt-event-v1` | sdk | collector | One measured attempt event in the journal |
| `evidence-receipt-v1` | sdk | observer/reviewer | A reviewer verdict bound to a criterion and subject digest |
| `execution-envelope-v1` | sdk | owner authority | Service-authorized execution budget with an explicit limit policy |
| `reservation-v1` | sdk | reservation authority | One admission-ledger reservation on the single authoritative ledger |
| `route-decision-v1` | sdk | router | The record of a routing decision, including unknown-usage retention |
| `capability-snapshot-v1` | sdk | router/measuring service | A time-bounded capability measurement of a subject |
| `agent-message-v1` | sdk | mailbox service | A mailbox-delivered agent message record |
| `mirror-target-v1` | **ops_engine** | ops-engine | A release/mirror target record; ops-engine keeps the machinery |
| `contract-catalog` | sdk | sdk contract freeze | The SDK-owned index of this set (does not index itself) |
| `contract-catalog-record-v1` | **router** | router | The router-owned published catalog surface; ownership does not transfer |

`contract-catalog` is the local name of `contract-catalog-v1.schema.json`; the
index is built from the ten contracts above it and excludes itself, because a
self-indexing catalog would make its digest depend on its own bytes.

Ownership is preserved explicitly, not reassigned: `mirror-target-v1` stays
`ops_engine`-owned and `contract-catalog-record-v1` stays `router`-owned, even
though both live in the SDK schema tree so they can be validated as one set.

## Acceptance criteria and where they are proven

1. **Reviewed schema, one named owner, immutable digest, producer/consumer/
   migration per contract.** Proven by `tests/contract/test_execution_contract_set.py`
   (`TestContractSetShape`) against `contract/execution-catalog.json`, whose
   per-schema `digest_sha256` values are recomputed from the actual schema bytes
   in the test. Ownership metadata lives in `schema_version.toml`
   `[execution.schemas]`.
2. **Unknown versions and missing required ids fail closed; methodology,
   autonomy policy and session schedule are distinct.** Proven by
   `TestFailClosedVersion`, `TestMissingIdsFailClosed` and
   `TestDistinctScheduleFields`. `require_supported_version` raises
   `UnsupportedSchemaVersionError` before any best-effort parse.
3. **Native handles and role decisions come from a trusted service.** Proven by
   `TestSchemaIsNotAuthentication`, `TestNativeHandleAuthority` and
   `TestEvidenceReceiptAuthority`. See `docs/execution-contract-trust-boundary.md`
   for the boundary and its downstream integration requirement.

## Immutable digests

Each contract's schema bytes are frozen by a sha256 in the catalog. The whole
set additionally carries one canonical digest over the sorted-key canonical
JSON of every schema (flat + execution), embedded in
`skillweave_sdk.validator.EXPECTED_SCHEMA_DIGEST` and enforced by
`verify_canonical_digest()`. The installed wheel ships the same bytes: a
packaging regression would change the digest of the installed copy, which
`TestInstalledWheel` catches.

## Migrations

Legacy consumers of the pre-SW-1510 flat envelope reach the frozen contract only
through `skillweave_sdk.validator.adapt_legacy_execution_envelope`. It is
explicit and tested; it never silently weakens authority. See
`docs/execution-contract-envelope.md`.

## Catalogue provenance

`contract/execution-catalog.json` records two distinct revisions:

- `baseline_commit` — the source revision the frozen schemas were authored
  against.
- `integration_commit` — the revision at which the enclosing change is
  integrated. This is resolved by the integration step, which alone can record
  it without self-reference; it is never this file's own commit's SHA.

The catalog instance is itself validated against `contract-catalog-v1` by
`test_catalog_instance_validates_against_its_schema`.

## CI qualification status

This set is **not yet CI-qualified**. The cross-repo contract guard described in
`README.md` is the separate SDK-CI task. Until that task lands, the frozen set
is proven by this repository's own test suite and an offline wheel
build/install, not by a green cross-repo pipeline.
