"""Tests for ldview / LDraw library discovery in brick_mcp._helpers."""

import os

# Imported at module load, before conftest patches _helpers._find_ldview.
from brick_mcp import _helpers
from brick_mcp._helpers import _find_ldview, _ldraw_dir_args


def _make_exe(path):
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


class TestFindLdview:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        exe = _make_exe(tmp_path / "ldview")
        monkeypatch.setenv("LDVIEW_BIN", exe)
        assert _find_ldview() == exe

    def test_macos_app_fallback(self, monkeypatch, tmp_path):
        exe = _make_exe(tmp_path / "LDView")
        monkeypatch.delenv("LDVIEW_BIN", raising=False)
        monkeypatch.setattr(_helpers.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_helpers.glob, "glob", lambda _pattern: [])
        monkeypatch.setattr(_helpers, "_MACOS_LDVIEW_PATHS", (exe,))
        assert _find_ldview() == exe

    def test_none_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LDVIEW_BIN", raising=False)
        monkeypatch.setattr(_helpers.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_helpers.glob, "glob", lambda _pattern: [])
        monkeypatch.setattr(
            _helpers, "_MACOS_LDVIEW_PATHS", (str(tmp_path / "missing"),)
        )
        assert _find_ldview() is None


class TestLdrawDirArgs:
    def test_ldraw_dir_env(self, monkeypatch, tmp_path):
        (tmp_path / "parts").mkdir()
        monkeypatch.setenv("LDRAW_DIR", str(tmp_path))
        assert _ldraw_dir_args() == [f"-LDrawDir={tmp_path}"]

    def test_derived_from_complete_zip(self, monkeypatch, tmp_path):
        (tmp_path / "ldraw" / "parts").mkdir(parents=True)
        monkeypatch.delenv("LDRAW_DIR", raising=False)
        monkeypatch.setenv("LDRAW_COMPLETE_ZIP", str(tmp_path / "complete.zip"))
        assert _ldraw_dir_args() == [
            f"-LDrawDir={os.path.join(tmp_path, 'ldraw')}"
        ]

    def test_empty_without_unpacked_library(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LDRAW_DIR", raising=False)
        monkeypatch.setenv("LDRAW_COMPLETE_ZIP", str(tmp_path / "complete.zip"))
        assert _ldraw_dir_args() == []
