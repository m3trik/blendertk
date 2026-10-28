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

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.node_utils._node_utils import NodeUtils


class Selection:
    """Namespace mirror of mayatk's ``Selection`` (category-driven select-by-type)."""

    @staticmethod
    def loop_multi_select(ring=False):
        """Extend the current edge selection to full loops (rings with ``ring``) — version-portable.

        Blender 5.0 split ``mesh.loop_multi_select(ring=…)`` into ``select_edge_loop_multi`` /
        ``select_edge_ring_multi``; 4.x ships only the combined op. Callers must be in mesh Edit
        Mode with an edge selection (same contract as the ops themselves)."""
        import bpy

        name = "select_edge_ring_multi" if ring else "select_edge_loop_multi"
        try:
            getattr(bpy.ops.mesh, name)()
        except AttributeError:  # pre-5.0: the split ops don't exist
            bpy.ops.mesh.loop_multi_select(ring=ring)

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
            "nRigids": lambda objs: Selection._select_by_rigid_body_type(
                objs, "PASSIVE"
            ),
            "Particles": lambda objs: Selection._select_by_particle_type(
                objs, "EMITTER"
            ),
            "Rigid Bodies": lambda objs: Selection._select_by_rigid_body_type(
                objs, "ACTIVE"
            ),
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
            "Non-Selectable Geometry": lambda objs: (
                Selection._select_unselectable_geometry(objs)
            ),
            "NURBS Curves": lambda objs: [o for o in objs if o.type == "CURVE"],
            "NURBS Surfaces": lambda objs: [o for o in objs if o.type == "SURFACE"],
            "Polygon Meshes": lambda objs: [o for o in objs if o.type == "MESH"],
            "Single-Instance Geometry": lambda objs: (
                Selection._select_single_instance_geometry(objs)
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
            # Empties standing for a Maya group (NodeUtils.is_group: the node-type stamp,
            # else a parent); Image-Plane empties are excluded (own leaf).
            "Groups": lambda objs: NodeUtils.is_group(objs, filter=True),
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
            # Signed-UV-area winding (shoelace over face.loops[].uv): a face flipped/mirrored in
            # UV space has negative area. Mirror of Maya's selectUVFaceOrientationComponents
            # Back-/Front-Facing, at blendertk's object-level granularity (like the sibling
            # Texture Borders / Unmapped leaves): selects objects that CONTAIN any flipped /
            # any front-facing UV face. Camera backface culling is the separate chk004 toggle.
            "Back-Facing": lambda objs: Selection._select_uv_face_orientation(
                objs, want_flipped=True
            ),
            "Front-Facing": lambda objs: Selection._select_uv_face_orientation(
                objs, want_flipped=False
            ),
            "Overlapping": lambda objs: Selection._select_uv_overlap(
                objs, want_overlap=True
            ),
            "Non-Overlapping": lambda objs: Selection._select_uv_overlap(
                objs, want_overlap=False
            ),
            # Blender's own boundary-between-UV-islands marker IS the seam edge.
            "Texture Borders": lambda objs: [
                o
                for o in objs
                if o.type == "MESH" and any(e.use_seam for e in o.data.edges)
            ],
            "Unmapped": lambda objs: [
                o for o in objs if o.type == "MESH" and not o.data.uv_layers
            ],
        },
    }

    @staticmethod
    # The whole body in the window's context: the handlers' hide/visibility reads,
    # the UV-overlap operators and the select_set writes all resolve the context's
    # view layer, which windowless is the scene's default, not the window's.
    @CoreUtils.window_context_override()
    def select_by_type(selection_type, objects=None, mode="replace", scope=None):
        """Select objects by category or leaf type (mirror of ``mtk.Selection.select_by_type``).

        Parameters:
            selection_type (str): A category name (e.g. "Geometry") or a specific leaf
                label (e.g. "Polygon Meshes").
            objects (list, optional): Objects to filter from. Defaults to the current
                selection, falling back to every object in the file.
            mode (str): "replace", "add", or "remove".
            scope (str, optional): With *objects* None: draw them from
                :meth:`objects_in_scope` (``"all"``, ``"selected"`` or ``"visible"``).

        Returns:
            list: The matched objects (also applied to the live selection per ``mode``).
        """
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        if objects is None and scope:
            objects = Selection.objects_in_scope(scope)
        elif objects is None:
            sel = list(CoreUtils.selected_objects())
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
            result.update(NodeUtils.get_children(obj, recursive=False))
        return result

    @staticmethod
    def select_hierarchy_above(objects):
        """All ancestor objects above the given objects (full parent chain)."""
        result = set()
        for obj in objects:
            result.update(NodeUtils.get_parent(obj, all=True))
        return result

    @staticmethod
    def select_hierarchy_below(objects):
        """All descendant objects below the given objects (full child subtree)."""
        result = set()
        for obj in objects:
            result.update(NodeUtils.get_children(obj, recursive=True))
        return result

    @staticmethod
    def _select_animated_objects(objects):
        """Objects with an action or drivers on their animation data."""
        return [
            o
            for o in objects
            if o.animation_data
            and (o.animation_data.action or o.animation_data.drivers)
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
            o
            for o in objects
            if getattr(o, "rigid_body", None) and o.rigid_body.type == rb_type
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
        """Empties standing for a Maya locator (``NodeUtils.is_locator``): the node-type
        stamp -- a locator rig's locator holds its object -- else a point marker with no
        children; never an image plane."""
        return NodeUtils.is_locator(objects, filter=True)

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
        from blendertk.core_utils._core_utils import CoreUtils

        candidates = [o for o in objects if o.type == "MESH" and o.data.uv_layers]
        if not candidates:
            return []

        result = []
        ts = bpy.context.scene.tool_settings
        prev_sync = ts.use_uv_select_sync
        ts.use_uv_select_sync = True
        try:
            with CoreUtils.preserved_selection() as prev_selected:
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
        return result

    @staticmethod
    def _signed_uv_area(face, uv_layer):
        """Shoelace signed area of a face's UVs in loop order. Positive = normal winding,
        negative = flipped/mirrored in UV space (matches Maya's face-orientation convention)."""
        uvs = [
            loop[uv_layer].uv for loop in face.loops
        ]  # materialize once (O(1) indexing)
        n = len(uvs)
        area = 0.0
        for i in range(n):
            u1, v1 = uvs[i]
            u2, v2 = uvs[(i + 1) % n]
            area += u1 * v2 - u2 * v1
        return 0.5 * area

    @staticmethod
    def _select_uv_face_orientation(objects, want_flipped):
        """Objects containing at least one flipped (``want_flipped=True``, negative signed UV
        area) / front-facing (``want_flipped=False``, positive) UV face — object-level analogue
        of Maya's ``selectUVFaceOrientationComponents`` Back-/Front-Facing. Reads the mesh copy
        (no Edit Mode needed), so it composes with ``select_by_type``'s object selection like the
        other UV leaves."""
        import bmesh

        result = []
        for o in objects:
            if o.type != "MESH" or not o.data.uv_layers:
                continue
            bm = bmesh.new()
            try:
                bm.from_mesh(o.data)
                uv_layer = bm.loops.layers.uv.active
                if uv_layer is None:
                    continue
                for f in bm.faces:
                    area = Selection._signed_uv_area(f, uv_layer)
                    if abs(area) > 1e-12 and (area < 0.0) == want_flipped:
                        result.append(o)
                        break
            finally:
                bm.free()
        return result

    @staticmethod
    def _apply_selection_mode(objects, mode):
        """Apply the selection mode to the given objects (mirror of ``mtk``'s helper, via
        :func:`CoreUtils.select` instead of ``cmds.select``).

        Predicates commonly run over ``bpy.data.objects`` (the whole file), which can
        include objects outside the active view layer (e.g. an excluded collection);
        ``CoreUtils.select`` skips those rather than letting one raise.
        """
        from blendertk.core_utils._core_utils import CoreUtils

        CoreUtils.select(objects, mode=mode)

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

    # -- UV-domain component conversions (cmb003 UV Shell Border / UV Perimeter / UV Edge Loop).
    # All three key off a single UV-boundary test: an edge bounds a UV island if it is a mesh-open
    # edge (naked, <2 faces) OR a UV discontinuity — its two faces assign different UVs to a shared
    # vertex, i.e. a seam that splits an otherwise-manifold surface in UV space. The UV-coord check
    # is ground truth (works with or without explicit seam marks); a mesh-continuous edge keeps
    # matching UVs at both endpoints by construction, so there are no false positives.
    @staticmethod
    def _edge_is_uv_boundary(edge, uv_layer):
        """True if `edge` lies on a UV-island boundary (mesh-open edge or UV seam)."""
        lf = edge.link_faces
        if len(lf) != 2:
            return True
        for v in edge.verts:
            uvs = []
            for f in lf:
                for loop in f.loops:
                    if loop.vert is v:
                        uvs.append(loop[uv_layer].uv)
                        break
            if len(uvs) == 2 and (uvs[0] - uvs[1]).length > 1e-6:
                return True
        return False

    @staticmethod
    def select_uv_shell_border(obj):
        """UV Shell Border: the UV-island boundary edges of the UV shell(s) touching the current
        selection — Maya's ``polySelectBorderShell 1``. Grows to the full UV island first
        (``select_linked`` with the UV delimiter, so a seam stops the grow), then keeps that
        island's UV-boundary edges. Differs from Shell Border (3D-topology open edges): a seam
        splitting a manifold surface in UV space is a boundary here but not there."""
        import bpy

        bpy.ops.mesh.select_linked(delimit={"UV"})
        bm = Selection._edit_bmesh(obj)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return 0
        # The UV island is the currently-selected face set; take its edges directly (don't rely
        # on a FACE->EDGE mode flush, which doesn't reliably carry the selection here).
        island_edges = {e for f in bm.faces if f.select for e in f.edges}
        border_idx = {
            e.index for e in island_edges if Selection._edge_is_uv_boundary(e, uv_layer)
        }
        bpy.ops.mesh.select_mode(type="EDGE")
        bm = Selection._edit_bmesh(obj)
        for e in bm.edges:
            e.select = e.index in border_idx
        Selection._flush(obj, bm)
        return len(border_idx)

    @staticmethod
    def select_uv_perimeter(obj):
        """UV Perimeter: the boundary of the current selection's UV footprint — Maya's
        ``ConvertSelectionToUVPerimeter``. Like Edge Perimeter (the ring around the selected
        region) but measured in UV space: an edge is on the perimeter if exactly one of its faces
        is selected (region boundary) OR both are selected but it is a UV seam interior to the
        selection (a UV-space boundary even though the region is topologically continuous there)."""
        import bpy

        bm = Selection._edit_bmesh(obj)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return 0
        sel_face_idx = {f.index for f in bm.faces if f.select}
        if not sel_face_idx:
            return 0
        bpy.ops.mesh.select_mode(type="EDGE")
        bm = Selection._edit_bmesh(obj)
        uv_layer = bm.loops.layers.uv.active
        selected = {bm.faces[i] for i in sel_face_idx}
        perimeter = set()
        for e in bm.edges:
            n_sel = sum(1 for f in e.link_faces if f in selected)
            if n_sel == 0:
                continue
            if n_sel == 1 or Selection._edge_is_uv_boundary(e, uv_layer):
                perimeter.add(e)
        for e in bm.edges:
            e.select = e in perimeter
        Selection._flush(obj, bm)
        return len(perimeter)

    @staticmethod
    def select_uv_edge_loop(obj):
        """UV Edge Loop: the topological edge loop through the selection, truncated at UV seams —
        Maya's ``polySelectEdges "edgeUVLoopOrBorder"``. Runs Blender's native loop select, then
        keeps only the run of loop edges reachable from the original selection without crossing a
        UV break: a UV-boundary edge is dropped, and a vertex touching any UV-boundary edge stops
        the walk (the UV is discontinuous there). If the selection was entirely on a seam (no
        walkable start), the original edges are returned unchanged."""
        import bpy

        bpy.ops.mesh.select_mode(type="EDGE")
        bm = Selection._edit_bmesh(obj)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            return 0
        orig_idx = {e.index for e in bm.edges if e.select}
        if not orig_idx:
            return 0

        # The current selection is already exactly ``orig_idx``; loop-select extends it in place.
        Selection.loop_multi_select()

        bm = Selection._edit_bmesh(obj)
        uv_layer = bm.loops.layers.uv.active
        loop_edges = {e for e in bm.edges if e.select}
        walkable = {
            e for e in loop_edges if not Selection._edge_is_uv_boundary(e, uv_layer)
        }
        # break vertices: a loop vertex the walk must stop at because one of its incident edges is
        # a UV boundary (seam/open). Only loop vertices are ever visited, so restrict the (costly)
        # boundary test to them rather than scanning every edge in the mesh.
        loop_verts = {v for e in loop_edges for v in e.verts}
        break_verts = {
            v
            for v in loop_verts
            if any(
                Selection._edge_is_uv_boundary(inc, uv_layer) for inc in v.link_edges
            )
        }
        start = {e for e in walkable if e.index in orig_idx}
        result = set(start)
        frontier = list(start)
        while frontier:  # BFS through shared verts, never crossing a break vertex
            e = frontier.pop()
            for v in e.verts:
                if v in break_verts:
                    continue
                for ne in v.link_edges:
                    if ne in walkable and ne not in result:
                        result.add(ne)
                        frontier.append(ne)
        if not result:  # selection sat entirely on a seam — keep it as-is
            result = {bm.edges[i] for i in orig_idx}
        for e in bm.edges:
            e.select = e in result
        Selection._flush(obj, bm)
        return len(result)

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

    # ------------------------------------------------------------------ the selection panel's operations
    # Mirrors of mayatk's (tentacle's selection panel calls them).

    @staticmethod
    def objects_in_scope(scope="all"):
        """The objects a selection filter draws from -- mirror of mayatk's:
        ``"all"`` (every object in the scene), ``"selected"`` or ``"visible"``
        (visible in the window's view layer).

        Raises:
            ValueError: An unknown *scope*.
        """
        import bpy

        if scope == "selected":
            return CoreUtils.selected_objects()
        if scope not in ("all", "visible"):
            raise ValueError(
                f"Unknown scope {scope!r}: use 'all', 'selected' or 'visible'."
            )
        with CoreUtils.window_context_override():  # the window's scene and layer
            if scope == "all":
                return list(bpy.context.scene.objects)
            layer = bpy.context.view_layer
            return [o for o in layer.objects if o.visible_get(view_layer=layer)]

    @staticmethod
    def reorder_selection(objects=None, method="name", reverse=False):
        """Reorder *objects* (``None``: the selection) by *method*
        (``CoreUtils.reorder_objects``) -- mirror of mayatk's. Blender's
        reselection carries no order, so the order is recorded on
        :class:`SelectionOrder` and the last object made active (Blender's
        newest pick).

        Returns:
            list: The objects in their new order; ``[]`` with none.
        """
        objects = (
            CoreUtils.selected_objects()
            if objects is None
            else [o for o in objects if o is not None]
        )
        if not objects:
            return []
        reordered = CoreUtils.reorder_objects(objects, method=method, reverse=reverse)
        if reordered:
            SelectionOrder.set_order(reordered)
            layer = CoreUtils.active_view_layer()
            if layer is not None:
                layer.objects.active = reordered[-1]
        return list(reordered or [])

    @staticmethod
    def _select_linked():
        import bpy

        bpy.ops.mesh.select_linked()

    @staticmethod
    def _region_to_loop():
        import bpy

        bpy.ops.mesh.region_to_loop()

    @staticmethod
    def _faces_select_linked_flat():
        import bpy

        bpy.ops.mesh.faces_select_linked_flat()

    @staticmethod
    def _shortest_path_select():
        import bpy

        bpy.ops.mesh.shortest_path_select()

    #: Convert To: conversion name (mayatk's) -> the operation on the Edit-Mode
    #: mesh. Blender has no vertex-face or UV component, so those two are not
    #: listed; touching vs contained is ``convert_to``'s one native flag.
    _CONVERSIONS = {
        "vertices": lambda obj: Selection.convert_to(obj, "VERT"),
        "vertex_perimeter": lambda obj: Selection.select_vertex_perimeter(obj),
        "edges": lambda obj: Selection.convert_to(obj, "EDGE"),
        "edge_loop": lambda obj: Selection.loop_multi_select(),
        "edge_ring": lambda obj: Selection.loop_multi_select(ring=True),
        "contained_edges": lambda obj: Selection.convert_to(obj, "EDGE", contained=True),
        "edge_perimeter": lambda obj: Selection.select_edge_perimeter(obj),
        "border_edges": lambda obj: Selection.select_border_edges(obj),
        "faces": lambda obj: Selection.convert_to(obj, "FACE"),
        "face_path": lambda obj: Selection.select_face_path(obj),
        "contained_faces": lambda obj: Selection.convert_to(obj, "FACE", contained=True),
        "face_perimeter": lambda obj: Selection.select_face_perimeter(obj),
        "uv_shell": lambda obj: Selection.select_uv_shell(obj),
        "uv_shell_border": lambda obj: Selection.select_uv_shell_border(obj),
        "uv_perimeter": lambda obj: Selection.select_uv_perimeter(obj),
        "uv_edge_loop": lambda obj: Selection.select_uv_edge_loop(obj),
        "shell": lambda obj: Selection._select_linked(),
        "shell_border": lambda obj: Selection.select_shell_border(obj),
    }

    @classmethod
    def get_conversion_types(cls):
        """The conversions :meth:`convert_selection` knows, in menu order --
        mirror of mayatk's, less the vertex-face and UV components Blender
        lacks."""
        return list(cls._CONVERSIONS)

    @classmethod
    def convert_selection(cls, conversion):
        """Convert the Edit-Mode selection of the active (else first selected)
        mesh, entering Edit Mode on it -- mirror of mayatk's
        ``Selection.convert_selection``.

        Returns:
            list: The mesh; ``[]`` with no mesh.

        Raises:
            ValueError: An unknown *conversion*.
            RuntimeError: Blender refused (its report).
        """
        op = cls._CONVERSIONS.get(conversion)
        if op is None:
            raise ValueError(f"Unknown conversion {conversion!r}.")
        obj = CoreUtils.ensure_component_mode("mesh")
        if obj is None:
            return []
        with CoreUtils.window_context_override():
            op(obj)
        return [obj]

    #: Selection constraints (mayatk's names) -> the one-shot expansion each is
    #: here, and the select mode it needs (``None``: keep). Angle grows a face
    #: selection: ``faces_select_linked_flat`` works in face mode only.
    _CONSTRAINTS = {
        "angle": (lambda obj: Selection._faces_select_linked_flat(), "face"),
        "border": (lambda obj: Selection._region_to_loop(), None),
        "edge_loop": (lambda obj: Selection.loop_multi_select(), None),
        "edge_ring": (lambda obj: Selection.loop_multi_select(ring=True), None),
        "shell": (lambda obj: Selection._select_linked(), None),
        "uv_edge_loop": (lambda obj: Selection.select_uv_edge_loop(obj), None),
    }

    @staticmethod
    def get_selection_constraints():
        """``None``: Blender keeps no selection constraint on -- mirror of
        mayatk's, which returns the constraints Maya has on."""
        return None

    @classmethod
    def set_selection_constraint(cls, constraint, state=True):
        """Expand the current Edit-Mode selection once by *constraint*'s rule:
        Blender's reading of mayatk's persistent constraint, as it has no
        drag-select constraint to leave on. *state* False does nothing (nothing
        stays on to switch off).

        Returns:
            None: no constraint stays on.

        Raises:
            ValueError: An unknown *constraint*, or no mesh to act on.
            RuntimeError: Blender refused (its report).
        """
        entry = cls._CONSTRAINTS.get(constraint)
        if entry is None:
            raise ValueError(f"Unknown selection constraint {constraint!r}.")
        if not state:
            return None
        op, mask = entry
        obj = CoreUtils.ensure_component_mode("mesh", mask)
        if obj is None:
            raise ValueError("Selection constraints need a mesh: select one.")
        with CoreUtils.window_context_override():
            op(obj)
        return None

    #: Select Nth's paths -> the selection operation, and the select mode it
    #: needs. The two-pick paths keep the mode: forcing edge mode would rebuild
    #: the selection and clear the pick history ``shortest_path_select`` reads.
    _EDGE_PATHS = {
        "edge_ring": (lambda: Selection.loop_multi_select(ring=True), "edge"),
        "edge_loop": (lambda: Selection.loop_multi_select(), "edge"),
        "edge_loop_path": (lambda: Selection._shortest_path_select(), None),
        "shortest_path": (lambda: Selection._shortest_path_select(), None),
        "border_edges": (lambda: Selection._region_to_loop(), "edge"),
    }

    @classmethod
    def select_edge_path(cls, components=None, path="edge_loop", step=1):
        """Select the edge path through the Edit-Mode selection of the active
        (else first selected) mesh, keeping every *step*-th component -- mirror
        of mayatk's ``Selection.select_edge_path``. ``components`` is accepted
        for mayatk's signature: the Edit-Mode selection is what is read.

        Returns:
            list: The mesh; ``[]`` with no mesh.

        Raises:
            ValueError: An unknown *path*.
            RuntimeError: Blender refused (its report: two picks needed).
        """
        import bpy

        entry = cls._EDGE_PATHS.get(path)
        if entry is None:
            raise ValueError(f"Unknown edge path {path!r}.")
        run, mask = entry
        obj = CoreUtils.ensure_component_mode("mesh", mask)
        if obj is None:
            return []
        with CoreUtils.window_context_override():
            run()
            if step > 1:
                # Headless-verified (Blender 5.1): select_nth(skip=step-1, nth=1)
                # keeps 1 of every `step` (12-vert circle, skip=3/nth=1 -> 3 kept),
                # mayatk's result[::step]. The RNA labels suggest the opposite
                # mapping; trust the measurement.
                bpy.ops.mesh.select_nth(skip=step - 1, nth=1)
        return [obj]

    #: Edit-Mode Select Similar: Blender's native type per component mode.
    _COMPONENT_SIMILAR = {"VERT": "NORMAL", "EDGE": "LENGTH", "FACE": "AREA"}

    @staticmethod
    def _uv_island_select_mode():
        """True with the UV editor in Island select mode, the analogue of Maya's
        UV-shell selection type: Blender 5's ``use_uv_select_island`` toggle,
        4.x's ``uv_select_mode`` ``ISLAND`` value."""
        import bpy

        ts = bpy.context.scene.tool_settings
        return bool(getattr(ts, "use_uv_select_island", False)) or (
            getattr(ts, "uv_select_mode", "") == "ISLAND"
        )

    @classmethod
    def select_similar(cls, objects=None, tolerance=0.0, include_original=False, **metrics):
        """Select what is similar to the selection -- mirror of mayatk's
        ``Selection.select_similar``.

        * Object Mode: meshes alike by the checked *metrics*
          (``EditUtils.get_similar_mesh``).
        * Edit Mode, UV editor in Island select mode: the UV islands of the
          selected island's topology and shape (``UvUtils.get_similar_uv_shells``,
          the Stack (Similar) oracle); nothing moves.
        * Edit Mode otherwise: Blender's native Select Similar for the component
          mode, *tolerance* its 0-1 threshold (larger values count as 1).

        Returns:
            tuple: ``(scope, matched)``: scope ``"objects"``, ``"uv_shells"`` or
                ``"components"``; *matched* what was found beyond the reference
                (``[]`` for none, and with nothing selected).

        Raises:
            TypeError: An unknown metric.
            RuntimeError: Blender refused (its report).
        """
        import bpy

        from blendertk.edit_utils._edit_utils import EditUtils
        from blendertk.uv_utils._uv_utils import UvUtils

        known = {
            "vertex", "edge", "face", "triangle", "shell", "uvcoord", "area",
            "world_area", "bounding_box",
        }
        unknown = set(metrics) - known
        if unknown:
            raise TypeError(f"Unknown Select Similar metrics: {sorted(unknown)}")
        objects = (
            CoreUtils.selected_objects()
            if objects is None
            else [o for o in objects if o is not None]
        )
        if not objects:
            return "objects", []
        active = CoreUtils.active_object()
        if active is not None and active.mode == "EDIT":
            if cls._uv_island_select_mode():
                found = UvUtils.get_similar_uv_shells(
                    [o for o in objects if o.type == "MESH"],
                    tolerance=tolerance,
                    include_reference=include_original,
                    select=True,
                )
                return "uv_shells", [
                    (name, index) for name, faces in (found or {}).items() for index in faces
                ]
            vert, edge, face = bpy.context.scene.tool_settings.mesh_select_mode
            mode = "FACE" if face else "EDGE" if edge else "VERT"
            with CoreUtils.window_context_override():
                bpy.ops.mesh.select_similar(
                    type=cls._COMPONENT_SIMILAR[mode],
                    threshold=min(max(tolerance, 0.0), 1.0),
                )
            return "components", [active]
        matched = EditUtils.get_similar_mesh(
            objects, tolerance=tolerance, inc_orig=include_original, select=True, **metrics
        )
        return "objects", [o for o in matched or () if o not in objects]

    #: Select Island's delimiters (Blender's Select Linked ones).
    _ISLAND_DELIMITERS = ("SEAM", "SHARP", "NORMAL", "MATERIAL", "UV")

    @classmethod
    def select_island(
        cls, faces=None, range_x=0.05, range_y=0.05, range_z=0.05, delimit=None
    ):
        """Select the contiguous faces, around the selected ones, whose normals
        are within the ranges of a selected face's normal -- mirror of mayatk's
        ``Selection.select_island``. Growth also stops at the *delimit*
        boundaries: ``"SEAM"`` (UV seams), ``"SHARP"`` (sharp edges),
        ``"NORMAL"`` (flipped neighbors), ``"MATERIAL"`` (another material),
        ``"UV"`` (UV-island borders). ``None`` puts the active or first selected
        mesh in Edit Mode.

        Returns:
            list: The meshes whose faces were selected; ``[]`` with no face selected.

        Raises:
            ValueError: An unknown delimiter.
        """
        import bmesh

        delimit = {str(d).upper() for d in (delimit or ())}
        unknown = delimit - set(cls._ISLAND_DELIMITERS)
        if unknown:
            raise ValueError(
                f"Unknown delimiters {sorted(unknown)}: use {list(cls._ISLAND_DELIMITERS)}."
            )
        if faces is None:
            if CoreUtils.ensure_component_mode("mesh") is None:
                return []
            faces = CoreUtils.selected_objects()
        meshes = [
            o for o in faces if getattr(o, "type", None) == "MESH" and o.mode == "EDIT"
        ]
        done = []
        for o in meshes:
            bm = bmesh.from_edit_mesh(o.data)
            bm.faces.index_update()
            seeds = [f for f in bm.faces if f.select]
            if not seeds:
                continue
            refs = [f.normal.copy() for f in seeds]
            uv_layer = bm.loops.layers.uv.active

            def similar(face, refs=refs):
                n = face.normal
                return any(
                    abs(n.x - r.x) <= range_x
                    and abs(n.y - r.y) <= range_y
                    and abs(n.z - r.z) <= range_z
                    for r in refs
                )

            def stops(edge, face, other, uv_layer=uv_layer):
                return (
                    ("SEAM" in delimit and edge.seam)
                    or ("SHARP" in delimit and not edge.smooth)
                    or ("NORMAL" in delimit and not edge.is_contiguous)
                    or ("MATERIAL" in delimit and face.material_index != other.material_index)
                    or (
                        "UV" in delimit
                        and uv_layer is not None
                        and Selection._edge_is_uv_boundary(edge, uv_layer)
                    )
                )

            island = {f.index for f in seeds}
            stack = list(seeds)
            while stack:
                face = stack.pop()
                for edge in face.edges:
                    for other in edge.link_faces:
                        if (
                            other.index not in island
                            and similar(other)
                            and not stops(edge, face, other)
                        ):
                            island.add(other.index)
                            stack.append(other)
            for seq in (bm.verts, bm.edges, bm.faces):
                for element in seq:
                    element.select = False
            for f in bm.faces:
                if f.index in island:
                    f.select_set(True)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(o.data)
            done.append(o)
        return done

    @staticmethod
    @CoreUtils.undoable
    def toggle_selectability(objects=None):
        """Toggle whether *objects* can be picked in the viewport
        (``hide_select``) -- mirror of mayatk's: one state for all, selectable
        again when every one is unselectable, else unselectable. One undo step.

        Blender drops an object it makes unselectable from the selection, so a
        selection can only be made unselectable; called with ``None`` and
        nothing selected, every unselectable object in the window's view layer
        becomes selectable again, and is selected.

        Returns:
            (tuple/list): ``(selectable, objects)``, the new state; ``[]`` with
                nothing to toggle.
        """
        objects = (
            CoreUtils.selected_objects()
            if objects is None
            else [o for o in objects if o is not None]
        )
        if objects:
            selectable = all(o.hide_select for o in objects)
            for o in objects:
                o.hide_select = not selectable
            return selectable, objects
        layer = CoreUtils.active_view_layer()
        hidden = [o for o in (layer.objects if layer is not None else ()) if o.hide_select]
        if not hidden:
            return []
        for o in hidden:
            o.hide_select = False
        CoreUtils.select(hidden, mode="add")
        return True, hidden

    @staticmethod
    def get_ignore_backfacing():
        """True while viewport picks skip what faces away from the camera: the
        3D views' X-ray is off -- mirror of mayatk's (camera-based selection).
        ``None`` without a 3D view (nothing to read)."""
        areas = CoreUtils.get_areas("VIEW_3D")
        if not areas:
            return None
        return not areas[0].spaces.active.shading.show_xray

    @staticmethod
    def set_ignore_backfacing(state=True):
        """Turn X-ray off (*state*) or on in every 3D view; returns the state set."""
        for area in CoreUtils.get_areas("VIEW_3D"):
            area.spaces.active.shading.show_xray = not state
        return bool(state)

    #: mayatk's rig picking modes.
    RIG_SELECTION_MODES = ("rig", "normal", "meshes_only")

    @staticmethod
    def get_rig_selection_mode():
        """``"normal"``: blendertk's rigs lock no mesh, so there is no rig-aware
        picking to lift -- mirror of mayatk's (tentacle shows the control in Maya
        only)."""
        return "normal"

    @classmethod
    def set_rig_selection_mode(cls, mode):
        """Mirror of mayatk's: no mesh is locked here, so none is unlocked.

        Returns:
            list: ``[]``: the rigged meshes unlocked.

        Raises:
            ValueError: An unknown *mode*.
        """
        if mode not in cls.RIG_SELECTION_MODES:
            raise ValueError(
                f"Unknown rig selection mode {mode!r}: use one of {cls.RIG_SELECTION_MODES}."
            )
        return []


class SelectionOrder:
    """Ordered **object** selection — rolled infrastructure Blender doesn't ship.

    Blender operators receive an unordered selection set; only component selection records
    order (``bmesh.select_history``). Maya tools that consume click order (Reorder Selection,
    ordered transfers, rig chains) therefore had no Blender surface. This tracker maintains
    one: a ``depsgraph_update_post`` handler diffs the selected set on every update, appending
    newly-selected names in the order they appear (the active object last — Blender makes the
    last-clicked object active, so single-click sequences record true click order; a box/lasso
    multi-select arrives as one batch in arbitrary order, same limit Maya's
    ``trackSelectionOrder`` has within one drag) and dropping deselected ones.

    Session-scoped (names, not saved with the .blend). ``enable()`` is idempotent;
    :func:`get` degrades gracefully to the unordered selection while disabled.
    """

    _order: list = []  # object names, oldest-selected first
    _enabled = False
    _handler = None  # the @persistent-wrapped callable actually registered

    @classmethod
    def enable(cls):
        """Start tracking (idempotent). Safe to call at startup — the handler is a no-op
        until selection actually changes. The registered callable is ``@persistent``:
        non-persistent handlers are cleared on every file load, which would silently kill
        a startup-armed tracker the first time the user opens a .blend."""
        import bpy
        from bpy.app.handlers import persistent

        if cls._enabled:
            return
        if cls._handler is None:

            @persistent
            def _selection_order_update(scene, depsgraph=None):
                cls._on_depsgraph(scene, depsgraph)

            cls._handler = _selection_order_update
        cls._order = [o.name for o in CoreUtils.selected_objects()]
        bpy.app.handlers.depsgraph_update_post.append(cls._handler)
        cls._enabled = True

    @classmethod
    def disable(cls):
        """Stop tracking and clear the recorded order."""
        import bpy

        try:
            bpy.app.handlers.depsgraph_update_post.remove(cls._handler)
        except ValueError:
            pass
        cls._enabled = False
        cls._order = []

    @classmethod
    def is_enabled(cls):
        return cls._enabled

    @classmethod
    def _on_depsgraph(cls, scene, depsgraph=None):
        import bpy

        # a render/undefined handler context -- nothing to diff
        if bpy.context.view_layer is None:
            return
        # The window's layer, as ``selected_objects`` reads it: a depsgraph handler runs
        # windowless, where ``context.view_layer`` is the scene's default layer.
        view_layer = CoreUtils._active_view_layer()
        selected = [
            o.name for o in view_layer.objects if o.select_get(view_layer=view_layer)
        ]
        selected_set = set(selected)
        order = [n for n in cls._order if n in selected_set]
        known = set(order)
        fresh = [n for n in selected if n not in known]
        active = view_layer.objects.active
        # Active last: Blender activates the last-clicked object, so within one update
        # batch the active is the newest pick.
        if active is not None and active.name in fresh:
            fresh = [n for n in fresh if n != active.name] + [active.name]
        cls._order = order + fresh

    @classmethod
    def get(cls, objects=None):
        """The selected objects in recorded selection order (oldest pick first).

        ``objects`` optionally restricts/reorders an explicit set instead of the live
        selection. While the tracker is disabled, falls back to the unordered selection —
        callers stay functional, just order-blind (documented degradation, mirroring how
        Maya behaves with ``trackSelectionOrder`` off)."""

        pool = list(objects if objects is not None else CoreUtils.selected_objects())
        if not cls._enabled:
            return pool
        rank = {n: i for i, n in enumerate(cls._order)}
        return sorted(pool, key=lambda o: rank.get(o.name, len(rank)))

    @classmethod
    def set_order(cls, objects):
        """Explicitly record ``objects`` (in the given order) as the selection order — the
        write half of Maya's 'reselect in new order' (Blender reselection alone can't carry
        order). Enables tracking so the order persists through subsequent picks."""
        cls.enable()
        cls._order = [o.name for o in objects]


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
