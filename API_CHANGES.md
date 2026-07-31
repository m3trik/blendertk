# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-07-31._

## Added (5)

- `cam_utils/_cam_utils.py::CamUtils.fit_camera_clipping(objects=None, space=None, buffer=0.25)`
- `cam_utils/_cam_utils.py::CamUtils.get_view_state(space=None)`
- `cam_utils/_cam_utils.py::CamUtils.set_view_state(state)`
- `mat_utils/_mat_utils.py::MatUtils.find_unassigned(objects=None)`
- `uv_utils/_uv_utils.py::UvUtils.get_neighbor_shell_bounds(objects)`

## Signature changed (2)

- `edit_utils/macros.py::DisplayMacros.m_frame`
  - was: `(cls)`
  - now: `(cls, steps: int = 2, adjust_clipping: bool = True) -> None`
- `xform_utils/_xform_utils.py::XformUtils.freeze_transforms`
  - was: `(objects, location=True, rotation=False, scale=True, store=True)`
  - now: `(objects, location=True, rotation=False, scale=True, store=True, instance_strategy='skip')`
