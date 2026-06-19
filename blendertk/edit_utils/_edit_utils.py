# !/usr/bin/python
# coding=utf-8
"""Mesh-editing utilities — reduce/decimate, coplanar dissolve, triangulate / tris-to-quads,
subdivide, and Subdivision-Surface levels.

``decimate`` / ``dissolve_coplanar`` mirror mayatk's ``EditUtils`` names. The triangulate /
tris-to-quads / subdivide helpers use Blender-idiomatic names (Maya routes these through mel,
so there's no mtk name to mirror). bmesh ops act on ``obj.data`` directly (no edit mode); the
modifier helpers bake via ``modifier_apply`` (guarded into OBJECT mode). All **headless-testable**.

``import bpy`` / ``bmesh`` are deferred into the call bodies (no import side effects).
"""
import math

import pythontk as ptk

from blendertk.core_utils._core_utils import _object_mode  # shared OBJECT-mode guard


def _meshes(objects):
    return [o for o in ptk.make_iterable(objects) if getattr(o, "type", None) == "MESH"]


def _bmesh_edit(obj, fn):
    """Run ``fn(bm)`` against a bmesh built from ``obj``'s data, then write it back (headless)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    fn(bm)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _bmesh_each(objects, fn):
    """Apply the bmesh edit ``fn(bm)`` to every mesh in ``objects``."""
    for o in _meshes(objects):
        _bmesh_edit(o, fn)


def _edit_mesh_each(meshes, fn):
    """Run ``fn(bm, obj) -> int`` against each mesh's **live edit** bmesh, entering/restoring the
    OBJECT↔EDIT round-trip as needed (multi-object edit) and flushing edits per mesh. Returns the
    summed ``fn`` contributions (``fn`` returns the count it produced, ``0`` to skip flushing that
    mesh).

    Use this for ops that act on the user's **component selection** (edges/verts): that selection
    lives on the mesh data and survives the mode toggle, so the same captured components are seen on
    every call (e.g. the Preview path's Object→Edit→Object cycle) — unlike :func:`_bmesh_edit`, which
    rebuilds a detached bmesh from object data and would drop the selection. ``meshes`` must be a
    non-empty list of mesh objects (callers filter via :func:`_meshes` and raise their own
    domain-specific message)."""
    import bpy
    import bmesh

    active = bpy.context.view_layer.objects.active
    prev_mode = active.mode if active else "OBJECT"
    if prev_mode != "EDIT":
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes:
            o.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.object.mode_set(mode="EDIT")
    try:
        total = 0
        for o in meshes:
            n = fn(bmesh.from_edit_mesh(o.data), o)
            if n:
                bmesh.update_edit_mesh(o.data)
                total += n
        return total
    finally:
        if prev_mode != "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")


def _apply_modifier(obj, mod_name):
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod_name)


def hook_bind_inverse(target, obj):
    """The ``matrix_inverse`` a Hook modifier needs so its geometry does **not jump** at bind time.

    A Blender Hook deforms a vertex as ``obj⁻¹ · target · matrix_inverse · obj · v``; for that to be
    the identity at bind (no displacement until the target actually moves), ``matrix_inverse`` must be
    ``target.matrix_world⁻¹ · obj.matrix_world``. Shared by every hook-binder in this package
    (``DynamicPipe`` curve-CP hooks, ``CurtainRig`` mesh-vertex hooks) so the bind stays correct
    regardless of where each object sits — read both ``matrix_world``\\ s *after* a
    ``view_layer.update()`` (they are lazy).

    Args:
        target (bpy.types.Object): The hook's control object (``modifier.object``).
        obj (bpy.types.Object): The object carrying the Hook modifier (curve or mesh).

    Returns:
        mathutils.Matrix: The value to assign to ``modifier.matrix_inverse``.
    """
    return target.matrix_world.inverted() @ obj.matrix_world


def hook_curve_point(curve, point_index, target, name=None, falloff_type="NONE"):
    """Hook control point *point_index* of *curve* to *target* so moving the target moves that point
    live — the curve-control-point bind shared by ``DynamicPipe`` (locator-driven pipe) and
    ``TubeRig`` (Spline-IK driver curve). Rigid by default (``falloff_type='NONE'`` → no radius
    falloff, so the hook ``center`` is irrelevant). Reads ``target``/``curve`` ``matrix_world``, so
    settle the depsgraph (``view_layer.update()``) before calling. Returns the modifier."""
    mod = curve.modifiers.new(name=name or f"Hook_{point_index}", type="HOOK")
    mod.object = target
    mod.falloff_type = falloff_type
    mod.vertex_indices_set([point_index])
    mod.matrix_inverse = hook_bind_inverse(target, curve)
    return mod


@_object_mode
def decimate(objects, percentage=50.0, preserve_quads=True, symmetry=False, apply=True):
    """Reduce mesh density via a Decimate (COLLAPSE) modifier — mirror of ``mtk.EditUtils.decimate``.

    ``percentage`` is the percent of detail to REMOVE (Maya polyReduce semantics) → modifier
    ``ratio = 1 - percentage/100``. ``preserve_quads`` keeps quads (skips collapse-triangulate);
    ``symmetry`` reduces symmetrically about X. ``apply`` bakes the modifier (destructive, matching
    Maya's ``replaceOriginal``); pass ``False`` to keep it live.
    """
    for o in _meshes(objects):
        mod = o.modifiers.new(name="Decimate", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(0.0, min(1.0, 1.0 - percentage / 100.0))
        mod.use_collapse_triangulate = not preserve_quads
        if symmetry:
            mod.use_symmetry = True
            mod.symmetry_axis = "X"
        if apply:
            _apply_modifier(o, mod.name)


@_object_mode
def dissolve_coplanar(objects, angle_tolerance=1.0, delimit=None, preserve_borders=True, apply=True):
    """Dissolve near-coplanar faces via a Decimate (PLANAR) modifier — mirror of
    ``mtk.EditUtils.dissolve_coplanar``. ``angle_tolerance`` is the max dihedral angle (degrees)
    treated as coplanar (~0 is lossless on hard-surface). ``delimit`` is the set of boundaries the
    dissolve must NOT cross (the planar Decimate ``delimit`` flags — any of ``NORMAL`` /
    ``MATERIAL`` / ``SEAM`` / ``SHARP`` / ``UV``), so hard edges / UV island borders survive.
    ``preserve_borders`` (default True) holds open mesh boundaries fixed (the Blender analogue of
    Maya reduce's *Keep Border* — it drives the modifier's ``use_dissolve_boundaries`` inverse).
    ``apply`` bakes the modifier.
    """
    for o in _meshes(objects):
        mod = o.modifiers.new(name="Decimate", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = math.radians(angle_tolerance)
        mod.use_dissolve_boundaries = not preserve_borders
        if delimit:
            mod.delimit = set(delimit)
        if apply:
            _apply_modifier(o, mod.name)


@_object_mode
def triangulate(objects):
    """Triangulate all faces of the given mesh object(s) (bmesh, headless)."""
    import bmesh

    _bmesh_each(objects, lambda bm: bmesh.ops.triangulate(bm, faces=bm.faces[:]))


@_object_mode
def tris_to_quads(objects, angle=40.0):
    """Merge adjacent triangles back into quads where the face/shape angle is within ``angle``
    degrees (bmesh ``join_triangles``, headless). Blender-idiomatic name for "quadrangulate"."""
    import bmesh

    limit = math.radians(angle)

    def _join(bm):
        bmesh.ops.join_triangles(
            bm, faces=bm.faces[:],
            angle_face_threshold=limit, angle_shape_threshold=limit,
            cmp_seam=False, cmp_sharp=False, cmp_uvs=False, cmp_materials=False,
        )

    _bmesh_each(objects, _join)


@_object_mode
def subdivide_mesh(objects, cuts=1):
    """Subdivide every edge ``cuts`` times, grid-filling faces (bmesh, headless) — "Add Divisions"."""
    import bmesh

    def _sub(bm):
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)

    _bmesh_each(objects, _sub)


@_object_mode
def boolean_op(objects, operation="DIFFERENCE", apply=True):
    """Boolean the first mesh by the remaining ones via Boolean modifiers (the §5 map for
    Maya's boolean macros). ``operation``: ``UNION`` / ``DIFFERENCE`` / ``INTERSECT``.
    ``apply`` bakes each modifier (destructive, matching Maya). Returns the base object."""
    objs = _meshes(objects)
    if len(objs) < 2:
        return None
    base, operands = objs[0], objs[1:]
    for operand in operands:
        mod = base.modifiers.new(name="Boolean", type="BOOLEAN")
        mod.operation = operation
        mod.object = operand
        if apply:
            _apply_modifier(base, mod.name)
    return base


def set_subdivision(objects, viewport_levels=None, render_levels=None, ensure=True):
    """Set Subdivision-Surface levels on the given mesh object(s), kept **live** (non-destructive
    smooth preview — the Blender analogue of Maya's smooth-mesh preview / smoothLevel).

    Ensures a Subsurf modifier exists (unless ``ensure=False``) and sets the given viewport /
    render levels. Returns the objects changed.
    """
    changed = []
    for o in _meshes(objects):
        mod = next((m for m in o.modifiers if m.type == "SUBSURF"), None)
        if mod is None:
            if not ensure:
                continue
            mod = o.modifiers.new(name="Subdivision", type="SUBSURF")
        if viewport_levels is not None:
            mod.levels = int(viewport_levels)
        if render_levels is not None:
            mod.render_levels = int(render_levels)
        changed.append(o)
    return changed


# ---------------------------------------------------------------- normals / shading
@_object_mode
def set_shading(objects, smooth=True):
    """Set smooth (averaged vertex normals) or flat (face normals) shading on all faces — the
    Blender analogue of Maya soften/harden-all and set-normals-to-face (bmesh, headless)."""
    def _f(bm):
        for face in bm.faces:
            face.smooth = smooth

    _bmesh_each(objects, _f)


def _edge_is_uv_seam(edge, uv_layer):
    """True when ``edge`` lies on a UV-island boundary — the UV coords of its shared verts differ
    between the two adjacent faces (the standard UV-seam test). Interior, exactly-two-face edges only."""
    if len(edge.link_faces) != 2:
        return False
    f1, f2 = edge.link_faces
    for v in edge.verts:
        uv1 = next((loop[uv_layer].uv for loop in f1.loops if loop.vert is v), None)
        uv2 = next((loop[uv_layer].uv for loop in f2.loops if loop.vert is v), None)
        if uv1 is None or uv2 is None:
            return False
        if (uv1 - uv2).length > 1e-6:
            return True
    return False


def average_normals(objects, by_uv_shell=False):
    """Average vertex normals by softening edges — Blender mirror of ``mtk.Components.average_normals``
    (Maya's ``polySoftEdge a=180``). Softens all faces/edges (smooth shading — normals are averaged
    across shared faces). With ``by_uv_shell`` the edges on UV-island boundaries stay sharp, so each
    UV shell is smoothed independently (smooth within islands, hard at the UV seams — the common
    game-art normal setup). bmesh on ``obj.data`` (headless). Returns the count processed."""
    import bmesh

    count = 0
    for o in _meshes(objects):
        me = o.data
        bm = bmesh.new()
        bm.from_mesh(me)
        for face in bm.faces:
            face.smooth = True
        for edge in bm.edges:
            edge.smooth = True  # smooth == not sharp
        uv = bm.loops.layers.uv.active
        if by_uv_shell and uv is not None:
            for edge in bm.edges:
                if _edge_is_uv_seam(edge, uv):
                    edge.smooth = False  # keep UV-island boundaries hard
        bm.to_mesh(me)
        bm.free()
        me.update()
        count += 1
    return count


def select_edges_by_angle(objects, low_angle=0.0, high_angle=180.0):
    """Select interior edges whose dihedral (face) angle is within ``[low_angle, high_angle]``
    degrees, on the given mesh object(s) — the Blender analogue of Maya's Select-Edges-By-Angle
    *range* (native ``mesh.edges_select_sharp`` takes only a single lower threshold).

    ``objects`` must already be in Edit Mode (the ``selection`` slot's ``_edit_mesh('EDGE')``
    ensures this and supplies the active object — the edit-mode object set is passed in rather than
    read from ``bpy.context``, which is unreliable from a deferred call). Boundary edges (not exactly
    two faces) are excluded. Replaces the current edge selection; returns the edges selected."""
    import bmesh

    lo, hi = math.radians(low_angle), math.radians(high_angle)
    total = 0
    for obj in _meshes(objects):
        if obj.mode != "EDIT":
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        bm.select_mode = {"EDGE"}
        for seq in (bm.verts, bm.edges, bm.faces):
            for el in seq:
                el.select = False
        for e in bm.edges:
            if len(e.link_faces) == 2 and lo <= e.calc_face_angle() <= hi:
                e.select = True
                total += 1
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
    return total


@_object_mode
def set_edge_hardness(objects, angle=30.0, upper_hardness=0, lower_hardness=180):
    """Smooth-shade, then mark interior edges hard/soft by their dihedral angle relative to
    ``angle`` — Blender split normals follow sharp edges automatically (mirror of ``mtk``
    set-normals-by-angle). Boundary edges (one face) are left untouched. bmesh, headless.

    Edges with angle ≥ ``angle`` get ``upper_hardness``; edges below get ``lower_hardness``.
    Blender edges are binary sharp/smooth, so the Maya 0..180 hardness *value* collapses to
    hard (< 90) / soft (≥ 90); ``None`` (the Maya -1 "disable") leaves that bucket as-is. The
    defaults (upper 0 = hard, lower 180 = soft) reproduce plain mark-sharp-by-angle.
    """
    limit = math.radians(angle)

    def _smooth_for(hardness):  # -> edge.smooth value, or None to leave the edge unchanged
        if hardness is None:
            return None
        return hardness >= 90.0  # smooth (soft) when leaning soft, sharp (hard) when leaning hard

    upper = _smooth_for(upper_hardness)
    lower = _smooth_for(lower_hardness)

    def _f(bm):
        for face in bm.faces:
            face.smooth = True
        for edge in bm.edges:
            if len(edge.link_faces) != 2:
                continue
            target = upper if edge.calc_face_angle() >= limit else lower
            if target is not None:
                edge.smooth = target

    _bmesh_each(objects, _f)


@_object_mode
def clear_custom_split_normals(objects):
    """Clear custom split normals on the given mesh object(s) — the Blender analogue of Maya's
    "unlock normals". Imported assets (FBX / Marmoset) carry custom split normals that override
    smooth/flat shading and silently block a re-smoothing pass, so this is run first by the
    set-normals-by-angle path. Returns the number of meshes cleared.
    """
    import bpy

    cleared = 0
    for o in _meshes(objects):
        bpy.context.view_layer.objects.active = o
        try:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
            cleared += 1
        except RuntimeError:
            pass  # no custom normals to clear
    return cleared


@_object_mode
def flip_normals(objects):
    """Reverse face winding / normals (bmesh ``reverse_faces``, headless)."""
    import bmesh

    _bmesh_each(objects, lambda bm: bmesh.ops.reverse_faces(bm, faces=bm.faces[:]))


@_object_mode
def recalculate_normals(objects, inside=False):
    """Recalculate consistent face normals, outward by default / inward if ``inside`` (bmesh)."""
    import bmesh

    def _f(bm):
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        if inside:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

    _bmesh_each(objects, _f)


# ---------------------------------------------------------------- cleanup
@_object_mode
def clean_geometry(
    objects, *, merge=True, merge_distance=0.0001, delete_loose=True, degenerate=True,
    recalculate=True, fill_holes=False,
):
    """Clean mesh geometry — merge doubles, dissolve degenerate (zero-area) faces, remove loose
    edges/verts, recalc normals, optionally fill holes. Mirror of ``mtk`` clean-geometry (bmesh,
    headless). ``merge`` gates the doubles merge independently of ``degenerate`` —
    ``merge_distance`` feeds both thresholds (floored for degenerate so exact-zero geometry
    still dissolves when merging is off). Returns the meshes processed.
    """
    import bmesh

    def _clean(bm):
        if merge and merge_distance > 0:
            bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=merge_distance)
        if degenerate:
            bmesh.ops.dissolve_degenerate(
                bm, dist=max(merge_distance, 1e-6), edges=bm.edges[:]
            )
        if delete_loose:
            wire_edges = [e for e in bm.edges if not e.link_faces]
            if wire_edges:
                bmesh.ops.delete(bm, geom=wire_edges, context="EDGES")
            loose_verts = [v for v in bm.verts if not v.link_edges]
            if loose_verts:
                bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
        if fill_holes:
            bmesh.ops.holes_fill(bm, edges=bm.edges[:])
        if recalculate:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

    processed = _meshes(objects)
    _bmesh_each(processed, _clean)
    return processed


# NOTE: ``find_problem_geometry`` (+ its ``_is_convex`` / ``_is_planar`` helpers) moved to
# ``core_utils/diagnostics/mesh_diag.py`` (``MeshDiagnostics``) — mirror of mayatk, where mesh
# problem-detection lives in ``Diagnostics`` not ``EditUtils``. ``btk.find_problem_geometry`` still
# resolves (registered from there); ``mesh_diag`` imports ``_bmesh_each`` / ``_object_mode`` here.


# ---------------------------------------------------------------- crease (subsurf edge crease)
def _crease_layer(bm):
    """The bmesh edge-crease float layer (Blender 4.0+ moved it to the ``crease_edge`` attribute,
    so the legacy ``bm.edges.layers.crease`` no longer exists)."""
    return bm.edges.layers.float.get("crease_edge") or bm.edges.layers.float.new("crease_edge")


def crease_edges(objects, amount=10.0, angle=None):
    """Set Subdivision-Surface edge crease on the given mesh object(s) — mirror of Maya's
    ``crease_edges(amount, angle)``. ``amount`` is the Maya 0–10 scale, normalized to Blender's
    0–1 crease (``polyCrease``).

    When ``angle`` (degrees) is given it also softens/hardens the same edges by it — the Blender
    equivalent of Maya's ``polySoftEdge -angle``: an edge whose dihedral angle **exceeds** ``angle``
    is marked sharp (hard), otherwise smooth (soft). So ``angle=0`` hardens every edge and
    ``angle=180`` softens every edge; boundary/wire edges (no dihedral) are left untouched.

    **Mode-aware** (so it is NOT ``@_object_mode``-guarded): in EDIT mode it acts on the *selected*
    edges (the marking-menu workflow); in OBJECT mode it acts on all edges of the object.
    """
    import bmesh

    value = max(0.0, min(1.0, amount / 10.0))
    threshold = None if angle is None else math.radians(angle)

    def _apply(bm, selected_only):
        # fetch (creating if needed) the crease layer BEFORE touching edges — adding a custom
        # data layer invalidates any pre-collected BMEdge references.
        cl = _crease_layer(bm)
        for e in bm.edges:
            if selected_only and not e.select:
                continue
            e[cl] = value
            if threshold is not None:
                try:
                    e.smooth = e.calc_face_angle() <= threshold
                except ValueError:
                    pass  # boundary / wire edge has no dihedral angle — leave it

    for o in _meshes(objects):
        if o.mode == "EDIT":
            bm = bmesh.from_edit_mesh(o.data)
            _apply(bm, selected_only=True)
            bmesh.update_edit_mesh(o.data)
        else:
            _bmesh_edit(o, lambda bm: _apply(bm, selected_only=False))
    return value


# ---------------------------------------------------------------- mirror / cut-on-axis
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _plane_frame(obj, axis, pivot):
    """World-space ``(point, unit normal)`` of the mirror/cut plane for ``axis`` about ``pivot``.

    ``pivot``: ``"object"`` (object origin, **object-local** axis), ``"world"`` (world origin),
    ``"center"`` (world-bbox center), ``"xmin"`` / ``"xmax"`` / … (that world-bbox face), or a
    world ``(x, y, z)`` point. All but ``"object"`` use world axes. The normal is the unsigned
    +axis direction — callers carry the axis sign separately (it only picks a side, never the
    plane itself).
    """
    from mathutils import Vector

    from blendertk.xform_utils._xform_utils import get_world_bbox

    base = str(axis).lstrip("-").lower()
    idx = _AXIS_INDEX[base]
    n = Vector((0.0, 0.0, 0.0))
    n[idx] = 1.0

    if pivot == "object":
        m3 = obj.matrix_world.to_3x3()
        return obj.matrix_world.translation.copy(), (m3 @ n).normalized()
    if isinstance(pivot, (tuple, list)) and len(pivot) == 3:
        return Vector(pivot), n
    if pivot == "world":
        return Vector((0.0, 0.0, 0.0)), n
    mn, mx = get_world_bbox(obj)
    p = (mn + mx) / 2.0
    if isinstance(pivot, str) and len(pivot) == 4 and pivot[1:] in ("min", "max"):
        face_idx = _AXIS_INDEX[pivot[0]]
        p[face_idx] = (mn if pivot.endswith("min") else mx)[face_idx]
    return p, n  # "center" (or unknown -> bbox center)


def _local_reflection(obj, point, normal):
    """The world reflection across plane ``(point, normal)`` expressed in ``obj``-local coords
    (exact under any object matrix, including non-uniform scale)."""
    from mathutils import Matrix

    m = obj.matrix_world
    refl_w = (
        Matrix.Translation(point)
        @ Matrix.Scale(-1.0, 4, normal)
        @ Matrix.Translation(-point)
    )
    return m.inverted() @ refl_w @ m


def _local_bisect_plane(obj, point, normal):
    """World plane -> ``obj``-local ``(plane_co, plane_no)`` for ``bmesh.ops.bisect_plane``
    (plane normals are covectors: local normal = M3ᵀ·world normal, side-preserving)."""
    m = obj.matrix_world
    return m.inverted() @ point, (m.to_3x3().transposed() @ normal).normalized()


def _duplicate_reflect(bm, refl, weld=None, threshold=1e-3):
    """Duplicate all geometry in ``bm`` and reflect the copy by the local matrix ``refl``
    (reversing the copy's winding). ``weld=(point, normal)`` (local) merges verts within
    ``threshold`` of that plane — the mirror seam."""
    import bmesh

    dup = bmesh.ops.duplicate(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:])["geom"]
    verts = [g for g in dup if isinstance(g, bmesh.types.BMVert)]
    faces = [g for g in dup if isinstance(g, bmesh.types.BMFace)]
    for v in verts:
        v.co = refl @ v.co
    bmesh.ops.reverse_faces(bm, faces=faces)
    if weld is not None:
        p, n = weld
        near = [v for v in bm.verts if abs((v.co - p).dot(n)) <= threshold]
        bmesh.ops.remove_doubles(bm, verts=near, dist=threshold)


@_object_mode
def mirror(
    objects,
    axis="x",
    pivot="object",
    merge_mode=1,
    delete_original=False,
    uninstance=False,
    merge_threshold=0.001,
):
    """Mirror mesh object(s) across an axis plane — mirror of ``mtk.EditUtils.mirror``.

    ``merge_mode``: ``-1`` the mirrored half becomes a **separate object**
    (``<name>_mirror``); ``0`` mirrored into the same mesh, seam left unwelded;
    ``1`` same mesh with seam verts welded (``merge_threshold``).
    ``delete_original`` (merge_mode ``-1`` only): remove the source object afterwards.
    ``uninstance``: make shared mesh data single-user first. The bounding-box *center*
    pivot (symmetrize) is not handled here — route it through
    :func:`cut_along_axis` ``(delete=True, mirror=True)`` like the Maya slot does.
    Returns the created objects (merge_mode ``-1``) or the modified sources.
    """
    import bpy

    out = []
    for obj in _meshes(objects):
        if uninstance and obj.data.users > 1:
            obj.data = obj.data.copy()
        point, normal = _plane_frame(obj, axis, pivot)
        refl = _local_reflection(obj, point, normal)
        if merge_mode < 0:

            def _reflect_all(bm, refl=refl):
                import bmesh

                for v in bm.verts:
                    v.co = refl @ v.co
                bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

            new = obj.copy()
            new.data = obj.data.copy()
            new.name = f"{obj.name}_mirror"
            for coll in obj.users_collection:
                coll.objects.link(new)
            _bmesh_edit(new, _reflect_all)
            out.append(new)
            if delete_original:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:  # don't leak an orphaned datablock
                    bpy.data.batch_remove([data])
        else:
            weld = _local_bisect_plane(obj, point, normal) if merge_mode >= 1 else None
            _bmesh_edit(
                obj,
                lambda bm, r=refl, w=weld: _duplicate_reflect(
                    bm, r, weld=w, threshold=merge_threshold
                ),
            )
            out.append(obj)
    return out


@_object_mode
def cut_along_axis(
    objects,
    axis="x",
    pivot="center",
    amount=1,
    offset=0.0,
    invert=False,
    delete=False,
    mirror=False,
    merge_threshold=1e-4,
):
    """Cut mesh object(s) along an axis — mirror of ``mtk.EditUtils.cut_along_axis``.

    ``amount`` evenly spaced cuts (``span/(amount+1)`` apart) centered on the pivot
    (+ ``offset`` along the signed axis). ``delete`` clears the **+axis** side beyond the
    deepest cut (Maya convention: ``"x"`` deletes the +X half — slots invert the UI sign).
    ``mirror`` (with ``delete``) reflects the surviving half across that cut plane and
    welds the seam — the bounding-box-center *symmetrize* used by the Mirror panel.
    """
    import bmesh
    from mathutils import Vector

    if invert:
        axis = axis[1:] if str(axis).startswith("-") else f"-{axis}"
    sign = -1.0 if str(axis).startswith("-") else 1.0
    if amount < 1:
        return

    for obj in _meshes(objects):
        point, normal = _plane_frame(obj, axis, pivot)
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        dots = [(c - point).dot(normal) for c in corners]
        span = max(dots) - min(dots)
        if span <= 0:
            continue
        spacing = span / (amount + 1)
        rel = [
            offset * sign - ((amount - 1) * spacing / 2) + spacing * i
            for i in range(amount)
        ]

        def _cut(bm, obj=obj, rel=rel, point=point, normal=normal):
            for d in rel:
                p_l, n_l = _local_bisect_plane(obj, point + d * normal, normal)
                bmesh.ops.bisect_plane(
                    bm,
                    geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
                    plane_co=p_l,
                    plane_no=n_l,
                    clear_inner=False,
                    clear_outer=False,
                )
            if delete:
                deepest = max(rel) if sign > 0 else min(rel)
                p_deep = point + deepest * normal
                p_l, n_l = _local_bisect_plane(obj, p_deep, normal * sign)
                bmesh.ops.bisect_plane(
                    bm,
                    geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
                    plane_co=p_l,
                    plane_no=n_l,
                    clear_inner=False,
                    clear_outer=True,  # clears the side plane_no points to (the signed axis)
                )
                if mirror:
                    refl = _local_reflection(obj, p_deep, normal)
                    _duplicate_reflect(
                        bm,
                        refl,
                        weld=_local_bisect_plane(obj, p_deep, normal),
                        threshold=max(merge_threshold, 1e-5),
                    )

        _bmesh_edit(obj, _cut)


# ---------------------------------------------------------------- wedge / snap-closest-verts
def wedge(objects, angle=90.0, divisions=4):
    """Wedge the selected faces about a selected hinge edge — mirror of Maya's
    ``WedgePolygon`` (``bmesh.ops.spin``: the faces sweep ``angle`` degrees around the edge
    in ``divisions`` steps, extruding the arc).

    **Edit-mode workflow** (mode-aware, like ``crease_edges``): select the face(s) plus one
    edge of those faces (the active edge wins when several are selected). The sweep is
    oriented to rotate the faces *outward* along their average normal. Returns the number
    of meshes wedged.
    """
    import bmesh
    from mathutils import Matrix, Vector

    wedged = 0
    for o in _meshes(objects):
        if o.mode != "EDIT":
            continue
        bm = bmesh.from_edit_mesh(o.data)
        faces = [f for f in bm.faces if f.select]
        active = bm.select_history.active
        hinge = None
        # the hinge must bound a selected face (Maya: "edges from the selected faces") —
        # an active edge elsewhere on the mesh is not a valid pivot
        if isinstance(active, bmesh.types.BMEdge) and any(
            f.select for f in active.link_faces
        ):
            hinge = active
        if hinge is None:  # fall back to any selected edge bounding a selected face
            hinge = next(
                (e for e in bm.edges if e.select and any(f.select for f in e.link_faces)),
                None,
            )
        if not faces or hinge is None:
            continue
        cent = (hinge.verts[0].co + hinge.verts[1].co) / 2.0
        axis = (hinge.verts[1].co - hinge.verts[0].co).normalized()
        # orient the sweep outward: a small probe rotation of the face center should move
        # WITH the average face normal, not against it
        face_center = sum((f.calc_center_median() for f in faces), Vector()) / len(faces)
        normal = sum((f.normal for f in faces), Vector())
        probe = (Matrix.Rotation(0.1, 4, axis) @ (face_center - cent)) + cent
        if (probe - face_center).dot(normal) < 0:
            axis = -axis
        bmesh.ops.spin(
            bm,
            geom=faces,
            cent=cent,
            axis=axis,
            angle=math.radians(angle),
            steps=max(int(divisions), 1),
            use_duplicate=False,
        )
        # the hinge verts sit ON the spin axis, so each step duplicates them in place,
        # leaving degenerate zero-area quads along the hinge — weld everything on the axis
        on_axis = [
            v
            for v in bm.verts
            if ((v.co - cent) - (v.co - cent).dot(axis) * axis).length <= 1e-6
        ]
        if on_axis:
            bmesh.ops.remove_doubles(bm, verts=on_axis, dist=1e-6)
        bmesh.update_edit_mesh(o.data)
        wedged += 1
    return wedged


@_object_mode
def snap_closest_verts(obj_a, obj_b, tolerance=10.0):
    """Snap each vertex of ``obj_a`` onto the closest vertex of ``obj_b`` within
    ``tolerance`` (world units) — mirror of ``mtk.EditUtils.snap_closest_verts``.
    KD-tree lookup; returns the number of vertices moved. (Maya's ``freeze_transforms``
    flag was a ``cmds`` world-query workaround and is not mirrored — the world math here
    is exact under any transform.)
    """
    from mathutils import kdtree

    verts_b = obj_b.data.vertices
    tree = kdtree.KDTree(len(verts_b))
    mb = obj_b.matrix_world
    for i, v in enumerate(verts_b):
        tree.insert(mb @ v.co, i)
    tree.balance()

    ma = obj_a.matrix_world
    ma_inv = ma.inverted()
    moved = 0
    for v in obj_a.data.vertices:
        co, _i, dist = tree.find(ma @ v.co)
        if co is not None and dist <= tolerance:
            v.co = ma_inv @ co
            moved += 1
    if moved:
        obj_a.data.update()
    return moved


def snap_to_grid(objects=None, grid_size=1.0, axes="xyz"):
    """Snap to the nearest grid point — mirror of ``mtk.Snap.snap_to_grid``. In EDIT mode snaps the
    active mesh's **selected vertices** (world space); otherwise snaps each object's **origin**.
    ``axes`` filters which of x/y/z snap (e.g. ``"xy"``). Returns the number of items snapped.
    """
    import bpy
    from mathutils import Vector

    if grid_size <= 0:
        return 0
    mask = [a in axes.lower() for a in ("x", "y", "z")]

    def _snap(co):
        return Vector(
            [round(c / grid_size) * grid_size if m else c for c, m in zip(co, mask)]
        )

    active = bpy.context.view_layer.objects.active
    if active and active.type == "MESH" and active.mode == "EDIT":
        import bmesh

        bm = bmesh.from_edit_mesh(active.data)
        mw, inv = active.matrix_world, active.matrix_world.inverted()
        moved = 0
        for v in bm.verts:
            if v.select:
                v.co = inv @ _snap(mw @ v.co)
                moved += 1
        if moved:
            bmesh.update_edit_mesh(active.data)
        return moved

    objs = [o for o in (objects if objects is not None else bpy.context.selected_objects) or [] if o]
    for o in objs:
        o.location = _snap(o.location)
    return len(objs)


def snap_to_surface(source_meshes, target, offset=0.0, threshold=None, invert=False):
    """Project the source meshes' vertices onto the closest point of ``target``'s surface —
    mirror of ``mtk.Snap.snap_to_surface`` (uses ``Object.closest_point_on_mesh`` in place of
    Maya's ``MFnMesh.getClosestPoint``). Vertices closer than ``offset`` to the surface are pushed
    out to exactly ``offset`` along the surface normal (signed: a vertex poking *through* the
    surface counts as negative distance and is pushed back out). ``threshold`` skips vertices
    farther than that from the surface (``None`` = no limit); ``invert`` flips the inside/outside
    sense (use when the target's normals point inward). Returns the number of vertices moved.
    """
    sources = [o for o in ptk.make_iterable(source_meshes) if getattr(o, "type", None) == "MESH"]
    if getattr(target, "type", None) != "MESH" or not sources:
        return 0
    offset = offset or 0.0
    tmw = target.matrix_world
    tinv = tmw.inverted()
    tnorm = tmw.to_3x3().inverted().transposed()  # normal-transform matrix (handles non-uniform scale)

    total = 0
    for src in sources:
        smw = src.matrix_world
        sinv = smw.inverted()
        moved = 0
        for v in src.data.vertices:
            world = smw @ v.co
            ok, loc, nrm, _idx = target.closest_point_on_mesh(tinv @ world)
            if not ok:
                continue
            closest = tmw @ loc
            normal = (tnorm @ nrm).normalized()
            to_vert = world - closest
            dist = to_vert.length
            if threshold is not None and dist > threshold:
                continue
            signed = dist if to_vert.dot(normal) >= 0 else -dist
            if invert:
                normal = -normal
                signed = -signed
            if signed < offset:
                v.co = sinv @ (closest + normal * offset)
                moved += 1
        if moved:
            src.data.update()
            total += moved
    return total


# ----------------------------------------------------------------------------
# Object-array primitives — shared by the duplicate_linear / _radial / _grid tools
# (each ships its own co-located panel, but they all build copies the same way, so
# these stay here in the shared edit engine rather than duplicated per tool).
# ----------------------------------------------------------------------------


def _link_like(new, src):
    """Link ``new`` into the same collection(s) as ``src`` (scene root as fallback)."""
    import bpy

    colls = src.users_collection or (bpy.context.scene.collection,)
    for c in colls:
        c.objects.link(new)


def _copy_object(src, instance=True):
    """A linked duplicate (``instance=True`` — shared data) or a full copy of ``src``,
    linked into the same collections."""
    new = src.copy()
    if not instance and src.data is not None:
        new.data = src.data.copy()
    _link_like(new, src)
    return new


def _group_under_empty(copies, name, center=False):
    """Parent ``copies`` under a fresh Empty named ``name`` without moving them. By default the
    Empty sits at the world origin (Maya's group transform); ``center=True`` puts it at the
    selection's average origin (Maya's "group + center pivot"). Returns the Empty."""
    import bpy

    empty = bpy.data.objects.new(name, None)
    _link_like(empty, copies[0])
    if center:
        import mathutils

        loc = sum(
            (c.matrix_world.translation for c in copies), mathutils.Vector()
        ) / len(copies)
        empty.location = loc
        inv = mathutils.Matrix.Translation(-loc)  # keep children in world space
        for c in copies:
            c.parent = empty
            c.matrix_parent_inverse = inv
    else:
        for c in copies:
            c.parent = empty  # empty at identity → no matrix_parent_inverse needed
    return empty


def _join_copies(copies, name):
    """Join ``copies`` into a single mesh named ``name`` (object-level op, headless-safe)."""
    import bpy

    for c in copies:  # join chokes on multi-user data — make each single-user first
        if c.data is not None and c.data.users > 1:
            c.data = c.data.copy()
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    for c in copies:
        c.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = name
    return joined


# Stable metric order so two objects compare element-for-element via ptk.are_similar.
_SIMILAR_FLAG_ORDER = (
    "vertex", "edge", "face", "triangle", "shell", "uvcoord", "area",
    "world_area", "bounding_box",
)


def _count_shells(bm):
    """Number of connected vertex components (mesh shells) in ``bm`` — Maya's polyEvaluate
    ``shell``."""
    seen, shells = set(), 0
    for start in bm.verts:
        if start in seen:
            continue
        shells += 1
        stack = [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            for e in v.link_edges:
                nv = e.other_vert(v)
                if nv not in seen:
                    seen.add(nv)
                    stack.append(nv)
    return shells


def _mesh_metrics(obj, flags):
    """The requested similarity metrics for ``obj`` in ``_SIMILAR_FLAG_ORDER`` order (mirror of
    mayatk's polyEvaluate flags) so two objects compare element-for-element."""
    import bmesh

    me = obj.data
    m = {}
    if "vertex" in flags:
        m["vertex"] = len(me.vertices)
    if "edge" in flags:
        m["edge"] = len(me.edges)
    if "face" in flags:
        m["face"] = len(me.polygons)
    if "triangle" in flags:  # fan-triangulation count (n-2 per face), no recompute needed
        m["triangle"] = sum(max(len(p.vertices) - 2, 0) for p in me.polygons)
    if "uvcoord" in flags:
        uv = me.uv_layers.active
        m["uvcoord"] = len(uv.data) if uv else 0
    if "area" in flags:
        m["area"] = round(sum(p.area for p in me.polygons), 6)
    if "bounding_box" in flags:
        m["bounding_box"] = [round(d, 6) for d in obj.dimensions]
    if "shell" in flags:
        bm = bmesh.new()
        bm.from_mesh(me)
        m["shell"] = _count_shells(bm)
        bm.free()
    if "world_area" in flags:
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.transform(obj.matrix_world)
        m["world_area"] = round(sum(f.calc_area() for f in bm.faces), 6)
        bm.free()
    return [m[k] for k in _SIMILAR_FLAG_ORDER if k in m]


def get_similar_mesh(
    objects=None, *, tolerance=0.0, inc_orig=False, select=False,
    vertex=False, edge=False, face=False, triangle=False, shell=False,
    uvcoord=False, area=False, world_area=False, bounding_box=False,
):
    """Find scene mesh objects similar to ``objects`` by topology / area / bounding-box metrics —
    Blender mirror of mayatk's ``EditUtils.get_similar_mesh`` (Maya's polyEvaluate-based object-level
    *Select Similar*). Each enabled metric (vertex / edge / face / triangle / shell / uvcoord / area /
    world_area / bounding_box) is compared against the reference object(s) via the SHARED
    ``ptk.are_similar`` within ``tolerance``; an object matches when it is similar to ANY reference
    across ALL enabled metrics. ``objects=None`` uses the current selection. Returns the matched
    objects (plus the originals when ``inc_orig``); selects them when ``select``. Headless-testable.
    """
    import bpy

    refs = _meshes(objects if objects is not None else bpy.context.selected_objects)
    flags = {
        n for n, on in (
            ("vertex", vertex), ("edge", edge), ("face", face), ("triangle", triangle),
            ("shell", shell), ("uvcoord", uvcoord), ("area", area),
            ("world_area", world_area), ("bounding_box", bounding_box),
        ) if on
    }
    if not refs or not flags:
        return []
    ref_metrics = [_mesh_metrics(r, flags) for r in refs]
    # Pool + selection must stay within the active view layer — select_set() raises on objects
    # that aren't in it (other scenes / excluded collections), and they're not valid matches anyway.
    view_objects = list(bpy.context.view_layer.objects)
    matched = []
    for o in (m for m in view_objects if m.type == "MESH"):
        if o in refs or o in matched:
            continue
        om = _mesh_metrics(o, flags)
        if any(ptk.are_similar(rm, om, tolerance=tolerance) for rm in ref_metrics):
            matched.append(o)
    result = matched + (refs if inc_orig else [])
    if select:
        for o in view_objects:
            o.select_set(False)
        for o in result:
            o.select_set(True)
        if result:
            bpy.context.view_layer.objects.active = result[0]
    return result


def separate_objects(objects=None, *, by_material=False, rename=False, center_pivots=True):
    """Separate mesh(es) into loose parts, or one object per material (``by_material``) — Blender
    mirror of mayatk's ``EditUtils.separate_objects`` (``mesh.separate`` LOOSE/MATERIAL). Optionally
    renames the results (``<base>``, ``<base>_part01`` …) and centers their origins on geometry.
    Returns the newly-created objects (the originals are not included). Headless-testable.
    """
    import bpy

    objs = _meshes(objects if objects is not None else bpy.context.selected_objects)
    if not objs:
        return []
    sep_type = "MATERIAL" if by_material else "LOOSE"
    new_objects = []
    for obj in objs:
        before = set(bpy.data.objects)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        try:
            bpy.ops.mesh.separate(type=sep_type)
        except RuntimeError:
            pass
        bpy.ops.object.mode_set(mode="OBJECT")
        parts = [o for o in bpy.data.objects if o not in before]
        if parts:  # something actually separated — post-process only then (no-op shouldn't mutate)
            produced = [obj] + parts  # the source keeps one part
            if center_pivots:
                bpy.ops.object.select_all(action="DESELECT")
                for p in produced:
                    p.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")
            if rename:
                base = obj.name.split(".")[0]
                for i, p in enumerate(produced):
                    p.name = base if i == 0 else f"{base}_part{i:02d}"
            new_objects.extend(parts)
    return new_objects


def _materials_of(obj):
    """The object's assigned material names (empty slots dropped)."""
    return [s.material.name for s in obj.material_slots if s.material]


def _material_key(obj):
    """Hashable grouping key for an object's material assignment — mirror of mayatk's
    ``group_objects_by_material``: no material -> ``"None"``; one -> its name; many -> the
    sorted tuple of names."""
    mats = _materials_of(obj)
    if not mats:
        return "None"
    if len(mats) == 1:
        return mats[0]
    return tuple(sorted(set(mats)))


def _world_bbox_center(obj):
    """World-space axis-aligned bounding-box centre of ``obj`` (Maya's ``xform -ws -bb`` midpoint),
    used to cluster objects by proximity. Delegates to the canonical ``xform_utils.get_world_bbox``."""
    from blendertk.xform_utils._xform_utils import get_world_bbox

    mn, mx = get_world_bbox(obj)
    return tuple((mn + mx) / 2.0)


@_object_mode
def combine_objects(
    objects=None, *, group_by_material=False, cluster_by_distance=False, threshold=10000.0
):
    """Combine mesh objects into one — Blender mirror of mayatk's ``EditUtils.combine_objects``
    (``object.join``). Plain: join everything into the first object's mesh. ``group_by_material``:
    join per material assignment instead (each result has one material set; objects whose material
    sets differ stay separate), and with ``cluster_by_distance`` each material group is further
    split by spatial proximity (``threshold`` world units, via the shared
    ``ptk.PointCloud.cluster_by_distance``). Returns the combined object (plain) or the list
    of combined objects (grouped). Headless-safe.
    """
    import bpy

    objs = _meshes(objects if objects is not None else bpy.context.selected_objects)
    if len(objs) < 2:
        return None

    if not group_by_material:
        return _join_copies(objs, objs[0].name)

    groups = {}
    for o in objs:
        groups.setdefault(_material_key(o), []).append(o)

    if cluster_by_distance:
        group_lists = []
        for members in groups.values():
            if len(members) < 2:
                group_lists.append(members)
                continue
            centers = [_world_bbox_center(o) for o in members]
            for idxs in ptk.PointCloud.cluster_by_distance(centers, threshold):
                group_lists.append([members[i] for i in idxs])
    else:
        group_lists = list(groups.values())

    return [
        _join_copies(members, members[0].name)
        for members in group_lists
        if len(members) >= 2
    ]


def detach_components(*, duplicate=False, separate=True, separate_each=False):
    """Detach the active mesh's selected faces — Blender mirror of mayatk's
    ``EditUtils.detach_components``. Edit-mode workflow (acts on the active mesh's current
    face selection):

      ``duplicate``      leave the originals in place and extract a COPY.
      ``separate``       move the extracted faces into a NEW object (off = split them off in
                         place via ``mesh.split``, staying within the same mesh; returns []).
      ``separate_each``  each extracted face becomes its own object (edge-split the extract
                         into face islands, then split into loose parts).

    Returns the newly created object(s). Requires an active mesh already in EDIT mode (the op
    is selection-based — silently entering edit mode would extract the wrong thing).
    """
    import bpy

    active = bpy.context.view_layer.objects.active
    if not (active and active.type == "MESH" and active.mode == "EDIT"):
        return []

    if duplicate:  # extract a copy: duplicate the selection in place first
        bpy.ops.mesh.duplicate()
    if not separate:  # detach in place — keep the geometry within the same object
        bpy.ops.mesh.split()
        return []

    before = set(bpy.data.objects)
    bpy.ops.mesh.separate(type="SELECTED")
    new = [o for o in bpy.data.objects if o not in before]
    if not (separate_each and new):
        return new

    # explode each extract into one object per face: with everything selected, edge-split
    # turns every face into its own loose island, then LOOSE separate gives one object each.
    bpy.ops.object.mode_set(mode="OBJECT")
    exploded = []
    for obj in new:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.edge_split()
        b2 = set(bpy.data.objects)
        try:
            bpy.ops.mesh.separate(type="LOOSE")
        except RuntimeError:
            pass
        bpy.ops.object.mode_set(mode="OBJECT")
        exploded.extend([o for o in bpy.data.objects if o not in b2])
    return new + exploded


@_object_mode
def get_overlapping_faces(objects, delete=False, select=True, round_ndigits=5):
    """Find faces geometrically coincident with another face — doubled geometry on *distinct*
    vertex sets (mirror of ``mtk.get_overlapping_faces``). Faces are grouped by the multiset of
    their rounded LOCAL vertex coordinates; within each coincident group all but the first count as
    overlapping. ``delete`` removes the overlaps; otherwise (``select``) they are flagged so
    entering Edit Mode shows them. bmesh on ``obj.data`` (``@_object_mode``-guarded). Returns the
    number of overlapping faces found across ``objects``."""
    import bmesh

    total = 0
    for o in _meshes(objects):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        groups = {}
        for face in bm.faces:
            key = tuple(sorted(
                (round(v.co.x, round_ndigits), round(v.co.y, round_ndigits),
                 round(v.co.z, round_ndigits))
                for v in face.verts
            ))
            groups.setdefault(key, []).append(face)
        dupes = [f for fs in groups.values() if len(fs) > 1 for f in fs[1:]]
        total += len(dupes)
        if delete:
            if dupes:
                bmesh.ops.delete(bm, geom=dupes, context="FACES")
                bm.to_mesh(o.data)
                o.data.update()
        elif select:
            for comp in (bm.verts, bm.edges, bm.faces):
                for c in comp:
                    c.select_set(False)
            for f in dupes:
                f.select_set(True)
            bm.select_flush(True)
            bm.to_mesh(o.data)
            o.data.update()
        bm.free()
    return total


@_object_mode
def get_overlapping_duplicates(objects=None, retain=None, select=False, delete=False, round_ndigits=5):
    """Find duplicate mesh objects overlapping in world space — mirror of
    ``mtk.get_overlapping_duplicates``. Each mesh is fingerprinted by vertex/face count, its rounded
    world-space bounding box, and a sample of world-space vertex positions; objects sharing a
    fingerprint are duplicates.

    ``objects`` is the pool to scan (``None`` = every scene mesh). ``retain`` (e.g. the current
    selection) flips the search to *"duplicates **of** the retained objects, omitting them"*: only
    groups containing a retained object are reported, and the retained ones are kept. With no
    ``retain`` the first of each group is kept and the rest reported. ``select`` selects / ``delete``
    removes the duplicates. Returns the list of duplicate objects."""
    import bpy
    from mathutils import Vector

    pool = _meshes(objects) if objects is not None else [o for o in bpy.data.objects if o.type == "MESH"]
    retain_set = set(_meshes(retain)) if retain is not None else set()
    pool = list(dict.fromkeys([*pool, *retain_set]))  # ensure retained are grouped, preserve order

    def _r(p):
        return (round(p.x, round_ndigits), round(p.y, round_ndigits), round(p.z, round_ndigits))

    def _fingerprint(o):
        me = o.data
        n = len(me.vertices)
        if not n:
            return None
        mw = o.matrix_world
        # bbox from the 8 object-space corners (cheap + consistent for duplicates) rather than a
        # full world-space pass over every vertex; a sample of verts disambiguates same-bbox shapes.
        corners = [mw @ Vector(c) for c in o.bound_box]
        xs, ys, zs = [p.x for p in corners], [p.y for p in corners], [p.z for p in corners]
        bbox = (
            round(min(xs), round_ndigits), round(min(ys), round_ndigits), round(min(zs), round_ndigits),
            round(max(xs), round_ndigits), round(max(ys), round_ndigits), round(max(zs), round_ndigits),
        )
        step = max(1, n // 8)  # up to ~8 evenly-spaced sampled verts
        sample = tuple(_r(mw @ me.vertices[i].co) for i in range(0, n, step))
        return (n, len(me.polygons), bbox, sample)

    groups = {}
    for o in pool:
        fp = _fingerprint(o)
        if fp is not None:
            groups.setdefault(fp, []).append(o)

    duplicates = []
    for grp in groups.values():
        if len(grp) < 2:
            continue
        if retain_set:
            if not any(o in retain_set for o in grp):
                continue  # only groups containing a retained object are of interest
            duplicates.extend(o for o in grp if o not in retain_set)
        else:
            duplicates.extend(grp[1:])  # keep the first of each group

    if select:
        bpy.ops.object.select_all(action="DESELECT")
        for o in duplicates:
            o.select_set(True)
    if delete:
        for o in duplicates:
            bpy.data.objects.remove(o, do_unlink=True)
    return duplicates


def _resample_polyline(points, n):
    """Resample an ordered list of points to exactly ``n`` points, evenly by arc length."""
    if len(points) == n:
        return [p.copy() for p in points]
    seglen = [0.0]
    for a, b in zip(points, points[1:]):
        seglen.append(seglen[-1] + (b - a).length)
    total = seglen[-1]
    if total == 0 or n < 2:
        return [points[0].copy() for _ in range(n)]
    out = []
    for i in range(n):
        target = total * i / (n - 1)
        j = 0
        while j < len(seglen) - 2 and seglen[j + 1] < target:
            j += 1
        seg = seglen[j + 1] - seglen[j]
        t = 0.0 if seg == 0 else (target - seglen[j]) / seg
        out.append(points[j].lerp(points[j + 1], t))
    return out


def loft(objects=None, *, close=False, reverse_normals=False, section_spans=1):
    """Loft a mesh surface across a sequence of profile curves / mesh edge-loops — a Blender mesh
    equivalent of Maya's ``loft`` (Blender ships no native NURBS loft). Each profile's ordered
    points are resampled to a common count and consecutive profiles are bridged with quad faces.

    ``close`` bridges the last profile back to the first (periodic in the loft direction);
    ``section_spans`` inserts that many linearly-interpolated rings between consecutive profiles;
    ``reverse_normals`` flips the result's winding. Profiles come from the selected curve/mesh
    objects in selection order (evaluated to ordered polylines). Returns the new lofted object,
    or None when fewer than two usable profiles are present. Mesh-based (no NURBS surface), so the
    Maya NURBS-surface params (degree / uniform parameterization / tolerance) don't apply.
    """
    import bpy

    pool = ptk.make_iterable(objects) if objects is not None else bpy.context.selected_objects
    depsgraph = bpy.context.evaluated_depsgraph_get()
    profiles = []
    for o in pool:
        if o.type not in ("CURVE", "MESH"):
            continue
        ev = o.evaluated_get(depsgraph)
        me = ev.to_mesh()
        pts = [o.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
        if len(pts) >= 2:
            profiles.append(pts)
    if len(profiles) < 2:
        return None

    n = max(len(p) for p in profiles)
    rings = [_resample_polyline(p, n) for p in profiles]

    if section_spans and section_spans > 1:  # interpolate extra rings between profiles
        dense = []
        for a, b in zip(rings, rings[1:]):
            dense.append(a)
            for s in range(1, section_spans):
                t = s / section_spans
                dense.append([a[i].lerp(b[i], t) for i in range(n)])
        dense.append(rings[-1])
        rings = dense

    if close:
        rings.append([p.copy() for p in rings[0]])

    verts = [tuple(co) for ring in rings for co in ring]
    faces = []
    for r in range(len(rings) - 1):
        for c in range(n - 1):
            a, b = r * n + c, r * n + c + 1
            d, e = (r + 1) * n + c, (r + 1) * n + c + 1
            faces.append((a, b, e, d))
    if reverse_normals:
        faces = [tuple(reversed(f)) for f in faces]

    mesh = bpy.data.meshes.new("Loft")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Loft", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


class EditUtils:
    """Namespace mirror of mayatk's ``EditUtils`` (helpers also exposed module-level)."""

    decimate = staticmethod(decimate)
    dissolve_coplanar = staticmethod(dissolve_coplanar)
    get_similar_mesh = staticmethod(get_similar_mesh)
    separate_objects = staticmethod(separate_objects)
    combine_objects = staticmethod(combine_objects)
    loft = staticmethod(loft)
    detach_components = staticmethod(detach_components)
    boolean_op = staticmethod(boolean_op)
    triangulate = staticmethod(triangulate)
    tris_to_quads = staticmethod(tris_to_quads)
    subdivide_mesh = staticmethod(subdivide_mesh)
    set_subdivision = staticmethod(set_subdivision)
    set_shading = staticmethod(set_shading)
    average_normals = staticmethod(average_normals)
    set_edge_hardness = staticmethod(set_edge_hardness)
    select_edges_by_angle = staticmethod(select_edges_by_angle)
    clear_custom_split_normals = staticmethod(clear_custom_split_normals)
    flip_normals = staticmethod(flip_normals)
    recalculate_normals = staticmethod(recalculate_normals)
    crease_edges = staticmethod(crease_edges)
    clean_geometry = staticmethod(clean_geometry)
    mirror = staticmethod(mirror)
    cut_along_axis = staticmethod(cut_along_axis)
    wedge = staticmethod(wedge)
    snap_closest_verts = staticmethod(snap_closest_verts)
    get_overlapping_faces = staticmethod(get_overlapping_faces)
    get_overlapping_duplicates = staticmethod(get_overlapping_duplicates)
