# !/usr/bin/python
# coding=utf-8
"""UV utilities — UV-coordinate translation and UV-set cleanup (mirror of mayatk's ``UvUtils``
names where they apply). Operate on the mesh UV layer via bmesh / ``mesh.uv_layers`` → headless.

The unwrap / pack / project / seam operators are ``bpy.ops.uv.*`` and live in the slot (they run
in edit mode); this module holds the data-level UV helpers that need no UV editor.

``import bpy`` / ``bmesh`` are deferred into the call bodies (no import side effects). Shares the
mesh primitives with ``edit_utils`` (the canonical home for mesh-bmesh infra).
"""

from collections import namedtuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils


# Maya's default primary UV-set name — renaming to it gives the Blender export pipeline parity.
DEFAULT_UV_SET = "map1"

# One per processed mesh (mirror of the result rows mayatk's ``cleanup_uv_sets`` reports):
#   object       object name
#   error        reason it was skipped, else None
#   initial_sets the UV-set names before cleanup
#   primary_set  the kept set's ORIGINAL name
#   deleted      names of the removed sets
#   final_name   the kept set's name after any rename
UvSetCleanupResult = namedtuple(
    "UvSetCleanupResult", "object error initial_sets primary_set deleted final_name"
)


# ---------------------------------------------------------------- lightmap UVs
# The lightmap goes on a SECOND UV layer (engine "UV2", Unity uv index 1). Detection is
# name-based against the canonical name + the conventional alternatives, mirroring mayatk's
# ``UvDiagnostics.find_lightmap_uv_set`` so a pre-existing / artist lightmap layer is REUSED
# (not duplicated) — real scenes don't name it uniformly.
LIGHTMAP_UV_SET = "Lightmap"
_LIGHTMAP_UV_NAMES = ("lightmap", "lightmapuv", "uv2", "uvchannel_2", "uvmap.001")


# ---------------------------------------------------------------- UV islands / shells


class _UvUtilsInternal(object):
    """Internal helpers for UvUtils."""

    @staticmethod
    def _uv_edit(obj, fn):
        """Run ``fn(bm)`` against ``obj``'s bmesh, **mode-aware** (NOT ``@_object_mode``):
        edit mode updates the live edit-bmesh, object mode round-trips through the mesh."""
        import bmesh

        if obj.mode == "EDIT":
            bm = bmesh.from_edit_mesh(obj.data)
            fn(bm)
            bmesh.update_edit_mesh(obj.data)
        else:
            EditUtils._bmesh_edit(obj, fn)

    @staticmethod
    def _uv_read(obj, fn):
        """Read-only variant of :func:`_uv_edit` — no mesh write-back (a get must not touch the
        mesh) and no edit-bmesh update."""
        import bmesh

        if obj.mode == "EDIT":
            fn(bmesh.from_edit_mesh(obj.data))
        else:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            fn(bm)
            bm.free()

    @staticmethod
    def _uv_bounds(objects):
        """Combined UV bounding box (min_u, min_v, max_u, max_v) across the meshes, or None.

        Running min/max, not a coordinate list — dense (photogrammetry-scale) meshes would
        otherwise materialize tens of millions of tuples. Read-only: a missing UV layer is
        skipped, never created.
        """
        box = [float("inf"), float("inf"), float("-inf"), float("-inf")]

        def _gather(bm):
            uvl = bm.loops.layers.uv.active
            if uvl is None:
                return
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[uvl].uv
                    if uv.x < box[0]:
                        box[0] = uv.x
                    if uv.y < box[1]:
                        box[1] = uv.y
                    if uv.x > box[2]:
                        box[2] = uv.x
                    if uv.y > box[3]:
                        box[3] = uv.y

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_read(o, _gather)
        return None if box[0] == float("inf") else tuple(box)

    @staticmethod
    def _face_areas(obj, bm):
        """(world_area_sum, uv_area_sum) over the bmesh's faces.

        World area via Newell's method, UV area via the shoelace formula — both exact for the
        simple (possibly non-convex) polygons real meshes carry; fan-triangulation would
        overcount non-convex UV faces. Read-only: a missing UV layer yields (0, 0).
        """
        from mathutils import Vector

        uvl = bm.loops.layers.uv.active
        if uvl is None:
            return 0.0, 0.0
        mw = obj.matrix_world
        world_sum = uv_sum = 0.0
        for face in bm.faces:
            pts = [mw @ loop.vert.co for loop in face.loops]
            uvs = [loop[uvl].uv for loop in face.loops]
            normal = Vector((0.0, 0.0, 0.0))
            shoelace = 0.0
            for i in range(len(pts)):
                j = (i + 1) % len(pts)
                normal += pts[i].cross(pts[j])
                shoelace += uvs[i].x * uvs[j].y - uvs[j].x * uvs[i].y
            world_sum += normal.length / 2.0
            uv_sum += abs(shoelace) / 2.0
        return world_sum, uv_sum

    @staticmethod
    def _uv_layer_is_empty(layer, eps=1e-9):
        """True when every loop UV sits at the origin — a never-unwrapped ('empty') UV set."""
        return all(abs(d.uv.x) <= eps and abs(d.uv.y) <= eps for d in layer.data)

    @staticmethod
    def _uv_layer_bbox_area(layer):
        """UV bounding-box area of a layer — a cheap 'fill' proxy for ``prefer_largest_area``
        (running min/max so dense meshes don't materialize a coordinate list)."""
        lo_x = lo_y = float("inf")
        hi_x = hi_y = float("-inf")
        for d in layer.data:
            u, v = d.uv.x, d.uv.y
            lo_x, lo_y = (u if u < lo_x else lo_x), (v if v < lo_y else lo_y)
            hi_x, hi_y = (u if u > hi_x else hi_x), (v if v > hi_y else hi_y)
        return 0.0 if lo_x == float("inf") else (hi_x - lo_x) * (hi_y - lo_y)

    @staticmethod
    def _uv_islands(bm, uv_layer, eps=1e-6):
        """Connected UV islands as lists of BMFaces — faces joined across **UV-continuous**
        edges (both sides carry the same UVs at the shared verts; a seam splits the island)."""

        def _uv_at(face, vert):
            for loop in face.loops:
                if loop.vert is vert:
                    return loop[uv_layer].uv
            return None

        def _continuous(face_a, face_b, edge):
            for v in edge.verts:
                a, b = _uv_at(face_a, v), _uv_at(face_b, v)
                if a is None or b is None or (a - b).length > eps:
                    return False
            return True

        parent = {f: f for f in bm.faces}

        def find(x):
            while parent[x] is not x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for e in bm.edges:
            link = e.link_faces
            if len(link) < 2:
                continue
            first = link[0]
            for other in link[1:]:
                if _continuous(first, other, e):
                    parent[find(first)] = find(other)
        groups = {}
        for f in bm.faces:
            groups.setdefault(find(f), []).append(f)
        return list(groups.values())

    @staticmethod
    def _island_bounds(island, uv_layer):
        """The island's UV bounding box as ``(u_min, v_min, u_max, v_max)``."""
        us = [loop[uv_layer].uv.x for f in island for loop in f.loops]
        vs = [loop[uv_layer].uv.y for f in island for loop in f.loops]
        return (min(us), min(vs), max(us), max(vs))

    @staticmethod
    def _island_bbox_center(island, uv_layer):
        u_min, v_min, u_max, v_max = _UvUtilsInternal._island_bounds(island, uv_layer)
        return (u_min + u_max) / 2.0, (v_min + v_max) / 2.0

    @staticmethod
    def _move_island(island, uv_layer, du, dv):
        for f in island:
            for loop in f.loops:
                loop[uv_layer].uv.x += du
                loop[uv_layer].uv.y += dv

    @staticmethod
    def _target_islands(obj, bm, uv_layer):
        """The islands an operation should act on: in EDIT mode only islands touched by the
        selection (Maya's "selected shells"); in object mode every island."""
        islands = _UvUtilsInternal._uv_islands(bm, uv_layer)
        if obj.mode == "EDIT":
            islands = [isl for isl in islands if any(f.select for f in isl)]
        return islands

    @staticmethod
    def _island_signature(island, uv_layer):
        """Cheap shape/size signature for similarity matching: (long side, short side, UV area,
        face count) of the island's bbox — approximates Maya's ``polyUVStackSimilarShells``
        grouping (its internal matching algorithm is undocumented/native, so there is nothing to
        port faithfully; long/short rather than width/height so a 90-degree-rotated copy of the
        same shell still matches)."""
        us = [loop[uv_layer].uv.x for f in island for loop in f.loops]
        vs = [loop[uv_layer].uv.y for f in island for loop in f.loops]
        width, height = max(us) - min(us), max(vs) - min(vs)
        area = 0.0
        for f in island:
            pts = [loop[uv_layer].uv for loop in f.loops]
            shoelace = 0.0
            for i in range(len(pts)):
                j = (i + 1) % len(pts)
                shoelace += pts[i].x * pts[j].y - pts[j].x * pts[i].y
            area += abs(shoelace) / 2.0
        return (max(width, height), min(width, height), area, len(island))

    @staticmethod
    def _islands_similar(sig_a, sig_b, tolerance):
        """True when two :func:`_island_signature` results match within *tolerance* — Maya's
        ``polyUVStackSimilarShells -tolerance`` UI range (0 = near-exact, 10 = very loose),
        normalized here to a 0-1 fractional difference."""
        long_a, short_a, area_a, faces_a = sig_a
        long_b, short_b, area_b, faces_b = sig_b
        if faces_a != faces_b:
            return False
        tol = max(tolerance, 0.0) / 10.0

        def _close(x, y):
            return abs(x - y) <= tol * max(abs(x), abs(y), 1e-9)

        return (
            _close(long_a, long_b)
            and _close(short_a, short_b)
            and _close(area_a, area_b)
        )

    @staticmethod
    def _reset_face_uv_rect(face, uv_layer):
        """Give *face* (a quad) an axis-aligned rectangular UV footprint, sized from its own 3D
        edge lengths — the anchor shape :func:`straighten_uv_shells` propagates across an island.
        Follow Active Quads reuses whatever UV shape the active face already has rather than
        rectangularizing it, so the seed face needs squaring up first. No-op on a non-quad."""
        if len(face.loops) != 4:
            return False
        verts = [loop.vert for loop in face.loops]
        width = (verts[0].co - verts[1].co).length
        height = (verts[1].co - verts[2].co).length
        if width <= 1e-9 or height <= 1e-9:
            return False
        for loop, (u, v) in zip(
            face.loops, ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
        ):
            loop[uv_layer].uv = (u, v)
        return True

    @staticmethod
    def _uv_selected(loop, uv_layer, sync):
        """A loop's UV counts as selected: in UV-sync mode it follows the vertex selection, otherwise
        it carries its own per-loop UV selection flag."""
        return loop.vert.select if sync else loop[uv_layer].select

    @staticmethod
    def _farthest_pair(points):
        """Approximate a point set's two farthest-apart points (a selection's endpoints) in O(n): a
        diameter's endpoints are extremes along some direction, so scan the four ``u+v`` / ``u-v``
        diagonal extremes and take the farthest-apart pair among those four candidates. Exact for the
        "straighten a roughly-linear row of UVs" case (its endpoints ARE the bbox-diagonal extremes),
        and avoids an O(n^2) blow-up when linear-align runs over a dense object-mode UV map."""
        lo_s = hi_s = lo_d = hi_d = points[0]
        for p in points:
            s, d = p.x + p.y, p.x - p.y
            if s < lo_s.x + lo_s.y:
                lo_s = p
            if s > hi_s.x + hi_s.y:
                hi_s = p
            if d < lo_d.x - lo_d.y:
                lo_d = p
            if d > hi_d.x - hi_d.y:
                hi_d = p
        cands = (lo_s, hi_s, lo_d, hi_d)
        a, b, best = cands[0], cands[1], -1.0
        for i in range(len(cands)):
            for j in range(i + 1, len(cands)):
                dist = (cands[i] - cands[j]).length_squared
                if dist > best:
                    best, a, b = dist, cands[i], cands[j]
        return a, b


    @staticmethod
    def _mesh_similarity(a, b):
        """Similarity score (0-1) between two mesh objects from world bounding-box volume +
        vertex count — mirror of mayatk's ``_calculate_mesh_similarity``."""

        def _bb_volume(o):
            import mathutils

            cs = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
            return (
                (max(c.x for c in cs) - min(c.x for c in cs))
                * (max(c.y for c in cs) - min(c.y for c in cs))
                * (max(c.z for c in cs) - min(c.z for c in cs))
            )

        v1, v2 = _bb_volume(a), _bb_volume(b)
        n1, n2 = len(a.data.vertices), len(b.data.vertices)
        volume_similarity = 1 - abs(v1 - v2) / (max(v1, v2) or 1.0)
        vertex_similarity = 1 - abs(n1 - n2) / (max(n1, n2) or 1)
        return (volume_similarity + vertex_similarity) / 2


class UvUtils(_UvUtilsInternal):
    """Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level)."""

    @staticmethod
    def calculate_uv_padding(map_size: int, normalize: bool = False, factor: int = 256):
        """The texture gutter for a given map size — Blender-side name for the ecosystem rule.

        Delegates to :meth:`pythontk.MathUtils.calculate_uv_padding`, the same primitive
        mayatk's identically-named helper wraps, so a map packed in one DCC (or
        round-tripped through RizomUV) keeps its gutter when repacked in the other. No
        second copy of the rule to drift.

        Parameters:
            map_size (int): Texture size in pixels (width or height).
            normalize (bool): Return the padding as a fraction of the tile instead of pixels.
            factor (int): Divisor setting the gutter. 256 = 1/256 of the tile.

        Returns:
            (float) Padding in pixels, or normalized to UV units when ``normalize``.

        Note the normalized result is map-size-INVARIANT (``(size/256)/size`` == 1/256):
        4 px at 1024, 8 px at 2048, 16 px at 4096 — always the same fraction of the tile.
        """
        return ptk.MathUtils.calculate_uv_padding(
            map_size, normalize=normalize, factor=factor
        )

    @staticmethod
    def move_uvs(objects, du=0.0, dv=0.0):
        """Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
        (whole UV map)."""

        def _shift(bm):
            uvl = bm.loops.layers.uv.verify()
            for face in bm.faces:
                for loop in face.loops:
                    loop[uvl].uv.x += du
                    loop[uvl].uv.y += dv

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_edit(o, _shift)

    @staticmethod
    def get_uv_bounds(objects):
        """The UV-space bounding box of *objects*, as one box over the whole input —
        mirror of mayatk's ``get_uv_bounds``.

        Parameters:
            objects: Mesh object(s).

        Returns:
            tuple: ``(u_min, v_min, u_max, v_max)``, or None when the input
            resolves to no UVs.
        """
        # Running min/max rather than collecting every UV: the move pad calls this
        # on each arrow press, and a dense mesh would otherwise allocate a tuple
        # per loop just to reduce it immediately.
        box = []

        def _read(bm):
            uvl = bm.loops.layers.uv.active
            if uvl is None:
                return
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[uvl].uv
                    if box:
                        box[0] = min(box[0], uv.x)
                        box[1] = min(box[1], uv.y)
                        box[2] = max(box[2], uv.x)
                        box[3] = max(box[3], uv.y)
                    else:
                        box[:] = (uv.x, uv.y, uv.x, uv.y)

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_read(o, _read)

        return tuple(box) if box else None

    @classmethod
    def transfer_uvs_to_similar(cls, source, candidates=None, tolerance=0.9):
        """Transfer UVs from one source mesh to every geometrically similar mesh — mirror of
        mayatk's ``transfer_uvs_to_similar`` (fan one source out to look-alike targets matched
        by bounding-box volume + vertex count).

        Linked duplicates of the source are excluded (they share its mesh datablock, so their
        UVs already match — Blender's analogue of Maya's true instances), and each candidate
        linked-duplicate group receives one transfer via a single representative (the shared
        datablock covers the rest). The transfer itself is the native Data-Transfer op with
        **TOPOLOGY** loop mapping — the Blender analogue of the component-based
        ``sampleSpace=4`` mtk's transfer uses: the targets are duplicates, so matching by
        topology (not spatial proximity) survives them being moved apart. Run under
        ``window_context_override`` so it works headless / from the Qt-pump state.

        Parameters:
            source: The mesh object whose UVs are copied (must be a single mesh).
            candidates: Pool to search for similar meshes; None (default) = every scene mesh.
            tolerance (float): Minimum similarity score (0-1) a candidate must reach.

        Returns:
            list: the target objects that received the transfer.
        """
        import bpy

        src_meshes = EditUtils._meshes(source)
        if len(src_meshes) != 1:
            raise ValueError(
                f"source must resolve to exactly one mesh (got {len(src_meshes)})."
            )
        src = src_meshes[0]

        pool = EditUtils._meshes(
            candidates if candidates is not None else list(bpy.context.scene.objects)
        )
        seen_data = {src.data.name}  # source + its linked duplicates are excluded
        targets = []
        for t in pool:
            if t is src or t.data.name in seen_data:
                continue
            seen_data.add(t.data.name)  # one representative per linked group
            if cls._mesh_similarity(src, t) >= tolerance:
                targets.append(t)

        if targets:
            saved_active = bpy.context.view_layer.objects.active
            saved_sel = list(CoreUtils.selected_objects())

            def _deselect_all():
                # Mode-/window-independent deselect: ``object.select_all`` poll-fails from
                # the Qt-pump context (same convention as the slots' view-layer loops).
                for o in bpy.context.view_layer.objects:
                    o.select_set(False)

            try:
                with CoreUtils.window_context_override():
                    _deselect_all()
                    src.select_set(True)
                    for t in targets:
                        t.select_set(True)
                    bpy.context.view_layer.objects.active = src
                    bpy.ops.object.data_transfer(
                        data_type="UV",
                        loop_mapping="TOPOLOGY",
                        layers_select_src="ACTIVE",
                        layers_select_dst="ACTIVE",
                    )
            finally:
                _deselect_all()
                for o in saved_sel:
                    try:
                        o.select_set(True)
                    except (RuntimeError, ReferenceError):
                        pass
                bpy.context.view_layer.objects.active = saved_active
        return sorted(targets, key=lambda o: o.name)

    @staticmethod
    def scale_uvs(objects, su=1.0, sv=1.0, pivot=(0.0, 0.0)):
        """Scale the UVs of the given mesh object(s) by ``(su, sv)`` about ``pivot`` (UV-space
        point, default the 0-1 origin) — whole-map scale, e.g. shrinking a packed map into a
        fraction of its tile anchored at the tile corner (the rizom bridge's UV_AREA math)."""
        pu, pv = pivot

        def _scale(bm):
            uvl = bm.loops.layers.uv.verify()
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[uvl].uv
                    uv.x = pu + (uv.x - pu) * su
                    uv.y = pv + (uv.y - pv) * sv

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_edit(o, _scale)

    @staticmethod
    def transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0, per_shell=False):
        """Flip and/or rotate (``angle`` degrees, CCW) the UVs of the given mesh object(s).

        ``per_shell=False`` (default): one shared pivot at the combined UV bounding-box center
        across every targeted object, so a multi-object map stays aligned (whole-map transform).
        ``per_shell=True``: each UV island (:func:`_target_islands` — selection-touched in EDIT
        mode, every island in object mode) transforms independently about its own bbox center.
        """
        import math

        cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))

        def _rotate(u, v):
            if flip_u:
                u = -u
            if flip_v:
                v = -v
            if angle:
                u, v = u * cos_a - v * sin_a, u * sin_a + v * cos_a
            return u, v

        if per_shell:

            def _apply_per_shell(bm, obj):
                uvl = bm.loops.layers.uv.verify()
                for island in _UvUtilsInternal._target_islands(obj, bm, uvl):
                    pu, pv = _UvUtilsInternal._island_bbox_center(island, uvl)
                    for face in island:
                        for loop in face.loops:
                            uv = loop[uvl].uv
                            u, v = _rotate(uv.x - pu, uv.y - pv)
                            uv.x, uv.y = u + pu, v + pv

            for o in EditUtils._meshes(objects):
                _UvUtilsInternal._uv_edit(
                    o, lambda bm, obj=o: _apply_per_shell(bm, obj)
                )
            return

        box = _UvUtilsInternal._uv_bounds(objects)
        if box is None:
            return
        pu, pv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

        def _apply(bm):
            uvl = bm.loops.layers.uv.verify()
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[uvl].uv
                    u, v = _rotate(uv.x - pu, uv.y - pv)
                    uv.x, uv.y = u + pu, v + pv

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_edit(o, _apply)

    @staticmethod
    def mirror_uvs(objects, axis="u", per_shell=True, preserve_position=True):
        """Mirror UVs across U or V — mirror of ``mtk.UvUtils.mirror_uvs``.

        ``preserve_position=True`` (default): footprint-preserving reassignment. The exact set of
        UV points stays put; only which UV lands on which point changes, solved with a one-to-one
        assignment (``ptk.MathUtils.linear_sum_assignment`` — same Hungarian-algorithm approach
        mayatk uses) so the shell's silhouette in UV space is unchanged. ``preserve_position=False``
        performs a real geometric flip via :func:`transform_uvs` instead.

        ``per_shell=True`` (default): each UV island (:func:`_target_islands`) is its own group,
        mirrored about its own bbox center. ``per_shell=False``: every island of a given object is
        combined into ONE group about that object's own bbox center — a deliberate relaxation of
        Maya's whole-*selection* grouping (which spans every selected object into a single point
        set); grouping stays per-object here so multi-object edit sessions don't need cross-object
        bmesh handles held open simultaneously.

        EDIT mode targets only islands touched by the selection; object mode targets every island.
        """
        axis_norm = (axis or "u").lower()
        flip_u = axis_norm in ("u", "h", "horizontal")
        flip_v = not flip_u

        if not preserve_position:
            UvUtils.transform_uvs(
                objects, flip_u=flip_u, flip_v=flip_v, per_shell=per_shell
            )
            return

        meshes = EditUtils._meshes(objects)
        if not meshes:
            return

        # Phase 1 — read (never held across calls: object-mode bmeshes are freed as soon as the
        # reading callback returns, so islands are captured as plain (face_index, loop_pos, u, v)
        # tuples rather than BMFace/BMLoop references).
        per_obj_islands = []  # [(obj, [island_points, ...])]
        for o in meshes:
            islands = []

            def _read(bm, islands=islands, obj=o):
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                for island in _UvUtilsInternal._target_islands(obj, bm, uvl):
                    pts = [
                        (f.index, li, loop[uvl].uv.x, loop[uvl].uv.y)
                        for f in island
                        for li, loop in enumerate(f.loops)
                    ]
                    if pts:
                        islands.append(pts)

            _UvUtilsInternal._uv_read(o, _read)
            if islands:
                per_obj_islands.append((o, islands))

        if not per_obj_islands:
            return

        # Phase 2 — group (per_shell: one group per island; else: one group per object) and solve
        # the assignment for each group independently.
        if per_shell:
            groups = [(o, isl) for o, islands in per_obj_islands for isl in islands]
        else:
            groups = [
                (o, [pt for isl in islands for pt in isl])
                for o, islands in per_obj_islands
            ]

        updates = {}  # obj -> {(face_index, loop_pos): (u, v)}
        for o, pts in groups:
            us = [p[2] for p in pts]
            vs = [p[3] for p in pts]
            center_u, center_v = (min(us) + max(us)) / 2.0, (min(vs) + max(vs)) / 2.0
            targets = [
                (2 * center_u - u, v) if flip_u else (u, 2 * center_v - v)
                for _, _, u, v in pts
            ]

            n = len(pts)
            cost = [[0.0] * n for _ in range(n)]
            for i, (tu, tv) in enumerate(targets):
                row = cost[i]
                for j, (_, _, su, sv) in enumerate(pts):
                    du, dv = tu - su, tv - sv
                    row[j] = du * du + dv * dv
            row_ind, col_ind = ptk.MathUtils.linear_sum_assignment(cost)
            assignment = dict(zip(row_ind, col_ind))

            obj_map = updates.setdefault(o, {})
            for i, (face_index, loop_pos, _, _) in enumerate(pts):
                slot = assignment.get(i)
                if slot is None:
                    continue
                _, _, su, sv = pts[slot]
                obj_map[(face_index, loop_pos)] = (su, sv)

        # Phase 3 — write back, one fresh bmesh handle per object.
        for o, mapping in updates.items():

            def _write(bm, mapping=mapping):
                uvl = bm.loops.layers.uv.verify()
                for f in bm.faces:
                    for li, loop in enumerate(f.loops):
                        coord = mapping.get((f.index, li))
                        if coord is not None:
                            loop[uvl].uv = coord

            _UvUtilsInternal._uv_edit(o, _write)

    @staticmethod
    def pin_uvs(objects, pin=True, selected_only=True):
        """Pin/unpin UVs (bmesh ``pin_uv``). ``selected_only`` restricts to the UVs of selected
        verts (the 3D-edit-mode workflow — no UV editor needed); object mode pins all."""

        def _pin(bm):
            uvl = bm.loops.layers.uv.verify()
            for face in bm.faces:
                for loop in face.loops:
                    if selected_only and not loop.vert.select:
                        continue
                    loop[uvl].pin_uv = pin

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_edit(o, _pin)

    @staticmethod
    def get_texel_density(objects, map_size):
        """Texel density (px per scene unit) of the meshes' faces against a ``map_size`` map —
        mirror of ``mtk.get_texel_density``: ``sqrt(uv_area / world_area) * map_size``.
        Returns 0 when either area is zero."""
        from math import sqrt

        totals = [0.0, 0.0]

        def _accumulate(o):
            def _sum(bm):
                w, u = _UvUtilsInternal._face_areas(o, bm)
                totals[0] += w
                totals[1] += u

            return _sum

        for o in EditUtils._meshes(objects):
            _UvUtilsInternal._uv_read(o, _accumulate(o))
        world_sum, uv_sum = totals
        if not world_sum or not uv_sum:
            return 0
        return sqrt(uv_sum / world_sum) * map_size

    @staticmethod
    def set_texel_density(objects, density=1.0, map_size=4096):
        """Scale each object's UVs (about its own UV bbox center) to the target texel density —
        mirror of ``mtk.set_texel_density``. Per-OBJECT, not per-UV-shell (documented divergence:
        Maya scales each shell about its own center; shell detection is deferred until needed)."""
        for o in EditUtils._meshes(objects):
            current = UvUtils.get_texel_density(o, map_size)
            if not current:
                continue
            scale = density / current
            box = _UvUtilsInternal._uv_bounds(o)
            pu, pv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

            def _scale(bm):
                uvl = bm.loops.layers.uv.verify()
                for face in bm.faces:
                    for loop in face.loops:
                        uv = loop[uvl].uv
                        uv.x = pu + (uv.x - pu) * scale
                        uv.y = pv + (uv.y - pv) * scale

            _UvUtilsInternal._uv_edit(o, _scale)

    @staticmethod
    @CoreUtils._object_mode
    def delete_extra_uv_sets(objects):
        """Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets"."""
        for o in EditUtils._meshes(objects):
            while len(o.data.uv_layers) > 1:
                o.data.uv_layers.remove(o.data.uv_layers[-1])

    @staticmethod
    @CoreUtils._object_mode
    def cleanup_uv_sets(
        objects,
        *,
        remove_empty=True,
        keep_only_primary=False,
        rename_to_map1=True,
        force_rename=False,
        prefer_largest_area=True,
        dry_run=False,
    ):
        """Standardize / clean up the UV sets (``uv_layers``) of the given mesh object(s).

        Blender counterpart of ``mtk.Diagnostics.cleanup_uv_sets`` (same options + report shape):

        - ``prefer_largest_area`` — pick the set with the largest UV footprint as the one to keep,
          instead of the first (index-0) set.
        - ``remove_empty`` — delete non-primary sets whose every UV sits at the origin (unmapped).
        - ``keep_only_primary`` — delete *all* non-primary sets (supersedes ``remove_empty``).
        - ``rename_to_map1`` — rename the kept set to :data:`DEFAULT_UV_SET` (``map1``).
        - ``force_rename`` — when a different set is already named ``map1``, overwrite it (delete the
          clash) instead of skipping the rename.
        - ``dry_run`` — compute the plan and report it, changing nothing.

        Returns one :class:`UvSetCleanupResult` per processed mesh. (Blender can't cheaply reorder
        ``uv_layers`` to index 0 the way Maya does, so the kept set is standardized by *name* only;
        when other sets are removed it lands at index 0 anyway.)
        """
        results = []
        for o in EditUtils._meshes(objects):
            me = o.data
            layers = list(me.uv_layers)
            initial = [layer.name for layer in layers]
            if not layers:
                results.append(
                    UvSetCleanupResult(
                        object=o.name,
                        error="no UV sets",
                        initial_sets=[],
                        primary_set=None,
                        deleted=[],
                        final_name=None,
                    )
                )
                continue

            if prefer_largest_area:
                nonempty = [
                    layer
                    for layer in layers
                    if not _UvUtilsInternal._uv_layer_is_empty(layer)
                ]
                primary = max(
                    nonempty or layers, key=_UvUtilsInternal._uv_layer_bbox_area
                )
            else:
                primary = layers[0]
            primary_name = primary.name

            if keep_only_primary:
                to_delete = [layer for layer in layers if layer != primary]
            elif remove_empty:
                to_delete = [
                    layer
                    for layer in layers
                    if layer != primary and _UvUtilsInternal._uv_layer_is_empty(layer)
                ]
            else:
                to_delete = []

            final_name = primary_name
            if rename_to_map1 and primary_name != DEFAULT_UV_SET:
                # only a 'map1' that will SURVIVE cleanup blocks the rename — one already queued for
                # deletion (keep_only_primary / remove_empty) frees the name, so the rename proceeds.
                clash = next(
                    (
                        layer
                        for layer in layers
                        if layer.name == DEFAULT_UV_SET
                        and layer != primary
                        and layer not in to_delete
                    ),
                    None,
                )
                if clash is None:
                    final_name = DEFAULT_UV_SET
                elif force_rename:
                    final_name = DEFAULT_UV_SET
                    to_delete.append(clash)
                # else: a surviving 'map1' with force off → skip the rename.

            delete_names = [layer.name for layer in to_delete]
            if not dry_run:
                for (
                    name
                ) in delete_names:  # by name — removing invalidates other layer refs
                    layer = me.uv_layers.get(name)
                    if layer is not None:
                        me.uv_layers.remove(layer)
                if final_name != primary_name:
                    kept = me.uv_layers.get(primary_name)
                    if kept is not None:
                        kept.name = final_name

            results.append(
                UvSetCleanupResult(
                    object=o.name,
                    error=None,
                    initial_sets=initial,
                    primary_set=primary_name,
                    deleted=delete_names,
                    final_name=final_name,
                )
            )
        return results

    @staticmethod
    def find_lightmap_uv_set(obj):
        """Name of *obj*'s existing lightmap UV layer, or ``None`` (mirror of
        ``mtk.find_lightmap_uv_set``).

        Matches :data:`LIGHTMAP_UV_SET` and the conventional alternatives case-tolerantly (any
        layer whose name contains "lightmap", or one of the known aliases). Never the first
        (index-0 texture) layer — a lightmap is the *second* channel — so an only-layer scene
        returns ``None`` and the baker creates a dedicated one.
        """
        me = getattr(obj, "data", None)
        layers = list(getattr(me, "uv_layers", []) or [])
        if len(layers) < 2:
            # A single UV layer is the texture channel; never treat it as the lightmap.
            return None
        for layer in layers[1:]:
            name = layer.name.strip().lower()
            if "lightmap" in name or name in _LIGHTMAP_UV_NAMES:
                return layer.name
        return None

    @staticmethod
    def create_lightmap_uvs(objects, uv_set=LIGHTMAP_UV_SET, margin=0.02, quiet=True):
        """Ensure each mesh has a packed, non-overlapping lightmap UV layer (UV2).

        Mirror of ``mtk.UvUtils.create_lightmap_uvs``: reuses a pre-existing lightmap-named
        layer (:func:`find_lightmap_uv_set`) when present, else adds ``uv_set`` as a second UV
        layer and fills it with a packed, non-overlapping unwrap via ``bpy.ops.uv.smart_project``
        (``scale_to_bounds`` packs the islands into the 0-1 square — exactly what a lightmap
        needs). The lightmap layer is left **active** so the subsequent bake targets it.

        Native-op based (smart_project runs headless from edit mode), so this needs no UV editor.
        Returns the names of the meshes processed.
        """
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        prior_active = bpy.context.view_layer.objects.active
        prior_selection = list(CoreUtils.selected_objects())
        prior_mode = getattr(prior_active, "mode", "OBJECT")
        if prior_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        done = []
        try:
            for o in EditUtils._meshes(objects):
                me = o.data
                name = UvUtils.find_lightmap_uv_set(o)
                if name is None:
                    if len(me.uv_layers) == 0:
                        # A lightmap is the *second* channel — keep an empty base (texture) layer
                        # so the lightmap lands on index 1 (Unity uv2), matching the manifest.
                        me.uv_layers.new(name="UVMap")
                    layer = me.uv_layers.new(name=uv_set)
                    if layer is None:
                        raise RuntimeError(
                            f"{o.name!r} already has the maximum 8 UV layers; "
                            "cannot add a lightmap layer"
                        )
                    name = layer.name
                me.uv_layers[name].active = True

                for x in CoreUtils.selected_objects():
                    x.select_set(False)
                o.select_set(True)
                bpy.context.view_layer.objects.active = o
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                try:
                    bpy.ops.uv.smart_project(
                        angle_limit=1.15, island_margin=margin, scale_to_bounds=True
                    )
                finally:
                    bpy.ops.object.mode_set(mode="OBJECT")
                done.append(o.name)
        finally:
            for x in CoreUtils.selected_objects():
                x.select_set(False)
            for x in prior_selection:
                try:
                    x.select_set(True)
                except ReferenceError:
                    pass
            if prior_active is not None:
                try:
                    bpy.context.view_layer.objects.active = prior_active
                    if prior_mode != "OBJECT":
                        bpy.ops.object.mode_set(mode=prior_mode)
                except (RuntimeError, ReferenceError):
                    pass
        return done

    @classmethod
    def auto_unwrap(
        cls,
        objects=None,
        method: str = "hard",
        map_size: int = 4096,
        pack: bool = None,
        orient: bool = True,
        engine_params: dict = None,
    ):
        """Automatically unwrap meshes with an external unwrapping engine.

        A true alternative to Blender's built-in Smart UV Project. Each mesh is
        exported to OBJ, unwrapped by the chosen engine, and its UVs
        transferred back — all in one undo step, with each mesh's original UVs
        snapshotted so a failure leaves that mesh untouched.

        Both engines return the input topology unchanged, so UVs map back
        loop-for-loop; no triangulation or spatial sampling is involved.

        Parameters:
            objects: Mesh object(s) to unwrap. None uses the selection. Linked
                duplicates collapse to one representative (shared mesh data).
            method (str): ``"hard"`` — Ministry of Flat, for hard-surface /
                mechanical meshes; topology-aware, artist-like seam placement,
                and it packs its own result. ``"organic"`` — Boundary First
                Flattening, for sculpted / scanned / character meshes;
                conformal flattening with automatic cone singularities. The
                engine keys ``"mof"`` / ``"bff"`` are accepted directly.
            map_size (int): Texture size the packing gutter is derived from;
                also Ministry of Flat's island-spacing resolution.
            pack (bool): What to do with the engine's UVs afterwards. None
                (default) picks per engine: Ministry of Flat's own island
                arrangement is kept and merely scaled into the 0-1 tile (it
                packs into a rectangle that overruns it), while BFF — which
                only flattens — gets a full pack. True forces the full repack;
                False leaves the engine's UVs exactly as produced.
            orient (bool): Rotate islands for a tighter fit when packing.
            engine_params (dict): Extra engine settings forwarded to
                :class:`pythontk.UvUnwrap` (e.g. ``{"separate_hard_edges": True}``
                for Ministry of Flat, ``{"n_cones": 8}`` for BFF).

        Returns:
            (AutoUnwrapResult): ``engine``, ``succeeded`` and ``failed``
            ``(name, reason)`` pairs. Truthy when at least one mesh unwrapped.

        Raises:
            FileNotFoundError: The engine executable isn't installed. The
                message carries its download URL.
            ValueError: No meshes given/selected, or an unknown *method*.
        """
        from blendertk.uv_utils._auto_unwrap import _AutoUnwrapInternal

        return _AutoUnwrapInternal.run(
            cls,
            objects=objects,
            method=method,
            map_size=map_size,
            pack=pack,
            orient=orient,
            engine_params=engine_params,
        )

    @classmethod
    def _pack_shells(cls, obj, map_size: int = 4096, orient: bool = True) -> None:
        """Pack the mesh's UV islands into the 0-1 square without overlap."""
        import bpy

        margin = cls.calculate_uv_padding(map_size, normalize=True)
        with CoreUtils.window_context_override():
            for other in bpy.context.view_layer.objects:
                other.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
            try:
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.select_all(action="SELECT")
                bpy.ops.uv.average_islands_scale()
                bpy.ops.uv.pack_islands(rotate=orient, margin=margin)
            finally:
                bpy.ops.object.mode_set(mode="OBJECT")

    @staticmethod
    def _fit_uvs_to_tile(obj) -> None:
        """Scale the mesh's UVs as a whole into 0-1, keeping their layout.

        A uniform, aspect-preserving fit — islands keep their relative
        arrangement and can't gain new overlaps. This is what an externally
        laid-out result needs: Ministry of Flat packs into a *rectangle* whose
        extent routinely runs past 1.0 (~1.5 x 1.8 is typical), so its packing
        is worth keeping but not its scale.

        Data-level (no operator), so it runs headless and needs no UV editor.
        """
        layer = obj.data.uv_layers.active
        if layer is None or not len(layer.data):
            return
        count = len(layer.data)
        buf = [0.0] * (2 * count)
        layer.data.foreach_get("uv", buf)
        us, vs = buf[0::2], buf[1::2]
        min_u, min_v = min(us), min(vs)
        # One scale for both axes -- islands keep their proportions, so the fit
        # can't introduce overlap. Mirrors Maya's polyNormalizeUV(collectively,
        # preserveAspectRatio), which likewise always normalizes.
        span = max(max(us) - min_u, max(vs) - min_v)
        if span <= 1e-9:
            return
        scale = 1.0 / span
        for i in range(count):
            buf[2 * i] = (buf[2 * i] - min_u) * scale
            buf[2 * i + 1] = (buf[2 * i + 1] - min_v) * scale
        layer.data.foreach_set("uv", buf)
        obj.data.update()

    @classmethod
    def transfer_uvs(cls, source, target, tolerance=0.1, match_by_similarity=True):
        """Copy the active UV layer from *source* mesh(es) onto *target* mesh(es).

        Mirror of ``mtk.UvUtils.transfer_uvs``. Two tiers, chosen per pair:
        when the loop counts match the UVs are copied straight across (exact,
        context-free, and the normal case for a tool that rewrites only UVs);
        otherwise ``data_transfer`` maps them spatially.

        Parameters:
            source: Mesh object(s) to read UVs from.
            target: Mesh object(s) to write UVs to.
            tolerance (float): Geometric-similarity tolerance, used only when
                *match_by_similarity* is True.
            match_by_similarity (bool): When True (default), treat the inputs as
                unordered groups and pair them by geometric similarity. Set
                False when the caller already supplies ordered pairs.
        """
        from blendertk.edit_utils._edit_utils import EditUtils

        sources, targets = EditUtils._meshes(source), EditUtils._meshes(target)
        if match_by_similarity:
            pairs, claimed = [], set()
            for src in sources:
                best, best_score = None, tolerance
                for dst in targets:
                    if dst.name in claimed:
                        continue
                    score = cls._mesh_similarity(src, dst)
                    if score >= best_score:
                        best, best_score = dst, score
                if best is not None:
                    claimed.add(best.name)
                    pairs.append((src, best))
        else:
            if len(sources) != len(targets):
                raise ValueError(
                    "source and target must be the same length when "
                    f"match_by_similarity=False (got {len(sources)} vs {len(targets)})."
                )
            pairs = zip(sources, targets)

        for src, dst in pairs:
            cls._transfer_uv_pair(src, dst)

    @staticmethod
    def _transfer_uv_pair(src, dst):
        """Copy one mesh's active UV layer onto another's."""
        import bpy

        src_uv = src.data.uv_layers.active
        if src_uv is None:
            return
        dst_uv = dst.data.uv_layers.active or dst.data.uv_layers.new(name=src_uv.name)

        src_data, dst_data = src_uv.data, dst_uv.data
        count = len(src_data)
        if count == len(dst_data):
            # Bulk C-level copy of the flat (u, v, u, v, ...) buffer -- far
            # faster than per-loop Python assignment on dense meshes, and exact
            # since topology and loop order match.
            buf = [0.0] * (2 * count)
            src_data.foreach_get("uv", buf)
            dst_data.foreach_set("uv", buf)
            dst.data.update()
            return

        with CoreUtils.window_context_override():
            try:
                if bpy.context.object and bpy.context.object.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:  # noqa: BLE001
                pass
            bpy.ops.object.select_all(action="DESELECT")
            dst.select_set(True)
            src.select_set(True)
            bpy.context.view_layer.objects.active = src  # active = transfer SOURCE
            bpy.ops.object.data_transfer(
                use_reverse_transfer=False,
                data_type="UV",
                use_create=True,
                loop_mapping="POLYINTERP_NEAREST",
                layers_select_src="ACTIVE",
                layers_select_dst="ACTIVE",
            )

    @staticmethod
    def get_uv_coords(objects):
        """Snapshot the active-layer UV coordinates per object (``{name: [(u, v), …]}`` in
        face/loop order) — pairs with :func:`set_uv_coords` for stack/unstack-style toggles."""
        snapshot = {}
        for o in EditUtils._meshes(objects):
            coords = []

            def _read(bm, coords=coords):
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                for f in sorted(bm.faces, key=lambda f: f.index):
                    for loop in f.loops:
                        uv = loop[uvl].uv
                        coords.append((uv.x, uv.y))

            _UvUtilsInternal._uv_read(o, _read)
            if coords:
                snapshot[o.name] = coords
        return snapshot

    @staticmethod
    def set_uv_coords(objects, snapshot):
        """Restore a :func:`get_uv_coords` snapshot (objects whose topology changed since the
        capture restore as far as the loop counts still line up)."""
        for o in EditUtils._meshes(objects):
            coords = snapshot.get(o.name)
            if not coords:
                continue

            def _write(bm, coords=coords):
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                it = iter(coords)
                for f in sorted(bm.faces, key=lambda f: f.index):
                    for loop in f.loops:
                        try:
                            u, v = next(it)
                        except StopIteration:
                            return
                        loop[uvl].uv = (u, v)

            _UvUtilsInternal._uv_edit(o, _write)

    @staticmethod
    def stack_uv_shells(objects, tolerance=None):
        """Stack UV islands on top of each other.

        ``tolerance=None`` (default): every targeted island stacks onto the first one found —
        Maya's plain ``texStackShells`` (no similarity gate). ``tolerance`` given (0-10, Maya's
        ``polyUVStackSimilarShells -tolerance`` range): islands only stack onto others whose
        :func:`_island_signature` matches within that tolerance; a shape with no match anywhere
        starts its own group and keeps its position — ``polyUVStackSimilarShells``.

        In EDIT mode only islands touched by the selection move; object mode targets every
        island. Returns the number of islands moved.
        """
        groups = []  # each: {"sig": signature-or-None, "center": (u, v)}
        moved = 0

        for o in EditUtils._meshes(objects):

            def _stack(bm, obj=o):
                nonlocal moved
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                for island in _UvUtilsInternal._target_islands(obj, bm, uvl):
                    center = _UvUtilsInternal._island_bbox_center(island, uvl)
                    sig = (
                        _UvUtilsInternal._island_signature(island, uvl)
                        if tolerance is not None
                        else None
                    )
                    if tolerance is None:
                        group = groups[0] if groups else None
                    else:
                        group = next(
                            (
                                g
                                for g in groups
                                if _UvUtilsInternal._islands_similar(
                                    sig, g["sig"], tolerance
                                )
                            ),
                            None,
                        )
                    if group is None:
                        groups.append({"sig": sig, "center": center})
                        continue
                    cu, cv = group["center"]
                    if abs(center[0] - cu) > 1e-9 or abs(center[1] - cv) > 1e-9:
                        _UvUtilsInternal._move_island(
                            island, uvl, cu - center[0], cv - center[1]
                        )
                        moved += 1

            _UvUtilsInternal._uv_edit(o, _stack)
        return moved

    @staticmethod
    def straighten_uv_shells(objects, mode="LENGTH_AVERAGE"):
        """Rectangularize the targeted UV shell(s) — mirror of Maya's ``texStraightenShell`` — via
        Blender's native Follow Active Quads operator: each island is isolated (selection scoped to
        just its own faces), one of its quads is squared up (:func:`_reset_face_uv_rect`) and set
        active, then ``uv.follow_active_quads`` propagates that rectangle across the rest of the
        island from the 3D edge lengths, straightening the whole shell into a grid.

        Needs EDIT mode with a mesh selection (drives the operator against the live edit-bmesh);
        silently skips objects not currently in Edit Mode. ``mode`` is the operator's own option
        (``EVEN`` / ``LENGTH`` / ``LENGTH_AVERAGE``); ``LENGTH_AVERAGE`` (default) best preserves
        relative face size across an irregular grid. Returns the number of islands straightened.
        """
        import bmesh
        import bpy

        straightened = 0
        for o in EditUtils._meshes(objects):
            if o.mode != "EDIT":
                continue
            bm = bmesh.from_edit_mesh(o.data)
            bm.faces.ensure_lookup_table()
            uvl = bm.loops.layers.uv.active
            if uvl is None:
                continue
            islands = _UvUtilsInternal._target_islands(o, bm, uvl)
            if not islands:
                continue

            prior_active_face = bm.faces.active
            prior_selection = {f: f.select for f in bm.faces}
            prior_active_obj = bpy.context.view_layer.objects.active
            bpy.context.view_layer.objects.active = o

            try:
                for island in islands:
                    if len(island) < 2:
                        continue  # a lone face is already "rectangular" -- nothing to unfold
                    seed = next((f for f in island if len(f.loops) == 4), None)
                    if seed is None or not _UvUtilsInternal._reset_face_uv_rect(
                        seed, uvl
                    ):
                        continue  # no quad to anchor from -- can't seed a grid
                    island_set = set(island)
                    for f in bm.faces:
                        f.select = f in island_set
                    bm.faces.active = seed
                    bmesh.update_edit_mesh(o.data)
                    try:
                        bpy.ops.uv.follow_active_quads(mode=mode)
                        straightened += 1
                    except RuntimeError:
                        pass  # non-grid topology (e.g. triangles/ngons) can't follow a quad chain
            finally:
                for f, sel in prior_selection.items():
                    f.select = sel
                if prior_active_face is not None:
                    bm.faces.active = prior_active_face
                bmesh.update_edit_mesh(o.data)
                bpy.context.view_layer.objects.active = prior_active_obj

        return straightened

    @staticmethod
    def derive_auto_seams(objects, angle=66.0, margin=0.0):
        """Auto-detect UV seams via a temporary Smart UV Project pass — mirror of Maya's
        ``u3dAutoSeam`` (which likewise exposes no user-tunable angle): each mesh is decomposed
        into islands the way Smart UV Project would, on a scratch UV layer, and those island
        borders are marked as real mesh seams (``uv.seams_from_islands``); the scratch layer is
        then discarded so the mesh's actual UV layers are untouched.

        Needs EDIT mode with a mesh selection (drives the operators against the live edit-mesh);
        silently skips objects not currently in Edit Mode. Returns the number of meshes processed.
        """
        import math

        import bpy

        meshes = [o for o in EditUtils._meshes(objects) if o.mode == "EDIT"]
        if not meshes:
            return 0

        prior_active_index = {}
        for o in meshes:
            me = o.data
            prior_active_index[o] = me.uv_layers.active_index
            layer = me.uv_layers.new(name="_autoseam_tmp", do_init=False)
            me.uv_layers.active = layer

        try:
            bpy.ops.uv.smart_project(
                angle_limit=math.radians(angle), island_margin=margin
            )
            bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
        finally:
            for o in meshes:
                me = o.data
                layer = me.uv_layers.get("_autoseam_tmp")
                if layer is not None:
                    me.uv_layers.remove(layer)
                idx = prior_active_index[o]
                if 0 <= idx < len(me.uv_layers):
                    me.uv_layers.active_index = idx

        return len(meshes)

    @staticmethod
    def distribute_uv_shells(objects, axis="u"):
        """Distribute UV islands evenly along ``axis`` (``"u"`` or ``"v"``) — the first and
        last islands keep their centers, the rest space evenly between (per object; Maya's
        ``texDistributeShells`` equivalent). EDIT mode targets only selection-touched islands.
        Returns the number of islands repositioned."""
        comp = 0 if axis.lower() == "u" else 1
        moved = 0
        for o in EditUtils._meshes(objects):

            def _distribute(bm, obj=o):
                nonlocal moved
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                islands = _UvUtilsInternal._target_islands(obj, bm, uvl)
                if len(islands) < 3:
                    return  # endpoints are fixed — nothing to space
                centered = sorted(
                    (
                        (_UvUtilsInternal._island_bbox_center(isl, uvl), isl)
                        for isl in islands
                    ),
                    key=lambda pair: pair[0][comp],
                )
                lo = centered[0][0][comp]
                hi = centered[-1][0][comp]
                step = (hi - lo) / (len(centered) - 1)
                for n, (center, island) in enumerate(centered[1:-1], start=1):
                    delta = (lo + step * n) - center[comp]
                    if abs(delta) > 1e-9:
                        _UvUtilsInternal._move_island(
                            island,
                            uvl,
                            delta if comp == 0 else 0.0,
                            delta if comp == 1 else 0.0,
                        )
                        moved += 1

            _UvUtilsInternal._uv_edit(o, _distribute)
        return moved

    @staticmethod
    def straighten_uvs(objects, u=True, v=True, angle=30.0):
        """Straighten the selected UV edges — edges within ``angle`` degrees of horizontal
        snap flat in V (``u``), near-vertical edges snap flat in U (``v``); the Maya
        ``texStraightenUVs`` semantics. Co-located loops on a vert (same UV vertex) move
        together so islands never tear. EDIT-mode selection-based. Returns edges snapped."""
        import math

        snapped = 0
        for o in EditUtils._meshes(objects):
            if o.mode != "EDIT":
                continue

            def _straighten(bm):
                nonlocal snapped
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return

                def _set_uv_vert(vert, old, new):
                    """Move every co-located loop UV on ``vert`` (one UV vertex = all loops
                    sharing the coordinate)."""
                    for loop in vert.link_loops:
                        if (loop[uvl].uv - old).length <= 1e-6:
                            loop[uvl].uv = new

                for e in (e for e in bm.edges if e.select):
                    for face in e.link_faces:
                        loops = [lo for lo in face.loops if lo.vert in e.verts]
                        if len(loops) != 2:
                            continue
                        uv_a, uv_b = loops[0][uvl].uv.copy(), loops[1][uvl].uv.copy()
                        du, dv = abs(uv_b.x - uv_a.x), abs(uv_b.y - uv_a.y)
                        if du < 1e-9 and dv < 1e-9:
                            continue
                        if (
                            u
                            and math.degrees(math.atan2(dv, du)) <= angle
                            and dv > 1e-9
                        ):
                            mid = (uv_a.y + uv_b.y) / 2.0
                            _set_uv_vert(loops[0].vert, uv_a, (uv_a.x, mid))
                            _set_uv_vert(loops[1].vert, uv_b, (uv_b.x, mid))
                            snapped += 1
                        elif (
                            v
                            and math.degrees(math.atan2(du, dv)) <= angle
                            and du > 1e-9
                        ):
                            mid = (uv_a.x + uv_b.x) / 2.0
                            _set_uv_vert(loops[0].vert, uv_a, (mid, uv_a.y))
                            _set_uv_vert(loops[1].vert, uv_b, (mid, uv_b.y))
                            snapped += 1

            _UvUtilsInternal._uv_edit(o, _straighten)
        return snapped

    @staticmethod
    def align_uvs(objects, axis="u", mode="avg"):
        """Align the selected UVs — mirror of Maya's ``performAlignUV`` (min/avg/max) and
        ``performLinearAlignUV`` (``mode="linear"``). ``min`` / ``max`` / ``avg`` snap every selected
        UV's U (``axis="u"``) or V (``axis="v"``) to the selection's min / max / arithmetic-mean
        (Maya's avgU is an arithmetic mean, matched here — not the bbox center); ``linear`` projects
        the selected UVs onto the straight line between their two endpoints (axis-agnostic). EDIT mode
        acts on the UV selection, object mode on the whole map. Returns the number of UVs moved."""
        import bpy

        comp = 0 if str(axis).lower() == "u" else 1
        moved = 0
        for o in EditUtils._meshes(objects):

            def _align(bm, obj=o):
                nonlocal moved
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                if obj.mode == "EDIT":
                    sync = bool(bpy.context.scene.tool_settings.use_uv_select_sync)
                    loops = [
                        l
                        for f in bm.faces
                        for l in f.loops
                        if _UvUtilsInternal._uv_selected(l, uvl, sync)
                    ]
                else:
                    loops = [l for f in bm.faces for l in f.loops]
                if len(loops) < 2:
                    return
                if mode == "linear":
                    pts = [l[uvl].uv.copy() for l in loops]
                    a, b = _UvUtilsInternal._farthest_pair(pts)
                    ab = b - a
                    denom = ab.length_squared
                    for l in loops:
                        p = l[uvl].uv
                        t = (p - a).dot(ab) / denom if denom > 1e-12 else 0.0
                        l[uvl].uv = a + ab * t
                        moved += 1
                else:
                    vals = [l[uvl].uv[comp] for l in loops]
                    target = (
                        min(vals)
                        if mode == "min"
                        else max(vals)
                        if mode == "max"
                        else sum(vals) / len(vals)
                    )
                    for l in loops:
                        l[uvl].uv[comp] = target
                        moved += 1

            _UvUtilsInternal._uv_edit(o, _align)
        return moved

    @staticmethod
    def gather_uv_shells(objects):
        """Gather the targeted UV shells back into the 0-1 tile — mirror of Maya's ``UVGatherShells``:
        each island is shifted by the negative floor of its bbox-center, so a shell sitting in tile
        (2,1) returns to (0,0). EDIT mode targets selection-touched islands, object mode every island.
        Returns the number of islands moved."""
        import math

        moved = 0
        for o in EditUtils._meshes(objects):

            def _gather(bm, obj=o):
                nonlocal moved
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                for island in _UvUtilsInternal._target_islands(obj, bm, uvl):
                    cu, cv = _UvUtilsInternal._island_bbox_center(island, uvl)
                    du, dv = math.floor(cu), math.floor(cv)
                    if du or dv:
                        _UvUtilsInternal._move_island(island, uvl, -du, -dv)
                        moved += 1

            _UvUtilsInternal._uv_edit(o, _gather)
        return moved

    @staticmethod
    def gather_to_udim(objects, udim=None, map_size=4096):
        """Move UV shells sitting outside the target UDIM tile into it — mirror of mayatk's
        ``UvUtils.gather_to_udim``, and the cheap alternative to a repack: each stray island keeps
        its sub-tile position via a whole-tile translation (an island straddling the tile's border
        gets the minimal pull-in instead), clamped inside the tile's border padding
        (:meth:`pythontk.MathUtils.uv_tile_margin` — the same margin the packers use). Islands
        already inside the tile do not move; overlaps with resident islands are accepted.

        ``udim=None`` targets the tile most of the input's islands already occupy (majority vote by
        island-bbox center), so the majority stays put. EDIT mode targets selection-touched islands,
        object mode every island. Returns the number of islands moved, or None when the input
        resolves to no UVs.
        """
        meshes = EditUtils._meshes(objects)

        if udim is not None:
            tile = ptk.MathUtils.udim_to_tile(udim)
        else:
            # Vote pass, run only when the target isn't given: it reads bounds
            # only, because in object mode the bmesh is freed when the callback
            # returns — island references must not escape it, so just the tile
            # crosses the pass boundary and islands are re-walked to apply.
            bounds = []
            for o in meshes:

                def _read_bounds(bm, obj=o):
                    uvl = bm.loops.layers.uv.active
                    if uvl is None:
                        return
                    bounds.extend(
                        _UvUtilsInternal._island_bounds(island, uvl)
                        for island in _UvUtilsInternal._target_islands(obj, bm, uvl)
                    )

                _UvUtilsInternal._uv_read(o, _read_bounds)

            tile = ptk.MathUtils.majority_tile(bounds)
            if tile is None:
                return None

        margin = ptk.MathUtils.uv_tile_margin(map_size)
        seen = moved = 0
        for o in meshes:

            def _gather(bm, obj=o):
                nonlocal seen, moved
                uvl = bm.loops.layers.uv.active
                if uvl is None:
                    return
                for island in _UvUtilsInternal._target_islands(obj, bm, uvl):
                    seen += 1
                    du, dv = ptk.MathUtils.fit_into_tile(
                        _UvUtilsInternal._island_bounds(island, uvl), tile, margin
                    )
                    if du or dv:
                        _UvUtilsInternal._move_island(island, uvl, du, dv)
                        moved += 1

            _UvUtilsInternal._uv_edit(o, _gather)
        return moved if seen else None

    @staticmethod
    def orient_uv_shells(objects, to_edge=False):
        """Orient the selected UV shells — mirror of Maya's ``texOrientShells`` /
        ``UvUtils.orient_shells`` (``to_edge=False`` -> native ``uv.align_rotation`` AUTO: square each
        shell to its nearest axis) and ``texOrientEdge`` (``to_edge=True`` -> EDGE: orient each shell
        so its selected edge runs along U/V). Drives the native operator against the edit-mesh, so it
        needs EDIT mode. Returns 1 if the operator ran, else 0."""
        import bpy

        if not any(o.mode == "EDIT" for o in EditUtils._meshes(objects)):
            return 0
        try:
            bpy.ops.uv.align_rotation(method="EDGE" if to_edge else "AUTO")
            return 1
        except RuntimeError:
            return 0  # e.g. EDGE with no edge selected

    @staticmethod
    def randomize_uv_shells(objects, seed=0):
        """Randomly offset the selected UV shells — mirror of Maya's ``RandomizeShells`` (a per-shell
        random translation; ``seed`` makes it deterministic). Drives ``uv.randomize_uv_transform``
        against the edit-mesh, so it needs EDIT mode. Returns 1 if the operator ran, else 0."""
        import bpy

        if not any(o.mode == "EDIT" for o in EditUtils._meshes(objects)):
            return 0
        try:
            bpy.ops.uv.randomize_uv_transform(
                random_seed=seed,
                use_loc=True,
                loc=(0.5, 0.5),
                use_rot=False,
                use_scale=False,
            )
            return 1
        except RuntimeError:
            return 0
