# !/usr/bin/python
# coding=utf-8
"""Mesh diagnostics — the Blender counterpart of mayatk's ``core_utils.diagnostics.mesh_diag``
(``MeshDiagnostics``).

Houses :func:`find_problem_geometry` (re-homed here from ``edit_utils`` to mirror mayatk, where
mesh problem-detection lives in ``Diagnostics`` not ``EditUtils``). The shared bmesh-iteration
helper ``_bmesh_each`` lives in ``edit_utils._edit_utils``; the ``@_object_mode`` guard is canonical
in the sibling ``core_utils._core_utils`` — both imported here (the same way mayatk's diagnostics
modules import ``XformUtils`` / ``NodeUtils``).
"""
from blendertk.core_utils._core_utils import _object_mode
from blendertk.edit_utils._edit_utils import _bmesh_each


def _is_convex(face):
    """True if ``face`` is convex (every interior turn keeps the same sign vs the face normal).
    Tris are always convex; degenerate faces report convex (handled by the degenerate pass)."""
    verts = face.verts
    n = len(verts)
    if n < 4:
        return True
    nrm = face.normal
    sign = 0
    for i in range(n):
        a = verts[i].co
        b = verts[(i + 1) % n].co
        c = verts[(i + 2) % n].co
        cross = (b - a).cross(c - b)
        d = cross.dot(nrm)
        if d > 1e-9:
            cur = 1
        elif d < -1e-9:
            cur = -1
        else:
            continue
        if sign == 0:
            sign = cur
        elif cur != sign:
            return False
    return True


def _is_planar(face, tolerance):
    """True if every vertex of ``face`` lies within ``tolerance`` (object units) of the face
    plane (point = first vert, normal = face normal). Tris are always planar."""
    if len(face.verts) < 4:
        return True
    nrm = face.normal
    if nrm.length_squared < 1e-12:
        return True  # degenerate — handled by the degenerate pass, not "non-planar"
    origin = face.verts[0].co
    return all(abs((v.co - origin).dot(nrm)) <= tolerance for v in face.verts)


def _uv_face_area(face, uv_layer):
    """Shoelace area of ``face`` in UV space (0 if it has fewer than 3 loops)."""
    loops = face.loops
    n = len(loops)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        a = loops[i][uv_layer].uv
        b = loops[(i + 1) % n][uv_layer].uv
        area += a.x * b.y - b.x * a.y
    return abs(area) * 0.5


@_object_mode
def find_problem_geometry(
    objects, *, ngons=False, nonmanifold=False, interior=False, nonplanar=False,
    loose=False, concave=False, quads=False, zero_area_faces=False,
    zero_length_edges=False, zero_uv_area=False, planar_tolerance=0.001,
    area_tolerance=1e-6, edge_length_tolerance=1e-6, uv_area_tolerance=1e-6, select=True,
):
    """Find (and optionally **select**) problem mesh components — the diagnostic half of Maya's
    Mesh Cleanup (the ``repair=False`` path). bmesh detection on ``obj.data`` (headless-testable);
    when ``select`` the matching components are flagged on the mesh so entering Edit Mode shows
    them. Mirrors mayatk's ``Diagnostics`` "select only" mode.

    ``@_object_mode``-guarded: the select flags are written via ``bm.to_mesh(obj.data)`` (through
    ``_bmesh_each``), which a *live* Edit-Mode bmesh would otherwise clobber — running in OBJECT
    mode (and restoring the caller's mode) makes the flags survive the round-trip back into Edit Mode.

    Criteria (all default off):

    - ``ngons``     — faces with more than 4 sides (FACE).
    - ``concave``   — non-convex faces (FACE).
    - ``nonplanar`` — faces whose verts deviate from the best-fit face plane by more than
      ``planar_tolerance`` (FACE) — Maya's "Non-Planar".
    - ``interior``  — faces whose every edge is shared by >2 faces (FACE).
    - ``nonmanifold`` — edges bordering ≠2 faces (EDGE).
    - ``loose``     — wire edges + unconnected verts (VERT/EDGE).
    - ``quads``     — faces with exactly 4 sides (FACE) — Maya's "Quads".
    - ``zero_area_faces``   — faces whose area ≤ ``area_tolerance`` (FACE) — Maya's "Zero Face Area".
    - ``zero_length_edges`` — edges shorter than ``edge_length_tolerance`` (EDGE) — Maya's
      "Zero Length Edges".
    - ``zero_uv_area``      — faces whose area in the active UV map ≤ ``uv_area_tolerance`` (FACE) —
      Maya's "Zero UV Face Area"; meshes with no UV layer contribute nothing.

    (Maya's "lamina" — two coincident faces on the *same* vertex set — and "holed faces" have no
    analogue: bmesh forbids a duplicate-vertex-set face, and a Blender face is a simple polygon with
    no interior hole. "Shared UVs" is likewise N/A — Blender UVs are per-loop, never vertex-shared.
    Overlapping faces from *doubled* geometry — coincident faces on distinct vertex sets — DO occur
    and are detected by the separate ``EditUtils.get_overlapping_faces``.)

    Returns ``{criterion: count}`` summed across all meshes. The select mode the slot should
    show is the natural component type of the highest-priority active criterion
    (FACE > EDGE > VERT), available as the ``"_mode"`` key.
    """
    counts = {
        "ngons": 0, "concave": 0, "nonplanar": 0, "interior": 0,
        "nonmanifold": 0, "loose": 0, "quads": 0, "zero_area_faces": 0,
        "zero_length_edges": 0, "zero_uv_area": 0,
    }
    # which component domain each criterion lives in (drives the shown select mode)
    face_crit = ngons or concave or nonplanar or interior or quads or zero_area_faces or zero_uv_area
    edge_crit = nonmanifold or zero_length_edges
    vert_crit = loose
    mode = "FACE" if face_crit else "EDGE" if edge_crit else "VERT" if vert_crit else "VERT"

    def _detect(bm):
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        bad_faces, bad_edges, bad_verts = set(), set(), set()

        if ngons:
            f = [face for face in bm.faces if len(face.verts) > 4]
            counts["ngons"] += len(f)
            bad_faces.update(f)
        if concave:
            f = [face for face in bm.faces if not _is_convex(face)]
            counts["concave"] += len(f)
            bad_faces.update(f)
        if nonplanar:
            f = [face for face in bm.faces if not _is_planar(face, planar_tolerance)]
            counts["nonplanar"] += len(f)
            bad_faces.update(f)
        if interior:
            f = [
                face for face in bm.faces
                if face.edges and all(len(e.link_faces) > 2 for e in face.edges)
            ]
            counts["interior"] += len(f)
            bad_faces.update(f)
        if nonmanifold:
            e = [edge for edge in bm.edges if len(edge.link_faces) != 2]
            counts["nonmanifold"] += len(e)
            bad_edges.update(e)
        if loose:
            e = [edge for edge in bm.edges if not edge.link_faces]
            v = [vert for vert in bm.verts if not vert.link_edges]
            counts["loose"] += len(e) + len(v)
            bad_edges.update(e)
            bad_verts.update(v)
        if quads:
            f = [face for face in bm.faces if len(face.verts) == 4]
            counts["quads"] += len(f)
            bad_faces.update(f)
        if zero_area_faces:
            f = [face for face in bm.faces if face.calc_area() <= area_tolerance]
            counts["zero_area_faces"] += len(f)
            bad_faces.update(f)
        if zero_length_edges:
            e = [edge for edge in bm.edges if edge.calc_length() <= edge_length_tolerance]
            counts["zero_length_edges"] += len(e)
            bad_edges.update(e)
        if zero_uv_area:
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is not None:
                f = [
                    face for face in bm.faces
                    if _uv_face_area(face, uv_layer) <= uv_area_tolerance
                ]
                counts["zero_uv_area"] += len(f)
                bad_faces.update(f)

        if select:
            for comp in (bm.verts, bm.edges, bm.faces):
                for c in comp:
                    c.select_set(False)
            for v in bad_verts:
                v.select_set(True)
            for e in bad_edges:
                e.select_set(True)
            for face in bad_faces:
                face.select_set(True)
            bm.select_flush(True)

    _bmesh_each(objects, _detect)
    counts["_mode"] = mode
    return counts


class MeshDiagnostics:
    """Mesh problem-detection (mirror of mayatk's ``MeshDiagnostics``)."""

    find_problem_geometry = staticmethod(find_problem_geometry)
