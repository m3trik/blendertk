# blendertk — API Changes

_Diff vs the last release (origin/main @ 0c5b5ea)._

## Removed (8)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.rename` — was `(cls, old_export_path: str, new_export_path: str) -> list`
- `env_utils/maya_bridge/templates/_bake_scene.py::USD_EXTENSIONS` — was `(constant)`
- `env_utils/maya_bridge/templates/_bake_scene.py::apply_instances` — was `(engine, imported)`
- `env_utils/maya_bridge/templates/_bake_scene.py::apply_manifest` — was `(engine, imported)`
- `env_utils/maya_bridge/templates/_bake_scene.py::apply_scene` — was `(engine, is_usd)`
- `env_utils/maya_bridge/templates/_bake_scene.py::apply_visibility` — was `(engine, imported)`
- `env_utils/maya_bridge/templates/_bake_scene.py::import_source` — was `(bpy)`
- `env_utils/maya_bridge/templates/_bake_scene.py::tag_node_types` — was `(engine, imported)`

## Added (48)

- `anim_utils/_anim_utils.py::AnimUtils.evaluable_override(objects)`
- `anim_utils/_anim_utils.py::AnimUtils.step_visibility_keys(objects)`
- `anim_utils/shots/_shots.py::BlenderShotStore.apply_transfer(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None) -> Optional['BlenderShotStore']`
- `anim_utils/shots/_shots.py::BlenderShotStore.export_transfer(cls, spell=None, objects=None) -> Optional[Dict[str, Any]]`
- `anim_utils/shots/shot_sequencer/segment_collector.py::SegmentCollector.label_for(data_path: str, array_index: int = -1) -> str`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_keys_tangent_dragged(self, groups: list, side: str, broken: bool) -> None`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline(class)`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.compare(cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None) -> Tuple[bool, List[str], List[str], bool]`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.is_unreadable(cls) -> bool`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.migrate_from_sidecar(cls, export_dir: str) -> int`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.read(cls) -> Set[str]`
- `env_utils/hierarchy_sync/hierarchy_baseline.py::HierarchyBaseline.write(cls, current_paths: Set[str], roots: Optional[Sequence[str]] = None) -> bool`
- `env_utils/maya_bridge/_scene_import.py::FBX_IMPORT_OPTIONS(constant)`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.import_payload(self, payload_path: str, *, fbx_options: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, scene_settings: bool = False, reduce_keys: Union[bool, str, None] = False, shots: bool = True, progress: Optional[Callable[[int, int, str], Optional[bool]]] = None) -> List[Any]`
- `env_utils/maya_bridge/_scene_import.py::REDUCE_KEYS_DEFAULT(constant)`
- `env_utils/maya_bridge/templates/_bake_scene.py::REDUCE_KEYS(constant)`
- `env_utils/maya_bridge/templates/_import_scene.py::RIG_CAPABILITY(constant)`
- `env_utils/maya_bridge/templates/_import_scene.py::RIG_MODE(constant)`
- `env_utils/maya_bridge/templates/_import_scene.py::scene_lights(cmds)`
- `env_utils/maya_bridge/templates/_import_scene.py::shots_section(cmds, spell)`
- `env_utils/maya_bridge/templates/_import_scene.py::skinning_methods(cmds)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::RIG_CAPABILITY(constant)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::RIG_MODE(constant)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::shots_section(cmds, spell)`
- `env_utils/maya_bridge/templates/_save_scene.py::rebuild_shots(engine, new_nodes)`
- `env_utils/maya_bridge/templates/import.py::rebuild_shots(new_nodes)`
- `env_utils/usd.py::UsdUtils.honor_reset_xform_stack(usd_path: str, objects: Optional[List[Any]] = None) -> int`
- `env_utils/usd.py::UsdUtils.mark_skinning_methods(filepath: str, objects: Optional[List[Any]] = None, root_prim_path: str = '') -> int`
- `env_utils/usd.py::UsdUtils.pin_primvar_indices(filepath: str) -> int`
- `env_utils/usd.py::UsdUtils.skinning_methods(usd_path: str) -> Dict[str, str]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.apply_channel_records(cls, obj_name, records: dict) -> int`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_records(cls, objects=None) -> dict`
- `node_utils/data_nodes.py::DataNodes.get_internal_json(key, default=None)`
- `node_utils/data_nodes.py::DataNodes.set_internal_json(key, payload)`
- `rig_utils/_rig_utils.py::RigUtils.set_bone_heads(armature, heads)`
- `rig_utils/_rig_utils.py::RigUtils.set_bone_lengths(armature, lengths)`
- `rig_utils/rig_graph_build.py::RigGraphBuilder(class)`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.build(self, graph: Dict[str, Any], imported: Sequence[Any], is_usd: bool = False) -> Dict[str, Any]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.capability(cls) -> Dict[str, Any]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.commit(self, record_id: str) -> int`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.evaluable(self)`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.linear_unit() -> str`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.remove(self, record_id: str) -> int`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.sample_world(self, node_id: str, frame: int) -> Optional[Tuple[float, float, float]]`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.scope(self)`
- `rig_utils/rig_graph_build.py::RigGraphBuilder.up_axis() -> str`
- `rig_utils/rig_graph_extract.py::RigGraphExtractor(class)`
- `rig_utils/rig_graph_extract.py::RigGraphExtractor.extract(self, objects: Optional[Sequence[Any]] = None) -> Dict[str, Any]`

## Signature changed (12)

- `anim_utils/_anim_utils.py::AnimUtils.optimize_keys`
  - was: `(objects=None, value_tolerance=0.001, remove_static_curves=True, remove_flat_keys=True, simplify_keys=False, stats=None)`
  - now: `(objects=None, value_tolerance=0.001, remove_static_curves=True, remove_flat_keys=True, simplify_keys=False, stats=None, max_error=None)`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.place_dragged_handle`
  - was: `(kp, side: str, dt: float, dv: float) -> None`
  - now: `(kp, side: str, dt: float, dv: float, broken: bool = False) -> None`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake`
  - was: `(self, src_path: str, out_path: str, *, timeout: float = 600) -> Any`
  - now: `(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, reduce_keys: Union[bool, str, None] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None) -> Any`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.bake_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: float = 600, smart_bake: Union[bool, str] = 'auto', **script_opts: Any) -> str`
  - now: `(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, rig_mode: str = 'auto', reduce_keys: Union[bool, str, None] = REDUCE_KEYS_DEFAULT, progress: Optional[Callable[..., Optional[bool]]] = None, **script_opts: Any) -> str`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.convert`
  - was: `(self, src_path: str, out_path: str, *, via: str = 'fbx', timeout: float = 600, **script_opts: Any) -> 'ptk.ScriptRunResult'`
  - now: `(self, src_path: str, out_path: str, *, via: str = 'fbx', timeout: Optional[float] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None, **script_opts: Any) -> 'ptk.ScriptRunResult'`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.import_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: float = 600, fbx_options: Optional[Dict[str, Any]] = None, smart_bake: Union[bool, str] = 'auto', scene_settings: Union[bool, str] = 'auto', **script_opts: Any) -> List[Any]`
  - now: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, rig_mode: str = 'auto', scene_settings: Union[bool, str] = 'auto', reduce_keys: Union[bool, str, None] = REDUCE_KEYS_DEFAULT, shots: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, **script_opts: Any) -> List[Any]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.render_bake_script`
  - was: `(self, src_path: str, out_path: str) -> str`
  - now: `(self, src_path: str, out_path: str, reduce_keys: Union[bool, str, None] = None) -> str`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.render_script`
  - was: `(self, src_path: str, out_path: str, *, via: str = 'fbx', embed_textures: bool = False, include_animation: bool = True, smart_bake: Union[bool, str] = 'auto') -> str`
  - now: `(self, src_path: str, out_path: str, *, via: str = 'fbx', embed_textures: bool = False, include_animation: bool = True, rig_mode: str = 'auto') -> str`
- `env_utils/maya_bridge/templates/_import_scene.py::write_manifest`
  - was: `(entries, visibility, node_types, scene, path)`
  - now: `(entries, visibility, node_types, scene, path, lights=(), skins=None, bones=None, shots=None, rig=None, machinery=None)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::export_usd`
  - was: `(cmds)`
  - now: `(cmds, frame_range=None)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::write_manifest`
  - was: `(cmds, materials=None, shading_groups=None)`
  - now: `(cmds, materials=None, shading_groups=None, bones=None, shots=None, rig=None, machinery=None)`
- `env_utils/usd.py::UsdUtils.bake_transform_caches`
  - was: `(objects: Optional[List[Any]] = None, frame_range: Optional[Tuple[float, float]] = None) -> int`
  - now: `(objects: Optional[List[Any]] = None, frame_range: Optional[Tuple[float, float]] = None, clean: bool = True) -> int`
