# blendertk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

_Generated: 2026-06-12_

## Index

- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils) — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).
- [`core_utils/_core_utils.py`](#core_utils--_core_utils) — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils) — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- [`node_utils/_node_utils.py`](#node_utils--_node_utils) — Node / datablock utilities — instancing via shared object data.
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils) — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils) — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,

---

<a id="cam_utils--_cam_utils"></a>
### `cam_utils/_cam_utils.py`

Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).

- [`adjust_camera_clipping(camera=None, near_clip=None, far_clip=None)`](blendertk/blendertk/cam_utils/_cam_utils.py#L61) — Adjust near/far clip planes of camera object(s) — mirror of ``mtk.adjust_camera_clipping``.
- **[`class CamUtils`](blendertk/blendertk/cam_utils/_cam_utils.py#L89)** — Namespace mirror of mayatk's ``CamUtils`` (helper also exposed module-level).

<a id="core_utils--_core_utils"></a>
### `core_utils/_core_utils.py`

Core blendertk utilities — DCC-environment info + cross-cutting decorators.

- [`undoable(fn)`](blendertk/blendertk/core_utils/_core_utils.py#L18) — Wrap ``fn`` so its changes collapse into a single Blender undo step.
- [`get_env_info(key=None)`](blendertk/blendertk/core_utils/_core_utils.py#L77) — Return Blender scene / environment info (mirror of ``mtk.get_env_info``).
- **[`class CoreUtils(ptk.CoreUtils)`](blendertk/blendertk/core_utils/_core_utils.py#L103)** — Blender ``CoreUtils`` — extends pythontk's DCC-agnostic ``CoreUtils`` (mirrors

<a id="edit_utils--_edit_utils"></a>
### `edit_utils/_edit_utils.py`

Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,

- [`decimate(objects, percentage=50.0, preserve_quads=True, symmetry=False, apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L52) — Reduce mesh density via a Decimate (COLLAPSE) modifier — mirror of ``mtk.EditUtils.decimate``.
- [`dissolve_coplanar(objects, angle_tolerance=1.0, apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L75) — Dissolve near-coplanar faces via a Decimate (PLANAR) modifier — mirror of
- [`triangulate(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L89) — Triangulate all faces of the given mesh object(s) (bmesh, headless).
- [`tris_to_quads(objects, angle=40.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L97) — Merge adjacent triangles back into quads where the face/shape angle is within ``angle``
- [`subdivide_mesh(objects, cuts=1)`](blendertk/blendertk/edit_utils/_edit_utils.py#L115) — Subdivide every edge ``cuts`` times, grid-filling faces (bmesh, headless) — "Add Divisions".
- [`set_subdivision(objects, viewport_levels=None, render_levels=None, ensure=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L125) — Set Subdivision-Surface levels on the given mesh object(s), kept **live** (non-destructive
- [`set_shading(objects, smooth=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L149) — Set smooth (averaged vertex normals) or flat (face normals) shading on all faces — the
- [`set_edge_hardness(objects, angle=30.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L160) — Smooth-shade, then mark edges **sharp** where the dihedral angle ≥ ``angle`` degrees —
- [`flip_normals(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L178) — Reverse face winding / normals (bmesh ``reverse_faces``, headless).
- [`recalculate_normals(objects, inside=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L186) — Recalculate consistent face normals, outward by default / inward if ``inside`` (bmesh).
- [`clean_geometry(objects, *, merge=True, merge_distance=0.0001, delete_loose=True, degenerate=True, recalculate=True, fill_holes=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L200) — Clean mesh geometry — merge doubles, dissolve degenerate (zero-area) faces, remove loose
- [`crease_edges(objects, amount=10.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L243) — Set Subdivision-Surface edge crease on the given mesh object(s) — mirror of Maya's
- **[`class EditUtils`](blendertk/blendertk/edit_utils/_edit_utils.py#L271)** — Namespace mirror of mayatk's ``EditUtils`` (helpers also exposed module-level).

<a id="node_utils--_node_utils"></a>
### `node_utils/_node_utils.py`

Node / datablock utilities — instancing via shared object data.

- [`get_instances(objects=None)`](blendertk/blendertk/node_utils/_node_utils.py#L24) — Return objects that share their data with another object (Maya-style instances).
- [`replace_with_instances(objects, freeze_transforms=False, center_pivot=False, delete_history=False)`](blendertk/blendertk/node_utils/_node_utils.py#L41) — Make ``objects[1:]`` instances of ``objects[0]`` by sharing its data — Blender's linked
- [`uninstance(objects)`](blendertk/blendertk/node_utils/_node_utils.py#L71) — Break the instance link — make each object's data single-user (mirror of ``mtk.uninstance``).
- **[`class NodeUtils`](blendertk/blendertk/node_utils/_node_utils.py#L84)** — Namespace mirror of mayatk's ``NodeUtils`` (instance helpers also exposed module-level).

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``

- [`move_uvs(objects, du=0.0, dv=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L16) — Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
- [`delete_extra_uv_sets(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L39) — Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets".
- **[`class UvUtils`](blendertk/blendertk/uv_utils/_uv_utils.py#L46)** — Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level).

<a id="xform_utils--_xform_utils"></a>
### `xform_utils/_xform_utils.py`

Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,

- [`get_world_bbox(obj)`](blendertk/blendertk/xform_utils/_xform_utils.py#L17) — Return ``(min, max)`` ``Vector``s of ``obj``'s bounding box in world space.
- [`freeze_transforms(objects, location=True, rotation=False, scale=True)`](blendertk/blendertk/xform_utils/_xform_utils.py#L47) — Apply (bake) the given transform channels into the object data — Blender's
- [`drop_to_grid(objects, align='Min', origin=False, center_pivot=False)`](blendertk/blendertk/xform_utils/_xform_utils.py#L65) — Drop each object so its bbox ``Min`` / ``Mid`` / ``Max`` sits on the ground (Z=0).
- [`center_pivot(objects, mode='object')`](blendertk/blendertk/xform_utils/_xform_utils.py#L93) — Move each object's origin (Blender's single pivot) — mirror of Maya's Center Pivot.
- [`get_pivot_modes()`](blendertk/blendertk/xform_utils/_xform_utils.py#L121) — Center-pivot mode keys understood by :func:`center_pivot`.
- [`match_scale(source, target, average=True)`](blendertk/blendertk/xform_utils/_xform_utils.py#L126) — Uniformly rescale ``source`` object(s) to match ``target``'s bounding-box size.
- [`move_to(source, target, pivot='center')`](blendertk/blendertk/xform_utils/_xform_utils.py#L143) — Move ``source`` object(s) so their pivot aligns with the ``target``'s pivot point.
- **[`class XformUtils`](blendertk/blendertk/xform_utils/_xform_utils.py#L157)** — Namespace mirror of mayatk's ``XformUtils`` (helpers also exposed module-level).
  - `XformUtils.get_pivot_options()` *(static)* — Pivot keys understood by :func:`move_to` (mirror of ``mtk.XformUtils.get_pivot_options``).
