"""Lookups against a local BrickLink Studio install.

Studio names some parts differently from the LDraw library (e.g. LDraw
67351c01.dat is Studio's 67351-bl.dat) and renders parts it doesn't know as a
"?" box.  Its data/StudioPartDefinition2.txt lists every part it knows, with
columns: Studio ItemNo, BaseStudioItemNo, BL ItemNo, BL ItemKey, LDraw ItemNo,
LDD ItemNo, Description, ...

Without a Studio install every lookup here is a no-op.
"""

from __future__ import annotations

import os

DEFAULT_VERSION = "2.26.10_1"

_known: set[str] | None = None  # LDraw filenames Studio has
_by_bl: dict[str, str] = {}  # BrickLink item no -> Studio LDraw filename
_reverse: dict[str, str] = {}  # Studio-only "-bl.dat" name -> LDraw filename
_descriptions: dict[str, str] = {}  # LDraw filename -> Studio description


def _studio_dir() -> str:
    return os.environ.get("STUDIO_DIR", "/Applications/Studio 2.0")


def version() -> str:
    """Return the installed Studio version, for the .info entry of .io files."""
    try:
        with open(os.path.join(_studio_dir(), "version.txt"), encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip()]
        return lines[1] if len(lines) > 1 else DEFAULT_VERSION
    except OSError:
        return DEFAULT_VERSION


def _load() -> set[str]:
    """Load Studio's part table once; return the set of LDraw names it knows."""
    global _known
    if _known is not None:
        return _known
    known: set[str] = set()
    path = os.path.join(_studio_dir(), "data", "StudioPartDefinition2.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 7 or not cols[4]:
                    continue
                studio_name, bl_item = cols[4], cols[2].lower()
                known.add(studio_name.lower())
                _descriptions.setdefault(studio_name.lower(), cols[6])
                if not bl_item:
                    continue
                _by_bl.setdefault(bl_item, studio_name)
                _descriptions.setdefault(f"{bl_item}.dat", cols[6])
                if bl_item.endswith("c01"):
                    # Same part without its cable / assembly suffix.
                    _descriptions.setdefault(f"{bl_item[:-3]}.dat", cols[6])
                if studio_name.lower().endswith("-bl.dat"):
                    _reverse[studio_name.lower()] = f"{bl_item}.dat"
    except OSError:
        pass
    _known = known
    return known


def to_studio_part(part: str) -> str:
    """Map an LDraw part filename to the name BrickLink Studio recognises."""
    known = _load()
    if not known or part.lower() in known:
        return part
    base = part.lower().removesuffix(".dat")
    for key in (base, base + "c01"):
        if key in _by_bl:
            return _by_bl[key]
    return part


def from_studio_part(part: str) -> str:
    """Map a Studio-only part filename back to its LDraw library name."""
    _load()
    return _reverse.get(part.lower(), part)


def description(part: str) -> str:
    """Return Studio's description of an LDraw part filename, or ""."""
    _load()
    return _descriptions.get(part.lower(), "")
