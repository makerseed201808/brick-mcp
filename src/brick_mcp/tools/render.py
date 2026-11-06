"""Render the current model to a PNG image using LDView."""

from __future__ import annotations

import os
import subprocess
import sys

from fastmcp.utilities.types import Image

from brick_mcp._helpers import _find_ldview, err, ok, run_ldview
from brick_mcp.model import get_model as _get_model
from brick_mcp.server import mcp


def _output_path(project, output_path: str, latitude: float, longitude: float) -> str:
    """Pick where to save: output_path, else next to the model, else user folders."""
    if output_path.strip():
        return os.path.abspath(os.path.expanduser(output_path.strip()))

    source = project.source_path
    stem = os.path.splitext(os.path.basename(source or project.root_submodel))[0]
    name = f"{stem}_render.png"
    if (latitude, longitude) != (30, 45):
        name = f"{stem}_render_lat{latitude:g}_lon{longitude:g}.png"

    candidates = [
        os.path.dirname(source) if source else "",
        os.path.expanduser("~/Documents/brick-mcp/renders"),
        os.path.expanduser("~/brick-mcp-renders"),
    ]
    for folder in candidates:
        if not folder:
            continue
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            continue
        if os.access(folder, os.W_OK):
            return os.path.join(folder, name)
    return os.path.abspath(name)


@mcp.tool
def render_model(
    width: int = 1280,
    height: int = 800,
    latitude: float = 30,
    longitude: float = 45,
    output_path: str = "",
    open_in_viewer: bool = False,
) -> list | dict | Image:
    """Render the current model to a PNG file and return its path and the image.

    The returned image is only visible to the assistant, NOT to the user.  To show
    the user the result, tell them the returned path, and pass open_in_viewer=True
    when they ask to see it.

    Requires `ldview` on PATH, in the Nix store, at $LDVIEW_BIN, or LDView.app
    in /Applications or ~/Applications (macOS).

    Args:
        width:     Maximum image width in pixels (the image is cropped to the model).
        height:    Maximum image height in pixels.
        latitude:  Camera latitude in degrees (default 30 = standard two-thirds view).
        longitude: Camera longitude in degrees (default 45 = standard two-thirds view).
        output_path: PNG path to write. Default: next to the saved model, else
                   ~/Documents/brick-mcp/renders.
        open_in_viewer: Also open the PNG in the system image viewer (macOS).

    Returns:
        [{"path": "...", ...}, Image] on success, or an error dict.
    """
    try:
        project = _get_model()
    except RuntimeError as exc:
        return err(str(exc), "NO_MODEL")

    ldview_bin = _find_ldview()
    if ldview_bin is None:
        return err(
            "ldview binary not found on PATH, in the Nix store or as LDView.app. "
            "Install the ldview Nix package, or on macOS LDView.app, or set LDVIEW_BIN.",
            "LDVIEW_NOT_FOUND",
        )

    png_path = _output_path(project, output_path, latitude, longitude)
    try:
        os.makedirs(os.path.dirname(png_path), exist_ok=True)
        if os.path.exists(png_path):
            os.remove(png_path)
        result = run_ldview(
            ldview_bin, project, png_path, width, height, latitude, longitude, 120
        )
    except FileNotFoundError:
        return err("ldview binary not found.", "LDVIEW_NOT_FOUND")
    except subprocess.TimeoutExpired:
        return err("LDView render timed out after 120 s.", "RENDER_TIMEOUT")
    except OSError as exc:
        return err(f"Cannot write render to {png_path}: {exc}", "RENDER_FAILED")

    if not os.path.isfile(png_path):
        stderr = (
            result.stderr.decode(errors="replace").strip()
            if result.stderr
            else "(no stderr)"
        )
        return err(
            f"LDView did not produce an image (exit {result.returncode}): {stderr}",
            "RENDER_FAILED",
        )

    if open_in_viewer and sys.platform == "darwin":
        subprocess.Popen(["open", png_path])

    with open(png_path, "rb") as fh:
        png_bytes = fh.read()

    return [
        ok(
            {"path": png_path, "opened_in_viewer": open_in_viewer},
            f"Render saved to {png_path} (tell the user this path; "
            "the image itself is only visible to the assistant)",
        ),
        Image(data=png_bytes, format="png"),
    ]
