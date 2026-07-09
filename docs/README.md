# blendertk

Blender 4.x+ utilities backing the [tentacle](https://github.com/m3trik/tentacle) Blender slots — the Blender counterpart to [mayatk](https://github.com/m3trik/mayatk).

`blendertk` mirrors mayatk's public API surface (`btk.X` ↔ `mtk.X`) at the *name + behavior* level, so the shared tentacle slot/UI layer stays branch-free across DCCs. It is a thin adapter over Blender's native `bpy` / `bmesh` operators plus a small set of genuine helpers — not a reimplementation of mayatk.

## Status

Active mayatk-parity port. The structure map (subpackage/class correspondence with mayatk, including what's intentionally absent) is [`STRUCTURE.md`](STRUCTURE.md); the measured gap and port-this-next recipes live in the tentacle repo (`docs/PARITY_AUDIT.md`, `docs/PARITY_PORTING_PLAN.md`, `docs/PARITY_SURFACE.md`).

## Requirements

- Blender 4.x+ (developed against 5.1 / Python 3.13)
- [`pythontk`](https://github.com/m3trik/pythontk)

`bpy` is provided by the Blender runtime.

## More

- [`data_nodes.md`](data_nodes.md) — shared scene-data carriers (`data_internal` / `data_export`) and how their metadata rides into the FBX (mirror of mayatk's system)
- [`CHANGELOG.md`](../CHANGELOG.md) — notable changes
- [`CLAUDE.md`](../CLAUDE.md) — contributor / agent conventions, test harness, hard rules
