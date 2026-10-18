"""The Forgejo release workflow is pinned by test, not by trust.

``.forgejo/workflows/forgejo-release.yml`` is the release path for this
repository: a tag push (or a manual dispatch) gates the title and the notes,
builds the release assets, then publishes through the ops-engine ReleaseHandler
to every destination in ``.ops.yaml``. This test pins the invariants that path
must not drift on, each of which has been the shape of a real failure elsewhere
in the estate:

1. **Trigger** — a ``v*.*.*`` tag push and a ``workflow_dispatch`` carrying a
   ``tag_name`` input. A release must be reachable both automatically and by an
   operator re-run after a partial publication.
2. **Assets** — ``scripts/build_release_assets.py`` runs *before* the publish
   step and the directory it writes is the directory the publish step hands to
   the engine. A release that builds nothing, or builds somewhere the publish
   never looks, attaches nothing and still reports success.
3. **Engine ref** — the single pinned ref ``v3.4.2``. A bare or floating install
   is the failure mode an unnoticed engine downgrade produces; the pin is a
   fixed point, so it must appear verbatim.
4. **Secrets** — exactly ``GITHUB_TOKEN`` (the Forgejo token) and
   ``GH_MIRROR_TOKEN`` (the GitHub mirror token). Nothing else.
5. **No ``vars.*``** — the destinations are a committed file, not a CI variable.

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

BUILD_STEP = "Build the release assets"
PUBLISH_STEP = "Publish the release to every destination"


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


def _step_names(doc: dict) -> list:
    """The step names of every job, in the order the runner executes them."""
    names = []
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            names.append(step.get("name", ""))
    return names


def _step(doc: dict, name: str) -> dict:
    matches = [
        step
        for job in doc["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == name
    ]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r}; found {len(matches)}"
    )
    return matches[0]


def test_build_step_precedes_the_publish_step(doc: dict) -> None:
    """The assets must be built before the step that attaches them.

    Order is read from the parsed jobs, not from the text: a step declared
    after the publish would run after it and attach nothing.
    """
    names = _step_names(doc)
    assert PUBLISH_STEP in names, f"the workflow has no {PUBLISH_STEP!r} step"
    assert BUILD_STEP in names, f"the workflow has no {BUILD_STEP!r} step"
    assert names.index(BUILD_STEP) < names.index(PUBLISH_STEP), (
        f"{BUILD_STEP!r} must precede {PUBLISH_STEP!r}; got order {names}"
    )


def test_build_step_runs_the_build_script_into_an_asset_directory(doc: dict) -> None:
    """The build step runs the accepted script and names its output directory.

    The script's own command line is ``--output-dir <dir>``; the directory it
    writes and the directory the publish step reads must be the same one, or
    the release attaches nothing.
    """
    script = _step(doc, BUILD_STEP)["run"]
    assert "scripts/build_release_assets.py" in script, (
        "the build step must run the accepted scripts/build_release_assets.py"
    )

    build_dir = re.search(r"--output-dir[= ]+(\S+)", script)
    assert build_dir is not None, (
        "the build step must name the artifact directory via --output-dir"
    )
    build_dir = build_dir.group(1).strip("'\"")

    publish = _step(doc, PUBLISH_STEP)["run"]
    publish_dir = re.search(r'publish_release\((.*?)\)', publish, re.DOTALL)
    assert publish_dir is not None, "the publish step must call publish_release"
    assert f'"{build_dir}"' in publish_dir.group(1), (
        "the publish step must receive the asset directory "
        f"{build_dir!r} as its artifact directory; got {publish_dir.group(1)!r}"
    )


def test_build_step_gets_the_tag_being_released(doc: dict) -> None:
    """The assets are built for the tag the release is for.

    The build script takes ``--tag`` and refuses when it disagrees with the
    declared version; a build without the tag would attach assets for whatever
    version happens to be on disk.
    """
    step = _step(doc, BUILD_STEP)
    assert "--tag" in step["run"], (
        "the build step must pass --tag so the assets match the release tag"
    )
    assert "TAG_NAME" in step.get("env", {}), (
        "the build step must take the run's tag (TAG_NAME) as its tag input"
    )


def test_the_whole_asset_contract_holds_together(doc: dict, text: str) -> None:
    """Criterion 2 in one place: the new contract, and nothing weakened.

    Every clause is asserted here so that a change weakening one of them turns
    this test red rather than passing through a gap between narrower tests:
    the build precedes the publish, the publish receives the asset directory,
    the secrets stay exactly the two named ones, no ``vars.*`` is read, and the
    removed "no asset upload" claim is not reintroduced anywhere in this module.
    """
    names = _step_names(doc)
    assert names.index(BUILD_STEP) < names.index(PUBLISH_STEP)

    assert re.search(
        r"--output-dir[= ]+\S*release-assets", _step(doc, BUILD_STEP)["run"]
    ), "the build must write into the release-assets directory"
    assert '"release-assets"' in _step(doc, PUBLISH_STEP)["run"], (
        "the publish step must receive the release-assets directory"
    )

    used = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert used == ALLOWED_SECRETS, (
        f"the secrets must stay {sorted(ALLOWED_SECRETS)}; found {sorted(used)}"
    )
    assert "vars." not in text, "no Actions variable may be read"

    # The needles are assembled from parts so this test does not match itself:
    # a literal here would make the assertion pass for the wrong reason.
    source = Path(__file__).read_text(encoding="utf-8")
    claim = "must attach " + "no asset"
    assert claim not in source, (
        "the old no-asset-upload claim must be removed, not restated weakly"
    )
    removed_test = "def test_" + "no_asset_upload_step"
    assert removed_test not in source, (
        "the old 'no asset upload' assertion must be removed, not renamed"
    )
    assert "softprops/action-gh-release" not in text.lower(), (
        "the workflow must not attach assets through a third-party action; the "
        "engine attaches the built directory"
    )


def test_pin_and_committed_destinations_survive_the_asset_upgrade(
    doc: dict, text: str
) -> None:
    """Criterion 3: the asset work changes neither the pin nor the destination source.

    The engine is installed from exactly one ref, ``v3.4.2``, and the
    destinations still come from the committed ``.ops.yaml``. Both are asserted
    against the changed workflow, together with the asset directory the build
    step produces, so this test is red on the workflow that has no build step
    and red again if the pin or the ``.ops.yaml`` read is ever touched.
    """
    refs = set(re.findall(r"ops-engine@(v[0-9][0-9.]*)", text))
    assert refs == {ENGINE_REF}, (
        f"the engine must be installed from exactly {ENGINE_REF!r}; "
        f"found {sorted(refs)}"
    )
    assert "ops-engine==" not in text, "no PyPI (unpinned-index) install allowed"
    assert "load_ops_yaml" in text, (
        "destinations must still be resolved from .ops.yaml via load_ops_yaml"
    )
    assert ".ops.yaml" in text, "the workflow must still name the committed .ops.yaml"

    # Non-vacuity: the invariants above are asserted next to the asset wiring,
    # so a workflow that never built an asset cannot pass this test by leaving
    # the pin and the destinations untouched.
    assert re.search(
        r"--output-dir[= ]+\S*release-assets", _step(doc, BUILD_STEP)["run"]
    ), "the build step must write the assets into release-assets"
    assert '"release-assets"' in _step(doc, PUBLISH_STEP)["run"], (
        "the publish step must receive the release-assets directory"
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
