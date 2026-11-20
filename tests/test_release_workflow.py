"""The Forgejo release workflow is pinned by test, not by trust.

``.forgejo/workflows/forgejo-release.yml`` is the release path for this
repository: a tag push (or a manual dispatch) gates the title and the notes,
builds the release assets, then hands publication to the ops-engine entry point
(``ops-engine.release_attach``) for every destination in ``.ops.yaml``. This
test pins the invariants that path must not drift on, each of which has been
the shape of a real failure elsewhere in the estate:

1. **Trigger** — a ``v*.*.*`` tag push and a ``workflow_dispatch`` carrying a
   ``tag_name`` input. A release must be reachable both automatically and by an
   operator re-run after a partial publication.
2. **Assets** — ``scripts/build_release_assets.py`` runs *before* the publish
   step and the directory it writes is the directory the publish step hands to
   the entry point. A release that builds nothing, or builds somewhere the
   publish never looks, attaches nothing and still reports success.
3. **Engine ref** — one pinned ref, ``v3.7.0``, for both the wheel the entry
   point is installed from and the audience gate fetched beside it. A bare or
   floating install is the failure mode an unnoticed engine downgrade
   produces; installing an engine other than the one that publishes would gate
   the notes with a different engine than the one that attaches the assets.
4. **Secrets** — exactly ``GITHUB_TOKEN`` (the Forgejo token) and
   ``GH_MIRROR_TOKEN`` (the GitHub mirror token). Nothing else.
5. **No ``vars.*``** — the destinations are a committed file, not a CI variable.
6. **No release-object writer** — this workflow never creates or edits a
   release itself; publication is the engine's entry point, and the PRD's
   "the engine publish call that raced the layover is removed" means the
   workflow calls the CLI rather than importing ``ReleaseHandler`` inline.

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

# The one ref this workflow installs the engine from, fetches the audience gate
# from, and the two secrets the publication is allowed to read.
ENGINE_REF = "v3.7.0"
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


def _engine_refs(text: str) -> set[str]:
    """Every engine ref named: the wheel download and the audience-gate URL."""
    refs = set(re.findall(r"releases/download/(v[0-9][0-9.]*)/ops_engine-", text))
    refs |= set(re.findall(r"ops-engine/(v[0-9][0-9.]*)/", text))
    return refs


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
    refs = _engine_refs(text)
    assert refs == {ENGINE_REF}, (
        f"the engine must be installed from {ENGINE_REF!r} and nothing else; "
        f"found refs {sorted(refs) or '(none)'}"
    )
    # The pinned ref, not a commit or a branch: a floating or bare install is
    # the drift this pins down.
    assert (
        f"https://github.com/LangeVC/ops-engine/releases/download/"
        f"{ENGINE_REF}/ops_engine-3.7.0-py3-none-any.whl" in text
    ), "the entry point must be installed from the pinned wheel URL"
    assert "ops-engine==" not in text, "no PyPI (unpinned-index) install allowed"
    assert "git+https://" not in text, (
        "the engine must install from the released wheel, not a VCS ref"
    )
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


def _build_output_dir(doc: dict) -> str:
    """The directory the build step hands the build script, from the parsed YAML.

    One helper so that "the publish receives the built directory" is anchored
    to what the build step actually declares, and the two steps cannot be
    satisfied by two unrelated constants.
    """
    match = re.search(r"--output-dir[= ]+(\S+)", _step(doc, BUILD_STEP)["run"])
    assert match is not None, (
        "the build step must name the artifact directory via --output-dir"
    )
    return match.group(1).strip("'\"")


def _assert_publish_takes_directory(doc: dict, directory: str) -> None:
    """The publish step hands the built directory to the engine's entry point.

    The CLI's contract is ``--artifacts <dir>``: it reads the files from that
    directory and attaches them. Asserting the flag, rather than a literal
    ``publish_release(`` call, is what makes this test track the REA-015
    entry point instead of the in-workflow publish call REA-040 removes.
    """
    publish = _step(doc, PUBLISH_STEP)["run"]
    match = re.search(r"--artifacts[= ]+(\S+)", publish)
    assert match is not None, (
        "the publish step must pass the artifact directory to the engine via "
        "--artifacts"
    )
    got = match.group(1).strip("'\"")
    assert got == directory, (
        "the publish step must receive %r as its artifact directory; got %r"
        % (directory, got)
    )


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

    # The publish assertion is linked to *this* step's output directory, not to
    # a fixed string: a fixed string would also be satisfied by a publish step
    # that names a directory no build step wrote.
    build_dir = _build_output_dir(doc)
    _assert_publish_takes_directory(doc, build_dir)


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


def test_publish_step_calls_the_engine_entry_point(text: str) -> None:
    """Publication goes through REA-015, not an in-workflow publish call.

    The entry point is the module ``ops_engine.release_attach``; invoking it
    (``-m`` or the console script) is what makes the workflow a caller. The
    in-process ``ReleaseHandler.publish_release`` call is the shape REA-040
    removes, so neither the handler name nor a direct import may appear.
    """
    assert "ops_engine.release_attach" in text, (
        "the publish step must call the ops-engine release attach entry point"
    )
    assert "--artifacts" in text, "the entry point takes the built directory"
    assert "--repo-dir" in text, (
        "the entry point must receive --repo-dir so it merges the Layer-3 "
        ".ops.yaml and verifies the declared assets"
    )
    assert "ReleaseHandler" not in text, (
        "the workflow must not import the engine's release handler; the entry "
        "point is the only publication path"
    )
    assert "publish_release" not in text, (
        "the in-workflow publish call that raced the layover is removed"
    )


def test_destinations_are_read_from_the_committed_ops_yaml(text: str) -> None:
    """The entry point reads .ops.yaml; the workflow ships no config copy.

    ``--config`` is optional and is what makes this true: without it the entry
    point resolves the destinations, the release block and ``release.assets``
    from the ``.ops.yaml`` in ``--repo-dir``. Passing a generated config would
    publish from a second declaration that drifts from the layover's release
    block, so the workflow must not pass one — and must not write one either.
    """
    assert "--repo-dir" in text, (
        "the workflow must pass --repo-dir so the entry point reads the "
        "committed .ops.yaml"
    )
    assert ".ops.yaml" in text, "the workflow must name the committed .ops.yaml"
    for marker in ("--config", "OPS_ENGINE_CONFIG", "release-config", "config.yml"):
        assert marker not in text, (
            f"the workflow must not reference {marker!r}: without --config the "
            "entry point reads the committed .ops.yaml, and a generated config "
            "would drift from the layover's release block"
        )


def test_the_whole_asset_contract_holds_together(doc: dict, text: str) -> None:
    """The asset contract in one place, and nothing weakened.

    Every clause is asserted here so that a change weakening one of them turns
    this test red rather than passing through a gap between narrower tests:
    the build precedes the publish, the publish receives the asset directory,
    the secrets stay exactly the two named ones, no ``vars.*`` is read, and the
    removed "no asset upload" claim is not reintroduced anywhere in this module.
    """
    names = _step_names(doc)
    assert names.index(BUILD_STEP) < names.index(PUBLISH_STEP)

    build_dir = _build_output_dir(doc)
    assert build_dir, "the build step must name a non-empty output directory"
    _assert_publish_takes_directory(doc, build_dir)

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
    """The asset work changes neither the pin nor the destination source.

    The engine comes from exactly one ref, ``v3.7.0``, and the destinations
    still come from the committed ``.ops.yaml``. Both are asserted against the
    changed workflow, together with the asset directory the build step
    produces, so this test is red on the workflow that has no build step and
    red again if the pin or the ``.ops.yaml`` read is ever touched.
    """
    refs = _engine_refs(text)
    assert refs == {ENGINE_REF}, (
        f"the engine must be installed from exactly {ENGINE_REF!r}; "
        f"found {sorted(refs) or '(none)'}"
    )
    assert "ops-engine==" not in text, "no PyPI (unpinned-index) install allowed"
    assert "--repo-dir" in text, (
        "destinations must still be resolved from the committed .ops.yaml via "
        "--repo-dir, with no --config copy"
    )
    assert ".ops.yaml" in text, "the workflow must still name the committed .ops.yaml"
    assert "--config" not in text, (
        "the workflow must not pass --config: the entry point reads .ops.yaml"
    )

    # Non-vacuity: the invariants above are asserted next to the asset wiring,
    # so a workflow that never built an asset cannot pass this test by leaving
    # the pin and the destinations untouched.
    _assert_publish_takes_directory(doc, _build_output_dir(doc))
