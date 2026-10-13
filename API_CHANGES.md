# blendertk — API Changes

_Diff vs the last release (origin/main @ 65a7623)._

## Removed (3)

- `anim_utils/shots/shot_manifest/manifest_data.py::BEHAVIOR_STATUS_COLORS` — was `(constant)`
- `anim_utils/shots/shot_manifest/shot_manifest_slots.py::ShotManifestSlots.btn_expand_extra` — was `(self)`
- `anim_utils/shots/shot_manifest/shot_manifest_slots.py::ShotManifestSlots.btn_expand_missing` — was `(self)`

## Added (9)

- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.glyph_html(status: str) -> str`
- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.issues_html(issues: Iterable[Tuple[str, str]]) -> str`
- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.level(status: str) -> str`
- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.status_color(status: str) -> Optional[str]`
- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.worst(statuses: Iterable[str]) -> Optional[str]`
- `anim_utils/shots/shot_manifest/manifest_editor.py::ManifestEditor.edits_mode(self) -> str`
- `anim_utils/shots/shot_manifest/manifest_editor.py::ManifestEditor.show_hidden_objects(self) -> None`
- `anim_utils/shots/shot_manifest/manifest_editor.py::ManifestEditor.unlocked_hidden_objects(self) -> List[Tuple[str, str]]`
- `anim_utils/shots/shot_manifest/view_combos.py::ViewCombosMixin(class)`

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
