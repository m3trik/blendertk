# !/usr/bin/python
# coding=utf-8
"""Bevel tool — engine + Switchboard slot wiring for the co-located ``bevel.ui``.

Blender port of mayatk's ``edit_utils.bevel`` (``btk.Bevel`` ↔ ``mtk.Bevel``): a thin wrapper
over native ``bmesh.ops.bevel`` driving the selected edges (or vertices) of the active Edit-Mode
selection — the same edge-component bevel as Maya — plus a ``BevelSlots`` panel that is a 1:1
mirror of mayatk's (``chk000`` Preview / ``s000`` Width / ``s001`` Segments / ``b000`` Create).
The Slots class is discovered and served by ``BlenderUiHandler`` (``marking_menu.show("bevel")``),
exactly the mayatk/MayaUiHandler split, so the panel lives in blendertk next to its engine rather
than in tentacle.

``bmesh.ops.bevel`` is used over the ``bpy.ops.mesh.bevel`` operator on purpose: it needs no 3D
view / region context, so it runs identically interactively and under headless ``--background``
(the operator's ``poll`` wants a viewport). ``import bpy`` is deferred into the call bodies and
the Qt-only ``uitk`` helper into its method, so importing the module / resolving the package
surface never needs a running Blender or Qt.
"""
import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import _meshes, _edit_mesh_each


class Bevel:
    """Native ``bmesh.ops.bevel`` engine (mirror of mayatk's ``Bevel``)."""

    @staticmethod
    def bevel(
        objects=None,
        width=0.1,
        segments=1,
        profile=0.5,
        clamp_overlap=True,
        affect="EDGES",
        offset_type="OFFSET",
    ):
        """Bevel the selected edges (or vertices) of the given mesh objects.

        Operates on the live Edit-Mode component selection — like Maya's edge-component bevel. The
        Object↔Edit round-trip (and its no-stacking guarantee on the Preview path) is handled by
        the shared :func:`~blendertk.edit_utils._edit_utils._edit_mesh_each`.

        Parameters:
            objects (list): Mesh objects to bevel. ``None`` → the current selection.
            width (float): Bevel offset (interpretation set by ``offset_type``).
            segments (int): Number of bevel segments.
            profile (float): Bevel profile shape (0–1; 0.5 = round). Not exposed on the panel
                (mayatk's UI has no profile control, so this panel mirrors it 1:1) — still tunable
                for callers that want it.
            clamp_overlap (bool): Clamp the width so adjacent bevels don't overlap. Not exposed on
                the panel for the same reason — still tunable for callers that want it.
            affect (str): ``"EDGES"`` or ``"VERTICES"``.
            offset_type (str): Offset interpretation — ``"OFFSET"`` / ``"WIDTH"`` / ``"DEPTH"``
                / ``"PERCENT"`` / ``"ABSOLUTE"``.

        Returns:
            int: The number of components beveled (0 when the selection was empty).
        """
        import bpy
        import bmesh

        meshes = _meshes(bpy.context.selected_objects if objects is None else objects)
        if not meshes:
            raise RuntimeError("Bevel requires a mesh selection.")

        def _do(bm, _obj):
            geom = (
                [e for e in bm.edges if e.select]
                if affect == "EDGES"
                else [v for v in bm.verts if v.select]
            )
            if not geom:
                return 0
            bmesh.ops.bevel(
                bm,
                geom=geom,
                offset=width,
                offset_type=offset_type,
                segments=segments,
                profile=profile,
                affect=affect,
                clamp_overlap=clamp_overlap,
            )
            return len(geom)

        total = _edit_mesh_each(meshes, _do)
        if not total:
            raise RuntimeError(
                "Nothing to bevel — select edge(s) (or vertices) in Edit Mode first."
            )
        return total


class BevelSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the bevel UI — 1:1 mirror of mayatk's ``BevelSlots`` (same
    objectNames, same layout, same widget count: Preview / Width / Segments / Create).

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on
    tentacle; the Qt-only ``uitk`` helper is deferred into ``header_init``.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bevel
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[bevel] ")

        # Per-field reset buttons must precede connect_multi/Preview (wrap-first — reparenting
        # a QUiLoader spinbox after a deferred-widget access invalidates its wrapper). Wrap the
        # whole UI (no range arg) like duplicate_grid / cut_on_axis — the range form leaves the
        # later connect_multi resolving a stale spinbox wrapper.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Bevel",
        )

        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Bevel",
                body="Add chamfer bevels to selected polygon edges.",
                steps=[
                    "In Edit Mode, select one or more edges (or vertices).",
                    "Set <b>Width</b> (offset distance) and <b>Segments</b> (1+).",
                    "Toggle <b>Preview</b> to iterate non-destructively, "
                    "or press <b>Create</b> to commit.",
                ],
                notes=[
                    "Profile shape and clamp-overlap use the <i>Bevel.bevel</i> defaults "
                    "(round profile, clamped). Edit the slot if you need finer control.",
                ],
            )
        )

    def perform_operation(self, objects):
        width = self.ui.s000.value()
        segments = self.ui.s001.value()
        # 1:1 with mayatk's panel — mayatk's Bevel UI exposes only Width/Segments, so this panel
        # mirrors it verbatim. Bevel.bevel still accepts profile= / clamp_overlap= for callers
        # that want them; they just aren't wired to a widget here.
        Bevel.bevel(objects, width, segments)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("bevel", reload=True)
    ui.show(pos="screen", app_exec=True)
