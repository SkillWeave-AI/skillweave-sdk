"""Assert every declared version surface in .version.yaml resolves and agrees.

The file ``.version.yaml`` (schema 1) declares every location that carries the
SDK distribution version.  pyproject.toml is the single source of truth.  This
test reads the manifest, resolves each location, extracts the version string
using the declared pattern, and compares it against the pyproject version.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Per-suffix patterns that identify files carrying a version *declaration*
# (not just a reference in a string literal).  JSON keys like sdk_version
# and schema_version only appear in .json files; version/__version__/
# SCHEMA_VERSION assignments appear in .toml and .py files.
_VERSION_PATTERNS: dict[str, list[re.Pattern]] = {
    ".toml": [
        re.compile(r'^version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', re.MULTILINE),
    ],
    ".py": [
        re.compile(r'^__version__\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', re.MULTILINE),
        re.compile(r'^SCHEMA_VERSION\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', re.MULTILINE),
    ],
    ".json": [
        re.compile(r'"sdk_version"\s*:\s*"[0-9]+\.[0-9]+\.[0-9]+"'),
        re.compile(r'"schema_version"\s*:\s*"[0-9]+\.[0-9]+\.[0-9]+"'),
    ],
}

# Subdirectory prefixes to exclude from version-file discovery.
_EXCLUDED_PREFIXES = ("tests/", "docs/", ".git/", "__pycache__/")


def _discover_version_files() -> set[str]:
    """Return the set of relative paths of files that declare a version string.

    Scans every non-excluded file in the repository tree and checks for version
    declaration patterns scoped by file suffix.  This discovers the actual version
    surfaces so the test can compare them against the .version.yaml manifest
    without hardcoding paths.
    """
    found: set[str] = set()
    for f in ROOT.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(ROOT))
        if any(rel.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            continue
        patterns = _VERSION_PATTERNS.get(f.suffix)
        if patterns is None:
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in patterns:
            if pat.search(content):
                found.add(rel)
                break
    return found


def _pyproject_version() -> str:
    """Return the version string from pyproject.toml."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[project]", 1)[1].split("\n[", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"', section, re.MULTILINE)
    assert match, "pyproject.toml has no [project] version"
    return match.group(1)


def _load_manifest() -> dict:
    """Load and validate .version.yaml."""
    version_manifest = ROOT / ".version.yaml"
    assert version_manifest.is_file(), ".version.yaml not found"
    manifest = yaml.safe_load(version_manifest.read_text(encoding="utf-8"))
    assert manifest.get("schema") == 1, ".version.yaml must use schema 1"
    assert manifest.get("source_of_truth") == "pyproject.toml"
    return manifest


def test_all_declared_surfaces_match_pyproject():
    manifest = _load_manifest()
    expected = _pyproject_version()

    locations = manifest.get("locations", [])
    assert len(locations) > 0, ".version.yaml has no locations"

    errors = []
    for entry in locations:
        path = entry["path"]
        pattern = entry["pattern"]
        required = entry.get("required", True)
        full_path = ROOT / path

        if not full_path.is_file():
            if required:
                errors.append(f"Required surface missing: {path}")
            continue

        content = full_path.read_text(encoding="utf-8")
        match = re.search(pattern, content)
        if not match:
            errors.append(f"Pattern did not match in {path}: {pattern!r}")
            continue

        found = match.group(1)
        if found != expected:
            errors.append(
                f"{path} declares version {found!r} but pyproject has {expected!r}"
            )

    if errors:
        raise AssertionError(
            "Version surface drift detected:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def test_declared_surfaces_cover_all_version_files():
    """Assert .version.yaml locations cover every version-carrying file in the tree.

    This discovers version files dynamically rather than hardcoding paths, so
    the test stays in sync when surfaces are added or removed.
    """
    manifest = _load_manifest()
    declared_paths = {entry["path"] for entry in manifest.get("locations", [])}
    actual_files = _discover_version_files()

    missing = sorted(actual_files - declared_paths)
    extra = sorted(declared_paths - actual_files)

    errors = []
    if missing:
        errors.append(
            f"Version files not declared in .version.yaml locations:\n"
            + "\n".join(f"  - {p}" for p in missing)
        )
    if extra:
        errors.append(
            f".version.yaml declares non-version files (not found in tree scan):\n"
            + "\n".join(f"  - {p}" for p in extra)
        )

    if errors:
        raise AssertionError(
            "Version surface coverage mismatch:\n" + "\n".join(errors)
        )


def test_release_title_policy_flat_scalars_present():
    """Assert .version.yaml carries flat product_name, pattern and changelog_path."""
    manifest = yaml.safe_load((ROOT / ".version.yaml").read_text(encoding="utf-8"))
    product_name = manifest.get("product_name")
    assert product_name, "product_name not found or empty in .version.yaml"
    assert isinstance(product_name, str), "product_name must be a string"
    pattern = manifest.get("pattern")
    assert pattern, "pattern not found or empty in .version.yaml"
    assert isinstance(pattern, str), "pattern must be a string"
    assert "\\{product\\}" in pattern or "{product}" in pattern, (
        "pattern must contain {product} substitution placeholder"
    )
    changelog_path = manifest.get("changelog_path")
    assert changelog_path, "changelog_path not found or empty in .version.yaml"
    assert isinstance(changelog_path, str), "changelog_path must be a string"
    assert (ROOT / changelog_path).is_file(), (
        f"changelog_path {changelog_path!r} does not point to an existing file"
    )
