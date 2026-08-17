# blendertk — API Changes

_Diff vs the last release (origin/main @ d03c0e1). Generated 2026-08-17._

## Removed (1)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_diff_report` — was `(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]`

## Added (8)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.format_diff_report(cls, missing: list, extra: list, reparented: list = None) -> str`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb004(self, index, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb006_init(self, widget) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_texture_optimization(self, template) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.optimize_textures(self, template)`
- `mat_utils/_mat_utils.py::MatUtils.image_paths_scope(cls, images, new_path=None)`
- `node_utils/data_nodes.py::DataNodes.set_export_json(key, payload)`
- `uv_utils/_uv_utils.py::UvUtils.get_similar_uv_shells(objects, tolerance=1.0, include_reference=False, select=False)`

## Signature changed (3)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_manifest`
  - was: `(cls, export_path: str, paths, *, data: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
  - now: `(cls, export_path: str, paths, *, data: Optional[dict] = None, last_diff: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
- `uv_utils/_uv_utils.py::UvUtils.get_uv_coords`
  - was: `(objects)`
  - now: `(objects, pins=False)`
- `uv_utils/_uv_utils.py::UvUtils.pin_uvs`
  - was: `(objects, pin=True, selected_only=True)`
  - now: `(objects, pin=True, selected_only=True, whole_shells=False)`
