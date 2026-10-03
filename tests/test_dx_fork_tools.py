"""Fork-only (dxoraxs): save_project calls POST /api/v1/project/save of the dxoraxs OrcaSlicer build."""
import json
import os

import httpx
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp import fork_tools as ft

B = "http://x:13130"
URL = f"{B}/api/v1/project/save"


def _env(m):
    m.setenv("ORCA_API_TOKEN", "tok")
    m.setenv("ORCA_API_URL", B)


@respx.mock
async def test_save_as_sends_path_and_returns_result(monkeypatch):
    _env(monkeypatch)
    ok = {"saved": True, "path": "/p/box.3mf", "bytes": 1234, "project": "/p/box"}
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=ok))
    out = await ft.save_project("/p/box.3mf")
    assert out == ok
    assert json.loads(route.calls[0].request.content) == {"overwrite": False, "path": "/p/box.3mf"}


@respx.mock
async def test_home_is_expanded_and_overwrite_passed(monkeypatch):
    _env(monkeypatch)
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"saved": True}))
    await ft.save_project("~/prints/a.3mf", overwrite=True)
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"overwrite": True, "path": os.path.expanduser("~/prints/a.3mf")}


@respx.mock
async def test_save_in_place_sends_no_path(monkeypatch):
    _env(monkeypatch)
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"saved": True}))
    await ft.save_project()
    assert json.loads(route.calls[0].request.content) == {"overwrite": False}


@respx.mock
async def test_existing_file_is_reported_with_hint(monkeypatch):
    _env(monkeypatch)
    respx.post(URL).mock(return_value=httpx.Response(409, json={"error": "exists"}))
    out = await ft.save_project("/p/a.3mf")
    assert out["error"] == "exists" and out["path"] == "/p/a.3mf" and "overwrite" in out["hint"]


@respx.mock
async def test_unnamed_project_in_place_asks_for_path(monkeypatch):
    _env(monkeypatch)
    respx.post(URL).mock(return_value=httpx.Response(409, json={"error": "no_project_path"}))
    out = await ft.save_project()
    assert out["error"] == "no_project_path" and "path" in out["hint"]


@respx.mock
async def test_stock_build_without_route_points_to_fork_build(monkeypatch):
    _env(monkeypatch)
    respx.post(URL).mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await ft.save_project("/p/a.3mf")
    assert out["error"] == "unsupported_build" and "dxoraxs/OrcaSlicer" in out["hint"]


@respx.mock
async def test_bad_extension_is_passed_through(monkeypatch):
    _env(monkeypatch)
    respx.post(URL).mock(return_value=httpx.Response(422, json={"error": "bad_extension"}))
    assert (await ft.save_project("/p/a.stl"))["error"] == "bad_extension"


async def test_fork_info():
    out = await ft.fork_info()
    assert out["fork"] == "dxoraxs" and out["version"].endswith("+dx")
    assert "save_project" in out["fork_tools"]


def test_tools_registered_with_annotations():
    tools = srv.mcp._tool_manager._tools
    assert tools["save_project"].annotations.read_only_hint is False
    assert tools["fork_info"].annotations.read_only_hint is True
