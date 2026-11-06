"""LDraw parts catalog: lazy-loads complete.zip bundled with ldview.

Provides:
  - get_catalog()      -- dict of part_number -> {part_number, name, category}
  - search_catalog()   -- substring search over name/number
  - get_part_info()    -- detailed info dict for a single part
  - get_part_dims()    -- (x_half, y_full, z_half) bounding-box half-extents (LDU)
  - part_aabb()        -- world-space AABB for a placed part
  - aabbs_overlap()    -- AABB overlap test
  - get_part_bbox()    -- true bounding box from part geometry (needs complete.zip)

The LDraw complete.zip is bundled in the ldview Nix derivation at
  /nix/store/*-ldview-*/share/ldraw/complete.zip
Set LDRAW_COMPLETE_ZIP to use a complete.zip elsewhere (e.g. without Nix).
A fallback hard-coded table of ~100 common parts is used when the zip is absent.
"""

from __future__ import annotations

import glob
import os
import re
import zipfile

from brick_mcp import studio

# ---------------------------------------------------------------------------
# Fallback parts table (used when complete.zip is unavailable)
# ---------------------------------------------------------------------------

_FALLBACK: dict[str, str] = {
    "3001.dat": "Brick 2 x 4",
    "3002.dat": "Brick 2 x 3",
    "3003.dat": "Brick 2 x 2",
    "3004.dat": "Brick 1 x 2",
    "3005.dat": "Brick 1 x 1",
    "3006.dat": "Brick 2 x 10",
    "3007.dat": "Brick 2 x 8",
    "3008.dat": "Brick 1 x 8",
    "3009.dat": "Brick 1 x 6",
    "3010.dat": "Brick 1 x 4",
    "3011.dat": "Brick 1 x 10",
    "3012.dat": "Brick 1 x 12",
    "3014.dat": "Brick 1 x 16",
    "3020.dat": "Plate 2 x 4",
    "3021.dat": "Plate 2 x 3",
    "3022.dat": "Plate 2 x 2",
    "3023.dat": "Plate 1 x 2",
    "3024.dat": "Plate 1 x 1",
    "3031.dat": "Plate 4 x 4",
    "3032.dat": "Plate 4 x 6",
    "3033.dat": "Plate 6 x 8",
    "3034.dat": "Plate 1 x 8",
    "3035.dat": "Plate 4 x 8",
    "3037.dat": "Slope Brick 45 2 x 4",
    "3038.dat": "Slope Brick 45 2 x 3",
    "3039.dat": "Slope Brick 45 2 x 2",
    "3040.dat": "Slope Brick 45 2 x 1",
    "3062b.dat": "Brick Round 1 x 1 Open Stud",
    "3062.dat": "Brick Round 1 x 1",
    "3068b.dat": "Tile 2 x 2",
    "3069b.dat": "Tile 1 x 2",
    "3070b.dat": "Tile 1 x 1",
    "3176.dat": "Plate Special 3 x 2 with 1 Stud",
    "3245c.dat": "Brick 1 x 2 x 2",
    "3298.dat": "Slope Brick 33 3 x 2",
    "3299.dat": "Slope Brick 33 3 x 1",
    "3461.dat": "Plate 1 x 10",
    "3460.dat": "Plate 1 x 12",
    "3622.dat": "Brick 1 x 3",
    "3623.dat": "Plate 1 x 3",
    "3660.dat": "Slope Brick 45 2 x 2 Inverted",
    "3665.dat": "Slope Brick 45 2 x 1 Inverted",
    "3666.dat": "Plate 1 x 6",
    "3710.dat": "Plate 1 x 4",
    "3747b.dat": "Slope Brick 33 3 x 2 Inverted",
    "3795.dat": "Plate 2 x 6",
    "3832.dat": "Plate 2 x 10",
    "3833.dat": "Plate 2 x 8",
    "4070.dat": "Brick Special 1 x 1 with Headlight",
    "4162.dat": "Tile 1 x 8",
    "4282.dat": "Plate 2 x 16",
    "4287.dat": "Slope Brick 33 3 x 1 Inverted",
    "4477.dat": "Plate 1 x 10",
    "6111.dat": "Brick 1 x 10",
    "6112.dat": "Brick 1 x 12",
    "30136.dat": "Brick Round 1 x 2",
    "30414.dat": "Plate Special 1 x 4 with 2 Studs",
    "32028.dat": "Plate Special 1 x 2 with Handle",
    "32316.dat": "Technic Beam 5",
    "32524.dat": "Technic Beam 7",
    "41770.dat": "Wing 2 x 4 Right",
    "41771.dat": "Wing 2 x 4 Left",
    "43723.dat": "Wing 3 x 6 Right",
    "43724.dat": "Wing 3 x 6 Left",
    "50950.dat": "Slope Brick Curved 3 x 1",
    "54200.dat": "Slope Brick 31 1 x 1 x 2/3",
    "60479.dat": "Plate 1 x 12",
    "60481.dat": "Slope Brick 65 2 x 1 x 2",
    "63864.dat": "Tile 1 x 3",
    "87079.dat": "Tile 2 x 4",
    "87580.dat": "Plate Special 2 x 2 with 1 Stud",
    "87609.dat": "Brick Round 2 x 2 Dome Top",
    "92438.dat": "Plate 8 x 16",
    "98283.dat": "Brick Special 1 x 2 with Groove",
    "3855.dat": "Window 1 x 2 x 3",
    "3856.dat": "Window Frame 1 x 4 x 3",
    "15068.dat": "Slope Brick Curved 2 x 2 x 2/3",
    "11477.dat": "Slope Brick Curved 2 x 1 x 2/3",
    "3455.dat": "Arch 1 x 2",
    "3308.dat": "Arch 1 x 4",
    "4490.dat": "Arch 1 x 3",
    "6182.dat": "Arch 1 x 6 x 2",
}

# ---------------------------------------------------------------------------
# Hard-coded precise bounding boxes for common parts.
# (x_half, y_full, z_half) in LDU.
# LDraw origin: centre of TOP face. Body extends DOWN in +Y by y_full.
# "Brick M x N": z_half=M*10, x_half=N*10, y_full=24.  Plates: y_full=8.
# ---------------------------------------------------------------------------

_PART_DIMS: dict[str, tuple[float, float, float]] = {
    "3001.dat": (40.0, 24.0, 20.0),
    "3002.dat": (30.0, 24.0, 20.0),
    "3003.dat": (20.0, 24.0, 20.0),
    "3004.dat": (20.0, 24.0, 10.0),
    "3005.dat": (10.0, 24.0, 10.0),
    "3006.dat": (100.0, 24.0, 20.0),
    "3007.dat": (80.0, 24.0, 20.0),
    "3008.dat": (80.0, 24.0, 10.0),
    "3009.dat": (60.0, 24.0, 10.0),
    "3010.dat": (40.0, 24.0, 10.0),
    "3011.dat": (100.0, 24.0, 10.0),
    "3012.dat": (120.0, 24.0, 10.0),
    "3014.dat": (160.0, 24.0, 10.0),
    "3622.dat": (30.0, 24.0, 10.0),
    "6111.dat": (100.0, 24.0, 10.0),
    "6112.dat": (120.0, 24.0, 10.0),
    "3245c.dat": (20.0, 48.0, 10.0),
    "3020.dat": (40.0, 8.0, 20.0),
    "3021.dat": (30.0, 8.0, 20.0),
    "3022.dat": (20.0, 8.0, 20.0),
    "3023.dat": (20.0, 8.0, 10.0),
    "3024.dat": (10.0, 8.0, 10.0),
    "3031.dat": (40.0, 8.0, 40.0),
    "3032.dat": (60.0, 8.0, 40.0),
    "3033.dat": (80.0, 8.0, 60.0),
    "3034.dat": (80.0, 8.0, 10.0),
    "3035.dat": (80.0, 8.0, 40.0),
    "3461.dat": (100.0, 8.0, 10.0),
    "3460.dat": (120.0, 8.0, 10.0),
    "3623.dat": (30.0, 8.0, 10.0),
    "3666.dat": (60.0, 8.0, 10.0),
    "3710.dat": (40.0, 8.0, 10.0),
    "3795.dat": (60.0, 8.0, 20.0),
    "3832.dat": (100.0, 8.0, 20.0),
    "3833.dat": (80.0, 8.0, 20.0),
    "4282.dat": (160.0, 8.0, 20.0),
    "4477.dat": (100.0, 8.0, 10.0),
    "60479.dat": (120.0, 8.0, 10.0),
    "92438.dat": (160.0, 8.0, 80.0),
    "30414.dat": (40.0, 8.0, 10.0),
    "32028.dat": (20.0, 8.0, 10.0),
    "87580.dat": (20.0, 8.0, 20.0),
    "3176.dat": (20.0, 8.0, 30.0),
    "3068b.dat": (20.0, 8.0, 20.0),
    "3069b.dat": (20.0, 8.0, 10.0),
    "3070b.dat": (10.0, 8.0, 10.0),
    "4162.dat": (80.0, 8.0, 10.0),
    "63864.dat": (30.0, 8.0, 10.0),
    "87079.dat": (40.0, 8.0, 20.0),
    "3062.dat": (10.0, 24.0, 10.0),
    "3062b.dat": (10.0, 24.0, 10.0),
    "30136.dat": (20.0, 24.0, 10.0),
    "87609.dat": (20.0, 24.0, 20.0),
}

# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_catalog: dict[str, dict[str, str]] | None = None


def _locate_complete_zip() -> str | None:
    env_path = os.environ.get("LDRAW_COMPLETE_ZIP")
    if env_path and os.path.isfile(env_path):
        return env_path
    candidates = sorted(
        glob.glob("/nix/store/*-ldview-*/share/ldraw/complete.zip"), reverse=True
    )
    return candidates[0] if candidates else None


def _guess_category(name: str) -> str:
    nl = name.lower()
    for kw, cat in [
        ("electric", "Electric"),
        ("plate", "Plate"),
        ("tile", "Tile"),
        ("slope", "Slope"),
        ("arch", "Arch"),
        ("technic", "Technic"),
        ("window", "Window"),
        ("door", "Door"),
        ("minifig", "Minifig"),
        ("wheel", "Wheel"),
        ("tyre", "Tyre"),
        ("axle", "Axle"),
        ("brick", "Brick"),
    ]:
        if kw in nl:
            return cat
    return "Other"


def _load_from_zip(zip_path: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    prefix = "ldraw/parts/"
    with zipfile.ZipFile(zip_path) as zf:
        entries = [
            n
            for n in zf.namelist()
            if n.lower().startswith(prefix)
            and n.lower().endswith(".dat")
            and "/" not in n[len(prefix) :]
        ]
        for entry in entries:
            fname = entry.split("/")[-1].lower()
            try:
                with zf.open(entry) as fh:
                    name = ""
                    category = ""
                    for lineno, raw in enumerate(fh):
                        if lineno > 20:
                            break
                        line = raw.decode("utf-8", "replace").lstrip("\ufeff").strip()
                        if lineno == 0:
                            if line.startswith("0 "):
                                name = line[2:].strip()
                        elif line.startswith("0 !CATEGORY "):
                            category = line[12:].strip()
                            break
                    if name:
                        result[fname] = {
                            "part_number": fname,
                            "name": name,
                            "category": category or _guess_category(name),
                        }
            except Exception:
                pass
    return result


def get_catalog() -> dict[str, dict[str, str]]:
    """Return the full parts catalog, loading lazily on first call.

    Maps lowercase part filename (e.g. "3001.dat") to
    {"part_number", "name", "category"}.  First call parses ~20 000 parts.
    """
    global _catalog
    if _catalog is None:
        zip_path = _locate_complete_zip()
        if zip_path:
            _catalog = _load_from_zip(zip_path)
        else:
            _catalog = {
                k: {"part_number": k, "name": v, "category": _guess_category(v)}
                for k, v in _FALLBACK.items()
            }
    return _catalog


# Names people search by that LDraw titles don't contain.  Maps part -> extra
# search terms; hits on these rank first.  455xx are LEGO element set numbers.
_SPIKE_ESSENTIAL = "spike essential"
_SPIKE_PRIME = "spike prime"
_ALIASES: dict[str, str] = {
    "67351.dat": f"{_SPIKE_ESSENTIAL} {_SPIKE_PRIME} hub small 2 port 45609",
    "67351c01.dat": f"{_SPIKE_ESSENTIAL} {_SPIKE_PRIME} hub small 2 port 45609",
    "68488.dat": f"{_SPIKE_ESSENTIAL} motor small angular 45607",
    "68488c01.dat": f"{_SPIKE_ESSENTIAL} motor small angular 45607",
    "37308.dat": f"{_SPIKE_ESSENTIAL} {_SPIKE_PRIME} color sensor 45605",
    "37308c01.dat": f"{_SPIKE_ESSENTIAL} {_SPIKE_PRIME} color sensor 45605",
    "47592.dat": f"{_SPIKE_ESSENTIAL} color light matrix 45608",
    "47592c01.dat": f"{_SPIKE_ESSENTIAL} color light matrix 45608",
    "45601.dat": f"{_SPIKE_PRIME} hub large 6 port 45601",
    "45601c01.dat": f"{_SPIKE_PRIME} hub large 6 port 45601",
    "54675c01.dat": f"{_SPIKE_PRIME} motor large angular 45602",
    "54696p02c01.dat": f"{_SPIKE_PRIME} motor medium angular 45603",
    "37316c01.dat": f"{_SPIKE_PRIME} distance sensor 45604",
    "37312c01.dat": f"{_SPIKE_PRIME} force sensor 45606",
}

_DIM_TERM = re.compile(r"\d+(?:\.\d+)?(?: x \d+(?:\.\d+)?)+")
_search_index: tuple[int, dict[str, str]] | None = None


def _normalize(text: str) -> str:
    """Lowercase, unify spelling, and turn "2x4" into "2 x 4"."""
    t = re.sub(r"\s+", " ", text.lower().strip())
    t = t.replace("colour", "color").replace("grey", "gray")
    return re.sub(r"(?<=\d) ?x ?(?=\d)", " x ", t)


def _index(catalog: dict[str, dict[str, str]]) -> dict[str, str]:
    """Return part -> normalized search text, cached per catalog."""
    global _search_index
    if _search_index is None or _search_index[0] != id(catalog):
        _search_index = (
            id(catalog),
            {
                pn: _normalize(
                    f"{info['name']} {pn} {_ALIASES.get(pn, '')} "
                    f"{studio.description(pn)}"
                )
                for pn, info in catalog.items()
            },
        )
    return _search_index[1]


def _query_terms(query: str) -> list[re.Pattern[str]]:
    """Split a normalized query into patterns that must all match.

    Dimensions match whole ("2 x 4" not inside "12 x 4"); words match as substrings.
    """
    terms = [
        re.compile(rf"(?<![\d.]){re.escape(m)}(?![\d.])")
        for m in _DIM_TERM.findall(query)
    ]
    words = _DIM_TERM.sub(" ", query).split()
    return terms + [re.compile(re.escape(w)) for w in words]


def _is_hidden(info: dict[str, str]) -> bool:
    """True for subparts (~), aliases (=), and obsolete parts (_)."""
    return info["name"][:1] in ("~", "=", "_") or info["category"] == "Obsolete"


def search_catalog(
    query: str, limit: int = 20, include_subparts: bool = False
) -> list[dict[str, str]]:
    """Search catalog by name or part number, best matches first (case-insensitive).

    All query words must match, in any order.  "2x4" matches "2 x 4", "colour"
    matches "color", and common names such as "spike hub" or "45609" are
    understood.  Plain parts rank above patterned ones and shorter names above
    longer ones.  Subparts, aliases and obsolete parts are left out unless
    include_subparts is set or nothing else matches.
    """
    catalog = get_catalog()
    index = _index(catalog)
    q = _normalize(query)
    terms = _query_terms(q)

    matches: list[tuple[tuple, dict[str, str]]] = []
    for pn, info in catalog.items():
        text = index[pn]
        if not all(t.search(text) for t in terms):
            continue
        base = pn.removesuffix(".dat")
        name = _normalize(info["name"])
        alias = _normalize(_ALIASES.get(pn, ""))
        key = (
            base != q,
            not (alias and all(t.search(alias) for t in terms)),
            not all(t.search(name) for t in terms),
            not name.startswith(q),
            "pattern" in name or "sticker" in name,
            len(name),
            pn,
        )
        matches.append((key, info))

    visible = [m for m in matches if not _is_hidden(m[1])]
    if not include_subparts and visible:
        matches = visible
    matches.sort(key=lambda m: m[0])
    return [info for _key, info in matches[:limit]]


def get_part_info(part_number: str) -> dict[str, str] | None:
    """Return catalog info for one part, or None if not found."""
    pn = part_number.lower().strip()
    if not pn.endswith(".dat"):
        pn += ".dat"
    return get_catalog().get(pn)


# ---------------------------------------------------------------------------
# Part bounding-box dimensions
# ---------------------------------------------------------------------------

_DIM_PATTERN = re.compile(
    r"(?P<m>\d+)\s*x\s*(?P<n>\d+)(?:\s*x\s*(?P<h>\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)


def _dims_from_name(name: str) -> tuple[float, float, float]:
    nl = name.lower()
    m = _DIM_PATTERN.search(nl)
    if m:
        z_half = float(int(m.group("m")) * 10)
        x_half = float(int(m.group("n")) * 10)
    else:
        x_half = z_half = 10.0
    y_full = 8.0 if any(w in nl for w in ("plate", "tile")) else 24.0
    return (x_half, y_full, z_half)


# ---------------------------------------------------------------------------
# Part geometry (true bounding boxes from complete.zip)
# ---------------------------------------------------------------------------

Point = tuple[float, float, float]
BBox = tuple[float, float, float, float, float, float]

_geom_zip: zipfile.ZipFile | None = None
_geom_entries: dict[str, str] | None = None
_geom_points: dict[str, set[Point]] = {}
_bbox_cache: dict[str, BBox | None] = {}


def _geometry_entries() -> dict[str, str]:
    """Return lowercase file path (relative to parts/ or p/) -> zip entry name."""
    global _geom_zip, _geom_entries
    if _geom_entries is None:
        _geom_entries = {}
        zip_path = _locate_complete_zip()
        if zip_path:
            _geom_zip = zipfile.ZipFile(zip_path)
            for entry in _geom_zip.namelist():
                low = entry.lower()
                for prefix in ("ldraw/p/", "ldraw/parts/"):  # parts/ wins over p/
                    if low.startswith(prefix):
                        _geom_entries[low[len(prefix) :]] = entry
    return _geom_entries


def _file_points(ref: str, stack: tuple[str, ...] = ()) -> set[Point]:
    """Return every vertex of an LDraw file and the files it references."""
    ref = ref.lower().replace("\\", "/")
    if ref in _geom_points:
        return _geom_points[ref]
    entry = _geometry_entries().get(ref)
    if entry is None or ref in stack:
        return set()

    points: set[Point] = set()
    for raw in _geom_zip.read(entry).decode("utf-8", "replace").splitlines():
        fields = raw.split()
        if not fields:
            continue
        try:
            if fields[0] in ("3", "4"):
                nums = [float(v) for v in fields[2:]]
                points.update(
                    (nums[i], nums[i + 1], nums[i + 2])
                    for i in range(0, len(nums) - 2, 3)
                )
            elif fields[0] == "1" and len(fields) >= 15:
                x, y, z, a, b, c, d, e, f, g, h, i = (float(v) for v in fields[2:14])
                for px, py, pz in _file_points(" ".join(fields[14:]), (*stack, ref)):
                    points.add(
                        (
                            round(x + a * px + b * py + c * pz, 3),
                            round(y + d * px + e * py + f * pz, 3),
                            round(z + g * px + h * py + i * pz, 3),
                        )
                    )
        except ValueError:
            continue
    _geom_points[ref] = points
    return points


def get_part_bbox(part_number: str) -> BBox | None:
    """Return a part's true (xmin,xmax,ymin,ymax,zmin,zmax) about its origin.

    Computed from the part's geometry, so it needs complete.zip; None without it.
    """
    pn = part_number.lower().strip()
    if not pn.endswith(".dat"):
        pn += ".dat"
    if pn not in _bbox_cache:
        pts = _file_points(pn)
        if pts:
            xs, ys, zs = zip(*pts)
            _bbox_cache[pn] = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
        else:
            _bbox_cache[pn] = None
        # Vertex sets of subparts are only needed while computing a part.
        if len(_geom_points) > 2000:
            _geom_points.clear()
    return _bbox_cache[pn]


def measured_bbox(part_number: str) -> BBox | None:
    """Return the geometry bbox for parts whose size can't be known otherwise.

    Standard parts keep the tidy stud-based dimensions from the table or their
    name (geometry would count studs as height and flag stacked bricks).
    """
    pn = part_number.lower().strip()
    if not pn.endswith(".dat"):
        pn += ".dat"
    if pn in _PART_DIMS:
        return None
    info = get_part_info(pn)
    if info and _DIM_PATTERN.search(info["name"]):
        return None
    return get_part_bbox(pn)


def get_part_dims(part_number: str) -> tuple[float, float, float]:
    """Return (x_half, y_full, z_half) AABB half-extents in LDU.

    Body occupies:
      X in [x - x_half, x + x_half]
      Y in [y, y + y_full]   (Y inverted; body below LDraw origin)
      Z in [z - z_half, z + z_half]
    """
    pn = part_number.lower().strip()
    if not pn.endswith(".dat"):
        pn += ".dat"
    hard = _PART_DIMS.get(pn)
    if hard:
        return hard
    bbox = measured_bbox(pn)
    if bbox:
        xmin, xmax, ymin, ymax, zmin, zmax = bbox
        return ((xmax - xmin) / 2.0, ymax - ymin, (zmax - zmin) / 2.0)
    info = get_part_info(pn)
    if info:
        return _dims_from_name(info["name"])
    return (10.0, 24.0, 10.0)


# ---------------------------------------------------------------------------
# AABB helpers used by layout tools
# ---------------------------------------------------------------------------


def part_aabb(
    x: float,
    y: float,
    z: float,
    rotation: list[float],
    part_number: str,
) -> tuple[float, float, float, float, float, float]:
    """Compute world-space AABB (xmin,xmax,ymin,ymax,zmin,zmax) for a placed part.

    For rotated parts the 8 OBB corners are transformed and their AABB is returned
    (conservative — may over-estimate for highly non-cubic parts at odd angles).
    """
    measured = measured_bbox(part_number)
    if measured:
        # Measured boxes need not be centred on the origin; rotate about the origin.
        r = rotation
        corners = [
            (
                x + r[0] * sx + r[1] * sy + r[2] * sz,
                y + r[3] * sx + r[4] * sy + r[5] * sz,
                z + r[6] * sx + r[7] * sy + r[8] * sz,
            )
            for sx in measured[:2]
            for sy in measured[2:4]
            for sz in measured[4:]
        ]
        xs, ys, zs = zip(*corners)
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

    hx, y_full, hz = get_part_dims(part_number)
    hy = y_full / 2.0
    # LDraw origin is at top face; body centre is hy below in +Y direction
    cx, cy, cz = x, y + hy, z
    r = rotation
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for sx in (-hx, hx):
        for sy in (-hy, hy):
            for sz in (-hz, hz):
                xs.append(cx + r[0] * sx + r[1] * sy + r[2] * sz)
                ys.append(cy + r[3] * sx + r[4] * sy + r[5] * sz)
                zs.append(cz + r[6] * sx + r[7] * sy + r[8] * sz)
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def aabbs_overlap(
    a: tuple[float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float],
    tolerance: float = 0.5,
) -> bool:
    """True if two AABBs penetrate by more than tolerance LDU.

    tolerance=0.5 lets surfaces touch without flagging a collision.
    """
    axmin, axmax, aymin, aymax, azmin, azmax = a
    bxmin, bxmax, bymin, bymax, bzmin, bzmax = b
    return (
        axmin < bxmax - tolerance
        and axmax > bxmin + tolerance
        and aymin < bymax - tolerance
        and aymax > bymin + tolerance
        and azmin < bzmax - tolerance
        and azmax > bzmin + tolerance
    )
