# Scene data nodes (`DataNodes`) — Blender mirror

> Mirror of mayatk's shared scene-data-node system — concepts, channel
> registry, and the engine-side contract are owned by
> **[mayatk/docs/data_nodes.md](https://github.com/m3trik/mayatk/blob/main/docs/data_nodes.md)**.
> This page documents only what diverges on Blender.

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
| Never-exports guarantee | a `network` node is structurally incapable of serialising into an FBX | excluded **by name** where a builder could sweep it in — the Scene Exporter's *selected* branch, SmartBake's scope filter, and the hand-off bridges' hierarchy closure (`handoff_export`, covering whole-scene sends); *all* and *visible* exclude it by type filter (geometry-only collection) |
| Shots app state | `shot_store` channel on `data_internal` | `scene["shot_store"]` — a scene ID property, not the carrier (predates the mayatk consolidation; folds in when the shots port lands) |
| Duplicate carrier | duplicate *short names* possible at different DAG levels — every accessor resolves to the shallowest path | object names are globally unique (`bpy.data.objects`); an FBX re-import lands as `data_export.001`, which the API deliberately ignores. An **unlinked** carrier (collection deleted) is relinked into the scene collection on the next write, so a producer can never publish to an object the exporter won't ship |
| Carrier visibility | `data_export` stays hidden; Maya exports hidden nodes in a selection | the carrier stays **visible/selectable** — Blender's `use_selection` export can only ship selectable objects (the `export_data_node` task clears any hide state defensively) |
| Outliner row | `data_export` is flagged `hiddenInOutliner` (transform + shape) — the Outliner never lists it | **no equivalent** — Blender's Outliner lists every object in the view layer and offers no per-object hide-from-Outliner flag; the only ways to drop the row (unlink from all collections, exclude the collection) also drop the object from the FBX export set, so the carrier stays listed |
| Proxied authored attrs | retired (`mirror_attr`, healed by its old producer) | never existed — no attr-proxy concept in `bpy` |

## Getting it into the FBX

Two exporter options make the hand-off work, both on in the Scene Exporter's
engine baseline `_DEFAULT_FBX_OPTIONS` (shipped as the `game_asset` preset, and
what an export with no preset selected uses). The shipped `default` preset is
Blender's own `export_scene.fbx` defaults, which have **neither** — the repair
below is what keeps the carrier readable under it:

- `use_custom_props=True` — Blender's FBX exporter drops custom properties
  unless asked (`bpy.ops.export_scene.fbx` defaults it off).
- `object_types` including `"EMPTY"` — anything the set leaves out is dropped from
  the FBX (and its children re-rooted), so without `"EMPTY"` the carrier Empty itself
  is filtered out of the export. Pinned here rather than inherited from the
  bridge-oriented `FbxUtils` defaults: the carrier is a hard requirement of this task.

**No preset can silently defeat either option** — not a user's, and not the
stock-defaults `default`: the write site (`_force_carrier_readability`) repairs
both — with a warning — whenever the carrier is in the export set, the same
"shipping the carrier and shipping what makes it readable are one decision" rule
the bridges enforce. The settings report is emitted *after* that repair, so it
discloses the values actually written. Pinned by `test/test_scene_exporter.py`.

The Scene Exporter's default-on **"Export Scene Data Node"** task
(`export_data_node`) first refreshes every known producer's channel from live
scene state (`btk.FbxUtils.run_export_preparers`, the mirror of mayatk's
producer registry — a mesh deleted since the last bake can't ship a stale
manifest), then folds the carrier into the export set in every export mode,
exactly like mayatk's. Unlike mayatk there is **no before-export session
hook** (`bpy.app.handlers` has no FBX-export event), so producers also
publish at authoring time — e.g. the Lightmap Baker writes
`lightmap_metadata` when a bake commits — which is what any
non-Scene-Exporter FBX export ships.

The round-trip (publish → export → re-import → property intact) is pinned by
`test/test_scene_exporter.py`.

**Hand-off bridges** get there a third way: `BlenderExportMixin` exposes
`include_data_export`, and a bridge whose *consumer* parses these channels turns
it on — `WebXrPreview` (its GLB conversion binds `lightmap_metadata` via
`ptk.MeshConvert.apply_glb_lightmaps`) and `UnityBridge` (its FBX lands in
`Assets/`, where unitytk reads it). The flag also **forces** the two exporter
options above at the point the carrier is appended, rather than declaring them in
the overridable `_fbx_options`: a bridge that overrides that method wholesale
(Substance / Marmoset do) must not be able to ship a carrier holding nothing.
Off by default — to a bridge that only wants geometry the carrier is a stray
Empty in the target's outliner — and never *created* just to ship. Pinned by
`test/test_fbx_utils.py`; mirror of mayatk's flag of the same name.

## Channels in use (Blender side)

| Channel | Producer | Notes |
|---|---|---|
| `fbx_takes` (on `data_export`) | Shots — `BlenderShotStore.publish_export_view` | same `[{name,start,end}]` schema as mayatk's; the Scene Exporter's **Export Shots as Animation Takes** task arms `FbxUtils` from it, and the write splits its baked scene-range AnimStack into one windowed stack per take (see `fbx_utils.py`'s module docstring for why Blender's own multi-stack export modes can't be used) |
| `shot_metadata` (on `data_export`) | Shots — `BlenderShotStore.publish_export_view` | same envelope as mayatk's; clip name = join key ([shot_export_unity.md](https://github.com/m3trik/mayatk/blob/main/docs/shot_export_unity.md)) |
| `lightmap_metadata` (on `data_export`) | Lightmap Baker — `refresh_export_metadata` | same JSON schema as mayatk's — one `LightmapMetadataController` reader serves both DCCs |
| `shadow_metadata` (on `data_export`) | Shadow Rig — `refresh_export_metadata` | same schema as mayatk's — one `ShadowPlaneController` reader |
| `emissive_groups` (on **both** carriers) | Emissive Groups — `refresh_export_metadata` | registry (authored state) on `data_internal`; manifest on `data_export`, plus per-group keyable `emissiveGroup_<name>` floats whose curves ship via transient scale-proxy Empties (Blender's FBX exporter can't animate custom properties) |
| `smart_bake_sessions` (on `data_internal`) | SmartBake `BakeSessionStore` | restore manifests; never exported |

Audio (`audio_manifest`) is not yet producing — the audio panel is VSE-only.
When its port lands it must register in `FbxUtils._KNOWN_PRODUCERS` **after**
shots (it scopes events against the freshly published `fbx_takes`; the order
contract is documented on the registry).
