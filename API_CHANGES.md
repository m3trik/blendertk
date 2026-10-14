# blendertk — API Changes

_Diff vs the last release (origin/main @ 31bd4d8)._

## Added (23)

- `env_utils/_env_utils.py::EnvUtils.scene_source_images_dir(subfolder: str = '', create: bool = False) -> str`
- `env_utils/_env_utils.py::EnvUtils.scene_workspace()`
- `env_utils/_env_utils.py::EnvUtils.twin_save_target(cls, scene: Optional[str] = None) -> Optional[str]`
- `env_utils/maya_bridge/_scene_import.py::BAKE_CACHE_KIND(constant)`
- `env_utils/maya_bridge/_scene_import.py::BAKE_INFO_KEY(constant)`
- `env_utils/maya_bridge/_scene_import.py::BAKE_INFO_SOURCE(constant)`
- `env_utils/maya_bridge/_scene_import.py::BAKE_INFO_TEXT(constant)`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake_dir(cls, project: Optional[str] = None, create: bool = False) -> Optional[str]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake_info(cls, baked_path: str) -> Dict[str, str]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake_slot(cls, src_path: str, project: Optional[str] = None) -> Optional[str]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.purge_unused_bakes(self, project: Optional[str] = None, dry_run: bool = False) -> List[str]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.rehome_bake(self, baked_path: str, project: Optional[str] = None) -> Optional[str]`
- `env_utils/maya_bridge/templates/_bake_scene.py::BAKE_INFO(constant)`
- `env_utils/maya_bridge/templates/_bake_scene.py::STREAM_HOME(constant)`
- `env_utils/reference_manager.py::ReferenceManagerSlots.purge_unused_bakes(self, dry_run=False)`
- `env_utils/reference_manager.py::ReferenceManagerSlots.scene_notes(self)`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.toolbag_scene_dir(cls) -> str`
- `node_utils/data_nodes.py::DataNodes.scene_project_root(cls) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.scene_source(cls, scene: Optional[str] = None) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.scene_source_images_dir(cls, subfolder: str = '', create: bool = False) -> str`
- `node_utils/data_nodes.py::DataNodes.scene_workspace(cls) -> Optional['ptk.Workspace']`
- `node_utils/data_nodes.py::DataNodes.set_scene_source(cls, scene: str, source: Optional[str] = None) -> Optional[str]`
- `rig_utils/shadow_rig/_shadow_rig.py::ShadowRig.default_texture_dir()`

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

## Moved (1)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `ui_utils/blender_bridge_slots_base.py::BlenderBridgeSlotsBase.default_output_dir`

## Signature changed (9)

- `env_utils/_env_utils.py::EnvUtils.link_blend_file`
  - was: `(path, link=True, instance=True, target_collection=None)`
  - now: `(path, link=True, instance=True, target_collection=None, relative=False)`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake`
  - was: `(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, reduce_keys: Union[bool, str, None] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None) -> Any`
  - now: `(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, reduce_keys: Union[bool, str, None] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None, info: Optional[Mapping[str, str]] = None, stream_home: Optional[str] = None) -> Any`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, rig_mode: str = 'auto', reduce_keys: Union[bool, str, None] = REDUCE_KEYS_DEFAULT, progress: Optional[Callable[..., Optional[bool]]] = None, **script_opts: Any) -> str`
  - now: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, rig_mode: str = 'auto', reduce_keys: Union[bool, str, None] = REDUCE_KEYS_DEFAULT, progress: Optional[Callable[..., Optional[bool]]] = None, project: Optional[str] = None, **script_opts: Any) -> str`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake_source`
  - was: `(baked_path: str) -> Optional[str]`
  - now: `(cls, baked_path: str) -> Optional[str]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.import_payload`
  - was: `(self, payload_path: str, *, fbx_options: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, scene_settings: bool = False, reduce_keys: Union[bool, str, None] = False, scene_data: bool = True, progress: Optional[Callable[[int, int, str], Optional[bool]]] = None) -> List[Any]`
  - now: `(self, payload_path: str, *, fbx_options: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, scene_settings: bool = False, reduce_keys: Union[bool, str, None] = False, scene_data: bool = True, progress: Optional[Callable[[int, int, str], Optional[bool]]] = None, stream_home: Optional[str] = None) -> List[Any]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.render_bake_script`
  - was: `(self, src_path: str, out_path: str, reduce_keys: Union[bool, str, None] = None) -> str`
  - now: `(self, src_path: str, out_path: str, reduce_keys: Union[bool, str, None] = None, info: Optional[Mapping[str, str]] = None, stream_home: Optional[str] = None) -> str`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.render_template`
  - was: `(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None) -> Optional[str]`
  - now: `(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, save_dir: Optional[str] = None) -> Optional[str]`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.send`
  - was: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
  - now: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, scene_dir: Optional[str] = None) -> Optional[Dict[str, Any]]`
- `mat_utils/texture_baker.py::TextureBaker.default_output_dir`
  - was: `(subdir: str = 'baked_textures') -> str`
  - now: `(subdir: str = 'baked') -> str`
