# blendertk — API Changes

_Diff vs the last release (origin/main @ 61c5772)._

## Added (1)

- `node_utils/data_nodes.py::DataNodes.twin_save_target(cls, scene: Optional[str] = None) -> Optional[str]`

## Deprecations (16)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.create_curtain` — remove in 0.14.0, not before 2026-10-26
- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.curtain_rail_from_selection` — remove in 0.14.0, not before 2026-10-26
- **HELD** `mat_utils/substance_bridge/_substance_bridge.py::SubstanceBridge.high_poly_path_for` — remove in 0.15.0, not before 2026-10-27
- **HELD** `anim_utils/shots/shot_sequencer/shot_lane.py::ShotLaneMixin.delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_all_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots` — remove in 0.16.0, not before 2026-11-06
- **HELD** `env_utils/scene_state.py::SceneState.read` — remove in 0.21.0, not before 2026-11-23
- **HELD** `env_utils/scene_state.py::SceneState.source` — remove in 0.21.0, not before 2026-11-23
- `env_utils/_env_utils.py::EnvUtils.scene_project_root` — remove in 0.22.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_source` — remove in 0.22.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_source_images_dir` — remove in 0.22.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.scene_workspace` — remove in 0.22.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.set_scene_source` — remove in 0.22.0, not before 2026-11-24
- `env_utils/_env_utils.py::EnvUtils.twin_save_target` — remove in 0.22.0, not before 2026-11-24
- `mat_utils/_mat_utils.py::MatUtils.update_materials` — remove in 0.22.0, not before 2026-11-24

## Signature changed (5)

- `env_utils/_env_utils.py::EnvUtils.scene_project_root`
  - was: `()`
  - now: `() -> Optional[str]`
- `env_utils/_env_utils.py::EnvUtils.scene_source`
  - was: `(cls, scene: Optional[str] = None) -> Optional[str]`
  - now: `(scene: Optional[str] = None) -> Optional[str]`
- `env_utils/_env_utils.py::EnvUtils.scene_workspace`
  - was: `()`
  - now: `() -> Optional[ptk.Workspace]`
- `env_utils/_env_utils.py::EnvUtils.set_scene_source`
  - was: `(cls, scene: str, source: Optional[str] = None) -> Optional[str]`
  - now: `(scene: str, source: Optional[str] = None) -> Optional[str]`
- `env_utils/_env_utils.py::EnvUtils.twin_save_target`
  - was: `(cls, scene: Optional[str] = None) -> Optional[str]`
  - now: `(scene: Optional[str] = None) -> Optional[str]`
