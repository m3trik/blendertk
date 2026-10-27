# blendertk — API Changes

_Diff vs the last release (origin/main @ 8cca703)._

## Moved out (6)

- `edit_utils/duplicate_radial.py::DuplicateRadialSlots` → `tentacle.ui.panels.duplicate_radial` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.b001_init` → `tentacle.ui.panels.duplicate_radial` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.header_init` → `tentacle.ui.panels.duplicate_radial` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.perform_operation` → `tentacle.ui.panels.duplicate_radial` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s015_init` → `tentacle.ui.panels.duplicate_radial` (from 0.23.0)
- `edit_utils/duplicate_radial.py::DuplicateRadialSlots.s016_init` → `tentacle.ui.panels.duplicate_radial` (from 0.23.0)

## Added (10)

- `core_utils/_core_utils.py::CoreUtils.selected(func)`
- `edit_utils/components.py::Components(class)`
- `edit_utils/components.py::Components.average_normals(objects, by_uv_shell=False)`
- `edit_utils/components.py::Components.reverse_normals(objects, mode=0)`
- `edit_utils/components.py::Components.set_edge_hardness(objects, angle_threshold, upper_hardness=None, lower_hardness=None, unlock_normals=False)`
- `edit_utils/components.py::Components.set_edge_softness(objects, soft=True)`
- `edit_utils/components.py::Components.set_to_face_normals(objects)`
- `edit_utils/components.py::Components.toggle_normal_lock(objects)`
- `edit_utils/components.py::Components.transfer_normals(objects, space='world')`
- `edit_utils/duplicate_radial.py::DuplicateRadial.regroup_copies(copies)`

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
