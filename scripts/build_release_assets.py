#!/usr/bin/env python3
"""Build the SDK release assets: one wheel, one sdist, one SHA256SUMS.

This is the single implementation of the asset build. The release workflow
calls it and the test suite calls it, so the artifact the workflow attaches is
the artifact the suite proves.

Determinism. Two builds of one commit with one ``SOURCE_DATE_EPOCH`` must
produce byte-identical files, otherwise a checksum published with a release
proves nothing about a later rebuild. The epoch is the tag commit time when a
tag is named, the head commit time otherwise, and it can be given explicitly.
The wheel honours the epoch through the build backend. The sdist does not, so
it is rewritten here with the epoch on every tar member and in the gzip header.

The source tree is never written to. ``setuptools`` writes a ``build/`` tree
and an egg-info directory next to the project it builds, so the build runs
against a staged copy of the checkout under a temporary directory, in its own
temporary virtual environment outside the source tree. Nothing is installed
into the interpreter running this script, and no index is contacted: the
backend must already be importable for that interpreter.

Every failure is raised as an ``AssetBuildError`` subclass whose ``name`` is
the stable prefix of the message, so a caller can grep for the cause.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path
from typing import Iterable

SOURCE_ROOT = Path(__file__).resolve().parents[1]

WHEEL_SUFFIX = ".whl"
SDIST_SUFFIX = ".tar.gz"
CHECKSUM_FILE = "SHA256SUMS"

# Never staged into the build copy: version control, caches and previous build
# output. Staging a previous `build/` or `dist/` would let one run's leftovers
# become another run's artifact.
STAGE_IGNORE = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "*.py[cod]",
    "*.egg-info",
    "build",
    "dist",
    ".venv",
    "venv",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)


class AssetBuildError(Exception):
    """A release-asset build failure carrying a stable, greppable name."""

    name = "AssetBuildError"


class MissingSourceError(AssetBuildError):
    name = "MissingSourceError"


class ReleaseVersionError(AssetBuildError):
    name = "ReleaseVersionError"


class ReleaseVersionMismatchError(AssetBuildError):
    name = "ReleaseVersionMismatchError"


class SourceDateEpochError(AssetBuildError):
    name = "SourceDateEpochError"


class BuildBackendUnavailableError(AssetBuildError):
    name = "BuildBackendUnavailableError"


class BuildFailedError(AssetBuildError):
    name = "BuildFailedError"


class UnexpectedReleaseAssetError(AssetBuildError):
    name = "UnexpectedReleaseAssetError"


class AssetWriteError(AssetBuildError):
    name = "AssetWriteError"


class ChecksumError(AssetBuildError):
    name = "ChecksumError"


def project_fields(text: str) -> dict:
    """Read name and version from the ``[project]`` table, nothing else.

    A hand-rolled scan rather than a TOML parse keeps this script runnable on
    the bare runner interpreter, which carries no third-party modules.
    """
    in_project = False
    fields: dict = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if not in_project:
            continue
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("name", "version"):
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def normalised_name(name: str) -> str:
    """The PEP 503 spelling a build backend gives an artifact file name.

    ``skillweave-sdk`` reaches the output directory as ``skillweave_sdk``; the
    expected file names have to be computed the same way the backend computes
    them, or a correct build reads as an unexpected asset.
    """
    return re.sub(r"[-_.]+", "_", name).lower()


def _declared_release(source: Path, tag: str | None) -> tuple:
    """The (name, version) the checkout declares, refused by name if absent."""
    pyproject = source / "pyproject.toml"
    if not pyproject.is_file():
        raise MissingSourceError(
            "no pyproject.toml under %s; refusing to build release assets for "
            "a checkout that declares no project." % source
        )
    fields = project_fields(pyproject.read_text(encoding="utf-8"))
    name = fields.get("name", "")
    version = fields.get("version", "")
    if not name or not version:
        raise ReleaseVersionError(
            "pyproject.toml [project] declares no name/version (name=%r, "
            "version=%r); refusing to build release assets without a version "
            "to name them." % (name, version)
        )
    if tag is not None and tag != "v" + version:
        raise ReleaseVersionMismatchError(
            "tag %s but pyproject.toml declares version %s; refusing to build "
            "release assets that do not name the tag they claim."
            % (tag, version)
        )
    return name, version


def _resolve_epoch(source: Path, tag: str | None, explicit: int | None) -> int:
    """The one instant both builds of this tag share.

    An explicit ``--source-date-epoch`` wins, then the inherited
    ``SOURCE_DATE_EPOCH``, then the commit time of the named tag (or of HEAD).
    A checkout whose head commit time cannot be read is a refusal, not a
    silent fallback to "now": a wall-clock epoch would make every rebuild
    differ.
    """
    if explicit is not None:
        if explicit < 0:
            raise SourceDateEpochError(
                "SOURCE_DATE_EPOCH %d is negative; refusing a epoch that no "
                "archive format can carry." % explicit
            )
        return explicit
    inherited = os.environ.get("SOURCE_DATE_EPOCH")
    if inherited:
        try:
            epoch = int(inherited)
        except ValueError:
            raise SourceDateEpochError(
                "SOURCE_DATE_EPOCH %r is not an integer; refusing an epoch "
                "that cannot be applied to the archives." % inherited
            ) from None
        if epoch < 0:
            raise SourceDateEpochError(
                "SOURCE_DATE_EPOCH %d is negative; refusing a epoch that no "
                "archive format can carry." % epoch
            )
        return epoch

    ref = "%s^{}" % tag if tag else "HEAD"
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", ref],
            cwd=str(source),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SourceDateEpochError(
            "cannot read the commit time of %s under %s (%s); pass "
            "--source-date-epoch to pin the build epoch explicitly."
            % (ref, source, detail.strip().splitlines()[-1] if detail.strip() else exc)
        ) from None
    try:
        return int(out.stdout.strip())
    except ValueError:
        raise SourceDateEpochError(
            "git reported %r as the commit time of %s; refusing a epoch that "
            "is not an integer." % (out.stdout.strip(), ref)
        ) from None


def _stage_source(source: Path, staged: Path, output_dir: Path) -> None:
    """Copy the checkout into ``staged``, leaving the original untouched.

    An output directory inside the source tree is excluded, so a run that
    writes ``release-assets/`` next to the project does not stage its own
    previous output into the next build.
    """
    patterns = list(STAGE_IGNORE)
    try:
        relative_output = output_dir.resolve().relative_to(source)
    except ValueError:
        pass
    else:
        if relative_output.parts:
            patterns.append(relative_output.parts[0])
    shutil.copytree(
        source,
        staged,
        ignore=shutil.ignore_patterns(*patterns),
        symlinks=True,
    )


def _build_venv(python: str, directory: Path) -> Path:
    """A temporary virtual environment outside the source tree.

    ``with_pip=False``: this script installs nothing. The backend it needs must
    already be importable, which ``system_site_packages=True`` makes visible;
    a build that had to reach an index would not be reproducible anyway.
    """
    try:
        venv.EnvBuilder(
            system_site_packages=True, with_pip=False, clear=True
        ).create(str(directory))
    except Exception as exc:  # noqa: BLE001 - the cause is reported by name
        raise BuildBackendUnavailableError(
            "cannot create a build virtual environment in %s (%s); the build "
            "runs in its own environment so the source tree stays clean."
            % (directory, exc)
        ) from None
    return directory / ("Scripts" if os.name == "nt" else "bin") / "python"


def _require_backend(venv_python: Path) -> None:
    probe = subprocess.run(
        [str(venv_python), "-c", "import build, setuptools"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise BuildBackendUnavailableError(
            "no build backend available to %s (import build/setuptools "
            "failed: %s); install the backend for this interpreter, this "
            "script installs nothing."
            % (
                venv_python,
                (probe.stderr.strip().splitlines() or ["(no stderr)"])[-1],
            )
        )


def _run_build(
    venv_python: Path, staged: Path, output_dir: Path, epoch: int
) -> None:
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    # A user site-packages tree would make the build depend on the operator's
    # account rather than on the interpreter, and byte-identical rebuilds stop
    # being checkable across machines.
    env["PYTHONNOUSERSITE"] = "1"
    proc = subprocess.run(
        [
            str(venv_python),
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(output_dir),
            ".",
        ],
        cwd=str(staged),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-15:])
        raise BuildFailedError(
            "the build backend exited %d building %s:\n%s"
            % (proc.returncode, staged, tail or "(no output)")
        )


def _collect_artifacts(
    output_dir: Path, name: str, version: str
) -> tuple:
    """Exactly one wheel and one sdist, both naming this project version."""
    wheels = sorted(p for p in output_dir.iterdir() if p.name.endswith(WHEEL_SUFFIX))
    sdists = sorted(p for p in output_dir.iterdir() if p.name.endswith(SDIST_SUFFIX))
    if len(wheels) != 1 or len(sdists) != 1:
        raise UnexpectedReleaseAssetError(
            "the build produced %d wheel(s) and %d sdist(s) in %s, expected "
            "exactly one of each: %s"
            % (
                len(wheels),
                len(sdists),
                output_dir,
                sorted(p.name for p in wheels + sdists),
            )
        )
    wheel, sdist = wheels[0], sdists[0]
    prefix = "%s-%s" % (normalised_name(name), version)
    for artifact in (wheel, sdist):
        if not artifact.name.startswith(prefix):
            raise UnexpectedReleaseAssetError(
                "built artifact %s does not start with %s; release assets must "
                "name the version their own manifest declares."
                % (artifact.name, prefix)
            )
    return wheel, sdist


def _verify_wheel_version(wheel: Path, name: str, version: str) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata = archive.read(
                "%s-%s.dist-info/METADATA" % (normalised_name(name), version)
            ).decode("utf-8")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ReleaseVersionMismatchError(
            "cannot read %s-%s.dist-info/METADATA from %s (%s); the artifact "
            "must name the version its own manifest declares."
            % (normalised_name(name), version, wheel.name, exc)
        ) from None
    found = re.search(r"(?m)^Version:\s*(\S+)\s*$", metadata)
    if found is None or found.group(1) != version:
        raise ReleaseVersionMismatchError(
            "the built wheel records version %s but pyproject.toml declares %s; "
            "refusing release assets whose artifact disagrees with its own "
            "manifest." % (found.group(1) if found else "(absent)", version)
        )


def _normalise_sdist(sdist: Path, epoch: int) -> None:
    """Rewrite the sdist so every member carries one epoch.

    The wheel takes ``SOURCE_DATE_EPOCH`` from the backend; the sdist keeps the
    build time of each file, so two builds of one commit differ until the
    archive is rewritten. Member order and content are preserved; only the
    timestamps, ownership and the gzip header are normalised.
    """
    try:
        with tarfile.open(sdist, "r:gz") as source:
            members = [
                (member, source.extractfile(member).read() if member.isreg() else None)
                for member in source.getmembers()
            ]
    except (tarfile.TarError, OSError) as exc:
        raise UnexpectedReleaseAssetError(
            "cannot read the built sdist %s (%s); the sdist must be a tar.gz "
            "this script can normalise to one epoch." % (sdist.name, exc)
        ) from None

    buffer = io.BytesIO()
    try:
        with tarfile.open(
            fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT
        ) as out:
            for member, data in members:
                member.mtime = epoch
                member.uid = member.gid = 0
                member.uname = member.gname = ""
                member.pax_headers = {}
                out.addfile(
                    member, io.BytesIO(data) if data is not None else None
                )
        sdist.write_bytes(gzip.compress(buffer.getvalue(), mtime=epoch))
    except OSError as exc:
        raise AssetWriteError(
            "cannot rewrite the built sdist %s to carry "
            "SOURCE_DATE_EPOCH=%d (%s); the wheel and the sdist must share one "
            "instant or a rebuild is not byte-identical."
            % (sdist.name, epoch, exc)
        ) from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(output_dir: Path, artifacts: Iterable) -> Path:
    """Write ``SHA256SUMS`` and prove the digests in it before returning.

    Relative file names, because the checksum file travels with the assets it
    lists: ``sha256sum -c SHA256SUMS`` runs in the download directory.
    """
    checksum_file = output_dir / CHECKSUM_FILE
    lines = []
    for artifact in sorted(artifacts, key=lambda p: p.name):
        lines.append("%s  %s" % (_sha256(artifact), artifact.name))
    if not lines:
        raise ChecksumError(
            "no distribution files to checksum in %s; refusing to write an "
            "empty %s." % (output_dir, CHECKSUM_FILE)
        )
    try:
        checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ChecksumError(
            "cannot write %s in %s (%s); a release without its checksums is a "
            "release whose assets cannot be verified."
            % (CHECKSUM_FILE, output_dir, exc)
        ) from None

    listed = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        digest, _, file_name = line.partition("  ")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not file_name:
            raise ChecksumError(
                "%s carries a malformed line %r; every line is '<sha256>  "
                "<file>' or sha256sum -c cannot read it."
                % (CHECKSUM_FILE, line)
            )
        listed[file_name] = digest
    for artifact in artifacts:
        actual = _sha256(artifact)
        if listed.get(artifact.name) != actual:
            raise ChecksumError(
                "%s records %s for %s but the file hashes to %s; the "
                "checksums would not survive `sha256sum -c`."
                % (
                    CHECKSUM_FILE,
                    listed.get(artifact.name, "(absent)"),
                    artifact.name,
                    actual,
                )
            )
    return checksum_file


def build_release_assets(
    source,
    output_dir,
    epoch: int | None = None,
    tag: str | None = None,
) -> dict:
    """Build wheel, sdist and ``SHA256SUMS`` for ``source`` into ``output_dir``.

    ``source`` is read, never written: the build runs against a staged copy in
    its own temporary directory. On any failure the artifacts this call
    produced are removed, so a failed run leaves no half-set behind for a
    publish step to attach.
    """
    source = Path(source).resolve()
    output_dir = Path(output_dir).resolve()
    if not source.is_dir():
        raise MissingSourceError(
            "source directory %s does not exist; refusing to build release "
            "assets from it." % source
        )

    name, version = _declared_release(source, tag)
    resolved_epoch = _resolve_epoch(source, tag, epoch)

    produced: list = []
    copied: list = []
    try:
        with tempfile.TemporaryDirectory(prefix="sdk-release-assets-") as work:
            work_dir = Path(work)
            staged = work_dir / "src"
            raw = work_dir / "raw"
            raw.mkdir()
            _stage_source(source, staged, output_dir)
            venv_python = _build_venv(sys.executable, work_dir / "build-venv")
            _require_backend(venv_python)
            # The backend writes into the temporary directory first: a build
            # that fails must leave the output directory exactly as it was,
            # never a wheel without its sdist and checksums.
            _run_build(venv_python, staged, raw, resolved_epoch)
            wheel, sdist = _collect_artifacts(raw, name, version)
            _verify_wheel_version(wheel, name, version)
            _normalise_sdist(sdist, resolved_epoch)

            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                for artifact in (wheel, sdist):
                    destination = output_dir / artifact.name
                    shutil.copyfile(artifact, destination)
                    produced.append(destination)
            except OSError as exc:
                raise AssetWriteError(
                    "cannot write the release assets into %s (%s); the assets "
                    "and their checksums must land in one directory or none of "
                    "them is usable." % (output_dir, exc)
                ) from None
            copied = list(produced)
            checksum_file = _write_checksums(output_dir, produced)
            produced.append(checksum_file)
    except AssetBuildError:
        for path in produced:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    except BaseException:
        # A non-AssetBuildError escape (a signal, a disk error) must not leave
        # assets behind that the checksum file does not describe.
        for path in copied:
            try:
                path.unlink()
            except OSError:
                pass
        raise

    return {
        "name": name,
        "version": version,
        "source_date_epoch": resolved_epoch,
        "output_dir": output_dir,
        "files": [path.name for path in produced],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the deterministic SDK release assets (wheel, sdist "
        "and SHA256SUMS) into an output directory.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="directory the release assets are written to",
    )
    parser.add_argument(
        "--source",
        default=str(SOURCE_ROOT),
        help="checkout to build; read only, never written to (default: the "
        "repository this script lives in)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="release tag the assets are for, e.g. v0.3.1; when given it must "
        "match the version pyproject.toml declares",
    )
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=None,
        help="the one instant both archives carry (default: SOURCE_DATE_EPOCH, "
        "else the commit time of the tag or of HEAD)",
    )
    args = parser.parse_args(argv)

    try:
        result = build_release_assets(
            args.source,
            args.output_dir,
            epoch=args.source_date_epoch,
            tag=args.tag,
        )
    except AssetBuildError as exc:
        sys.stderr.write("%s: %s\n" % (exc.name, exc))
        return 2

    sys.stdout.write(
        "built release assets for %s %s at SOURCE_DATE_EPOCH=%d: %s\n"
        % (
            result["name"],
            result["version"],
            result["source_date_epoch"],
            ", ".join(result["files"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
