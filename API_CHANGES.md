# blendertk — API Changes

_Diff vs the last release (origin/main @ c5bb7a0). Generated 2026-08-19._

## Added (17)

- `env_utils/scene_exporter/scene_exporter_slots.py::SceneExporterSlots.b012(self) -> None`
- `mat_utils/marmoset_bridge/_marmoset_bridge.py::MarmosetBridge.baked_texture_dir(cls) -> str`
- `mat_utils/substance_bridge/substance_bridge_slots.py::SubstanceBridgeSlots.live_param_tooltips(self)`
- `uv_utils/texture_transfer.py::TextureTransfer(class)`
- `uv_utils/texture_transfer.py::TextureTransfer.assign_results(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None) -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.auto_source_uv_set(cls, obj) -> str`
- `uv_utils/texture_transfer.py::TextureTransfer.correspondence(cls, target, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None) -> Dict[str, Any]`
- `uv_utils/texture_transfer.py::TextureTransfer.default_output_dir(cls) -> str`
- `uv_utils/texture_transfer.py::TextureTransfer.face_materials(cls, obj) -> Tuple[List[Any], 'np.ndarray']`
- `uv_utils/texture_transfer.py::TextureTransfer.material_constant(material, channel: str) -> Optional[Tuple[float, ...]]`
- `uv_utils/texture_transfer.py::TextureTransfer.material_maps(material) -> Dict[str, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.output_base_dir() -> Optional[str]`
- `uv_utils/texture_transfer.py::TextureTransfer.pair_by_name(targets: Sequence, sources: Sequence) -> Dict[Any, Any]`
- `uv_utils/texture_transfer.py::TextureTransfer.positions_match(cls, a, b, tolerance: float = 0.0001) -> bool`
- `uv_utils/texture_transfer.py::TextureTransfer.resolve_output_dir(cls, entry: Optional[str] = None) -> str`
- `uv_utils/texture_transfer.py::TextureTransfer.topology_matches(cls, a, b) -> Tuple[bool, str]`
- `uv_utils/texture_transfer.py::TextureTransfer.transfer(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_suffix: str = '_TRANSFER') -> Dict[str, Dict[str, str]]`

## Signature changed (2)

- `mat_utils/_mat_utils.py::MatUtils.find_and_copy_textures`
  - was: `(images=None, search_dir=None, dest_dir=None, mode='copy')`
  - now: `(images=None, search_dir=None, dest_dir=None, mode='copy', use_valid_paths=True)`
- `mat_utils/marmoset_bridge/_marmoset_engine.py::MarmosetEngine.send`
  - was: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
  - now: `(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]`
