# !/usr/bin/python
# coding=utf-8
"""Blender-side selection + FBX-export hooks shared by the hand-off bridge engines.

:class:`BlenderExportMixin` supplies the two DCC-specific :class:`pythontk.HandoffBridge`
hooks every Blender-originating bridge shares -- read the selection and export it to
FBX (including the strip-materials path) -- so the Maya bridge, the Unity bridge, and
any future Blender->X bridge don't each re-implement them. Mirror of mayatk's
:class:`mayatk.env_utils.handoff_export.MayaExportMixin`.

``import bpy`` is deferred into the strip path so the engine surface resolves under
headless ``blender --background`` and outside Blender entirely; ``blendertk`` itself
imports Qt-free.
"""

from __future__ import annotations

from typing import Any, Dict, List

import blendertk as btk
from pythontk import Payload


class BlenderExportMixin:
    """The Blender producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

    Supplies the two DCC-specific :class:`pythontk.HandoffBridge` steps every
    Blender-originating bridge shares -- read the selection and produce the FBX
    :class:`pythontk.Payload` (incl. the strip-materials path). Bridges needing side
    artifacts override :meth:`_produce` and call :meth:`_export_fbx` themselves.
    Mirror of mayatk's :class:`mayatk.env_utils.handoff_export.MayaExportMixin`.
    """

    #: Ship the shared ``data_export`` carrier alongside the exported objects.
    #:
    #: ``data_export`` is the in-band metadata surface -- the lightmap manifest and
    #: every future producer stamp custom properties onto that one Empty, and the FBX
    #: exporter carries them as user properties. A *selection* export omits it (it is
    #: not in the selected set nor its hierarchy closure), so a bridge whose consumer
    #: READS that metadata must opt in or its deliverable silently arrives bare. Off
    #: by default: to a bridge that only wants geometry the carrier is a stray empty
    #: in the target's outliner. Mirror of mayatk's flag of the same name.
    #:
    #: Turning it on also forces the export options the carrier needs to mean anything
    #: (``use_custom_props``, ``EMPTY`` in ``object_types``) -- see :meth:`_export_fbx`.
    include_data_export: bool = False

    def _data_export_carrier(self) -> List[Any]:
        """``[data_export]`` when this bridge ships it and the scene has one, else ``[]``.

        Never *creates* the carrier: absent means the scene has no in-band metadata to
        ship, and manufacturing an empty one would only put a stray Empty in the
        deliverable. Returned as a list so callers concatenate rather than branch.
        """
        if not self.include_data_export:
            return []
        try:
            from blendertk.node_utils.data_nodes import DataNodes

            node = DataNodes.get_export_node(create=False)
        except ImportError:  # engine-surface tests outside Blender
            return []
        return [node] if node is not None else []

    def _resolve_objects(self, objects):
        """Return the objects to export; ``None`` -> current selection."""
        if objects is None:
            objects = btk.selected_objects()
        return objects or []

    @staticmethod
    def _hierarchy_closure(objects, descend: bool = True) -> List[Any]:
        """*objects* plus their ancestors (and, per *descend*, descendants).

        Blender's FBX exporter writes EXACTLY the given objects -- an unlisted
        parent Empty is dropped and its children re-root, so a bare selection
        silently flattens the scene graph on the far side (live report). Two
        directions, separately load-bearing:

        - **Ancestors** (always) carry the path: only the chain itself is
          added, never an ancestor's other children -- sending one mesh must
          not widen to its siblings.
        - **Descendants** (*descend*) carry the content: selecting a group
          Empty means sending the group (Maya's export-selection includes the
          subtree; Blender's does not, so the closure supplies the parity).
          The Visible Only scope passes ``descend=False`` -- re-adding a
          HIDDEN child of a visible parent would defeat the scope.

        Accepts names or objects; unresolvable names drop (matching
        ``FbxUtils.export``'s tolerance). Outside Blender (engine-surface
        tests) there is no ``bpy`` and nothing to close over -- the input
        passes through unchanged.
        """
        try:
            import bpy
        except ImportError:
            return list(objects)

        resolved: List[Any] = []
        for o in objects:
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None and obj not in resolved:
                resolved.append(obj)

        closure = list(resolved)
        seen = set(closure)
        for obj in resolved:
            parent = obj.parent
            while parent is not None and parent not in seen:
                seen.add(parent)
                closure.append(parent)
                parent = parent.parent
            if not descend:
                continue
            for child in obj.children_recursive:
                if child not in seen:
                    seen.add(child)
                    closure.append(child)
        return closure

    def _scene_objects(self) -> List[Any]:
        """Every object in the CURRENT scene (the whole-scene hand-off).

        Used by ``save_as``, where "save the scene as ..." means the scene rather than
        the selection. The current scene's objects, NOT ``bpy.data.objects`` -- the
        latter also sweeps in unlinked/orphaned objects and objects belonging to other
        scenes (same rule as the panel's "Entire Scene" scope). Unfiltered by type: the
        export's ``object_types`` already decides what travels, and empties/armatures
        must travel.
        """
        import bpy

        return list(bpy.context.scene.objects)

    def _produce(self, objects, request) -> Payload:
        """Export the hierarchy closure of *objects* to a temp FBX :class:`pythontk.Payload`.

        The closure (see :meth:`_hierarchy_closure`) happens here, not in
        ``_resolve_objects``, because it is scope-dependent: Visible Only must
        not descend into hidden children, and only the request carries the
        scope. The closed set rides on ``Payload.extras["export_set"]`` so a
        subclass sidecar (e.g. the Maya bridge's manifest) covers exactly what
        was exported -- a group-Empty send must manifest the DESCENDANT
        meshes' materials, not just the selected Empty.
        """
        objects = self._hierarchy_closure(
            objects, descend=request.params.get("SCOPE") != "visible"
        )
        fbx_path = self._make_payload_path()
        self._export_fbx(objects, fbx_path, request.params)
        return Payload(primary=fbx_path, extras={"export_set": objects})

    def _fbx_options(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Blender ``export_scene.fbx`` options derived from the bridge params.

        Bridges that need a different surface override this.

        ``object_types`` widens ``fbx_utils._EXPORT_DEFAULTS`` for the DCC-to-DCC hand-off:
        Blender's exporter drops every excluded type and re-roots its children, so the set
        must cover everything whose absence is SILENT loss on the far side — ``EMPTY``
        (the scene graph; Maya groups), ``ARMATURE`` (skin deformation — the mirror of
        mayatk's ``FBXExportSkins``), ``OTHER`` (curve / text / metaball geometry, which
        the exporter meshes on the way out). Cameras and lights stay out: the hand-off
        ships assets, and the far side has its own.
        """
        return dict(
            object_types={"MESH", "EMPTY", "ARMATURE", "OTHER"},
            embed_textures=bool(params.get("EMBED_TEXTURES", True)),
            path_mode=("COPY" if params.get("EMBED_TEXTURES", True) else "AUTO"),
            use_triangles=bool(params.get("TRIANGULATE", False)),
            bake_anim=bool(params.get("INCLUDE_ANIMATION", False)),
            apply_unit_scale=bool(params.get("APPLY_UNIT_SCALE", True)),
        )

    def _export_fbx(
        self, objects: List[Any], fbx_path: str, params: Dict[str, Any]
    ) -> None:
        """Export *objects* to *fbx_path* with FBX options derived from *params*.

        When ``INCLUDE_MATERIALS`` is False the objects are copied (full data copy),
        their material slots cleared on the copies, the copies exported, then removed
        -- the originals and the user's selection are untouched (Blender's FBX
        exporter has no "exclude materials" flag).

        The ``data_export`` carrier (when :attr:`include_data_export`) joins the export
        set but never the strip copy -- it holds no material slots to clear, and a copy
        would ship under a ``.001`` name the consumer does not look for.
        """
        fbx_opts = self._fbx_options(params)
        carrier = self._data_export_carrier()
        if carrier:
            # Forced HERE rather than declared in _fbx_options, which subclasses
            # override wholesale: the exporter drops custom properties by default and
            # excluded object types outright, so either omission ships an Empty named
            # `data_export` carrying nothing -- the failure that looks most like
            # success. Shipping the carrier and shipping what makes it readable are
            # one decision, so they cannot be separated by an override.
            fbx_opts["use_custom_props"] = True
            # Through the shared coercion, not a bare set(): a preset-sourced
            # `object_types` can be a list or even a single string, and set("MESH")
            # explodes into characters.
            types = btk.FbxUtils._as_object_types(
                fbx_opts.get("object_types") or {"MESH"}
            )
            fbx_opts["object_types"] = types | {"EMPTY"}

        if bool(params.get("INCLUDE_MATERIALS", True)):
            btk.FbxUtils.export_selection_fbx(
                filepath=fbx_path, objects=list(objects) + carrier, **fbx_opts
            )
            return

        # Strip-materials path: export shader-less copies, leave originals alone.
        import bpy

        src = [bpy.data.objects.get(o) if isinstance(o, str) else o for o in objects]
        src = [o for o in src if o is not None]
        dups = []  # (object, copied_data)
        dup_of = {}
        for o in src:
            nd = o.copy()
            copied_data = None
            if getattr(o, "data", None) is not None:
                copied_data = o.data.copy()
                nd.data = copied_data
            bpy.context.scene.collection.objects.link(nd)
            dups.append((nd, copied_data))
            dup_of[o] = nd
        # Re-parent each copy onto the copy of its parent: ``o.copy()`` keeps
        # ``.parent`` aimed at the ORIGINAL, which is not in the exported set,
        # so the exporter would re-root every child and the strip path would
        # flatten the very hierarchy the closure preserved. The copied
        # ``matrix_parent_inverse`` stays valid -- the new parent has the
        # source parent's transform -- so assigning ``.parent`` directly
        # keeps world placement. A parent OUTSIDE the set stays aimed at the
        # original (unexported -> the exporter re-roots that child with its
        # world transform, same as before the closure existed).
        for o in src:
            if o.parent in dup_of:
                dup_of[o].parent = dup_of[o.parent]
        try:
            for obj, _ in dups:
                data = getattr(obj, "data", None)
                if data is not None and hasattr(data, "materials"):
                    data.materials.clear()
            btk.FbxUtils.export_selection_fbx(
                filepath=fbx_path, objects=[d[0] for d in dups] + carrier, **fbx_opts
            )
        finally:
            for obj, copied_data in dups:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
                # Drop the orphaned copied datablock so the strip leaves no residue.
                if copied_data is not None and getattr(copied_data, "users", 0) == 0:
                    for coll in (
                        getattr(bpy.data, "meshes", None),
                        getattr(bpy.data, "curves", None),
                    ):
                        try:
                            if coll is not None and copied_data.name in coll:
                                coll.remove(copied_data)
                                break
                        except Exception:
                            pass
