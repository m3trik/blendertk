# blendertk — API Changes

_Diff vs the last release (origin/main @ 53a6ca2)._

## Removed (17)

- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — was `(cls, export_dir: str) -> int`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name` — was `(self, name: str, name_regex: Optional[str] = None) -> str`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — was `(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — was `(self, objects=None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — was `(self, objects=None, search_dirs=None, walk: bool = True) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — was `(self, objects=None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — was `(cls) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — was `(self, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects=None, dry_run: bool = False) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — was `(self, dirs_by_map: Dict[str, str], objects=None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — was `(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.clear_bake_set` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_scope` — was `(self, index) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.list_changed_objects` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.revert_to_source` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_bake_set` — was `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_bake_set_init` — was `(self, widget) -> None`
- `mat_utils/bake_sets.py::LightmapBakeSet` — was `(class)`

## Added (32)

- `anim_utils/shots/shots_slots.py::ShotsController.on_drop_stale_shots_changed(self, checked: bool) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.chk_drop_stale_shots(self, checked)`
- `env_utils/material_records.py::MaterialRecords(class)`
- `env_utils/material_records.py::MaterialRecords.entries(cls, objects: Iterable[Any], notes: Optional[List[str]] = None) -> List[Dict[str, Any]]`
- `env_utils/material_records.py::MaterialRecords.export_record(cls, ctx: 'ptk.ExportContext') -> Optional['ptk.Record']`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_breakdown(self, output_dir: Optional[str] = None, relocate: bool = False)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_maps(self, report: Optional['ptk.StaleReport'] = None, output_dir: Optional[str] = None, relocate: bool = False) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.combine(self, maps=None, size: Optional[int] = None, merge_materials: bool = True, on_progress: Optional[Callable[[int, int, str], Any]] = None) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.combine_sizes(self, maps=None) -> List[Tuple[int, float]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.map_folders(self, objects=None) -> Dict[str, List[str]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.rebake(self, objects=None, packing: Optional[str] = None, relocate: bool = False, **bake_kwargs) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.rebake_report(self, objects=None, output_dir: Optional[str] = None, relocate: bool = False, intensity: Optional[float] = None)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.track(self, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_page(self, index) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_page_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.combine_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.fix_all(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.open_map_folder(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.rebake_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.revert_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_by_state(self, kind: str) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.show_bake_state(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.tbl_maps_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.track_selected(self) -> None`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.foreign_writer(cls, map_name: str, writers: Optional[Dict[str, str]] = None) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.fork_lightmap(cls, old_name: str, new_name: str) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.strangers(cls, folder: str, stem: str, ext: str = '.exr', claims: Optional[Dict[str, FrozenSet[str]]] = None) -> Set[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.track(cls, objects=None, lighting: Optional[Dict[str, Any]] = None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.untracked(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.wrote(cls, map_name: str, writers: Optional[Dict[str, str]] = None) -> bool`
- `node_utils/data_nodes.py::DataNodes.discard_if_empty(cls, scope: ptk.Scope) -> bool`
- `node_utils/data_nodes.py::DataNodes.remove(cls, scope: ptk.Scope, key: str) -> bool`

## Deprecations (9)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.create_curtain` — remove in 0.14.0, not before 2026-10-26
- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.curtain_rail_from_selection` — remove in 0.14.0, not before 2026-10-26
- **HELD** `mat_utils/substance_bridge/_substance_bridge.py::SubstanceBridge.high_poly_path_for` — remove in 0.15.0, not before 2026-10-27
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- `env_utils/scene_state.py::SceneState.read` — remove in 0.21.0, not before 2026-11-23
- `env_utils/scene_state.py::SceneState.source` — remove in 0.21.0, not before 2026-11-23

## Signature changed (11)

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.generate_export_path`
  - was: `(self, version_format: str = '', extension: str = '.fbx', output_format: Optional[str] = None) -> str`
  - now: `(self, *, extension: str = '.fbx', output_format: Optional[str] = None) -> str`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.name_context`
  - was: `(self, name_regex: Optional[str] = None) -> Dict[str, str]`
  - now: `(self) -> Dict[str, str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.perform_export`
  - was: `(self, export_dir: str, objects: Optional[Union[List, Callable]] = None, preset_name: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: Optional[str] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None) -> bool`
  - now: `(self, export_dir: str, objects: Optional[Union[List, Callable]] = None, preset_name: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, create_log_file: bool = False, *, log_level: Optional[str] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None) -> bool`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.resolve_export_path`
  - was: `(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', name_regex: Optional[str] = None, report: bool = True, version_format: str = '', timestamp: bool = False) -> Dict[str, Any]`
  - now: `(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', *, report: bool = True) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.commit_lightmap`
  - was: `(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None, uv_rects: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
  - now: `(self, mapping: Dict[str, str], *, scale_offsets: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.rebake_changed`
  - was: `(self, packing: Optional[str] = None, reach: Optional[float] = None, lighting: bool = True, **bake_kwargs) -> LightmapBakeResult`
  - now: `(self, packing: Optional[str] = None, reach: Optional[float] = None, lighting: bool = True, relocate: bool = False, **bake_kwargs) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.stale_report`
  - was: `(self, objects=None, reach: Optional[float] = None)`
  - now: `(self, objects=None, reach: Optional[float] = None, output_dir: Optional[str] = None, relocate: bool = False)`
- `light_utils/lightmap_baker/web_export.py::LightmapWebExport.build_manifest`
  - was: `(self, encoded: Dict[str, Tuple[str, float]], carrier: str, lighting: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`
  - now: `(self, encoded: Dict[str, Tuple[str, float]], carrier: str) -> Dict[str, Any]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.prepare_for_export`
  - was: `(cls, objects=None) -> list`
  - now: `(cls) -> list`
- `rig_utils/shadow_rig/_shadow_rig.py::ShadowRig.create`
  - was: `(cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis='auto', source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, rig_type='projected', horizon_size=None, horizon_spans=None)`
  - now: `(cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, *, source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, rig_type='projected', horizon_size=None, horizon_spans=None)`
- `rig_utils/shadow_rig/_shadow_rig.py::ShadowRig.create_silhouette_texture`
  - was: `(self, size=512, axis='auto', recursive=True, path=None, *, refit=True, source_size=None, uniform_alpha=True, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.0)`
  - now: `(self, size=512, *, recursive=True, path=None, refit=True, source_size=None, uniform_alpha=True, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.0)`
