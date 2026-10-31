# blendertk

**Role**: Blender 4.x+ utils (dev/test on **5.1 / Python 3.13**): the Blender host library and
each tool's Blender API half, as mayatk is Maya's; tentacle's shared panels call it through
`self.tk`. Built to mirror mayatk's public API and released in the cascade beside it.

**Nav**: [← root](../CLAUDE.md) · [docs](docs/README.md) · **Deps**: [pythontk](../pythontk/CLAUDE.md) · [uitk](../uitk/CLAUDE.md) · **Mirrors**: [mayatk](../mayatk/CLAUDE.md) · **Used by**: [tentacle](../tentacle/CLAUDE.md) (`self.tk` in Blender)
**Parity**: [tentacle/docs/PARITY_AUDIT.md](../tentacle/docs/PARITY_AUDIT.md) (measured gap, auto-gen) · [tentacle/docs/PARITY_PORTING_PLAN.md](../tentacle/docs/PARITY_PORTING_PLAN.md) (port-this-next recipes) — read these before adding scope.

> **Parity gate — do NOT eyeball.** tentacle's shared Slots classes call this library through `self.tk`, so every chain they call must resolve here as it does in mayatk (same name, a signature that takes the call), or be hidden (`requires`, `slots.tools.HOSTS`) or guarded (`hasattr` / `getattr`) where it does not. [tentacle/docs/PARITY_SURFACE.md](../tentacle/docs/PARITY_SURFACE.md) lists every gap and every one-host operation (regenerate: `python m3trik/scripts/compare_panel_surface.py --all --write`; one slots module: `--panel <name>`); **a gap fails the gate (exit 1)**. A rename or a signature change here is checked against every caller the same way.

## API surface

[`API_INDEX.md`](API_INDEX.md) · [`API_REGISTRY.md`](API_REGISTRY.md) · [`API_CHANGES.md`](API_CHANGES.md) · shadows [`API_SHADOWS.md`](../m3trik/docs/API_SHADOWS.md) — registry rules in [root](../CLAUDE.md). Upstream: [pythontk](../pythontk/API_INDEX.md) · [uitk](../uitk/API_INDEX.md) · mirror target [mayatk](../mayatk/API_INDEX.md).

## Hard rule — session safety (protect user work)

NEVER attach to or test against a **running** Blender. Always launch a **fresh** instance.
Headless tests run via `blender --background --factory-startup --python <script>` (a new
process every time). No exceptions for speed.

## Design — mirror mayatk, don't reinvent the wheel

> **Structure map (SSoT):** [`docs/STRUCTURE.md`](docs/STRUCTURE.md) — the subpackage/class/module
> correspondence with mayatk + which subpackages are intentionally absent. Consult it (and update
> it) whenever you add/move/rename, so a change in one package mirrors mechanically to the other.

- **Mirror mayatk's public names** (`btk.X` ↔ `mtk.X`) at the *name + behavior* level, and the
  signature too where a shared tentacle panel calls the operation (otherwise mayatk = string-node
  idioms, bpy = object refs). This keeps the shared tentacle slots branch-free.
- **Relax the mirror where concepts diverge** (rigging → Armature + vertex groups, NURBS,
  shader graphs) — use Blender-idiomatic names there, don't cargo-cult a Maya concept.
- **Prefer a native `bpy.ops` / `bmesh.ops` / object property over reimplementing a mayatk
  algorithm.** Many mayatk helpers exist only because Maya's `cmds` API is low-level; Blender
  often ships the same capability as one operator.
- **Before reimplementing a helper, check if it's DCC-agnostic and belongs in `pythontk`**
  (HTML formatters, file/recent-files logic, key-timing math) — extract there, don't duplicate.
- Don't pre-create empty `*_utils` groups — implement lazily as slots demand (YAGNI).

## Tool panels — tentacle's

As in mayatk ([mayatk/CLAUDE.md](../mayatk/CLAUDE.md) owns the rule): every tool's panel is tentacle's,
and blendertk holds the host operations, no UI. Blender specifics:

- **Defer the Qt-only `uitk` imports** into the method that uses them — headless Blender
  (`--background`) ships no Qt binding, so the engine surface must import Qt-free (the same reason
  `import bpy` is deferred).
- `BlenderUiHandler` (`ui_utils/blender_ui_handler.py`, mirror of `MayaUiHandler`) registers no UI
  source of its own; it wraps Blender's native menus, wired into `tcl_blender` via `handlers={"ui":
  BlenderUiHandler}`. Verify with `test/test_blender_ui_handler.py` (needs Qt, not bpy → runs under
  the workspace `.venv`; skips→PASS under the Blender harness).

## Imports

- **Defer `import bpy` into call bodies** (not module top) so importing a module / resolving
  the package surface never needs a running Blender — matches the no-import-side-effects rule.
- Public surface is registered via root `DEFAULT_INCLUDE` + `bootstrap_package` (mirror mayatk);
  subpackage `__init__.py` = docstring, plus one `lazy_exports` call when it publishes names.

## Test

Headless smoke / unit tests require the Blender runtime (`bpy`):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup `
  --python o:\Cloud\Code\_scripts\blendertk\test\blender_smoke_test.py
```

Pointed: `python blendertk/test/run_tests.py test_smart_bake` (a typo exits 2); no names = all
suites, for review / release. Fresh instance per suite (Qt suites offscreen), aggregates
`===RESULT===` + `OK`/`FAIL` counts, badges full runs. `Run-Tests.ps1` wraps it. What a change
reaches: `python m3trik/scripts/affected_tests.py <files> --run`.
(Pure-logic helpers with no `bpy` dep may also run under the workspace `.venv`.)

See [CHANGELOG.md](CHANGELOG.md) for history.
