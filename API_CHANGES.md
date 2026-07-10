# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-07-10._

## Removed (18)

- `uv_utils/uv_transform.py::UvTransformSlots` — was `(class)`
- `uv_utils/uv_transform.py::UvTransformSlots.b023` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b024` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b025` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b026` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b034` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b035` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b036` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.b037` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.header_init` — was `(self, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.open_uv_editor` — was `(self)`
- `uv_utils/uv_transform.py::UvTransformSlots.s041` — was `(self, value, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.tb005` — was `(self, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.tb005_init` — was `(self, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.tb006` — was `(self, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.tb006_init` — was `(self, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.tb008` — was `(self, widget)`
- `uv_utils/uv_transform.py::UvTransformSlots.tb008_init` — was `(self, widget)`

## Added (23)

- `rig_utils/shadow_rig.py::ShadowRig.bake(self, start=None, end=None)`
- `rig_utils/shadow_rig.py::ShadowRig.bake_planes(cls, planes=None, start=None, end=None)`
- `rig_utils/shadow_rig.py::ShadowRig.find_shadow_planes(cls, objects=None)`
- `rig_utils/shadow_rig.py::ShadowRig.refresh_export_metadata(cls)`
- `rig_utils/shadow_rig.py::ShadowRigSlots.b002(self)`
- `uv_utils/shell_xform.py::ShellXformSlots(class)`
- `uv_utils/shell_xform.py::ShellXformSlots.b023(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b024(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b025(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b026(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b034(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b035(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b036(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.b037(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.header_init(self, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.open_uv_editor(self)`
- `uv_utils/shell_xform.py::ShellXformSlots.s041(self, value, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.tb005(self, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.tb005_init(self, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.tb006(self, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.tb006_init(self, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.tb008(self, widget)`
- `uv_utils/shell_xform.py::ShellXformSlots.tb008_init(self, widget)`

## Signature changed (1)

- `light_utils/lightmap_baker/lightmap_baker.py::LightmapBaker.commit_lightmap`
  - was: `(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
  - now: `(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None, uv_rects: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]`
