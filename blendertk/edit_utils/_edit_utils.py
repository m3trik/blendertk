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


def _apply_modifier(obj, mod_name):
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod_name)


@_object_mode
def decimate(objects, percentage=50.0, preserve_quads=True, symmetry=False, apply=True):
    """Reduce mesh density via a Decimate (COLLAPSE) modifier — mirror of ``mtk.EditUtils.decimate``.

    ``percentage`` is the percent of detail to REMOVE (Maya polyReduce semantics) → modifier
    ``ratio = 1 - percentage/100``. ``preserve_quads`` keeps quads (skips collapse-triangulate);
    ``symmetry`` reduces symmetrically about X. ``apply`` bakes the modifier (destructive, matching
    Maya's ``replaceOriginal``); pass ``False`` to keep it live.
    """
    import bpy

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
def dissolve_coplanar(objects, angle_tolerance=1.0, apply=True):
    """Dissolve near-coplanar faces via a Decimate (PLANAR) modifier — mirror of
    ``mtk.EditUtils.dissolve_coplanar``. ``angle_tolerance`` is the max dihedral angle (degrees)
    treated as coplanar (~0 is lossless on hard-surface). ``apply`` bakes the modifier.
    """
    for o in _meshes(objects):
        mod = o.modifiers.new(name="Decimate", type="DECIMATE")
        mod.decimate_type = "DISSOLVE"
        mod.angle_limit = math.radians(angle_tolerance)
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


@_object_mode
def set_edge_hardness(objects, angle=30.0):
    """Smooth-shade, then mark edges **sharp** where the dihedral angle ≥ ``angle`` degrees —
    Blender split normals follow sharp edges automatically (mirror of ``mtk`` set-normals-by-angle).
    Boundary edges (one face) are left smooth. bmesh, headless.
    """
    limit = math.radians(angle)

    def _f(bm):
        for face in bm.faces:
            face.smooth = True
        for edge in bm.edges:
            if len(edge.link_faces) == 2:
                edge.smooth = edge.calc_face_angle() < limit  # sharp where angle >= limit

    _bmesh_each(objects, _f)


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


# ---------------------------------------------------------------- crease (subsurf edge crease)
def _crease_layer(bm):
    """The bmesh edge-crease float layer (Blender 4.0+ moved it to the ``crease_edge`` attribute,
    so the legacy ``bm.edges.layers.crease`` no longer exists)."""
    return bm.edges.layers.float.get("crease_edge") or bm.edges.layers.float.new("crease_edge")


def crease_edges(objects, amount=10.0):
    """Set Subdivision-Surface edge crease on the given mesh object(s) — mirror of Maya's
    ``crease_edges``. ``amount`` is the Maya 0–10 scale, normalized to Blender's 0–1 crease.

    **Mode-aware** (so it is NOT ``@_object_mode``-guarded): in EDIT mode it creases the *selected*
    edges (the marking-menu workflow); in OBJECT mode it creases all edges of the object.
    """
    import bmesh

    value = max(0.0, min(1.0, amount / 10.0))
    for o in _meshes(objects):
        if o.mode == "EDIT":
            bm = bmesh.from_edit_mesh(o.data)
            cl = _crease_layer(bm)
            for e in bm.edges:
                if e.select:
                    e[cl] = value
            bmesh.update_edit_mesh(o.data)
        else:
            def _set(bm):
                cl = _crease_layer(bm)
                for e in bm.edges:
                    e[cl] = value

            _bmesh_edit(o, _set)
    return value


class EditUtils:
    """Namespace mirror of mayatk's ``EditUtils`` (helpers also exposed module-level)."""

    decimate = staticmethod(decimate)
    dissolve_coplanar = staticmethod(dissolve_coplanar)
    boolean_op = staticmethod(boolean_op)
    triangulate = staticmethod(triangulate)
    tris_to_quads = staticmethod(tris_to_quads)
    subdivide_mesh = staticmethod(subdivide_mesh)
    set_subdivision = staticmethod(set_subdivision)
    set_shading = staticmethod(set_shading)
    set_edge_hardness = staticmethod(set_edge_hardness)
    flip_normals = staticmethod(flip_normals)
    recalculate_normals = staticmethod(recalculate_normals)
    crease_edges = staticmethod(crease_edges)
    clean_geometry = staticmethod(clean_geometry)
