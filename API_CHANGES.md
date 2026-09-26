# blendertk — API Changes

_Diff vs the last release (origin/main @ f096c57)._

## Added (13)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.delete_stale_shots(self) -> None`
- `anim_utils/shots/shots_slots.py::ShotsController.confirm_stale_removal(stale, parent=None) -> bool`
- `anim_utils/shots/shots_slots.py::ShotsController.on_delete_stale_shots(self) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_delete_stale(self)`
- `core_utils/diagnostics/scene_audit.py::SceneAnalyzer(class)`
- `core_utils/diagnostics/scene_audit.py::SceneAnalyzer.format_audit_html(cls, adaptive: bool = False, objects=None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[Iterable[str]] = None, scope: Optional[str] = None) -> Dict[str, str]`
- `core_utils/diagnostics/scene_audit.py::SceneAnalyzer.format_audit_text(cls, adaptive: bool = False, objects=None, sections: Optional[Iterable[str]] = None, scope: Optional[str] = None) -> Dict[str, str]`
- `core_utils/diagnostics/scene_audit.py::SceneInfoSection(class)`
- `core_utils/diagnostics/scene_audit.py::SceneInfoSection.normalize(cls, sections: Optional[Iterable[str]]) -> List[str]`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.adopt_sidecar(cls, export_path: str, *, base_stem: bool = False) -> bool`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.inherited_from(cls) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.scene_path(cls) -> str`
- `node_utils/data_nodes.py::DataNodes.writer_stamp(cls) -> str`

## Deprecations (10)

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
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — remove in 0.13.0, not before 2026-10-24

## Moved (1)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `node_utils/data_nodes.py::DataNodes.project_root`
