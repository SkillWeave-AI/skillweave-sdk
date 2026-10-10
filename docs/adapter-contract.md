# Adapter contract set (SW-ADAPT-001)

The SDK freezes the **neutral adapter** wire contracts as a namespaced,
versioned set under `src/skillweave_sdk/schemas/adapter/`. This document is the
map: what each contract owns and how the SDK relates to the runtime that
produces and consumes it.

> The SDK owns the **contract bytes**. It does **not** become a second adapter
> runtime and holds no gate authority. Core (`skillweave.adapters`) consumes
> these records through the installed `skillweave_sdk.validator` registry.

## The three adapter contracts

| Contract | `$id` | Purpose |
|---|---|---|
| `adapter-descriptor-v1` | `https://skillweave.dev/schemas/adapter/adapter-descriptor/v1` | The neutral descriptor of one adapter: id, version, runtime categories, operations, transport, capabilities, credential refs, optional opaque `package_ref` |
| `credential-reference-v1` | `https://skillweave.dev/schemas/adapter/credential-reference/v1` | A credential *reference* (a name, never a value) and its availability state |
| `search-outcome-v1` | `https://skillweave.dev/schemas/adapter/search-outcome/v1` | A typed search result: status, results, attempts, synthesis |

A descriptor is deliberately **neutral**: it names no concrete provider and
carries no package-format kind vocabulary. The independent axes — runtime
category, invocation transport, package format/kind, distribution — are four
distinct facts, and only the runtime category set is closed (`search`,
`generic`).

## Fail-closed shape

* `search-outcome-v1` binds `status` to a closed enum
  (`success` | `blocked` | `error` | `unavailable` | `cancelled`); a `success`
  requires **at least one** result, and every non-success status requires
  `results: []`. This is what makes "an outage is never dressed up as a
  retrieved source" a schema fact, not a convention.
* `adapter-descriptor-v1` quarantines a package-format `kind` as an **opaque
  open string** under `package_ref`; a container ref must name the admitted
  `member_ref`. `additionalProperties: false` everywhere, so an unknown field
  fails rather than being silently ignored.
* A descriptor naming an unsupported `schema_version` fails closed; the facade
  never best-effort parses a future contract.

## Installed-artifact authority

The schema bytes are the authority. The validator exposes
`ADAPTER_SCHEMA_SUBDIR`, `ADAPTER_SCHEMA_FILENAMES`, and the frozen
`EXPECTED_SCHEMA_DIGEST`; the installed-wheel regression test builds the SDK,
installs it into a fresh target, and asserts the installed digest and registry
size match — so a wheel that omits the nested `adapter/` schemas cannot pass by
falling back to a source tree.

## Conformance

`skillweave-sdk/tests/contract/test_adapter_contract_set.py` and the adapter
assertions in `test_execution_contract_set.py` (wheel contents, installed
digest) are the machine checks behind this document.

## Boundary

Capacium is an **optional, explicit, versioned package-format reader** on the
consumer side — never runtime adapter authority inside the SDK. The SDK freezes
the neutral descriptor contract; which format produced a descriptor is the
consumer's concern.
