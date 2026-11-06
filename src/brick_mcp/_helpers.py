"""Shared response helpers for brick-mcp tools.

All tools return a dict with {"ok": bool, ...} envelope.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from typing import Any

from fastmcp.utilities.types import Image


def ok(data: Any = None, message: str = "") -> dict:
    """Standard success response envelope."""
    result: dict = {"ok": True}
    if message:
        result["message"] = message
    if data is not None:
        result["data"] = data
    return result


def err(message: str, code: str = "ERROR") -> dict:
    """Standard error response envelope."""
    return {"ok": False, "error": {"code": code, "message": message}}


_MACOS_LDVIEW_PATHS = (
    os.path.expanduser("~/Applications/LDView.app/Contents/MacOS/LDView"),
    "/Applications/LDView.app/Contents/MacOS/LDView",
)


def _find_ldview() -> str | None:
    """Return path to ldview binary: $LDVIEW_BIN, PATH, Nix store, then LDView.app."""
    env_bin = os.environ.get("LDVIEW_BIN")
    if env_bin and os.access(env_bin, os.X_OK):
        return env_bin
    found = shutil.which("ldview")
    if found:
        return found
    # Fallback: locate the installed Nix derivation in the store so rendering
    # works even when the devenv PATH is not active (e.g. VS Code MCP subprocess).
    nix_candidates = sorted(glob.glob("/nix/store/*-ldview-*/bin/ldview"), reverse=True)
    if nix_candidates:
        return nix_candidates[0]
    # macOS: the LDView.app bundle's binary accepts the same CLI flags.
    for path in _MACOS_LDVIEW_PATHS:
        if os.access(path, os.X_OK):
            return path
    return None


def _ldraw_dir_args() -> list[str]:
    """Return ["-LDrawDir=..."] when an unpacked LDraw library is known.

    Uses $LDRAW_DIR, else an "ldraw" folder unpacked next to
    $LDRAW_COMPLETE_ZIP.  The Nix ldview finds its library on its own.
    """
    ldraw_dir = os.environ.get("LDRAW_DIR")
    if not ldraw_dir:
        zip_path = os.environ.get("LDRAW_COMPLETE_ZIP")
        if zip_path:
            ldraw_dir = os.path.join(os.path.dirname(zip_path), "ldraw")
    if ldraw_dir and os.path.isdir(os.path.join(ldraw_dir, "parts")):
        return [f"-LDrawDir={ldraw_dir}"]
    return []


def _egl_env() -> dict[str, str]:
    """Build the minimal env additions needed for headless EGL on non-NixOS hosts.

    libglvnd looks for EGL vendor ICDs in /run/opengl-driver (NixOS), /etc, and
    /usr/share — none of which exist in a plain dev container.  We point it at
    the Mesa ICD directly, and force Mesa's software rasterizer so that no GPU
    or display server is needed.
    """
    extra: dict[str, str] = {}

    # Surfaceless EGL — no X11 / Wayland display required.
    extra["EGL_PLATFORM"] = "surfaceless"
    extra["DISPLAY"] = ""

    # Force Mesa software rasterizer (llvmpipe/swrast).
    extra["LIBGL_ALWAYS_SOFTWARE"] = "1"
    extra["MESA_LOADER_DRIVER_OVERRIDE"] = "swrast"

    # Point libglvnd at the Mesa EGL ICD when the system paths are absent.
    system_icd_dirs = [
        "/run/opengl-driver/share/glvnd/egl_vendor.d",
        "/etc/glvnd/egl_vendor.d",
        "/usr/share/glvnd/egl_vendor.d",
    ]
    if not any(os.path.isdir(d) for d in system_icd_dirs):
        candidates = sorted(
            glob.glob("/nix/store/*-mesa-*/share/glvnd/egl_vendor.d"), reverse=True
        )
        if candidates:
            extra["__EGL_VENDOR_LIBRARY_DIRS"] = candidates[0]

    return extra


# Filled shading on a white background; frames the model tightly (AutoCrop) and
# keeps LDView from querying the LDraw parts tracker for unknown parts.
_LDVIEW_STYLE_ARGS = (
    "-SaveActualSize=0",
    "-SaveZoomToFit=1",
    "-SaveAlpha=0",
    "-AutoCrop=1",
    "-BackgroundColor3=0xFFFFFF",
    "-FOV=0.1",
    "-ShowHighlightLines=1",
    "-EdgeThickness=0.5",
    "-LineSmoothing=1",
    "-FSAAMode=4",
    "-UseQualityLighting=1",
    "-SubduedLighting=1",
    "-UseSpecular=0",
    "-CurveQuality=6",
    "-ShowErrors=0",
    "-CheckPartTracker=0",
)


def run_ldview(
    ldview_bin: str,
    project: Any,
    png_path: str,
    width: int,
    height: int,
    latitude: float = 30,
    longitude: float = 45,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Render project to png_path with LDView."""
    with tempfile.TemporaryDirectory(prefix="brick-mcp-render-") as tmpdir:
        ldr_path = os.path.join(tmpdir, "model.ldr")
        # Standard LDraw text (LDView understands type-1).
        with open(ldr_path, "w", encoding="utf-8") as fh:
            fh.write(project.to_ldraw_text())
        cmd = [
            ldview_bin,
            ldr_path,
            f"-SaveSnapshot={png_path}",
            f"-SaveWidth={width}",
            f"-SaveHeight={height}",
            f"-DefaultLatLong={latitude},{longitude}",
            *_LDVIEW_STYLE_ARGS,
            *_ldraw_dir_args(),
        ]
        env = {**os.environ, **_egl_env()}
        return subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)


def try_render(project: Any, width: int = 800, height: int = 600) -> Image | None:
    """Best-effort render of the current model. Returns Image or None."""
    ldview_bin = _find_ldview()
    if ldview_bin is None:
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="brick-mcp-render-") as tmpdir:
            png_path = os.path.join(tmpdir, "model.png")
            run_ldview(ldview_bin, project, png_path, width, height)
            if not os.path.isfile(png_path):
                return None

            with open(png_path, "rb") as fh:
                return Image(data=fh.read(), format="png")
    except Exception:
        return None


def try_render_to_file(project: Any, png_path: str) -> str | None:
    """Best-effort render to png_path. Returns the path, or None if not written."""
    ldview_bin = _find_ldview()
    if ldview_bin is None:
        return None
    try:
        run_ldview(ldview_bin, project, png_path, 1280, 800)
    except Exception:
        return None
    return png_path if os.path.isfile(png_path) else None


def _auto_render_enabled() -> bool:
    return os.environ.get("BRICK_MCP_AUTO_RENDER", "").lower() in ("1", "true", "yes")


def ok_with_render(project: Any, data: Any = None, message: str = "") -> list | dict:
    """Return ok() envelope, plus a PNG if BRICK_MCP_AUTO_RENDER is set.

    Off by default: a render per edit is slow and floods the conversation.
    batch() still appends one render, and render_model() renders on demand.
    """
    result = ok(data, message)
    if not _auto_render_enabled():
        return result
    img = try_render(project)
    if img is not None:
        return [result, img]
    return result


def placement_warnings(project: Any, part_id: str, submodel: Any) -> list[dict]:
    """Return a list of parts that overlap with the given part after placement.

    Uses the same AABB logic as check_overlaps / validate_placement.
    Returns an empty list when there are no conflicts or on any error
    (warnings must never block an operation).
    """
    try:
        from brick_mcp.catalog import aabbs_overlap, part_aabb

        parts = project.list_parts(submodel)
        target = next((p for p in parts if p["id"] == part_id), None)
        if target is None:
            return []

        target_bb = part_aabb(
            target["x"],
            target["y"],
            target["z"],
            target["rotation"],
            target["part_number"],
        )
        conflicts = []
        for p in parts:
            if p["id"] == part_id:
                continue
            other_bb = part_aabb(
                p["x"], p["y"], p["z"], p["rotation"], p["part_number"]
            )
            if aabbs_overlap(target_bb, other_bb):
                conflicts.append(
                    {
                        "id": p["id"],
                        "part_number": p["part_number"],
                        "x": p["x"],
                        "y": p["y"],
                        "z": p["z"],
                    }
                )
        return conflicts
    except Exception:
        return []
