# blendertk

Blender utilities backing the [tentacle](https://github.com/m3trik/tentacle) Blender slots —
the Blender counterpart to [mayatk](https://github.com/m3trik/mayatk).

`blendertk` mirrors mayatk's public API surface (`btk.X` ↔ `mtk.X`) so the shared tentacle
slot/UI layer stays branch-free across DCCs. It is a thin adapter over Blender's native
`bpy` / `bmesh` operators plus a small set of genuine helpers — not a reimplementation of
mayatk.

## Status

Greenfield scaffold. See the full plan in
[`tentacle/docs/BLENDER_PORT_PLAN.md`](https://github.com/m3trik/tentacle).

## Requirements

- Blender 4.x+ (developed against 5.1 / Python 3.13)
- [`pythontk`](https://github.com/m3trik/pythontk)

`bpy` is provided by the Blender runtime.
