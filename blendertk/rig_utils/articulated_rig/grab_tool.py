# !/usr/bin/python
# coding=utf-8
"""The viewport grab for articulated rigs (Blender): press on a rigged part, drag, let
go -- the twin of mayatk's ``grab_tool.py``.

What a hand does to the rig in Unity or a headset, an animator does here: the held
point follows the cursor across the view-facing plane through it, and the rig is solved
by the same ``ptk.ArticulationModel`` the runtimes port -- so the pose keyed in Blender
is the pose a grab would have made. The controls are written, never the joints, so every
drag is keyable like any other edit: one undo step per drag, keyed on release when Auto
Keying is on. With the rig's end control on, a drag on the end link carries the end
control (what was moved, and what is keyed); a drag on a link above it poses the FK
controls, which reshapes the arm under the end link it pins.

:class:`ArticulatedRigGrab` is a thin modal operator (mayatk's is a ``draggerContext``)
over plain-value steps (:meth:`~ArticulatedRigGrab.press` /
:meth:`~ArticulatedRigGrab.drag` / :meth:`~ArticulatedRigGrab.release` on world rays),
which is what a test drives -- a modal operator needs a user's mouse. Like Target Weld
(``edit_utils/target_weld.py``) the tool stays on for drag after drag until Esc / RMB,
and every Alt-modified event and every view navigation passes through, so Alt-tumble
keeps working inside it. Other keys pass between drags, never during one: mid-drag a
key press is held back, so no edit (an undo above all) lands on the rig the drag holds.
"""

from typing import Any, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.rig_utils.articulated_rig._articulated_rig import ArticulatedRig

#: Events that move the view, never the rig.
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
        "NDOF_MOTION",
    }
)


class _ArticulatedRigGrabInternal:
    """The modal operator's bodies (module-level so the lazily built class stays
    thin; the ``target_weld.py`` pattern)."""

    #: The operator's id (``bpy.ops.btk.articulated_rig_grab``).
    OPERATOR = "btk.articulated_rig_grab"
    #: What the status bar says while the tool is on.
    STATUS = "Articulated Rig Grab  |  LMB-drag: pose the rig  |  Esc / RMB: exit"

    @staticmethod
    def _ensure_operator() -> None:
        """Register the modal operator once per process (idempotent)."""
        import bpy

        if hasattr(bpy.types, "BTK_OT_articulated_rig_grab"):
            return

        class BTK_OT_articulated_rig_grab(bpy.types.Operator):
            """Grab a part of an articulated rig and drag it into a pose."""

            bl_idname = "btk.articulated_rig_grab"
            bl_label = "Articulated Rig Grab"
            bl_options = {"REGISTER"}

            def invoke(self, context, event):
                return _ArticulatedRigGrabInternal._op_invoke(self, context, event)

            def modal(self, context, event):
                return _ArticulatedRigGrabInternal._op_modal(self, context, event)

            def cancel(self, context):
                _ArticulatedRigGrabInternal._finish(self, context)

        bpy.utils.register_class(BTK_OT_articulated_rig_grab)

    @staticmethod
    def _op_invoke(op, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            op.report({"WARNING"}, "The Articulated Rig Grab needs a 3D viewport.")
            return {"CANCELLED"}
        op._tool = ArticulatedRigGrab()
        context.window.cursor_modal_set("HAND")
        context.workspace.status_text_set(_ArticulatedRigGrabInternal.STATUS)
        context.window_manager.modal_handler_add(op)
        return {"RUNNING_MODAL"}

    @staticmethod
    def _finish(op, context):
        tool = getattr(op, "_tool", None)
        if tool is not None and tool.hold:
            tool.release()  # a drag the exit cut short keeps what it did
        if context.window is not None:
            context.window.cursor_modal_restore()
        if context.workspace is not None:
            context.workspace.status_text_set(None)
        return {"FINISHED"}

    @staticmethod
    def _in_region(context, event) -> bool:
        region = context.region
        return (
            region is not None
            and 0 <= event.mouse_region_x <= region.width
            and 0 <= event.mouse_region_y <= region.height
        )

    @staticmethod
    def _op_modal(op, context, event):
        tool = op._tool
        # Alt-modified input is view navigation in the Maya-style keymaps (and Maya
        # keeps Alt-tumble live inside a tool): it always passes through, and ends a
        # drag in flight -- its button belongs to the camera now.
        if event.alt:
            if tool.hold:
                tool.release()
            return {"PASS_THROUGH"}
        etype = event.type
        if etype in _NAV_EVENTS or (
            etype.startswith("NUMPAD") and etype != "NUMPAD_ENTER"
        ):
            return {"PASS_THROUGH"}
        if etype in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            if not tool.hold:
                return {"PASS_THROUGH"}
            tool.drag(*ArticulatedRigGrab.ray(context, event))
            if context.area is not None:
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if etype == "LEFTMOUSE":
            if event.value == "PRESS":
                if not _ArticulatedRigGrabInternal._in_region(context, event):
                    return {"PASS_THROUGH"}  # a click outside the viewport
                if not tool.press(*ArticulatedRigGrab.ray(context, event)):
                    op.report({"INFO"}, "Articulated Rig Grab: nothing rigged there.")
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE" and tool.hold:
                tool.release()
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}
        if etype in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return _ArticulatedRigGrabInternal._finish(op, context)
        if tool.hold and event.value == "PRESS":
            # Mid-drag the rig is the drag's: a key acting on it (G, X) fights the
            # drag, and an undo frees the bones it holds. Between drags keys pass.
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


class ArticulatedRigGrab(ptk.LoggingMixin, _ArticulatedRigGrabInternal):
    """Grab and drag the parts of articulated rigs in the viewport."""

    #: How far back along the view an orthographic pick ray starts.
    ORTHO_BACK = 1.0e6

    def __init__(self):
        self.rig: Optional[ArticulatedRig] = None
        self.hold: Optional[dict] = None
        self.plane: Optional[Tuple[Any, Any]] = None

    @classmethod
    def activate(cls) -> bool:
        """Make the grab the active tool in the 3D viewport -- first giving any end
        control without a solve its solve (:meth:`ArticulatedRig.repair_scene`).

        Returns:
            True once the tool runs.

        Raises:
            RuntimeError: A background Blender (a modal tool needs a window), or no
                3D viewport.
        """
        import bpy

        cls._ensure_operator()
        # --background has a 3D view in its layout, but a modal invoke there cancels
        # silently: refuse it plainly instead (as Target Weld does).
        if bpy.app.background:
            raise RuntimeError(
                "The Articulated Rig Grab needs an interactive Blender with a 3D view."
            )
        ArticulatedRig.repair_scene()
        context = CoreUtils.get_view3d_context()
        if not context or not context.get("region"):
            raise RuntimeError(
                "No 3D viewport: open one to use the Articulated Rig Grab."
            )
        with bpy.context.temp_override(**context):
            bpy.ops.btk.articulated_rig_grab("INVOKE_DEFAULT")
        return True

    # ============================================================ plain values
    @staticmethod
    def pick(
        origin: Sequence[float], direction: Sequence[float]
    ) -> Optional[Tuple[ArticulatedRig, Any, Tuple[float, float, float]]]:
        """The rigged part a world ray hits first: ``(rig, part, point)``, or None.
        Only parts riding a joint count -- the root link stays put. Each part's
        visible meshes are ray-cast in their own space, as evaluated."""
        from mathutils import Vector

        depsgraph = CoreUtils._evaluated_depsgraph()
        layer = CoreUtils._active_view_layer()  # the one the depsgraph evaluates
        source = Vector(origin)
        ray = Vector(direction).normalized()
        best = None
        for rig in ArticulatedRig.scene_rigs():
            spec = rig.spec
            nodes = rig._nodes()
            for joint in spec["joints"]:
                for member in spec["links"][joint["link"]]:
                    part = nodes.get(member["uuid"])
                    if not rig._alive(part):
                        continue
                    for obj in [part] + list(part.children_recursive):
                        if obj.type != "MESH" or not obj.visible_get(view_layer=layer):
                            continue
                        to_local = obj.matrix_world.inverted()
                        hit, location, _normal, _index = obj.ray_cast(
                            to_local @ source,
                            to_local.to_3x3() @ ray,
                            depsgraph=depsgraph,
                        )
                        if not hit:
                            continue
                        point = obj.matrix_world @ location
                        distance = (point - source).length
                        if best is None or distance < best[0]:
                            best = (distance, rig, part, tuple(point))
        return None if best is None else best[1:]

    def press(self, origin: Sequence[float], direction: Sequence[float]) -> bool:
        """Take hold of whatever rigged part the ray hits. Returns whether something
        was taken."""
        from mathutils import Vector

        found = self.pick(origin, direction)
        if found is None:
            return False
        rig, part, point = found
        self.rig = rig
        self.hold = rig.grab_begin(part, point)
        self.plane = (Vector(point), Vector(direction).normalized())
        return True

    def drag(
        self, origin: Sequence[float], direction: Sequence[float]
    ) -> Optional[list]:
        """Move the held point to where the ray crosses the view-facing plane through
        it, and pose the rig there (unkeyed until release)."""
        from mathutils import Vector
        from mathutils.geometry import intersect_line_plane

        if not self.hold:
            return None
        anchor, normal = self.plane
        start = Vector(origin)
        target = intersect_line_plane(
            start, start + Vector(direction).normalized(), anchor, normal
        )
        if target is None:
            return None  # a ray along the plane: nowhere to put the point
        return self.rig.grab_to(self.hold, tuple(target), key=False)

    def release(self) -> None:
        """Let go: key what the drag moved (the end control, or the FK controls) when
        Auto Keying is on, and make the drag one undo step."""
        held = self.hold is not None
        try:
            if held:
                self.rig.key_hold(self.hold)
        finally:
            self.rig = self.hold = self.plane = None
            if held:
                CoreUtils.undo_push("Articulated Rig: Grab")

    # ================================================================ viewport
    @classmethod
    def ray(
        cls, context, event
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """The world pick ray under the cursor of a viewport *event*: from the eye
        for a perspective view, along the view for an orthographic one."""
        from bpy_extras import view3d_utils

        region, view = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        direction = view3d_utils.region_2d_to_vector_3d(region, view, coord)
        origin = view3d_utils.region_2d_to_origin_3d(
            region, view, coord, clamp=cls.ORTHO_BACK
        )
        return tuple(origin), tuple(direction)
