# skillweave-sdk

Contract authority for SkillWeave: the core schemas and the taxonomy value set
every consumer validates against.

**No runtime code. No product logic.** This repository is the root of the release
graph and has no runtime dependency on `skillweave`.

Forgejo is canonical; GitHub is the read-only mirror.

## Contents

| Path | Purpose |
|---|---|
| `schemas/` | The core schemas (JSON Schema draft 2020-12) |
| `schemas/execution/` | The frozen execution contract set (SW-1510-CONTRACT): run/attempt/evidence/routing |
| `contract/` | Machine-readable value-set extracts, so a consumer can validate without a Python context |
| `docs/execution-contract*.md` | The frozen set, its envelope policy and its trust boundary |
| `schema_version.toml` | Root of the release graph; consumers pin a version from here |
| `src/skillweave_sdk/` | The distribution: schemas, the taxonomy value set and the validator |

## The frozen execution contract set

`schemas/execution/` freezes the integrated run, attempt, evidence and routing
contracts as a namespaced v1 set, indexed by `contract/execution-catalog.json`
with one named owner and an immutable sha256 per contract. The SDK owns the
**contract**; Core, ops-engine and the router keep their **runtime** ownership
and consume these records through the SDK. There is no second workflow runtime
here.

- `docs/execution-contract.md` — contracts, owners, digests, migration, provenance
- `docs/execution-contract-envelope.md` — the `observe_adapt` / `enforcing` limit policies
- `docs/execution-contract-trust-boundary.md` — wire validation vs. authenticated context

The set is **not yet CI-qualified**; that is the separate SDK-CI task.

## Where the line runs

This SDK owns the **contract** — the authoritative **value set** of schema and
vocabulary, not every line of code that touches it.

```
skillweave-sdk        owns the CONTRACT    (schemas, taxonomy value set)
skillweave            owns EXECUTION       (runtime, kernel, engine)
skillweave-profiles   owns OPINION         (profiles, category packs)
skillweave-packs-pro  owns COMMERCIAL OPINION (provider-bound)
```

Concretely, on the state vocabulary: `run-state.schema.json#/properties/state/enum`
carries the value set. The core carries `RunStateModel` (the member names) and
`legal_transitions` (execution semantics). A validator needs only the value set —
that is the argument for an external pack author being able to validate without
core access.

## Versioning

`schema_version.toml` → `schema.version`. A breaking change raises the major, an
additive one the minor. A consumer pins an exact version and validates against it;
it never references "latest".

The distribution version in `pyproject.toml` is the same number, asserted by
`tests/test_version_agrees.py` rather than derived, so neither file silently
follows the other.

## Cross-repo contract CI

Consumers keep a guard that compares their value sets against the pinned SDK
version. Break a contract here and the consumer's build goes red. The proof is
only delivered once a deliberately broken contract turns the other repository's
build red — not when the pipeline is green.
