# blendertk — API Changes

_Diff vs the last release (origin/main @ 7ff40d8)._

## Removed (1)

- `anim_utils/key_stash/_key_stash.py::KeyStash.is_previewing` — was `(self, clip_id: Optional[int] = None) -> bool`

## Added (18)

- `anim_utils/_anim_utils.py::AnimUtils.get_redundant_flat_keys(objects, value_tolerance=1e-05, remove=False, time_range=None, selected_only=False)`
- `anim_utils/_anim_utils.py::AnimUtils.simplify_curve(objects, value_tolerance=0.001, time_range=None, selected_only=False)`
- `anim_utils/shots/_shots.py::BlenderScenePersistence.record_changed(self) -> bool`
- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.on_sub_track_selected(self, rows: list) -> None`
- `env_utils/maya_bridge/_scene_import.py::MayaSceneImport.apply_world(manifest_path: str, hdri: str = '', strength: float = 1.0) -> Dict[str, str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.name_context(self, name_regex: Optional[str] = None) -> Dict[str, str]`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.resolve_export_path(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', name_regex: Optional[str] = None, report: bool = True, version_format: str = '', timestamp: bool = False) -> Dict[str, Any]`
- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.output_name_preview(self) -> str`
- `env_utils/scene_exporter/task_manager.py::TaskManager.begin_run(self, run: ptk.ExportRun) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_output_writable(self) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.create_glb(self, fbx_path: Optional[str] = None, announce: bool = True) -> Optional[str]`
- `env_utils/scene_exporter/task_manager.py::TaskManager.export_path(self) -> str`
- `env_utils/scene_exporter/task_manager.py::TaskManager.publish_clip_mode(self) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.run_tasks(self, tasks: Dict[str, Any]) -> bool`
- `env_utils/scene_exporter/task_manager.py::TaskManager.write_scene_data_sidecar(self, glb_path: Optional[str] = None) -> None`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_color_stops(cls, objects=None, channel='highlight') -> dict`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.preview_channels(cls, objects, channel='highlight', keys=(), colors=None, fps=None) -> dict`
- `node_utils/attributes/channels/_channels.py::Channels.set_key_at_current_time(cls, objects, descriptor, keyed=True)`

## Signature changed (9)

- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.format_export_name`
  - was: `(self, name: str) -> str`
  - now: `(self, name: str, name_regex: Optional[str] = None) -> str`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporter.generate_export_path`
  - was: `(self, version_format: str = '', extension: str = '.fbx') -> str`
  - now: `(self, version_format: str = '', extension: str = '.fbx', output_format: Optional[str] = None) -> str`
- `env_utils/scene_exporter/task_manager.py::TaskManager.apply_declared_takes`
  - was: `(self)`
  - now: `(self, mode: Union[bool, str, None] = 'both')`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_objects_below_floor`
  - was: `(self, enabled, tolerance: float = 0.5) -> tuple`
  - now: `(self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.exclude_hdr`
  - was: `(self, enabled)`
  - now: `(self, *legacy_enabled: bool, enabled: bool = True) -> None`
- `light_utils/lightmap_baker/web_export.py::LightmapWebExport.wired_for_export`
  - was: `(self, objects=None, carrier: str = 'occlusion', percentile: Optional[float] = None) -> Iterator[Optional[Dict[str, Any]]]`
  - now: `(self, objects=None, carrier: str = 'occlusion', percentile: Optional[float] = None, glb_path: Optional[str] = None) -> Iterator[Optional[Dict[str, Any]]]`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_colors`
  - was: `(cls, objects=None, channel='highlight') -> dict`
  - now: `(cls, objects=None, channel='highlight', stop: str = 'hi') -> dict`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.key_pulse`
  - was: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, lead_in=None, lead_out=None, color=None, auto_create=True, channel='highlight', preview=None, delete_visibility_keys=False, whole_frames=True)`
  - now: `(cls, objects=None, start=0, end=100, period=86, bright_fraction=0.59, ramp_fraction=0.25, lead_in=None, lead_out=None, color=None, dim_color=None, auto_create=True, channel='highlight', preview=None, delete_visibility_keys=False, whole_frames=True)`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.set_channel_color`
  - was: `(cls, objects=None, color=None, channel='highlight') -> list`
  - now: `(cls, objects=None, color=None, channel='highlight', stop: str = 'hi') -> list`
