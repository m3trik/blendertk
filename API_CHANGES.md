# blendertk — API Changes

_Diff vs the last release (origin/main @ 30727eb)._

## Removed (1)

- `mat_utils/substance_bridge/_substance_bridge.py::HighPolySet` — was `(class)`

## Added (13)

- `core_utils/_core_utils.py::CoreUtils.preserved_selection()`
- `env_utils/handoff_export.py::BlenderExportMixin.scope_closure(cls, objects, params: Optional[Dict[str, Any]] = None) -> List[Any]`
- `mat_utils/bake_sets.py::BakeSourceSet(class)`
- `mat_utils/bake_sets.py::BakeSourceSet.companion_path(cls, export_path: str) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.baked_material_name(cls, mat_name: str) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.source_material_name(cls, mat_name: str) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.source_model_path_for(cls, fbx_path: str) -> str`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.texture_set_aliases(cls, materials, log=None) -> Dict[str, str]`
- `mat_utils/substance_bridge/_substance_bridge.py::SubstanceBridge.source_model_path_for(cls, fbx_path: str) -> str`
- `node_utils/data_nodes.py::DataNodes.ensure_path_rebase(cls) -> bool`
- `node_utils/data_nodes.py::DataNodes.writer_stamp_of(cls, scene_path: Optional[str]) -> str`
- `ui_utils/blender_bridge_slots_base.py::BlenderBridgeSlotsBase.scoped_objects(self, params, warn: bool = True)`
- `uv_utils/_uv_utils.py::UvUtils.get_uv_shell_sets(objects=None, whole_shells=False)`

## Deprecations (13)

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
- **HELD** `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar` — remove in 0.13.0, not before 2026-10-24
- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.create_curtain` — remove in 0.14.0, not before 2026-10-26
- **HELD** `edit_utils/curtain/_curtain.py::CurtainUtils.curtain_rail_from_selection` — remove in 0.14.0, not before 2026-10-26
- `mat_utils/substance_bridge/_substance_bridge.py::SubstanceBridge.high_poly_path_for` — remove in 0.15.0, not before 2026-10-27

## Moved (1)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `node_utils/data_nodes.py::DataNodes.writer_stamp`

## Signature changed (2)

- `env_utils/_env_utils.py::EnvUtils.delete_scene_file`
  - was: `(path)`
  - now: `(path, permanent: bool = False)`
- `uv_utils/rizom_bridge/_rizom_bridge.py::RizomUVBridge.send`
  - was: `(self, objects, load_uvs=True, import_groups=True, load_uvw_props=True, load_textures=True)`
  - now: `(self, objects, load_uvs=True, import_groups=True, load_uvw_props=True, load_textures=True, params=None)`
