# !/usr/bin/python
# coding=utf-8
"""Camera utilities — clip-plane adjustment (mirror of mayatk's ``cam_utils``) plus interactive
Maya-style viewport navigation (Tumble / Track / Dolly / Roll via ``navigate_view``).

Clip adjustment operates on camera object ``.data`` (no VIEW_3D context) → **headless-testable**,
as is the pure mouse-delta→view math backing the nav tools (``_orbit_rotation`` etc.); only the
modal nav *invoke* needs an interactive VIEW_3D. ``import bpy`` / ``mathutils`` are deferred into
the call bodies (no import side effects).
"""
import pythontk as ptk

# Blender camera clip defaults.
_NEAR_DEFAULT = 0.1
_FAR_DEFAULT = 1000.0


def _resolve_cameras(camera):
    """Coerce ``camera`` (object / list / None) to a list of camera *objects*. ``None`` ->
    the scene's active (render) camera — Blender's closest analogue to the viewport camera."""
    import bpy

    if not camera:
        cam = bpy.context.scene.camera
        return [cam] if cam else []
    return [o for o in ptk.make_iterable(camera) if getattr(o, "type", None) == "CAMERA"]


def _scene_bbox_corners():
    """The 8 world-space corners of the combined bbox of visible mesh objects (or all meshes)."""
    import bpy
    from mathutils import Vector
    import blendertk as btk

    geo = btk.get_visible_geometry()
    if not geo:
        # Fall back to every mesh in the current scene (not bpy.data.objects — that would also
        # sweep in objects belonging to other scenes / unlinked orphans).
        geo = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not geo:
        return None
    boxes = [btk.get_world_bbox(o) for o in geo]
    mn = Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)))
    mx = Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)))
    return [Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]


def _resolve_clip(value, max_dist, *, near):
    """Resolve a near/far clip directive to a float (or ``None`` to leave unchanged).

    ``'reset'`` -> Blender default; ``'auto'`` -> from ``max_dist`` (far = dist*1.2, near =
    far/3000 floored at 0.1, matching mtk's Z-precision ratio); a number -> itself.
    """
    if value == "reset":
        return _NEAR_DEFAULT if near else _FAR_DEFAULT
    if value == "auto":
        if max_dist <= 0:
            return _NEAR_DEFAULT if near else _FAR_DEFAULT
        far = max_dist * 1.2
        return max(far / 3000.0, _NEAR_DEFAULT) if near else far
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def adjust_camera_clipping(camera=None, near_clip=None, far_clip=None):
    """Adjust near/far clip planes of camera object(s) — mirror of ``mtk.adjust_camera_clipping``.

    ``camera=None`` targets the scene's active camera. ``near_clip``/``far_clip``: ``None``
    leaves unchanged, ``'auto'`` derives from the scene bbox vs. the camera position, ``'reset'``
    restores Blender defaults (0.1 / 1000.0), or pass a float.
    """
    cams = _resolve_cameras(camera)
    if not cams:
        return
    needs_auto = near_clip == "auto" or far_clip == "auto"
    corners = _scene_bbox_corners() if needs_auto else None
    for cam in cams:
        data = cam.data
        max_dist = 0.0
        if needs_auto and corners:
            cam_pos = cam.matrix_world.translation
            max_dist = max((c - cam_pos).length for c in corners)
        if near_clip is not None:
            val = _resolve_clip(near_clip, max_dist, near=True)
            if val is not None:
                data.clip_start = val
        if far_clip is not None:
            val = _resolve_clip(far_clip, max_dist, near=False)
            if val is not None:
                data.clip_end = val


# --------------------------------------------------------------------------- interactive view nav
# Maya-style viewport navigation (Tumble / Track / Dolly + Roll). Maya *arms a drag tool*
# (``setToolTo tumbleContext``/``trackContext``/``dollyContext``); Blender's native nav is always
# on via the mouse but not armable from a menu — so we roll our own: a modal operator that drags
# the RegionView3D exactly like Maya. The per-mode delta->view math below is pure (``mathutils``
# only) and headless-testable; only the modal *invoke* needs an interactive VIEW_3D.
_ORBIT_SENS = 0.005   # rad / pixel  (~0.29°/px; a 15° tumble ≈ 52 px)
_ROLL_SENS = 0.005    # rad / pixel
_DOLLY_SENS = 0.01    # fraction of view_distance / pixel
_TRACK_SENS = 0.001   # (fraction of view_distance) / pixel


def _orbit_rotation(view_rotation, dx, dy, sens=_ORBIT_SENS):
    """Turntable orbit (Maya Tumble): ``dx`` orbits around world-Z (azimuth), ``dy`` around the
    view's right axis (elevation). Returns the new view quaternion; the pivot (``view_location``)
    is unchanged, so the eye orbits around it."""
    from mathutils import Quaternion, Vector

    right = view_rotation @ Vector((1.0, 0.0, 0.0))
    azimuth = Quaternion((0.0, 0.0, 1.0), -dx * sens)
    elevation = Quaternion(right, dy * sens)  # +dy: drag up tilts the view up (Maya tumble feel)
    return azimuth @ elevation @ view_rotation


def _roll_rotation(view_rotation, dx, sens=_ROLL_SENS):
    """Roll around the view (forward) axis — the interactive analogue of Maya's discrete
    ``cmds.roll`` (Maya has no roll *tool*, so a drag roll is the natural interactive form)."""
    from mathutils import Quaternion, Vector

    forward = view_rotation @ Vector((0.0, 0.0, -1.0))
    return Quaternion(forward, dx * sens) @ view_rotation


def _dolly_distance(view_distance, dy, sens=_DOLLY_SENS, min_dist=1e-4):
    """Dolly (Maya Dolly): drag up (``dy`` > 0) moves the eye toward the pivot (smaller
    ``view_distance``). Multiplicative so it feels constant at any zoom; floored so the eye never
    crosses the pivot."""
    return max(min_dist, view_distance * (1.0 - dy * sens))


def _track_location(view_location, view_rotation, view_distance, dx, dy, sens=_TRACK_SENS):
    """Track / pan (Maya Track): slide the pivot *opposite* the drag on the view's right/up axes,
    scaled by ``view_distance`` so the scene follows the cursor (grab-and-drag) on BOTH axes at any
    zoom. Returns the new pivot location. (Moving the pivot translates the whole camera rig, so
    ``-right``/``-up`` move the camera against the drag, shifting the scene *with* it.)"""
    from mathutils import Vector

    right = view_rotation @ Vector((1.0, 0.0, 0.0))
    up = view_rotation @ Vector((0.0, 1.0, 0.0))
    scale = sens * view_distance
    return view_location - right * (dx * scale) - up * (dy * scale)


def _apply_view_nav(rv3d, mode, dx, dy):
    """Apply one mouse-delta step to a ``RegionView3D`` for the given nav ``mode``."""
    if mode == "ORBIT":
        rv3d.view_rotation = _orbit_rotation(rv3d.view_rotation, dx, dy)
    elif mode == "ROLL":
        rv3d.view_rotation = _roll_rotation(rv3d.view_rotation, dx)
    elif mode == "DOLLY":
        rv3d.view_distance = _dolly_distance(rv3d.view_distance, dy)
    elif mode == "TRACK":
        rv3d.view_location = _track_location(
            rv3d.view_location, rv3d.view_rotation, rv3d.view_distance, dx, dy
        )


_MODE_CURSOR = {"ORBIT": "SCROLL_XY", "DOLLY": "MOVE_Y", "TRACK": "SCROLL_XY", "ROLL": "SCROLL_X"}


def _view_nav_invoke(op, context, event):
    rv3d = getattr(context, "region_data", None)
    if context.area is None or context.area.type != "VIEW_3D" or rv3d is None:
        op.report({"WARNING"}, "Viewport navigation requires a 3D viewport.")
        return {"CANCELLED"}
    op._rv3d = rv3d
    op._dragging = False
    op._last = (event.mouse_region_x, event.mouse_region_y)
    # Snapshot for Esc-cancel (copy — these are live references that mutate as we navigate).
    op._start = (rv3d.view_rotation.copy(), rv3d.view_location.copy(), rv3d.view_distance)
    context.window.cursor_modal_set(_MODE_CURSOR.get(op.mode, "SCROLL_XY"))
    if context.workspace is not None:
        context.workspace.status_text_set(
            f"{op.mode.title()}  |  LMB-drag: navigate  |  RMB / Enter: finish  |  Esc: cancel"
        )
    context.window_manager.modal_handler_add(op)
    return {"RUNNING_MODAL"}


def _view_nav_finish(op, context, result):
    if context.window is not None:
        context.window.cursor_modal_restore()
    if context.workspace is not None:
        context.workspace.status_text_set(None)
    if context.area is not None:
        context.area.tag_redraw()
    return result


def _view_nav_modal(op, context, event):
    # Pass Maya-style Alt+drag native nav through untouched — consuming Alt breaks the
    # Industry-Compatible / emulated-3-button orbit/pan/dolly (see the modal-tool recipe).
    if event.alt:
        # Keep the drag anchor current while native nav owns the mouse:
        # without this, the first post-Alt MOUSEMOVE during an active drag
        # applies the whole Alt-period cursor travel as one large jump.
        op._last = (event.mouse_region_x, event.mouse_region_y)
        return {"PASS_THROUGH"}
    et, ev = event.type, event.value
    if et == "LEFTMOUSE":
        op._dragging = ev == "PRESS"
        op._last = (event.mouse_region_x, event.mouse_region_y)
        return {"RUNNING_MODAL"}
    if et == "MOUSEMOVE":
        if op._dragging:
            x, y = event.mouse_region_x, event.mouse_region_y
            _apply_view_nav(op._rv3d, op.mode, x - op._last[0], y - op._last[1])
            op._last = (x, y)
            if context.area is not None:
                context.area.tag_redraw()
        return {"RUNNING_MODAL"}
    if et == "ESC" and ev == "PRESS":
        rot, loc, dist = op._start  # restore the view we snapshotted at invoke
        op._rv3d.view_rotation, op._rv3d.view_location, op._rv3d.view_distance = rot, loc, dist
        return _view_nav_finish(op, context, {"CANCELLED"})
    if et in {"RIGHTMOUSE", "RET", "NUMPAD_ENTER"} and ev == "PRESS":
        return _view_nav_finish(op, context, {"FINISHED"})
    return {"RUNNING_MODAL"}


def _ensure_view_nav_operator():
    """Register the modal nav operator once per process (idempotent)."""
    import bpy

    if hasattr(bpy.types, "BTK_OT_view_nav"):
        return

    class BTK_OT_view_nav(bpy.types.Operator):
        """Interactive Maya-style viewport navigation (Orbit / Dolly / Track / Roll)."""

        bl_idname = "btk.view_nav"
        bl_label = "Viewport Navigate"
        bl_options = {"REGISTER"}
        mode: bpy.props.EnumProperty(
            name="Mode",
            items=[(m, m.title(), m.title()) for m in ("ORBIT", "DOLLY", "TRACK", "ROLL")],
            default="ORBIT",
        )

        def invoke(self, context, event):
            return _view_nav_invoke(self, context, event)

        def modal(self, context, event):
            return _view_nav_modal(self, context, event)

    bpy.utils.register_class(BTK_OT_view_nav)


def navigate_view(mode="ORBIT"):
    """Arm an interactive Maya-style viewport-navigation tool: **LMB-drag** to Orbit/Dolly/Track/
    Roll the 3D view — Blender's analogue of Maya's ``setToolTo tumbleContext``/``dollyContext``/
    ``trackContext`` (and, for Roll, its discrete ``cmds.roll`` given as a drag for consistency).

    Deferred one timer tick so the Qt click that launched it unwinds first, then invoked
    ``INVOKE_DEFAULT`` under a VIEW_3D override (the Qt event-pump has no active viewport) — the
    proven pattern from ``target_weld``.

    Parameters:
        mode: ``"ORBIT"`` | ``"DOLLY"`` | ``"TRACK"`` | ``"ROLL"``.

    Returns:
        bool: True once the tool is scheduled.

    Raises:
        RuntimeError: in ``--background`` — a modal invoke there CANCELs silently, so refuse it
            deterministically instead.
    """
    import bpy

    from blendertk.core_utils._core_utils import get_view3d_context

    _ensure_view_nav_operator()
    if bpy.app.background:
        raise RuntimeError(
            "Viewport navigation requires an interactive Blender session with a 3D viewport."
        )

    def _run():
        ctx = get_view3d_context()
        if ctx and ctx.get("region"):
            with bpy.context.temp_override(**ctx):
                bpy.ops.btk.view_nav("INVOKE_DEFAULT", mode=mode)
        return None  # one-shot

    bpy.app.timers.register(_run, first_interval=0.01)
    return True


class CamUtils:
    """Namespace mirror of mayatk's ``CamUtils`` (helper also exposed module-level)."""

    adjust_camera_clipping = staticmethod(adjust_camera_clipping)
    navigate_view = staticmethod(navigate_view)
