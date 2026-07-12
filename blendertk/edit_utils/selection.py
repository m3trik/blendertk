# !/usr/bin/python
# coding=utf-8
"""Category-driven select-by-type — mirror of mayatk's ``edit_utils.selection.Selection``
(``btk.Selection`` <-> ``mtk.Selection``), backing the shared ``list000`` "Select by Type"
ExpandableList in ``tentacle/slots/*/selection.py``.

Maya's ``_SELECTION_CONFIG`` keys off DAG **node types** (``cmds.ls(type=...)``) because Maya
separates transform/shape/deformer nodes. Blender collapses all of that onto a single
``bpy.types.Object`` with a ``.type`` enum plus modifiers/constraints/physics settings living ON
the object, so most handlers here filter ``Object`` properties instead of listing typed nodes.
Handlers act at OBJECT granularity even for the "UV" category — Blender's own per-component UV
select tools already live in the ``uv`` submenu slot; this list is a coarse "select every object
matching X" sweep (matching how the category this replaces already worked).

Categories mirror mayatk's shape 1:1 (same category + leaf names) so the shared tentacle slots
stay branch-free; only the callable BODIES differ, using Blender-native primitives instead of
string-node lookups. Maya deformer leaves map to their nearest Blender modifier via the
modifier-carrier idiom (Clusters -> Hook, Wires -> Curve — select the meshes carrying that
modifier, exactly like nCloths -> CLOTH / Fluids -> FLUID). Leaves with no Blender analogue at
all (bone/sub-object/brush-datablock concepts — IK Handles, Joints, Brushes, Dynamic Constraints,
Sculpts, Templated Geometry, UV Front/Back-Facing) are intentionally NOT built here; they're
tracked as ``na`` in ``tentacle/docs/parity_map.py`` (``HANDLERS["selection"]``) instead of
silently dropped. A few
Blender-only leaves with no Maya counterpart (Metaballs/Text/Volumes/Armatures/Light Probes/
Speakers) are kept as additive bonus entries under the closest matching category — they existed
in the flat ``_SELECT_TYPES`` dict this replaces, so keeping them avoids regressing capability.

``import bpy``/``bmesh`` are deferred into the call bodies (no import side effects).
"""
from blendertk.node_utils._node_utils import get_children, get_parent


class Selection:
    """Namespace mirror of mayatk's ``Selection`` (category-driven select-by-type)."""

    # Geometry-bearing object types -- excludes Empty/Armature/Light/Camera/Speaker/LightProbe,
    # which are helper/scene objects with no render geometry of their own.
    _GEOMETRY_TYPES = {
        "MESH",
        "CURVE",
        "SURFACE",
        "META",
        "FONT",
        "CURVES",
        "POINTCLOUD",
        "VOLUME",
        "GREASEPENCIL",
        "LATTICE",
    }

    _SELECTION_CONFIG = {
        "Animation": {
            "Animated Objects": lambda objs: Selection._select_animated_objects(objs),
            # Maya's cluster deformer has no standalone Object in Blender; the Hook modifier is
            # its direct analogue (a control-object-driven per-vertex deformer), so the idiom
            # translation selects the meshes carrying a Hook modifier -- same pattern as the
            # Dynamics leaves (nCloths -> CLOTH, Fluids -> FLUID).
            "Clusters": lambda objs: Selection._select_by_modifier(objs, "HOOK"),
            # Blender constraints live ON the object (no separate constraint node to select);
            # the idiom translation selects the objects that carry one or more constraints.
            "Constraints": lambda objs: [o for o in objs if o.constraints],
        },
        "Dynamics": {
            "Fluids": lambda objs: Selection._select_by_modifier(objs, "FLUID"),
            # Hair-root attachment -- the closest Blender analogue of a Maya follicle.
            "Follicles": lambda objs: Selection._select_by_particle_type(objs, "HAIR"),
            "Lattices": lambda objs: [o for o in objs if o.type == "LATTICE"],
            "nCloths": lambda objs: Selection._select_by_modifier(objs, "CLOTH"),
            "nRigids": lambda objs: Selection._select_by_rigid_body_type(objs, "PASSIVE"),
            "Particles": lambda objs: Selection._select_by_particle_type(objs, "EMITTER"),
            "Rigid Bodies": lambda objs: Selection._select_by_rigid_body_type(objs, "ACTIVE"),
            "Rigid Constraints": lambda objs: [
                o for o in objs if getattr(o, "rigid_body_constraint", None)
            ],
            # Maya paint-effects strokes -> Blender's own stroke-based object type.
            "Strokes": lambda objs: [o for o in objs if o.type == "GREASEPENCIL"],
            # Maya's wire deformer (a curve-driven mesh deform) maps to Blender's Curve modifier
            # (bpy.types.CurveModifier -- curve-driven mesh deformation); select the carrier meshes.
            "Wires": lambda objs: Selection._select_by_modifier(objs, "CURVE"),
        },
        "Geometry": {
            "All Geometry": lambda objs: [
                o for o in objs if o.type in Selection._GEOMETRY_TYPES
            ],
            "Hidden Geometry": lambda objs: Selection._select_hidden_geometry(objs),
            "Non-Selectable Geometry": lambda objs: Selection._select_unselectable_geometry(
                objs
            ),
            "NURBS Curves": lambda objs: [o for o in objs if o.type == "CURVE"],
            "NURBS Surfaces": lambda objs: [o for o in objs if o.type == "SURFACE"],
            "Polygon Meshes": lambda objs: [o for o in objs if o.type == "MESH"],
            "Single-Instance Geometry": lambda objs: Selection._select_single_instance_geometry(
                objs
            ),
            # Blender-only bonus leaves (no Maya counterpart) -- kept from the _SELECT_TYPES
            # dict this replaces so no prior capability regresses.
            "Metaballs": lambda objs: [o for o in objs if o.type == "META"],
            "Text": lambda objs: [o for o in objs if o.type == "FONT"],
            "Volumes": lambda objs: [o for o in objs if o.type == "VOLUME"],
        },
        "Hierarchy": {
            "Ancestors": lambda objs: Selection.select_hierarchy_above(objs),
            "Children": lambda objs: Selection.select_children(objs),
            "Descendants": lambda objs: Selection.select_hierarchy_below(objs),
            # Empties used purely as organizational parents (Maya's is_group check: null
            # transform, no shape, has children); Image-Plane empties are excluded (own leaf).
            "Groups": lambda objs: [
                o
                for o in objs
                if o.type == "EMPTY" and o.children and o.empty_display_type != "IMAGE"
            ],
        },
        "Scene": {
            "Assets": lambda objs: [
                o for o in objs if getattr(o, "asset_data", None) is not None
            ],
            "Cameras": lambda objs: [o for o in objs if o.type == "CAMERA"],
            "Image Planes": lambda objs: [
                o for o in objs if o.type == "EMPTY" and o.empty_display_type == "IMAGE"
            ],
            "Lights": lambda objs: [o for o in objs if o.type == "LIGHT"],
            "Locators": lambda objs: Selection._select_locators(objs),
            "Keyed Locators": lambda objs: Selection._select_keyed_locators(objs),
            # Every Blender Object carries its own transform (no separate transform/shape
            # node split like Maya's DAG) -- "Transforms" is effectively "everything".
            "Transforms": lambda objs: list(objs),
            # Blender-only bonus leaves (see Geometry note above).
            "Armatures": lambda objs: [o for o in objs if o.type == "ARMATURE"],
            "Light Probes": lambda objs: [o for o in objs if o.type == "LIGHT_PROBE"],
            "Speakers": lambda objs: [o for o in objs if o.type == "SPEAKER"],
        },
        "UV": {
            "Overlapping": lambda objs: Selection._select_uv_overlap(objs, want_overlap=True),
            "Non-Overlapping": lambda objs: Selection._select_uv_overlap(
                objs, want_overlap=False
            ),
            # Blender's own boundary-between-UV-islands marker IS the seam edge.
            "Texture Borders": lambda objs: [
                o for o in objs if o.type == "MESH" and any(e.use_seam for e in o.data.edges)
            ],
            "Unmapped": lambda objs: [
                o for o in objs if o.type == "MESH" and not o.data.uv_layers
            ],
        },
    }

    @staticmethod
    def select_by_type(selection_type, objects=None, mode="replace"):
        """Select objects by category or leaf type (mirror of ``mtk.Selection.select_by_type``).

        Parameters:
            selection_type (str): A category name (e.g. "Geometry") or a specific leaf
                label (e.g. "Polygon Meshes").
            objects (list, optional): Objects to filter from. Defaults to the current
                selection, falling back to every object in the file.
            mode (str): "replace", "add", or "remove".

        Returns:
            list: The matched objects (also applied to the live selection per ``mode``).
        """
        import bpy
        from blendertk.core_utils._core_utils import selected_objects

        if objects is None:
            sel = list(selected_objects())
            objects = sel if sel else list(bpy.data.objects)
        objects = [o for o in objects if o]
        if not objects:
            return []

        if selection_type in Selection._SELECTION_CONFIG:
            result = set()
            for handler in Selection._SELECTION_CONFIG[selection_type].values():
                try:
                    res = handler(objects)
                    if res:
                        result.update(res)
                except Exception:
                    continue
            Selection._apply_selection_mode(result, mode)
            return list(result)

        handler = None
        for category in Selection._SELECTION_CONFIG.values():
            if selection_type in category:
                handler = category[selection_type]
                break

        if not handler:
            raise ValueError(f"Unknown selection type: {selection_type}")

        result = handler(objects)
        Selection._apply_selection_mode(result, mode)
        return list(result) if isinstance(result, set) else result

    @staticmethod
    def select_children(objects):
        """The immediate children of the given objects (one level below only)."""
        result = set()
        for obj in objects:
            result.update(get_children(obj, recursive=False))
        return result

    @staticmethod
    def select_hierarchy_above(objects):
        """All ancestor objects above the given objects (full parent chain)."""
        result = set()
        for obj in objects:
            result.update(get_parent(obj, all=True))
        return result

    @staticmethod
    def select_hierarchy_below(objects):
        """All descendant objects below the given objects (full child subtree)."""
        result = set()
        for obj in objects:
            result.update(get_children(obj, recursive=True))
        return result

    @staticmethod
    def _select_animated_objects(objects):
        """Objects with an action or drivers on their animation data."""
        return [
            o
            for o in objects
            if o.animation_data and (o.animation_data.action or o.animation_data.drivers)
        ]

    @staticmethod
    def _select_by_modifier(objects, mod_type):
        return [o for o in objects if any(m.type == mod_type for m in o.modifiers)]

    @staticmethod
    def _select_by_particle_type(objects, ps_type):
        return [
            o
            for o in objects
            if any(ps.settings.type == ps_type for ps in o.particle_systems)
        ]

    @staticmethod
    def _select_by_rigid_body_type(objects, rb_type):
        return [
            o for o in objects if getattr(o, "rigid_body", None) and o.rigid_body.type == rb_type
        ]

    @staticmethod
    def _safe_select_set(obj, state):
        """``Object.select_set()``/``hide_set()`` raise ``RuntimeError`` for an object
        outside the active view layer (e.g. sitting in an excluded collection) -- verified
        live in Blender 5.1.2. Selection predicates here commonly run over
        ``bpy.data.objects`` (the whole file, not scoped to the active view layer), so
        every ``select_set`` call must be guarded and skip objects that can't be selected
        rather than letting one un-selectable match abort the entire operation.

        Returns:
            bool: True if the object's selection state was actually changed.
        """
        try:
            obj.select_set(state)
            return True
        except RuntimeError:
            return False

    @staticmethod
    def _hide_get_safe(obj):
        """Whether ``obj`` is hidden, combining Blender's two independent hide flags.

        ``Object.hide_get()`` (the per-view-layer "eye" toggle) does NOT raise for an
        object outside the active view layer -- verified live in Blender 5.1.2, it
        silently returns ``False`` -- so a bare ``hide_get()`` call would miss an object
        hidden via the persistent ``hide_viewport`` ("Disable in Viewports") flag while it
        happens to sit outside the active view layer. Check both explicitly rather than
        relying on a ``RuntimeError`` that never fires.
        """
        return bool(obj.hide_get()) or bool(obj.hide_viewport)

    @staticmethod
    def _select_hidden_geometry(objects):
        return [
            o
            for o in objects
            if o.type in Selection._GEOMETRY_TYPES and Selection._hide_get_safe(o)
        ]

    @staticmethod
    def _select_unselectable_geometry(objects):
        return [
            o for o in objects if o.type in Selection._GEOMETRY_TYPES and o.hide_select
        ]

    @staticmethod
    def _select_single_instance_geometry(objects):
        """One representative object per shared-data group — mirror of mayatk's
        ``filter_duplicate_instances`` (dedupe instances down to a single transform each)."""
        seen = set()
        result = []
        for o in objects:
            if o.type not in Selection._GEOMETRY_TYPES:
                continue
            key = o.data.name if getattr(o, "data", None) is not None else o.name
            if key not in seen:
                seen.add(key)
                result.append(o)
        return result

    @staticmethod
    def _select_locators(objects):
        """Empties acting as plain point markers (no children, not an image plane) —
        the closest Blender analogue of a Maya locator."""
        return [
            o
            for o in objects
            if o.type == "EMPTY" and not o.children and o.empty_display_type != "IMAGE"
        ]

    @staticmethod
    def _select_keyed_locators(objects):
        """Locators (see ``_select_locators``) that have animation keys."""
        return Selection._select_animated_objects(Selection._select_locators(objects))

    @staticmethod
    def _select_uv_overlap(objects, want_overlap):
        """Objects whose UVs do/don't contain any overlapping faces, via native
        ``bpy.ops.uv.select_overlap()`` run per-object in Edit Mode (UV-sync forced ON so the
        result reads back onto the mesh's own face-select state)."""
        import bpy
        from blendertk.core_utils._core_utils import selected_objects

        candidates = [o for o in objects if o.type == "MESH" and o.data.uv_layers]
        if not candidates:
            return []

        result = []
        ts = bpy.context.scene.tool_settings
        prev_sync = ts.use_uv_select_sync
        prev_active = bpy.context.view_layer.objects.active
        prev_selected = list(selected_objects())
        ts.use_uv_select_sync = True
        try:
            for o in candidates:
                for other in prev_selected:
                    Selection._safe_select_set(other, False)
                # A candidate outside the active view layer (e.g. an excluded collection)
                # can't be made active/edited -- skip it rather than crash the whole sweep.
                if not Selection._safe_select_set(o, True):
                    continue
                bpy.context.view_layer.objects.active = o
                has_overlap = False
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                    bpy.ops.mesh.select_all(action="SELECT")
                    bpy.ops.uv.select_all(action="SELECT")
                    bpy.ops.uv.select_overlap()
                    import bmesh

                    bm = bmesh.from_edit_mesh(o.data)
                    has_overlap = any(f.select for f in bm.faces)
                except RuntimeError:
                    has_overlap = False
                finally:
                    bpy.ops.object.mode_set(mode="OBJECT")
                    Selection._safe_select_set(o, False)
                if has_overlap == want_overlap:
                    result.append(o)
        finally:
            ts.use_uv_select_sync = prev_sync
            for o in prev_selected:
                Selection._safe_select_set(o, True)
            if prev_active:
                bpy.context.view_layer.objects.active = prev_active
        return result

    @staticmethod
    def _apply_selection_mode(objects, mode):
        """Apply the selection mode to the given objects (mirror of ``mtk``'s helper, via
        ``Object.select_set`` instead of ``cmds.select``).

        Predicates commonly run over ``bpy.data.objects`` (the whole file), which can
        include objects outside the active view layer (e.g. an excluded collection) that
        ``select_set`` can't touch -- skip those via ``_safe_select_set`` instead of
        letting one un-selectable match raise and abort the whole selection.
        """
        objs = [o for o in objects if o]
        if mode == "replace":
            import bpy

            for o in list(bpy.context.view_layer.objects):
                if o:
                    Selection._safe_select_set(o, False)
        if mode == "remove":
            for o in objs:
                Selection._safe_select_set(o, False)
            return
        active = None
        for o in objs:
            if Selection._safe_select_set(o, True) and active is None:
                active = o
        if active is not None:
            import bpy

            bpy.context.view_layer.objects.active = active

    # ---------------------------------------------------------- Convert-To (cmb003 mirror) ---
    # Mirror of mayatk's ``core_utils.components.GetComponentsMixin``/``Components`` conversion
    # surface, scoped to what the shared ``selection.py`` cmb003 "Convert To" combo needs.
    # mayatk's version is Maya string-component-idiom machinery (``polyListComponentConversion``,
    # component descriptor strings); this is the Blender-idiomatic equivalent built directly on
    # bmesh, per blendertk's name+behavior (not signature) mirror convention.
    #
    # Touching vs. contained (Maya's plain "Faces"/"Edges" vs. "Contained Faces"/"Contained
    # Edges") is a single native flag, verified empirically in a fresh headless Blender 5.1
    # across every upward pair (vert->edge, vert->face, edge->face): the default
    # ``select_mode`` conversion is CONTAINED (every sub-component of a destination element
    # must already be selected); ``use_expand=True`` is TOUCHING (any destination element
    # sharing so much as one sub-component gets selected, matching ``vert.link_faces`` /
    # ``edge.link_faces`` directly). Downward conversions (face -> vert/edge) are unambiguous
    # either way. No bmesh graph walk needed for this axis at all.
    @staticmethod
    def _edit_bmesh(obj):
        """The active BMesh for `obj` in Edit Mode, with lookup tables ensured."""
        import bmesh

        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        return bm

    @staticmethod
    def _flush(obj, bm):
        import bmesh

        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

    @staticmethod
    def convert_to(obj, mode, contained=False):
        """Convert the current Edit-Mode selection to `mode` ('VERT'/'EDGE'/'FACE') — Maya
        ``PolySelectConvert`` parity. ``contained=False`` (default) = Maya's plain
        Verts/Edges/Faces (touching); ``contained=True`` = Maya's Contained Edges/Contained
        Faces. See the class-level note above for how the native flag maps to each."""
        import bpy

        bpy.ops.mesh.select_mode(type=mode, use_expand=not contained)

    @staticmethod
    def select_face_path(obj):
        """Face Path: the shortest face-adjacency path between exactly two selected faces —
        Maya's Face Path, mirrored via Blender's own ``shortest_path_select`` run in FACE
        select mode (the same primitive already driving Edge Loop Path / Shortest Edge Path
        in ``tb000``, one level up)."""
        import bpy

        bpy.ops.mesh.select_mode(type="FACE")
        bpy.ops.mesh.shortest_path_select()

    @staticmethod
    def select_vertex_perimeter(obj):
        """Vertex Perimeter: the vertices of the boundary loop around the current face-region
        selection — Maya's ``ConvertSelectionToVertexPerimeter``."""
        import bpy

        bpy.ops.mesh.region_to_loop()
        bpy.ops.mesh.select_mode(type="VERT")

    @staticmethod
    def select_edge_perimeter(obj):
        """Edge Perimeter: the boundary edge loop around the current face-region selection —
        Maya's ``ConvertSelectionToEdgePerimeter``. Distinct from Border Edges (below): this
        follows the SELECTED REGION's shape; Border Edges finds the mesh's own naked/open
        edges regardless of region shape."""
        import bpy

        bpy.ops.mesh.region_to_loop()

    @staticmethod
    def select_face_perimeter(obj):
        """Face Perimeter: the ring of faces immediately surrounding the current face-region
        selection (one step outward) — Maya's ``polySelectFacePerimeter``. No single native op
        does this, so it's a direct bmesh adjacency query: every face across a boundary edge
        of the selection that isn't itself part of the selection."""
        bm = Selection._edit_bmesh(obj)
        selected = {f for f in bm.faces if f.select}
        if not selected:
            return 0
        ring = {
            nf
            for f in selected
            for e in f.edges
            for nf in e.link_faces
            if nf not in selected
        }
        for f in bm.faces:
            f.select = f in ring
        Selection._flush(obj, bm)
        return len(ring)

    @staticmethod
    def select_border_edges(obj):
        """Border Edges: the naked (open, single-face) mesh edges among the current
        selection's own edges — Maya's ``Components.get_border_components`` pipeline (the
        tentacle Maya slot's call site referenced a non-existent method; fixed 2026-07-06, see
        the tentacle CHANGELOG). Falls back to the WHOLE mesh's open boundary when nothing is
        selected."""
        bm = Selection._edit_bmesh(obj)
        any_selected = (
            any(v.select for v in bm.verts)
            or any(e.select for e in bm.edges)
            or any(f.select for f in bm.faces)
        )
        if any_selected:
            import bpy

            bpy.ops.mesh.select_mode(type="EDGE", use_expand=True)
            bm = Selection._edit_bmesh(obj)
            candidates = {e for e in bm.edges if e.select}
        else:
            candidates = set(bm.edges)
        border = {e for e in candidates if e.is_boundary}
        for e in bm.edges:
            e.select = e in border
        # No manual face-clear here: BMesh's element ``.select`` setter cascades to child
        # elements, so unconditionally writing ``face.select = False`` on every face would
        # deselect the border edges just set above (via their owning faces) before the flush
        # below ever runs. ``select_flush_mode()`` in EDGE mode derives face state correctly
        # on its own.
        Selection._flush(obj, bm)
        return len(border)

    @staticmethod
    def select_shell_border(obj):
        """Shell Border: the naked/open boundary edges of the connected shell(s) touching the
        current selection — Maya's ``polyConvertToShellBorder``. Grows to the full shell
        first (``select_linked``), then filters to its open edges (a closed/manifold shell
        yields none)."""
        import bpy

        bpy.ops.mesh.select_linked()
        return Selection.select_border_edges(obj)

    @staticmethod
    def select_uv_shell(obj):
        """UV Shell: every face sharing a UV island with the current selection — Maya's
        ``polySelectBorderShell 0``. Uses the mesh-domain UV delimiter (no UV-editor context
        needed) — the same primitive already backing ``tb002``'s "By UV Border" island option."""
        import bpy

        bpy.ops.mesh.select_linked(delimit={"UV"})

    @staticmethod
    def get_available_selection_types():
        """A flat, sorted list of every leaf selection-type label."""
        categories = Selection.get_selection_categories()
        return sorted(item for items in categories.values() for item in items)

    @staticmethod
    def get_selection_categories():
        """Dict of category -> leaf-label list (mirror of ``mtk.Selection.get_selection_categories``)."""
        return {
            category: list(types.keys())
            for category, types in Selection._SELECTION_CONFIG.items()
        }


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
