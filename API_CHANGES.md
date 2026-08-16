# blendertk — API Changes

_Diff vs the last release (origin/main @ d03c0e1). Generated 2026-08-16._

## Removed (1)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_diff_report` — was `(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]`

## Added (6)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.format_diff_report(cls, missing: list, extra: list, reparented: list = None) -> str`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb004(self, index, widget) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.cmb006_init(self, widget) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_texture_optimization(self, template) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.optimize_textures(self, template)`
- `node_utils/data_nodes.py::DataNodes.set_export_json(key, payload)`

## Signature changed (1)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_manifest`
  - was: `(cls, export_path: str, paths, *, data: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
  - now: `(cls, export_path: str, paths, *, data: Optional[dict] = None, last_diff: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
