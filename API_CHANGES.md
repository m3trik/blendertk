# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-08-01._

## Removed (26)

- `env_utils/scene_exporter/task_manager.py::TaskManager.check_absolute_paths` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_locator_names` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_materials` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_floating_point_keys` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_framerate` — was `(self, target_key) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_geometry_lod_suffix` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_hidden_geometry` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_objects_below_floor` — was `(self, enabled, tolerance: float = 0.5) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_overlapping_duplicate_mesh` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_referenced_objects` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_root_default_transforms` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_texture_file_size` — was `(self, max_mb) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_untied_keyframes` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_valid_paths` — was `(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.convert_to_relative_paths` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.exclude_hdr` — was `(self, enabled)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.export_data_node` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ignore_groups` — was `(self, value)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.optimize_keys` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.reassign_duplicate_materials` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.resolve_invalid_texture_paths` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.set_bake_animation_range` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.set_linear_unit` — was `(self, value)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.smart_bake` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.snap_keys_to_frame` — was `(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.tie_all_keyframes` — was `(self)`
