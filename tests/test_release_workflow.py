"""The Forgejo release workflow is pinned by test, not by trust.

``.forgejo/workflows/forgejo-release.yml`` is the release path for this
repository: a tag push (or a manual dispatch) gates the title and the notes,
then publishes through the ops-engine ReleaseHandler to every destination in
``.ops.yaml``. This test pins the four invariants that path must not drift on,
each of which has been the shape of a real failure elsewhere in the estate:

1. **Trigger** — a ``v*.*.*`` tag push and a ``workflow_dispatch`` carrying a
   ``tag_name`` input. A release must be reachable both automatically and by an
   operator re-run after a partial publication.
2. **Engine ref** — the single pinned ref ``v3.4.2``. A bare or floating install
   is the failure mode an unnoticed engine downgrade produces; the pin is a
   fixed point, so it must appear verbatim.
3. **Secrets** — exactly ``GITHUB_TOKEN`` (the Forgejo token) and
   ``GH_MIRROR_TOKEN`` (the GitHub mirror token). Nothing else.
4. **No ``vars.*`` and no asset upload** — the SDK is consumed from published
   source by tag, so it builds no asset here and reads no Actions variable; the
   destinations are a committed file, not a CI variable.

The YAML is parsed (not merely grepped): a workflow that does not parse never
runs, and a text-only check would pass a file the runner would reject. Reading
``.ops.yaml`` is also asserted, because "publish to every destination" is only
true if the destinations are actually read from that file.
"""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".forgejo" / "workflows" / "forgejo-release.yml"

# The one ref this workflow installs the engine from, and the two secrets the
# publication is allowed to read.
ENGINE_REF = "v3.4.2"
ALLOWED_SECRETS = {"GITHUB_TOKEN", "GH_MIRROR_TOKEN"}


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    """Return the ``on:`` mapping.

    YAML 1.1 resolves the bare key ``on`` to the boolean ``True``, and a parser
    that follows GitHub's own handling leaves it a string. Accept either, so the
    test does not depend on which side of that fork the parser sits.
    """
    for key in ("on", True, "true"):
        if key in doc:
            return doc[key]
    raise AssertionError(f"workflow has no 'on:' trigger block; keys: {list(doc)}")


def test_workflow_parses_as_yaml(doc: dict) -> None:
    assert isinstance(doc, dict)
    assert "jobs" in doc and doc["jobs"], "workflow declares no jobs"


def test_triggers_on_version_tag_push(doc: dict) -> None:
    on = _triggers(doc)
    push = on.get("push")
    assert push is not None, "workflow does not trigger on push"
    assert "v*.*.*" in push.get("tags", []), (
        "the push trigger must be the semver tag glob 'v*.*.*'; got "
        f"{push.get('tags')!r}"
    )


def test_workflow_dispatch_takes_tag_name(doc: dict) -> None:
    on = _triggers(doc)
    dispatch = on.get("workflow_dispatch")
    assert dispatch is not None, "workflow has no workflow_dispatch trigger"
    inputs = dispatch.get("inputs", {})
    assert "tag_name" in inputs, (
        "workflow_dispatch must take a 'tag_name' input; got "
        f"{list(inputs)}"
    )
    assert inputs["tag_name"].get("required") is True, (
        "the 'tag_name' input must be required"
    )


def test_engine_is_pinned_to_the_exact_ref(text: str) -> None:
    refs = set(re.findall(r"ops-engine@(v[0-9][0-9.]*)", text))
    assert refs == {ENGINE_REF}, (
        f"the engine must be installed from {ENGINE_REF!r} and nothing else; "
        f"found install refs {sorted(refs)}"
    )
    # The pinned ref, not a commit or a branch: a floating or bare install is
    # the drift this pins down.
    assert "git+https://github.com/LangeVC/ops-engine@v3.4.2" in text
    assert "ops-engine==" not in text, "no PyPI (unpinned-index) install allowed"
    assert "/main" not in text and "@HEAD" not in text, "no floating ref allowed"


def test_audience_gate_is_fetched_from_the_same_pinned_ref(text: str) -> None:
    # The gate script is fetched by URL, so it carries its own ref; it must be
    # the same pinned ref as the install, or the notes are gated by a different
    # engine than the one that publishes.
    urls = re.findall(r"ops-engine/(v[0-9][0-9.]*)/", text)
    assert urls, "the audience gate is not fetched from a pinned engine ref"
    assert set(urls) == {ENGINE_REF}, (
        f"the audience gate must come from {ENGINE_REF!r}; found {sorted(set(urls))}"
    )


def test_only_the_two_named_secrets_are_read(text: str) -> None:
    used = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert used == ALLOWED_SECRETS, (
        "the workflow must read exactly "
        f"{sorted(ALLOWED_SECRETS)}; found {sorted(used)}"
    )


def test_no_actions_variable_is_read(text: str) -> None:
    assert "vars." not in text, (
        "the workflow must read no Actions variable; destinations and the engine "
        "ref are committed, not CI variables"
    )
    # Guard the expansion syntax too, in case a comment is ever added back that
    # names the token: the runner only expands the '${{ ... }}' form.
    assert "${{ vars." not in text


def test_no_asset_upload_step(text: str) -> None:
    lowered = text.lower()
    for marker in (
        "upload",
        "action-gh-release",
        "gh release upload",
        "softprops/action-gh-release",
    ):
        assert marker not in lowered, (
            f"the workflow must attach no asset, but it contains {marker!r}"
        )


def test_destinations_are_read_from_the_committed_ops_yaml(text: str) -> None:
    assert "load_ops_yaml" in text, (
        "destinations must be resolved from .ops.yaml via load_ops_yaml, not "
        "restated as literals"
    )
    assert ".ops.yaml" in text, "the workflow must name the committed .ops.yaml"
    assert "publish_release" in text, (
        "publication must go through the engine's ReleaseHandler.publish_release"
    )
