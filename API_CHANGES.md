# blendertk — API Changes

_Diff vs the last release (origin/main @ 41260cc)._

## Removed (4)

- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.clear_bake_source` — was `(self) -> None`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.live_param_tooltip_blocks` — was `(self)`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.select_bake_source` — was `(self) -> None`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.set_bake_source_from_selection` — was `(self) -> None`

## Added (38)

- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.add_shot_space(self, shot_id: int, frames: float, edge: str = 'leading') -> Tuple[float, float]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.delete_shot(self, shot_id: int, delete_contents: bool = True, close_gap: bool = True) -> Dict[str, Any]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.insert_shot(self, name: str, duration: float, after_shot_id: Optional[int] = None, at_position: Optional[int] = None, gap: Optional[float] = None, objects: Optional[List[str]] = None, description: str = '')`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.ledger(self)`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.merge_shots(self, shot_ids: List[int], name: Optional[str] = None)`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.reconcile_system_edits(self) -> Dict[str, int]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.resize_shot_bounds(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.sequence_separation(self) -> float`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.split_shot(self, shot_id: int, at_frame: float, name: Optional[str] = None, gap: float = 0.0)`
- `anim_utils/shots/shot_sequencer/clip_motion.py::ClipMotionMixin.on_keys_batch_moved(self, groups) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.delete_shot(self, shot_id: int) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.merge_shot_with(self, shot_id: int, other_id: int) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.split_shot_at(self, shot_id: int, time: float) -> None`
- `anim_utils/shots/shots_slots.py::ShotsController.on_add_space(self, edge: str = 'leading') -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_add_leading_space(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_add_trailing_space(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_delete_all(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_all(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_all_both(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_all_leading(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_all_trailing(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_both(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_leading(self)`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_trim_trailing(self)`
- `core_utils/_core_utils.py::CoreUtils.visible_override(objects)`
- `env_utils/fbx_utils.py::FbxUtils.bake_range()`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_names(self, scope=None) -> tuple`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bounces(self) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.map_levels(cls, paths) -> Dict[str, Tuple[float, float]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.peak_level(cls, paths) -> Optional[Tuple[str, float, float]]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBakerSlots.cmb_device_init(self, widget) -> None`
- `mat_utils/render_opacity/_render_opacity.py::RenderOpacity.refresh_export_metadata(cls)`
- `mat_utils/render_opacity/_render_opacity.py::RenderOpacity.visibility_tracks(cls) -> list`
- `mat_utils/texture_baker.py::TextureBaker.denoise_images(cls, paths: Iterable[str], outputs: Optional[Iterable[Optional[str]]] = None, gpu: Optional[bool] = None) -> Dict[str, str]`
- `ui_utils/blender_bridge_slots_base.py::BlenderBridgeSlotsBase.clear_bake_source(self) -> None`
- `ui_utils/blender_bridge_slots_base.py::BlenderBridgeSlotsBase.live_param_tooltip_blocks(self)`
- `ui_utils/blender_bridge_slots_base.py::BlenderBridgeSlotsBase.select_bake_source(self) -> None`
- `ui_utils/blender_bridge_slots_base.py::BlenderBridgeSlotsBase.set_bake_source_from_selection(self) -> None`

## Signature changed (11)

- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.fit_shot_to_content`
  - was: `(self, shot_id: int, mode: str = 'fit') -> Tuple[float, float]`
  - now: `(self, shot_id: int, mode: str = 'fit', edge: str = 'both') -> Tuple[float, float]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.move_curve_keys`
  - was: `(cls, crv, times: list, delta: float, plug=None, eps: float = 0.001) -> None`
  - now: `(cls, crv, times: list, delta: float, plug=None, eps: float = 0.001, ledger=None, ledger_key: str = '') -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.recreate_curve_keys`
  - was: `(cls, crv, pairs: list, plug=None, eps: float = 0.001) -> None`
  - now: `(cls, crv, pairs: list, plug=None, eps: float = 0.001, ledger=None, ledger_key: str = '') -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.trim_shot_to_content`
  - was: `(self, shot_id: int) -> Tuple[float, float]`
  - now: `(self, shot_id: int, edge: str = 'both') -> Tuple[float, float]`
- `anim_utils/shots/shot_sequencer/clip_motion.py::ClipMotionMixin.scale_attribute_keys`
  - was: `(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None`
  - now: `(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> bool`
- `anim_utils/shots/shots_slots.py::ShotsController.on_trim_all_shots`
  - was: `(self) -> None`
  - now: `(self, edge: str = 'both') -> None`
- `anim_utils/shots/shots_slots.py::ShotsController.on_trim_empty`
  - was: `(self) -> None`
  - now: `(self, edge: str = 'both') -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers`
  - was: `() -> None`
  - now: `(only: Optional[Iterable[str]] = None) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_locator_names`
  - was: `(self, enabled) -> tuple`
  - now: `(self, enabled=True) -> tuple`
- `light_utils/lightmap_baker/web_export.py::LightmapWebExport.wire_lightmaps`
  - was: `(self, encoded: Dict[str, Tuple[str, float]], carrier: str = 'occlusion', uv_set: Optional[str] = None) -> Dict[str, Any]`
  - now: `(self, encoded: Dict[str, Tuple[str, float]], carrier: str = 'occlusion', uv_set: Optional[str] = None, rects: Optional[Dict[str, List[float]]] = None) -> Dict[str, Any]`
- `mat_utils/texture_baker.py::TextureBaker.denoise_image`
  - was: `(cls, path: str, output: Optional[str] = None) -> Optional[str]`
  - now: `(cls, path: str, output: Optional[str] = None, gpu: Optional[bool] = None) -> Optional[str]`
