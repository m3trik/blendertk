# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-07-07._

## Signature changed (5)

- `env_utils/maya_bridge/_maya_bridge.py::MayaBridge.maya_path`
  - was: `(self, value: Optional[str]) -> None`
  - now: `(self) -> Optional[str]`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.resolution`
  - was: `(self, value: int) -> None`
  - now: `(self) -> int`
- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.samples`
  - was: `(self, value: int) -> None`
  - now: `(self) -> int`
- `node_utils/attributes/channels/_channels.py::Channels.single_object_mode`
  - was: `(self, value)`
  - now: `(self)`
- `uv_utils/rizom_bridge/_rizom_bridge.py::RizomUVBridge.rizom_path`
  - was: `(self, value)`
  - now: `(self)`
