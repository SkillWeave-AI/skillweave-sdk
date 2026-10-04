# Execution envelope limit policy

`execution-envelope-v1` is the service-authorized execution budget for one run.
It carries **exactly one** limit policy, named explicitly by
`limit_policy.policy`. There is no implicit default and no silent widening.

## Two policies, one honest choice

### `observe_adapt`

The default for open-ended work. It records atomic counters and applies only:

- per-request limits (`max_request_bytes`, `max_output_tokens`), and
- active-slot caps (`max_inflight_requests`, `max_parallel_lanes`).

It has **no** cumulative USD/token/request ceiling and **no** whole-job
deadline. `deadline_utc` is a scheduling target, or `null`. A `null` deadline is
an honest "no deadline" value — never a fabricated future date. Its optional
`counters` block may be omitted entirely; when present, its values are
nonnegative **measurements**, not completion-stopping ceilings. The schema does
not require an invented positive maximum to satisfy `observe_adapt`.

### `enforcing`

An explicit opt-in. The envelope states its cumulative ceilings and its
deadline flag, and the runtime aborts when they are reached:

- `counters.max_requests` / `counters.max_reserved_tokens` — positive integers;
- `enforcement.max_requests` / `enforcement.max_reserved_tokens` — positive
  cumulative ceilings;
- `enforcement.max_correction_rounds` — zero is a valid finite choice;
- `enforcement.deadline` — when `true`, `deadline_utc` must be a real timestamp.

`enforcing` is never inferred. A caller that wants enforcement writes it.

## Unknown usage is retained, never zero

An unmeasured request is recorded as `usage_status: "unknown"` (see
`reservation-v1` and `route-decision-v1`). Unknown usage stays reserved; it is
never collapsed to zero, which would overstate remaining budget.

## The legacy adapter never weakens intent

`skillweave_sdk.validator.adapt_legacy_execution_envelope` maps a pre-SW-1510
flat envelope onto this contract with exactly two sound outcomes, chosen from
the legacy content:

| Legacy envelope | Outcome |
|---|---|
| No cumulative ceiling and no deadline | `observe_adapt`; per-request limits and slots preserved; legacy request/token figures, if both present, kept only as nonnegative **accounting counters** |
| A finite cumulative ceiling or a deadline | `enforcing`; the ceilings are preserved exactly and are **never downgraded** to `observe_adapt` |

Anything that cannot be represented soundly raises `LegacyMigrationError` with
an actionable message:

- an unknown or absent `schema_version` (fails closed via
  `UnsupportedSchemaVersionError`);
- a missing required field;
- a non-positive per-request limit;
- an enforcing legacy envelope whose deadline is absent — a fabricated date is
  **not** accepted; supply a real deadline.

The adapter does not change any existing caller's authority as a side effect of
migration: a finite legacy ceiling is preserved as enforcing, and one that
cannot be preserved is rejected rather than silently relaxed.

`tests/contract/test_execution_contract_set.py::TestLegacyAdapter` and
`TestEnvelopeLimits` prove both policies and both adapter outcomes.
