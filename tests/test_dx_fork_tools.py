"""Fork-only (dxoraxs): fork_info and the registration of the fork tools on upstream's server."""
import orcaslicer_mcp.server as srv
from orcaslicer_mcp import fork_tools as ft

NAMES = ["send_to_printer", "printer_status", "sync_print_outcomes", "set_print_verdict", "create_calibration",
         "fork_info"]


async def test_fork_info():
    out = await ft.fork_info()
    assert out["fork"] == "dxoraxs" and out["version"].endswith("+dx")
    assert out["fork_tools"] == NAMES


def test_register_adds_annotated_tools_once_and_leaves_upstream_alone():
    tools = srv.mcp._tool_manager._tools
    before = set(tools)
    assert not before & set(NAMES)  # importing fork_tools alone registers nothing
    try:
        ft.register()
        ft.register()
        assert set(tools) == before | set(NAMES)
        assert tools["send_to_printer"].annotations.read_only_hint is False
        assert tools["fork_info"].annotations.read_only_hint is True
    finally:  # upstream's tests count the tools of this shared server
        for n in NAMES:
            tools.pop(n, None)
            srv._TOOL_ANNOTATIONS.pop(n, None)
