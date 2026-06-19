# !/usr/bin/python
# coding=utf-8
"""Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.

Blender port of mayatk's ``edit_utils.bridge`` (``btk.Bridge`` ↔ ``mtk.Bridge``): connect two
open edge loops with new faces — the same open-border bridge as Maya's ``polyBridgeEdge``.

``bmesh.ops.bridge_loops`` is used over the ``bpy.ops.mesh.bridge_edge_loops`` operator on purpose
(same reason as :mod:`~blendertk.edit_utils.bevel`): it needs no 3D-view / region context, so it
runs identically interactively and under headless ``--background`` (the operator's ``poll`` wants a
viewport). The optional **divisions** are added by subdividing the new spanning edges, mirroring
Maya's "``divisions+1`` rows of faces".

Divergence (documented for parity bookkeeping — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
``bmesh.ops.bridge_loops`` is a *linear* bridge with two knobs — vertex-pairing **offset**
(``twist_offset``) and **merge**. Maya's ``Curve Type`` (Linear / Blend / Curve, the last building
a NURBS handle), ``Smoothing Angle``, and ``Taper`` have no ``bmesh`` analogue, so those widgets in
the shared ``.ui`` are hidden at runtime (like ``wheel_rig`` hides its vestigial widgets) rather
than faked. ``import bpy`` / ``bmesh`` are deferred into the call bodies and the Qt-only ``uitk``
helper into its method.
"""
import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import _meshes, _edit_mesh_each


class Bridge:
    """Native ``bmesh.ops.bridge_loops`` engine (mirror of mayatk's ``Bridge``)."""

    @staticmethod
    def bridge(objects=None, divisions=0, offset=0, merge=False):
        """Bridge the two selected open edge loops of each given mesh with new faces.

        Operates on the live Edit-Mode edge selection — like Maya's edge-component bridge. The
        Object↔Edit round-trip (and its no-stacking guarantee on the Preview path) is handled by
        the shared :func:`~blendertk.edit_utils._edit_utils._edit_mesh_each`.

        Parameters:
            objects (list): Mesh objects to bridge. ``None`` → the current selection.
            divisions (int): Extra edge loops across the bridge (``divisions+1`` rows of faces).
            offset (int): Vertex-pairing offset between the two loops (``bmesh`` ``twist_offset``).
            merge (bool): Merge the loops instead of bridging (collapses the gap).

        Returns:
            int: The number of faces created (0 → nothing bridged; raises instead, see below).

        Raises:
            RuntimeError: when the selection isn't two bridgeable open loops (friendly message,
                mirroring Maya's diagnosed failure rather than a raw traceback).
        """
        import bpy
        import bmesh

        meshes = _meshes(bpy.context.selected_objects if objects is None else objects)
        if not meshes:
            raise RuntimeError("Bridge requires a mesh selection.")

        def _do(bm, _obj):
            edges = [e for e in bm.edges if e.select]
            if len(edges) < 2:
                return 0
            try:
                ret = bmesh.ops.bridge_loops(
                    bm,
                    edges=edges,
                    use_pairs=False,
                    use_cyclic=False,
                    use_merge=merge,
                    merge_factor=0.5,
                    twist_offset=int(offset),
                )
            except RuntimeError:
                return 0  # this mesh's selection wasn't bridgeable; reported below via total==0
            new_edges = ret.get("edges", [])
            if divisions and new_edges:
                # Subdivide the new spanning edges → (divisions+1) rows of faces (Maya parity).
                bmesh.ops.subdivide_edges(bm, edges=new_edges, cuts=int(divisions))
            return len(ret.get("faces", []))

        total = _edit_mesh_each(meshes, _do)
        if not total:
            raise RuntimeError(
                "Nothing to bridge — select two open edge loops (border edges of two holes) on "
                "the same mesh. Both loops should have a matching edge count."
            )
        return total


class BridgeSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the bridge UI (live preview + divisions / offset).

    Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into
    ``header_init``.
    """

    # Widgets in the shared .ui with no bmesh.ops.bridge_loops analogue (see module docstring).
    # Hidden rather than deleteLater (the runtime loader can invalidate deleted-widget wrappers).
    _VESTIGIAL = ("cmb000", "s001", "s003", "s004", "chk001")

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bridge
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[bridge] ")

        # Per-field reset buttons must precede connect_multi/Preview (wrap-first — reparenting a
        # QUiLoader spinbox after a deferred-widget access invalidates its wrapper).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Bridge",
        )

        self.sb.connect_multi(self.ui, "s000", "valueChanged", self.preview.refresh)
        self.sb.connect_multi(self.ui, "s002", "valueChanged", self.preview.refresh)

        for name in self._VESTIGIAL:
            w = getattr(self.ui, name, None)
            if w is not None:
                w.setVisible(False)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Bridge",
                body="Connect two open edge loops with new polygon faces.",
                steps=[
                    "In Edit Mode, select the border edges of <b>two open loops</b> (holes) on "
                    "the same mesh — both with a matching edge count.",
                    "Set <b>Divisions</b> for extra rows across the bridge and <b>Offset</b> to "
                    "shift which vertices pair up.",
                    "Toggle <b>Preview</b> to iterate non-destructively, or press <b>Create</b> "
                    "to commit.",
                ],
                notes=[
                    "Preview snapshots the mesh and re-bridges the same captured loops on each "
                    "change, so values never stack.",
                    "Blender's bridge is linear; Maya's Curve/Blend types, Smoothing Angle and "
                    "Taper have no Blender equivalent and are hidden.",
                ],
            )
        )

    def perform_operation(self, objects):
        Bridge.bridge(
            objects,
            divisions=self.ui.s000.value(),
            offset=self.ui.s002.value(),
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
