# Scene data nodes (`DataNodes`) — Blender mirror

> Mirror of mayatk's shared scene-data-node system — concepts, channel
> registry, and the engine-side contract are owned by
> **[mayatk/docs/data_nodes.md](../../mayatk/docs/data_nodes.md)**. This page
> documents only what diverges on Blender.

`blendertk.node_utils.data_nodes.DataNodes` gives Blender tools the same two
shared carriers (`data_internal` / `data_export`) and the same string-channel
API (`set_internal_string` / `set_export_string` / `ensure_internal` /
`ensure_export`) as `mtk.DataNodes`, so a producer ports across DCCs without
renaming anything.

## What diverges from Maya

| Aspect | Maya | Blender |
|---|---|---|
| Carrier node | `data_internal` = `network` node; `data_export` = locked, hidden `transform` + zero-scale locator shape | both are plain **Empty objects** (custom properties) |
| Channel storage | dynamic string attrs → FBX **user properties** | **custom properties** (`obj["key"]`) → FBX **user properties** |
| Never-exports guarantee | a `network` node is structurally incapable of serialising into an FBX | `data_internal` is excluded **by name** by the export object-set builders (`env_utils.scene_exporter`, SmartBake) |
| Carrier visibility | `data_export` stays hidden; Maya exports hidden nodes in a selection | the carrier stays **visible/selectable** — Blender's `use_selection` export can only ship selectable objects (the `export_data_node` task clears any hide state defensively) |
| Proxied authored attrs | retired (`mirror_attr`, healed by its old producer) | never existed — no attr-proxy concept in `bpy` |

## Getting it into the FBX

Two exporter options make the hand-off work, both defaults in the Scene
Exporter's `_DEFAULT_FBX_OPTIONS` (and its shipped `default` preset):

- `use_custom_props=True` — Blender's FBX exporter drops custom properties
  unless asked (`bpy.ops.export_scene.fbx` defaults it off).
- `object_types` including `"EMPTY"` — the bridge-oriented `FbxUtils` defaults
  are mesh-only; without the override the carrier Empty itself is filtered out
  of the export.

The Scene Exporter's default-on **"Export Scene Data Node"** task
(`export_data_node`) then folds the carrier into the export set in every
export mode, exactly like mayatk's. Unlike mayatk there is **no before-export
refresh hook** (`bpy.app.handlers` has no FBX-export event), so producers
publish at authoring time — e.g. the Lightmap Baker writes `lightmap_metadata`
when a bake commits — and the carrier is already current at export.

The round-trip (publish → export → re-import → property intact) is pinned by
`test/test_scene_exporter.py`.

## Channels in use (Blender side)

| Channel | Producer | Notes |
|---|---|---|
| `lightmap_metadata` (on `data_export`) | Lightmap Baker | same JSON schema as mayatk's — one `LightmapMetadataController` reader serves both DCCs |
| `smart_bake_sessions` (on `data_internal`) | SmartBake `BakeSessionStore` | restore manifests; never exported |

Shots (`fbx_takes` / `shot_metadata`) and Audio (`audio_manifest`) are not yet
ported — the Scene Exporter's "Export Shots as Animation Takes" checkbox stays
a disabled placeholder until the Shots port lands.
