"""Tools that exist only in the dxoraxs fork. server.py stays byte-identical to upstream:
the console script points at main() here, which registers these tools and runs the server.

Projects and plates were fork tools until upstream 0.1.15-0.1.18 shipped its own; they are
upstream's now."""
from __future__ import annotations

import asyncio
import os
import shlex
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .errors import ApiError, Conflict, NotFound, Validation, BadRequest
from .server import mcp, _client, _err, _TOOL_ANNOTATIONS, _VERSION
from . import server as _srv
from . import moonraker as _mr
from . import outcomes as _outcomes
from . import mac_support as _mac

_mac.apply_env_defaults()
_mac.install_autostart()


# Upstream names the printer in the outcome store by ORCA_PRINTER_ID (save_gcode,
# list_print_history); our config says PRINTER_ID. One name for both, so rows agree.
if os.environ.get("PRINTER_ID", "").strip():
    os.environ.setdefault("ORCA_PRINTER_ID", os.environ["PRINTER_ID"].strip())

FORK = "dxoraxs"
FORK_REPO = "https://github.com/dxoraxs/orcaslicer-mcp"
ORCA_FORK_RELEASES = "https://github.com/dxoraxs/OrcaSlicer/releases"


async def _run_gcode_check(path: str) -> list[str] | None:
    """PRINT_GCODE_CHECK = a command that gets the G-code path as its last argument and exits
    non-zero listing problems on stdout. None = passed or not configured."""
    cmd = os.environ.get("PRINT_GCODE_CHECK", "").strip()
    if not cmd:
        return None
    proc = await asyncio.create_subprocess_exec(
        *shlex.split(cmd), path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
    except asyncio.TimeoutError:
        proc.kill()
        return ["gcode check timed out after 60 s"]
    if proc.returncode == 0:
        return None
    lines = [ln.strip() for ln in out.decode(errors="replace").splitlines() if ln.strip()]
    return lines or [f"gcode check exited {proc.returncode}"]


_HINTS = {
    "unsaved_changes": "the open project has unsaved changes: save_project first, or pass discard=true",
    "plate_not_empty": "move its objects away (move_object_to_plate) or delete them first",
    "last_plate": "a project always keeps one plate",
}


def _moonraker_missing() -> dict:
    return {"error": "moonraker_not_configured",
            "hint": "set MOONRAKER_URL (e.g. http://192.168.100.10) in the MCP server env"}


def _ensure_store() -> None:
    with closing(_outcomes.connect(create=True)):
        pass


async def _sync_outcomes(m: _mr.Moonraker) -> int:
    _ensure_store()
    jobs = await m.history(since=_outcomes.last_job_end_time())
    n = 0
    for job in jobs:
        if job.get("status") in _mr.FINISHED_JOB_STATES:
            _outcomes.record_outcome(job, printer_id=_mr.printer_id())
            n += 1
    return n


async def send_to_printer(
    start: Annotated[bool, Field(description=(
        "Start printing right after the upload. Refused while the printer is printing or paused."))] = False,
    filename: Annotated[str | None, Field(description=(
        "Name for the G-code file (default <object>_<timestamp>.gcode)."))] = None,
    skip_check: Annotated[bool, Field(description=(
        "Skip the PRINT_GCODE_CHECK command. Only when the user explicitly asks."))] = False,
) -> dict:
    """Send the last successful slice to the Klipper printer through Moonraker, optionally starting
    the print. Steps: Klipper must be ready (and idle when start=true); the G-code is saved and
    the slice recorded exactly as save_gcode does (so sync_print_outcomes can later join the real
    result); the PRINT_GCODE_CHECK command, if configured, must pass; then the file is uploaded.
    Needs MOONRAKER_URL."""
    url = _mr.base_url()
    if not url:
        return _moonraker_missing()
    try:
        async with _mr.Moonraker(url) as m:
            info = await m.info()
            if info.get("state") != "ready":
                return {"error": "printer_not_ready", "klippy_state": info.get("state"),
                        "message": info.get("state_message")}
            if start:
                ps = (await m.status()).get("print_stats", {}).get("state")
                if ps is None or ps in _mr.ACTIVE_PRINT_STATES:
                    return {"error": "printer_busy", "print_state": ps}
            _ensure_store()  # save_gcode records the slice only into an existing store
            saved = await _srv.save_gcode(filename)
            if "error" in saved:
                return saved
            if not skip_check:
                problems = await _run_gcode_check(saved["path"])
                if problems:
                    return {"error": "gcode_check_failed", "problems": problems,
                            "path": saved["path"]}
            up = await m.upload(Path(saved["path"]), start)
            try:
                await _sync_outcomes(m)
            except (_mr.MoonrakerError, sqlite3.Error):
                pass  # history is a best-effort side job; the upload already happened
    except _mr.MoonrakerError as e:
        return {"error": e.code, "detail": e.detail}
    return {"uploaded": (up.get("item") or {}).get("path", saved["filename"]),
            "print_started": bool(up.get("print_started")), "path": saved["path"],
            "outcome_recorded": saved.get("outcome_recorded")}


async def printer_status() -> dict:
    """Klipper/Moonraker state of the printer: klippy state, print state, current file and
    progress, extruder and bed temperatures. Read-only. Needs MOONRAKER_URL."""
    url = _mr.base_url()
    if not url:
        return _moonraker_missing()
    try:
        async with _mr.Moonraker(url) as m:
            info = await m.info()
            st = await m.status() if info.get("state") == "ready" else {}
    except _mr.MoonrakerError as e:
        return {"error": e.code, "detail": e.detail}
    ps = st.get("print_stats", {})
    return {"klippy_state": info.get("state"), "message": info.get("state_message"),
            "print_state": ps.get("state"), "filename": ps.get("filename") or None,
            "progress": (st.get("virtual_sdcard") or {}).get("progress"),
            "print_duration_s": ps.get("print_duration"),
            "extruder": st.get("extruder"), "heater_bed": st.get("heater_bed")}


async def sync_print_outcomes() -> dict:
    """Pull finished jobs from the printer's Moonraker history into the print-outcome store
    (result, duration, filament), joined to the slice that send_to_printer/save_gcode recorded.
    Run it before recall_prints so past prints show their real results. Needs MOONRAKER_URL."""
    url = _mr.base_url()
    if not url:
        return _moonraker_missing()
    try:
        async with _mr.Moonraker(url) as m:
            n = await _sync_outcomes(m)
    except _mr.MoonrakerError as e:
        return {"error": e.code, "detail": e.detail}
    return {"recorded": n, "store": str(_outcomes.db_path())}


async def set_print_verdict(
    verdict: Annotated[str, Field(description="What the user said about the print, e.g. 'corners lifted'.")],
    gcode_filename: Annotated[str | None, Field(description=(
        "Which print; default the most recent printed job."))] = None,
) -> dict:
    """Attach the user's verdict on a finished print to its outcome row, so recall_prints shows
    it next to the settings. Run sync_print_outcomes first if the job just finished."""
    if not _outcomes.is_available():
        return {"error": "no_outcome_store", "hint": "run sync_print_outcomes first"}
    return _outcomes.set_verdict(verdict, gcode_filename)


CalibrationKind = Literal["temp_tower", "flow_rate", "pa_tower", "pa_line", "pa_pattern",
                          "max_volumetric", "retraction"]


def _calibration_err(e: ApiError) -> dict:
    if isinstance(e, NotFound) and e.route_missing:
        return {"error": "not available on this OrcaSlicer build",
                "hint": f"needs the dxoraxs OrcaSlicer build with POST /api/v1/calibration ({ORCA_FORK_RELEASES})"}
    out = _err(e)
    if not out.get("errors"):
        out.pop("errors", None)
    body = getattr(e, "body", None) or {}
    for k in ("detail", "modified", "project"):
        if k in body:
            out[k] = body[k]
    return out


async def create_calibration(
    kind: Annotated[CalibrationKind, Field(description=(
        "The test, as in OrcaSlicer's Calibration menu: temp_tower (temperature tower), flow_rate, "
        "pa_tower / pa_line / pa_pattern (pressure advance), max_volumetric (max volumetric speed), "
        "retraction (retraction tower)."))],
    start: Annotated[float | None, Field(description=(
        "First value: °C for temp_tower (the hottest, bottom block), PA, mm³/s, or mm of retraction. "
        "Omitted: the menu's default."))] = None,
    end: Annotated[float | None, Field(description=(
        "Last value: °C for temp_tower (the coldest, top block; at least 5 below start), PA, mm³/s, mm."))] = None,
    step: Annotated[float | None, Field(description=(
        "Step for pa_*, max_volumetric and retraction. temp_tower always steps 5 °C."))] = None,
    flow_pass: Annotated[int | None, Field(description="flow_rate only: pass 1 (coarse) or 2 (fine).")] = None,
    linear: Annotated[bool | None, Field(description=(
        "flow_rate only: true = Orca YOLO (linear) flow test, false = classic pass 1/2."))] = None,
    pattern: Annotated[Literal["archimedean", "monotonic"] | None, Field(description=(
        "flow_rate only: top surface pattern of the test pads."))] = None,
    print_numbers: Annotated[bool | None, Field(description="pa_line only: print the PA values.")] = None,
    accelerations: Annotated[list[float] | None, Field(description="pa_pattern only: accelerations, mm/s².")] = None,
    speeds: Annotated[list[float] | None, Field(description="pa_pattern only: speeds, mm/s.")] = None,
    nozzle_based_resize: Annotated[bool | None, Field(description=(
        "temp_tower only: scale the tower to the nozzle (default true, as in the menu)."))] = None,
    if_unsaved: Annotated[Literal["save", "discard", "fail"], Field(description=(
        "The test replaces the open project. Unsaved changes: 'fail' (default) refuses, 'save' "
        "saves to its path first, 'discard' drops them."))] = "fail",
    if_preset_changes: Annotated[Literal["discard", "fail"], Field(description=(
        "Edited, unsaved presets: 'fail' (default) refuses and lists them in modified, 'discard' "
        "drops the edits. Save a preset with save_preset to keep it."))] = "fail",
) -> dict:
    """Create a calibration test exactly as OrcaSlicer's Calibration menu does, without its dialog:
    a new project with the test model, its settings, and the per-height value (temperature, PA,
    speed, retraction) that OrcaSlicer applies while slicing. Select the printer, filament and
    process presets first: the test is built for them. Then slice and show the preview to the user;
    print only after the user confirms. Needs the dxoraxs OrcaSlicer build."""
    body: dict = {"kind": kind}
    for key, value in (("start", start), ("end", end), ("step", step), ("pass", flow_pass),
                       ("linear", linear), ("pattern", pattern), ("print_numbers", print_numbers),
                       ("accelerations", accelerations), ("speeds", speeds),
                       ("nozzle_based_resize", nozzle_based_resize)):
        if value is not None:
            body[key] = value
    body["if_unsaved"] = if_unsaved
    body["if_preset_changes"] = if_preset_changes
    try:
        async with _client() as c:
            return await c._request("POST", "/api/v1/calibration", json=body, timeout=130.0)
    except ApiError as e:
        return _calibration_err(e)


async def fork_info() -> dict:
    """Which build of this MCP server is running: the upstream version it is based on, the
    fork, and the fork-only tools it adds."""
    return {"version": f"{_VERSION}+dx", "upstream_version": _VERSION, "fork": FORK,
            "repo": FORK_REPO, "orca_build": ORCA_FORK_RELEASES,
            "fork_tools": [fn.__name__ for fn in _FORK_TOOLS]}


_FORK_TOOLS = {
    send_to_printer: ("Send G-code to the printer (optionally start)", False, False),
    printer_status: ("Printer status (Moonraker)", True, False),
    sync_print_outcomes: ("Sync print results from Moonraker", False, False),
    set_print_verdict: ("Record verdict on a print", False, False),
    create_calibration: ("Create a calibration test (Calibration menu)", False, True),
    fork_info: ("Fork build info", True, False),
}


def register() -> None:
    """Add the fork tools to upstream's server. Idempotent."""
    for fn, annotation in _FORK_TOOLS.items():
        if fn.__name__ not in _TOOL_ANNOTATIONS:
            mcp.tool()(fn)
            _TOOL_ANNOTATIONS[fn.__name__] = annotation
    _srv._apply_tool_annotations()


def main() -> None:
    register()
    _srv.main()
