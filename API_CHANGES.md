# blendertk — API Changes

_Diff vs the last release (origin/main @ bf6b0b2)._

## Removed (2)

- `anim_utils/shots/shot_manifest/export_menu.py::ExportMenuMixin` — was `(class)`
- `anim_utils/shots/shot_manifest/export_menu.py::ExportMenuMixin.export` — was `(self) -> None`

## Added (28)

- `anim_utils/_anim_utils.py::AnimUtils.euler_filter(objects) -> list`
- `anim_utils/_anim_utils.py::AnimUtils.get_key_curves(fcurves) -> list`
- `anim_utils/_anim_utils.py::AnimUtils.shift_key_values(edits) -> int`
- `anim_utils/shots/_shots.py::BlenderShotStore.from_dict(cls, data: Dict[str, Any]) -> 'BlenderShotStore'`
- `anim_utils/shots/shot_manifest/export_preview.py::ExportPreviewMixin(class)`
- `anim_utils/shots/shot_manifest/export_preview.py::ExportPreviewMixin.export(self) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync(class)`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.detach(self) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.key_targets(self, groups=None) -> list`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_infinity(self, side: str, mode: str, curves) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_key_menu(self, menu, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_key_selection(self, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_keys_deleted(self, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_keys_edited(self, edits) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_keys_inserted(self, items) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_tangent_type(self, type_id: str, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_tangents_broken(self, broken: bool, groups) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.on_tangents_edited(self, rows) -> None`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.outline(self) -> List[dict]`
- `anim_utils/shots/shot_sequencer/graph_sync.py::GraphSync.sync(self, full: bool = False) -> None`
- `anim_utils/shots/shot_sequencer/key_menu.py::KeyMenuMixin.register_key_targets(self, editor_name: str, source) -> None`
- `anim_utils/shots/shot_sequencer/segment_collector.py::SegmentCollector.summary_times(segments, claimed_times) -> list`
- `env_utils/hierarchy_sync/_fbx_stage_worker.py::WARNING_PREFIX(constant)`
- `env_utils/maya_bridge/templates/_import_scene.py::stepped_keys(spell)`
- `env_utils/maya_bridge/templates/_save_scene.py::covering_take(mel, path)`
- `env_utils/maya_bridge/templates/_save_scene.py::fallback_take(mel, path, warn)`
- `env_utils/maya_bridge/templates/import.py::covering_take(mel, path)`
- `env_utils/maya_bridge/templates/import.py::fallback_take(mel, path, warn)`

## Deprecations (17)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — remove in 0.11.0, not before 2026-10-23
- **HELD** `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name` — remove in 0.12.0, not before 2026-10-23
- **HELD** `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — remove in 0.13.0, not before 2026-10-24
- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.create_curtain` — remove in 0.14.0, not before 2026-10-26
- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.curtain_rail_from_selection` — remove in 0.14.0, not before 2026-10-26
- **HELD** `mat_utils/substance_bridge/_substance_bridge.py::SubstanceBridge.high_poly_path_for` — remove in 0.15.0, not before 2026-10-27
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.16.0, not before 2026-11-06

## Signature changed (4)

- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.format_behavior_html`
  - was: `(behaviors, broken=(), status_color=None, stale=(), disabled=()) -> str`
  - now: `(behaviors, broken=(), status_color=None, stale=(), disabled=(), marked=()) -> str`
- `anim_utils/shots/shot_manifest/shot_manifest_controller.py::ShotManifestController.assess`
  - was: `(self, skip_key_check: bool = False) -> None`
  - now: `(self, skip_key_check: bool = False, note: str = '') -> None`
- `env_utils/fbx_utils.py::FbxUtils.import_fbx`
  - was: `(filepath, **fbx_opts)`
  - now: `(filepath, stash=True, logger=None, **fbx_opts)`
- `env_utils/maya_bridge/templates/_import_scene.py::write_manifest`
  - was: `(entries, visibility, node_types, scene, path, lights=(), skins=None, bones=None, scene_data=None, rig=None, machinery=None)`
  - now: `(entries, visibility, node_types, scene, path, lights=(), skins=None, bones=None, scene_data=None, rig=None, machinery=None, stepped=None)`
