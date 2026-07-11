# !/usr/bin/python
# coding=utf-8
"""Bridge tool — engine + Switchboard slot wiring for the co-located ``bridge.ui``.

Blender port of mayatk's ``edit_utils.bridge`` (``btk.Bridge`` <-> ``mtk.Bridge``): connect two
open edge loops with new faces — the same open-border bridge as Maya's ``polyBridgeEdge``.

``bmesh.ops.bridge_loops`` is used over the ``bpy.ops.mesh.bridge_edge_loops`` operator on purpose
(same reason as :mod:`~blendertk.edit_utils.bevel`): it needs no 3D-view / region context, so it
runs identically interactively and under headless ``--background`` (the operator's ``poll`` wants a
viewport). The optional **divisions** are added by subdividing the new spanning edges, mirroring
Maya's "``divisions+1`` rows of faces". **Smoothing Angle** is a small addition on the same call:
the new spanning edges get ``edge.smooth`` set from their dihedral face angle — the same idiom
already used by ``crease_edges`` / ``set_edge_hardness`` in ``_edit_utils``.

The ``.ui`` is a verbatim copy of mayatk's — same objectNames (``chk000``, ``cmb000``, ``s000-4``,
``chk001``, ``b000``) — now that uitk host-namespaces the QSettings branch per DCC
(``Switchboard.add_ui`` / ``MainWindow._relative_state`` via ``context_tags``), identical
objectNames across mayatk's and blendertk's copy of the same panel no longer collide in the shared
"uitk"/"shared" registry root, so there is no need to renumber widgets to dodge it.

Divergence (documented for parity bookkeeping — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
``bmesh.ops.bridge_loops`` is a *linear* bridge with two knobs — vertex-pairing **offset**
(``twist_offset``) and **merge**. Maya's ``Curve Type`` "Blend"/"Curve" items (the latter building
a NURBS handle you can shape), **Taper**, and **Twist** have no ``bmesh`` analogue, so those combo
items / widgets are disabled at runtime (grayed, with a one-line tooltip) rather than faked — see
``BridgeSlots.__init__`` for the per-widget reasoning. **Cleanup** (``chk001``) exists in Maya only
to delete the temporary NURBS handle + construction history a Curve-type bridge leaves behind;
Blender's bridge never creates one, so it is disabled too. Maya's ``PRESERVE_GEOMETRY``
construction-history workaround for historyless meshes has no blendertk equivalent to port —
blendertk's ``Preview`` always snapshots/restores a full datablock copy regardless of what the
operation does, so that hazard doesn't exist here.

``import bpy`` / ``bmesh`` are deferred into the call bodies and the Qt-only ``uitk`` helper into
its method.
"""
import math

import pythontk as ptk

from blendertk.core_utils.preview import Preview
from blendertk.edit_utils._edit_utils import _meshes, _edit_mesh_each


class Bridge:
    """Native ``bmesh.ops.bridge_loops`` engine (mirror of mayatk's ``Bridge``)."""

    @staticmethod
    def bridge(objects=None, divisions=0, smoothing_angle=None, offset=0, merge=False):
        """Bridge the two selected open edge loops of each given mesh with new faces.

        Operates on the live Edit-Mode edge selection — like Maya's edge-component bridge. The
        Object<->Edit round-trip (and its no-stacking guarantee on the Preview path) is handled by
        the shared :func:`~blendertk.edit_utils._edit_utils._edit_mesh_each`.

        Parameters:
            objects (list): Mesh objects to bridge. ``None`` -> the current selection.
            divisions (int): Extra edge loops across the bridge (``divisions+1`` rows of faces).
            smoothing_angle (float): Degrees; the new spanning edges are marked smooth below this
                dihedral angle, hard at/above it (mirrors Maya's ``smoothingAngle``). ``None``
                skips the pass, leaving the new edges at ``bmesh``'s default (smooth).
            offset (int): Vertex-pairing offset between the two loops (``bmesh`` ``twist_offset``).
            merge (bool): Merge the loops instead of bridging (collapses the gap).

        Returns:
            int: The number of faces created (0 → nothing bridged; raises instead, see below).

        Raises:
            RuntimeError: when the selection isn't two bridgeable open loops (friendly message,
                mirroring Maya's diagnosed failure rather than a raw traceback).
        """
        import bmesh
        from blendertk.core_utils._core_utils import selected_objects

        meshes = _meshes(selected_objects() if objects is None else objects)
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
            if smoothing_angle is not None:
                threshold = math.radians(smoothing_angle)
                for e in new_edges:
                    try:
                        e.smooth = e.calc_face_angle() < threshold
                    except ValueError:
                        pass  # border/degenerate rung (one linked face) — no dihedral angle
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
    """Switchboard slot wiring for the bridge UI — 1:1 mirror of mayatk's ``BridgeSlots`` (same
    objectNames, same layout, same widget count).

    Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helper is deferred into
    ``header_init``.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bridge
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[bridge] ")

        # Only "Linear" has a bmesh analogue (bmesh.ops.bridge_loops is inherently a linear
        # vertex-to-vertex bridge); disable just the Blend/Curve items, not the whole combo —
        # same per-item-disable idiom as mirror.py's cmb001 "Merge: Extrude". Must run before
        # add_reset_buttons/connect_multi wrap the widgets (same ordering reason as those calls).
        self._disable_unsupported_curve_types()

        # Cleanup (chk001) only matters for Maya's Curve bridge type (deletes the temporary
        # NURBS handle + construction history); Blender's bridge never creates one, so the
        # whole checkbox is inapplicable. Disabled (not hidden) so the layout still matches
        # mayatk's 1:1.
        self.ui.chk001.setEnabled(False)
        self.ui.chk001.setToolTip(
            "Not applicable in Blender — bmesh.ops.bridge_loops never creates a curve handle "
            "to clean up (see the Type combo)."
        )
        # TODO(blender-parity): re-enable alongside a future Curve bridge type, if one is added.

        # Taper / Twist drive Maya's NURBS extrusion path (scale / rotation along the bridge);
        # bmesh.ops.bridge_loops has no path/profile to apply them to (only the integer vertex-
        # pairing Offset in s002), so there is no small addition that implements them for real.
        for name, reason in (
            (
                "s003",
                "Not supported in Blender — Taper has no bmesh.ops.bridge_loops equivalent "
                "(the bridge has no extrusion path/profile to scale along).",
            ),
            (
                "s004",
                "Not supported in Blender — Twist has no bmesh.ops.bridge_loops equivalent "
                "(only the integer vertex-pairing Offset above is supported).",
            ),
        ):
            w = getattr(self.ui, name)
            w.setEnabled(False)
            w.setToolTip(reason)
        # TODO(blender-parity): Taper / Twist would need a per-ring scale/rotation pass over
        # the divisions-subdivided rungs — real geometry work, not a small addition.

        # Per-field reset buttons (uitk option-box) must precede connect_multi/Preview —
        # wrapping reparents the widgets and invalidates any already-deferred wrapper.
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            undo_message="Bridge",
        )

        self.sb.connect_multi(
            self.ui, "cmb000", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "s000-4", "valueChanged", self.preview.refresh)
        self.sb.connect_multi(self.ui, "chk001", "toggled", self.preview.refresh)

    def _disable_unsupported_curve_types(self) -> None:
        """Disable the "Blend" and "Curve" items (cmb000 indices 1-2) — both need a NURBS-style
        cross-section blend / control handle that ``bmesh.ops.bridge_loops`` has no equivalent
        for. Only those two items are disabled; Linear (index 0, the default) still bridges
        normally.
        """
        model = self.ui.cmb000.model()
        for index, reason in (
            (
                1,
                "Not supported in Blender — bmesh.ops.bridge_loops has no cross-section "
                "blend, only a linear vertex-to-vertex bridge.",
            ),
            (
                2,
                "Not supported in Blender — a Curve bridge needs a NURBS control handle, "
                "which bmesh has no equivalent for.",
            ),
        ):
            item = model.item(index) if model is not None else None
            if item is not None:
                item.setFlags(item.flags() & ~self.sb.QtCore.Qt.ItemIsEnabled)
                item.setToolTip(reason)

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.set_help_text(
            fmt(
                title="Bridge",
                body="Connect two open edge loops with new polygon faces.",
                steps=[
                    "In Edit Mode, select the border edges of <b>two open loops</b> (holes) on "
                    "the same mesh — both with a matching edge count.",
                    "<b>Type</b> is Linear only here — Blend / Curve have no bmesh equivalent "
                    "and are disabled.",
                    "Adjust <b>Divisions</b>, <b>Smoothing Angle</b>, and <b>Offset</b>.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Create</b> to commit.",
                ],
                notes=[
                    "Taper, Twist, and Cleanup are Maya-only (NURBS extrusion-path / curve-"
                    "handle concepts) and are disabled — see each widget's tooltip.",
                    "Preview snapshots the mesh and re-bridges the same captured loops on each "
                    "change, so values never stack.",
                ],
            )
        )

    def perform_operation(self, objects):
        Bridge.bridge(
            objects,
            divisions=self.ui.s000.value(),
            smoothing_angle=self.ui.s001.value(),
            offset=self.ui.s002.value(),
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
