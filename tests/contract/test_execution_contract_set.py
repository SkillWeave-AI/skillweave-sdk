"""Contract tests for the frozen execution contract set (SW-1510-CONTRACT).

These tests exercise the SDK's public validator and authority boundary against
the frozen ``execution/`` schema set and assert the three acceptance criteria:

1. every contract has a reviewed schema, one named owner, an immutable digest
   and an explicit producer/consumer/migration in the catalog;
2. unknown versions and missing required ids fail closed, and methodology,
   autonomy policy and session schedule stay distinct fields;
3. native handles and role decisions come from a trusted service; model-authored
   strings and self-issued wire flags are never accepted as runtime or reviewer
   attestation.

A JSON schema validates untrusted wire data; it does not authenticate. The
authority tests therefore cross the external boundary explicitly and never
claim that schema validation alone provides runtime authentication.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from skillweave_sdk.validator import (
    EXPECTED_SCHEMA_DIGEST,
    EXECUTION_SCHEMA_FILENAMES,
    SCHEMA_FILENAMES,
    AuthorityBoundaryError,
    AuthenticatedServiceContext,
    LegacyMigrationError,
    UnsupportedSchemaVersionError,
    adapt_legacy_execution_envelope,
    canonical_digest,
    load_registry,
    require_supported_version,
    supported_schema_versions,
    validate,
    verify_canonical_digest,
    verify_evidence_receipt_authority,
    verify_native_handle,
    verify_run_snapshot_authority,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "src" / "skillweave_sdk" / "schemas"
EXECUTION = SCHEMAS / "execution"
CATALOG_INSTANCE = ROOT / "contract" / "execution-catalog.json"

CONTRACTS = {
    "run-snapshot": "run-snapshot-v1.schema.json",
    "attempt-event": "attempt-event-v1.schema.json",
    "evidence-receipt": "evidence-receipt-v1.schema.json",
    "execution-envelope": "execution-envelope-v1.schema.json",
    "reservation": "reservation-v1.schema.json",
    "route-decision": "route-decision-v1.schema.json",
    "capability-snapshot": "capability-snapshot-v1.schema.json",
    "agent-message": "agent-message-v1.schema.json",
    "mirror-target": "mirror-target-v1.schema.json",
    "contract-catalog": "contract-catalog-v1.schema.json",
}

SCHEMA_IDS = {
    "run-snapshot": "https://skillweave.dev/schemas/execution/run-snapshot/v1",
    "attempt-event": "https://skillweave.dev/schemas/execution/attempt-event/v1",
    "evidence-receipt": "https://skillweave.dev/schemas/execution/evidence-receipt/v1",
    "execution-envelope": "https://skillweave.dev/schemas/execution/execution-envelope/v1",
    "reservation": "https://skillweave.dev/schemas/execution/reservation/v1",
    "route-decision": "https://skillweave.dev/schemas/execution/route-decision/v1",
    "capability-snapshot": "https://skillweave.dev/schemas/execution/capability-snapshot/v1",
    "agent-message": "https://skillweave.dev/schemas/execution/agent-message/v1",
    "mirror-target": "https://skillweave.dev/schemas/execution/mirror-target/v1",
    "contract-catalog": "https://skillweave.dev/schemas/execution/contract-catalog/v1",
    "contract-catalog-record": "https://skillweave.dev/schemas/execution/contract-catalog-record/v1",
}

DIGEST = "a" * 64


def _registry():
    return load_registry(SCHEMAS)


def _validate(name: str, instance: dict) -> list[str]:
    return validate(instance, SCHEMA_IDS[name], SCHEMAS)


def _observe_envelope():
    # An honest observe_adapt envelope: no cumulative ceiling and no invented
    # future deadline. deadline_utc is null ("no deadline") and counters are
    # omitted rather than filled with fake maxima.
    return {
        "schema_version": "1.0.0",
        "envelope_id": "env-1",
        "authorized_principal": "owner",
        "subject_digest": DIGEST,
        "brief_digest": DIGEST,
        "scope": {},
        "deadline_utc": None,
        "limit_policy": {
            "policy": "observe_adapt",
            "max_request_bytes": 100,
            "max_output_tokens": 100,
            "max_inflight_requests": 2,
            "max_parallel_lanes": 2,
        },
        "allowed_routes": ["byteplus/deepseek-v4.1-flash"],
        "irreversible_actions": [],
        "authority_evidence_digest": DIGEST,
    }


def _enforcing_envelope():
    return {
        "schema_version": "1.0.0",
        "envelope_id": "env-1",
        "authorized_principal": "owner",
        "subject_digest": DIGEST,
        "brief_digest": DIGEST,
        "scope": {},
        "deadline_utc": "2026-12-31T00:00:00Z",
        "limit_policy": {
            "policy": "enforcing",
            "max_request_bytes": 100,
            "max_output_tokens": 100,
            "max_inflight_requests": 2,
            "max_parallel_lanes": 2,
            "counters": {"max_requests": 10, "max_reserved_tokens": 100},
            "enforcement": {
                "max_requests": 10,
                "max_reserved_tokens": 100,
                "max_correction_rounds": 1,
                "deadline": True,
            },
        },
        "allowed_routes": ["byteplus/deepseek-v4.1-flash"],
        "irreversible_actions": [],
        "authority_evidence_digest": DIGEST,
    }


def _valid(name: str) -> dict:
    fixtures = {
        "run-snapshot": {
            "schema_version": "1.0.0",
            "run_id": "run-1",
            "version": 1,
            "task_id": "SW-1510-CONTRACT",
            "attempt": 1,
            "state": "IN_PROGRESS",
            "gate_state": "pending",
            "methodology": "assay",
            "autonomy_policy": "unicorn",
            "session_schedule": "batch",
            "subject_digest": DIGEST,
            "brief_digest": DIGEST,
            "envelope_digest": DIGEST,
            "process_identity": {"handle_id": "proc-1", "service_principal": "native-run-service"},
            "event_cursor": 0,
            "authority_decision_digest": DIGEST,
        },
        "attempt-event": {
            "schema_version": "1.0.0",
            "run_id": "run-1",
            "task_id": "t1",
            "attempt": 1,
            "event_id": "req-1",
            "sequence": 1,
            "event_type": "MEASURE",
            "measured_at": "2026-10-04T00:00:00Z",
            "issuer": "collector",
            "principal": "observer-1",
            "evidence_digest": DIGEST,
            "payload": {},
        },
        "evidence-receipt": {
            "schema_version": "1.0.0",
            "run_id": "run-1",
            "task_id": "t1",
            "attempt": 1,
            "criterion_id": "ac-1",
            "subject_digest": DIGEST,
            "artifact_digest": DIGEST,
            "verifier_version": "1.0.0",
            "verdict": "pass",
            "issuer": "observer",
            "principal": "reviewer-1",
            "authority_decision_digest": DIGEST,
            "signature": "sig",
            "semantic_checks": [{"check_id": "c1", "result": "pass", "evidence_digest": DIGEST}],
        },
        "execution-envelope": _observe_envelope(),
        "reservation": {
            "schema_version": "1.0.0",
            "reservation_id": "res-1",
            "envelope_id": "env-1",
            "run_id": "run-1",
            "task_id": "t1",
            "attempt": 1,
            "request_id": "req-1",
            "parent_request_id": "",
            "expires_at": "2026-12-31T00:00:00Z",
            "request_bytes": 0,
            "reserved_tokens": 1,
            "max_output_tokens": 1,
            "state": "held",
            "usage_status": "unknown",
            "issuer": "reservation-authority",
        },
        "route-decision": {
            "schema_version": "1.0.0",
            "run_id": "run-1",
            "task_id": "t1",
            "attempt": 1,
            "request_id": "req-1",
            "parent_request_id": "",
            "requested_model": "byteplus/deepseek-v4.1-flash",
            "resolved_provider": "byteplus",
            "resolved_model": "deepseek-v4.1-flash",
            "answering_model": "deepseek-v4.1-flash",
            "attribution_status": "answered",
            "catalog_revision": "r1",
            "policy_revision": "p1",
            "reservation_id": "res-1",
            "attempt_order": ["byteplus/deepseek-v4.1-flash"],
            "measured_at": "2026-10-04T00:00:00Z",
            "outcome": "ok",
            "usage": {"usage_status": "unknown"},
        },
        "capability-snapshot": {
            "schema_version": "1.0.0",
            "snapshot_id": "snap-1",
            "revision": "r1",
            "measured_at": "2026-10-04T00:00:00Z",
            "expires_at": "2026-10-04T01:00:00Z",
            "subject": "byteplus/deepseek-v4.1-flash",
            "capabilities": {},
            "evidence_level": "probe",
            "limits": {},
            "readiness": "ready",
            "issuer": "router",
        },
        "agent-message": {
            "schema_version": "1.0.0",
            "message_id": "msg-1",
            "run_id": "run-1",
            "task_id": "t1",
            "attempt": 1,
            "sender_principal": "ops-1",
            "recipient_principal": "reviewer-1",
            "message_type": "handoff",
            "correlation_id": "corr-1",
            "created_at": "2026-10-04T00:00:00Z",
            "expires_at": "2026-10-04T01:00:00Z",
            "payload_digest": DIGEST,
            "ack_state": "pending",
            "authority_decision_digest": DIGEST,
        },
        "mirror-target": {
            "schema_version": "1.0.0",
            "source_forge": "forgejo",
            "source_owner": "skillweave",
            "source_repo": "skillweave-sdk",
            "old_destination": "skillweave/skillweave-sdk",
            "new_destination": "SkillWeave-AI/skillweave-sdk",
            "visibility": "private",
            "role": "mirror",
            "names_unchanged": True,
            "source_refs_digest": DIGEST,
            "authority_evidence_digest": DIGEST,
        },
        "contract-catalog": json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8")),
        "contract-catalog-record": {
            "schema_version": "1.0.0",
            "record_revision": "r1",
            "surface_owner": "ops-engine",
            "surface_revision": "r1",
            "digest_algorithm": "sha256",
            "surface_digests": {},
            "compatibility": {},
        },
    }
    return json.loads(json.dumps(fixtures[name]))


# ---------------------------------------------------------------------------
# AC1 — schemas, owners, digests, producer/consumer/migration
# ---------------------------------------------------------------------------


class TestContractSetShape:
    def test_all_contracts_load_from_the_registry(self):
        registry = _registry()
        for name, sid in SCHEMA_IDS.items():
            assert sid in registry, f"missing schema {name}"

    @pytest.mark.parametrize("name", sorted(CONTRACTS))
    def test_each_contract_validates_a_minimal_instance(self, name):
        assert _validate(name, _valid(name)) == []

    def test_catalog_instance_has_one_named_owner_and_digest_each(self):
        catalog = json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8"))
        assert len(catalog["schema_digests"]) == 10
        allowed_owners = set(catalog["owners"])
        for entry in catalog["schema_digests"]:
            assert entry["owner"] in allowed_owners, entry["name"]
            assert len(entry["digest_sha256"]) == 64
            assert entry["producer"]
            assert entry["consumers"]
            assert entry["migration"]

    def test_catalog_digests_match_actual_schema_bytes(self):
        catalog = json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8"))
        for entry in catalog["schema_digests"]:
            actual = hashlib.sha256((EXECUTION / entry["path"]).read_bytes()).hexdigest()
            assert actual == entry["digest_sha256"], entry["name"]

    def test_catalog_instance_validates_against_its_schema(self):
        assert _validate("contract-catalog", _valid("contract-catalog")) == []

    def test_catalog_preserves_core_and_ops_engine_ownership(self):
        catalog = json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8"))
        for holder in ("sdk", "core", "ops_engine", "router", "observer"):
            assert holder in catalog["owners"]

    def test_mirror_target_stays_ops_engine_owned(self):
        catalog = json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8"))
        entry = next(e for e in catalog["schema_digests"] if e["name"] == "mirror-target-v1")
        assert entry["owner"] == "ops_engine"

    def test_catalog_does_not_index_itself(self):
        catalog = json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8"))
        names = {e["name"] for e in catalog["schema_digests"]}
        assert "contract-catalog-v1" not in names

    def test_router_surface_record_is_separately_owned(self):
        catalog = json.loads(CATALOG_INSTANCE.read_text(encoding="utf-8"))
        entry = next(
            e for e in catalog["schema_digests"] if e["name"] == "contract-catalog-record-v1"
        )
        assert entry["owner"] == "router"
        record = json.loads((EXECUTION / "contract-catalog-record-v1.schema.json").read_text())
        assert record["$id"] != SCHEMA_IDS["contract-catalog"]

    def test_schema_version_toml_names_owner_and_migration_per_contract(self):
        import tomllib

        data = tomllib.loads((ROOT / "schema_version.toml").read_text(encoding="utf-8"))
        schemas = data["execution"]["schemas"]
        assert len(schemas) == 11
        for key, meta in schemas.items():
            assert meta["owner"], key
            assert meta["migration"], key

    def test_immutable_digest_verifies(self):
        verify_canonical_digest(SCHEMAS)
        assert canonical_digest(SCHEMAS) == canonical_digest(SCHEMAS)

    def test_tampered_schema_changes_the_digest(self, tmp_path):
        import shutil

        copy = tmp_path / "schemas"
        shutil.copytree(SCHEMAS, copy)
        target = copy / "execution" / "run-snapshot-v1.schema.json"
        text = target.read_text(encoding="utf-8").replace('"Run Snapshot v1"', '"Tampered"')
        target.write_text(text, encoding="utf-8")
        assert canonical_digest(copy) != canonical_digest(SCHEMAS)
        with pytest.raises(Exception):
            verify_canonical_digest(copy)


# ---------------------------------------------------------------------------
# AC2 — fail closed on unknown version; distinct methodology/policy/schedule
# ---------------------------------------------------------------------------


class TestFailClosedVersion:
    def test_supported_versions_are_exactly_v1(self):
        assert supported_schema_versions(SCHEMAS) == frozenset({"1.0.0"})

    def test_unknown_version_is_refused(self):
        instance = _valid("run-snapshot")
        instance["schema_version"] = "2.0.0"
        with pytest.raises(UnsupportedSchemaVersionError):
            require_supported_version(instance, SCHEMAS)

    def test_missing_version_is_refused(self):
        instance = _valid("run-snapshot")
        del instance["schema_version"]
        with pytest.raises(UnsupportedSchemaVersionError):
            require_supported_version(instance, SCHEMAS)

    def test_non_object_is_refused(self):
        with pytest.raises(UnsupportedSchemaVersionError):
            require_supported_version("not an object", SCHEMAS)

    def test_supported_version_returns_string(self):
        assert require_supported_version(_valid("run-snapshot"), SCHEMAS) == "1.0.0"

    @pytest.mark.parametrize("name", sorted(CONTRACTS))
    def test_wrong_version_fails_schema_validation(self, name):
        instance = _valid(name)
        instance["schema_version"] = "9.9.9"
        assert _validate(name, instance) != []


class TestDistinctScheduleFields:
    def test_all_three_fields_present_and_required(self):
        props = json.loads((EXECUTION / "run-snapshot-v1.schema.json").read_text())["properties"]
        for field in ("methodology", "autonomy_policy", "session_schedule"):
            assert field in props

    def test_methodology_and_autonomy_are_enum_bound(self):
        props = json.loads((EXECUTION / "run-snapshot-v1.schema.json").read_text())["properties"]
        assert props["methodology"]["enum"] == ["assay", "rex", "ralph", "gauntlet"]
        assert props["autonomy_policy"]["enum"] == ["conservative", "medium", "unicorn"]

    def test_schedule_is_not_an_autonomy_enum(self):
        props = json.loads((EXECUTION / "run-snapshot-v1.schema.json").read_text())["properties"]
        assert "enum" not in props["session_schedule"]

    def test_missing_schedule_fails(self):
        instance = _valid("run-snapshot")
        del instance["session_schedule"]
        assert _validate("run-snapshot", instance) != []

    def test_methodology_value_rejected_in_autonomy_field(self):
        instance = _valid("run-snapshot")
        instance["autonomy_policy"] = "assay"
        assert _validate("run-snapshot", instance) != []


def _required_id_fields(name: str) -> list[str]:
    schema = json.loads((EXECUTION / CONTRACTS[name]).read_text(encoding="utf-8"))
    fields = []
    for field in schema.get("required", []):
        prop = schema.get("properties", {}).get(field, {})
        if prop.get("type") == "string" and "minLength" in prop:
            fields.append(field)
    return fields


class TestMissingIdsFailClosed:
    @pytest.mark.parametrize("name", sorted(CONTRACTS))
    def test_missing_required_fields_rejected(self, name):
        fields = _required_id_fields(name)
        assert fields, f"{name} declares no required string id fields"
        for field in fields:
            instance = _valid(name)
            del instance[field]
            assert _validate(name, instance) != [], f"{name} accepted without {field}"

    @pytest.mark.parametrize("name", sorted(CONTRACTS))
    def test_empty_required_id_rejected(self, name):
        for field in _required_id_fields(name):
            instance = _valid(name)
            instance[field] = ""
            assert _validate(name, instance) != [], f"{name} accepted empty {field}"


class TestUnknownUsageRetained:
    def test_reservation_unknown_usage_is_not_zero(self):
        instance = _valid("reservation")
        instance["usage_status"] = "zero"
        assert _validate("reservation", instance) != []

    def test_route_usage_status_rejects_fabricated_value(self):
        instance = _valid("route-decision")
        instance["usage"] = {"usage_status": "free"}
        assert _validate("route-decision", instance) != []


class TestEnvelopeLimits:
    def test_zero_per_request_limit_rejected(self):
        instance = _valid("execution-envelope")
        instance["limit_policy"]["max_request_bytes"] = 0
        assert _validate("execution-envelope", instance) != []

    def test_empty_allowed_routes_rejected(self):
        instance = _valid("execution-envelope")
        instance["allowed_routes"] = []
        assert _validate("execution-envelope", instance) != []

    def test_observe_adapt_has_no_hidden_whole_job_deadline_field(self):
        policy = json.loads(
            (EXECUTION / "execution-envelope-v1.schema.json").read_text()
        )["properties"]["limit_policy"]["oneOf"][0]["properties"]
        assert "enforcement" not in policy

    def test_observe_adapt_accepts_null_deadline(self):
        instance = _valid("execution-envelope")
        instance["deadline_utc"] = None
        assert _validate("execution-envelope", instance) == []

    def test_observe_adapt_requires_no_invented_cumulative_maximum(self):
        instance = _valid("execution-envelope")
        assert "counters" not in instance["limit_policy"]
        assert _validate("execution-envelope", instance) == []

    def test_observe_adapt_accepts_honest_nonnegative_counters(self):
        instance = _valid("execution-envelope")
        instance["limit_policy"]["counters"] = {
            "max_requests": 0,
            "max_reserved_tokens": 0,
        }
        assert _validate("execution-envelope", instance) == []
        instance["limit_policy"]["counters"]["max_requests"] = -1
        assert _validate("execution-envelope", instance) != []

    def test_enforcing_policy_declares_its_ceilings_explicitly(self):
        assert _validate("execution-envelope", _enforcing_envelope()) == []
        instance = _enforcing_envelope()
        del instance["limit_policy"]["enforcement"]
        assert _validate("execution-envelope", instance) != []

    def test_enforcing_requires_positive_cumulative_ceiling(self):
        instance = _enforcing_envelope()
        instance["limit_policy"]["enforcement"]["max_requests"] = 0
        assert _validate("execution-envelope", instance) != []

    def test_observe_adapt_rejects_enforcement_block(self):
        instance = _valid("execution-envelope")
        instance["limit_policy"]["enforcement"] = {
            "max_requests": 1, "max_reserved_tokens": 1,
            "max_correction_rounds": 0, "deadline": True,
        }
        assert _validate("execution-envelope", instance) != []


class TestDigestShape:
    def test_malformed_digest_rejected(self):
        instance = _valid("run-snapshot")
        instance["subject_digest"] = "not-a-digest"
        assert _validate("run-snapshot", instance) != []

    def test_evidence_receipt_requires_a_semantic_check(self):
        instance = _valid("evidence-receipt")
        instance["semantic_checks"] = []
        assert _validate("evidence-receipt", instance) != []


# ---------------------------------------------------------------------------
# AC3 — trusted-service handles; no model-authored strings as attestation
# ---------------------------------------------------------------------------


def _run_context(**kw):
    base = dict(
        principal="run-service-1",
        roles=frozenset({"run-service"}),
        service_principal="native-run-service",
        issued_handle_ids=frozenset({"proc-1"}),
        authority_decision_digests=frozenset({DIGEST}),
    )
    base.update(kw)
    return AuthenticatedServiceContext(**base)


def _reviewer_context(**kw):
    base = dict(
        principal="reviewer-1",
        roles=frozenset({"reviewer"}),
        authority_decision_digests=frozenset({DIGEST}),
        worker_principal="worker-2",
    )
    base.update(kw)
    return AuthenticatedServiceContext(**base)


class TestSchemaIsNotAuthentication:
    def test_process_identity_must_be_an_object_not_a_string(self):
        instance = _valid("run-snapshot")
        instance["process_identity"] = "run-service-said-so"
        assert _validate("run-snapshot", instance) != []

    def test_handle_requires_service_principal_field(self):
        instance = _valid("run-snapshot")
        del instance["process_identity"]["service_principal"]
        assert _validate("run-snapshot", instance) != []

    def test_wire_flag_cannot_authenticate_a_handle(self):
        # Shape is valid, but the claimed service principal is not the
        # authenticated one, so the external boundary refuses it.
        instance = _valid("run-snapshot")
        instance["process_identity"] = {
            "handle_id": "proc-1",
            "service_principal": "native-run-service",
        }
        assert _validate("run-snapshot", instance) == []
        with pytest.raises(AuthorityBoundaryError):
            verify_native_handle(instance["process_identity"], _run_context(service_principal="other"))

    def test_trusted_context_rejects_wire_shaped_containers(self):
        # A parsed wire object cannot be forwarded wholesale as authenticated
        # context: roles must be a frozenset of str, not a JSON list.
        with pytest.raises(AuthorityBoundaryError):
            AuthenticatedServiceContext(
                principal="p", roles=["reviewer"]  # type: ignore[arg-type]
            )
        with pytest.raises(AuthorityBoundaryError):
            AuthenticatedServiceContext(principal="", roles=frozenset({"reviewer"}))

    def test_trusted_context_rejects_non_string_principal(self):
        with pytest.raises(AuthorityBoundaryError):
            AuthenticatedServiceContext(
                principal={"name": "forged"},  # type: ignore[arg-type]
                roles=frozenset({"reviewer"}),
            )


class TestNativeHandleAuthority:
    def test_issued_handle_accepted(self):
        verify_native_handle(_valid("run-snapshot")["process_identity"], _run_context())

    def test_forged_handle_id_rejected(self):
        forged = {"handle_id": "made-up", "service_principal": "native-run-service"}
        with pytest.raises(AuthorityBoundaryError):
            verify_native_handle(forged, _run_context())

    def test_self_issued_service_principal_rejected(self):
        forged = {"handle_id": "proc-1", "service_principal": "self"}
        with pytest.raises(AuthorityBoundaryError):
            verify_native_handle(forged, _run_context())

    def test_snapshot_without_run_service_role_rejected(self):
        snapshot = _valid("run-snapshot")
        with pytest.raises(AuthorityBoundaryError):
            verify_run_snapshot_authority(snapshot, _run_context(roles=frozenset({"worker"})))

    def test_snapshot_with_admitted_decision_accepted(self):
        verify_run_snapshot_authority(_valid("run-snapshot"), _run_context())

    def test_snapshot_with_unadmitted_decision_rejected(self):
        snapshot = _valid("run-snapshot")
        snapshot["authority_decision_digest"] = "b" * 64
        with pytest.raises(AuthorityBoundaryError):
            verify_run_snapshot_authority(snapshot, _run_context())


class TestEvidenceReceiptAuthority:
    def test_matching_reviewer_accepted(self):
        verify_evidence_receipt_authority(_valid("evidence-receipt"), _reviewer_context())

    def test_forged_reviewer_principal_rejected(self):
        receipt = _valid("evidence-receipt")
        receipt["principal"] = "attacker"
        with pytest.raises(AuthorityBoundaryError):
            verify_evidence_receipt_authority(receipt, _reviewer_context())

    def test_self_issued_role_flag_is_not_authority(self):
        # A wire record cannot grant itself a reviewer role; the role must be
        # in the authenticated context.
        receipt = _valid("evidence-receipt")
        receipt["role"] = "reviewer"
        with pytest.raises(AuthorityBoundaryError):
            verify_evidence_receipt_authority(receipt, _reviewer_context(roles=frozenset()))

    def test_worker_cannot_attest_own_attempt(self):
        receipt = _valid("evidence-receipt")
        with pytest.raises(AuthorityBoundaryError):
            verify_evidence_receipt_authority(
                receipt, _reviewer_context(principal="reviewer-1", worker_principal="reviewer-1")
            )

    def test_missing_authority_decision_rejected(self):
        receipt = _valid("evidence-receipt")
        del receipt["authority_decision_digest"]
        assert _validate("evidence-receipt", receipt) != []
        with pytest.raises(AuthorityBoundaryError):
            verify_evidence_receipt_authority(receipt, _reviewer_context())


class TestRoutingAndMirrorSemantics:
    def test_route_attribution_unknown_is_representable(self):
        instance = _valid("route-decision")
        instance["answering_model"] = ""
        instance["attribution_status"] = "unknown"
        assert _validate("route-decision", instance) == []

    def test_mirror_names_unchanged_const_true(self):
        instance = _valid("mirror-target")
        instance["names_unchanged"] = False
        assert _validate("mirror-target", instance) != []

    def test_mirror_visibility_closed_enum(self):
        instance = _valid("mirror-target")
        instance["visibility"] = "topsecret"
        assert _validate("mirror-target", instance) != []


# ---------------------------------------------------------------------------
# legacy adapter — explicit and tested, never silent
# ---------------------------------------------------------------------------


class TestLegacyAdapter:
    def _unbounded(self):
        # A legacy envelope with no cumulative ceiling and no deadline.
        return {
            "envelope_id": "env-legacy",
            "authorized_principal": "owner",
            "subject_digest": DIGEST,
            "brief_digest": DIGEST,
            "scope": {},
            "max_request_bytes": 100,
            "max_output_tokens": 100,
            "max_inflight_requests": 2,
            "max_parallel_lanes": 2,
            "allowed_routes": ["byteplus/deepseek-v4.1-flash"],
            "irreversible_actions": [],
            "authority_evidence_digest": DIGEST,
        }

    def _enforcing(self):
        legacy = self._unbounded()
        legacy.update(
            deadline_utc="2026-12-31T00:00:00Z",
            max_requests=10,
            max_reserved_tokens=100,
        )
        return legacy

    def test_unbounded_legacy_becomes_observe_adapt(self):
        adapted = adapt_legacy_execution_envelope(self._unbounded())
        assert adapted["limit_policy"]["policy"] == "observe_adapt"
        assert adapted["deadline_utc"] is None
        assert "counters" not in adapted["limit_policy"]
        assert _validate("execution-envelope", adapted) == []

    def test_legacy_request_and_token_figures_are_ceilings_not_free_counters(self):
        # In the legacy shape max_requests/max_reserved_tokens were finite
        # ceilings. The adapter must not relabel them as harmless accounting
        # counters to escape enforcement: with a deadline they become an
        # enforcing policy carrying exactly those values.
        legacy = self._unbounded()
        legacy.update(
            deadline_utc="2026-12-31T00:00:00Z",
            max_requests=7,
            max_reserved_tokens=42,
        )
        adapted = adapt_legacy_execution_envelope(legacy)
        assert adapted["limit_policy"]["policy"] == "enforcing"
        assert adapted["limit_policy"]["enforcement"]["max_requests"] == 7
        assert adapted["limit_policy"]["enforcement"]["max_reserved_tokens"] == 42
        assert _validate("execution-envelope", adapted) == []

    def test_legacy_ceiling_without_deadline_is_rejected_not_downgraded(self):
        legacy = self._unbounded()
        legacy.update(max_requests=7, max_reserved_tokens=42)
        with pytest.raises(LegacyMigrationError):
            adapt_legacy_execution_envelope(legacy)

    def test_finite_legacy_ceiling_is_preserved_as_enforcing(self):
        adapted = adapt_legacy_execution_envelope(self._enforcing())
        assert adapted["limit_policy"]["policy"] == "enforcing"
        assert adapted["limit_policy"]["enforcement"]["max_requests"] == 10
        assert adapted["limit_policy"]["enforcement"]["deadline"] is True
        assert adapted["deadline_utc"] == "2026-12-31T00:00:00Z"
        assert _validate("execution-envelope", adapted) == []

    def test_legacy_ceiling_is_never_downgraded_to_observe_adapt(self):
        adapted = adapt_legacy_execution_envelope(self._enforcing())
        assert adapted["limit_policy"]["policy"] != "observe_adapt"

    def test_enforcing_legacy_without_deadline_is_rejected(self):
        legacy = self._unbounded()
        legacy.update(max_requests=10, max_reserved_tokens=100)
        with pytest.raises(LegacyMigrationError):
            adapt_legacy_execution_envelope(legacy)

    def test_missing_required_field_is_rejected(self):
        legacy = self._unbounded()
        del legacy["authority_evidence_digest"]
        with pytest.raises(LegacyMigrationError):
            adapt_legacy_execution_envelope(legacy)

    def test_unknown_schema_version_is_refused(self):
        legacy = self._unbounded()
        legacy["schema_version"] = "2.0.0"
        with pytest.raises(UnsupportedSchemaVersionError):
            adapt_legacy_execution_envelope(legacy)


# ---------------------------------------------------------------------------
# registry integration
# ---------------------------------------------------------------------------


class TestExecutionRegistry:
    def test_run_snapshot_state_refs_shared_run_state(self):
        schema = json.loads((EXECUTION / "run-snapshot-v1.schema.json").read_text())
        assert schema["properties"]["state"]["$ref"].startswith(
            "https://skillweave.dev/schemas/run-state/v1"
        )

    def test_state_ref_resolves_to_the_run_state_enum(self):
        instance = _valid("run-snapshot")
        instance["state"] = "not-a-real-state"
        assert _validate("run-snapshot", instance) != []


# ---------------------------------------------------------------------------
# installed-wheel regression — the digest must survive the build, not just
# a PYTHONPATH=src checkout
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def installed_wheel(tmp_path_factory):
    """Build the wheel from a pristine copy, install it, and load *that*.

    A ``PYTHONPATH=src`` test passes even when the wheel omits the nested
    ``execution/`` schemas, because the source tree is present on disk. This
    fixture closes that gap by packaging and installing for real, then importing
    the installed copy in a subprocess with ``PYTHONPATH`` pointed only at the
    installed target (and every other copy removed from ``sys.path``).

    The build runs against a **staged copy** assembled from the tracked package
    sources, never against ``ROOT`` in place. Building in place leaves a
    ``build/lib`` tree behind, and setuptools reuses it on the next build even
    after a package-data glob is removed — which would let a stale artefact
    satisfy the packaging assertions on a dirty developer tree. Staging the
    sources guarantees the test exercises the *current* ``pyproject.toml``
    package-data declaration every run.
    """
    staging = tmp_path_factory.mktemp("stage")
    shutil.copy2(ROOT / "pyproject.toml", staging / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", staging / "README.md")
    shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
    shutil.copytree(ROOT / "src" / "skillweave_sdk",
                    staging / "src" / "skillweave_sdk")

    outdir = tmp_path_factory.mktemp("wheel")
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(outdir)],
        cwd=str(staging), capture_output=True, text=True,
    )
    if build.returncode != 0:
        # The build step is a hard requirement: skipping on its absence would
        # hide exactly the packaging gap this test exists to catch.
        raise AssertionError(
            f"wheel build failed ({build.returncode})\nstdout:\n{build.stdout}\n"
            f"stderr:\n{build.stderr}"
        )
    wheels = sorted(outdir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, found {[w.name for w in wheels]}"
    wheel = wheels[0]

    target = tmp_path_factory.mktemp("installed")
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
         "--no-build-isolation", "--quiet", "--target", str(target), str(wheel)],
        capture_output=True, text=True,
    )
    if install.returncode != 0:
        raise AssertionError(
            f"wheel install failed ({install.returncode})\nstdout:\n"
            f"{install.stdout}\nstderr:\n{install.stderr}"
        )
    return wheel, target


def _probe_installed(target: Path) -> dict:
    """Import the installed package in a clean subprocess and report facts.

    ``sys.path`` is rebuilt to contain only the target, so a source-tree copy
    cannot satisfy the import by accident.
    """
    probe = (
        "import json, sys\n"
        "sys.path[:] = [p for p in sys.path if p not in ('', '.')]\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import skillweave_sdk\n"
        "import skillweave_sdk.validator as v\n"
        "from importlib.resources import files\n"
        "import os\n"
        "root = os.path.dirname(skillweave_sdk.__file__)\n"
        "exec_dir = files('skillweave_sdk') / 'schemas' / 'execution'\n"
        "print(json.dumps({\n"
        "  'module_file': skillweave_sdk.__file__,\n"
        "  'import_root': root,\n"
        "  'canonical_digest': v.canonical_digest(),\n"
        "  'registry_size': len(v.load_registry()),\n"
        "  'execution_files': sorted(p.name for p in exec_dir.iterdir()),\n"
        "}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(target)],
        cwd=str(target), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""},
    )
    assert result.returncode == 0, (
        f"importing the installed wheel failed\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestInstalledWheel:
    def test_wheel_contains_every_execution_schema(self, installed_wheel):
        wheel, _ = installed_wheel
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
        execution = [
            n for n in names
            if n.startswith("skillweave_sdk/schemas/execution/") and n.endswith(".json")
        ]
        assert len(execution) == len(EXECUTION_SCHEMA_FILENAMES), (
            f"wheel ships {len(execution)} execution schemas, expected "
            f"{len(EXECUTION_SCHEMA_FILENAMES)}: {names}"
        )
        shipped = {n.rsplit("/", 1)[1] for n in execution}
        assert shipped == set(EXECUTION_SCHEMA_FILENAMES)

    def test_installed_wheel_imports_with_matching_digest(self, installed_wheel):
        _, target = installed_wheel
        facts = _probe_installed(target)
        # The import resolved to the installed copy, not the source tree.
        assert str(target) in facts["module_file"], facts
        assert facts["execution_files"], "no execution schemas visible after install"
        assert facts["registry_size"] == len(SCHEMA_FILENAMES) + len(EXECUTION_SCHEMA_FILENAMES)
        # The digest of the installed bytes equals the frozen digest — proving
        # the wheel carries the exact frozen schemas, not a stale subset.
        assert facts["canonical_digest"] == canonical_digest(SCHEMAS)
        assert facts["canonical_digest"] == EXPECTED_SCHEMA_DIGEST
