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
    def _transfer_order(objects):
        """The transfer's meshes, the source first: *objects*, or for ``None`` the
        selection with the active mesh first (it must be selected); ``[]`` with
        nothing selected.

        Raises:
            ValueError: No selected active mesh for ``None``, or fewer than two meshes.
        """
        if objects is None:
            selection = CoreUtils.selected_objects()
            if not selection:
                return []
            active = CoreUtils.active_object()
            if active is None or active not in selection or active.type != "MESH":
                raise ValueError("Select target mesh(es) with the source mesh active.")
            objects = [active] + [o for o in selection if o != active]
        meshes = _ComponentsInternal._meshes(objects)
        if len(meshes) < 2:
            raise ValueError("Transfer needs a source mesh and one or more target meshes.")
        return meshes

    @staticmethod
    def _meshes(objects):
        return [o for o in objects or () if getattr(o, "type", None) == "MESH"]

    @staticmethod
    def _component_scope():
        """True in Edit Mode: an operation then acts on the selected components
        (falling back to whole meshes when none is selected), as Maya's do."""
        active = CoreUtils.active_object()
        return bool(active and active.mode == "EDIT")


    @staticmethod
    def _edit_meshes(objects):
        """The meshes of *objects* in Edit Mode: what a component operation acts
        on, Blender's component selection existing only there."""
        import pythontk as ptk

        return [
            o
            for o in ptk.make_iterable(objects or ())
            if getattr(o, "type", None) == "MESH" and getattr(o, "mode", None) == "EDIT"
        ]

    @staticmethod
    def _run_edit_op(objects, idname, **props):
        """Run the edit-mesh operator *idname* (``"mesh.poke"``) on the Edit-Mode
        selection. Returns the meshes of *objects* in Edit Mode; ``[]``, with
        nothing run, when none is (entering Edit Mode on the user's behalf would
        act on a selection they cannot see).

        Raises:
            RuntimeError: Blender refused (its report).
        """
        import bpy

        meshes = _ComponentsInternal._edit_meshes(objects)
        if not meshes:
            return []
        group, name = idname.split(".")
        # The window context: edit-mesh operators poll ``edit_object`` from
        # screen context, dead in the Qt-pump state.
        with CoreUtils.window_context_override():
            getattr(getattr(bpy.ops, group), name)(**props)
        return meshes

    @staticmethod
    def _require_operator(idname, label, addon):
        """Raise unless the add-on operator *idname* is registered: the add-on
        is the canonical implementation (wrap-if-present, never rebuilt)."""
        from blendertk.ui_utils._ui_utils import UiUtils

        if UiUtils.get_operator(idname) is None:
            raise RuntimeError(
                f"{label} needs the {addon} add-on: install it from "
                "Edit > Preferences > Get Extensions, then retry."
            )


class Components(_ComponentsInternal):
    """Component-level mesh operations — mirror of mayatk's ``Components``."""

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def crease_edges(edges=None, amount=None, angle=None):
        """Crease the meshes *edges* (subdivision sharpness): in Edit Mode their
        selected edges, else every edge; optionally soften or harden them by angle.

        Mirror of mayatk's ``Components.crease_edges`` (``polyCrease`` /
        ``polySoftEdge``), over :meth:`EditUtils.crease_edges`: the Subdivision
        Surface edge crease, and the edges' smooth flag for *angle*. ``None``
        takes the selection; with nothing selected, ``[]``.

        Parameters:
            edges (list/None): Mesh objects (a Blender edge selection lives in
                Edit Mode); others are dropped.
            amount (float, optional): Maya's 0 (none) to 10 (full) scale; ``None``
                leaves the crease.
            angle (float, optional): Harden the edges sharper than it (degrees)
                and soften the rest; ``None`` leaves them.

        Returns:
            list: The meshes acted on; ``[]`` with nothing selected.
        """
        meshes = Components._meshes(edges)
        if meshes and (amount is not None or angle is not None):
            EditUtils.crease_edges(meshes, amount=amount, angle=angle)
        return meshes

    @staticmethod
    @CoreUtils.undoable
    def transfer_creased_edges(frm=None, to=None):
        """Copy the edge creases of the mesh *frm* to the meshes *to* with Blender's
        Data Transfer, each edge from the nearest source edge.

        Mirror of mayatk's ``Components.transfer_creased_edges`` (edge id to edge
        id there). ``frm`` ``None`` takes the selection: the active mesh is the
        source (it must be selected), the other selected meshes the targets --
        Blender's convention, where Maya's source is the first selected. ``to``
        is read only with an explicit ``frm``.

        Returns:
            list: The target meshes; ``[]`` with nothing selected.

        Raises:
            ValueError: No selected active mesh for ``None``, fewer than two
                meshes, or the operator's own report.
        """
        objects = None
        if frm is not None:
            targets = list(to) if isinstance(to, (list, tuple, set)) else [to]
            objects = [frm] + [o for o in targets if o is not None]
        return Components.transfer_mesh_data(
            objects, "CREASE", edge_mapping="NEAREST"
        )

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

        meshes = Components._transfer_order(objects)
        if not meshes:
            return []
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

    # ------------------------------------------------------------------ polygon operations
    # Mirrors of mayatk's (tentacle's polygons panel calls them): ``None`` takes
    # the selection, and the Edit-Mode selection of its meshes is what they act
    # on; ``[]`` comes back when no selected mesh is in Edit Mode.

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def extrude(components=None, keep_faces_together=True, divisions=1, thickness=0.0):
        """Extrude the Edit-Mode selection (``mesh.extrude_region``, or each face
        on its own), *divisions* times, each step pushed along the normals by its
        share of *thickness* (``transform.shrink_fatten``). One undo step. Mirror
        of mayatk's ``Components.extrude`` (whose extrude manipulator Blender
        lacks: *thickness* 0 extrudes in place, divisions stacked there).

        Returns:
            list: The meshes extruded; ``[]`` with none in Edit Mode.

        Raises:
            RuntimeError: No 3D viewport for a non-zero *thickness* (checked
                before anything changes), or Blender refused.
        """
        import bpy

        meshes = Components._edit_meshes(components)
        if not meshes:
            return []
        ctx = None
        if thickness:
            ctx = CoreUtils.get_view3d_context()
            if not ctx:
                raise RuntimeError(
                    "No 3D viewport available: open a 3D view to extrude with a "
                    "thickness."
                )
            # ``get_view3d_context`` may give ``region=None``; shrink_fatten takes none.
            ctx = {k: v for k, v in ctx.items() if v is not None}
        op = (
            bpy.ops.mesh.extrude_region
            if keep_faces_together
            else bpy.ops.mesh.extrude_faces_move
        )
        steps = max(int(divisions), 1)
        for _ in range(steps):
            with CoreUtils.window_context_override():
                op()
            if ctx:  # shrink_fatten needs a VIEW_3D region
                with bpy.context.temp_override(**ctx):
                    bpy.ops.transform.shrink_fatten(value=thickness / steps)
        return meshes

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def inset_faces(faces=None, offset=0.1, depth=0.0, individual=False):
        """Inset the selected faces (``mesh.inset``: *offset* is its thickness).
        One undo step. Mirror of mayatk's ``Components.inset_faces``.

        Returns:
            list: The meshes inset; ``[]`` with none in Edit Mode.
        """
        return Components._run_edit_op(
            faces, "mesh.inset", thickness=offset, depth=depth, use_individual=individual
        )

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def divide_faces(faces=None, u=True, v=True, cuts=1, tris=False):
        """Divide the selected faces with ``mesh.subdivide`` (*cuts* each way),
        triangulating the result with *tris*. One undo step. Mirror of mayatk's
        ``Components.divide_faces``: *u* / *v* are its one-direction splits,
        which Blender's subdivide has no face pattern for, so they are accepted
        for the shared signature and a face always divides both ways (tentacle
        shows the U / V boxes in Maya only).

        Returns:
            list: The meshes divided; ``[]`` with none in Edit Mode.
        """
        import bpy

        meshes = Components._edit_meshes(faces)
        if not meshes:
            return []
        with CoreUtils.window_context_override():
            bpy.ops.mesh.subdivide(number_cuts=max(int(cuts), 1))
            if tris:
                bpy.ops.mesh.quads_convert_to_tris()
        return meshes

    @staticmethod
    @CoreUtils.undoable
    def circularize(components=None):
        """Reshape the selected loop into an even circle with the LoopTools
        add-on's Circle. One undo step. Mirror of mayatk's. The add-on is checked
        first, so a missing one is named even with nothing selected.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.

        Raises:
            RuntimeError: LoopTools is not installed, or Blender refused.
        """
        Components._require_operator("mesh.looptools_circle", "Circularize", "LoopTools")
        if components is None:
            components = CoreUtils.selected_objects()
        return Components._run_edit_op(components, "mesh.looptools_circle")

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def fill_holes(components=None):
        """Cap the holes bounded by the selected border edges (``mesh.fill_holes``,
        any number of sides). One undo step. Mirror of mayatk's.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.
        """
        return Components._run_edit_op(components, "mesh.fill_holes", sides=0)

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def symmetrize(components=None):
        """Mirror the selected components across the mesh's X axis
        (``mesh.symmetrize``). One undo step. Mirror of mayatk's.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.
        """
        return Components._run_edit_op(components, "mesh.symmetrize")

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def bridge_edges(edges=None):
        """Bridge the selected edge loops (``mesh.bridge_edge_loops``). One undo
        step. Mirror of mayatk's ``Components.bridge_edges``.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.
        """
        return Components._run_edit_op(edges, "mesh.bridge_edge_loops")

    @staticmethod
    @CoreUtils.undoable
    def collapse_components(components=None):
        """Collapse each selected face region to a point; merge selected loose
        vertices and edges at one center (``mesh.merge``). One undo step. Mirror
        of mayatk's ``Components.collapse_components``.

        Faces count only while face select mode is on: in vertex mode a face
        whose vertices are all picked reads as selected too, and loose picks
        must merge at one center. An edge picked beside faces collapses with its
        region (``COLLAPSE`` works per connected region), so nothing is skipped.

        Returns:
            tuple: ``(meshes, [])``; ``([], [])`` with nothing selected in Edit Mode.
        """
        import bmesh
        import bpy

        meshes = Components._edit_meshes(
            components if components is not None else CoreUtils.selected_objects()
        )
        picked = [
            o for o in meshes if any(v.select for v in bmesh.from_edit_mesh(o.data).verts)
        ]
        if not picked:
            return [], []
        face_mode = bpy.context.scene.tool_settings.mesh_select_mode[2]
        faces = face_mode and any(
            any(f.select for f in bmesh.from_edit_mesh(o.data).faces) for o in picked
        )
        with CoreUtils.window_context_override():
            bpy.ops.mesh.merge(type="COLLAPSE" if faces else "CENTER")
        return picked, []

    @staticmethod
    @CoreUtils.undoable
    def connect_components(components=None):
        """Connect the selected vertices (in pick order, else across their
        faces) with new edges: ``mesh.vert_connect_path``, falling back to
        ``mesh.vert_connect``. One undo step. Mirror of mayatk's (Maya's
        interactive Connect tool there).

        Success is judged by the edge count: the path op raises "Invalid
        selection order" without a pick history, and the fallback can finish
        while connecting nothing.

        Returns:
            list: The mesh; ``[]`` with no mesh or nothing connected.
        """
        import bmesh
        import bpy

        obj = CoreUtils.ensure_component_mode("mesh")
        if obj is None:
            return []
        before = len(bmesh.from_edit_mesh(obj.data).edges)
        for op in (bpy.ops.mesh.vert_connect_path, bpy.ops.mesh.vert_connect):
            try:
                with CoreUtils.window_context_override():
                    op()
                break
            except RuntimeError:  # unordered / cross-face picks the path op rejects
                continue
        if len(bmesh.from_edit_mesh(obj.data).edges) == before:
            return []
        return [obj]

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def poke_faces(faces=None):
        """Add a center vertex to each selected face (``mesh.poke``). One undo
        step. Mirror of mayatk's.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.
        """
        return Components._run_edit_op(faces, "mesh.poke")

    @staticmethod
    def slide_edges(edges=None):
        """Slide the selected edge loops interactively: edge select mode, then
        Blender's modal ``transform.edge_slide`` under a 3D-viewport override.
        Mirror of mayatk's (Maya's Slide Edge tool). Not undoable here: the modal
        pushes its own undo step on confirm.

        Returns:
            bool: True: the slide is running.

        Raises:
            ValueError: No mesh is active or selected.
            RuntimeError: No 3D viewport, or Blender refused.
        """
        import bpy

        if CoreUtils.ensure_component_mode("mesh", "edge") is None:
            raise ValueError("Slide needs an active or selected mesh.")
        ctx = CoreUtils.get_view3d_context()
        if not ctx:
            raise RuntimeError("No 3D viewport available: open a 3D view to slide edges.")
        ctx = {k: v for k, v in ctx.items() if v is not None}
        with bpy.context.temp_override(**ctx):
            bpy.ops.transform.edge_slide("INVOKE_DEFAULT")
        return True

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def offset_edge_loops(edges=None):
        """Add an edge loop on each side of the selected edges
        (``mesh.offset_edge_loops``). One undo step. Mirror of mayatk's.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.
        """
        return Components._run_edit_op(edges, "mesh.offset_edge_loops")

    @staticmethod
    @CoreUtils.undoable
    def edit_edge_flow(edges=None):
        """Move the selected edge loops to follow the surface curvature with the
        Edit Mesh Tools (or Loop Flow) add-on's Set Flow. One undo step. Mirror
        of mayatk's. The add-on is checked first, so a missing one is named even
        with nothing selected.

        Returns:
            list: The meshes; ``[]`` with none in Edit Mode.

        Raises:
            RuntimeError: The add-on is not installed, or Blender refused.
        """
        Components._require_operator(
            "mesh.set_edge_flow", "Edit Edge Flow", "Edit Mesh Tools (or Loop Flow)"
        )
        if edges is None:
            edges = CoreUtils.selected_objects()
        return Components._run_edit_op(edges, "mesh.set_edge_flow")

    @staticmethod
    def adjusted_distance_between_vertices(
        p1=None, p2=None, adjust=0.0, as_percentage=False
    ):
        """The distance between two points, adjusted by *adjust* (an amount, or
        a percentage with *as_percentage*). Mirror of mayatk's.

        Parameters:
            p1, p2: Points ``(x, y, z)``. Both ``None``: the active mesh's two
                selected vertices in Edit Mode, in its local space (where
                Blender's merge measures).

        Returns:
            (float/None): The distance; ``None`` when called with ``None`` and
                the selection is not exactly two vertices.
        """
        import pythontk as ptk

        if p1 is None and p2 is None:
            import bmesh

            active = CoreUtils.active_object()
            if not (active and active.type == "MESH" and active.mode == "EDIT"):
                return None
            coords = [tuple(v.co) for v in bmesh.from_edit_mesh(active.data).verts if v.select]
            if len(coords) != 2:
                return None
            p1, p2 = coords
        distance = ptk.distance_between_points(p1, p2)
        return distance * (1 + adjust / 100) if as_percentage else distance + adjust

    #: The Transfer menu (:meth:`get_transfer_types` / :meth:`transfer`): label ->
    #: (the transfer, what it copies). Data Transfer copies one data layer at a time,
    #: so Maya's Attribute Values is its granular items here, and Maya's Shading Sets
    #: is Material Slots; Maya's Maps (a render bake) and Vertex Order (an interactive
    #: reindex) have no Blender operator.
    _TRANSFERS = {
        "UVs": ("_transfer_uvs", "The active UV map, source → targets."),
        "Vertex Colors": (
            "_transfer_vertex_colors",
            "The active color attribute, in its own domain (point or face corner).",
        ),
        "Vertex Group Weights": (
            "_transfer_vertex_weights",
            "Every vertex group's weights, matched by name.",
        ),
        "Custom Normals": ("_transfer_custom_normals", "The custom split normals."),
        "Material Slots": (
            "_transfer_material_slots",
            "The source's material slots, and so its shading.",
        ),
    }

    @classmethod
    def get_transfer_types(cls):
        """The Transfer menu: ``{label: what it copies}`` in menu order, each a
        transfer :meth:`transfer` runs. Mirror of mayatk's
        ``Components.get_transfer_types`` (Maya's Transfer items there)."""
        return {label: spec[1] for label, spec in cls._TRANSFERS.items()}

    @classmethod
    @CoreUtils.undoable
    def transfer(cls, objects, transfer_type):
        """Copy a :meth:`get_transfer_types` layer from the first mesh of *objects*
        to the rest (Data Transfer, or the material-slot copy). Mirror of mayatk's
        ``Components.transfer``.

        Parameters:
            objects (list/None): The meshes, the source first; ``None`` takes the
                selection, the active mesh the source (it must be selected) --
                Blender's convention, where Maya's source is the first selected.
            transfer_type (str): A :meth:`get_transfer_types` label.

        Returns:
            list: ``[source, *targets]``; ``[]`` with nothing selected.

        Raises:
            ValueError: An unknown type, no selected active mesh for ``None``, fewer
                than two meshes, or Blender's own report.
        """
        spec = cls._TRANSFERS.get(transfer_type)
        if spec is None:
            raise ValueError(
                f"Unknown transfer '{transfer_type}': {', '.join(cls._TRANSFERS)}."
            )
        meshes = cls._transfer_order(objects)
        if meshes:
            getattr(cls, spec[0])(meshes)
        return meshes

    @staticmethod
    def _transfer_uvs(meshes):
        Components.transfer_mesh_data(
            meshes, "UV", layers_select_src="ACTIVE", layers_select_dst="ACTIVE"
        )

    @staticmethod
    def _transfer_vertex_colors(meshes):
        """The source's active color attribute, matched to its domain, so painted
        per-vertex and per-corner colors both transfer (a mesh with none: points,
        ``geometry.color_attribute_add``'s own default)."""
        attrs = getattr(meshes[0].data, "color_attributes", None)
        active = attrs.active_color if attrs else None
        data_type = (
            "COLOR_CORNER" if active is not None and active.domain == "CORNER" else "COLOR_VERTEX"
        )
        Components.transfer_mesh_data(
            meshes,
            data_type,
            vert_mapping="NEAREST",
            layers_select_src="ACTIVE",
            layers_select_dst="ACTIVE",
        )

    @staticmethod
    def _transfer_vertex_weights(meshes):
        Components.transfer_mesh_data(
            meshes,
            "VGROUP_WEIGHTS",
            vert_mapping="NEAREST",
            layers_select_src="ALL",
            layers_select_dst="NAME",
        )

    @staticmethod
    def _transfer_custom_normals(meshes):
        Components.transfer_normals(meshes)

    @staticmethod
    def _transfer_material_slots(meshes):
        """``object.material_slot_copy`` from the source, under an override naming
        the source and targets: it reads the context object and selection, screen
        context and dead in the Qt-pump state."""
        import bpy

        source = meshes[0]
        with CoreUtils.window_context_override():
            with bpy.context.temp_override(
                active_object=source,
                object=source,
                selected_objects=meshes,
                selected_editable_objects=meshes,
            ):
                try:
                    bpy.ops.object.material_slot_copy()
                except RuntimeError as error:  # the operator's own report
                    raise ValueError(str(error)) from error
