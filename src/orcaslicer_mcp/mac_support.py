"""Fork-only (dxoraxs): desktop conveniences around the OrcaSlicer connection.

- apply_env_defaults(): when ORCA_API_TOKEN is not set, take the token from OrcaSlicer's own
  config (OrcaSlicer.conf, app.remote_api_token), so the MCP config holds no secret and a
  regenerated token keeps working. ORCA_CONF_PATH overrides the config location.
- install_autostart(): when OrcaSlicer is not running at all, the first unreachable request
  launches it in the background (macOS `open -g`: no focus change) and waits for the API,
  then retries once. Opt-in: ORCA_AUTOSTART=1. If OrcaSlicer runs but the API does not
  answer, nothing is launched and the error says to enable the Remote API.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .client import OrcaClient
from .errors import NotReachable

READY_TIMEOUT = 90.0
POLL = 1.0
_platform = sys.platform


def conf_path() -> Path:
    override = os.environ.get("ORCA_CONF_PATH")
    if override:
        return Path(override)
    if _platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OrcaSlicer" / "OrcaSlicer.conf"
    if _platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "OrcaSlicer" / "OrcaSlicer.conf"
    return Path.home() / ".config" / "OrcaSlicer" / "OrcaSlicer.conf"


def _read_conf() -> dict:
    try:
        raw = conf_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    # The file is JSON, optionally followed by a "# MD5 checksum ..." line.
    body = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def apply_env_defaults() -> None:
    if os.environ.get("ORCA_API_TOKEN", "").strip():
        return
    token = (_read_conf().get("app") or {}).get("remote_api_token")
    if isinstance(token, str) and token.strip():
        os.environ["ORCA_API_TOKEN"] = token.strip()


class _Host:
    def is_running(self) -> bool:
        try:
            return subprocess.run(["pgrep", "-x", "OrcaSlicer"], capture_output=True).returncode == 0
        except OSError:
            return True  # cannot tell: never launch a second copy

    async def launch(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            "open", "-g", "-a", "OrcaSlicer",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise NotReachable(f"could not start OrcaSlicer: {err.decode(errors='replace').strip()}")


_host = _Host()
_launch_lock = asyncio.Lock()


def _autostart_enabled() -> bool:
    return _platform == "darwin" and os.environ.get("ORCA_AUTOSTART", "").strip().lower() in ("1", "true", "yes")


async def _wait_ready(client: OrcaClient, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client._http.get("/api/v1/health", timeout=2.0)
            if resp.status_code < 500:
                return True
        except Exception:
            pass
        await asyncio.sleep(POLL)
    return False


async def _ensure_orca(client: OrcaClient, original: NotReachable) -> None:
    """Return when OrcaSlicer is up after a launch; raise NotReachable otherwise."""
    if not _autostart_enabled():
        raise original
    async with _launch_lock:
        if _host.is_running():
            # Maybe another call just launched it: give the API a moment before blaming settings.
            if await _wait_ready(client, min(5.0, READY_TIMEOUT)):
                return
            raise NotReachable(
                f"{original} (OrcaSlicer is running but its Remote API does not answer: "
                "enable it in OrcaSlicer Preferences > Remote API)") from original
        await _host.launch()
        if not await _wait_ready(client, READY_TIMEOUT):
            raise NotReachable(f"OrcaSlicer was started but its Remote API did not answer "
                               f"within {READY_TIMEOUT:.0f} s") from original


_installed = False


def install_autostart() -> None:
    global _installed
    if _installed:
        return
    original_request = OrcaClient._request

    async def _request(self, method, path, **kw):
        try:
            return await original_request(self, method, path, **kw)
        except NotReachable as e:
            await _ensure_orca(self, e)
            return await original_request(self, method, path, **kw)

    OrcaClient._request = _request
    _installed = True
