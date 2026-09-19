# blendertk — API Changes

_Diff vs the last release (origin/main @ 9a9788e)._

## Added (29)

- `anim_utils/smart_bake/_smart_bake.py::BakeResult.declined(self) -> Dict[str, str]`
- `anim_utils/smart_bake/_smart_bake.py::BakeResult.skip(self, key: str, reason: str) -> None`
- `env_utils/_env_utils.py::EnvUtils.scene_artifact_path(suffix: str) -> str`
- `env_utils/fbx_utils.py::FbxUtils.begin_export(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None, stagers: Optional[Iterable[str]] = None) -> Optional[ptk.ExportSnapshot]`
- `env_utils/fbx_utils.py::FbxUtils.disable_export_producer(cls, spec) -> None`
- `env_utils/fbx_utils.py::FbxUtils.enable_export_producer(cls, spec) -> None`
- `env_utils/fbx_utils.py::FbxUtils.end_export(cls) -> None`
- `env_utils/fbx_utils.py::FbxUtils.export_context(cls, mode: str = ptk.ExportContext.PIPELINE, **decisions) -> ptk.ExportContext`
- `env_utils/fbx_utils.py::FbxUtils.export_prepared(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None, stagers: Optional[Iterable[str]] = None)`
- `env_utils/fbx_utils.py::FbxUtils.producers(cls, only: Optional[Iterable[Any]] = None) -> Dict[Any, Callable]`
- `env_utils/fbx_utils.py::FbxUtils.publish(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None) -> ptk.ExportSnapshot`
- `env_utils/fbx_utils.py::FbxUtils.publish_authored(cls, records) -> ptk.ExportSnapshot`
- `env_utils/fbx_utils.py::FbxUtils.register_export_stager(cls, name: str, prepare: Optional[Callable[[], Any]] = None, finish: Optional[Callable[[], Any]] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.scratch_export(cls)`
- `env_utils/fbx_utils.py::FbxUtils.stage(cls, names: Optional[Iterable[str]] = None)`
- `env_utils/fbx_utils.py::FbxUtils.stagers(cls, names: Optional[Iterable[str]] = None) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_stager(cls, name: str) -> None`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.export_data_node_init(self, widget) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.ensure_scene_records_published(self)`
- `env_utils/usd.py::UsdUtils.mark_container_skeletons(filepath: str) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `mat_utils/emissive_groups.py::EmissiveGroups.export_record(cls, ctx: 'ptk.ExportContext') -> Optional['ptk.Record']`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]`
- `node_utils/data_nodes.py::DataNodes.dump_export_nodes(cls, decode: bool = True) -> Dict[str, Dict[str, Any]]`
- `node_utils/data_nodes.py::DataNodes.get_export_nodes(cls) -> List[Any]`
- `node_utils/data_nodes.py::DataNodes.read(cls, scope: ptk.Scope, key: str) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.values(cls, scope: ptk.Scope) -> Dict[str, Any]`
- `node_utils/data_nodes.py::DataNodes.write(cls, scope: ptk.Scope, key: str, text: Optional[str]) -> Optional[str]`
- `rig_utils/shadow_rig.py::ShadowRig.plane_record(cls, plane)`

## Deprecations (11)

_Live retirement debt, earliest deadline first. An **EXPIRED** row has outlived its one-release window: delete the alias and its tests rather than moving the date._

- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_mode` — remove in 0.9.0
- `node_utils/data_nodes.py::DataNodes.get_export_string` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.get_internal_json` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.get_internal_string` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_export_json` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_export_string` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_internal_json` — **no removal version recorded**
- `node_utils/data_nodes.py::DataNodes.set_internal_string` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.register_export_preparer` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers` — **no removal version recorded**
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_preparer` — **no removal version recorded**

## Moved (2)

_Still resolvable at the same call site -- hoisted to a base class or re-exported from another module. NOT a removal: no alias or minor bump is owed._

- `node_utils/data_nodes.py::DataNodes.dump`
- `node_utils/data_nodes.py::DataNodes.format_dump`

## Signature changed (18)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotEditDialog.show`
  - was: `(parent=None, name: str = '', start: float = 1.0, end: float = 100.0, description: str = '', title: str = 'Shot')`
  - now: `(parent=None, name: str = '', start: float = 1.0, end: float = 100.0, description: str = '', title: str = 'Shot', validate=None)`
- `env_utils/fbx_utils.py::FbxUtils.bake_range`
  - was: `()`
  - now: `(takes=None)`
- `env_utils/fbx_utils.py::FbxUtils.register_export_preparer`
  - was: `(name: str, prepare) -> None`
  - now: `(cls, name: str, prepare) -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers`
  - was: `(only: Optional[Iterable[str]] = None) -> None`
  - now: `(cls, only: Optional[Iterable[str]] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_preparer`
  - was: `(name: str) -> None`
  - now: `(cls, name: str) -> None`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.refresh_export_metadata`
  - was: `(cls)`
  - now: `(cls) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.ensure_export`
  - was: `()`
  - now: `(cls)`
- `node_utils/data_nodes.py::DataNodes.ensure_internal`
  - was: `()`
  - now: `(cls)`
- `node_utils/data_nodes.py::DataNodes.get_export_node`
  - was: `(create=True)`
  - now: `(cls, create: bool = True)`
- `node_utils/data_nodes.py::DataNodes.get_export_string`
  - was: `(key)`
  - now: `(cls, key)`
- `node_utils/data_nodes.py::DataNodes.get_internal_json`
  - was: `(key, default=None)`
  - now: `(cls, key, default=None)`
- `node_utils/data_nodes.py::DataNodes.get_internal_node`
  - was: `(create=True)`
  - now: `(cls, create: bool = True)`
- `node_utils/data_nodes.py::DataNodes.get_internal_string`
  - was: `(key)`
  - now: `(cls, key)`
- `node_utils/data_nodes.py::DataNodes.set_export_json`
  - was: `(key, payload)`
  - now: `(cls, key, payload)`
- `node_utils/data_nodes.py::DataNodes.set_export_string`
  - was: `(key, value)`
  - now: `(cls, key, value)`
- `node_utils/data_nodes.py::DataNodes.set_internal_json`
  - was: `(key, payload)`
  - now: `(cls, key, payload)`
- `node_utils/data_nodes.py::DataNodes.set_internal_string`
  - was: `(key, value)`
  - now: `(cls, key, value)`
- `rig_utils/shadow_rig.py::ShadowRig.export_record`
  - was: `(cls, plane)`
  - now: `(cls, ctx)`
