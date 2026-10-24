# Scene data nodes (`DataNodes`) — Blender

> The model (declaration, store, producers), every record's table and what an
> export carries are owned by
> **[mayatk's data_nodes.md](https://github.com/m3trik/mayatk/blob/main/docs/data_nodes.md)**.
> This page covers Blender's carriers and what diverges.

`blendertk.node_utils.data_nodes.DataNodes` is the Blender `ptk.SceneStoreBase`:
the same class name and the same `read` / `write` / `remove` / `values`
contract as `mtk.DataNodes`, so every record (`ptk.SceneRecords`) loads and
saves through it unchanged and a producer ports across DCCs without renaming
anything.

## The two carriers

```text
scene["data_internal"]     ID property group, on each Scene     PRIVATE -- never exported
└─ <record key>            string: the record's JSON            one per private record

data_export                plain Empty                          DELIVERABLE -- rides every export
├─ <record key>            string custom property: JSON         one per deliverable record
└─ emissiveGroup_<name>    float custom property 0-1,           each emissive group's weight
                           keyed on the Empty's own action
```

A record is stored as on Maya: one string per key, enveloped or bare, a clear
leaving `""` and `remove` deleting the property. `values` turns an ID property
group or array into a dict or list and skips `_`-prefixed keys. Blender has no
`audio_clip_<track_id>` enums: its sound strips are the audio's authoring
state.

## What diverges from Maya

| Aspect | Maya | Blender |
|---|---|---|
| Private carrier | a `network` node | one scene ID property group, `scene["data_internal"]` -- outside every object-set export by construction, no Outliner row |
| Private scope | one scene per file | **per scene**: each scene of a multi-scene `.blend` keeps its own group, and the store reads and writes the active scene's |
| Deliverable carrier | a locked, hidden `transform` + zero-scale locator | a plain Empty, whose custom properties Blender's FBX exporter writes as user properties |
| Carrier visibility | stays hidden; Maya exports hidden nodes in a selection | may be hidden: the write shows it for its `use_selection` funnel (linking it to the scene root while its collection is hidden or excluded) and hides it again after |
| Duplicate carrier | duplicate short names resolve to the shallowest DAG path | names are unique per library; an FBX re-import lands as `data_export.001`, which the store ignores. Only the file's own `data_export` is written; a library-linked one is listed by `get_export_nodes` (Maya's `NS:data_export`). An unlinked carrier is relinked on the next write |
| Carrier lifetime | a `keepAlive` input stops Maya deleting the node | a property group has no inputs |
| Legacy layout | -- | a file saved before 2026-09-18 kept private records on a `data_internal` **Empty** and the shot store / key stash as top-level `scene["shot_store"]` / `scene["key_stash"]`. Reads find them without migrating; the first private write **folds** them: each scene's top-level pair into its own group, the Empty's properties into every local scene's group, then the Empty goes (a legacy value found beside the group wins: an older blendertk wrote it). Until then the export sets drop the Empty by name. A library-linked `data_internal` is never read or folded |
| Record migration (`migrate`) | in every commit, and when a scene opens (`kAfterOpen`) | in every commit, and when a file loads (`load_post`: an open, a revert, File ▸ New); the move leaves the file unmodified |
| Writer stamp | the scene file spelled from its own project | the same, except a UNC path keeps its leading `\\`: a forward-slash `//` reads as Blender's file-relative prefix |
| Files written since the last save (`ptk.PendingFiles`: a bake's lightmaps wait in a session folder until the save) | committed at `kBeforeSave` of the open scene; an autosave or a `.ma` export is a copy and commits nothing; dropped at `kAfterOpen` / `kAfterNew` | committed in `save_pre`, after the records are re-spelled (the target does not exist yet, so a stamp naming it would read as a gone file's). `save_pre` cannot tell a Save As from a Save Copy (both hand it the new path while `bpy.data.filepath` is still the old one), so both commit as a fork: nothing over a file the open one reads, a taken name moved to the next free one. After a copy, the open file keeps the records the commit wrote (its maps are home, no longer in the session): a copy can cost the open file a name, never a map. Dropped at `load_post`: an open, a revert, or File ▸ New |
| Before-export hook | `kBeforeExport` republishes opted-in records for any FBX export | **none** -- `bpy.app.handlers` has no FBX-export event, so a plain File ▸ Export ships whatever the carrier holds; producers also publish at authoring time (`FbxUtils.publish_authored`), and `enable_export_producer` records the opt-in for parity |
| Stagers (`FbxUtils.STAGERS`) | `render_effects`, `lightmap_folder_hints` | those two plus `emissive_groups` (below). The Scene Exporter stages them all; a hand-off bridge runs only the rows it names (`export_stagers`; `UnityBridge`: `render_effects`, `emissive_groups`). Session stagers (the shadow preview) `prepare` in every bracket and before a publish outside one, but `finish` only when a bracket closes |
| Crossing a carrier's non-record properties | value and curve move | the value moves; the curve does not |

## What the export carries

As on Maya (mayatk's *What an export carries*), with one transport difference:
Blender's FBX exporter cannot animate a custom property, so each keyed
`emissiveGroup_<name>` weight ships on a transient child Empty of
`data_export` named for it, its `scale.x` keyed (marked
`emissiveGroupCurveProxy` and `curveProxy`; the `emissive_groups` stager adds
it for the write and removes it after). The render-effects curves ride
`<object>__opacity` / `<object>__highlight` children, as on Maya.

### Getting the carrier into the FBX

Two exporter options make the carrier readable, both in the Scene Exporter's
engine baseline `_DEFAULT_FBX_OPTIONS` (shipped as the `game_asset` preset,
and what an export with no preset uses):

- `use_custom_props=True` -- Blender's FBX exporter drops custom properties
  unless asked. The shipped `default` preset (Blender's own operator defaults)
  leaves it off.
- `object_types` including `"EMPTY"` -- otherwise the carrier Empty itself is
  filtered out of the export.

**No preset can defeat either**: the write repairs both, with a warning,
whenever the carrier is in the export set, and the settings report is emitted
after the repair. `FbxUtils.export_selection_fbx` (the Substance and Marmoset
writes) repairs them the same way. Pinned by `test/test_scene_exporter.py`.

The Scene Exporter's default-on *Export Scene Data Node* task
(`export_data_node`) is the one publish of an export -- `FbxUtils.publish` with
the run's Animation Clips mode -- and adds the carrier in every export mode; a
run with the task off still publishes once before the write.

**Hand-off bridges**: `BlenderExportMixin.include_data_export` (on for
`WebXrPreview` and `UnityBridge`, off by default) refreshes the carrier's
`derived` records under a HANDOFF context and **forces** both options where
the carrier is appended, rather than in the overridable `_fbx_options`, so a
subclass overriding that method cannot ship a carrier holding nothing. Never
*creates* a carrier just to ship. Pinned by `test/test_fbx_utils.py`.

## Producers (Blender side)

`btk.FbxUtils.PRODUCERS` names the same eight records as mayatk's, with the
same payload schemas -- one Unity reader and one GLB build serve both DCCs:

| Record | Producer | Blender notes |
|---|---|---|
| `animation` | `BlenderShotStore.produce_export_records` | the write splits its baked scene-range AnimStack into one windowed stack per take (`fbx_utils.py`'s module docstring) |
| `audio` | `AudioUtils.export_record` | one event per playing sound strip, named for the strip, at its visible start, scoped to the takes by `ptk.AudioUtils.scope_events`; a strip trimmed at its head plays from its file's start (the export notes it) |
| `render_effects` | `RenderEffects.export_record` | -- |
| `shadows` | `ShadowRig.export_record` | -- |
| `articulation` | `ArticulatedRig.export_record` | -- |
| `emissive_groups` | `EmissiveGroups.export_record` | weights ship on the proxy Empties above |
| `lightmaps` | `LightmapRecords.export_record` | map file names, no folder; a GLB build is handed `LightmapRecords.search_dirs()` |
| `materials` | `MaterialRecords.export_record` | a Principled BSDF read into glTF's metallic-roughness terms |

The Scene Exporter and the Unity bridge finish the written FBX through
`FbxUtils.finish_deliverable`, as on Maya: each record's files embedded (each
audio event's under its name), the `lightmapInfo` markers and empty channels
dropped.

## Crossing into another file

The records cross by the same rules as on Maya (mayatk's *Crossing into another
scene*: one engine, `ptk.RecordTransfer`, driven by each record's declaration),
through Blender's own routes:

- **A linked library made local** -- the Reference Manager's *Make Local* /
  *Unlink and Import* (`EnvUtils.make_library_local(library, scene_data=...)`).
  A library's private records live on its scenes, which no link reaches, so
  `DataNodes.carriers_in(library)` links them for the read and removes what it
  linked. The panel asks only when a merge would keep something -- **Yes**
  merges, **No** drops, **Cancel** leaves the library linked -- and names are
  respelled to where the move put each object and action (Blender's `.001`
  clash suffix). The library's actions come local with it (its parked clips
  name them), and the library is dropped only once nothing of it is still
  linked.
- **A Maya hand-off** -- `MayaBridge`'s *Include Scene Data*
  (`INCLUDE_SCENE_DATA`) and `MayaSceneImport.import_scene(scene_data=...)`:
  the portable records ride the sidecar's `shots` / `records` sections both
  ways.

`DataNodes.OWNERS` names the Blender owners (`BlenderShotStore`, `KeyStash`,
`EmissiveGroups`); there is no bake-session owner -- a Blender bake parks
nothing beside its record. An emissive group's membership rides the mesh data
through a make-local (boolean face attributes), and the hand-off payload
(registry, face indices, each member's face count) through a bridge.
