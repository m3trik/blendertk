# blendertk — API Changes

_Diff vs the last release (origin/main @ 3626b42)._

## Removed (2)

- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.live_param_tooltips` — was `(self)`
- `mat_utils/texture_path_editor.py::TexturePathEditorSlots.tb_find_and_copy_textures_init` — was `(self, widget)`

## Added (18)

- `edit_utils/naming/_naming.py::Naming.SUFFIX_TYPES(cls) -> Tuple[Tuple[str, str, str, str], ...]`
- `edit_utils/naming/_naming.py::Naming.affix_rules(cls, overrides=None, modes=None)`
- `env_utils/_env_utils.py::EnvUtils.texture_search_dirs(path=None)`
- `env_utils/handoff_export.py::BlenderExportMixin.lightmap_search_dirs(self) -> List[str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.confirm(self, question: str) -> bool`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.confirm(self, question: str) -> bool`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.ignore_groups_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths(self, objects=None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies(self, objects=None, search_dirs=None, walk: bool = True) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths(self, objects=None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps(self, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects=None, dry_run: bool = False) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps(self, dirs_by_map: Dict[str, str], objects=None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs(cls, objects=None) -> List[str]`
- `mat_utils/_mat_utils.py::MatUtils.plan_find_and_copy_textures(images=None, search_dir=None, dest_dir=None, use_valid_paths=True)`
- `mat_utils/game_shader.py::GameShaderSlots.opacity_mode(self) -> Optional[str]`
- `mat_utils/game_shader.py::GameShaderSlots.txt000_init(self, widget)`
- `mat_utils/substance_bridge/parameters.py::Parameters.affix_parts(value: 'Any', *, default: str = 'prefix') -> 'tuple[str, str]'`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.live_param_tooltip_blocks(self)`

## Signature changed (6)

- `edit_utils/naming/_naming.py::Naming.suffix_by_type`
  - was: `(cls, objects, group_suffix='_GRP', locator_suffix='_LOC', joint_suffix='_JNT', mesh_suffix='_GEO', nurbs_curve_suffix='_CRV', camera_suffix='_CAM', light_suffix='_LGT', display_layer_suffix='_LYR', ik_handle_suffix='_IKH', nurbs_surface_suffix='_SRF', cluster_suffix='_CLS', lattice_suffix='_LAT', skin_cluster_suffix='_SKN', blend_shape_suffix='_BS', constraint_suffix='_CON', material_suffix='_MAT', shading_group_suffix='_SG', texture_suffix='_TEX', set_suffix='_SET', custom_suffixes=None, strip=None, strip_trailing_ints=False, strip_trailing_underscores=False, strip_trailing_padding=True, dry_run=False)`
  - now: `(cls, objects, group_suffix=None, locator_suffix=None, joint_suffix=None, mesh_suffix=None, nurbs_curve_suffix=None, camera_suffix=None, light_suffix=None, display_layer_suffix=None, ik_handle_suffix=None, nurbs_surface_suffix=None, cluster_suffix=None, lattice_suffix=None, skin_cluster_suffix=None, blend_shape_suffix=None, constraint_suffix=None, material_suffix=None, shading_group_suffix=None, texture_suffix=None, set_suffix=None, custom_suffixes=None, affix_mode=None, affix_modes=None, strip=None, strip_trailing_ints=False, strip_trailing_underscores=False, strip_trailing_padding=True, dry_run=False)`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.perform_export`
  - was: `(self, export_dir: str, objects: Optional[Union[List, Callable]] = None, preset_name: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]`
  - now: `(self, export_dir: str, objects: Optional[Union[List, Callable]] = None, preset_name: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None) -> bool`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ignore_groups`
  - was: `(self, value)`
  - now: `(self, names, case_sensitive: bool = False)`
- `mat_utils/image_to_plane/_image_to_plane.py::ImageToPlane.create`
  - was: `(cls, image_paths, mat_type='standard', suffix='_MAT', prefix='', plane_height=10.0, group=False, group_name='imagePlanes_GRP', roughness=0.0)`
  - now: `(cls, image_paths, mat_type='standard', suffix=None, prefix='', plane_height=10.0, group=False, group_name='imagePlanes_GRP', roughness=0.0)`
- `uv_utils/texture_transfer.py::TextureTransfer.assign_results`
  - was: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None) -> Dict[str, str]`
  - now: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '') -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.transfer`
  - was: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_suffix: str = '_TRANSFER') -> Dict[str, Dict[str, str]]`
  - now: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None) -> Dict[str, Dict[str, str]]`
