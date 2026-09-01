[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/blendertk.svg)](https://pypi.org/project/blendertk/)
[![Blender](https://img.shields.io/badge/Blender-4.x+-orange.svg)](https://www.blender.org/)
[![Tests](https://img.shields.io/badge/Tests-4586%20passed-brightgreen.svg)](../test/)

# blendertk

<!-- short_description_start -->
*Blender 4.x+ tech-art toolkit mirroring [mayatk](https://github.com/m3trik/mayatk)'s public API (`btk.X` ↔ `mtk.X`) — modeling, animation, materials, rigging, and scene-pipeline automation over native `bpy`/`bmesh`, plus tool panels and one-click bridges into Marmoset, Substance Painter, RizomUV, Maya, Unity, and the browser.*
<!-- short_description_end -->

blendertk is the Blender layer of the `pythontk → uitk → {mayatk, blendertk} → tentacle` ecosystem. It mirrors mayatk at the **name + behavior** level — not signatures, not line counts — so the shared [tentacle](https://github.com/m3trik/tentacle) slot/UI layer stays branch-free across DCCs. Where Blender ships the capability natively, blendertk stays a thin adapter over `bpy`/`bmesh` operators; where it doesn't, the DCC-agnostic core lives upstream in [pythontk](https://github.com/m3trik/pythontk) and both DCC layers adapt it.

## Installation

```bash
pip install blendertk
```

**Requirements:** Blender 4.x+ (developed against 5.1 / Python 3.13) · [`pythontk`](https://pypi.org/project/pythontk/). `bpy` is provided by the Blender runtime — importing blendertk and resolving its surface never needs a running Blender (all `bpy` imports are deferred into call bodies).

## Packages

Same subpackage names, main-module filenames, and namespace classes as mayatk — the full correspondence map (including what is deliberately absent on each side, and why) is [`STRUCTURE.md`](STRUCTURE.md).

| Package | What it covers |
|---|---|
| `anim_utils` | Shots suite (store, manifest, sequencer panels over the shared pythontk shots engine), smart bake, blendshape animation, key scaling/staggering |
| `audio_utils` | Sound-strip CRUD over the Video Sequence Editor (deliberately not a mirror of Maya's DG-node audio machinery) |
| `cam_utils` | Camera utilities |
| `core_utils` | `CoreUtils`, `AutoInstancer`, `Preview`, script-job manager over `bpy.app.handlers`, diagnostics |
| `display_utils` | Color ID, exploded view, Blender-only `OutlinerTint` |
| `edit_utils` | `EditUtils`, mirror, cut-on-axis, duplicate (linear/radial/grid), bevel, bridge, snap, naming, curtain rig, target weld |
| `env_utils` | `BlenderConnection`, workspaces (`workspace.mel` shared with Maya), reference manager, hierarchy sync, FBX/USD, scene exporter, Maya + Unity bridges, WebXR preview, docked script console |
| `light_utils` | Lightmap baker + web export, HDR manager, lights-from-geometry |
| `mat_utils` | Game shader, material updater, texture path editor, shader templates, render opacity, image-to-plane, emissive groups, Marmoset + Substance live-RPC bridges |
| `node_utils` | `NodeUtils`, `DataNodes` ([shared scene data carriers](data_nodes.md)), Channels tool |
| `nurbs_utils` | Curve helpers (relaxed mirror), `ImageTracer`, curve-to-tube |
| `rig_utils` | Constraints/drivers/armature primitives; tube / wheel / telescope / shadow rigs |
| `ui_utils` | `BlenderUiHandler`, native-menu wrapper, Qt dock container, calculator, style setter |
| `uv_utils` | UV utilities, Rizom bridge |
| `xform_utils` | Transforms and matrix IO |

Classes — and, for the wildcard-scanned `*_utils` roots, their public methods — are exposed at the package root via the lazy-loading resolver:

```python
import blendertk as btk

btk.selected_objects()                      # bare form — CoreUtils.selected_objects, wildcard-exposed
btk.EditUtils.mirror(...)                   # class-qualified — mirrors mtk.EditUtils.mirror
btk.AutoInstancer(tolerance=0.001).run()    # same call in mayatk: mtk.AutoInstancer(...).run()
```

Full public surface (auto-generated): [`API_REGISTRY.md`](../API_REGISTRY.md); compact index: [`API_INDEX.md`](../API_INDEX.md).

## Highlights

- **Maya bridge, both directions** — `MayaBridge.send()` exports the selection and launches an interactive Maya on it; `save_as()` runs mayapy headlessly and returns a written `.ma`/`.mb`; `MayaSceneImport` pulls a Maya scene *in* via a blocking headless FBX round-trip, translating FBX-hostile shaders and rebuilding packed PBR maps from a manifest sidecar. Mirrored by mayatk's `BlenderBridge`, so each DCC reads and writes the other's native format off one shared export pipeline.
- **Lightmap pipeline** — `LightmapBaker` commits bake state (markers + manifest) that every exporter reads on its own: Unity via the FBX carrier, GLB via pythontk's lightmap applier, and `LightmapWebExport` wraps Blender's own glTF exporter for the [live WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md) — a clean no-op on unbaked scenes. mayatk's Blender bridge drives this baker headlessly for the Maya round trip.
- **Drive Blender from outside** — `BlenderConnection` mirrors mayatk's `MayaConnection`: fresh `--background` instances only, the backbone of the test suite. An artist's open session is never touched.
- **Hierarchy sync** — diff a scene against a reference `.blend` (or `.fbx`, staged to a temp `.blend`) linked as a library, then repair drift: stub missing objects, quarantine extras, fix fuzzy renames and reparents; `ObjectSwapper` pulls matched reference objects in fresh.
- **Shared workspaces** — one project folder serves Maya natively and Blender via blendertk: the `workspace.mel` codec and template store live in pythontk, and the co-located Workspace Editor panel mirrors Maya's Project Window.
- **Tool panels** — every engine that warrants one ships a co-located uitk panel (`<tool>.py` + `<tool>.ui`), discovered by `BlenderUiHandler` — the same split mayatk uses, so tentacle's navigation stays a thin layer.

## Parity — how the mirror stays honest

Parity is measured, not eyeballed: `compare_panel_surface.py` regenerates a per-element matrix ([tentacle/docs/PARITY_SURFACE.md](https://github.com/m3trik/tentacle/blob/main/docs/PARITY_SURFACE.md)), every conscious divergence is ledgered with a reason in tentacle's `parity_map.py`, and untriaged deltas fail the sweep. The coarse gap and port-this-next recipes live in [PARITY_AUDIT.md](https://github.com/m3trik/tentacle/blob/main/docs/PARITY_AUDIT.md) and [PARITY_PORTING_PLAN.md](https://github.com/m3trik/tentacle/blob/main/docs/PARITY_PORTING_PLAN.md).

## Session safety

Nothing in blendertk attaches to a running Blender. Tests and headless tooling always launch a **fresh** `blender --background --factory-startup` process; `BlenderConnection` does the same. See [CLAUDE.md](../CLAUDE.md) for the full rule.

## Guides

- **[Scene data nodes](data_nodes.md)** — the `data_internal` / `data_export` carriers and how their metadata rides into the FBX (Blender delta of [mayatk's owner doc](https://github.com/m3trik/mayatk/blob/main/docs/data_nodes.md))
- **[Emissive groups → Unity](https://github.com/m3trik/mayatk/blob/main/docs/emissive_groups.md)** (mayatk, cross-package SSoT) — runtime-toggleable emissive regions; blendertk's `EmissiveGroups` stores membership as per-group boolean face attributes
- **[Live WebXR preview](https://github.com/m3trik/pythontk/blob/main/docs/webxr_preview.md)** (pythontk) — the shared DCC → glTF → headset pipeline behind `btk.WebXrPreview`

## Links

- **Full API:** [`API_REGISTRY.md`](../API_REGISTRY.md) · [`API_CHANGES.md`](../API_CHANGES.md)
- **Structure / parity map:** [`STRUCTURE.md`](STRUCTURE.md)
- **Test suite:** [`test/README.md`](../test/README.md) — suite layout, sentinel conventions, fresh-instance runner
- **Changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
- **Contributor / AI-agent guide:** [`CLAUDE.md`](../CLAUDE.md)
- **PyPI:** https://pypi.org/project/blendertk/
- **Issues:** https://github.com/m3trik/blendertk/issues

## License

MIT — see [LICENSE](../LICENSE).
