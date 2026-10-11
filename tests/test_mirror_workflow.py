"""Pin the SDK mirror workflow to the ops-engine v3.4.2 contract.

The accepted SW-SDK-REL-002 shipped a mirror workflow that imported ``yaml``
and ``ops_engine`` -- neither of which the bare Forgejo runner has -- so no
mirror to GitHub ever happened.  This module pins the *real* ops-engine v3.4.2
mirror workflow instead: an install-free, stdlib-only force-push whose
destination scan reads the committed ``.ops.yaml``.

It pins two things and no more:

  1. Static: ``.forgejo/workflows/mirror.yml``, with its comment and blank
     lines removed, is byte-for-byte the v3.4.2 workflow body.  The body is
     embedded below as ``_V3_4_2_WORKFLOW_BODY_NORMALIZED`` -- the v3.4.2 file
     with comments and blank lines stripped -- because the SDK cannot import
     the private ops-engine at test time.  Any regression in a non-comment line
     of the workflow fails here.  ``.ops.yaml`` is pinned to its shape: exactly
     two destinations, each with exactly the keys ``forge``, ``repo``,
     ``role`` and ``visibility``.
  2. Behavioural: the scan step's embedded Python is *run* against the real
     ``.ops.yaml`` and its stdout must be exactly the public mirror destination.

The scan step is located by its ``python3 - <<'PY'`` heredoc contract, not by
file position, so the module pins the contract -- the scan and its output --
and never a hand-copied restatement of the YAML.
"""

import ast
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".forgejo" / "workflows" / "mirror.yml"
OPS_YAML = ROOT / ".ops.yaml"

# The single secret the workflow may require.  It is a human-operated forge
# secret; any other name would be a hidden dependency on an unreviewed surface.
ONLY_SECRET = "GH_MIRROR_TOKEN"

# The retired install-time knobs.  Their presence anywhere -- including inside a
# comment -- re-introduces the operator-selected artifact path this change
# removes, so the check is a plain substring scan.
FORBIDDEN_STRINGS = (
    "OPS_ENGINE_VERSION",
    "OPS_ENGINE_WHEEL_URL",
    "OPS_ENGINE_WHEEL_SHA256",
)

# The one destination the embedded scan must resolve: the public read-only
# mirror.  The canonical Forgejo destination is deliberately not the answer --
# the scan selects ``forge == "github"``.
EXPECTED_MIRROR_DESTINATION = "SkillWeave-AI/skillweave-sdk"

# The two destinations ``.ops.yaml`` must declare, as (forge, repo, role,
# visibility) tuples.
EXPECTED_DESTINATIONS = (
    ("forgejo", "skillweave/skillweave-sdk", "release", "public"),
    ("github", "SkillWeave-AI/skillweave-sdk", "release", "public"),
)

# ops-engine v3.4.2 ``.forgejo/workflows/mirror.yml`` with its comment lines and
# blank lines removed.  The only difference permitted in the SDK copy is its
# header comment (see the module docstring); that is why the comparison below
# normalises both sides identically first.
_V3_4_2_WORKFLOW_BODY_NORMALIZED = r'''name: Mirror to GitHub
on:
  push:
    branches: ["**"]
    tags: ["v*"]
jobs:
  mirror:
    runs-on: ubuntu-latest
    steps:
      - uses: https://github.com/actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Push to GitHub mirror
        env:
          GH_MIRROR_TOKEN: ${{ secrets.GH_MIRROR_TOKEN }}
        run: |
          set -euo pipefail
          mirror_repo="$(
            python3 - <<'PY'
          import sys
          def _clean_scalar(raw):
              """Strip YAML quoting/inline-comment then flag anything leftover.
              The real loader (config_loader.load_ops_yaml) accepts forms this
              line-scan cannot parse: a single- or double-quoted scalar, and an
              inline comment after a value. Rather than pass the raw bytes
              (quotes, trailing comment) into a push remote that git then
              rejects with an obscure error, this mirror strips the exact YAML
              wrapping it understands and, when wrapping it does NOT understand
              remains, refuses by name instead of guessing.
              """
              v = raw.strip()
              if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                  v = v[1:-1].strip()
              marker = None
              if v and v[0] in "\"'":
                  marker = "an unmatched quote"
              elif "#" in v:
                  rest = v.split("#", 1)[1]
                  if rest.strip():
                      marker = "an inline comment"
              if marker:
                  return "", marker
              return v, None
          indent = None
          git_repos = []
          with open(".ops.yaml", "r", encoding="utf-8") as fh:
              in_destinations = False
              cur = {}
              cur_indent = None
              for lineno, raw in enumerate(fh, start=1):
                  line = raw.rstrip("\n")
                  stripped = line.strip()
                  if not stripped or stripped.startswith("#"):
                      continue
                  pad = len(line) - len(line.lstrip(" "))
                  if in_destinations and stripped == "destinations:":
                      raise LookupError("nested destinations is not supported")
                  if not in_destinations and stripped == "destinations:":
                      in_destinations = True
                      indent = pad
                      continue
                  if not in_destinations:
                      continue
                  if pad <= indent:
                      break
                  if stripped.startswith("- "):
                      if cur and cur.get("forge") == "github":
                          git_repos.append(cur["repo"])
                      cur = {}
                      cur_indent = pad
                      field = stripped[2:]
                      if ":" not in field:
                          raise LookupError("unexpected destinations sequence entry")
                      key, _, value = field.partition(":")
                      if key not in ("forge", "repo", "role", "visibility"):
                          raise LookupError("unknown destination field: %r" % key)
                      value, marker = _clean_scalar(value)
                      if marker:
                          sys.stderr.write(
                              "MirrorDestinationBoundaryError: .ops.yaml destination value "
                              "at line %d carries %s; this mirror does not parse YAML and "
                              "cannot be sure which repo it pushes to. Refusing to "
                              "guess a push remote over one a reviewer did not see.\n"
                              % (lineno, marker)
                          )
                          sys.exit(2)
                      cur[key] = value
                  else:
                      if cur_indent is None or pad <= cur_indent:
                          raise LookupError(
                              "unexpected destinations indentation at line %d" % lineno
                          )
                      key, _, value = stripped.partition(":")
                      if ":" not in stripped or not value.strip():
                          raise LookupError(
                              "destination value must be `key: value` at line %d" % lineno
                          )
                      if key not in ("forge", "repo", "role", "visibility"):
                          raise LookupError("unknown destination field: %r" % key)
                      value, marker = _clean_scalar(value)
                      if marker:
                          sys.stderr.write(
                              "MirrorDestinationBoundaryError: .ops.yaml destination value "
                              "at line %d carries %s; this mirror does not parse YAML and "
                              "cannot be sure which repo it pushes to. Refusing to "
                              "guess a push remote over one a reviewer did not see.\n"
                              % (lineno, marker)
                          )
                          sys.exit(2)
                      cur[key] = value
              if in_destinations and cur and cur.get("forge") == "github":
                  git_repos.append(cur["repo"])
          if len(git_repos) != 1:
              sys.stderr.write(
                  "MirrorDestinationBoundaryError: .ops.yaml must declare exactly one "
                  "github destination (the mirror); refusing to push an ambiguous or "
                  "unconfigured mirror remote rather than silently choosing one.\n"
              )
              sys.exit(2)
          sys.stdout.write(git_repos[0] + "\n")
          PY
          )"
          remote="https://x-access-token:${GH_MIRROR_TOKEN}@github.com/${mirror_repo}.git"
          git push --force "$remote" "+${GITHUB_REF}:${GITHUB_REF}"
'''


def _normalize(text: str) -> str:
    """Drop comment lines and blank lines, the way the contract ignores them."""
    kept = [
        line.rstrip()
        for line in text.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept) + "\n"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _scan_step_run() -> str:
    """Return the single ``run: |`` block scalar from the workflow.

    The workflow has exactly one -- the install-free scan/push step -- so the
    block is identified structurally (a ``run: |`` key whose value is a literal
    block scalar), not by any display name.
    """
    lines = _workflow_text().split("\n")
    runs = []
    index = 0
    while index < len(lines):
        header = re.match(r"^(\s*)run:\s*\|\s*$", lines[index])
        if not header:
            index += 1
            continue
        header_indent = len(header.group(1))
        body = []
        cursor = index + 1
        content_indent = None
        while cursor < len(lines):
            line = lines[cursor]
            if line.strip() == "":
                body.append("")
                cursor += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= header_indent:
                break
            if content_indent is None:
                content_indent = indent
            body.append(line[content_indent:])
            cursor += 1
        while body and body[-1] == "":
            body.pop()
        runs.append("\n".join(body))
        index = cursor
    assert len(runs) == 1, f"expected exactly one `run: |` block, found {len(runs)}"
    return runs[0]


def _extract_heredoc(script: str) -> str:
    """Return the body of the ``python3 - <<'LABEL' ... LABEL`` heredoc."""
    match = re.search(r"<<'([A-Za-z_][A-Za-z0-9_]*)'", script)
    assert match, "the scan step has no python3 - <<'LABEL' heredoc"
    label = match.group(1)
    lines = script.split("\n")
    start = None
    for index, line in enumerate(lines):
        if re.search(r"<<'" + re.escape(label) + r"'", line):
            start = index + 1
            break
    assert start is not None, "the heredoc opener could not be located"
    body = []
    for line in lines[start:]:
        if line.strip() == label:
            break
        body.append(line)
    else:
        raise AssertionError("the heredoc has no closing label")
    return textwrap.dedent("\n".join(body))


def _run_embedded_scan() -> subprocess.CompletedProcess:
    """Run the scan step's embedded Python against the committed ``.ops.yaml``.

    The program is piped to ``python3 -`` exactly as the runner does; the CWD
    is the repository root, which is where the scan opens ``.ops.yaml``.
    """
    source = _extract_heredoc(_scan_step_run())
    return subprocess.run(
        ["python3", "-"],
        input=source,
        text=True,
        capture_output=True,
        cwd=str(ROOT),
    )


def _parse_destinations(text: str) -> list:
    """Parse the ``destinations`` block of ``.ops.yaml`` without importing yaml.

    The manifest is rigid (one sequence of ``key: value`` mappings), so a
    minimal line scan is enough to pin its shape.  This is independent of the
    workflow's own scan on purpose: the manifest contract is checked against the
    plain file, not against the program under test.
    """
    lines = text.split("\n")
    base_indent = None
    entries = []
    current = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if base_indent is None:
            if stripped == "destinations:":
                base_indent = len(raw) - len(raw.lstrip(" "))
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= base_indent:
            break
        if stripped.startswith("- "):
            current = {}
            entries.append(current)
            stripped = stripped[2:].strip()
        key, _, value = stripped.partition(":")
        current[key.strip()] = value.strip()
    return entries


# ---------------------------------------------------------------------------
# AC1 -- the workflow is the ops-engine v3.4.2 mirror workflow, verbatim
# ---------------------------------------------------------------------------


def test_workflow_body_is_v3_4_2_verbatim():
    assert _normalize(_workflow_text()) == _V3_4_2_WORKFLOW_BODY_NORMALIZED, (
        "mirror.yml, with comments and blank lines removed, is not the "
        "ops-engine v3.4.2 mirror workflow; only its header comment may differ"
    )


def test_scan_heredoc_is_valid_python():
    ast.parse(_extract_heredoc(_scan_step_run()))


def test_workflow_imports_neither_yaml_nor_ops_engine():
    lowered = _workflow_text().lower()
    assert "import yaml" not in lowered, "mirror.yml imports yaml; the runner has none"
    assert "from yaml" not in lowered, "mirror.yml imports yaml; the runner has none"
    assert "ops_engine" not in lowered, (
        "mirror.yml references ops_engine; the runner does not have it"
    )


def test_workflow_is_install_free():
    lowered = _workflow_text().lower()
    for needle in ("pip install", "pip3 install", "curl ", "wget "):
        assert needle not in lowered, (
            f"mirror.yml appears to install or fetch something ({needle!r}); "
            "the v3.4.2 mirror provisions nothing"
        )


def test_forbidden_install_time_strings_absent():
    text = _workflow_text()
    present = [needle for needle in FORBIDDEN_STRINGS if needle in text]
    assert not present, (
        "mirror.yml still carries retired install-time pins "
        f"{present}; the v3.4.2 mirror has none"
    )


def test_workflow_reads_no_actions_variables():
    variables = sorted(set(re.findall(r"\bvars\.[A-Za-z0-9_]+", _workflow_text())))
    assert not variables, (
        f"mirror.yml reads Actions variables {variables}; the v3.4.2 mirror "
        "depends on no operator-provisioned variable"
    )


def test_only_secret_is_gh_mirror_token():
    secrets = sorted(set(re.findall(r"\bsecrets\.([A-Za-z0-9_]+)", _workflow_text())))
    assert secrets == [ONLY_SECRET], (
        f"mirror.yml references secrets {secrets}; the only permitted secret is "
        f"{ONLY_SECRET}"
    )


# ---------------------------------------------------------------------------
# AC2 -- .ops.yaml declares exactly the two destinations, in the v3.4.2 shape
# ---------------------------------------------------------------------------


def test_ops_yaml_declares_exactly_the_two_destinations():
    destinations = _parse_destinations(OPS_YAML.read_text(encoding="utf-8"))
    assert len(destinations) == 2, destinations
    for destination in destinations:
        assert set(destination) == {"forge", "repo", "role", "visibility"}, (
            f"destination {destination!r} must carry exactly the keys forge, "
            "repo, role, visibility -- no url, owner or name"
        )
    assert (
        tuple(
            (d["forge"], d["repo"], d["role"], d["visibility"])
            for d in destinations
        )
        == EXPECTED_DESTINATIONS
    )


# ---------------------------------------------------------------------------
# AC3 -- the embedded destination scan, run for real against .ops.yaml
# ---------------------------------------------------------------------------


def test_embedded_scan_prints_exactly_the_mirror_destination():
    assert OPS_YAML.is_file(), ".ops.yaml is missing from the repository root"
    result = _run_embedded_scan()
    assert result.returncode == 0, (
        "the embedded destination scan failed:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert result.stdout.strip() == EXPECTED_MIRROR_DESTINATION, (
        f"the embedded scan printed {result.stdout.strip()!r}; expected exactly "
        f"{EXPECTED_MIRROR_DESTINATION!r}"
    )


# ---------------------------------------------------------------------------
# AC3 -- the scan's refusal contract, pinned by running it against mutations
#
# The scan is not a YAML parser; it is a refusal-first reader of one rigid
# list.  A green run against the happy path would not show that it refuses:
# these tests run the *same extracted program* against deliberately broken
# manifests and require a non-zero exit, so the "refuses rather than guesses"
# property the workflow documents is actually exercised.
# ---------------------------------------------------------------------------


def _run_scan_against(tmp_path: Path, ops_yaml_body: str) -> subprocess.CompletedProcess:
    program = _extract_heredoc(_scan_step_run())
    (tmp_path / ".ops.yaml").write_text(ops_yaml_body, encoding="utf-8")
    return subprocess.run(
        ["python3", "-"],
        input=program,
        text=True,
        capture_output=True,
        cwd=str(tmp_path),
    )


def test_scan_accepts_a_single_github_destination(tmp_path: Path):
    result = _run_scan_against(
        tmp_path,
        "destinations:\n  - forge: github\n    repo: Owner/name\n    role: release\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Owner/name"


def test_scan_refuses_two_github_destinations(tmp_path: Path):
    result = _run_scan_against(
        tmp_path,
        "destinations:\n"
        "  - forge: github\n    repo: a/one\n    role: release\n"
        "  - forge: github\n    repo: b/two\n    role: release\n",
    )
    assert result.returncode != 0, "an ambiguous mirror destination must be refused"
    assert "MirrorDestinationBoundaryError" in result.stderr


def test_scan_refuses_an_unknown_destination_field(tmp_path: Path):
    result = _run_scan_against(
        tmp_path,
        "destinations:\n  - forge: github\n    url: https://example.invalid\n",
    )
    assert result.returncode != 0, "an unknown destination field must be refused"
    assert "unknown destination field" in result.stderr


def test_scan_refuses_an_inline_comment_on_a_value(tmp_path: Path):
    result = _run_scan_against(
        tmp_path,
        "destinations:\n  - forge: github\n    repo: Owner/name # not for pushing\n",
    )
    assert result.returncode != 0, "a value the line-scan cannot read must be refused"
    assert "MirrorDestinationBoundaryError" in result.stderr

