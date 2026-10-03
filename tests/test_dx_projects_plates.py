"""Fork-only (dxoraxs): project new/open and plate tools over the dxoraxs OrcaSlicer API."""
import json

import httpx
import respx

from orcaslicer_mcp import fork_tools as ft

B = "http://x:13130"
PLATES = {"current": 0, "plates": [{"index": 0, "name": "", "objects": [{"id": 7, "name": "box"}],
                                    "slice_result_valid": False}]}


def _env(m):
    m.setenv("ORCA_API_TOKEN", "tok")
    m.setenv("ORCA_API_URL", B)


def _sent(route):
    return json.loads(route.calls[0].request.content)


@respx.mock
async def test_open_project_expands_home(monkeypatch, tmp_path):
    _env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "a.3mf").write_bytes(b"PK")
    r = respx.post(f"{B}/api/v1/project/open").mock(
        return_value=httpx.Response(200, json={"project": "/p/a", "objects": 2, "plates": 1}))
    out = await ft.open_project("~/a.3mf")
    assert out["objects"] == 2
    assert _sent(r) == {"path": str(tmp_path / "a.3mf"), "discard": False}


@respx.mock
async def test_open_project_unsaved_changes_hint(monkeypatch, tmp_path):
    _env(monkeypatch)
    (tmp_path / "a.3mf").write_bytes(b"PK")
    respx.post(f"{B}/api/v1/project/open").mock(
        return_value=httpx.Response(409, json={"error": "unsaved_changes"}))
    out = await ft.open_project(str(tmp_path / "a.3mf"))
    assert out["error"] == "unsaved_changes" and "discard" in out["hint"]


@respx.mock
async def test_open_project_missing_file(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{B}/api/v1/project/open").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    # open_project checks the file locally first, so the request is never sent
    out = await ft.open_project("/definitely/not/here.3mf")
    assert out["error"] == "file_not_found"


@respx.mock
async def test_new_project_with_discard(monkeypatch):
    _env(monkeypatch)
    r = respx.post(f"{B}/api/v1/project/new").mock(
        return_value=httpx.Response(200, json={"project": "", "objects": 0, "plates": 1}))
    out = await ft.new_project(discard=True)
    assert out["objects"] == 0 and _sent(r) == {"discard": True}


@respx.mock
async def test_list_plates(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{B}/api/v1/plates").mock(return_value=httpx.Response(200, json=PLATES))
    assert (await ft.list_plates()) == PLATES


@respx.mock
async def test_add_and_select_plate(monkeypatch):
    _env(monkeypatch)
    add = respx.post(f"{B}/api/v1/plates").mock(return_value=httpx.Response(200, json={**PLATES, "added": 1}))
    sel = respx.post(f"{B}/api/v1/plates/select").mock(return_value=httpx.Response(200, json=PLATES))
    assert (await ft.add_plate("parts"))["added"] == 1
    assert _sent(add) == {"name": "parts"}
    await ft.select_plate(0)
    assert _sent(sel) == {"index": 0}


@respx.mock
async def test_delete_nonempty_plate_refused(monkeypatch):
    _env(monkeypatch)
    respx.delete(f"{B}/api/v1/plates/1").mock(return_value=httpx.Response(409, json={"error": "plate_not_empty"}))
    out = await ft.delete_plate(1)
    assert out["error"] == "plate_not_empty" and "move" in out["hint"]


@respx.mock
async def test_move_object_to_plate(monkeypatch):
    _env(monkeypatch)
    r = respx.post(f"{B}/api/v1/objects/7/plate").mock(return_value=httpx.Response(200, json={"id": 7, "plate": 1}))
    out = await ft.move_object_to_plate(7, 1)
    assert out == {"id": 7, "plate": 1} and _sent(r) == {"index": 1}


@respx.mock
async def test_unknown_plate(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{B}/api/v1/plates/select").mock(return_value=httpx.Response(404, json={"error": "unknown_plate"}))
    assert (await ft.select_plate(9))["error"] == "unknown_plate"


@respx.mock
async def test_stock_build_reports_unsupported(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{B}/api/v1/plates").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await ft.list_plates()
    assert out["error"] == "unsupported_build" and "dxoraxs/OrcaSlicer" in out["hint"]
