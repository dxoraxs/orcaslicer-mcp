# dxoraxs fork of orcaslicer-mcp

Upstream: https://github.com/MaxEllis/orcaslicer-mcp. `main` here is upstream's newest
release plus the changes below.

## What the fork adds

- Projects and plates (`save_project`, `open_project`, `new_project`, `list_plates`, ...) were
  fork tools until upstream shipped its own (0.1.15 to 0.1.18); since the 0.1.21 merge they
  are upstream's, with upstream's arguments (`if_unsaved` instead of `discard`).
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
| `PRINTER_ID` | id stored with outcomes (default `biqu`); also becomes upstream's `ORCA_PRINTER_ID` when that is unset |
| `PRINT_GCODE_CHECK` | command run as `<cmd> <gcode path>`; non-zero exit blocks the upload, stdout lines are the problems |
| `PRINT_OUTCOMES_DIR` | where G-code copies and `outcomes.db` live |
| `ORCA_AUTOSTART` | `1` to launch OrcaSlicer on demand (macOS) |

Fork code lives in `src/orcaslicer_mcp/fork_tools.py`, `moonraker.py` and `mac_support.py`;
`server.py` is upstream's file unchanged: the console script in `pyproject.toml` points at
`fork_tools:main`, which registers the fork tools and then runs upstream's `main`.

## Updates

- `dx-sync-upstream` (daily): merges the newest upstream release tag, runs the tests, pushes,
  and starts `dx-release`. A conflict, red tests or a refused push opens an issue; then run
  `dx/sync-upstream.sh` locally.
- `dx-release` (every push to main): tests, then a GitHub release `v<upstream>-dx.<n>`.

## Running it

```bash
uvx --from git+https://github.com/dxoraxs/orcaslicer-mcp orcaslicer-mcp
```
