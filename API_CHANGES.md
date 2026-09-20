# blendertk — API Changes

_Diff vs the last release (origin/main @ 8d39c96)._

## Removed (15)

- `env_utils/fbx_utils.py::FbxUtils.register_export_preparer` — was `(cls, name: str, prepare) -> None`
- `env_utils/fbx_utils.py::FbxUtils.run_export_preparers` — was `(cls, only: Optional[Iterable[str]] = None) -> None`
- `env_utils/fbx_utils.py::FbxUtils.unregister_export_preparer` — was `(cls, name: str) -> None`
- `env_utils/maya_bridge/templates/_import_scene.py::shots_section` — was `(cmds, spell)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::shots_section` — was `(cmds, spell)`
- `env_utils/maya_bridge/templates/_save_scene.py::rebuild_shots` — was `(engine, new_nodes)`
- `env_utils/maya_bridge/templates/import.py::rebuild_shots` — was `(new_nodes)`
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_mode` — was `(self) -> None`
- `node_utils/data_nodes.py::DataNodes.get_export_string` — was `(cls, key)`
- `node_utils/data_nodes.py::DataNodes.get_internal_json` — was `(cls, key, default=None)`
- `node_utils/data_nodes.py::DataNodes.get_internal_string` — was `(cls, key)`
- `node_utils/data_nodes.py::DataNodes.set_export_json` — was `(cls, key, payload)`
- `node_utils/data_nodes.py::DataNodes.set_export_string` — was `(cls, key, value)`
- `node_utils/data_nodes.py::DataNodes.set_internal_json` — was `(cls, key, payload)`
- `node_utils/data_nodes.py::DataNodes.set_internal_string` — was `(cls, key, value)`

## Added (15)

- `anim_utils/key_stash/_key_stash.py::KeyStash.discard_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/key_stash/_key_stash.py::KeyStash.merge_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/shots/_shots.py::BlenderShotStore.merge_carrier(cls, carriers, other, ctx) -> None`
- `anim_utils/shots/_shots.py::BlenderShotStore.transfer_in(cls, payload: Dict[str, Any], ctx: 'ptk.TransferContext') -> None`
- `anim_utils/shots/_shots.py::BlenderShotStore.transfer_out(cls, ctx: 'ptk.TransferContext') -> Optional[Dict[str, Any]]`
- `core_utils/_core_utils.py::CoreUtils.all_ids(library=None) -> list`
- `env_utils/fbx_utils.py::FbxUtils.scene_range_take(cls, options: dict) -> list`
- `env_utils/maya_bridge/templates/_import_scene.py::scene_data_sections(cmds, spell)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::scene_data_sections(cmds, spell)`
- `env_utils/maya_bridge/templates/_save_scene.py::rebuild_scene_data(engine, new_nodes)`
- `env_utils/maya_bridge/templates/import.py::rebuild_scene_data(new_nodes)`
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_in(cls, payload: dict, ctx: 'ptk.TransferContext') -> None`
- `mat_utils/emissive_groups.py::EmissiveGroups.transfer_out(cls, ctx: 'ptk.TransferContext') -> Optional[dict]`
- `node_utils/data_nodes.py::DataNodes.carriers_in(cls, library) -> Dict[ptk.Scope, '_Carrier']`
- `node_utils/data_nodes.py::DataNodes.library_renames(before) -> Any`

## Signature changed (8)

- `anim_utils/shots/_shots.py::BlenderShotStore.apply_transfer`
  - was: `(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None) -> Optional['BlenderShotStore']`
  - now: `(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None, ctx: Optional['ptk.TransferContext'] = None) -> Optional['BlenderShotStore']`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.reconcile_system_edits`
  - was: `(self) -> Dict[str, int]`
  - now: `(self, follow: bool = True) -> Dict[str, int]`
- `anim_utils/shots/shot_sequencer/clip_motion.py::ClipMotionMixin.scale_attribute_keys`
  - was: `(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> bool`
  - now: `(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float, ledger=None) -> bool`
- `env_utils/_env_utils.py::EnvUtils.make_library_local`
  - was: `(library)`
  - now: `(library, scene_data='merge')`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.import_payload`
  - was: `(self, payload_path: str, *, fbx_options: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, scene_settings: bool = False, reduce_keys: Union[bool, str, None] = False, shots: bool = True, progress: Optional[Callable[[int, int, str], Optional[bool]]] = None) -> List[Any]`
  - now: `(self, payload_path: str, *, fbx_options: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, scene_settings: bool = False, reduce_keys: Union[bool, str, None] = False, scene_data: bool = True, progress: Optional[Callable[[int, int, str], Optional[bool]]] = None) -> List[Any]`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.import_scene`
  - was: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, rig_mode: str = 'auto', scene_settings: Union[bool, str] = 'auto', reduce_keys: Union[bool, str, None] = REDUCE_KEYS_DEFAULT, shots: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, **script_opts: Any) -> List[Any]`
  - now: `(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, rig_mode: str = 'auto', scene_settings: Union[bool, str] = 'auto', reduce_keys: Union[bool, str, None] = REDUCE_KEYS_DEFAULT, scene_data: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, **script_opts: Any) -> List[Any]`
- `env_utils/maya_bridge/templates/_import_scene.py::write_manifest`
  - was: `(entries, visibility, node_types, scene, path, lights=(), skins=None, bones=None, shots=None, rig=None, machinery=None)`
  - now: `(entries, visibility, node_types, scene, path, lights=(), skins=None, bones=None, scene_data=None, rig=None, machinery=None)`
- `env_utils/maya_bridge/templates/_import_scene_usd.py::write_manifest`
  - was: `(cmds, materials=None, shading_groups=None, bones=None, shots=None, rig=None, machinery=None)`
  - now: `(cmds, materials=None, shading_groups=None, bones=None, scene_data=None, rig=None, machinery=None)`
