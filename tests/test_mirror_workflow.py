"""Pin the reviewable mirror contract, and pin the *behaviour*, not a copy.

The mirror workflow is a small pipeline: it reads the destinations from
``.ops.yaml`` (the scan), proves the destination named there, then force-pushes
the triggering ref.  A green YAML file that *looks* right is not evidence; the
contract only holds if the destination the workflow would actually resolve is
the public mirror GitHub repository, and only the mirror token is required.

So this module does two things and no more:

  1. Static pins: ``.forgejo/workflows/mirror.yml`` parses, carries none of the
     retired install-time knobs (``OPS_ENGINE_VERSION``, ``OPS_ENGINE_WHEEL_URL``,
     ``OPS_ENGINE_WHEEL_SHA256``), reads no ``vars.*``, and needs exactly one
     secret.  ``GH_MIRROR_TOKEN`` is a *human-operated forge secret*; the
     retired trio came from the operator's Actions *variables* store, which is a
     different, non-reviewable authority surface -- that is why "no vars.* read"
     and "only GH_MIRROR_TOKEN" are the same property stated twice.
  2. The behavioural pin: it *runs* the scan step's embedded Python against the
     real ``.ops.yaml`` and asserts its stdout is exactly the mirror destination.
     A typo in the scan (wrong key, wrong field, wrong join) fails here even
     though the workflow still parses.
"""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".forgejo" / "workflows" / "mirror.yml"
OPS_YAML = ROOT / ".ops.yaml"

# The retired install-time dependency pins.  Their presence anywhere in the
# workflow -- including inside a comment -- re-introduces the operator-selected
# artifact path this change removes, so the check is a plain substring scan.
FORBIDDEN_STRINGS = (
    "OPS_ENGINE_VERSION",
    "OPS_ENGINE_WHEEL_URL",
    "OPS_ENGINE_WHEEL_SHA256",
)

# The single secret the workflow may require.  Anything else a workflow asks
# the operator to provision is a hidden dependency on an unreviewed surface.
ONLY_SECRET = "GH_MIRROR_TOKEN"

# The one destination the embedded scan must resolve: the public read-only
# mirror.  The canonical Forgejo destination is deliberately *not* the answer --
# the scan selects ``forge == "github"``.
EXPECTED_MIRROR_DESTINATION = "SkillWeave-AI/skillweave-sdk"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow_document() -> dict:
    return yaml.safe_load(_workflow_text())


def _run_embedded_py(source: str) -> subprocess.CompletedProcess:
    """Pipe an embedded heredoc body to ``python3`` the way the runner does.

    ``stdin`` carries the program, matching ``python3 - <<'PY'``; no shell
    expansion is layered on top, so the body under test is exactly the bytes in
    the workflow.
    """
    return subprocess.run(
        ["python3", "-"],
        input=source,
        text=True,
        capture_output=True,
        cwd=str(ROOT),
    )


# ---------------------------------------------------------------------------
# AC1 -- the workflow parses and carries no retired install-time dependency
# ---------------------------------------------------------------------------


def test_workflow_parses_as_yaml():
    document = _workflow_document()
    assert isinstance(document, dict), "mirror.yml must be a YAML mapping"
    assert "jobs" in document and document["jobs"], "mirror.yml declares no jobs"


def test_workflow_is_install_free():
    """No step fetches or installs the engine: the workflow provisions nothing.

    Install-free is stronger than "no wheel URL": a ``pip install`` by name is
    still a provisioning step whose result depends on an index the review cannot
    see. The mirror job must not install the engine at all.
    """
    lowered = _workflow_text().lower()
    for needle in ("pip install", "pip3 install", "curl ", "wget "):
        assert needle not in lowered, (
            f"mirror.yml appears to install or fetch something ({needle!r}); "
            "the mirror must be install-free"
        )


def test_forbidden_install_time_strings_absent():
    text = _workflow_text()
    present = [needle for needle in FORBIDDEN_STRINGS if needle in text]
    assert not present, (
        "mirror.yml still carries retired install-time pins "
        f"{present}; the mirror must be install-free"
    )


def test_workflow_reads_no_actions_variables():
    text = _workflow_text()
    variables = sorted(set(re.findall(r"\bvars\.[A-Za-z0-9_]+", text)))
    assert not variables, (
        f"mirror.yml reads Actions variables {variables}; the mirror must depend "
        "on no operator-provisioned variable"
    )


# ---------------------------------------------------------------------------
# AC2 -- exactly one secret, and it is the mirror token
# ---------------------------------------------------------------------------


def test_only_secret_is_gh_mirror_token():
    text = _workflow_text()
    secrets = sorted(set(re.findall(r"\bsecrets\.([A-Za-z0-9_]+)", text)))
    assert secrets == [ONLY_SECRET], (
        f"mirror.yml references secrets {secrets}; the only permitted secret is "
        f"{ONLY_SECRET}"
    )


# ---------------------------------------------------------------------------
# AC3 -- the embedded destination scan, run for real against .ops.yaml
# ---------------------------------------------------------------------------


def run_scan():
    """Locate the scan step and execute its embedded Python against .ops.yaml.

    The step is found by its *comment marker* (``# DESTINATION_SCAN``), not by
    its display name, so the established ``- name:`` lines stay in place.  The
    Python is the ``python3 - <<'LABEL' ... LABEL`` heredoc body inside that
    step's ``run:`` script.
    """
    document = _workflow_document()
    steps = document["jobs"]["mirror"]["steps"]

    matches = [
        step
        for step in steps
        if "DESTINATION_SCAN" in (step.get("run") or "")
    ]
    assert len(matches) == 1, (
        f"expected exactly one step marked '# DESTINATION_SCAN', found {len(matches)}"
    )
    run = matches[0]["run"]

    heredoc = re.search(
        r"<<'([A-Za-z_][A-Za-z0-9_]*)'\n(.*?)\n\1\b", run, re.DOTALL
    )
    assert heredoc, "the DESTINATION_SCAN step has no python3 - <<'LABEL' heredoc"
    source = heredoc.group(2)

    result = _run_embedded_py(source)
    assert result.returncode == 0, (
        "the embedded destination scan failed:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return result.stdout


def test_embedded_scan_prints_exactly_the_mirror_destination():
    # The scan runs with CWD=ROOT, so it reads the committed .ops.yaml.
    assert OPS_YAML.is_file(), ".ops.yaml is missing from the repository root"
    stdout = run_scan()
    assert stdout.strip() == EXPECTED_MIRROR_DESTINATION, (
        f"the embedded scan printed {stdout.strip()!r}; expected exactly "
        f"{EXPECTED_MIRROR_DESTINATION!r}"
    )


def test_ops_yaml_contract():
    """The manifest is the source the scan reads, so pin its shape too."""
    manifest = yaml.safe_load(OPS_YAML.read_text(encoding="utf-8"))
    destinations = manifest["destinations"]
    forges = sorted(d["forge"] for d in destinations)
    assert forges == ["forgejo", "github"], forges

    by_forge = {d["forge"]: d for d in destinations}
    assert by_forge["forgejo"]["owner"] == "skillweave"
    assert by_forge["github"]["owner"] == "SkillWeave-AI"
    for dest in destinations:
        assert dest["name"] == "skillweave-sdk"
        assert dest["role"] == "release"
        assert dest["visibility"] == "public"
