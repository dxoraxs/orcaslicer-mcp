# dxoraxs fork of orcaslicer-mcp

Upstream: https://github.com/MaxEllis/orcaslicer-mcp. `main` here is upstream's newest
release plus the changes below.

## What the fork adds

- `save_project(path=None, overwrite=False)`: saves the open OrcaSlicer project as a .3mf,
  with no dialog and without taking focus. Needs the dxoraxs OrcaSlicer build
  (https://github.com/dxoraxs/OrcaSlicer/releases), whose Remote API has
  `POST /api/v1/project/save`. On a stock build the tool answers `unsupported_build`.
- `open_project(path, discard=False)`, `new_project(discard=False)`: open a .3mf as a project
  or start an empty one, without dialogs; unsaved changes are refused unless `discard=true`.
- `list_plates`, `add_plate(name)`, `select_plate(index)`, `delete_plate(index)` (empty plates
  only), `move_object_to_plate(object_id, index)`. Slicing works on the current plate.
- All of the above need the dxoraxs OrcaSlicer build (rolling release `orca-dx`); a stock build
  answers `unsupported_build`.
- `send_to_printer(start=False, filename=None, skip_check=False)`: saves the last slice like
  `save_gcode` (recording it in the outcome store), runs `PRINT_GCODE_CHECK` if set, and uploads
  it to Moonraker, optionally starting the print. Refuses when Klipper is not ready, and
  refuses to start while a print runs or is paused.
- `printer_status()`: klippy state, print state, file, progress, temperatures.
- `sync_print_outcomes()`: pulls finished jobs from Moonraker history into the outcome store,
  joined to their slices, so `recall_prints` shows real results; `set_print_verdict(verdict)`
  attaches the user's words to the last print.
- `fork_info()`: the running version (`<upstream>+dx`) and the fork tools.
- No token in the MCP config needed: without `ORCA_API_TOKEN` the token is read from
  OrcaSlicer.conf (`ORCA_CONF_PATH` overrides the location).
- `ORCA_AUTOSTART=1` (macOS): when OrcaSlicer is not running, the first request starts it in
  the background (`open -g`, no focus change) and waits up to 90 s for the API.

### Environment

| Variable | Meaning |
|----------|---------|
| `MOONRAKER_URL` | e.g. `http://192.168.100.10`; Moonraker must trust this host |
| `PRINTER_ID` | id stored with outcomes (default `biqu`) |
| `PRINT_GCODE_CHECK` | command run as `<cmd> <gcode path>`; non-zero exit blocks the upload, stdout lines are the problems |
| `PRINT_OUTCOMES_DIR` | where G-code copies and `outcomes.db` live |
| `ORCA_AUTOSTART` | `1` to launch OrcaSlicer on demand (macOS) |

Fork code lives in `src/orcaslicer_mcp/fork_tools.py`, `moonraker.py` and `mac_support.py`;
`server.py` only gains one import line, so upstream merges rarely conflict.

## Updates

- `dx-sync-upstream` (daily): merges the newest upstream release tag, runs the tests, pushes,
  and starts `dx-release`. A conflict, red tests or a refused push opens an issue; then run
  `dx/sync-upstream.sh` locally.
- `dx-release` (every push to main): tests, then a GitHub release `v<upstream>-dx.<n>`.

## Running it

```bash
uvx --from git+https://github.com/dxoraxs/orcaslicer-mcp orcaslicer-mcp
```
