# blendertk — API Changes

_Diff vs the last release (origin/main @ a059164)._

## Removed (6)

- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.b002` — was `(self) -> None`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots` — was `(class)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.header_init` — was `(self, widget)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.tb000` — was `(self, widget)`
- `mat_utils/render_opacity/render_opacity_slots.py::RenderOpacitySlots.tb000_init` — was `(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.b004` — was `(self)`

## Added (30)

- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.chk001(self, checked: bool) -> None`
- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.header_init(self, widget) -> None`
- `anim_utils/key_stash/key_stash_slots.py::KeyStashSlots.refresh_from_scene(self) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.move_attribute_keys(self, obj: str, attr: Optional[str], delta: float, times: Optional[List[float]] = None, window: Optional[tuple] = None) -> int`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.scale_shot_keys(self, old_start: float, old_end: float, new_start: float, new_end: float) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_key_menu(self, menu, key_groups: list) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_key_tangent_dragged(self, clip_id: int, time: float, side: str, dt: float, dv: float) -> None`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.place_dragged_handle(kp, side: str, dt: float, dv: float) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.run_config_from_values(self, values: Dict[str, Any], override_checks: bool = False, ignore_groups_case_sensitive: bool = False) -> Dict[str, Any]`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots(class)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.header_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb000_init(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001(self, widget)`
- `mat_utils/render_opacity/render_effects_slots.py::RenderEffectsSlots.tb001_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRig.auto_recalculate(cls, on=True)`
- `rig_utils/shadow_rig.py::ShadowRig.auto_recalculate_enabled(cls)`
- `rig_utils/shadow_rig.py::ShadowRig.planes_lit_by(cls, source)`
- `rig_utils/shadow_rig.py::ShadowRig.recalculate_stale(cls, planes=None)`
- `rig_utils/shadow_rig.py::ShadowRig.set_source_softness(cls, source, value)`
- `rig_utils/shadow_rig.py::ShadowRig.source_size(cls, source)`
- `rig_utils/shadow_rig.py::ShadowRig.source_softness(cls, source)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.chk_follow(self, checked)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.chk_follow_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.chk_horizon_preview_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.reproject_sources(self)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.s001(self, value)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.s001_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.source_from_selection(self)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.txt_source_init(self, widget)`

## Signature changed (10)

- `anim_utils/_anim_utils.py::AnimUtils.reduce_to_extremes`
  - was: `(objects=None, value_tolerance=0.001, stats=None)`
  - now: `(objects=None, value_tolerance=0.001, stats=None, max_error=None)`
- `anim_utils/_anim_utils.py::AnimUtils.snap_keys`
  - was: `(objects, selected_only=False, time_range=None, method='nearest')`
  - now: `(objects=None, selected_only=False, time_range=None, method='nearest')`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.resize_shot_bounds`
  - was: `(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True) -> None`
  - now: `(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True, clamp: bool = True) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.ripple_downstream`
  - was: `(self, shot_id: int, after_frame: float, delta: float) -> None`
  - now: `(self, shot_id: int, after_frame: float, delta: float, carry_gap: bool = True) -> None`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.ripple_upstream`
  - was: `(self, shot_id: int, before_frame: float, delta: float) -> None`
  - now: `(self, shot_id: int, before_frame: float, delta: float, carry_gap: bool = True) -> None`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_fade`
  - was: `(cls, objects=None, start=0, end=15, direction='in', auto_create=True, tangent='LINEAR')`
  - now: `(cls, objects=None, start=0, end=15, direction='in', auto_create=True, tangent='LINEAR', preview=None, delete_visibility_keys=False, channel='opacity', whole_frames=True)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, color=None, auto_create=True, channel='highlight')`
  - now: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, lead_in=None, lead_out=None, color=None, auto_create=True, channel='highlight', preview=None, delete_visibility_keys=False, whole_frames=True)`
- `rig_utils/shadow_rig.py::ShadowRig.bake_horizon`
  - was: `(self, bins=None, size=None, path=None, *, only_if_changed=False)`
  - now: `(self, size=None, spans=None, path=None, *, only_if_changed=False)`
- `rig_utils/shadow_rig.py::ShadowRig.create`
  - was: `(cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis='auto', source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, rig_type='projected', horizon_bins=None, horizon_size=None)`
  - now: `(cls, targets, light_pos=(5.0, 5.0, 10.0), texture_res=512, axis='auto', source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, rig_type='projected', horizon_size=None, horizon_spans=None)`
- `rig_utils/shadow_rig.py::ShadowRig.silhouette_is_stale`
  - was: `(cls, plane)`
  - now: `(cls, plane, *, degrees=None, distance=None)`
