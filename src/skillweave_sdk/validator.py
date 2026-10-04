"""Schema validator for the SkillWeave SDK.

This module is the SDK's only validation surface. It is intentionally
dependency-free (Python standard library only): the SDK must install in a
clean environment without pulling the SkillWeave runtime.

It implements the subset of JSON Schema Draft 2020-12 that the SDK schemas
actually use, resolves cross-schema ``$ref`` by ``$id`` through a registry,
and exposes the canonical schema digest that every consumer pins.

The subset covers: ``$ref``, ``type``, ``required``, ``properties``,
``additionalProperties`` (boolean), ``items``, ``enum``, ``const``, ``oneOf``,
``allOf``, ``if``/``then``, ``minimum``, ``minItems``, ``minLength``,
``uniqueItems``, ``pattern``. Unrecognised keywords are ignored,
as Draft 2020-12 permits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from importlib.resources import files as _resources_files
except ImportError:
    # Python < 3.9 fallback (importlib_resources backport or
    # importlib.resources without .files).
    try:
        from importlib.resources import files as _resources_files
    except ImportError:
        _resources_files = None  # type: ignore[assignment]

# All schema filenames in the SDK contract set: 4 core + 9 lifecycle.
SCHEMA_FILENAMES = (
    "evidence.schema.json",
    "prompt-sequence.schema.json",
    "run-state.schema.json",
    "workflow-context.schema.json",
    "work-profile.schema.json",
    "lifecycle-profile.schema.json",
    "deliverable-contract.schema.json",
    "evidence-contract.schema.json",
    "category-pack.schema.json",
    "category-taxonomy.schema.json",
    "model-provider.schema.json",
    "search-provider.schema.json",
    "subject-ref.schema.json",
)

#: Directory (relative to a schema root) holding the frozen execution
#: contract set (SW-1510-CONTRACT). The SDK owns these wire/storage contracts;
#: Core, ops-engine and the router keep their own runtime ownership and consume
#: them through the SDK. The SDK never implements a second workflow runtime.
EXECUTION_SCHEMA_SUBDIR = "execution"

#: The frozen execution contract set. ``contract-catalog-v1`` is the SDK-owned
#: index of the set and does not index itself; ``contract-catalog-record-v1`` is
#: the ops-engine/router-owned surface published through a separate contract so
#: each surface has exactly one named owner.
EXECUTION_SCHEMA_FILENAMES = (
    "run-snapshot-v1.schema.json",
    "attempt-event-v1.schema.json",
    "evidence-receipt-v1.schema.json",
    "execution-envelope-v1.schema.json",
    "reservation-v1.schema.json",
    "route-decision-v1.schema.json",
    "capability-snapshot-v1.schema.json",
    "agent-message-v1.schema.json",
    "mirror-target-v1.schema.json",
    "contract-catalog-v1.schema.json",
    "contract-catalog-record-v1.schema.json",
)

# Canonical digest is computed over the sorted-key canonical JSON of every
# schema, concatenated in sorted-identity order. It changes iff the
# byte set changes. Covers both the flat core/lifecycle schemas and the nested
# execution contract set.
EXPECTED_SCHEMA_DIGEST = "d1b8c5396dff9bcf7ac8442bfa08c43e9e3eb35c0d24c269d2dccfcf2acf3b5e"


class SchemaDigestError(Exception):
    """Raised when the computed schema digest diverges from the canonical one."""


class SchemaError(Exception):
    """Raised when a schema file is missing or malformed."""


def _default_schemas_dir() -> Path:
    env = os.environ.get("SKILLWEAVE_SCHEMA_DIR")
    if env:
        return Path(env)
    if _resources_files is not None:
        try:
            return Path(str(_resources_files("skillweave_sdk") / "schemas"))
        except Exception:
            pass
    # Fallback: resolve relative to this file (source-tree dev).
    return Path(__file__).resolve().parent / "schemas"


def _canonical(schema: dict) -> bytes:
    return json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_schema(schemas_dir: Path, filename: str) -> dict:
    path = schemas_dir / filename
    if not path.is_file():
        raise SchemaError(f"missing schema file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid JSON in {path}: {exc}") from exc


def load_registry(schemas_dir: Path | None = None) -> dict[str, dict]:
    """Map every SDK schema's ``$id`` to its parsed schema object.

    Loads both the flat core/lifecycle schemas and the nested ``execution/``
    contract set so that cross-schema references between them resolve.
    """
    schemas_dir = schemas_dir or _default_schemas_dir()
    registry: dict[str, dict] = {}
    for filename in SCHEMA_FILENAMES:
        schema = read_schema(schemas_dir, filename)
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str):
            raise SchemaError(f"{filename}: missing string $id")
        registry[schema_id] = schema
    for filename in EXECUTION_SCHEMA_FILENAMES:
        schema = read_schema(schemas_dir / EXECUTION_SCHEMA_SUBDIR, filename)
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str):
            raise SchemaError(f"{filename}: missing string $id")
        registry[schema_id] = schema
    return registry


def _contract_files(schemas_dir: Path) -> list[tuple[str, Path]]:
    """Every contract file as a ``(stable identity key, path)`` pair."""
    files: list[tuple[str, Path]] = [
        (filename, schemas_dir / filename) for filename in SCHEMA_FILENAMES
    ]
    files.extend(
        (
            f"{EXECUTION_SCHEMA_SUBDIR}/{filename}",
            schemas_dir / EXECUTION_SCHEMA_SUBDIR / filename,
        )
        for filename in EXECUTION_SCHEMA_FILENAMES
    )
    return files


def canonical_digest(schemas_dir: Path | None = None) -> str:
    schemas_dir = schemas_dir or _default_schemas_dir()
    hasher = hashlib.sha256()
    for key, path in sorted(_contract_files(schemas_dir)):
        hasher.update(key.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(_canonical(read_schema(schemas_dir, key)))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def verify_canonical_digest(
    schemas_dir: Path | None = None,
    expected: str | None = None,
) -> None:
    """Verify the canonical schema digest.

    When *expected* is ``None`` (the normal case) the canonical digest embedded
    in this module is used. Callers that need to pin a specific SHA (e.g. a
    gate manifest) pass it explicitly.
    """
    if expected is None:
        expected = EXPECTED_SCHEMA_DIGEST
    computed = canonical_digest(schemas_dir)
    if computed != expected:
        raise SchemaDigestError(
            f"schema digest drifted: computed {computed}, "
            f"expected {expected}"
        )


# --- validation -----------------------------------------------------------

_TYPE = {
    dict: "object",
    list: "array",
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    type(None): "null",
}


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return expected == _TYPE.get(type(value))


def _deep_equal(a: Any, b: Any) -> bool:
    return a == b


def _resolve_ref(
    ref: str, root_schema: dict, registry: dict[str, dict]
) -> tuple[dict, dict]:
    """Resolve a ``$ref`` and return ``(schema, new_root)``.

    Local pointers resolve against the current root; a URI ``$id`` ref switches
    the root to the referenced file's schema so that its own ``#/...`` pointers
    resolve against it. A ``$ref`` with a fragment (e.g.
    ``https://.../v1#/$defs/category``) resolves the base URI in the registry
    then follows the JSON Pointer fragment.
    """
    if ref.startswith("#"):
        resolved = _resolve_pointer(root_schema, ref[1:])
        if not isinstance(resolved, dict):
            raise SchemaError(f"$ref does not resolve to a schema: {ref}")
        return resolved, root_schema
    if "#" in ref:
        base_uri, _, fragment = ref.partition("#")
        if base_uri in registry:
            resolved = _resolve_pointer(registry[base_uri], fragment)
            if not isinstance(resolved, dict):
                raise SchemaError(f"$ref does not resolve to a schema: {ref}")
            return resolved, registry[base_uri]
    if ref in registry:
        return registry[ref], registry[ref]
    raise SchemaError(f"unresolved $ref: {ref}")


def _resolve_pointer(root: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return root
    node = root
    for part in pointer.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit():
            node = node[int(part)]
        else:
            raise SchemaError(f"unresolved JSON pointer fragment: {pointer}")
    return node


def _validate(
    instance: Any,
    schema: dict,
    registry: dict[str, dict],
    path: str,
    root_schema: dict,
) -> list[str]:
    errors: list[str] = []

    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved, new_root = _resolve_ref(ref, root_schema, registry)
        return _validate(instance, resolved, registry, path, new_root)

    if "enum" in schema:
        enum = schema["enum"]
        if not any(_deep_equal(instance, member) for member in enum):
            errors.append(f"{path}: not one of {enum!r}")

    if "const" in schema:
        if not _deep_equal(instance, schema["const"]):
            errors.append(f"{path}: must be {schema['const']!r}")

    type_spec = schema.get("type")
    if type_spec is not None:
        types = type_spec if isinstance(type_spec, list) else [type_spec]
        if not any(_matches_type(instance, t) for t in types):
            errors.append(
                f"{path}: expected type {type_spec!r}, got "
                f"{_TYPE.get(type(instance))!r}"
            )

    if "minimum" in schema and isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if instance < schema["minimum"]:
            errors.append(
                f"{path}: {instance} is less than minimum {schema['minimum']}"
            )

    if "minLength" in schema and isinstance(instance, str):
        if len(instance) < schema["minLength"]:
            errors.append(
                f"{path}: length {len(instance)} is less than minimum "
                f"{schema['minLength']}"
            )

    if "pattern" in schema and isinstance(instance, str):
        if re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if "minItems" in schema and isinstance(instance, list):
        if len(instance) < schema["minItems"]:
            errors.append(
                f"{path}: expected at least {schema['minItems']} items, "
                f"got {len(instance)}"
            )

    if "uniqueItems" in schema and isinstance(instance, list):
        if schema["uniqueItems"]:
            seen = []
            for idx, item in enumerate(instance):
                for prev in seen:
                    if _deep_equal(item, prev):
                        errors.append(f"{path}[{idx}]: duplicate item")
                        break
                seen.append(item)

    if "allOf" in schema:
        all_of = schema["allOf"]
        for idx, subschema in enumerate(all_of):
            errors.extend(
                _validate(instance, subschema, registry, f"{path} (allOf[{idx}])", root_schema)
            )

    if "if" in schema:
        if_errors = _validate(instance, schema["if"], registry, path, root_schema)
        if not if_errors:
            if "then" in schema:
                errors.extend(
                    _validate(instance, schema["then"], registry, path, root_schema)
                )
        else:
            if "else" in schema:
                errors.extend(
                    _validate(instance, schema["else"], registry, path, root_schema)
                )

    if "oneOf" in schema:
        branch_results = []
        for branch in schema["oneOf"]:
            branch_results.append(
                _validate(instance, branch, registry, path, root_schema)
            )
        passing = [1 for be in branch_results if not be]
        if sum(passing) != 1:
            errors.append(
                f"{path}: must match exactly one of "
                f"{len(schema['oneOf'])} variants"
            )

    if "required" in schema and isinstance(instance, dict):
        for key in schema["required"]:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")

    if "properties" in schema and isinstance(instance, dict):
        props = schema["properties"]
        for key, subschema in props.items():
            if key in instance:
                errors.extend(
                    _validate(
                        instance[key], subschema, registry,
                        f"{path}.{key}", root_schema,
                    )
                )
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(
                        f"{path}: additional property {key!r} is not allowed"
                    )

    if "items" in schema and isinstance(instance, list):
        items_schema = schema["items"]
        for idx, item in enumerate(instance):
            errors.extend(
                _validate(item, items_schema, registry, f"{path}[{idx}]", root_schema)
            )

    return errors


def validate(
    instance: Any,
    schema_id: str,
    schemas_dir: Path | None = None,
) -> list[str]:
    """Validate *instance* against the schema identified by *schema_id*.

    Returns a list of error strings; an empty list means valid.
    """
    registry = load_registry(schemas_dir or _default_schemas_dir())
    if schema_id not in registry:
        raise SchemaError(f"unknown schema id: {schema_id}")
    return _validate(
        instance, registry[schema_id], registry, "$", registry[schema_id]
    )


def validate_file(path: Path, schema_id: str) -> list[str]:
    return validate(json.loads(path.read_text(encoding="utf-8")), schema_id)


# --- execution contract set: fail-closed version guard ---------------------


class UnsupportedSchemaVersionError(Exception):
    """Raised when an instance declares a schema_version the SDK cannot accept.

    Unknown or absent versions fail closed: the SDK never best-effort parses a
    future or unspecified wire contract.
    """


def supported_schema_versions(schemas_dir: Path | None = None) -> frozenset[str]:
    """The ``schema_version`` consts accepted by the execution contract set."""
    schemas_dir = schemas_dir or _default_schemas_dir()
    versions: set[str] = set()
    for filename in EXECUTION_SCHEMA_FILENAMES:
        schema = read_schema(schemas_dir / EXECUTION_SCHEMA_SUBDIR, filename)
        const = schema.get("properties", {}).get("schema_version", {}).get("const")
        if isinstance(const, str):
            versions.add(const)
    return frozenset(versions)


def require_supported_version(
    instance: Any,
    schemas_dir: Path | None = None,
) -> str:
    """Return the declared ``schema_version`` or fail closed.

    Raises :class:`UnsupportedSchemaVersionError` when the instance is not an
    object, has no ``schema_version``, or declares a version outside the set
    the SDK freezes. Callers use this before :func:`validate` so that an
    unknown version is refused explicitly rather than silently rejected or
    silently accepted.
    """
    if not isinstance(instance, dict):
        raise UnsupportedSchemaVersionError(
            f"expected an object with schema_version, got {type(instance).__name__}"
        )
    declared = instance.get("schema_version")
    if not isinstance(declared, str) or not declared:
        raise UnsupportedSchemaVersionError("missing required string schema_version")
    if declared not in supported_schema_versions(schemas_dir):
        raise UnsupportedSchemaVersionError(f"unsupported schema_version: {declared}")
    return declared


# --- external authority boundary (trusted service seam) --------------------
#
# A JSON schema validates untrusted wire data. It CANNOT authenticate. The
# functions below are the explicit boundary between the two: the instance is
# untrusted input, and every trusted fact (authenticated principal, service
# principal, issued handle ids, admitted authority decisions) must be supplied
# by the trusted service seam that runs this code. Flags a caller places inside
# the wire JSON (issuer="service", role="reviewer") are never consulted here.
#
# The runtime that owns the seam lives in Core / ops-engine. The SDK defines the
# contract and the pure decision function; it does not become that runtime.


class AuthorityBoundaryError(Exception):
    """Raised when untrusted wire data does not match the authenticated context."""


@dataclass(frozen=True)
class AuthenticatedServiceContext:
    """Trusted facts obtained from the authenticated service seam.

    This object is constructed by the runtime after authentication (mTLS, a
    signed service token, a native handle registry lookup). None of its fields
    may be populated from the wire instance under verification.
    """

    principal: str
    roles: frozenset[str]
    service_principal: str | None = None
    issued_handle_ids: frozenset[str] = frozenset()
    authority_decision_digests: frozenset[str] = frozenset()
    worker_principal: str | None = None


#: Roles that may author an independent reviewer/observer attestation.
REVIEWER_ROLES = frozenset({"reviewer", "observer"})

#: Roles that may issue a native run handle.
RUN_SERVICE_ROLES = frozenset({"run-service", "native-run-service", "observer"})


def verify_native_handle(handle: Any, context: AuthenticatedServiceContext) -> None:
    """Assert *handle* was issued by the authenticated service.

    Shape alone is not enough: the handle id must be in the service's issued
    registry and the recorded service principal must equal the authenticated
    one. A self-described ``{"handle_id": "x", "service_principal": "y"}`` from
    the wire therefore fails closed.
    """
    if not isinstance(handle, dict):
        raise AuthorityBoundaryError("native handle must be a service-issued object")
    service_principal = handle.get("service_principal")
    handle_id = handle.get("handle_id")
    if not context.service_principal or service_principal != context.service_principal:
        raise AuthorityBoundaryError(
            "native handle service_principal is not the authenticated service"
        )
    if not isinstance(handle_id, str) or handle_id not in context.issued_handle_ids:
        raise AuthorityBoundaryError("native handle was not issued by this service")


def verify_run_snapshot_authority(
    snapshot: Any, context: AuthenticatedServiceContext
) -> None:
    """Authority check for a run-snapshot-v1 wire record."""
    if not isinstance(snapshot, dict):
        raise AuthorityBoundaryError("run snapshot must be an object")
    if not (context.roles & RUN_SERVICE_ROLES):
        raise AuthorityBoundaryError("authenticated context lacks a run-service role")
    verify_native_handle(snapshot.get("process_identity"), context)
    _verify_decision(snapshot.get("authority_decision_digest"), context)


def verify_evidence_receipt_authority(
    receipt: Any, context: AuthenticatedServiceContext
) -> None:
    """Authority check for an evidence-receipt-v1 wire record.

    The claimed reviewer principal must equal the authenticated principal, the
    context must hold a reviewer/observer role, the authority decision must be
    one the service admitted, and the reviewer must not be the worker whose work
    is judged.
    """
    if not isinstance(receipt, dict):
        raise AuthorityBoundaryError("evidence receipt must be an object")
    claimed = receipt.get("principal")
    if not isinstance(claimed, str) or claimed != context.principal:
        raise AuthorityBoundaryError("receipt principal is not the authenticated reviewer")
    if not (context.roles & REVIEWER_ROLES):
        raise AuthorityBoundaryError("authenticated context lacks a reviewer role")
    if context.worker_principal is not None and claimed == context.worker_principal:
        raise AuthorityBoundaryError("a worker cannot attest its own attempt")
    issuer = receipt.get("issuer")
    if not isinstance(issuer, str) or not issuer:
        raise AuthorityBoundaryError("receipt issuer is missing")
    _verify_decision(receipt.get("authority_decision_digest"), context)


def _verify_decision(digest: Any, context: AuthenticatedServiceContext) -> None:
    if not isinstance(digest, str) or digest not in context.authority_decision_digests:
        raise AuthorityBoundaryError(
            "authority_decision_digest was not admitted by the trusted service"
        )


# --- explicit legacy adapters ---------------------------------------------
#
# Legacy consumers of the pre-SW-1510 flat shapes reach the frozen contracts
# only through these explicit, tested adapters. There is no silent coercion:
# an adapter that cannot represent a legacy value raises rather than guessing.

LEGACY_ENVELOPE_FIELDS = (
    "envelope_id",
    "authorized_principal",
    "subject_digest",
    "brief_digest",
    "scope",
    "deadline_utc",
    "max_requests",
    "max_reserved_tokens",
    "max_request_bytes",
    "max_output_tokens",
    "max_inflight_requests",
    "max_parallel_lanes",
    "allowed_routes",
    "irreversible_actions",
    "authority_evidence_digest",
)


def adapt_legacy_execution_envelope(legacy: dict) -> dict:
    """Map a pre-SW-1510 flat envelope onto the frozen execution-envelope-v1.

    The flat limits become an explicit ``observe_adapt`` policy: atomic counters
    are retained for accounting, per-request limits and active slots are
    preserved, and no hidden whole-job ceiling is created. A legacy
    ``max_correction_rounds`` has no observe_adapt representation and raises
    rather than being silently dropped.
    """
    if not isinstance(legacy, dict):
        raise AuthorityBoundaryError("legacy envelope must be an object")
    require_supported_version(legacy) if "schema_version" in legacy else None
    if "max_correction_rounds" in legacy:
        raise AuthorityBoundaryError(
            "legacy max_correction_rounds cannot be represented under "
            "observe_adapt; supply an explicit enforcing envelope instead"
        )
    missing = [f for f in LEGACY_ENVELOPE_FIELDS if f not in legacy]
    if missing:
        raise AuthorityBoundaryError(f"legacy envelope missing fields: {missing}")
    return {
        "schema_version": "1.0.0",
        "envelope_id": legacy["envelope_id"],
        "authorized_principal": legacy["authorized_principal"],
        "subject_digest": legacy["subject_digest"],
        "brief_digest": legacy["brief_digest"],
        "scope": legacy["scope"],
        "deadline_utc": legacy["deadline_utc"],
        "limit_policy": {
            "policy": "observe_adapt",
            "max_request_bytes": legacy["max_request_bytes"],
            "max_output_tokens": legacy["max_output_tokens"],
            "max_inflight_requests": legacy["max_inflight_requests"],
            "max_parallel_lanes": legacy["max_parallel_lanes"],
            "counters": {
                "max_requests": legacy["max_requests"],
                "max_reserved_tokens": legacy["max_reserved_tokens"],
            },
        },
        "allowed_routes": legacy["allowed_routes"],
        "irreversible_actions": legacy["irreversible_actions"],
        "authority_evidence_digest": legacy["authority_evidence_digest"],
    }
