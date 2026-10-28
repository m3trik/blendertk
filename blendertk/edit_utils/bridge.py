# !/usr/bin/python
# coding=utf-8
"""Bridge: connect two open edge loops with new faces — mirror of mayatk's
``edit_utils.bridge.Bridge`` (``btk.Bridge`` <-> ``mtk.Bridge``), the API tentacle's Bridge
panel calls: the same open-border bridge as Maya's ``polyBridgeEdge``.

``bmesh.ops.bridge_loops`` is used over the ``bpy.ops.mesh.bridge_edge_loops`` operator on purpose
(same reason as :mod:`~blendertk.edit_utils.bevel`): it needs no 3D-view / region context, so it
runs identically interactively and under headless ``--background`` (the operator's ``poll`` wants a
viewport). The optional **divisions** are added by subdividing the new spanning edges, mirroring
Maya's "``divisions+1`` rows of faces". **Smoothing Angle** is a small addition on the same call:
the new spanning edges get ``edge.smooth`` set from their dihedral face angle — the same idiom
already used by ``crease_edges`` / ``set_edge_hardness`` in ``_edit_utils``.

Divergence (documented for parity bookkeeping — see ``tentacle/docs/PARITY_PORTING_PLAN.md``):
``bmesh.ops.bridge_loops`` is a *linear* bridge with two knobs — vertex-pairing **offset**
(``twist_offset``) and **merge**. Maya's "Blend" / "Curve" curve types (the latter building a NURBS
handle you can shape), **Taper**, and **Twist** have no ``bmesh`` analogue, so ``Bridge`` names them
in ``UNSUPPORTED_OPTIONS`` (with the reason) and refuses them off their neutral values rather than
faking them; the shared panel disables their controls. **Cleanup** exists in Maya only to delete
the temporary NURBS handle + construction history a Curve bridge leaves behind; this bridge never
creates one, so ``cleanup_curves`` has nothing to do. Maya's ``PRESERVE_GEOMETRY``
construction-history workaround has no blendertk equivalent to port — blendertk's ``Preview``
always snapshots/restores a full datablock copy regardless of what the operation does, so that
hazard doesn't exist here.

``import bpy`` / ``bmesh`` are deferred into the call bodies.
"""

import math

from blendertk.edit_utils._edit_utils import EditUtils


class Bridge:
    """Native ``bmesh.ops.bridge_loops`` engine (mirror of mayatk's ``Bridge``)."""

    #: The curve types :meth:`bridge` names, in ``curve_type`` order (mirror of mayatk's,
    #: the panel's Type combo order); only ``"linear"`` builds here.
    CURVE_TYPES = ("linear", "blend", "curve")

    #: :meth:`bridge` options (or curve types) this host cannot honor, by name, with the
    #: reason; the shared panel disables their controls (mayatk's is empty).
    UNSUPPORTED_OPTIONS = {
        "blend": "Not supported in Blender: bmesh.ops.bridge_loops has no cross-section "
        "blend, only a linear vertex-to-vertex bridge.",
        "curve": "Not supported in Blender: a Curve bridge needs a NURBS control handle, "
        "which bmesh has no equivalent for.",
        "taper": "Not supported in Blender: Taper has no bmesh.ops.bridge_loops equivalent "
        "(the bridge has no extrusion path to scale along).",
        "twist": "Not supported in Blender: Twist has no bmesh.ops.bridge_loops equivalent "
        "(only the integer vertex-pairing Offset is supported).",
        "cleanup_curves": "Not applicable in Blender: bmesh.ops.bridge_loops never creates "
        "a curve handle to clean up.",
    }

    @staticmethod
    def bridge(
        objects=None,
        divisions=0,
        smoothing_angle=None,
        offset=0,
        merge=False,
        *,
        curve_type=None,
        taper=None,
        twist=None,
        cleanup_curves=False,
    ):
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
            curve_type (str): ``"linear"`` (or ``None``); ``"blend"`` / ``"curve"`` raise
                (:attr:`UNSUPPORTED_OPTIONS`) — mirror of mayatk's keyword.
            taper (float): Only the neutral ``1.0`` (or ``None``); any other value raises.
            twist (float): Only the neutral ``0.0`` (or ``None``); any other value raises.
            cleanup_curves (bool): Mirror of mayatk's; nothing to clean up here.

        Returns:
            int: The number of faces created (0 → nothing bridged; raises instead, see below).

        Raises:
            ValueError: an option this host cannot build, off its neutral value (the reason
                from :attr:`UNSUPPORTED_OPTIONS`) — checked before the mesh is touched.
            RuntimeError: when the selection isn't two bridgeable open loops (friendly message,
                mirroring Maya's diagnosed failure rather than a raw traceback).
        """
        if curve_type not in (None, "linear"):
            raise ValueError(
                Bridge.UNSUPPORTED_OPTIONS.get(curve_type)
                or f"curve_type must be one of {Bridge.CURVE_TYPES}, not {curve_type!r}."
            )
        for option, value, neutral in (("taper", taper, 1.0), ("twist", twist, 0.0)):
            if value is not None and value != neutral:
                raise ValueError(Bridge.UNSUPPORTED_OPTIONS[option])
        # cleanup_curves: nothing to clean up -- this bridge never builds a curve handle.

        import bmesh
        from blendertk.core_utils._core_utils import CoreUtils

        meshes = EditUtils._meshes(
            CoreUtils.selected_objects() if objects is None else objects
        )
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

        total = EditUtils._edit_mesh_each(meshes, _do)
        if not total:
            raise RuntimeError(
                "Nothing to bridge — select two open edge loops (border edges of two holes) on "
                "the same mesh. Both loops should have a matching edge count."
            )
        return total


# -----------------------------------------------------------------------------

