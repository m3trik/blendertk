# blendertk

**Role**: Blender 4.x+ utils (dev/test on **5.1 / Python 3.13**). Does for the tentacle
Blender slots what mayatk does for the Maya slots. **Greenfield** — built to mirror mayatk's
public API.

**Nav**: [← root](../CLAUDE.md) · **Deps**: [pythontk](../pythontk/CLAUDE.md) · **Used by**: [tentacle](../tentacle/CLAUDE.md) (`slots/blender`)
**Plan**: [tentacle/docs/BLENDER_PORT_PLAN.md](../tentacle/docs/BLENDER_PORT_PLAN.md) — read this before adding scope.

## Hard rule — session safety (protect user work)

NEVER attach to or test against a **running** Blender. Always launch a **fresh** instance.
Headless tests run via `blender --background --factory-startup --python <script>` (a new
process every time). No exceptions for speed.

## Design — mirror mayatk, don't reinvent the wheel

- **Mirror mayatk's public names** (`btk.X` ↔ `mtk.X`) at the *name + behavior* level, NOT
  signatures (mayatk = string-node idioms, bpy = object refs). This keeps the shared tentacle
  slots branch-free.
- **Relax the mirror where concepts diverge** (rigging → Armature + vertex groups, NURBS,
  shader graphs) — use Blender-idiomatic names there, don't cargo-cult a Maya concept.
- **Prefer a native `bpy.ops` / `bmesh.ops` / object property over reimplementing a mayatk
  algorithm.** Many mayatk helpers exist only because Maya's `cmds` API is low-level; Blender
  often ships the same capability as one operator. See the plan's §5 capability map.
- **Before reimplementing a helper, check if it's DCC-agnostic and belongs in `pythontk`**
  (HTML formatters, file/recent-files logic, key-timing math) — extract there, don't duplicate.
- Don't pre-create empty `*_utils` groups — implement lazily as slots demand (YAGNI).

## Imports

```python
import bpy        # primary Blender API
import bmesh      # mesh editing
```

- **Defer `import bpy` into call bodies** (not module top) so importing a module / resolving
  the package surface never needs a running Blender — matches the no-import-side-effects rule.
- Public surface is registered via root `DEFAULT_INCLUDE` + `bootstrap_package` (mirror mayatk);
  subpackage `__init__.py` = docstring only.

## Test

Headless smoke / unit tests require the Blender runtime (`bpy`):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --background --factory-startup `
  --python o:\Cloud\Code\_scripts\blendertk\test\blender_smoke_test.py
```

(Pure-logic helpers with no `bpy` dependency may also run under the workspace `.venv`.)

See [CHANGELOG.md](CHANGELOG.md) for history.
