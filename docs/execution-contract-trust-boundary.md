# Trust boundary: authenticated context vs. untrusted wire

A JSON schema validates **untrusted wire data**. It does **not** authenticate.
Keeping those two facts separate is the point of this boundary. The SDK defines
the contract and a pure decision function; it does **not** become the runtime
that owns authentication.

## What the SDK provides

`skillweave_sdk.validator` exposes a small, dependency-free boundary:

- `AuthenticatedServiceContext` — a frozen dataclass carrying facts the runtime
  obtained **after** authentication (mTLS, a signed service token, a native
  handle registry lookup): `principal`, `roles`, `service_principal`,
  `issued_handle_ids`, `authority_decision_digests`, `worker_principal`.
- `verify_native_handle` — asserts a native handle was issued by the
  authenticated service, not merely shaped like one.
- `verify_run_snapshot_authority` — asserts the caller holds a run-service role,
  the process identity handle is service-issued, and the authority decision was
  admitted.
- `verify_evidence_receipt_authority` — asserts the claimed reviewer principal
  equals the authenticated principal, the caller holds a reviewer/observer role,
  the decision was admitted, and the reviewer is not the worker whose attempt is
  judged.

Every failure raises `AuthorityBoundaryError`.

## What it deliberately refuses

- A wire flag such as `issuer: "service"` or `role: "reviewer"` is never
  consulted. Roles must be in the authenticated context.
- A self-described handle `{"handle_id": ..., "service_principal": ...}` fails
  closed unless the id is in the service's issued registry **and** the recorded
  service principal equals the authenticated one.
- A parsed JSON object cannot be forwarded wholesale as "trusted" context: the
  `AuthenticatedServiceContext` constructor refuses non-string principals and
  non-`frozenset` role/handle/decision collections.

## The limit of this guarantee (read this)

The SDK **cannot prove a caller authenticated anything**. `AuthenticatedServiceContext`
is a shape check, not authentication. A caller that constructs it from wire
values defeats it. "Author tests" and "the author says the context is trusted"
are **not** proof that the runtime seam has authenticated the values.

## Downstream integration requirement (not yet met here)

The runtime that owns the seam — Core and ops-engine, not this SDK — **must**:

1. construct `AuthenticatedServiceContext` only from its authentication result;
2. derive none of its fields from the untrusted instance under verification;
3. call these verifiers at the actual production seam (the real run/attempt/
   review path), not only in tests;
4. keep the authenticated context outside any untrusted wire-input structure.

Until those points hold in the runtime and are proven by a test at the real
production seam, criterion 3 is **contract-complete and integration-pending**.
This repository proves the decision function's behaviour; it does not claim the
runtime already calls it. That integration is the job of the Core/ops-engine
change tracked separately from this SDK contract freeze.

`tests/contract/test_execution_contract_set.py` (`TestSchemaIsNotAuthentication`,
`TestNativeHandleAuthority`, `TestEvidenceReceiptAuthority`) proves the decision
function refuses forged handles, self-issued roles, worker self-attestation and
unadmitted decisions.
