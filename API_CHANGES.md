# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-08-01._

## Added (26)

- `env_utils/scene_exporter/task_manager.py::TaskManager.check_absolute_paths(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_locator_names(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_duplicate_materials(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_floating_point_keys(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_framerate(self, target_key) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_geometry_lod_suffix(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_hidden_geometry(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_objects_below_floor(self, enabled, tolerance: float = 0.5) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_overlapping_duplicate_mesh(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_referenced_objects(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_root_default_transforms(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_texture_file_size(self, max_mb) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_untied_keyframes(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_valid_paths(self, enabled) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.convert_to_relative_paths(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.exclude_hdr(self, enabled)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.export_data_node(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ignore_groups(self, value)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.optimize_keys(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.reassign_duplicate_materials(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.resolve_invalid_texture_paths(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.set_bake_animation_range(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.set_linear_unit(self, value)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.smart_bake(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.snap_keys_to_frame(self)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.tie_all_keyframes(self)`
