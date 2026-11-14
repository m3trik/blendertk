# blendertk

<!-- packs (generated: edit the packs, then sync) -->
Shared rules (packs; Claude Code loads them, other agents read them first): [principles](.agents/packs/principles.md) · [python](.agents/packs/python.md)
<!-- /packs -->

**Role**: Blender 4.x+ utils (dev/test on **5.1 / Python 3.13**): the Blender host library and
each tool's Blender API half, as mayatk is Maya's; tentacle's shared panels call it through
`self.tk`.

**Nav**: [← root](../AGENTS.md) · [docs](docs/README.md) · **Deps**: [pythontk](../pythontk/AGENTS.md) · [uitk](../uitk/AGENTS.md) · **Mirrors**: [mayatk](../mayatk/AGENTS.md) · **Used by**: [tentacle](../tentacle/AGENTS.md) (`self.tk` in Blender)
**Parity**: [tentacle/docs/PARITY_AUDIT.md](../tentacle/docs/PARITY_AUDIT.md) (measured gap, auto-gen) · [tentacle/docs/PARITY_PORTING_PLAN.md](../tentacle/docs/PARITY_PORTING_PLAN.md) (port-this-next recipes) — read these before adding scope.

> **Parity gate — do NOT eyeball.** tentacle's shared Slots classes call this library through `self.tk`, so every chain they call must resolve here as it does in mayatk (same name, a signature that takes the call), or be hidden (`requires`, `slots.tools.HOSTS`) or guarded (`hasattr` / `getattr`) where it does not. [tentacle/docs/PARITY_SURFACE.md](../tentacle/docs/PARITY_SURFACE.md) lists every gap and every one-host operation (regenerate: `python m3trik/scripts/compare_panel_surface.py --all --write`; one slots module: `--panel <name>`); **a gap fails the gate (exit 1)**. A rename or a signature change here is checked against every caller the same way.

## API surface

Searched live (`api.search`; MCP `api_search`), never a committed file — rules in [root](../AGENTS.md). Upstream: [pythontk](../pythontk/AGENTS.md) · [uitk](../uitk/AGENTS.md) · mirror target [mayatk](../mayatk/AGENTS.md).

## Hard rule — session safety (protect user work)

NEVER attach to or test against a Blender a person opened, not even for speed. Headless tests
run via `blender --background --factory-startup --python <script>`, a new process each, or with
`--reuse` in a background Blender this tooling launched and keeps between runs (the principles rule).

## Design — mirror mayatk, don't reinvent the wheel

> **Structure map (SSoT):** [`docs/STRUCTURE.md`](docs/STRUCTURE.md) — the subpackage/class/module
> map to mayatk, deliberate absences included; update it with every add, move or rename.

- **Mirror mayatk's public names** (`btk.X` ↔ `mtk.X`) at the *name + behavior* level, and the
  signature too where a shared tentacle panel calls the operation (otherwise mayatk = string-node
  idioms, bpy = object refs). This keeps the shared tentacle slots branch-free. **mayatk is the
  reference**: where the twins differ, blendertk adopts mayatk's name or shape (a deprecation alias
  for a public name it drops), and a parity request never edits mayatk's side.
- **Relax the mirror where concepts diverge** (rigging → Armature + vertex groups, NURBS,
  shader graphs) — use Blender-idiomatic names there, don't cargo-cult a Maya concept.
- **Prefer a native `bpy.ops` / `bmesh.ops` / object property over reimplementing a mayatk
  algorithm.** Many mayatk helpers exist only because Maya's `cmds` API is low-level; Blender
  often ships the same capability as one operator.
- **A DCC-agnostic helper belongs in `pythontk`** (HTML formatters, recent files, key-timing
  math): extract it there.
- Don't pre-create empty `*_utils` groups — implement lazily as slots demand (YAGNI).

## Tool panels — tentacle's

As in mayatk ([mayatk/AGENTS.md](../mayatk/AGENTS.md) owns the rule): every tool's panel is tentacle's,
and blendertk holds the host operations, no UI. Blender specifics:

- **Defer the Qt-only `uitk` imports** into the method that uses them — headless Blender
  (`--background`) ships no Qt binding, so the engine surface must import Qt-free (the same reason
  `import bpy` is deferred).
- `BlenderUiHandler` (`ui_utils/blender_ui_handler.py`, mirror of `MayaUiHandler`) registers no UI
  source of its own; it wraps Blender's native menus, and `BlenderMarkingMenuHost.ui_handler` names
  it to the marking menu. Verify with `tests/test_blender_ui_handler.py` (needs Qt, not bpy → runs under
  the workspace `.venv`; skips→PASS under the Blender harness).

- **Hosting a marking menu**: `BlenderMarkingMenuHost` (`ui_utils/marking_menu_host/`, a
  `uitk.MarkingMenuHost` and `uitk.MarkingMenuLaunchHost`) over `QtBootstrap`/`QtRuntime`
  (`ui_utils/qt_runtime.py`: Qt provisioned, a QApplication pumped from `bpy.app.timers`),
  `KeymapBridge` (the 3D View keymap carries the activation key and drives the menu through its
  public `activate`/`release`/`armed`; its operator's `tentacle.show_marking_menu` idname is a
  saved name) and `BlenderWindow`. Name no product: the hosted application names itself
  (`HostedApp`, its env vars, folder and messages). Verify the bridge by calling its operator and
  poller in a fresh `blender --background`, never with synthesized keystrokes.

## Imports

- **Defer `import bpy` into call bodies**, so importing never needs a running Blender.
- Public surface is registered via root `DEFAULT_INCLUDE` + `bootstrap_package` (mirror mayatk);
  subpackage `__init__.py` = docstring, plus one `lazy_exports` call when it publishes names.

## Test

Headless smoke / unit tests require the Blender runtime (`bpy`):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup `
  --python blendertk\tests\blender_smoke_test.py
```

Pointed: `python blendertk/tests/run_tests.py test_smart_bake` (a typo exits 2); no names = all
suites, for review / release. Fresh instance per suite (Qt suites offscreen); `Run-Tests.ps1`
wraps it.
(Pure-logic helpers with no `bpy` dep may also run under the workspace `.venv`.)

See [CHANGELOG.md](CHANGELOG.md) for history.
