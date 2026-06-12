# blendertk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

_Generated: 2026-06-12_

## Index

- [`anim_utils/_anim_utils.py`](#anim_utils--_anim_utils) — Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's
- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils) — Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``).
- [`core_utils/_core_utils.py`](#core_utils--_core_utils) — Core blendertk utilities — DCC-environment info + cross-cutting decorators.
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils) — Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
- [`mat_utils/_mat_utils.py`](#mat_utils--_mat_utils) — Material utilities — get/assign/create/select-by-material (mirror of mayatk's ``MatUtils``
- [`node_utils/_node_utils.py`](#node_utils--_node_utils) — Node / datablock utilities — instancing via shared object data.
- [`ui_utils/_ui_utils.py`](#ui_utils--_ui_utils) — UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils) — UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils) — Transform utilities — object-level transform ops (world bbox, freeze, drop-to-grid,

---

<a id="anim_utils--_anim_utils"></a>
### `anim_utils/_anim_utils.py`

Animation utilities — key-timing math over ``fcurve.keyframe_points`` (mirror of mayatk's

- [`get_fcurves(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L46) — All fcurves across the given objects' actions (slot-aware;
- [`shift_keys(objects, offset)`](blendertk/blendertk/anim_utils/_anim_utils.py#L70) — Shift every key of the given objects by ``offset`` frames.
- [`move_keys_to_frame(objects, frame=None, retain_spacing=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L75) — Move the objects' keys so they align to ``frame`` (default: the current frame).
- [`adjust_key_spacing(objects, spacing=1, frame=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L103) — Add (+) or remove (−) ``spacing`` frames of space at ``frame`` (default: the current
- [`align_selected_keyframes(objects, target_frame=None, use_earliest=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L124) — Move the SELECTED keyframes (``select_control_point``, e.g.
- [`set_visibility_keys(objects, visible=True, frame=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L153) — Key viewport + render visibility (``hide_viewport``/``hide_render``) at ``frame``
- [`add_intermediate_keys(objects, step=1.0)`](blendertk/blendertk/anim_utils/_anim_utils.py#L171) — Insert sampled keys every ``step`` frames between each fcurve's first and last key
- [`remove_intermediate_keys(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L200) — Remove every key strictly between each fcurve's first and last (keeps only the
- [`select_keys(objects, time=None, add_to_selection=False)`](blendertk/blendertk/anim_utils/_anim_utils.py#L213) — Select keyframe points (``select_control_point`` — visible in the Dope Sheet /
- [`invert_keys(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L229) — Mirror key times about the center of each object's own key range (reverses the motion).
- [`stagger_keys(objects, spacing=5)`](blendertk/blendertk/anim_utils/_anim_utils.py#L245) — Re-time the objects sequentially: each object's keys start ``spacing`` frames after the
- [`snap_keys(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L262) — Snap every key to whole frames.
- [`scale_keys(objects, factor, pivot=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L270) — Scale key times by ``factor`` about ``pivot`` (defaults to each action's first key).
- [`set_stepped(objects, stepped=True)`](blendertk/blendertk/anim_utils/_anim_utils.py#L286) — Set stepped (CONSTANT) or smooth (BEZIER) interpolation on every key.
- [`delete_keys(objects)`](blendertk/blendertk/anim_utils/_anim_utils.py#L295) — Remove all animation from the given objects.
- [`fit_playback_range(objects=None)`](blendertk/blendertk/anim_utils/_anim_utils.py#L305) — Set the scene frame range to the keyed extent of ``objects`` (or every scene object).
- [`copy_keys(source)`](blendertk/blendertk/anim_utils/_anim_utils.py#L322) — Return the action carrying ``source``'s keys (the copy buffer for :func:`paste_keys`).
- [`paste_keys(objects, action)`](blendertk/blendertk/anim_utils/_anim_utils.py#L328) — Link a COPY of ``action`` to each target (independent keys, mirror of Maya paste).
- **[`class AnimUtils`](blendertk/blendertk/anim_utils/_anim_utils.py#L345)** — Namespace mirror (helpers also exposed module-level).

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
- [`get_recent_files(index=None)`](blendertk/blendertk/core_utils/_core_utils.py#L103) — Recently-opened .blend paths, most recent first (mirror of ``mtk.get_recent_files``).
- [`get_recent_autosave(filter_time=24, timestamp_format='%H:%M:%S')`](blendertk/blendertk/core_utils/_core_utils.py#L121) — Recent autosave .blend files as ``(path, timestamp)`` pairs, newest first
- **[`class CoreUtils(ptk.CoreUtils)`](blendertk/blendertk/core_utils/_core_utils.py#L152)** — Blender ``CoreUtils`` — extends pythontk's DCC-agnostic ``CoreUtils`` (mirrors

<a id="edit_utils--_edit_utils"></a>
### `edit_utils/_edit_utils.py`

Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,

- [`decimate(objects, percentage=50.0, preserve_quads=True, symmetry=False, apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L52) — Reduce mesh density via a Decimate (COLLAPSE) modifier — mirror of ``mtk.EditUtils.decimate``.
- [`dissolve_coplanar(objects, angle_tolerance=1.0, apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L75) — Dissolve near-coplanar faces via a Decimate (PLANAR) modifier — mirror of
- [`triangulate(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L89) — Triangulate all faces of the given mesh object(s) (bmesh, headless).
- [`tris_to_quads(objects, angle=40.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L97) — Merge adjacent triangles back into quads where the face/shape angle is within ``angle``
- [`subdivide_mesh(objects, cuts=1)`](blendertk/blendertk/edit_utils/_edit_utils.py#L115) — Subdivide every edge ``cuts`` times, grid-filling faces (bmesh, headless) — "Add Divisions".
- [`boolean_op(objects, operation='DIFFERENCE', apply=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L126) — Boolean the first mesh by the remaining ones via Boolean modifiers (the §5 map for
- [`set_subdivision(objects, viewport_levels=None, render_levels=None, ensure=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L143) — Set Subdivision-Surface levels on the given mesh object(s), kept **live** (non-destructive
- [`set_shading(objects, smooth=True)`](blendertk/blendertk/edit_utils/_edit_utils.py#L167) — Set smooth (averaged vertex normals) or flat (face normals) shading on all faces — the
- [`set_edge_hardness(objects, angle=30.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L178) — Smooth-shade, then mark edges **sharp** where the dihedral angle ≥ ``angle`` degrees —
- [`flip_normals(objects)`](blendertk/blendertk/edit_utils/_edit_utils.py#L196) — Reverse face winding / normals (bmesh ``reverse_faces``, headless).
- [`recalculate_normals(objects, inside=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L204) — Recalculate consistent face normals, outward by default / inward if ``inside`` (bmesh).
- [`clean_geometry(objects, *, merge=True, merge_distance=0.0001, delete_loose=True, degenerate=True, recalculate=True, fill_holes=False)`](blendertk/blendertk/edit_utils/_edit_utils.py#L218) — Clean mesh geometry — merge doubles, dissolve degenerate (zero-area) faces, remove loose
- [`crease_edges(objects, amount=10.0)`](blendertk/blendertk/edit_utils/_edit_utils.py#L261) — Set Subdivision-Surface edge crease on the given mesh object(s) — mirror of Maya's
- **[`class EditUtils`](blendertk/blendertk/edit_utils/_edit_utils.py#L289)** — Namespace mirror of mayatk's ``EditUtils`` (helpers also exposed module-level).

<a id="mat_utils--_mat_utils"></a>
### `mat_utils/_mat_utils.py`

Material utilities — get/assign/create/select-by-material (mirror of mayatk's ``MatUtils``

- [`get_mats(objects)`](blendertk/blendertk/mat_utils/_mat_utils.py#L15) — Unique materials assigned to the given object(s), in slot order.
- [`create_mat(mat_type='standard', name='')`](blendertk/blendertk/mat_utils/_mat_utils.py#L25) — Create a new material (mirror of ``mtk.MatUtils.create_mat``).
- [`assign_mat(objects, material)`](blendertk/blendertk/mat_utils/_mat_utils.py#L47) — Assign ``material`` to the given object(s) — whole-object assignment (all slots).
- [`find_by_mat_id(material, objects=None)`](blendertk/blendertk/mat_utils/_mat_utils.py#L61) — Objects using ``material`` (mirror of ``mtk.find_by_mat_id`` at the object level).
- [`select_by_material(material, add=False)`](blendertk/blendertk/mat_utils/_mat_utils.py#L76) — Select every scene object using ``material`` (optionally adding to the selection).
- [`reload_textures()`](blendertk/blendertk/mat_utils/_mat_utils.py#L91) — Reload every image datablock from disk (mirror of ``mtk.MatUtils.reload_textures``).
- **[`class MatUtils`](blendertk/blendertk/mat_utils/_mat_utils.py#L103)** — Namespace mirror of mayatk's ``MatUtils`` (helpers also exposed module-level).

<a id="node_utils--_node_utils"></a>
### `node_utils/_node_utils.py`

Node / datablock utilities — instancing via shared object data.

- [`get_instances(objects=None)`](blendertk/blendertk/node_utils/_node_utils.py#L24) — Return objects that share their data with another object (Maya-style instances).
- [`replace_with_instances(objects, freeze_transforms=False, center_pivot=False, delete_history=False)`](blendertk/blendertk/node_utils/_node_utils.py#L41) — Make ``objects[1:]`` instances of ``objects[0]`` by sharing its data — Blender's linked
- [`uninstance(objects)`](blendertk/blendertk/node_utils/_node_utils.py#L71) — Break the instance link — make each object's data single-user (mirror of ``mtk.uninstance``).
- **[`class NodeUtils`](blendertk/blendertk/node_utils/_node_utils.py#L84)** — Namespace mirror of mayatk's ``NodeUtils`` (instance helpers also exposed module-level).

<a id="ui_utils--_ui_utils"></a>
### `ui_utils/_ui_utils.py`

UI utilities — opening Blender editors (the analogue of Maya's editor-window mel commands).

- [`get_editor_types()`](blendertk/blendertk/ui_utils/_ui_utils.py#L40) — The friendly-name → ``Area.ui_type`` map understood by :func:`open_editor`.
- [`open_editor(editor)`](blendertk/blendertk/ui_utils/_ui_utils.py#L45) — Open ``editor`` (a friendly name from :data:`EDITOR_TYPES` or a raw ``ui_type``)
- **[`class UiUtils`](blendertk/blendertk/ui_utils/_ui_utils.py#L68)** — Namespace mirror (helpers also exposed module-level).

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``

- [`move_uvs(objects, du=0.0, dv=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L43) — Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
- [`transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0)`](blendertk/blendertk/uv_utils/_uv_utils.py#L88) — Flip and/or rotate (``angle`` degrees, CCW) the UVs of the given mesh object(s) about
- [`pin_uvs(objects, pin=True, selected_only=True)`](blendertk/blendertk/uv_utils/_uv_utils.py#L117) — Pin/unpin UVs (bmesh ``pin_uv``).
- [`get_texel_density(objects, map_size)`](blendertk/blendertk/uv_utils/_uv_utils.py#L161) — Texel density (px per scene unit) of the meshes' faces against a ``map_size`` map —
- [`set_texel_density(objects, density=1.0, map_size=4096)`](blendertk/blendertk/uv_utils/_uv_utils.py#L185) — Scale each object's UVs (about its own UV bbox center) to the target texel density —
- [`delete_extra_uv_sets(objects)`](blendertk/blendertk/uv_utils/_uv_utils.py#L209) — Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets".
- **[`class UvUtils`](blendertk/blendertk/uv_utils/_uv_utils.py#L216)** — Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level).

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
