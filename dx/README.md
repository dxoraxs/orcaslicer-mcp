# dxoraxs fork of orcaslicer-mcp

Upstream: https://github.com/MaxEllis/orcaslicer-mcp. `main` here is upstream's newest
release plus the changes below.

## What the fork adds

- `save_project(path=None, overwrite=False)`: saves the open OrcaSlicer project as a .3mf,
  with no dialog and without taking focus. Needs the dxoraxs OrcaSlicer build
  (https://github.com/dxoraxs/OrcaSlicer/releases), whose Remote API has
  `POST /api/v1/project/save`. On a stock build the tool answers `unsupported_build`.
- `fork_info()`: the running version (`<upstream>+dx`) and the fork tools.

Fork code lives in `src/orcaslicer_mcp/fork_tools.py`; `server.py` only gains one import
line, so upstream merges rarely conflict.

## Updates

- `dx-sync-upstream` (daily): merges the newest upstream release tag, runs the tests, pushes,
  and starts `dx-release`. A conflict, red tests or a refused push opens an issue; then run
  `dx/sync-upstream.sh` locally.
- `dx-release` (every push to main): tests, then a GitHub release `v<upstream>-dx.<n>`.

## Running it

```bash
uvx --from git+https://github.com/dxoraxs/orcaslicer-mcp orcaslicer-mcp
```
