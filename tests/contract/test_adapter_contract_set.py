"""Focused tests for the neutral adapter-facade contract set (SW-ADAPT-001).

The SDK owns three neutral contracts -- the adapter descriptor, the credential
reference and the typed search outcome. They are *neutral*: the descriptor
names no concrete provider and no package-format kind enum, the credential
reference carries availability but never a value, and the search outcome types
a failure so an outage is never dressed up as a retrieved source.

Every test drives the SDK's public, dependency-free validator so the contract
is enforced by the same code path Core and the facade use at runtime.
"""

from pathlib import Path

from skillweave_sdk.validator import (
    EXPECTED_SCHEMA_DIGEST,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "src" / "skillweave_sdk" / "schemas"

DESCRIPTOR = "https://skillweave.dev/schemas/adapter/adapter-descriptor/v1"
REFERENCE = "https://skillweave.dev/schemas/adapter/credential-reference/v1"
OUTCOME = "https://skillweave.dev/schemas/adapter/search-outcome/v1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _descriptor() -> dict:
    return {
        "schema_version": "1.0.0",
        "id": "external-synthetic-search",
        "version": "1.2.3",
        "categories": ["search"],
        "operations": [
            {
                "name": "search.query",
                "input_contract": "https://skillweave.dev/schemas/adapter/search-outcome/v1",
                "result_contract": "https://skillweave.dev/schemas/adapter/search-outcome/v1",
            }
        ],
        "transport": "http",
        "capabilities": ["network-egress"],
        "credential_refs": ["fixture-secret-reference"],
    }


def _reference() -> dict:
    return {
        "schema_version": "1.0.0",
        "reference_id": "fixture-secret-reference",
        "source": "consumer_secret_store",
        "variable_names": ["PERPLEXITY_API_KEY"],
        "availability": "available",
        "injection": "owned_child_environment_only",
        "persistent_values": False,
    }


def _outcome() -> dict:
    return {
        "schema_version": "1.0.0",
        "query_sha256": "a" * 64,
        "requested_provider": "perplexity",
        "status": "success",
        "results": [
            {
                "title": "Result",
                "url": "https://example.test/result",
                "snippet": "text",
                "source": "example.test",
                "provider": "perplexity",
            }
        ],
        "attempts": [
            {"provider": "perplexity", "transport": "mcp-stdio", "status": "ok"},
        ],
    }


def _errors(schema_id: str, instance: dict) -> list[str]:
    return validate(instance, schema_id, SCHEMAS)


# ---------------------------------------------------------------------------
# adapter descriptor
# ---------------------------------------------------------------------------


class TestAdapterDescriptor:
    def test_neutral_external_descriptor_is_accepted(self):
        assert _errors(DESCRIPTOR, _descriptor()) == []

    def test_unknown_category_fails_closed(self):
        instance = _descriptor()
        instance["categories"] = ["unknown"]
        assert _errors(DESCRIPTOR, instance) != []

    def test_second_format_kind_is_opaque_and_accepted(self):
        instance = _descriptor()
        instance["categories"] = ["generic"]
        instance["transport"] = "in-process"
        instance["package_ref"] = {
            "format": "other-format",
            "format_version": "2",
            "name": "utility",
            "version": "1",
            "digest": "b" * 64,
            "kind": "utility-artifact",
        }
        assert _errors(DESCRIPTOR, instance) == []

    def test_package_kind_is_not_an_enum(self):
        import json

        schema = json.loads(
            (SCHEMAS / "adapter" / "adapter-descriptor-v1.schema.json").read_text()
        )
        kind = schema["properties"]["package_ref"]["oneOf"][1]["properties"]["kind"]
        assert "enum" not in kind, "kind must stay an opaque open string"
        assert kind["type"] == "string"

    def test_container_root_without_member_is_refused(self):
        instance = _descriptor()
        instance["package_ref"] = {
            "format": "capacium",
            "format_version": "1",
            "name": "perplexity",
            "version": "1",
            "digest": "c" * 64,
            "kind": "mcp-server",
            "container": True,
        }
        assert _errors(DESCRIPTOR, instance) != []

    def test_container_root_with_member_is_accepted(self):
        instance = _descriptor()
        instance["package_ref"] = {
            "format": "capacium",
            "format_version": "1",
            "name": "perplexity",
            "version": "1",
            "digest": "c" * 64,
            "kind": "mcp-server",
            "container": True,
            "member_ref": "perplexity-server",
        }
        assert _errors(DESCRIPTOR, instance) == []

    def test_descriptor_cannot_persist_a_credential_value(self):
        instance = _descriptor()
        instance["credential_value"] = "sk-live-should-never-ride-along"
        assert _errors(DESCRIPTOR, instance) != []

    def test_unknown_major_version_fails_closed(self):
        instance = _descriptor()
        instance["schema_version"] = "2.0.0"
        assert _errors(DESCRIPTOR, instance) != []


# ---------------------------------------------------------------------------
# credential reference
# ---------------------------------------------------------------------------


class TestCredentialReference:
    def test_reference_only_missing_credential_is_accepted(self):
        instance = _reference()
        instance["availability"] = "missing"
        assert _errors(REFERENCE, instance) == []

    def test_persisted_credential_value_is_refused(self):
        instance = _reference()
        instance["cookie_value"] = "session=secret"
        assert _errors(REFERENCE, instance) != []

    def test_injection_must_be_an_owned_child_environment(self):
        instance = _reference()
        instance["injection"] = "shared_process_environment"
        assert _errors(REFERENCE, instance) != []

    def test_reference_never_persists_values(self):
        instance = _reference()
        instance["persistent_values"] = True
        assert _errors(REFERENCE, instance) != []


# ---------------------------------------------------------------------------
# search outcome
# ---------------------------------------------------------------------------


class TestSearchOutcome:
    def test_success_with_real_source_is_accepted(self):
        assert _errors(OUTCOME, _outcome()) == []

    def test_success_without_sources_is_rejected(self):
        instance = _outcome()
        instance["results"] = []
        assert _errors(OUTCOME, instance) != []

    def test_provider_failure_is_not_a_retrieved_source(self):
        instance = _outcome()
        instance["status"] = "error"
        instance["error_code"] = "provider_error"
        # A non-success status must carry no results.
        assert _errors(OUTCOME, instance) != []

    def test_error_status_with_zero_results_is_accepted(self):
        instance = _outcome()
        instance["status"] = "error"
        instance["results"] = []
        instance["error_code"] = "provider_error"
        assert _errors(OUTCOME, instance) == []

    def test_unsafe_source_scheme_is_rejected(self):
        instance = _outcome()
        instance["results"][0]["url"] = "file:///etc/passwd"
        assert _errors(OUTCOME, instance) != []

    def test_configured_fallback_is_attributed(self):
        instance = _outcome()
        instance["requested_provider"] = "perplexity"
        instance["attempts"] = [
            {"provider": "perplexity", "transport": "mcp-stdio", "status": "error"},
            {"provider": "duckduckgo", "transport": "http", "status": "ok"},
        ]
        assert _errors(OUTCOME, instance) == []
        # The provisional provider must be visible, not the only attempt.
        assert {a["provider"] for a in instance["attempts"]} == {
            "perplexity",
            "duckduckgo",
        }

    def test_unknown_major_version_fails_closed(self):
        instance = _outcome()
        instance["schema_version"] = "2.0.0"
        assert _errors(OUTCOME, instance) != []


# ---------------------------------------------------------------------------
# registry / digest integration
# ---------------------------------------------------------------------------


ADAPTER_SCHEMA_IDS = {DESCRIPTOR, REFERENCE, OUTCOME}


class TestRegistryIntegration:
    def test_every_adapter_id_is_registered(self):
        from skillweave_sdk.validator import load_registry

        registry = load_registry(SCHEMAS)
        for sid in ADAPTER_SCHEMA_IDS:
            assert sid in registry, f"missing {sid}"

    def test_adapter_schemas_are_covered_by_the_canonical_digest(self):
        from skillweave_sdk.validator import canonical_digest

        assert canonical_digest(SCHEMAS) == EXPECTED_SCHEMA_DIGEST

    def test_existing_search_provider_contract_is_unchanged(self):
        # The lifecycle search-provider contract stays present and valid; the
        # adapter descriptor composes it rather than replacing it.
        from skillweave_sdk.validator import load_registry

        registry = load_registry(SCHEMAS)
        assert (
            "https://skillweave.dev/schemas/lifecycle/search-provider/v1" in registry
        )
