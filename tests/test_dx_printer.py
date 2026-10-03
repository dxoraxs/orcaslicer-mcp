"""Fork-only (dxoraxs): send_to_printer, printer_status, sync_print_outcomes, set_print_verdict
against a mocked Moonraker (and a mocked OrcaSlicer for the slice)."""
import sys

import httpx
import respx

from orcaslicer_mcp import fork_tools as ft
from orcaslicer_mcp import outcomes as oc

B = "http://orca:13130"
M = "http://printer"
OBJS = {"count": 1, "objects": [{"id": 1, "index": 0, "name": "box", "size_mm": [20, 20, 10], "instances": 1,
                                 "transform": {"offset": [0, 0, 5], "rotation": [0, 0, 0], "scale": [1, 1, 1]}}]}
GCODE = b"PRINT_START\nG1 X1\n"


def _env(m, tmp_path, check: str | None = None):
    m.setenv("ORCA_API_TOKEN", "tok"); m.setenv("ORCA_API_URL", B)
    m.setenv("PRINT_OUTCOMES_DIR", str(tmp_path))
    m.setenv("MOONRAKER_URL", M)
    if check is None:
        m.delenv("PRINT_GCODE_CHECK", raising=False)
    else:
        m.setenv("PRINT_GCODE_CHECK", check)
    respx.get(f"{B}/api/v1/gcode").mock(return_value=httpx.Response(200, content=GCODE))
    respx.get(f"{B}/api/v1/objects").mock(return_value=httpx.Response(200, json=OBJS))
    respx.get(url__regex=rf"{B}/api/v1/config.*").mock(
        return_value=httpx.Response(200, json={"config": {"layer_height": "0.2"}}))


def _printer(state="ready", print_state="standby"):
    respx.get(f"{M}/printer/info").mock(return_value=httpx.Response(
        200, json={"result": {"state": state, "state_message": "Printer is ready"}}))
    respx.get(url__regex=rf"{M}/printer/objects/query.*").mock(return_value=httpx.Response(
        200, json={"result": {"status": {"print_stats": {"state": print_state, "filename": ""},
                                         "virtual_sdcard": {"progress": 0.0},
                                         "extruder": {"temperature": 25.0, "target": 0.0},
                                         "heater_bed": {"temperature": 24.0, "target": 0.0}}}}))
    respx.get(url__regex=rf"{M}/server/history/list.*").mock(return_value=httpx.Response(
        200, json={"result": {"count": 0, "jobs": []}}))


def _upload(print_started: bool):
    return respx.post(f"{M}/server/files/upload").mock(return_value=httpx.Response(
        201, json={"result": {"item": {"path": "box.gcode", "root": "gcodes"},
                              "print_started": print_started, "action": "create_file"}}))


@respx.mock
async def test_send_uploads_without_starting(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer()
    up = _upload(False)
    out = await ft.send_to_printer(filename="box.gcode")
    assert out["uploaded"] == "box.gcode" and out["print_started"] is False
    body = up.calls[0].request.content
    assert b'name="print"' in body and b"false" in body and GCODE in body
    assert (tmp_path / "gcode" / "box.gcode").read_bytes() == GCODE
    assert out["outcome_recorded"] is True


@respx.mock
async def test_send_and_start(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer()
    up = _upload(True)
    out = await ft.send_to_printer(start=True, filename="box.gcode")
    assert out["print_started"] is True
    assert b"true" in up.calls[0].request.content


@respx.mock
async def test_start_refused_while_printing(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer(print_state="printing")
    up = _upload(True)
    out = await ft.send_to_printer(start=True, filename="box.gcode")
    assert out["error"] == "printer_busy" and up.calls.call_count == 0


@respx.mock
async def test_upload_only_allowed_while_printing(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer(print_state="printing")
    _upload(False)
    out = await ft.send_to_printer(start=False, filename="box.gcode")
    assert out["uploaded"] == "box.gcode"


@respx.mock
async def test_klipper_not_ready_refuses(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer(state="shutdown")
    up = _upload(False)
    out = await ft.send_to_printer(filename="box.gcode")
    assert out["error"] == "printer_not_ready" and out["klippy_state"] == "shutdown"
    assert up.calls.call_count == 0


@respx.mock
async def test_failed_gcode_check_blocks_upload(monkeypatch, tmp_path):
    check = f'{sys.executable} -c "import sys; print(\'нет PRINT_END\'); sys.exit(1)"'
    _env(monkeypatch, tmp_path, check=check); _printer()
    up = _upload(False)
    out = await ft.send_to_printer(filename="box.gcode")
    assert out["error"] == "gcode_check_failed" and out["problems"] == ["нет PRINT_END"]
    assert up.calls.call_count == 0


@respx.mock
async def test_passing_gcode_check_gets_the_file_path(monkeypatch, tmp_path):
    marker = tmp_path / "checked.txt"
    check = f'{sys.executable} -c "import sys,pathlib; pathlib.Path(r\'{marker}\').write_text(sys.argv[1])"'
    _env(monkeypatch, tmp_path, check=check); _printer()
    _upload(False)
    out = await ft.send_to_printer(filename="box.gcode")
    assert "error" not in out
    assert marker.read_text() == str(tmp_path / "gcode" / "box.gcode")


@respx.mock
async def test_printer_unreachable(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    respx.get(f"{M}/printer/info").mock(side_effect=httpx.ConnectError("down"))
    out = await ft.send_to_printer(filename="box.gcode")
    assert out["error"] == "printer_unreachable"
    assert "ConnectError" in out["detail"]


async def test_not_configured(monkeypatch):
    monkeypatch.delenv("MOONRAKER_URL", raising=False)
    assert (await ft.printer_status())["error"] == "moonraker_not_configured"


@respx.mock
async def test_printer_status(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer(print_state="printing")
    out = await ft.printer_status()
    assert out["klippy_state"] == "ready" and out["print_state"] == "printing"
    assert out["extruder"] == {"temperature": 25.0, "target": 0.0}


@respx.mock
async def test_sync_outcomes_joins_history_to_slice(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer()
    _upload(False)
    await ft.send_to_printer(filename="box.gcode")
    job = {"job_id": "000A", "filename": "box.gcode", "status": "completed", "end_time": 1000.0,
           "total_duration": 3600.0, "metadata": {"filament_weight_total": 12.5}}
    running = {"job_id": "000B", "filename": "next.gcode", "status": "in_progress"}
    respx.get(url__regex=rf"{M}/server/history/list.*").mock(return_value=httpx.Response(
        200, json={"result": {"count": 2, "jobs": [job, running]}}))
    out = await ft.sync_print_outcomes()
    assert out["recorded"] == 1
    row = oc.recall(model_name="box")[0]
    assert row["result"] == "success" and row["duration_s"] == 3600.0 and row["filament_g"] == 12.5
    assert row["printer_id"] == "biqu"


@respx.mock
async def test_set_print_verdict(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path); _printer()
    _upload(False)
    await ft.send_to_printer(filename="box.gcode")
    job = {"job_id": "000A", "filename": "box.gcode", "status": "completed", "end_time": 1000.0}
    respx.get(url__regex=rf"{M}/server/history/list.*").mock(return_value=httpx.Response(
        200, json={"result": {"count": 1, "jobs": [job]}}))
    await ft.sync_print_outcomes()
    out = await ft.set_print_verdict("углы отслоились")
    assert out["human_verdict"] == "углы отслоились"
