# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-07-19._

## Removed (1)

- `anim_utils/shots/shot_manifest/shot_manifest_slots.py::ShotManifestController.browse_csv` — was `(self) -> None`

## Added (30)

- `cam_utils/_cam_utils.py::navigate_view(mode='ORBIT')`
- `core_utils/auto_instancer/_auto_instancer.py::AutoInstancer.default_summary() -> Dict[str, object]`
- `core_utils/auto_instancer/_auto_instancer.py::AutoInstancer.format_summary(summary: Dict[str, object], output_count: int) -> str`
- `core_utils/script_job_manager.py::ScriptJobManager.suppressed(self, *tokens: Optional[int]) -> Iterator[None]`
- `env_utils/_env_utils.py::create_workspace(root, rules=None, create_dirs=True)`
- `env_utils/_env_utils.py::current_workspace(path=None)`
- `env_utils/_env_utils.py::delete_workspace_template(name)`
- `env_utils/_env_utils.py::list_workspace_templates()`
- `env_utils/_env_utils.py::promote_workspace(root=None)`
- `env_utils/_env_utils.py::save_workspace_template(name, rules)`
- `env_utils/_env_utils.py::scenes_dir(path=None)`
- `env_utils/_env_utils.py::set_current_workspace(root=None)`
- `env_utils/_env_utils.py::source_images_dir(path=None)`
- `env_utils/_env_utils.py::workspace_root(path=None)`
- `env_utils/_env_utils.py::workspace_scenes_dir(root)`
- `env_utils/_env_utils.py::workspace_template_rules(name=None)`
- `env_utils/reference_manager.py::ReferenceManagerSlots.mark_workspace(self)`
- `env_utils/reference_manager.py::ReferenceManagerSlots.new_workspace(self)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots(class)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.add_rule(self)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.clear_rules(self)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.header_init(self, widget)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.open_folder(self)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.remove_row(self, row)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.reset_row(self, row)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.reset_rules(self)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.tbl000_init(self, widget)`
- `env_utils/workspace_editor.py::WorkspaceEditorSlots.txt000_init(self, widget)`
- `rig_utils/shadow_rig.py::ShadowRig.delete(self, delete_textures=False)`
- `rig_utils/shadow_rig.py::ShadowRig.delete_rigs(cls, planes=None, delete_textures=False)`

## Signature changed (2)

- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.move_object_in_shot`
  - was: `(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float, prevent_overlap: bool = False) -> None`
  - now: `(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float) -> None`
- `core_utils/auto_instancer/_auto_instancer.py::auto_instance`
  - was: `(objects: Optional[Sequence[object]] = None, tolerance: float = 0.001, scale_tolerance: Optional[float] = None, require_same_material: Union[bool, int] = True, check_uvs: bool = False, check_hierarchy: bool = False, separate_combined: bool = False, combine_assemblies: bool = True, combine_non_instanced: bool = True, combine_by_material: bool = True, combine_by_distance: bool = True, combine_distance_threshold: float = 10000.0, search_radius_mult: float = 1.5, is_static: bool = True, needs_individual: bool = False, will_be_lightmapped: bool = False, can_gpu_instance: bool = True, verbose: bool = True, log_level: str = 'WARNING') -> List[object]`
  - now: `(objects: Optional[Sequence[object]] = None, tolerance: float = 0.001, scale_tolerance: Optional[float] = None, require_same_material: Union[bool, int] = True, check_uvs: bool = False, check_hierarchy: bool = False, separate_combined: bool = False, combine_assemblies: bool = True, combine_non_instanced: bool = True, combine_by_material: bool = True, combine_by_distance: bool = True, combine_distance_threshold: float = 10000.0, search_radius_mult: float = 1.5, is_static: bool = True, needs_individual: bool = False, will_be_lightmapped: bool = False, can_gpu_instance: bool = True, verbose: bool = True, log_level: str = 'WARNING', return_summary: bool = False) -> Union[List[object], Tuple[List[object], Dict[str, object]]]`
