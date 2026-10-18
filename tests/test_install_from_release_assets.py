"""The SDK installs from its release assets alone, and the suite proves it.

``scripts/build_release_assets.py`` is what a release attaches: one wheel, one
sdist and a ``SHA256SUMS``. The install path the core documentation will name
is ``pip install --no-index --find-links <asset dir> skillweave-sdk`` — a plain
pip with no index, served only by the files in that directory. Until this test
existed that path was trusted, not exercised.

Three things must hold, and each is checked against a real install rather than
against the script's own summary:

1. **The assets install and name their own version.** The built assets are
   installed into a fresh virtual environment by that environment's own pip,
   ``skillweave_sdk`` is imported from the installed package, and the version
   ``importlib.metadata`` reports for the distribution equals the version
   ``pyproject.toml`` declares.
2. **No index is reachable.** A requirement that the asset directory cannot
   satisfy fails by name, and pip contacts no index on the way — otherwise the
   install above would silently succeed by reaching a network the release host
   does not have. A host without the standard ``venv`` module is a named skip,
   never a silent pass.
3. **Nothing is left behind and nothing outside the test changes.** The build
   and the install run in temporary directories, no ``build``, ``dist`` or
   environment directory appears in the source tree, and the interpreter
   running the suite is the same before and after.

The wheel is installed, not built, so the install needs no index and no build
backend: the ``--no-index`` install is the release path, and a wheel that
needed a download would be a finding rather than a test setup detail.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_release_assets.py"

# One instant for the build, so the assets are the bytes a release publishes.
EPOCH = 1_700_000_000

# A build and an install each start a process that compiles nothing but can be
# slow on a loaded host; the timeout is a guard against a hung pip, not a
# performance budget.
TIMEOUT_SECONDS = 1800

DISTRIBUTION_NAME = "skillweave-sdk"
IMPORT_NAME = "skillweave_sdk"

# The distribution a release never contains. Asking for it is how the
# no-index behaviour is shown: pip may not fall back to a network.
ABSENT_REQUIREMENT = "skillweave-sdk-not-in-the-release-assets"

NAMED_SKIP_NO_VENV = (
    "named skip: this interpreter has no standard venv module, so it cannot "
    "create the clean environment the release install is proven in; the "
    "install path is unproven on this host, not proven to fail"
)
NAMED_SKIP_NO_ENSUREPIP = (
    "named skip: this interpreter has no ensurepip module, so a virtual "
    "environment cannot carry the pip that performs the release install; the "
    "install path is unproven on this host, not proven to fail"
)
NAMED_FAIL_INSTALL = (
    "the install from the release assets failed; the assets alone must satisfy "
    "`pip install --no-index --find-links` and no index is configured to fall "
    "back to"
)
NAMED_FAIL_INDEX_REACHABLE = (
    "pip reported an index it could not reach or consulted; the release "
    "install must be served by the asset directory alone and contact no "
    "network"
)

# pip prints one of these when a requirement is not satisfied locally. Either
# wording is the named cause; the test does not accept a bare non-zero exit.
_NO_DISTRIBUTION_MESSAGES = (
    "No matching distribution found",
    "Could not find a version that satisfies the requirement",
)
_URL = re.compile(r"https?://")
_INDEX_LINE = re.compile(r"Looking in indexes", re.IGNORECASE)

# pytest writes these while the suite runs; they are not the source tree this
# test is about.
_FINGERPRINT_SKIP = {".git", "__pycache__", ".pytest_cache", ".benchmarks"}


def _declared_version() -> str:
    """The version ``pyproject.toml`` declares, read the way a release reads it."""
    with open(ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _require_venv():
    """The standard ``venv`` module, or a named skip — never a silent pass.

    The module is imported only after the availability check, so a host that
    lacks it reaches the skip instead of an ImportError at collection time.
    """
    spec = importlib.util.find_spec("venv")
    if spec is None or spec.loader is None:
        pytest.skip(NAMED_SKIP_NO_VENV)
    return importlib.import_module("venv")


def _require_ensurepip() -> None:
    """A virtual environment whose ``with_pip`` can work, or a named skip."""
    spec = importlib.util.find_spec("ensurepip")
    if spec is None or spec.loader is None:
        pytest.skip(NAMED_SKIP_NO_ENSUREPIP)


def _child_env() -> dict:
    """The environment a child process gets: local files only.

    ``PYTHONPATH`` and ``PYTHONHOME`` are dropped so the child cannot import
    the source tree instead of what it installed, and the pip index variables
    are dropped so an operator's ``PIP_EXTRA_INDEX_URL`` cannot quietly turn a
    no-index install into a normal one. ``PIP_CONFIG_FILE`` at the null device
    does the same for a ``pip.conf`` on the host.
    """
    env = dict(os.environ)
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
    ):
        env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PIP_PROGRESS_BAR"] = "off"
    return env


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts" if os.name == "nt" else "bin") / "python"


def _build_assets(output_dir: Path) -> Path:
    """Build the release assets with the build script, as the workflow does."""
    assert SCRIPT.is_file(), (
        f"the release-asset build script is missing at {SCRIPT}; the install "
        "from the release assets has no assets to install from"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(ROOT),
            "--output-dir",
            str(output_dir),
            "--source-date-epoch",
            str(EPOCH),
        ],
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, (
        "building the release assets failed:\n" + result.stdout + result.stderr
    )
    return output_dir


def _create_clean_venv(environment: Path) -> Path:
    """A fresh virtual environment with pip, outside the source tree.

    ``with_pip`` uses the stdlib's bundled pip wheels, so creating the
    environment contacts no index either.
    """
    venv_module = _require_venv()
    _require_ensurepip()
    venv_module.EnvBuilder(with_pip=True, clear=True).create(str(environment))
    python = _venv_python(environment)
    assert python.is_file(), f"the virtual environment {environment} has no python"
    return python


def _pip_install(python: Path, find_links: Path, requirement: str) -> subprocess.CompletedProcess:
    """The release install command, run by the environment's own pip."""
    return subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(find_links),
            "--no-cache-dir",
            requirement,
        ],
        cwd=str(find_links.parent),
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=TIMEOUT_SECONDS,
    )


_PROBE = (
    "import json, sys\n"
    "from importlib import metadata\n"
    "import skillweave_sdk\n"
    "try:\n"
    "    version = metadata.version(%r)\n"
    "except metadata.PackageNotFoundError:\n"
    "    version = None\n"
    "sys.stdout.write(json.dumps({\n"
    "    'distribution_version': version,\n"
    "    'module_version': getattr(skillweave_sdk, '__version__', None),\n"
    "    'module_file': getattr(skillweave_sdk, '__file__', None),\n"
    "    'prefix': sys.prefix,\n"
    "}))\n" % DISTRIBUTION_NAME
)


def _probe_install(python: Path, cwd: Path) -> dict:
    """Import the installed package in the environment and report its version.

    The working directory is outside the source tree and ``PYTHONPATH`` is
    empty, so the import can only resolve to what pip installed — an import
    that succeeded from the checkout would prove nothing.
    """
    result = subprocess.run(
        [str(python), "-c", _PROBE],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, (
        "importing skillweave_sdk in the environment the assets were installed "
        "into failed:\n" + result.stdout + result.stderr
    )
    return json.loads(result.stdout)


def _distribution_version_or_none() -> str | None:
    """What the interpreter running the suite has installed under this name."""
    from importlib import metadata

    try:
        return metadata.version(DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return None


def _fingerprint(root: Path) -> dict:
    """Name, size and mtime of every file a run must not disturb."""
    seen = {}
    for path in sorted(root.rglob("*")):
        if any(part in _FINGERPRINT_SKIP for part in path.parts) or path.suffix == ".pyc":
            continue
        if path.is_file():
            stat = path.stat()
            seen[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
        else:
            seen[str(path.relative_to(root)) + "/"] = None
    return seen


def _fingerprint_diff(before: dict, after: dict) -> tuple:
    added = sorted(set(after) - set(before))
    changed = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    removed = sorted(set(before) - set(after))
    return added, changed, removed


@pytest.fixture(scope="module")
def release_assets(tmp_path_factory) -> Path:
    """One build of this repository, installable by every test in the module."""
    output_dir = tmp_path_factory.mktemp("release-assets") / "assets"
    return _build_assets(output_dir)


@pytest.fixture(scope="module")
def install_environment(tmp_path_factory) -> Path:
    """One clean environment the assets are installed into."""
    environment = tmp_path_factory.mktemp("clean-environments") / "venv"
    _create_clean_venv(environment)
    return environment


# --------------------------------------------------------------------------
# Criterion 1: the assets install with `pip install --no-index --find-links`
# by the environment's own pip, the package imports, and the installed
# distribution version equals the version pyproject.toml declares.
# --------------------------------------------------------------------------


def test_installs_from_release_assets_and_imports_the_declared_version(
    release_assets: Path, install_environment: Path, tmp_path: Path
) -> None:
    declared = _declared_version()
    python = _venv_python(install_environment)
    result = _pip_install(python, release_assets, DISTRIBUTION_NAME)
    assert result.returncode == 0, (
        f"{NAMED_FAIL_INSTALL} (requirement {DISTRIBUTION_NAME!r} at version "
        f"{declared}, assets in {release_assets}):\n"
        + result.stdout
        + result.stderr
    )

    probed = _probe_install(python, tmp_path)
    assert probed["distribution_version"] == declared, (
        f"the installed distribution reports version "
        f"{probed['distribution_version']!r} but pyproject.toml declares "
        f"{declared!r}; the release assets do not carry the version they claim"
    )
    assert probed["module_version"] == declared, (
        f"the installed skillweave_sdk reports __version__ "
        f"{probed['module_version']!r}, not the declared {declared!r}"
    )
    assert probed["prefix"] == str(install_environment), (
        f"skillweave_sdk was imported from prefix {probed['prefix']!r}, not "
        f"from the environment the assets were installed into "
        f"({install_environment}); an import that resolved elsewhere does "
        "not prove the release install"
    )
    assert probed["module_file"] and str(install_environment) in probed["module_file"], (
        f"skillweave_sdk resolved to {probed['module_file']!r}, outside the "
        "environment the assets were installed into"
    )


# --------------------------------------------------------------------------
# Criterion 2: no index fallback is configured — a requirement the assets do
# not satisfy fails by name — and a host without the venv module is a named
# skip, not a silent pass.
# --------------------------------------------------------------------------


def test_pip_reaches_no_index_so_an_unknown_requirement_fails_by_name(
    release_assets: Path, install_environment: Path
) -> None:
    result = _pip_install(
        _venv_python(install_environment), release_assets, ABSENT_REQUIREMENT
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"pip installed {ABSENT_REQUIREMENT!r} although the asset directory "
        f"{release_assets} does not contain it; an index must be reachable, "
        "which the release host does not have"
    )
    assert any(message in combined for message in _NO_DISTRIBUTION_MESSAGES), (
        f"the failure must name its cause ({' or '.join(_NO_DISTRIBUTION_MESSAGES)}); "
        "pip said:\n" + combined
    )
    assert not _INDEX_LINE.search(combined), (
        f"{NAMED_FAIL_INDEX_REACHABLE}\n" + combined
    )
    assert not _URL.search(combined), (
        f"{NAMED_FAIL_INDEX_REACHABLE} pip output names a network location:\n"
        + combined
    )


def test_a_missing_venv_module_is_a_named_skip(monkeypatch) -> None:
    """The skip is asserted here so it cannot degrade into a silent pass."""
    real_find_spec = importlib.util.find_spec

    def without_venv(name, *args, **kwargs):
        if name == "venv":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", without_venv)
    with pytest.raises(pytest.skip.Exception) as excinfo:
        _require_venv()
    assert str(excinfo.value) == NAMED_SKIP_NO_VENV, (
        "the skip must carry the named reason verbatim so a skipped install "
        f"proof is readable in the report; it said {str(excinfo.value)!r}"
    )


# --------------------------------------------------------------------------
# Criterion 3: the install leaves no environment, build or dist directory
# behind, and the interpreter running the suite is not modified.
# --------------------------------------------------------------------------


def test_no_build_dist_or_environment_is_left_behind(tmp_path: Path) -> None:
    declared = _declared_version()
    interpreter_before = (sys.executable, sys.prefix, list(sys.path))
    installed_before = _distribution_version_or_none()
    before = _fingerprint(ROOT)

    workspace = tmp_path / "release-workspace"
    assets = _build_assets(workspace / "assets")
    environment = workspace / "env"
    python = _create_clean_venv(environment)
    result = _pip_install(python, assets, DISTRIBUTION_NAME)
    assert result.returncode == 0, (
        f"{NAMED_FAIL_INSTALL}\n" + result.stdout + result.stderr
    )

    # The environment is where the test put it: outside the source tree, so
    # pytest's own temporary-directory cleanup is what removes it.
    assert tmp_path not in ROOT.parents and ROOT not in tmp_path.parents, (
        f"the test workspace {tmp_path} is inside the source tree {ROOT}"
    )
    assert _venv_python(environment).exists(), (
        f"the environment the test created at {environment} has no python"
    )

    added, changed, removed = _fingerprint_diff(before, _fingerprint(ROOT))
    assert not added, f"the run created {added} in the source tree"
    assert not changed, f"the run modified {changed} in the source tree"
    assert not removed, f"the run removed {removed} from the source tree"
    for forbidden in ("build", "dist", "venv", ".venv"):
        assert not (ROOT / forbidden).exists(), (
            f"the run left a {forbidden}/ directory in the source tree"
        )
    assert not list(ROOT.rglob("*.egg-info")), (
        "the run left an egg-info directory in the source tree"
    )

    interpreter_after = (sys.executable, sys.prefix, list(sys.path))
    assert interpreter_after == interpreter_before, (
        "the interpreter running the suite changed while the install ran; the "
        "release install must happen in its own environment"
    )
    assert _distribution_version_or_none() == installed_before, (
        f"the install changed what the suite interpreter has installed under "
        f"{DISTRIBUTION_NAME!r} (before {installed_before!r}, after "
        f"{_distribution_version_or_none()!r})"
    )

    # The environment this test created is removed by the test, not left for a
    # later cleanup to find.
    shutil.rmtree(environment)
    assert not environment.exists(), (
        f"the test could not remove the environment it created at {environment}"
    )


def test_the_assets_alone_satisfy_the_requirement(
    release_assets: Path, tmp_path: Path
) -> None:
    """The positive control for the no-index proof.

    The adversarial test above shows a requirement the assets lack fails. This
    one shows the same command with the requirement the assets do carry
    succeeds, so the failure above is about the requirement, not about an
    install that cannot work at all.
    """
    environment = tmp_path / f"control-{uuid.uuid4().hex[:8]}"
    python = _create_clean_venv(environment)
    result = _pip_install(python, release_assets, DISTRIBUTION_NAME)
    assert result.returncode == 0, (
        f"{NAMED_FAIL_INSTALL}\n" + result.stdout + result.stderr
    )
    probed = _probe_install(python, tmp_path)
    assert probed["distribution_version"] == _declared_version()
