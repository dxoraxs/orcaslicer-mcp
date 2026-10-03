"""Tools that exist only in the dxoraxs fork. Kept out of server.py so upstream merges touch
a single import line there. Imported by server.py after _TOOL_ANNOTATIONS is defined.

save_project needs the dxoraxs OrcaSlicer build (github.com/dxoraxs/OrcaSlicer), whose
Remote API adds POST /api/v1/project/save; stock MCP builds answer 404 for it."""
from __future__ import annotations

import asyncio
import os
import shlex
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .errors import ApiError, Conflict, NotFound, Validation, BadRequest
from .server import mcp, _client, _err, _TOOL_ANNOTATIONS, _VERSION
from . import server as _srv
from . import moonraker as _mr
from . import outcomes as _outcomes
from . import mac_support as _mac

_mac.apply_env_defaults()
_mac.install_autostart()

FORK = "dxoraxs"
FORK_REPO = "https://github.com/dxoraxs/orcaslicer-mcp"
ORCA_FORK_RELEASES = "https://github.com/dxoraxs/OrcaSlicer/releases"


@mcp.tool()
async def save_project(
    path: Annotated[str | None, Field(description=(
        "Absolute path of the .3mf to write on the OrcaSlicer host, e.g. "
        "'/Users/me/prints/box/box.3mf' ('~' is expanded, '.3mf' is appended when missing, "
        "missing folders are created). Omit to save the already-saved project in place."))] = None,
    overwrite: Annotated[bool, Field(description="Replace an existing file at path.")] = False,
) -> dict:
    """Save the open OrcaSlicer project (plate, objects, per-object settings, painting, presets)
    as a .3mf project file, without any dialog. OrcaSlicer then treats that file as the current
    project (get_status.project shows it, and later saves go there). Refuses an existing file
    unless overwrite=true. Needs the dxoraxs OrcaSlicer build."""
    body: dict = {"overwrite": overwrite}
    if path:
        body["path"] = os.path.expanduser(path)
    try:
        async with _client() as c:
            return await c._request("POST", "/api/v1/project/save", json=body, timeout=130.0)
    except Conflict as e:
        code = str(e)
        out = {"error": code}
        if code == "exists":
            out.update(path=body.get("path"), hint="pass overwrite=true to replace it")
        elif code == "no_project_path":
            out["hint"] = "the project has never been saved; pass a path"
        return out
    except NotFound as e:
        if e.route_missing:
            return {"error": "unsupported_build",
                    "hint": f"this OrcaSlicer has no project-save API; install the dxoraxs build "
                            f"from {ORCA_FORK_RELEASES}"}
        return _err(e)
    except (Validation, BadRequest) as e:
        return {"error": str(e)}
    except ApiError as e:
        return _err(e)


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


@mcp.tool()
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
            if saved.get("outcome_row_id"):
                # upstream save_gcode records with the store's default printer id
                with closing(_outcomes.connect()) as conn:
                    conn.execute("UPDATE prints SET printer_id=? WHERE id=?",
                                 (_mr.printer_id(), saved["outcome_row_id"]))
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def fork_info() -> dict:
    """Which build of this MCP server is running: the upstream version it is based on, the
    fork, and the fork-only tools it adds."""
    return {"version": f"{_VERSION}+dx", "upstream_version": _VERSION, "fork": FORK,
            "repo": FORK_REPO, "orca_build": ORCA_FORK_RELEASES,
            "fork_tools": ["save_project", "send_to_printer", "printer_status",
                           "sync_print_outcomes", "set_print_verdict", "fork_info"]}


_TOOL_ANNOTATIONS.update({
    "save_project": ("Save project (.3mf) to a path", False, False),
    "send_to_printer": ("Send G-code to the printer (optionally start)", False, False),
    "printer_status": ("Printer status (Moonraker)", True, False),
    "sync_print_outcomes": ("Sync print results from Moonraker", False, False),
    "set_print_verdict": ("Record verdict on a print", False, False),
    "fork_info": ("Fork build info", True, False),
})

# Normally server.py imports this module mid-way and annotates every tool afterwards. If this
# module is imported first, server.py has already annotated before these tools existed: redo it.
if hasattr(_srv, "_apply_tool_annotations"):
    _srv._apply_tool_annotations()
