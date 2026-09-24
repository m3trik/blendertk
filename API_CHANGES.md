# blendertk — API Changes

_Diff vs the last release (origin/main @ b7b4d32)._

## Removed (1)

- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.cmb000` — was `(self, index, widget) -> None`

## Added (27)

- `env_utils/_env_utils.py::EnvUtils.scene_save_path(directory, name, case=None, suffix='', subfolder='')`
- `env_utils/reference_manager.py::ReferenceManagerSlots.copy_path_selected(self)`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.adaptive(self) -> bool`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.bake_targets(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.btn_reset_defaults_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.clear_exclusions(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.select_exclusions(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.set_exclusions(self) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.set_exclusions_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_baker_slots.py::LightmapBakerSlots.spn_samples_init(self, widget) -> None`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.migrate_folder_hints(cls, objects=None) -> List[str]`
- `light_utils/lightmap_baker/lightmap_records.py::LightmapRecords.superseding(cls, objects) -> Iterator[List[str]]`
- `mat_utils/arnold_bridge.py::ArnoldBridge.temporary(self, materials) -> Iterator[List[str]]`
- `mat_utils/arnold_bridge.py::ArnoldBridge.unrenderable_materials(cls) -> List[str]`
- `mat_utils/bake_sets.py::BakeSet(class)`
- `mat_utils/bake_sets.py::BakeSet.clear(cls) -> None`
- `mat_utils/bake_sets.py::BakeSet.collection(cls)`
- `mat_utils/bake_sets.py::BakeSet.define(cls, objects: Optional[List[Any]] = None) -> List[Any]`
- `mat_utils/bake_sets.py::BakeSet.exists(cls) -> bool`
- `mat_utils/bake_sets.py::BakeSet.members(cls) -> List[Any]`
- `mat_utils/bake_sets.py::BakeSet.meshes(cls) -> List[Any]`
- `mat_utils/bake_sets.py::LightmapExcludeSet(class)`
- `mat_utils/texture_baker.py::TextureBaker.texture_set(obj) -> Optional[Tuple[str, str]]`
- `node_utils/data_nodes.py::DataNodes.install_path_rebase(cls) -> bool`
- `node_utils/data_nodes.py::DataNodes.project_root(cls) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.remove_path_rebase(cls) -> None`
- `uv_utils/rizom_bridge/parameters.py::HOST_TOKEN_DEFAULTS(constant)`

## Deprecations (9)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its window: delete the alias and its tests rather than moving the date. A **HELD** row is due by version, but its notice has not yet had its calendar window._

- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.heal_lightmap_paths` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.lightmap_dependencies` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.normalize_lightmap_paths` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.refresh_export_metadata` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.relocate_lightmaps` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.repath_lightmaps` — remove in 0.11.0, not before 2026-10-23
- **HELD** `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.search_dirs` — remove in 0.11.0, not before 2026-10-23
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name` — remove in 0.12.0, not before 2026-10-23

## Moved (5)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `mat_utils/substance_bridge/_substance_bridge.py::HighPolySet.clear`
- `mat_utils/substance_bridge/_substance_bridge.py::HighPolySet.collection`
- `mat_utils/substance_bridge/_substance_bridge.py::HighPolySet.define`
- `mat_utils/substance_bridge/_substance_bridge.py::HighPolySet.exists`
- `mat_utils/substance_bridge/_substance_bridge.py::HighPolySet.members`
