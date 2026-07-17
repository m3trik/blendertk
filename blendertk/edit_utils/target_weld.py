# !/usr/bin/python
# coding=utf-8
"""Target Weld — interactive drag-a-vertex-onto-another merge tool.

Blender port of Maya's **Merge Vertex Tool** (``cmds.targetWeldCtx`` /
``mel MergeVertexTool``), the tool tentacle's Maya ``polygons.b043`` (and ``b008`` with
``mergeToCenter=True``) activates. Blender has no native equivalent — snap+auto-merge only
approximates the workflow — so this module ships a modal operator that mirrors the Maya tool
**functionally and cosmetically**:

- Persistent tool: stays active for repeated welds until Esc / RMB (like a Maya tool context).
- Gesture: press a vertex (the *source*), drag — a dashed rubber-band line follows the cursor —
  release on the *target* vertex to merge. Releasing over nothing cancels that drag only.
- ``merge_to_center=False`` → the merged vertex lands **at the target** (Maya's Target Weld);
  ``True`` → at the **midpoint** (Maya's Weld Center flavor of the same context).
- Cosmetics: hovered candidate vertex highlights in Maya's preselection aqua, the armed source
  in Maya's selected-component yellow, plus a Maya-style in-view prompt (top-center, dark
  backdrop) and a crosshair pick cursor. Activation mirrors the Maya slot's prep exactly:
  enter component (Edit) mode, vertex mask, clear the selection.
- Each completed weld pushes its own undo step, so Ctrl+Z steps weld-by-weld like Maya.

Design notes:

- Picking is **screen-space** like Maya's: every visible (non-hidden) vertex is projected with
  one numpy matmul per mouse-move and the front-most vertex within ``PICK_RADIUS`` px wins —
  no BVH/ray-cast, so isolated verts, border verts and wireframes pick exactly like Maya.
  Projected caches are per-object and rebuilt whenever the BMesh is invalidated (undo, weld,
  topology ops passed through to Blender) and on every press.
- Viewport navigation stays fully live: plain MMB / wheel / trackpad / NDOF pass through,
  and **every Alt-modified event passes through** — Maya keeps Alt tumble/track/dolly working
  inside a tool context, and the Maya-style Blender keymaps (Industry Compatible / Alt-nav)
  bind navigation to Alt+LMB/MMB/RMB, which the tool must never consume. Unmodified LMB
  inside the viewport belongs to the tool; clicks elsewhere pass through.
- Multi-object Edit Mode is supported; like Maya, source and target must belong to the same
  mesh (Maya cannot even express the cross-object case — welding across objects is refused
  with a prompt rather than silently ignored).
- The operator class is built lazily inside ``_ensure_operator()`` (the ``macros.py`` pattern)
  and ``import bpy`` is deferred into call bodies, so importing this module — and resolving the package
  surface (``btk.target_weld``) — never needs a running Blender. The pure geometry helpers
  (:func:`project_points`, :func:`pick_screen_point`, :func:`weld_position`,
  :func:`dash_segments`) are numpy-only and testable with no ``bpy`` at all.
"""
from typing import Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------------------------
# Cosmetics — Maya Viewport 2.0 component-display defaults.
# --------------------------------------------------------------------------------------------
COLOR_PRESELECT = (0.35, 0.9, 1.0, 1.0)  # Maya preselection-highlight aqua
COLOR_SOURCE = (1.0, 1.0, 0.0, 1.0)  # Maya selected-vertex yellow
COLOR_LINE = (0.9, 0.9, 0.9, 0.9)  # rubber-band dash
COLOR_HALO = (0.0, 0.0, 0.0, 0.8)  # dark halo under markers (contrast on any bg)
COLOR_PROMPT_BG = (0.0, 0.0, 0.0, 0.55)  # Maya inViewMessage-style backdrop
COLOR_PROMPT_TEXT = (1.0, 1.0, 1.0, 1.0)

PICK_RADIUS = 14.0  # px — Maya-like pick tolerance
MARKER_SIZE = 9.0  # px — square vertex handle (Maya draws square handles)
HALO_SIZE = 13.0
DASH_LEN, GAP_LEN = 6.0, 4.0

_NAV_EVENTS = frozenset(
    {
        "MIDDLEMOUSE",
        "WHEELUPMOUSE",
        "WHEELDOWNMOUSE",
        "WHEELINMOUSE",
        "WHEELOUTMOUSE",
        "MOUSEROTATE",
        "MOUSEPAN",
        "MOUSEZOOM",
        "TRACKPADPAN",
        "TRACKPADZOOM",
    }
)


# --------------------------------------------------------------------------------------------
# Pure geometry helpers (no bpy — unit-testable anywhere).
# --------------------------------------------------------------------------------------------
def project_points(
    mvp: np.ndarray, coords: np.ndarray, width: float, height: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Project ``coords`` (N,3) through the 4x4 ``mvp`` into pixel space.

    Returns:
        (xy, depth): ``xy`` (N,2) pixel positions with ``NaN`` rows for points behind the
        camera; ``depth`` (N,) NDC depth (smaller = nearer the viewer).
    """
    coords = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    hom = np.empty((len(coords), 4), dtype=np.float64)
    hom[:, :3] = coords
    hom[:, 3] = 1.0
    clip = hom @ np.asarray(mvp, dtype=np.float64).T
    w = clip[:, 3]
    valid = w > 1e-9
    safe_w = np.where(valid, w, 1.0)
    ndc = clip[:, :3] / safe_w[:, None]
    xy = np.empty((len(coords), 2), dtype=np.float64)
    xy[:, 0] = (ndc[:, 0] * 0.5 + 0.5) * width
    xy[:, 1] = (ndc[:, 1] * 0.5 + 0.5) * height
    xy[~valid] = np.nan
    depth = np.where(valid, ndc[:, 2], np.inf)
    return xy, depth


def pick_screen_point(
    mouse_xy: Sequence[float],
    points_xy: np.ndarray,
    depths: np.ndarray,
    radius: float = PICK_RADIUS,
    exclude: Optional[int] = None,
) -> Optional[int]:
    """Index of the best pick candidate within ``radius`` px of ``mouse_xy``, or ``None``.

    Among the in-radius candidates the **front-most** wins (quantized NDC depth), with screen
    distance as the tie-break — so overlapping front/back verts resolve to the visible one,
    the way Maya picks.
    """
    if len(points_xy) == 0:
        return None
    d2 = points_xy - np.asarray(mouse_xy, dtype=np.float64)
    d2 = d2[:, 0] ** 2 + d2[:, 1] ** 2
    d2 = np.where(np.isnan(d2), np.inf, d2)
    if exclude is not None:
        d2[exclude] = np.inf
    in_radius = np.flatnonzero(d2 <= radius * radius)
    if in_radius.size == 0:
        return None
    order = np.lexsort((d2[in_radius], np.round(depths[in_radius], 4)))
    return int(in_radius[order[0]])


def weld_position(src_co, tgt_co, merge_to_center: bool = False):
    """The merged vertex's final position: the target (Maya Target Weld) or the midpoint
    (Maya's ``mergeToCenter=True`` Weld Center flavor)."""
    if merge_to_center:
        return tuple((s + t) * 0.5 for s, t in zip(src_co, tgt_co))
    return tuple(tgt_co)


def dash_segments(p0, p1, dash: float = DASH_LEN, gap: float = GAP_LEN):
    """2D dashed-line vertex pairs from ``p0`` to ``p1`` (flat list of (x, y) endpoints,
    consumable as a ``'LINES'`` batch)."""
    p0 = np.asarray(p0, dtype=np.float64)
    p1 = np.asarray(p1, dtype=np.float64)
    delta = p1 - p0
    length = float(np.hypot(*delta))
    if length < 1e-6:
        return []
    direction = delta / length
    verts, t = [], 0.0
    while t < length:
        end = min(t + dash, length)
        verts.append(tuple(p0 + direction * t))
        verts.append(tuple(p0 + direction * end))
        t = end + gap
    return verts


# --------------------------------------------------------------------------------------------
# BMesh-level weld (needs bpy/bmesh — testable in headless --background Blender).
# --------------------------------------------------------------------------------------------
def weld_pair(bm, v_src, v_tgt, merge_to_center: bool = False) -> None:
    """Merge ``v_src`` into ``v_tgt`` on ``bm`` (both verts of the same BMesh; edge-connected
    or not). The surviving vertex sits at :func:`weld_position`. Both input BMVert references
    are invalid afterward."""
    import bmesh
    from mathutils import Vector

    co = Vector(weld_position(v_src.co, v_tgt.co, merge_to_center))
    bmesh.ops.pointmerge(bm, verts=[v_src, v_tgt], merge_co=co)


# --------------------------------------------------------------------------------------------
# Modal-operator internals (module-level so the lazily-built class body stays thin).
# --------------------------------------------------------------------------------------------
def _edit_meshes(context):
    """Mesh objects currently in Edit Mode (multi-object edit aware)."""
    objs = getattr(context, "objects_in_mode", None)
    if objs:
        return [o for o in objs if o.type == "MESH"]
    view_layer = context.view_layer
    return [o for o in view_layer.objects if o.type == "MESH" and o.mode == "EDIT"]


def _cache_for(op, obj):
    """Per-object pick cache: the edit BMesh + visible-vert local coords / index arrays.
    Rebuilt whenever the BMesh was invalidated (undo, weld, pass-through topology ops)."""
    import bmesh

    cache = op._caches.get(obj.name)
    if (
        cache is None
        or not cache["bm"].is_valid
        or len(cache["bm"].verts) != cache["count"]
    ):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        visible = [v for v in bm.verts if not v.hide]
        cache = {
            "bm": bm,
            "count": len(bm.verts),
            "coords": np.array(
                [v.co[:] for v in visible], dtype=np.float64
            ).reshape(-1, 3),
            "index": np.array([v.index for v in visible], dtype=np.int64),
        }
        op._caches[obj.name] = cache
    return cache


def _pick(op, context, mouse, exclude=None):
    """Best vertex under the cursor across every Edit-Mode mesh.

    Returns ``{"obj", "index", "co", "xy", "depth"}`` (``co`` = local-space coord — the
    drag anchor and the vert's identity check) or ``None``. ``exclude`` is a ``(obj_name,
    vert_index)`` pair (the armed source, so a drag can't target itself).
    """
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    persp = np.array(rv3d.perspective_matrix, dtype=np.float64)
    best = None
    for obj in _edit_meshes(context):
        cache = _cache_for(op, obj)
        if len(cache["coords"]) == 0:
            continue
        mvp = persp @ np.array(obj.matrix_world, dtype=np.float64)
        xy, depth = project_points(mvp, cache["coords"], region.width, region.height)
        excl = None
        if exclude is not None and exclude[0] == obj.name:
            rows = np.flatnonzero(cache["index"] == exclude[1])
            excl = int(rows[0]) if rows.size else None
        row = pick_screen_point(mouse, xy, depth, PICK_RADIUS * _ui_scale(), exclude=excl)
        if row is None:
            continue
        hit = {
            "obj": obj.name,
            "index": int(cache["index"][row]),
            "co": tuple(cache["coords"][row]),  # local-space — survives nav mid-drag
            "xy": (float(xy[row, 0]), float(xy[row, 1])),
            "depth": float(depth[row]),
        }
        if best is None or hit["depth"] < best["depth"]:
            best = hit
    return best


def _do_weld(op, context, source, target) -> bool:
    """Weld the armed source onto the released target (same-object pairs only). Pushes one
    undo step per completed weld so Ctrl+Z steps weld-by-weld, like Maya."""
    import bmesh
    import bpy
    from mathutils import Vector

    if source["obj"] != target["obj"]:
        op._notice = "Source and target must belong to the same mesh."
        return False
    obj = context.view_layer.objects.get(source["obj"])
    if obj is None:
        return False
    cache = _cache_for(op, obj)
    bm = cache["bm"]
    bm.verts.ensure_lookup_table()
    try:
        v_src, v_tgt = bm.verts[source["index"]], bm.verts[target["index"]]
    except IndexError:  # topology changed under us — stale pick, drop it
        op._caches.pop(obj.name, None)
        return False
    # A pass-through edit mid-drag can shift indices WITHOUT changing the vert count (which
    # is what the cache keys on) — the press-time coord is the source's identity check, so a
    # stale index aborts instead of silently welding the wrong vertex.
    if (v_src.co - Vector(source["co"])).length > 1e-6:
        op._caches.pop(obj.name, None)
        return False
    weld_pair(bm, v_src, v_tgt, op.merge_to_center)
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    op._caches.pop(obj.name, None)  # indices shifted — force a rebuild
    op._welds += 1
    bpy.ops.ed.undo_push(message="Target Weld")
    return True


def _ui_scale() -> float:
    """Blender's interface scale — markers, pick radius and the prompt track it (the way
    Maya's handles track DPI)."""
    try:
        import bpy

        return float(bpy.context.preferences.system.ui_scale)
    except Exception:
        return 1.0


def _source_xy(op, context):
    """The armed source's CURRENT screen position (re-projected from its local coord each
    draw, so the rubber band stays anchored to the vertex through mid-drag navigation).
    ``None`` when it projects behind the camera."""
    from bpy_extras import view3d_utils
    from mathutils import Vector

    obj = context.view_layer.objects.get(op._source["obj"])
    if obj is None:
        return None
    world = obj.matrix_world @ Vector(op._source["co"])
    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world)


def _drop_transients(op, context, drop_source: bool) -> None:
    """Clear the hover highlight (and optionally the armed drag) when navigation begins —
    their cached screen positions go stale as soon as the view moves."""
    if op._hover is None and (not drop_source or op._source is None):
        return
    op._hover = None
    if drop_source:
        op._source = None
    _tag_redraw(context)


def _in_region(context, event) -> bool:
    region = context.region
    return (
        region is not None
        and 0 <= event.mouse_region_x <= region.width
        and 0 <= event.mouse_region_y <= region.height
    )


def _tag_redraw(context):
    if context.area is not None:
        context.area.tag_redraw()


def _prompt_text(op) -> str:
    if op._notice:
        return f"Target Weld: {op._notice}"
    if op._source is not None:
        return "Release on the target vertex to weld.  |  Esc: cancel"
    mode = "merge at center" if op.merge_to_center else "merge at target"
    return f"Target Weld ({mode}): drag a vertex onto a target vertex to merge.  |  Esc: exit"


def _finish(op, context):
    import bpy

    if op._handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(op._handle, "WINDOW")
        op._handle = None
    if context.window is not None:
        context.window.cursor_modal_restore()
    if context.workspace is not None:
        context.workspace.status_text_set(None)
    _tag_redraw(context)
    return {"FINISHED"} if op._welds else {"CANCELLED"}


def _op_invoke(op, context, event):
    import bpy

    if context.area is None or context.area.type != "VIEW_3D" or context.region is None:
        op.report({"WARNING"}, "Target Weld requires a 3D viewport.")
        return {"CANCELLED"}
    if not _edit_meshes(context):
        op.report({"WARNING"}, "Target Weld requires a mesh in Edit Mode.")
        return {"CANCELLED"}
    op._region = context.region
    op._mouse = (event.mouse_region_x, event.mouse_region_y)
    op._source = None  # armed source: {"obj", "index", "xy", ...}
    op._hover = None  # current candidate under the cursor
    op._notice = ""  # transient prompt override (cross-object refusal)
    op._welds = 0
    op._caches = {}
    op._handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw, (op,), "WINDOW", "POST_PIXEL"
    )
    context.window.cursor_modal_set("CROSSHAIR")
    context.workspace.status_text_set(
        "Target Weld  |  LMB-drag: weld vertex onto target  |  Esc / RMB: exit"
    )
    context.window_manager.modal_handler_add(op)
    _tag_redraw(context)
    return {"RUNNING_MODAL"}


def _op_modal(op, context, event):
    # The tool ends with the Edit session (Tab out, undo past entry, mode ops passed through).
    if not _edit_meshes(context):
        return _finish(op, context)

    # Maya keeps Alt+drag camera navigation live inside a tool context (tumble / track /
    # dolly), and the Maya-style Blender keymaps (Industry Compatible / Alt-nav / emulated
    # 3-button) bind navigation to Alt+mouse — so an Alt-modified event is ALWAYS navigation
    # here, never part of the weld gesture. Pass every one through, before the LMB/RMB
    # handling below can consume it (plain MMB / wheel / trackpad / NDOF navigation already
    # passes through). Alt also abandons an in-flight drag (its LMB conflicts with ours — a
    # passed-through release would otherwise leave a ghost rubber band armed with no button
    # held), exactly like Maya handing the gesture to the camera.
    if event.alt:
        _drop_transients(op, context, drop_source=True)
        return {"PASS_THROUGH"}

    etype = event.type
    if etype in _NAV_EVENTS or (
        etype.startswith("NUMPAD") and etype != "NUMPAD_ENTER"  # numpad = view nav keys
    ):
        # The hover highlight's cached screen position is stale the moment the view moves
        # (wheel zoom gets no follow-up MOUSEMOVE to refresh it) — drop it; it re-picks on
        # the next move. The armed source stays: its anchor re-projects every draw.
        _drop_transients(op, context, drop_source=False)
        return {"PASS_THROUGH"}

    if etype in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
        op._mouse = (event.mouse_region_x, event.mouse_region_y)
        exclude = (
            (op._source["obj"], op._source["index"]) if op._source is not None else None
        )
        op._hover = _pick(op, context, op._mouse, exclude=exclude)
        _tag_redraw(context)
        return {"PASS_THROUGH"}

    if etype == "LEFTMOUSE":
        if not _in_region(context, event):  # clicks outside the viewport aren't ours
            return {"PASS_THROUGH"}
        op._notice = ""
        op._mouse = (event.mouse_region_x, event.mouse_region_y)
        if event.value == "PRESS":
            op._caches.clear()  # pass-through edits may have moved verts — repick fresh
            op._source = _pick(op, context, op._mouse)
            op._hover = None
        elif event.value == "RELEASE" and op._source is not None:
            target = _pick(
                op, context, op._mouse, exclude=(op._source["obj"], op._source["index"])
            )
            if target is not None:
                _do_weld(op, context, op._source, target)
            op._source = None
            op._hover = _pick(op, context, op._mouse)
        _tag_redraw(context)
        return {"RUNNING_MODAL"}

    if etype in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
        if op._source is not None:  # cancel the in-flight drag only
            op._source = None
            _tag_redraw(context)
            return {"RUNNING_MODAL"}
        return _finish(op, context)

    if etype in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
        return _finish(op, context)

    return {"PASS_THROUGH"}


# --------------------------------------------------------------------------------------------
# Drawing (POST_PIXEL — 2D screen space, like Maya's tool feedback).
# --------------------------------------------------------------------------------------------
def _square(cx, cy, size):
    h = size * 0.5
    return [
        (cx - h, cy - h),
        (cx + h, cy - h),
        (cx + h, cy + h),
        (cx - h, cy - h),
        (cx + h, cy + h),
        (cx - h, cy + h),
    ]


def _draw(op):
    import blf
    import bpy
    import gpu
    from gpu_extras.batch import batch_for_shader

    context = bpy.context
    if context.region != op._region:  # the handler fires for every 3D view — ours only
        return

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    scale = _ui_scale()

    def tris(verts, color):
        if not verts:
            return
        batch = batch_for_shader(shader, "TRIS", {"pos": verts})
        shader.uniform_float("color", color)
        batch.draw(shader)

    def marker(xy, color):
        tris(_square(xy[0], xy[1], HALO_SIZE * scale), COLOR_HALO)
        tris(_square(xy[0], xy[1], MARKER_SIZE * scale), color)

    # Rubber band: source -> snapped target (or the raw cursor when nothing is under it).
    # The source anchor is re-projected from the vertex each draw (nav mid-drag tracks).
    if op._source is not None:
        src_xy = _source_xy(op, context)
        if src_xy is not None:
            end = op._hover["xy"] if op._hover is not None else op._mouse
            dashes = dash_segments(src_xy, end, dash=DASH_LEN * scale, gap=GAP_LEN * scale)
            if dashes:
                batch = batch_for_shader(shader, "LINES", {"pos": dashes})
                shader.uniform_float("color", COLOR_LINE)
                batch.draw(shader)
            marker(src_xy, COLOR_SOURCE)
    if op._hover is not None:
        marker(op._hover["xy"], COLOR_PRESELECT)

    # In-view prompt — Maya inViewMessage style: top-center white text on a dark backdrop.
    text = _prompt_text(op)
    font_id = 0
    blf.size(font_id, 13.0 * scale)
    tw, th = blf.dimensions(font_id, text)
    region = context.region
    pad = 8.0 * scale
    cx, top = region.width * 0.5, region.height - 24.0 * scale
    tris(
        [
            (cx - tw / 2 - pad, top - th - pad),
            (cx + tw / 2 + pad, top - th - pad),
            (cx + tw / 2 + pad, top + pad),
            (cx - tw / 2 - pad, top - th - pad),
            (cx + tw / 2 + pad, top + pad),
            (cx - tw / 2 - pad, top + pad),
        ],
        COLOR_PROMPT_BG,
    )
    blf.position(font_id, cx - tw / 2, top - th, 0)
    blf.color(font_id, *COLOR_PROMPT_TEXT)
    blf.draw(font_id, text)

    gpu.state.blend_set("NONE")


# --------------------------------------------------------------------------------------------
# Lazy operator registration (macros.py pattern) + public entry point.
# --------------------------------------------------------------------------------------------
def _ensure_operator():
    """Register the modal operator once per process (idempotent)."""
    import bpy

    if hasattr(bpy.types, "BTK_OT_target_weld"):
        return

    class BTK_OT_target_weld(bpy.types.Operator):
        """Interactively merge one vertex onto another by dragging (Maya Target Weld)."""

        bl_idname = "btk.target_weld"
        bl_label = "Target Weld"
        bl_options = {"REGISTER"}
        merge_to_center: bpy.props.BoolProperty(
            name="Merge To Center",
            description="Merge at the midpoint of the two vertices instead of at the target",
            default=False,
        )

        def invoke(self, context, event):
            return _op_invoke(self, context, event)

        def modal(self, context, event):
            return _op_modal(self, context, event)

        def cancel(self, context):
            _finish(self, context)

    bpy.utils.register_class(BTK_OT_target_weld)


def target_weld(merge_to_center: bool = False) -> bool:
    """Activate the interactive Target Weld tool (mirror of Maya's ``MergeVertexTool``).

    Mirrors the Maya slot's activation prep exactly — enter component (Edit) mode on the
    active mesh, restrict the mask to vertices, clear the selection — then starts the modal
    tool in the 3D viewport. Runs correctly from the Qt marking-menu timer context (no
    ``bpy.context.window``) via a VIEW_3D override.

    Parameters:
        merge_to_center: ``False`` → merged vertex lands at the target (Maya Target Weld);
            ``True`` → at the midpoint (Maya's ``targetWeldCtx -mergeToCenter`` Weld Center).

    Returns:
        bool: True once the tool is running.

    Raises:
        RuntimeError: No 3D viewport, or no active mesh object to edit.
    """
    import bpy

    from blendertk.core_utils._core_utils import get_view3d_context, selected_objects

    _ensure_operator()
    # --background still has a VIEW_3D area in its screen layout, but a modal invoke there is
    # an "Invalid operator call" that CANCELs silently — refuse it deterministically instead.
    if bpy.app.background:
        raise RuntimeError(
            "Target Weld requires an interactive Blender session with a 3D viewport."
        )
    ctx = get_view3d_context()
    if not ctx or not ctx.get("region"):
        raise RuntimeError("No 3D viewport available — open a 3D view to use Target Weld.")
    with bpy.context.temp_override(**ctx):
        active = bpy.context.view_layer.objects.active
        if active is None or active.type != "MESH":
            # Maya's tool works on the selected mesh — fall back like it does.
            meshes = [o for o in selected_objects() if o.type == "MESH"]
            if not meshes:
                raise RuntimeError("Target Weld requires an active or selected mesh object.")
            active = meshes[0]
            bpy.context.view_layer.objects.active = active
        if active.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.btk.target_weld("INVOKE_DEFAULT", merge_to_center=merge_to_center)
    return True


class TargetWeld:
    """Namespace class (mirror of the co-located-tool convention; Maya's counterpart is the
    native ``targetWeldCtx`` context, so only the entry point and pure helpers are public)."""

    activate = staticmethod(target_weld)
    weld_pair = staticmethod(weld_pair)
    weld_position = staticmethod(weld_position)
    project_points = staticmethod(project_points)
    pick_screen_point = staticmethod(pick_screen_point)
    dash_segments = staticmethod(dash_segments)


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
