# blendertk — API Changes

_Diff vs the last release (origin/main @ 3573a65)._

## Removed (7)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_key_tangent_dragged` — was `(self, clip_id: int, time: float, side: str, dt: float, dv: float) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_delete_all_shots` — was `(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_all_shots` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_locator_names` — was `(self, enabled=True) -> tuple`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::ROUNDTRIP` — was `(constant)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.preview` — was `(cls, objects=None, channel='highlight', enabled=True)`
- `mat_utils/substance_bridge/_substance_bridge.py::ROUNDTRIP` — was `(constant)`

## Added (40)

- `env_utils/maya_bridge/templates/_import_scene.py::uniquify_short_names(cmds)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::uniquify_short_names(cmds)`
- `env_utils/upstream_patches.py::SIBLING_ARMATURES(constant)`
- `env_utils/usd.py::UsdUtils.collapse_static_xforms(filepath: str, tolerance: float = 0.0001, distance: float = 1e-05) -> int`
- `env_utils/usd.py::UsdUtils.mark_orthographic(filepath: str, cameras: List[Any], root_prim_path: str = '') -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakeResult(class)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakeResult.files(self) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakeResult.folders(self) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake(self, objects=None, packing: str = 'atlas', output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', on_progress: Optional[Callable[[int, int, str], bool]] = None, intensity: float = 1.0, **kwargs) -> LightmapBakeResult`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_verdict(self, paths) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.baked_objects(self, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.preflight(self) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots(class)`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.b000(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb000(self, index, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb000_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb002_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_device_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_resolution_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb_scope_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.header_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.open_output(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.revert_to_source(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.txt000_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.txt_output_dir_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords(class)`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.baked_objects(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.claims(cls, objects=None) -> Dict[str, FrozenSet[str]]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.commit(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0) -> Dict[str, str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.heal_lightmap_paths(cls, objects=None) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.lightmap_dependencies(cls, objects=None, search_dirs=None, walk: bool = True) -> List[Dict[str, Any]]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.migrate_legacy(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.normalize_lightmap_paths(cls, objects=None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.refresh_export_metadata(cls) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.relocate_lightmaps(cls, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects=None, dry_run: bool = False) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.repath_lightmaps(cls, dirs_by_map: Dict[str, str], objects=None, relative: bool = True) -> int`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.revert(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.search_dirs(cls, objects=None) -> List[str]`
- `mat_utils/texture_baker.py::TextureBaker.image_sources(images) -> List[str]`

## Deprecations (8)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its one-release window: delete the alias and its tests rather than moving the date._

- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — remove in 0.11.0
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — remove in 0.11.0

## Moved (13)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.b000`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb000`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb000_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb002_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_device_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_resolution_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_scope_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.header_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.open_output`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.revert_to_source`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.txt000_init`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.txt_output_dir_init`

## Signature changed (6)

- `env_utils/scene_exporter/task_manager.py::TaskManager.exclude_hdr`
  - was: `(self, *legacy_enabled: bool, enabled: bool = True) -> None`
  - now: `(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_atlas`
  - was: `(self, objects=None, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', **kwargs) -> Dict[str, Tuple[str, List[float]]]`
  - now: `(self, objects=None, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', claims: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Tuple[str, List[float]]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.pack_atlas`
  - was: `(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None) -> Dict[str, Tuple[str, List[float]]]`
  - now: `(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None, claims: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[str, List[float]]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_fade`
  - was: `(cls, objects=None, start=0, end=15, direction='in', auto_create=True, tangent='LINEAR', preview=None, delete_visibility_keys=False, channel='opacity', whole_frames=True)`
  - now: `(cls, objects=None, start=0, end=15, direction='in', auto_create=True, tangent='LINEAR', delete_visibility_keys=False, channel='opacity', whole_frames=True)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, lead_in=None, lead_out=None, color=None, dim_color=None, auto_create=True, channel='highlight', preview=None, delete_visibility_keys=False, whole_frames=True)`
  - now: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, lead_in=None, lead_out=None, color=None, dim_color=None, auto_create=True, channel='highlight', delete_visibility_keys=False, whole_frames=True)`
- `mat_utils/texture_baker.py::TextureBaker.bake`
  - was: `(self, objects=None, *, bake_type: str = 'COMBINED', pass_filter: Optional[set] = None, use_pass_color: bool = True, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '', margin: Optional[int] = None, uv_set=None, stem: Optional[Any] = None, size: Optional[Any] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, colorspace: str = 'Non-Color') -> Dict[str, str]`
  - now: `(self, objects=None, *, bake_type: str = 'COMBINED', pass_filter: Optional[set] = None, use_pass_color: bool = True, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '', margin: Optional[int] = None, uv_set=None, stem: Optional[Any] = None, size: Optional[Any] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, colorspace: str = 'Non-Color', claims: Optional[Any] = None) -> Dict[str, str]`
