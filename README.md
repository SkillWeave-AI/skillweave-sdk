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
| `contract/` | Machine-readable value-set extracts, so a consumer can validate without a Python context |
| `schema_version.toml` | Root of the release graph; consumers pin a version from here |
| `src/skillweave_sdk/` | The distribution. Carries the version and nothing else yet |

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
