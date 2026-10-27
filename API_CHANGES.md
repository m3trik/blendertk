# blendertk — API Changes

_Diff vs the last release (origin/main @ 8cca703)._

## Moved out (44)

- `edit_utils/duplicate_radial.py::DuplicateRadialSlots` → `tentacle.ui.panels.duplicate_radial.duplicate_radial.DuplicateRadialSlots` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.b001_init` → `tentacle.ui.panels.duplicate_radial.duplicate_radial.DuplicateRadialSlots` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.header_init` → `tentacle.ui.panels.duplicate_radial.duplicate_radial.DuplicateRadialSlots` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.perform_operation` → `tentacle.ui.panels.duplicate_radial.duplicate_radial.DuplicateRadialSlots` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s015_init` → `tentacle.ui.panels.duplicate_radial.duplicate_radial.DuplicateRadialSlots` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s016_init` → `tentacle.ui.panels.duplicate_radial.duplicate_radial.DuplicateRadialSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.add_group` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.bake_mask` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.bake_vertex_colors` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.compact_slots` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.create_export_curve_proxies` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.export_record` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.key_weight` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.list_groups` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.make_weights_keyable` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.refresh_export_metadata` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.remove_export_curve_proxies` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.remove_group` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.remove_keyable_weights` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.select_group` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.set_default` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_in` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_out` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroups.validate` → `tentacle.tools.emissive_groups.blender.EmissiveGroups` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b000` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b001` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b002` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.b003` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.compact_slots` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.header_init` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.key_weights` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.make_weights_keyable` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.remove_group` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.remove_keyable_weights` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.republish_export` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.select_members` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.tb000` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.tb000_init` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.tbl000_init` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.txt000_init` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.weights_all_off` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)
- `mat_utils/emissive_groups.py::EmissiveGroupsSlots.weights_all_on` → `tentacle.ui.panels.emissive_groups.emissive_groups.EmissiveGroupsSlots` (from 0.23.0)

## Added (17)

- `anim_utils/_anim_utils.py::AnimUtils.remove_fcurve(action, slot, fc) -> bool`
- `anim_utils/shots/_shots.py::BlenderShotStore.fcurve_for_key(key: str)`
- `anim_utils/shots/_shots.py::BlenderShotStore.is_transform_path(data_path: str) -> bool`
- `core_utils/_core_utils.py::CoreUtils.active_view_layer()`
- `core_utils/_core_utils.py::CoreUtils.selected(func: Callable) -> Callable`
- `edit_utils/components.py::Components(class)`
- `edit_utils/components.py::Components.average_normals(objects, by_uv_shell=False)`
- `edit_utils/components.py::Components.reverse_normals(objects, mode=0)`
- `edit_utils/components.py::Components.set_edge_hardness(objects, angle_threshold, upper_hardness=None, lower_hardness=None, unlock_normals=False)`
- `edit_utils/components.py::Components.set_edge_softness(objects, soft=True)`
- `edit_utils/components.py::Components.set_to_face_normals(objects)`
- `edit_utils/components.py::Components.toggle_normal_lock(objects)`
- `edit_utils/components.py::Components.transfer_normals(objects, space='world')`
- `edit_utils/duplicate_radial.py::DuplicateRadial.regroup_copies(copies)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.ensure_highlight_props(cls, obj, color=None, dim_color=None)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.ensure_opacity_prop(cls, obj, value=1.0)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.set_key(obj, data_path, frame, value, interp, index=-1)`

## Deprecations (14)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `env_utils/scene_state.py::SceneState.read` — remove in 0.21.0, not before 2026-11-23
- **HELD** `env_utils/scene_state.py::SceneState.source` — remove in 0.21.0, not before 2026-11-23
- `anim_utils/shots/_shots.py::BlenderShotStore.iter_action_fcurves` — remove in 0.23.0, not before 2026-11-26
- `env_utils/_env_utils.py::EnvUtils.scene_project_root` — remove in 0.23.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_source` — remove in 0.23.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_source_images_dir` — remove in 0.23.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_workspace` — remove in 0.23.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.set_scene_source` — remove in 0.23.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.twin_save_target` — remove in 0.23.0, not before 2026-11-24
- `mat_utils/_mat_utils.py::MatUtils.update_materials` — remove in 0.23.0, not before 2026-11-24
