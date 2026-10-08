"""Contract authority for SkillWeave.

The repository owns the contract bytes — the JSON schemas under ``schemas/``
and the taxonomy value set — and ``schema_version.toml`` is the root of the
release graph that consumers pin against.

``validator`` exposes the standalone, dependency-free validator that loads by
schema ID, resolves cross-schema ``$ref``, and computes the canonical digest.

``authority_root`` is the public accessor for the ownership inventory. The
inventory ships as PACKAGE DATA inside ``skillweave_sdk/_authority/`` (not a
``.data/data`` payload), so it is reachable through this one path from any
install mode — a venv, ``pip install --target``, a zip-safe install or an
editable install:

    from importlib.resources import files
    catalog = files("skillweave_sdk") / "_authority" / "execution-catalog.json"
"""

from __future__ import annotations

from importlib.resources import files as _files

__all__ = ["__version__", "SCHEMA_VERSION", "authority_root", "validator"]

#: Distribution version. Kept equal to ``[schema].version`` in
#: ``schema_version.toml``; a test asserts the two agree.
__version__ = "0.2.0"

#: Canonical schema version consumers pin against.
SCHEMA_VERSION = "0.2.0"


def authority_root():
    """Return the installed ownership-inventory directory as a Traversable.

    Resolved through ``importlib.resources.files("skillweave_sdk")`` so it is
    the payload inside the *installed package* — the bytes the consumer
    actually reads — never a source-tree path that only exists on the build
    host. Raises ``FileNotFoundError`` when the inventory did not ship, so a
    consumer cannot mistake an absent payload for a missing SDK.
    """
    root = _files("skillweave_sdk") / "_authority"
    if not root.is_dir():
        raise FileNotFoundError(
            "skillweave_sdk/_authority is absent from the installed package; "
            "the ownership inventory did not ship with this distribution"
        )
    return root

