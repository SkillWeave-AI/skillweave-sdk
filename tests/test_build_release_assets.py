"""The release-asset build is proven, not trusted.

``scripts/build_release_assets.py`` produces the wheel, the sdist and the
``SHA256SUMS`` that a release attaches. Three things must hold, and each is
checked here against the artifacts themselves rather than against the script's
own summary:

1. **The set is complete and the failures are named.** The output directory
   holds exactly one wheel, one sdist and a ``SHA256SUMS`` with one sha256 line
   per distribution file, and a build or checksum failure exits non-zero with a
   message whose first token names the cause.
2. **One commit and one epoch give one artifact.** Two builds of the same
   commit at the same ``SOURCE_DATE_EPOCH`` are byte-identical. The control
   below rebuilds at a different epoch and requires the bytes to differ: without
   it, "identical" would also pass a build that ignores the epoch entirely.
3. **The artifacts verify where they land, and the source tree is untouched.**
   ``sha256sum -c SHA256SUMS`` passes in the output directory, and a run leaves
   no ``build/``, ``dist/`` or egg-info behind and changes no file of the source
   tree it reads.

The real repository is the source under test for the artifact properties, so
the names and the version asserted here are the names and the version a release
publishes. The failure paths use a small synthetic project so they stay fast.
"""

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_release_assets.py"

# One instant for every test that does not deliberately vary the epoch.
EPOCH = 1_700_000_000
OTHER_EPOCH = 1_600_000_000

CHECKSUM_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/]+)$")


def _load_script():
    """The module under test, loaded from its path.

    ``scripts/`` is not a package, so the file is loaded directly; a missing
    script is the base-state failure and surfaces as a load error rather than a
    skipped test.
    """
    spec = importlib.util.spec_from_file_location("build_release_assets", SCRIPT)
    assert spec is not None and spec.loader is not None, (
        f"cannot load the build script from {SCRIPT}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(source: Path, output_dir: Path, epoch=None, tag=None, env_epoch=False):
    """Run the script the way the release workflow does: as a shell command."""
    cmd = [sys.executable, str(SCRIPT), "--output-dir", str(output_dir),
           "--source", str(source)]
    env = dict(os.environ)
    if tag is not None:
        cmd += ["--tag", tag]
    if env_epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    elif epoch is not None:
        cmd += ["--source-date-epoch", str(epoch)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _distribution_files(output_dir: Path):
    return sorted(
        path for path in output_dir.iterdir()
        if path.name.endswith((".whl", ".tar.gz"))
    )


def _fingerprint(root: Path) -> dict:
    """Name, size and mtime of every file a run must not disturb.

    ``.git``, caches and byte-code are excluded: pytest itself writes those
    while these tests run, and they are not the source tree this criterion is
    about.
    """
    skip = {".git", "__pycache__", ".pytest_cache"}
    seen = {}
    for path in sorted(root.rglob("*")):
        if any(part in skip for part in path.parts) or path.suffix == ".pyc":
            continue
        if path.is_file():
            stat = path.stat()
            seen[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
        else:
            seen[str(path.relative_to(root)) + "/"] = None
    return seen


def _write_project(root: Path, version: str = "0.3.0", name: str = "demo-sdk"):
    """A minimal buildable checkout, independent of this repository."""
    package = root / "src" / "demo_sdk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'"""demo."""\n\n__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [build-system]
            requires = ["setuptools>=77.0.0"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "{name}"
            version = "{version}"
            description = "demo project for the release-asset build"
            readme = "README.md"
            requires-python = ">=3.9"

            [tool.setuptools.packages.find]
            where = ["src"]
            """
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return _write_project(tmp_path_factory.mktemp("project"))


@pytest.fixture(scope="module")
def assets(tmp_path_factory) -> Path:
    """One build of this repository, reused by the read-only assertions."""
    output_dir = tmp_path_factory.mktemp("assets") / "release-assets"
    result = _run_cli(ROOT, output_dir, epoch=EPOCH)
    assert result.returncode == 0, (
        "the build of this repository failed:\n" + result.stderr
    )
    return output_dir


# --------------------------------------------------------------------------
# Criterion 1: builds the wheel and the sdist and writes SHA256SUMS, one
# sha256 line per distribution file; non-zero exit with a named message when
# the build or the checksum step fails.
# --------------------------------------------------------------------------


def test_build_writes_wheel_sdist_and_checksums(assets: Path) -> None:
    names = sorted(path.name for path in _distribution_files(assets))
    assert len(names) == 2, f"expected one wheel and one sdist, got {names}"
    assert sum(name.endswith(".tar.gz") for name in names) == 1, names
    assert sum(name.endswith(".whl") for name in names) == 1, names
    assert (assets / "SHA256SUMS").is_file(), (
        "the build must write SHA256SUMS next to the artifacts it lists"
    )


def test_checksums_hold_one_line_per_distribution_file(assets: Path) -> None:
    lines = [
        line for line in
        (assets / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    listed = {}
    for line in lines:
        match = CHECKSUM_LINE.match(line)
        assert match, (
            f"SHA256SUMS line {line!r} is not '<sha256>  <file>'; sha256sum -c "
            "cannot read it"
        )
        listed[match.group("name")] = match.group("digest")
    assert sorted(listed) == sorted(
        path.name for path in _distribution_files(assets)
    ), (
        "SHA256SUMS must list exactly the distribution files in the output "
        f"directory, one line each; it lists {sorted(listed)}"
    )
    for path in _distribution_files(assets):
        assert listed[path.name] == _sha256(path), (
            f"SHA256SUMS records {listed[path.name]} for {path.name} but the "
            f"file hashes to {_sha256(path)}"
        )


def test_build_failure_exits_non_zero_with_a_named_message(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    _write_project(broken)
    # A backend that does not exist stands in for any build failure: with
    # --no-isolation the import fails immediately and no index is contacted.
    (broken / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [build-system]
            requires = ["definitely-not-a-real-backend"]
            build-backend = "definitely_not_a_real_backend"

            [project]
            name = "demo-sdk"
            version = "0.3.0"
            description = "demo"
            """
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    result = _run_cli(broken, output_dir, epoch=EPOCH)
    assert result.returncode != 0, (
        "a build whose backend cannot be imported must not report success:\n"
        + result.stdout
    )
    assert result.stderr.startswith("BuildFailedError: "), (
        "the failure must name its cause as the first token of the message; "
        f"stderr was {result.stderr!r}"
    )
    if output_dir.exists():
        assert not list(output_dir.glob("*.whl")), (
            "a failed build must not leave a wheel in the output directory"
        )


def test_checksum_step_fails_by_name_when_a_digest_does_not_hold(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_script()
    artifact = tmp_path / "demo_sdk-0.3.0-py3-none-any.whl"
    artifact.write_bytes(b"not really a wheel")
    real_sha256 = module._sha256
    calls = {"count": 0}

    def drifting(path):
        calls["count"] += 1
        # The first pass computes the digest that is written down; the second
        # pass is the verification read, and it sees a file that changed.
        return "0" * 64 if calls["count"] > 1 else real_sha256(path)

    monkeypatch.setattr(module, "_sha256", drifting)
    with pytest.raises(module.ChecksumError) as excinfo:
        module._write_checksums(tmp_path, [artifact])
    assert excinfo.value.name == "ChecksumError", (
        "the checksum failure must carry the stable name 'ChecksumError'"
    )


def test_tag_that_disagrees_with_the_declared_version_is_refused(
    project: Path, tmp_path: Path
) -> None:
    result = _run_cli(project, tmp_path / "out", epoch=EPOCH, tag="v9.9.9")
    assert result.returncode != 0, (
        "a tag that does not name the declared version must be refused"
    )
    assert result.stderr.startswith("ReleaseVersionMismatchError: "), (
        f"the refusal must be named; stderr was {result.stderr!r}"
    )


def test_source_without_a_pyproject_is_refused_by_name(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_cli(empty, tmp_path / "out", epoch=EPOCH)
    assert result.returncode != 0
    assert result.stderr.startswith("MissingSourceError: "), (
        f"stderr was {result.stderr!r}"
    )


# --------------------------------------------------------------------------
# Criterion 2: two builds of the same commit at the same SOURCE_DATE_EPOCH are
# byte-identical, proven by comparing sha256 of separately built directories.
# --------------------------------------------------------------------------


def test_two_builds_of_one_commit_are_byte_identical(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output_dir in (first, second):
        result = _run_cli(ROOT, output_dir, epoch=EPOCH)
        assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in first.iterdir()) == sorted(
        p.name for p in second.iterdir()
    ), "the two builds produced different file sets"
    for path in sorted(first.iterdir()):
        counterpart = second / path.name
        assert _sha256(path) == _sha256(counterpart), (
            f"{path.name} differs between two builds of one commit at one "
            "SOURCE_DATE_EPOCH; a checksum published with the release would "
            "not describe a rebuild"
        )


def test_the_epoch_is_honoured_and_not_ignored(tmp_path: Path) -> None:
    """The control for the determinism claim.

    Byte-identical output at one epoch is only evidence of a deterministic
    build if a different epoch produces different bytes. Otherwise the build
    could be ignoring the epoch and the first test would still pass.
    """
    first = tmp_path / "epoch-a"
    second = tmp_path / "epoch-b"
    assert _run_cli(ROOT, first, epoch=EPOCH).returncode == 0
    assert _run_cli(ROOT, second, epoch=OTHER_EPOCH).returncode == 0
    differing = [
        path.name
        for path in first.iterdir()
        if path.name.endswith((".whl", ".tar.gz"))
        and _sha256(path) != _sha256(second / path.name)
    ]
    assert differing, (
        "changing SOURCE_DATE_EPOCH changed nothing in the wheel or the sdist; "
        "the epoch is not reaching the artifacts, so the same-epoch comparison "
        "proves nothing"
    )


def test_inherited_source_date_epoch_matches_the_explicit_flag(tmp_path: Path) -> None:
    from_env = tmp_path / "from-env"
    from_flag = tmp_path / "from-flag"
    assert _run_cli(ROOT, from_env, epoch=EPOCH, env_epoch=True).returncode == 0
    assert _run_cli(ROOT, from_flag, epoch=EPOCH).returncode == 0
    for path in sorted(from_env.iterdir()):
        assert _sha256(path) == _sha256(from_flag / path.name), (
            f"{path.name} differs between the inherited SOURCE_DATE_EPOCH and "
            "the explicit epoch; the workflow exports the variable, so the two "
            "routes must agree"
        )


def test_sdist_carries_the_epoch_on_every_member(assets: Path, tmp_path: Path) -> None:
    sdist = next(path for path in _distribution_files(assets) if path.name.endswith(".tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
    assert members, f"{sdist.name} has no members"
    for member in members:
        assert member.mtime == EPOCH, (
            f"{member.name} carries mtime {member.mtime}, not the epoch "
            f"{EPOCH}; a rebuild would differ"
        )
        assert member.uid == 0 and member.gid == 0, (
            f"{member.name} carries uid/gid {member.uid}/{member.gid}; the "
            "build must not depend on the account that ran it"
        )
        assert not member.uname and not member.gname, (
            f"{member.name} carries user/group names {member.uname!r}/"
            f"{member.gname!r}"
        )


# --------------------------------------------------------------------------
# Criterion 3: sha256sum -c SHA256SUMS passes in the output directory, and the
# run never writes into the source tree.
# --------------------------------------------------------------------------


def test_sha256sum_check_passes_in_the_output_directory(assets: Path) -> None:
    sha256sum = shutil.which("sha256sum")
    if sha256sum is None:
        pytest.skip(
            "named skip: no sha256sum binary on this host, so the published "
            "checksum format cannot be verified with the tool that consumes it"
        )
    result = subprocess.run(
        [sha256sum, "-c", "SHA256SUMS"],
        cwd=str(assets),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "sha256sum -c SHA256SUMS failed in the output directory:\n"
        + result.stdout
        + result.stderr
    )


def test_the_run_writes_nothing_into_the_source_tree(tmp_path: Path) -> None:
    source = _write_project(tmp_path / "project")
    # Pre-existing output directories must come through untouched: the script
    # stages its build elsewhere, so neither is written to.
    (source / "build").mkdir()
    (source / "build" / "sentinel").write_text("sentinel\n", encoding="utf-8")
    (source / "dist").mkdir()
    (source / "dist" / "sentinel").write_text("sentinel\n", encoding="utf-8")
    before = _fingerprint(source)

    result = _run_cli(source, tmp_path / "out", epoch=EPOCH)
    assert result.returncode == 0, result.stderr

    after = _fingerprint(source)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(
        name for name in set(before) & set(after) if before[name] != after[name]
    )
    assert not added, f"the run created {added} in the source tree"
    assert not removed, f"the run removed {removed} from the source tree"
    assert not changed, f"the run modified {changed} in the source tree"
    assert not list(source.rglob("*.egg-info")), (
        "the run left an egg-info directory in the source tree"
    )
    assert (source / "build" / "sentinel").read_text(encoding="utf-8") == "sentinel\n"


def test_this_repository_source_tree_is_untouched(tmp_path: Path) -> None:
    """The same check against the real checkout, which is what a release builds."""
    before = _fingerprint(ROOT)
    result = _run_cli(ROOT, tmp_path / "out", epoch=EPOCH)
    assert result.returncode == 0, result.stderr
    after = _fingerprint(ROOT)
    added = sorted(set(after) - set(before))
    changed = sorted(
        name for name in set(before) & set(after) if before[name] != after[name]
    )
    assert not added, f"the run created {added} in this repository"
    assert not changed, f"the run modified {changed} in this repository"
    for forbidden in ("build", "dist"):
        assert not (ROOT / forbidden).exists() or before.get(
            f"{forbidden}/"
        ) is not None, (
            f"the run created a {forbidden}/ directory in the source tree"
        )


def test_output_directory_holds_only_the_three_assets(assets: Path) -> None:
    expected = {"SHA256SUMS"} | {path.name for path in _distribution_files(assets)}
    found = {path.name for path in assets.iterdir()}
    assert found == expected, (
        "the output directory must hold exactly the wheel, the sdist and "
        f"SHA256SUMS; it holds {sorted(found)}"
    )
