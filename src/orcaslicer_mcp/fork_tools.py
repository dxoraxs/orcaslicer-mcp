"""Tools that exist only in the dxoraxs fork. Kept out of server.py so upstream merges touch
a single import line there. Imported by server.py after _TOOL_ANNOTATIONS is defined.

save_project needs the dxoraxs OrcaSlicer build (github.com/dxoraxs/OrcaSlicer), whose
Remote API adds POST /api/v1/project/save; stock MCP builds answer 404 for it."""
from __future__ import annotations

import os
from typing import Annotated

from pydantic import Field

from .errors import ApiError, Conflict, NotFound, Validation, BadRequest
from .server import mcp, _client, _err, _TOOL_ANNOTATIONS, _VERSION

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


@mcp.tool()
async def fork_info() -> dict:
    """Which build of this MCP server is running: the upstream version it is based on, the
    fork, and the fork-only tools it adds."""
    return {"version": f"{_VERSION}+dx", "upstream_version": _VERSION, "fork": FORK,
            "repo": FORK_REPO, "orca_build": ORCA_FORK_RELEASES,
            "fork_tools": ["save_project", "fork_info"]}


_TOOL_ANNOTATIONS.update({
    "save_project": ("Save project (.3mf) to a path", False, False),
    "fork_info": ("Fork build info", True, False),
})
