# !/usr/bin/python
# coding=utf-8
"""Dynamic Pipe tool — Blender port of mayatk's ``edit_utils.dynamic_pipe``.

Maya builds the pipe by lofting NURBS circles parented to a chain of locators; **Blender has no
NURBS loft**, so the faithful analogue (same UX — "move a handle, the pipe follows live") is a curve
whose control points are driven by **Hook modifiers** (one per handle object) plus a round **bevel**
for thickness. Moving a handle in the viewport updates its hooked control point and the bevel
re-sweeps — exactly like Maya's history-driven loft following the locators.

Divergences (documented for parity — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
  * No per-locator NURBS circle + loft → one beveled curve. ``bevel_depth`` = radius gives the round
    cross-section (folding in Maya's per-locator ``circle``); ``bevel_resolution`` = profile
    smoothness. Maya's two-phase build (``__init__`` then ``create_pipe_geometry`` to loft segments)
    collapses to one step — the curve built in ``__init__`` *is* the whole pipe.
  * Blender has **no ordered selection** (Maya's ``ls(orderedSelection=True)``); the Slots order the
    selected handles by name so the chain is user-controllable (``handle_01``, ``handle_02``, …).
  * ``import bpy`` is deferred into the call bodies.

The co-located ``DynamicPipeSlots`` panel is discovered by ``BlenderUiHandler``
(``marking_menu.show("dynamic_pipe")``); like mayatk's, it is shelf/handler-launched and is **not**
wired to a tentacle nav button — Maya does not expose ``dynamic_pipe`` through tentacle either
(see ``tentacle/docs/archive/BLENDER_FEATURE_GAPS.md``), so adding one would be a divergence, not parity.

The ``.ui`` is now byte-for-byte mayatk's (see the mayatk/blendertk parity sweep); mayatk's copy
carries a stale ``windowTitle``/header ("Create Shader Network" / "CREATE STINGRAY SHADER" — a
leftover from ``mat_utils/game_shader.ui``, not a `dynamic_pipe`-authored string). That looks like an
upstream mayatk bug rather than an intentional label; ported verbatim per the parity mandate since
mayatk is read-only source of truth for this pass — flag for a mayatk-side fix in a follow-up.
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils


class DynamicPipe(ptk.LoggingMixin):
    """Build a pipe-style mesh driven by a chain of handle objects (Empties/locators) — Blender
    mirror of mayatk's ``DynamicPipe``.

    Public attributes:
        handles (list): Final ordered handle objects (originals + inserted in-betweens) — the
            Blender analogue of mayatk's ``locators``.
        curve (bpy.types.Object): The beveled curve object that *is* the pipe (mirror of mayatk's
            ``curve``; the bevel folds in mayatk's per-locator ``circles``).
    """

    def __init__(self, handles, num_inbetween=0, radius=1.0, resolution=4):
        import bpy

        handles = [o for o in ptk.make_iterable(handles) if o]
        if len(handles) < 2:
            raise ValueError("At least two handle objects are required.")
        self.radius = float(radius)
        self.resolution = int(resolution)
        # Settle the depsgraph so a just-placed/created handle's matrix_world is current, not stale
        # at the origin, before we read positions (the matrix_world-is-lazy gotcha).
        bpy.context.view_layer.update()
        self.handles = self._with_inbetweens(handles, int(num_inbetween))
        bpy.context.view_layer.update()  # newly-created in-between Empties' matrices now current
        self.curve = self._build_curve(self.handles)
        self._add_hooks(self.curve, self.handles)
        bpy.context.view_layer.update()  # settle the hooked control points

    # ------------------------------------------------------------------ build

    @classmethod
    def from_selection(cls, **kwargs) -> "DynamicPipe":
        """A pipe through the selected handle objects (Empties), chained in
        name order -- Blender keeps no selection order -- built as one undo
        step (mirror of mayatk's ``from_selection``).

        Parameters:
            **kwargs: The constructor's options (``num_inbetween``, ``radius``,
                ``resolution``).

        Raises:
            ValueError: Fewer than two objects are selected (checked before
                the undo step opens, so a refused click leaves none).
        """
        handles = sorted(CoreUtils.selected_objects(), key=lambda o: o.name)
        if len(handles) < 2:
            raise ValueError(
                "Select at least two handle objects (Empties), named in path order."
            )
        return cls._build_undoable(handles, **kwargs)

    @classmethod
    @CoreUtils.undoable
    def _build_undoable(cls, handles, **kwargs) -> "DynamicPipe":
        """The pipe through *handles*, one undo step (:meth:`from_selection`)."""
        return cls(handles, **kwargs)

    @staticmethod
    def _world_pos(obj):
        """World-space origin of a handle object (mirror of mayatk's ``_world_pos`` xform query)."""
        return obj.matrix_world.translation.copy()

    def _with_inbetweens(self, base, n):
        """Insert ``n`` linearly-interpolated Empties between each consecutive handle pair — mirror
        of mayatk's ``_with_inbetweens`` (``spaceLocator`` → Empty). The new Empties become hook
        handles too, so the curve resolution can grow without hand-placing extra nodes."""
        import bpy

        if n <= 0:
            return list(base)
        result = []
        for i, handle in enumerate(base):
            result.append(handle)
            if i == len(base) - 1:
                break
            start = self._world_pos(handle)
            end = self._world_pos(base[i + 1])
            collection = (
                handle.users_collection[0]
                if handle.users_collection
                else bpy.context.collection
            )
            for k in range(1, n + 1):
                t = k / float(n + 1)
                empty = bpy.data.objects.new(f"{handle.name}_inbetween", None)
                empty.empty_display_type = "PLAIN_AXES"
                empty.location = start.lerp(end, t)
                collection.objects.link(empty)
                result.append(empty)
        return result

    def _build_curve(self, handles):
        """A NURBS curve **at the world origin** (identity matrix → clean hook bind) with one
        control point per handle, beveled into a round tube. ``order_u`` = degree-3 (order 4) where
        there are ≥4 points, else linear — mirror of mayatk's ``_build_curve`` degree fallback."""
        import bpy

        cu = bpy.data.curves.new("DynamicPipe", "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = self.radius
        cu.bevel_resolution = self.resolution
        cu.use_fill_caps = False  # open tube, like Maya's open loft

        spline = cu.splines.new("NURBS")
        spline.points.add(len(handles) - 1)  # one control point exists by default
        for pt, handle in zip(spline.points, handles):
            p = self._world_pos(handle)
            pt.co = (p.x, p.y, p.z, 1.0)
        spline.order_u = min(
            4, len(handles)
        )  # ≤ point count (else Blender clamps/refuses)
        spline.use_endpoint_u = True  # pass through the first/last handle

        obj = bpy.data.objects.new("DynamicPipe", cu)
        bpy.context.collection.objects.link(obj)
        return obj

    def _add_hooks(self, curve, handles):
        """Bind one Hook modifier per handle to its control point so moving the handle moves the
        point live (the faithful analogue of Maya's circle parented under its locator).

        Each control point is bound via the shared ``edit_utils.hook_curve_point`` (rigid hook +
        the gotcha-laden no-jump ``matrix_inverse`` formula), the same helper ``TubeRig`` uses for
        its Spline-IK driver curve.
        """
        from blendertk.edit_utils._edit_utils import EditUtils

        for i, handle in enumerate(handles):
            EditUtils.hook_curve_point(curve, i, handle, name=f"Hook_{handle.name}")


# -----------------------------------------------------------------------------

