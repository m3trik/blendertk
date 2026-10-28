# !/usr/bin/python
# coding=utf-8
"""Bevel tool engine.

Blender port of mayatk's ``edit_utils.bevel`` (``btk.Bevel`` ↔ ``mtk.Bevel``): a thin wrapper
over native ``bmesh.ops.bevel`` driving the selected edges (or vertices) of the active Edit-Mode
selection — the same edge-component bevel as Maya. The panel is tentacle's shared Bevel tool
(``slots/tools/bevel.py``).

``bmesh.ops.bevel`` is used over the ``bpy.ops.mesh.bevel`` operator on purpose: it needs no 3D
view / region context, so it runs identically interactively and under headless ``--background``
(the operator's ``poll`` wants a viewport). ``import bpy`` is deferred into the call bodies and
the Qt-only ``uitk`` helper into its method, so importing the module / resolving the package
surface never needs a running Blender or Qt.
"""


from blendertk.edit_utils._edit_utils import EditUtils


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
        import bmesh
        from blendertk.core_utils._core_utils import CoreUtils

        meshes = EditUtils._meshes(
            CoreUtils.selected_objects() if objects is None else objects
        )
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

        total = EditUtils._edit_mesh_each(meshes, _do)
        if not total:
            raise RuntimeError(
                "Nothing to bevel — select edge(s) (or vertices) in Edit Mode first."
            )
        return total


# -----------------------------------------------------------------------------

