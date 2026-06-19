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

from blendertk.core_utils._core_utils import _object_mode
from blendertk.edit_utils._edit_utils import _meshes, _bmesh_edit


def _uv_edit(obj, fn):
    """Run ``fn(bm)`` against ``obj``'s bmesh, **mode-aware** (NOT ``@_object_mode``):
    edit mode updates the live edit-bmesh, object mode round-trips through the mesh."""
    import bmesh

    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        fn(bm)
        bmesh.update_edit_mesh(obj.data)
    else:
        _bmesh_edit(obj, fn)


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


def move_uvs(objects, du=0.0, dv=0.0):
    """Translate the UVs of the given mesh object(s) by ``(du, dv)`` — "move to UV space"
    (whole UV map)."""

    def _shift(bm):
        uvl = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                loop[uvl].uv.x += du
                loop[uvl].uv.y += dv

    for o in _meshes(objects):
        _uv_edit(o, _shift)


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

    for o in _meshes(objects):
        _uv_read(o, _gather)
    return None if box[0] == float("inf") else tuple(box)


def transform_uvs(objects, flip_u=False, flip_v=False, angle=0.0):
    """Flip and/or rotate (``angle`` degrees, CCW) the UVs of the given mesh object(s) about
    the combined UV bounding-box center (one shared pivot so multi-object maps stay aligned)."""
    import math

    box = _uv_bounds(objects)
    if box is None:
        return
    pu, pv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def _apply(bm):
        uvl = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uvl].uv
                u, v = uv.x - pu, uv.y - pv
                if flip_u:
                    u = -u
                if flip_v:
                    v = -v
                if angle:
                    u, v = u * cos_a - v * sin_a, u * sin_a + v * cos_a
                uv.x, uv.y = u + pu, v + pv

    for o in _meshes(objects):
        _uv_edit(o, _apply)


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

    for o in _meshes(objects):
        _uv_edit(o, _pin)


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


def get_texel_density(objects, map_size):
    """Texel density (px per scene unit) of the meshes' faces against a ``map_size`` map —
    mirror of ``mtk.get_texel_density``: ``sqrt(uv_area / world_area) * map_size``.
    Returns 0 when either area is zero."""
    from math import sqrt

    totals = [0.0, 0.0]

    def _accumulate(o):
        def _sum(bm):
            w, u = _face_areas(o, bm)
            totals[0] += w
            totals[1] += u

        return _sum

    for o in _meshes(objects):
        _uv_read(o, _accumulate(o))
    world_sum, uv_sum = totals
    if not world_sum or not uv_sum:
        return 0
    return sqrt(uv_sum / world_sum) * map_size


def set_texel_density(objects, density=1.0, map_size=4096):
    """Scale each object's UVs (about its own UV bbox center) to the target texel density —
    mirror of ``mtk.set_texel_density``. Per-OBJECT, not per-UV-shell (documented divergence:
    Maya scales each shell about its own center; shell detection is deferred until needed)."""
    for o in _meshes(objects):
        current = get_texel_density(o, map_size)
        if not current:
            continue
        scale = density / current
        box = _uv_bounds(o)
        pu, pv = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

        def _scale(bm):
            uvl = bm.loops.layers.uv.verify()
            for face in bm.faces:
                for loop in face.loops:
                    uv = loop[uvl].uv
                    uv.x = pu + (uv.x - pu) * scale
                    uv.y = pv + (uv.y - pv) * scale

        _uv_edit(o, _scale)


@_object_mode
def delete_extra_uv_sets(objects):
    """Remove all but the first UV map on the given mesh object(s) — "Cleanup UV Sets"."""
    for o in _meshes(objects):
        while len(o.data.uv_layers) > 1:
            o.data.uv_layers.remove(o.data.uv_layers[-1])


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


def _uv_layer_is_empty(layer, eps=1e-9):
    """True when every loop UV sits at the origin — a never-unwrapped ('empty') UV set."""
    return all(abs(d.uv.x) <= eps and abs(d.uv.y) <= eps for d in layer.data)


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


@_object_mode
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
    for o in _meshes(objects):
        me = o.data
        layers = list(me.uv_layers)
        initial = [layer.name for layer in layers]
        if not layers:
            results.append(
                UvSetCleanupResult(
                    object=o.name, error="no UV sets", initial_sets=[],
                    primary_set=None, deleted=[], final_name=None,
                )
            )
            continue

        if prefer_largest_area:
            nonempty = [layer for layer in layers if not _uv_layer_is_empty(layer)]
            primary = max(nonempty or layers, key=_uv_layer_bbox_area)
        else:
            primary = layers[0]
        primary_name = primary.name

        if keep_only_primary:
            to_delete = [layer for layer in layers if layer != primary]
        elif remove_empty:
            to_delete = [
                layer for layer in layers if layer != primary and _uv_layer_is_empty(layer)
            ]
        else:
            to_delete = []

        final_name = primary_name
        if rename_to_map1 and primary_name != DEFAULT_UV_SET:
            # only a 'map1' that will SURVIVE cleanup blocks the rename — one already queued for
            # deletion (keep_only_primary / remove_empty) frees the name, so the rename proceeds.
            clash = next(
                (
                    layer for layer in layers
                    if layer.name == DEFAULT_UV_SET and layer != primary and layer not in to_delete
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
            for name in delete_names:  # by name — removing invalidates other layer refs
                layer = me.uv_layers.get(name)
                if layer is not None:
                    me.uv_layers.remove(layer)
            if final_name != primary_name:
                kept = me.uv_layers.get(primary_name)
                if kept is not None:
                    kept.name = final_name

        results.append(
            UvSetCleanupResult(
                object=o.name, error=None, initial_sets=initial,
                primary_set=primary_name, deleted=delete_names, final_name=final_name,
            )
        )
    return results


# ---------------------------------------------------------------- lightmap UVs
# The lightmap goes on a SECOND UV layer (engine "UV2", Unity uv index 1). Detection is
# name-based against the canonical name + the conventional alternatives, mirroring mayatk's
# ``UvDiagnostics.find_lightmap_uv_set`` so a pre-existing / artist lightmap layer is REUSED
# (not duplicated) — real scenes don't name it uniformly.
LIGHTMAP_UV_SET = "Lightmap"
_LIGHTMAP_UV_NAMES = ("lightmap", "lightmapuv", "uv2", "uvchannel_2", "uvmap.001")


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

    prior_active = bpy.context.view_layer.objects.active
    prior_selection = [o for o in bpy.context.selected_objects]
    prior_mode = getattr(prior_active, "mode", "OBJECT")
    if prior_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    done = []
    try:
        for o in _meshes(objects):
            me = o.data
            name = find_lightmap_uv_set(o)
            if name is None:
                if len(me.uv_layers) == 0:
                    # A lightmap is the *second* channel — keep an empty base (texture) layer
                    # so the lightmap lands on index 1 (Unity uv2), matching the manifest.
                    me.uv_layers.new(name="UVMap")
                name = me.uv_layers.new(name=uv_set).name
            me.uv_layers[name].active = True

            for x in bpy.context.selected_objects:
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
        for x in bpy.context.selected_objects:
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


# ---------------------------------------------------------------- UV islands / shells
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


def _island_bbox_center(island, uv_layer):
    us = [loop[uv_layer].uv.x for f in island for loop in f.loops]
    vs = [loop[uv_layer].uv.y for f in island for loop in f.loops]
    return (min(us) + max(us)) / 2.0, (min(vs) + max(vs)) / 2.0


def _move_island(island, uv_layer, du, dv):
    for f in island:
        for loop in f.loops:
            loop[uv_layer].uv.x += du
            loop[uv_layer].uv.y += dv


def _target_islands(obj, bm, uv_layer):
    """The islands an operation should act on: in EDIT mode only islands touched by the
    selection (Maya's "selected shells"); in object mode every island."""
    islands = _uv_islands(bm, uv_layer)
    if obj.mode == "EDIT":
        islands = [isl for isl in islands if any(f.select for f in isl)]
    return islands


def get_uv_coords(objects):
    """Snapshot the active-layer UV coordinates per object (``{name: [(u, v), …]}`` in
    face/loop order) — pairs with :func:`set_uv_coords` for stack/unstack-style toggles."""
    snapshot = {}
    for o in _meshes(objects):
        coords = []

        def _read(bm, coords=coords):
            uvl = bm.loops.layers.uv.active
            if uvl is None:
                return
            for f in sorted(bm.faces, key=lambda f: f.index):
                for loop in f.loops:
                    uv = loop[uvl].uv
                    coords.append((uv.x, uv.y))

        _uv_read(o, _read)
        if coords:
            snapshot[o.name] = coords
    return snapshot


def set_uv_coords(objects, snapshot):
    """Restore a :func:`get_uv_coords` snapshot (objects whose topology changed since the
    capture restore as far as the loop counts still line up)."""
    for o in _meshes(objects):
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

        _uv_edit(o, _write)


def stack_uv_shells(objects):
    """Stack UV islands — move each targeted island so its bbox center coincides with the
    first island's center (in EDIT mode only islands touched by the selection move; object
    mode stacks every island). Maya's ``texStackShells`` groups shells by similarity first —
    here ALL targeted islands stack (documented divergence). Returns the number moved."""
    target = []  # the first island's center, shared across all given objects

    moved = 0
    for o in _meshes(objects):

        def _stack(bm, obj=o):
            nonlocal moved
            uvl = bm.loops.layers.uv.active
            if uvl is None:
                return
            for island in _target_islands(obj, bm, uvl):
                cu, cv = _island_bbox_center(island, uvl)
                if not target:
                    target.extend((cu, cv))
                    continue
                if abs(target[0] - cu) > 1e-9 or abs(target[1] - cv) > 1e-9:
                    _move_island(island, uvl, target[0] - cu, target[1] - cv)
                    moved += 1

        _uv_edit(o, _stack)
    return moved


def distribute_uv_shells(objects, axis="u"):
    """Distribute UV islands evenly along ``axis`` (``"u"`` or ``"v"``) — the first and
    last islands keep their centers, the rest space evenly between (per object; Maya's
    ``texDistributeShells`` equivalent). EDIT mode targets only selection-touched islands.
    Returns the number of islands repositioned."""
    comp = 0 if axis.lower() == "u" else 1
    moved = 0
    for o in _meshes(objects):

        def _distribute(bm, obj=o):
            nonlocal moved
            uvl = bm.loops.layers.uv.active
            if uvl is None:
                return
            islands = _target_islands(obj, bm, uvl)
            if len(islands) < 3:
                return  # endpoints are fixed — nothing to space
            centered = sorted(
                ((_island_bbox_center(isl, uvl), isl) for isl in islands),
                key=lambda pair: pair[0][comp],
            )
            lo = centered[0][0][comp]
            hi = centered[-1][0][comp]
            step = (hi - lo) / (len(centered) - 1)
            for n, (center, island) in enumerate(centered[1:-1], start=1):
                delta = (lo + step * n) - center[comp]
                if abs(delta) > 1e-9:
                    _move_island(island, uvl, delta if comp == 0 else 0.0,
                                 delta if comp == 1 else 0.0)
                    moved += 1

        _uv_edit(o, _distribute)
    return moved


def straighten_uvs(objects, u=True, v=True, angle=30.0):
    """Straighten the selected UV edges — edges within ``angle`` degrees of horizontal
    snap flat in V (``u``), near-vertical edges snap flat in U (``v``); the Maya
    ``texStraightenUVs`` semantics. Co-located loops on a vert (same UV vertex) move
    together so islands never tear. EDIT-mode selection-based. Returns edges snapped."""
    import math

    snapped = 0
    for o in _meshes(objects):
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
                    if u and math.degrees(math.atan2(dv, du)) <= angle and dv > 1e-9:
                        mid = (uv_a.y + uv_b.y) / 2.0
                        _set_uv_vert(loops[0].vert, uv_a, (uv_a.x, mid))
                        _set_uv_vert(loops[1].vert, uv_b, (uv_b.x, mid))
                        snapped += 1
                    elif v and math.degrees(math.atan2(du, dv)) <= angle and du > 1e-9:
                        mid = (uv_a.x + uv_b.x) / 2.0
                        _set_uv_vert(loops[0].vert, uv_a, (mid, uv_a.y))
                        _set_uv_vert(loops[1].vert, uv_b, (mid, uv_b.y))
                        snapped += 1

        _uv_edit(o, _straighten)
    return snapped


class UvUtils:
    """Namespace mirror of mayatk's ``UvUtils`` (helpers also exposed module-level)."""

    move_uvs = staticmethod(move_uvs)
    delete_extra_uv_sets = staticmethod(delete_extra_uv_sets)
    cleanup_uv_sets = staticmethod(cleanup_uv_sets)
    find_lightmap_uv_set = staticmethod(find_lightmap_uv_set)
    create_lightmap_uvs = staticmethod(create_lightmap_uvs)
    transform_uvs = staticmethod(transform_uvs)
    pin_uvs = staticmethod(pin_uvs)
    get_texel_density = staticmethod(get_texel_density)
    set_texel_density = staticmethod(set_texel_density)
    get_uv_coords = staticmethod(get_uv_coords)
    set_uv_coords = staticmethod(set_uv_coords)
    stack_uv_shells = staticmethod(stack_uv_shells)
    distribute_uv_shells = staticmethod(distribute_uv_shells)
    straighten_uvs = staticmethod(straighten_uvs)
