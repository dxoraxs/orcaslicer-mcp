"""Fork-only (dxoraxs): create_calibration against a mocked OrcaSlicer POST /api/v1/calibration."""
import json

import httpx
import respx

from orcaslicer_mcp import fork_tools as ft

B = "http://orca:13130"
ROUTE = f"{B}/api/v1/calibration"


def _env(m):
    m.setenv("ORCA_API_TOKEN", "tok")
    m.setenv("ORCA_API_URL", B)


@respx.mock
async def test_sends_only_the_given_fields_and_returns_the_reply(monkeypatch):
    _env(monkeypatch)
    reply = {"ok": True, "kind": "temp_tower", "objects": 1, "project": {"name": "Nozzle temperature test"},
             "modified": ["print", "filament"]}
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json=reply))
    out = await ft.create_calibration("temp_tower", start=250, end=220)
    assert out == reply
    assert json.loads(route.calls.last.request.content) == {
        "kind": "temp_tower", "start": 250, "end": 220, "if_unsaved": "fail", "if_preset_changes": "fail"}


@respx.mock
async def test_flow_rate_passes_its_own_fields(monkeypatch):
    _env(monkeypatch)
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"ok": True}))
    await ft.create_calibration("flow_rate", flow_pass=2, linear=True, pattern="monotonic",
                                if_unsaved="discard", if_preset_changes="discard")
    assert json.loads(route.calls.last.request.content) == {
        "kind": "flow_rate", "pass": 2, "linear": True, "pattern": "monotonic",
        "if_unsaved": "discard", "if_preset_changes": "discard"}


@respx.mock
async def test_edited_presets_refusal_keeps_orcas_explanation(monkeypatch):
    _env(monkeypatch)
    respx.post(ROUTE).mock(return_value=httpx.Response(409, json={
        "error": "unsaved_preset_changes", "modified": ["filament"],
        "detail": "save the presets, or pass if_preset_changes: \"discard\""}))
    out = await ft.create_calibration("pa_tower")
    assert "unsaved_preset_changes" in out["error"]
    assert out["modified"] == ["filament"]
    assert "if_preset_changes" in out["detail"]


@respx.mock
async def test_bad_range_is_reported_with_the_rule(monkeypatch):
    _env(monkeypatch)
    respx.post(ROUTE).mock(return_value=httpx.Response(400, json={
        "error": "bad_range", "detail": "start <= 500, end >= 155, start >= end + 5, whole degrees"}))
    out = await ft.create_calibration("temp_tower", start=200, end=210)
    assert "bad_range" in out["error"]
    assert "start >= end + 5" in out["detail"]


@respx.mock
async def test_build_without_the_route_says_which_build_is_needed(monkeypatch):
    _env(monkeypatch)
    respx.post(ROUTE).mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await ft.create_calibration("retraction")
    assert "not available" in out["error"]
    assert out["hint"].startswith("needs the dxoraxs OrcaSlicer build")
