# Scene records (`DataNodes`) — Blender mirror

> Mirror of mayatk's scene-record system — the model (declaration, store,
> producers), the records table and the engine-side contract are owned by
> **[mayatk/docs/data_nodes.md](https://github.com/m3trik/mayatk/blob/main/docs/data_nodes.md)**.
> This page documents only what diverges on Blender.

`blendertk.node_utils.data_nodes.DataNodes` is the Blender `ptk.SceneStoreBase`:
the same class name and the same `read` / `write` / `values` contract as
`mtk.DataNodes`, so every record (`ptk.SceneRecords`) loads and saves through it
unchanged and a producer ports across DCCs without renaming anything.

## What diverges from Maya

| Aspect | Maya | Blender |
|---|---|---|
| Private carrier (`data_internal`) | a `network` node | **one scene ID property group**, `scene["data_internal"]` — outside every object-set export by construction, no Outliner row, nothing to exclude by name in a current file |
| Private scope | one scene per file | **per scene**: each scene of a multi-scene `.blend` keeps its own group, and the store reads and writes the active scene's -- as the shots and key-stash records always were |
| Deliverable carrier (`data_export`) | a locked, hidden `transform` + zero-scale locator shape | a plain **Empty** whose custom properties Blender's FBX exporter writes as user properties |
| Shots / key-stash app state | `shot_store` / `key_stash` records on `data_internal` | the same records in the same group; a file saved before the group held them as top-level `scene["shot_store"]` / `scene["key_stash"]` |
| Migration | — | a file saved before 2026-09-18 is **folded on its first private write**: each scene's two top-level properties move into its own group, the old `data_internal` Empty's properties are copied into **every** local scene's group (the Empty was file-global: each scene read it), and the Empty is removed; a legacy value found beside the group was written after it (an older blendertk reopened the file) and wins. Reads find the old records without migrating (a panel draw may not edit data), and until the fold the export sets drop that Empty by name. A **library-linked** `data_internal` is the library's records: never read, never folded |
| Carrier lifetime | a keep-alive input stops Maya deleting the network node with its last input | a property group has no inputs, so nothing to guard |
| Writer stamp (`DataNodes.writer_stamp`) | the scene file spelled from its own project | the same, except a UNC share keeps its backslashes: a forward-slash `//` reads as Blender's file-relative prefix |
| Duplicate carrier | duplicate *short names* at different DAG levels resolve to the shallowest path | object names are unique per library; an FBX re-import lands as `data_export.001`, which the store ignores. The store writes only the file's OWN `data_export`; a library-linked module's is listed by `get_export_nodes` (Maya's `NS:data_export`) but never written. An **unlinked** carrier (collection deleted) is relinked into the scene on the next write |
| Carrier visibility | stays hidden; Maya exports hidden nodes in a selection | stays **visible/selectable** — a `use_selection` export can only ship what it can select (the `export_data_node` task clears any hide state defensively) |
| Before-export hook | the `kBeforeExport` session hook republishes opted-in records for any FBX export | **none** — `bpy.app.handlers` has no FBX-export event, so producers also publish at authoring time through `FbxUtils.publish_authored` (the Lightmap Baker on commit), which is what a non-Scene-Exporter write ships; `enable_export_producer` records the opt-in for parity |
| Stagers | `FbxUtils.STAGERS` runs the render-effects curve-proxy transport | `STAGERS` is empty: the Scene Exporter stages Blender's curve proxies in its own tasks (deferred restores). Session stagers (the shadow preview stands down for the write) `prepare` in every bracket and before a publish outside one, but `finish` only when a bracket closes -- so every write runs in one (the Scene Exporter's, the FBX and USD hand-offs'), and the Scene Exporter also stages the finish of what its publish prepared, for a run that stops before its write. `publish_authored` runs none |

## Crossing into another file

The records cross by the same rules as on Maya (mayatk's *Crossing into another
scene*: one engine, `ptk.RecordTransfer`, driven by each record's declaration),
through Blender's own routes:

- **A linked library made local** -- the Reference Manager's *Make Local* /
  *Unlink and Import* (`EnvUtils.make_library_local(library, scene_data=...)`).
  A library's private records live on its scene, which no link reaches, so
  `DataNodes.carriers_in(library)` reads them while it is still linked
  (linking its scenes for the read, then removing what the read linked), with
  its `data_export` and any pre-group `data_internal` Empty. The panel asks
  only when a merge would keep something -- **Yes** merges, **No** drops,
  **Cancel** leaves the library linked -- and names are respelled to where the
  move put each object and action (Blender's `.001` clash suffix; a name an
  object kept while an action lost it is ambiguous and left alone). The
  library's actions come local with it: its parked clips name them. Before
  this, the made-local `data_export.001` held deliverables nothing read, and a
  library's pre-group Empty landed under the canonical name, where the next
  private write folded it into this file's records and **replaced** them.
- **A Maya hand-off** -- `MayaBridge`'s *Include Scene Data*
  (`INCLUDE_SCENE_DATA`; was `INCLUDE_SHOTS`) and `MayaSceneImport`'s
  `scene_data=` (was `shots=`): the portable records ride the sidecar's
  `shots` / `records` sections both ways.

`DataNodes.OWNERS` names the Blender owners (`BlenderShotStore`, `KeyStash`,
`EmissiveGroups`); there is no bake-session owner -- a Blender bake parks
nothing beside its record. An emissive group's membership rides the mesh data
through a make-local (the boolean face attributes), and the hand-off payload
(registry, face indices, each member's face count) through a bridge.

`make_library_local` also takes every datablock of the library local **pass
after pass** until one localizes nothing more, and drops the library only once
nothing of it is still linked: `make_local` is a silent no-op on an ID whose
only user is still linked (a mesh under a linked object), and the one-pass loop
it replaced visited meshes first -- dropping the library then took every mesh
object it had just made local.

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
  is filtered out of the export.

**No preset can silently defeat either option**: the write site
(`_force_carrier_readability`) repairs both — with a warning — whenever the
carrier is in the export set, and the settings report is emitted *after* that
repair, so it discloses the values actually written. Pinned by
`test/test_scene_exporter.py`.

The Scene Exporter's default-on **"Export Scene Data Node"** task
(`export_data_node`) is the one publish of an export: `FbxUtils.publish` with the
run's Animation Clips mode as the producers' input, then the carrier joins the
export set in every export mode, exactly like mayatk's. A run with the task off
still publishes once before the write. The round-trip (publish → export →
re-import → property intact) is pinned by `test/test_scene_exporter.py`.

**Hand-off bridges**: `BlenderExportMixin` exposes `include_data_export`, and a
bridge whose *consumer* parses the records turns it on — `WebXrPreview` (its GLB
conversion binds `lightmap_metadata` via `ptk.MeshConvert.apply_glb_lightmaps`)
and `UnityBridge` (its FBX lands in `Assets/`). The carrier's `derived` records
are refreshed under a HANDOFF context first, and the flag **forces** the two
exporter options above at the point the carrier is appended, rather than
declaring them in the overridable `_fbx_options`: a bridge that overrides that
method wholesale (Substance / Marmoset do) must not be able to ship a carrier
holding nothing. Off by default, and never *created* just to ship. Pinned by
`test/test_fbx_utils.py`.

## Producers (Blender side)

`btk.FbxUtils.PRODUCERS` names the same record specs as mayatk's, with the same
payload schemas — one Unity reader and one GLB applier serve both DCCs:
`shot_metadata` (`BlenderShotStore.produce_export_records`; each clip carries its
own range, the take list `ptk.SceneRecords.declared_takes` reads -- `fbx_takes` is
no longer written, and a legacy one is cleared by the next shots publish; the
write splits its baked scene-range AnimStack into one windowed stack per take,
see `fbx_utils.py`'s module docstring), `visibility_tracks`
(`RenderEffects`), `shadow_metadata` (`ShadowRig`), `emissive_groups`
(`EmissiveGroups`; the keyed weights ship via transient scale-proxy Empties,
since Blender's FBX exporter cannot animate a custom property) and
`lightmap_metadata` (`LightmapRecords`; map file names, no folder -- a GLB build
is handed `LightmapRecords.search_dirs()`).

Audio (`audio_manifest`) is not yet produced — the audio panel is VSE-only.
When its port lands it adds one row to `PRODUCERS`; the declared dependency on
`shot_metadata` already orders it after shots.
