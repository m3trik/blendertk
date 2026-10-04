# blendertk — API Changes

_Diff vs the last release (origin/main @ 1ceb879)._

## Removed (5)

- `anim_utils/shots/shot_manifest/_shot_manifest.py::BlenderShotManifest.reapply_object` — was `(self, shot, obj) -> bool`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000` — was `(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000_init` — was `(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001` — was `(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001_init` — was `(self, widget)`

## Added (37)

- `anim_utils/shots/_shots.py::BlenderShotStore.curve_key(obj_name: str, data_path: str, index: int = 0) -> str`
- `anim_utils/shots/_shots.py::BlenderShotStore.discard_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/shots/_shots.py::BlenderShotStore.edit_serial(cls) -> int`
- `anim_utils/shots/_shots.py::BlenderShotStore.resolve_member(self, name: str) -> Tuple[str, str]`
- `anim_utils/shots/_shots.py::BlenderShotStore.scene_edit(self, label: str = 'edit', snapshot: bool = True)`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.behavior_paths(obj, behavior_name: str) -> List[str]`
- `anim_utils/shots/shot_sequencer/clip_motion.py::ClipMotionMixin.curves_for_attrs(obj_name: str, attr_names) -> list`
- `audio_utils/audio_clips.py::AudioClipsSlots.select_track(self, name: str) -> bool`
- `core_utils/diagnostics/uv_diag.py::UvDiagnostics(class)`
- `core_utils/diagnostics/uv_diag.py::UvDiagnostics.is_bakeable_lightmap(cls, obj, uv_set: str) -> bool`
- `display_utils/_display_utils.py::DisplayUtils.set_viewport_overlay(**flags)`
- `env_utils/_env_utils.py::EnvUtils.scene_project_root()`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.hide_relationship_lines() -> int`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::drop_unmergeable_locator_shapes(cmds)`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.decide_check_failure(self, check: str, messages: List[str], remaining: List[str]) -> str`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.lightmap_info(cls, obj) -> Dict[str, Any]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.project_root() -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.transfer_lightmaps(cls, targets, source, *, output_dir: Optional[str] = None, output_name: Optional[str] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1) -> Dict[str, Dict[str, str]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.apply_effect(cls, obj, effect, start, end, recipe=None, fps=None, place=None, anchor=None)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.scene_recipe(cls)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.scene_store()`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.write_keys(cls, obj, keys, channel='opacity', interp='LINEAR', mirror=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.b000(self, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.btn_remove(self, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.btn_webxr(self, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.btn_webxr_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.cmb_effect(self, index, widget=None)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.cmb_effect_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.focus(self, channel, objects, title='', apply=None, apply_text='')`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.stk_effects_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.ui_field(self, name)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.unfocus(self, *_args) -> None`
- `uv_utils/_uv_utils.py::LIGHTMAP_OVERLAP_TOLERANCE(constant)`
- `uv_utils/_uv_utils.py::UvUtils.apply_uv_layout(layouts, uv_set=None, quiet=False)`
- `uv_utils/_uv_utils.py::UvUtils.get_uv_triangles(obj, uv_set=None)`
- `uv_utils/texture_transfer.py::TextureTransfer.find_combined(cls, meshes: Sequence) -> Optional[Tuple[Any, Tuple]]`
- `uv_utils/texture_transfer.py::TextureTransfer.pair_sources(cls, targets: Sequence, sources: Sequence) -> Dict[Any, Any]`

## Deprecations (13)

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
- `mat_utils/substance_bridge/_substance_bridge.py::SubstanceBridge.high_poly_path_for` — remove in 0.15.0, not before 2026-10-27

## Signature changed (14)

- `anim_utils/_anim_utils.py::AnimUtils.invert_keys`
  - was: `(objects, mode='time', value_pivot=0.0, start_frame=None, relative=True, delete_original=False)`
  - now: `(objects, mode='time', value_pivot=0.0, start_frame=None, relative=True, delete_original=False, selected_only=False)`
- `anim_utils/segment_keys.py::SegmentKeys.collect_segments`
  - was: `(cls, objects: Union[str, List[str]], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001, transform_only: bool = True) -> List[Dict[str, Any]]`
  - now: `(cls, objects: Union[str, List[str]], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001, transform_only: bool = False) -> List[Dict[str, Any]]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.apply_behavior`
  - was: `(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None`
  - now: `(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None, recipe: Optional[Any] = None, fps: Optional[float] = None) -> List[Tuple[str, float]]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.apply_to_shots`
  - was: `(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]`
  - now: `(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None, resolve_fn=None, conflict_fn=None, release_fn=None) -> Dict[str, list]`
- `anim_utils/shots/shot_manifest/behaviors/_behaviors.py::Behaviors.verify_behavior`
  - was: `(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None) -> bool`
  - now: `(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None, recipe: Optional[Any] = None, fps: Optional[float] = None) -> bool`
- `anim_utils/shots/shot_manifest/manifest_data.py::ManifestData.format_behavior_html`
  - was: `(behaviors, broken=(), status_color=None) -> str`
  - now: `(behaviors, broken=(), status_color=None, stale=()) -> str`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.commit`
  - was: `(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0) -> Dict[str, str]`
  - now: `(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0, written: bool = True) -> Dict[str, str]`
- `mat_utils/_mat_utils.py::MatUtils.create_pbr_material`
  - was: `(textures, name=None, normal_direction='OpenGL', config=None, plan=None)`
  - now: `(textures, name=None, normal_direction='OpenGL', config=None, plan=None, ambient_occlusion=True)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_fade`
  - was: `(cls, objects=None, start=0, end=15, direction='in', auto_create=True, tangent='LINEAR', delete_visibility_keys=False, channel='opacity', whole_frames=True)`
  - now: `(cls, objects=None, start=0, end=None, direction='in', auto_create=True, tangent='LINEAR', delete_visibility_keys=False, channel='opacity', whole_frames=True, recipe=None)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, lead_in=None, lead_out=None, color=None, dim_color=None, auto_create=True, channel='highlight', delete_visibility_keys=False, whole_frames=True)`
  - now: `(cls, objects=None, start=0, end=100, period=None, bright_fraction=None, ramp_fraction=None, lead_in=None, lead_out=None, color=None, dim_color=None, auto_create=True, channel='highlight', delete_visibility_keys=False, whole_frames=True, recipe=None)`
- `mat_utils/texture_baker.py::TextureBaker.bake`
  - was: `(self, objects=None, *, bake_type: str = 'COMBINED', pass_filter: Optional[set] = None, use_pass_color: bool = True, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '', margin: Optional[int] = None, uv_set=None, stem: Optional[Any] = None, size: Optional[Any] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, colorspace: str = 'Non-Color', claims: Optional[Any] = None) -> Dict[str, str]`
  - now: `(self, objects=None, *, bake_type: str = 'COMBINED', pass_filter: Optional[set] = None, use_pass_color: bool = True, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '', margin: Optional[int] = None, uv_set=None, stem: Optional[Any] = None, size: Optional[Any] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, colorspace: str = 'Non-Color', claims: Optional[Any] = None, shader: Optional[Any] = None) -> Dict[str, str]`
- `uv_utils/_uv_utils.py::UvUtils.create_lightmap_uvs`
  - was: `(objects, uv_set=LIGHTMAP_UV_SET, margin=0.02, quiet=True)`
  - now: `(objects, uv_set=LIGHTMAP_UV_SET, margin=0.02, quiet=True, force=False)`
- `uv_utils/texture_transfer.py::TextureTransfer.assign_results`
  - was: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '') -> Dict[str, str]`
  - now: `(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '', assign_from: str = 'target', sources: Sequence[Any] = ()) -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.transfer`
  - was: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None) -> Dict[str, Dict[str, str]]`
  - now: `(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None, assign_from: str = 'target') -> Dict[str, Dict[str, str]]`
