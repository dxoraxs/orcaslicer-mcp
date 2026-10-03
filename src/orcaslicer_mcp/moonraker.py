"""Fork-only (dxoraxs): a minimal Moonraker client for sending sliced G-code to a Klipper printer
and reading its state and job history. Configured by MOONRAKER_URL (e.g. http://192.168.100.10);
Moonraker's trusted_clients is expected to admit this host, so no API key is sent."""
from __future__ import annotations

import os
from pathlib import Path

import httpx

ACTIVE_PRINT_STATES = {"printing", "paused"}
FINISHED_JOB_STATES = {"completed", "cancelled", "error", "klippy_shutdown", "klippy_disconnect",
                       "interrupted", "server_exit"}


class MoonrakerError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def base_url() -> str | None:
    url = os.environ.get("MOONRAKER_URL", "").strip().rstrip("/")
    return url or None


def printer_id() -> str:
    return os.environ.get("PRINTER_ID", "biqu")


def _result(resp: httpx.Response) -> dict:
    data = resp.json()
    return data.get("result", data) if isinstance(data, dict) else {}


class Moonraker:
    def __init__(self, url: str, timeout: float = 10.0):
        self._http = httpx.AsyncClient(base_url=url, timeout=timeout)
        self._url = url

    async def __aenter__(self) -> "Moonraker":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, **kw) -> dict:
        try:
            resp = await self._http.request(method, path, **kw)
        except httpx.TransportError as e:
            raise MoonrakerError("printer_unreachable", f"{self._url}: {e}") from e
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("error", {}).get("message", "")
            except Exception:
                msg = resp.text[:200]
            raise MoonrakerError("moonraker_error", f"HTTP {resp.status_code} {path}: {msg}")
        return _result(resp)

    async def info(self) -> dict:
        return await self._call("GET", "/printer/info")

    async def status(self) -> dict:
        q = "print_stats&virtual_sdcard=progress&extruder=temperature,target&heater_bed=temperature,target"
        return (await self._call("GET", f"/printer/objects/query?{q}")).get("status", {})

    async def upload(self, path: Path, start: bool, timeout: float = 120.0) -> dict:
        with path.open("rb") as fh:
            return await self._call(
                "POST", "/server/files/upload",
                files={"file": (path.name, fh, "application/octet-stream")},
                data={"root": "gcodes", "print": "true" if start else "false"},
                timeout=timeout)

    async def history(self, since: float = 0.0, limit: int = 50) -> list[dict]:
        r = await self._call("GET", "/server/history/list",
                             params={"limit": limit, "since": since, "order": "asc"})
        return r.get("jobs") or []
