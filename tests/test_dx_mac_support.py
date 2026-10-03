"""Fork-only (dxoraxs): token from OrcaSlicer.conf and background autostart of OrcaSlicer."""
import json

import httpx
import pytest
import respx

from orcaslicer_mcp import mac_support as ms
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import load_config
from orcaslicer_mcp.errors import NotReachable

B = "http://127.0.0.1:13130"


def _conf(tmp_path, app: dict) -> str:
    p = tmp_path / "OrcaSlicer.conf"
    p.write_text(json.dumps({"app": app}, indent=1))
    return str(p)


def test_token_read_from_conf_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCA_API_TOKEN", raising=False)
    monkeypatch.setenv("ORCA_CONF_PATH", _conf(tmp_path, {"remote_api_token": "abc", "remote_api_enabled": True}))
    ms.apply_env_defaults()
    assert load_config().token == "abc"


def test_env_token_wins_over_conf(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCA_API_TOKEN", "from-env")
    monkeypatch.setenv("ORCA_CONF_PATH", _conf(tmp_path, {"remote_api_token": "abc"}))
    ms.apply_env_defaults()
    assert load_config().token == "from-env"


def test_missing_or_broken_conf_is_ignored(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCA_API_TOKEN", raising=False)
    bad = tmp_path / "OrcaSlicer.conf"
    bad.write_text("{not json")
    monkeypatch.setenv("ORCA_CONF_PATH", str(bad))
    ms.apply_env_defaults()
    monkeypatch.setenv("ORCA_CONF_PATH", str(tmp_path / "absent.conf"))
    ms.apply_env_defaults()
    with pytest.raises(RuntimeError):
        load_config()


def test_conf_with_trailing_checksum_line(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCA_API_TOKEN", raising=False)
    p = tmp_path / "OrcaSlicer.conf"
    p.write_text(json.dumps({"app": {"remote_api_token": "xyz"}}) + "\n# MD5 checksum 0123\n")
    monkeypatch.setenv("ORCA_CONF_PATH", str(p))
    ms.apply_env_defaults()
    assert load_config().token == "xyz"


class FakeHost:
    def __init__(self, running: bool):
        self.running = running
        self.launched = 0

    def is_running(self) -> bool:
        return self.running

    async def launch(self) -> None:
        self.launched += 1
        self.running = True


def _setup(monkeypatch, host):
    ms.install_autostart()  # normally done when fork_tools is imported
    monkeypatch.setenv("ORCA_API_TOKEN", "tok")
    monkeypatch.setenv("ORCA_API_URL", B)
    monkeypatch.setenv("ORCA_AUTOSTART", "1")
    monkeypatch.setattr(ms, "_host", host)
    monkeypatch.setattr(ms, "_platform", "darwin")
    monkeypatch.setattr(ms, "READY_TIMEOUT", 2.0)
    monkeypatch.setattr(ms, "POLL", 0.01)


@respx.mock
async def test_autostart_launches_when_not_running_and_retries(monkeypatch):
    host = FakeHost(running=False)
    _setup(monkeypatch, host)
    route = respx.get(f"{B}/api/v1/status")
    route.side_effect = [httpx.ConnectError("refused"), httpx.Response(200, json={"app": "OrcaSlicer"})]
    respx.get(f"{B}/api/v1/health").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with OrcaClient(load_config()) as c:
        out = await c.get_status()
    assert host.launched == 1 and out["app"] == "OrcaSlicer"


@respx.mock
async def test_no_launch_when_orca_runs_but_api_is_off(monkeypatch):
    host = FakeHost(running=True)
    _setup(monkeypatch, host)
    monkeypatch.setattr(ms, "READY_TIMEOUT", 0.05)
    respx.get(f"{B}/api/v1/status").mock(side_effect=httpx.ConnectError("refused"))
    async with OrcaClient(load_config()) as c:
        with pytest.raises(NotReachable) as e:
            await c.get_status()
    assert host.launched == 0 and "Remote API" in str(e.value)


@respx.mock
async def test_autostart_is_off_unless_enabled(monkeypatch):
    host = FakeHost(running=False)
    _setup(monkeypatch, host)
    monkeypatch.delenv("ORCA_AUTOSTART")
    respx.get(f"{B}/api/v1/status").mock(side_effect=httpx.ConnectError("refused"))
    async with OrcaClient(load_config()) as c:
        with pytest.raises(NotReachable):
            await c.get_status()
    assert host.launched == 0


@respx.mock
async def test_api_never_comes_up_after_launch(monkeypatch):
    host = FakeHost(running=False)
    _setup(monkeypatch, host)
    monkeypatch.setattr(ms, "READY_TIMEOUT", 0.05)
    respx.get(f"{B}/api/v1/status").mock(side_effect=httpx.ConnectError("refused"))
    respx.get(f"{B}/api/v1/health").mock(side_effect=httpx.ConnectError("refused"))
    async with OrcaClient(load_config()) as c:
        with pytest.raises(NotReachable) as e:
            await c.get_status()
    assert host.launched == 1 and "did not answer" in str(e.value)
