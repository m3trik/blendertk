# !/usr/bin/python
# coding=utf-8
"""Component-level mesh operations — mirror of mayatk's ``core_utils.components``.

So far the normals operations a panel shared by both hosts calls (tentacle's
``slots/normals.py``), each with mayatk's name and signature, over the
``EditUtils`` bmesh helpers. In ``edit_utils`` rather than mayatk's
``core_utils``: the kernel may not import the helpers it composes. ``objects``
``None`` takes the selection (``CoreUtils.selected``); in Edit Mode an
operation acts on the selected components, as Maya's do.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.edit_utils._edit_utils import EditUtils


class _ComponentsInternal(object):
    """Internal helpers for Components."""

    @staticmethod
    def _meshes(objects):
        return [o for o in objects or () if getattr(o, "type", None) == "MESH"]

    @staticmethod
    def _component_scope():
        """True in Edit Mode: an operation then acts on the selected components
        (falling back to whole meshes when none is selected), as Maya's do."""
        active = CoreUtils.active_object()
        return bool(active and active.mode == "EDIT")


class Components(_ComponentsInternal):
    """Component-level mesh operations — mirror of mayatk's ``Components``."""

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def set_edge_hardness(
        objects,
        angle_threshold,
        upper_hardness=None,
        lower_hardness=None,
        unlock_normals=False,
    ):
        """Mark edges hard or soft by their angle against ``angle_threshold``.

        Edges at or above it take ``upper_hardness``, below it ``lower_hardness``
        (Maya's 0..180; Blender edges are binary, so under 90 is hard, ``None``
        leaves that bucket). Custom split normals are Blender's locked normals
        and block the update: ``unlock_normals`` clears them first; without it
        the meshes carrying them are left untouched and returned.

        Returns:
            list: The names of the meshes skipped for locked normals; empty when
            the operation ran (or had nothing to do).
        """
        if upper_hardness is None and lower_hardness is None:
            return []
        meshes = Components._meshes(objects)
        if unlock_normals:
            EditUtils.clear_custom_split_normals(meshes)
        else:
            locked = [o.name for o in meshes if EditUtils.has_custom_split_normals([o])]
            if locked:
                return locked
        EditUtils.set_edge_hardness(
            meshes,
            angle=angle_threshold,
            upper_hardness=upper_hardness,
            lower_hardness=lower_hardness,
        )
        return []

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def average_normals(objects, by_uv_shell=False):
        """Average the normals of *objects* (soften every edge); ``by_uv_shell``
        keeps UV-island borders hard, so each shell smooths on its own."""
        EditUtils.average_normals(Components._meshes(objects), by_uv_shell=by_uv_shell)

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def set_edge_softness(objects, soft=True):
        """Soften (``soft=True``, smooth shading) or harden the edge normals of
        *objects*; in Edit Mode, of the selected components."""
        EditUtils.set_shading(
            Components._meshes(objects),
            smooth=soft,
            selected_only=Components._component_scope(),
        )

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def set_to_face_normals(objects):
        """Set the vertex normals of *objects* to their faces' (flat shading); in
        Edit Mode, of the selected components."""
        EditUtils.set_shading(
            Components._meshes(objects),
            smooth=False,
            selected_only=Components._component_scope(),
        )

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def reverse_normals(objects, mode=0):
        """Reverse the normals of *objects* by Maya's ``polyNormal`` modes:
        0 Reverse, 1 Propagate, 2 Conform, 3 Reverse and Extract, 4 Reverse and
        Propagate. Propagate and Extract are seeded by the selected faces."""
        meshes = Components._meshes(objects)
        if mode == 1:
            EditUtils.propagate_normals(meshes)
        elif mode == 2:
            EditUtils.conform_normals(meshes)
        elif mode == 3:
            EditUtils.extract_reversed_faces(meshes)
        elif mode == 4:
            EditUtils.propagate_normals(meshes, reverse=True)
        else:
            EditUtils.flip_normals(
                meshes, selected_only=Components._component_scope()
            )

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def toggle_normal_lock(objects):
        """Lock the normals of *objects* (bake them as custom split normals), or
        unlock them (clear those) when every mesh is locked already.

        Returns:
            True when now locked, False when unlocked, None when *objects* hold
            no mesh.
        """
        meshes = Components._meshes(objects)
        if not meshes:
            return None
        if EditUtils.has_custom_split_normals(meshes):
            EditUtils.clear_custom_split_normals(meshes)
            return False
        EditUtils.add_custom_split_normals(meshes)
        return True

    @staticmethod
    @CoreUtils.undoable
    def transfer_normals(objects, space="world"):
        """Transfer normals from the first mesh of *objects* to the rest, as
        custom split normals (native Data Transfer).

        ``None`` takes the selection, the active mesh as the source (it must be
        selected); with nothing selected, ``[]`` (mayatk's ``CoreUtils.selected``
        contract). ``space`` is mayatk's: ``"world"`` and ``"local"`` match the
        nearest face (in world or object space), ``"component"`` and
        ``"topology"`` by index, for meshes of one topology.

        Raises:
            ValueError: An unknown ``space``, fewer than two meshes, no selected
                active mesh for ``None``, or the transfer failing.
        """
        mappings = {
            "world": "POLYINTERP_NEAREST",
            "local": "POLYINTERP_NEAREST",
            "component": "TOPOLOGY",
            "topology": "TOPOLOGY",
        }
        if space not in mappings:
            raise ValueError(
                f"space parameter must be one of: {', '.join(mappings)}"
            )
        targets = Components.transfer_mesh_data(
            objects,
            "CUSTOM_NORMAL",
            loop_mapping=mappings[space],
            use_object_transform=space != "local",
        )
        return targets if targets == [] else None  # mayatk's: None once it ran

    @staticmethod
    def transfer_mesh_data(objects, data_type, **options):
        """Copy *data_type* from the first mesh of *objects* to the rest with Blender's
        Data Transfer (``object.data_transfer``): the primitive under the transfer
        operations (:meth:`transfer_normals`; UVs, colors, weights, creases).

        ``None`` takes the selection, the active mesh as the source (it must be
        selected); with nothing selected, ``[]`` (mayatk's ``CoreUtils.selected``
        contract). Blender's vocabulary, so no mayatk twin: an operation a panel
        shared by both hosts calls is named for what it transfers
        (``transfer_normals``), its blendertk half built on this and its mayatk
        half on ``transferAttributes``. No undo step of its own: its callers are.

        Parameters:
            objects (list/None): The meshes, the source first; others are dropped.
            data_type (str): ``object.data_transfer``'s ``data_type`` (``"UV"``,
                ``"CUSTOM_NORMAL"``, ``"VGROUP_WEIGHTS"``, ``"COLOR_VERTEX"``,
                ``"CREASE"`` ...).
            **options: The operator's other properties (``vert_mapping``,
                ``edge_mapping``, ``layers_select_src`` ...); ``loop_mapping``
                defaults to ``"POLYINTERP_NEAREST"`` (nearest face, interpolated).

        Returns:
            (list): The target meshes; ``[]`` with nothing selected.

        Raises:
            ValueError: No selected active mesh for ``None``, fewer than two
                meshes, or the operator's own report.
        """
        import bpy

        if objects is None:
            selection = CoreUtils.selected_objects()
            if not selection:
                return []
            active = CoreUtils.active_object()
            if active is None or active not in selection or active.type != "MESH":
                raise ValueError("Select target mesh(es) with the source mesh active.")
            objects = [active] + [o for o in selection if o != active]
        meshes = Components._meshes(objects)
        if len(meshes) < 2:
            raise ValueError("Transfer needs a source mesh and one or more target meshes.")
        source, targets = meshes[0], meshes[1:]
        options = {"loop_mapping": "POLYINTERP_NEAREST", **options}
        with CoreUtils.window_context_override():
            with bpy.context.temp_override(
                active_object=source,
                object=source,
                selected_objects=meshes,
                selected_editable_objects=meshes,
            ):
                try:
                    bpy.ops.object.data_transfer(data_type=data_type, **options)
                except RuntimeError as error:  # the operator's own report
                    raise ValueError(str(error)) from error
        return targets
