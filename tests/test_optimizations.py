"""Tests for search ranking, Studio name mapping, measured sizes and render output."""

import asyncio
import json
import os
import stat
import zipfile
from unittest.mock import patch

import pytest

from fastmcp import Client

from brick_mcp import catalog, io_file, studio
from brick_mcp.server import mcp
from brick_mcp.tools.batch import batch
from brick_mcp.tools.file_ops import new_model, open_model, save_model
from brick_mcp.tools.manipulation import add_part
from brick_mcp.tools.render import render_model


@pytest.fixture
def fake_studio(monkeypatch, tmp_path):
    """A minimal Studio install, with the module's cached tables reset around it."""
    data = tmp_path / "studio" / "data"
    data.mkdir(parents=True)
    (data.parent / "version.txt").write_text("Version\n9.9.9_1\n")
    (data / "StudioPartDefinition2.txt").write_text(
        "Studio ItemNo\tBase\tBL ItemNo\tKey\tLDraw ItemNo\tLDD\tDescription\n"
        "1\t1\t3001\t1\t3001.dat\t\tBrick 2 x 4\n"
        "2\t2\t67351c01\t2\t67351-bl.dat\t\tElectric, Hub Small - SPIKE Prime\n"
        "3\t3\t37308c01\t3\t37308c01.dat\t\tElectric Sensor, Color - SPIKE Prime\n"
    )
    monkeypatch.setenv("STUDIO_DIR", str(data.parent))
    for name, value in (
        ("_known", None),
        ("_by_bl", {}),
        ("_reverse", {}),
        ("_descriptions", {}),
    ):
        monkeypatch.setattr(studio, name, value)


@pytest.fixture
def fake_library(monkeypatch, tmp_path):
    """A complete.zip with one part built from a subpart, with geometry caches reset."""
    path = tmp_path / "complete.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "ldraw/parts/9999.dat",
            "0 Electric Test Hub\n"
            "3 16 -30 0 -10 30 0 -10 30 40 10\n"
            "1 16 0 -5 0 1 0 0 0 1 0 0 0 1 s\\9999s01.dat\n",
        )
        zf.writestr("ldraw/parts/s/9999s01.dat", "0 ~sub\n3 16 0 0 0 0 0 20 5 0 0\n")
    monkeypatch.setenv("LDRAW_COMPLETE_ZIP", str(path))
    monkeypatch.setattr(catalog, "_catalog", None)
    monkeypatch.setattr(catalog, "_search_index", None)
    monkeypatch.setattr(catalog, "_geom_entries", None)
    monkeypatch.setattr(catalog, "_geom_zip", None)
    monkeypatch.setattr(catalog, "_geom_points", {})
    monkeypatch.setattr(catalog, "_bbox_cache", {})


@pytest.fixture
def catalog_of(monkeypatch):
    def install(**names):
        parts = {
            f"{pn.removeprefix('p')}.dat": {
                "part_number": f"{pn.removeprefix('p')}.dat",
                "name": name,
                "category": catalog._guess_category(name),
            }
            for pn, name in names.items()
        }
        monkeypatch.setattr(catalog, "_catalog", parts)
        monkeypatch.setattr(catalog, "_search_index", None)

    return install


class TestSearchRanking:
    def test_plain_part_ranks_first(self, catalog_of):
        catalog_of(
            p15466="~Electric Brick  2 x  4 with USB",
            p189c01="Electric Train  4.5V On/Off Switch Brick  2 x  4",
            p3001p01="Brick  2 x  4 with Red Pattern",
            p3001="Brick  2 x  4",
        )
        first = catalog.search_catalog("brick 2x4")[0]
        assert first["part_number"] == "3001.dat"

    def test_dimensions_match_whole(self, catalog_of):
        catalog_of(p1="Brick 12 x 4", p2="Brick  2 x  4")
        result = catalog.search_catalog("2x4")
        assert [p["part_number"] for p in result] == ["2.dat"]

    def test_words_match_in_any_order(self, catalog_of):
        catalog_of(p1="Plate  2 x  4", p2="Brick  2 x  4")
        assert catalog.search_catalog("2x4 plate")[0]["part_number"] == "1.dat"

    def test_british_and_american_spelling(self, catalog_of):
        catalog_of(p1="Electric Colour Sensor")
        assert catalog.search_catalog("color sensor")
        assert catalog.search_catalog("colour sensor")

    def test_lego_names_find_spike_parts(self, catalog_of):
        catalog_of(
            p67351c01="Electric Powered Up 2 Port Hub",
            p27254="Wheel 37 x 62 with Rocky Spikes",
        )
        assert catalog.search_catalog("spike hub")[0]["part_number"] == "67351c01.dat"
        assert catalog.search_catalog("45609")[0]["part_number"] == "67351c01.dat"
        assert catalog.search_catalog("spike")[0]["part_number"] == "67351c01.dat"

    def test_subparts_hidden_unless_only_match(self, catalog_of):
        catalog_of(p1="~Motor Case", p2="Motor")
        assert [p["part_number"] for p in catalog.search_catalog("motor")] == ["2.dat"]
        assert len(catalog.search_catalog("motor", include_subparts=True)) == 2
        assert catalog.search_catalog("case")[0]["part_number"] == "1.dat"


class TestStudioNames:
    def test_maps_both_ways(self, fake_studio):
        assert studio.to_studio_part("67351c01.dat") == "67351-bl.dat"
        assert studio.from_studio_part("67351-bl.dat") == "67351c01.dat"

    def test_bare_part_maps_to_its_assembly(self, fake_studio):
        assert studio.to_studio_part("37308.dat") == "37308c01.dat"

    def test_known_and_unknown_parts_unchanged(self, fake_studio):
        assert studio.to_studio_part("3001.dat") == "3001.dat"
        assert studio.to_studio_part("nosuch.dat") == "nosuch.dat"
        assert studio.from_studio_part("3001.dat") == "3001.dat"

    def test_no_studio_is_a_no_op(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STUDIO_DIR", str(tmp_path / "missing"))
        monkeypatch.setattr(studio, "_known", None)
        assert studio.to_studio_part("67351c01.dat") == "67351c01.dat"
        assert studio.version() == studio.DEFAULT_VERSION

    def test_io_round_trip_restores_ldraw_names(self, fake_studio, tmp_path):
        path = str(tmp_path / "m.io")
        new_model("m")
        add_part("67351c01", 15, 0, 0, 0)
        assert save_model(path)["ok"] is True

        with zipfile.ZipFile(path) as zf:
            assert b"67351-bl.dat" in zf.read("model.ldr")
            assert b"67351-bl.dat" in zf.read("modelv2.ldr")
            assert b"9.9.9_1" in zf.read(".info")

        model_bytes, _ = io_file.read_io_file(path)
        assert b"67351c01.dat" in model_bytes
        assert b"-bl.dat" not in model_bytes
        assert open_model(path)["ok"] is True


class TestMeasuredSize:
    def test_bbox_includes_subparts(self, fake_library):
        assert catalog.get_part_bbox("9999") == (-30, 30, -5, 40, -10, 20)

    def test_unnamed_size_uses_geometry(self, fake_library):
        assert catalog.get_part_dims("9999") == (30, 45, 15)

    def test_aabb_uses_real_offset_box(self, fake_library):
        identity = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        assert catalog.part_aabb(100, 0, 0, identity, "9999") == (
            70,
            130,
            -5,
            40,
            -10,
            20,
        )

    def test_named_size_ignores_geometry(self, fake_library):
        assert catalog.measured_bbox("3001") is None
        assert catalog.get_part_dims("3001") == (40.0, 24.0, 20.0)

    def test_missing_library_falls_back(self, monkeypatch):
        monkeypatch.delenv("LDRAW_COMPLETE_ZIP", raising=False)
        monkeypatch.setattr(catalog, "_geom_entries", {})
        monkeypatch.setattr(catalog, "_bbox_cache", {})
        monkeypatch.setattr(catalog, "_geom_points", {})
        assert catalog.get_part_bbox("nosuch") is None
        assert catalog.get_part_dims("nosuch") == (10.0, 24.0, 10.0)


@pytest.fixture
def fake_ldview(tmp_path):
    """An executable that writes a PNG wherever -SaveSnapshot points."""
    exe = tmp_path / "ldview"
    exe.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in\n'
        '  -SaveSnapshot=*) printf PNG > "${a#-SaveSnapshot=}";;\n'
        "esac; done\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    with patch("brick_mcp.tools.render._find_ldview", return_value=str(exe)), patch(
        "brick_mcp._helpers._find_ldview", return_value=str(exe)
    ):
        yield str(exe)


class TestRenderOutput:
    def test_saved_next_to_model_and_path_returned(self, fake_ldview, tmp_path):
        new_model("m")
        add_part("3001", 4, 0, 0, 0)
        save_model(str(tmp_path / "m.io"))
        info, image = render_model()
        assert info["data"]["path"] == str(tmp_path / "m_render.png")
        assert os.path.isfile(info["data"]["path"])
        assert image.data == b"PNG"

    def test_explicit_output_path(self, fake_ldview, tmp_path):
        new_model("m")
        target = tmp_path / "sub" / "out.png"
        info, _image = render_model(output_path=str(target))
        assert info["data"]["path"] == str(target)
        assert target.is_file()

    def test_view_angle_gets_its_own_file(self, fake_ldview, tmp_path):
        new_model("m")
        save_model(str(tmp_path / "m.ldr"))
        info, _image = render_model(longitude=225)
        assert info["data"]["path"].endswith("m_render_lat30_lon225.png")

    def test_open_in_viewer(self, fake_ldview, tmp_path):
        new_model("m")
        target = tmp_path / "out.png"

        def write_png(_bin, _project, png_path, *_args):
            with open(png_path, "wb") as fh:
                fh.write(b"PNG")

        with patch("brick_mcp.tools.render.sys.platform", "darwin"), patch(
            "brick_mcp.tools.render.run_ldview", side_effect=write_png
        ), patch("brick_mcp.tools.render.subprocess.Popen") as popen:
            render_model(output_path=str(target), open_in_viewer=True)
        popen.assert_called_once_with(["open", str(target)])

    def test_save_writes_preview(self, fake_ldview, tmp_path):
        new_model("m")
        result = save_model(str(tmp_path / "m.io"))
        assert result["data"]["preview_path"] == str(tmp_path / "m_preview.png")
        assert (tmp_path / "m_preview.png").is_file()

    def test_edits_do_not_render_by_default(self, fake_ldview, monkeypatch):
        monkeypatch.delenv("BRICK_MCP_AUTO_RENDER", raising=False)
        new_model("m")
        assert isinstance(add_part("3001", 4, 0, 0, 0), dict)

    def test_batch_still_renders_once(self, fake_ldview):
        result = batch(calls=[{"tool": "new_model", "args": {"name": "m"}}])
        assert isinstance(result, list)

    def test_render_serializes_over_mcp(self, fake_ldview, tmp_path):
        """Output-schema errors only show up through the MCP protocol."""
        new_model("m")
        target = tmp_path / "out.png"

        async def call():
            async with Client(mcp) as client:
                return await client.call_tool(
                    "render_model", {"output_path": str(target)}
                )

        result = asyncio.run(call())
        assert json.loads(result.content[0].text)["data"]["path"] == str(target)
        assert result.content[1].type == "image"
