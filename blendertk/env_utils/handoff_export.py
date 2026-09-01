# !/usr/bin/python
# coding=utf-8
"""Blender-side selection + export hooks shared by the hand-off bridge engines.

:class:`BlenderExportMixin` supplies the two DCC-specific :class:`pythontk.HandoffBridge`
hooks every Blender-originating bridge shares -- read the selection and export it in
the request's carrier, FBX (including the strip-materials path) or USD -- so the Maya
bridge, the Unity bridge, and any future Blender->X bridge don't each re-implement
them. Mirror of mayatk's :class:`mayatk.env_utils.handoff_export.MayaExportMixin`.

``import bpy`` is deferred into the strip path so the engine surface resolves under
headless ``blender --background`` and outside Blender entirely; ``blendertk`` itself
imports Qt-free.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import blendertk as btk
from pythontk import Payload


class BlenderExportMixin:
    """The Blender producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

    Supplies the two DCC-specific :class:`pythontk.HandoffBridge` steps every
    Blender-originating bridge shares -- read the selection and produce the
    :class:`pythontk.Payload` in the request's carrier (FBX, incl. the
    strip-materials path, or USD). Bridges needing side artifacts override
    :meth:`_produce` and call :meth:`_export_payload` themselves with a path whose
    extension names the carrier. Mirror of mayatk's
    :class:`mayatk.env_utils.handoff_export.MayaExportMixin`.
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

    #: ``FbxUtils._KNOWN_PRODUCERS`` keys whose channel is COMPUTED from live
    #: scene state rather than merely republished from authored state, and so must
    #: be rebuilt before a hand-off ships the carrier (mirror of mayatk's). A
    #: producer with nothing to publish clears its channel, so this must NOT be the
    #: whole set -- ``visibility_tracks`` reads the visibility curves themselves,
    #: which an artist edits between one preview push and the next.
    refresh_producers: Tuple[str, ...] = ("visibility",)

    def lightmap_search_dirs(self) -> List[str]:
        """Where Blender's map files live now (:class:`pythontk.PreviewBridge` hook).

        Answers the question the FBX's lightmap manifest cannot: it records the
        folder the bake was COMMITTED from, and a project reorganised since (or
        opened on another machine) leaves every EXR lookup missing -- which
        previews as an unlit push and reads as a broken bake. Mirror of
        mayatk's method of the same name: the workspace's texture folders plus
        wherever the markers' maps were actually found
        (:meth:`LightmapBaker.search_dirs`), so a map the walk had to go
        looking for still reaches a consumer that can only join a basename
        against a list.
        """
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        return LightmapBaker.search_dirs()

    #: What the USD carrier does with linked duplicates in the export set. USD
    #: leaves Blender FLAT (``use_instancing`` off: USD's instancing hands Maya
    #: read-only prototypes, not its shared-shape model, and prototype prim names
    #: break 1:1 name matching), so data sharing does not survive the hop.
    #: ``False`` (default) REFUSES the send with a pointer at FBX, which carries
    #: instancing natively -- for a scene hand-off a flat copy is a silent
    #: structural loss (what reverted a USD default on 2026-08-02). ``True`` lets
    #: it through flat with a warning: right for a texturing / baking target that
    #: never hands geometry back. Mirror of mayatk's flag of the same name.
    usd_flattens_instances: bool = False

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
            from blendertk.env_utils.fbx_utils import FbxUtils
        except ImportError:  # engine-surface tests outside Blender
            return []

        # Make the DERIVED channels current first -- and only those (mirror of
        # mayatk's). Most channels are authored state a producer republishes,
        # but some are computed from the live scene every export --
        # ``visibility_tracks`` reads the visibility curves themselves -- and go
        # stale the moment an artist re-keys. Narrowed because a producer with
        # nothing to publish CLEARS its channel: refreshing everything from here
        # wiped a lightmap manifest the scene no longer described.
        if self.refresh_producers:
            try:
                FbxUtils.run_export_preparers(only=self.refresh_producers)
            except Exception:  # noqa: BLE001
                self.logger.debug("data_export refresh skipped.", exc_info=True)

        node = DataNodes.get_export_node(create=False)
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

        # ``data_internal`` must never ride a hand-off FBX. In Maya that
        # guarantee is structural (a network node can't enter a DAG export
        # set); here the carrier is a plain Empty that a whole-scene send
        # sweeps in — and ``include_data_export`` forcing ``use_custom_props``
        # would then ship SmartBake manifests and the emissive registry as
        # FBX user properties on the far side.
        from blendertk.node_utils.data_nodes import DataNodes

        return [o for o in closure if o.name != DataNodes.INTERNAL]

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

    @staticmethod
    def _visible_objects() -> List[Any]:
        """Every CURRENTLY VISIBLE object in the scene (the Visible Only scope).

        Mirror of mayatk's, and the same sibling relationship to
        :meth:`_scene_objects`: the two widening scopes
        ``uitk.bridge.Parameters.scope_spec`` declares. Static because it
        consults only the scene, which lets
        ``BlenderBridgeSlotsBase.resolve_scope_objects`` call it for panels
        whose bridge has no export mixin -- one implementation of "visible".

        Unfiltered by type, exactly as ``_scene_objects`` is: a visible group
        Empty is part of what the user sees, and the export's ``object_types``
        already decides what travels. ``visible_get()`` rather than
        ``hide_viewport``: it answers for the evaluated view layer, which is
        what "visible" means to the person looking at the screen.
        """
        import bpy

        return [o for o in bpy.context.scene.objects if o.visible_get()]

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
        path = self._make_payload_path(self.payload_extension(request))
        self._export_payload(objects, path, request.params)
        return Payload(primary=path, extras={"export_set": objects})

    def _payload_writers(
        self,
    ) -> Dict[str, Callable[[List[Any], str, Dict[str, Any]], None]]:
        """``{carrier: writer(objects, path, params)}`` -- the Strategy table.

        A new carrier is one entry here plus its writer; a bridge that needs a
        different surface for one carrier overrides the entry, not the dispatch.
        Mirror of mayatk's.
        """
        return {"fbx": self._export_fbx, "usd": self._export_usd}

    def _export_payload(
        self, objects: List[Any], path: str, params: Dict[str, Any]
    ) -> None:
        """Export *objects* to *path* in the carrier its extension names -- the
        ONE dispatch, keyed on the extension (:meth:`pythontk.HandoffBridge.carrier_of`)
        so a bridge that builds its own payload path only has to name it right."""
        self._payload_writers()[self.carrier_of(path)](objects, path, params)

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
            # Mirror of mayatk's pinned ``FBXExportTangents``: an asset that
            # ships no tangents leaves its tangent basis for the receiver to
            # invent, and receivers disagree — three.js swaps in a screen-space
            # derivative basis and flips green to compensate, while a baker
            # wants the SAME basis the asset was authored against.
            use_tspace=True,
            embed_textures=bool(params.get("EMBED_TEXTURES", True)),
            path_mode=("COPY" if params.get("EMBED_TEXTURES", True) else "AUTO"),
            use_triangles=bool(params.get("TRIANGULATE", False)),
            bake_anim=bool(params.get("INCLUDE_ANIMATION", False)),
            # One scene-range AnimStack with ABSOLUTE times: the exporter's default
            # multi-stack modes write one start-zeroed stack per action, so a clip
            # keyed at 10-90 arrives in the target at 0-80 (measured on the pull
            # route). Same rule as FbxUtils.export / the Scene Exporter.
            bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False,
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

        # Guards the reset below on having ATTEMPTED the split rather than on
        # having armed one, mirroring mayatk: a raise inside ``apply_takes``
        # would otherwise leave armed state behind with nothing to clear it.
        wants_animation = bool(params.get("INCLUDE_ANIMATION", False))
        if wants_animation:
            # Realize the shots the scene DECLARES as named AnimStacks, so every
            # animated hand-off carries per-shot clips rather than one
            # whole-timeline take a consumer has to slice by hand. Mirror of
            # ``mtk.MayaExportMixin._export_fbx``: the take split reached only
            # the Scene Exporter, which calls it explicitly, so the preview and
            # the exporter disagreed about whether shots survive.
            #
            # Declared, never regenerated -- this arms whatever is already on the
            # carrier, so a preview push stays free of scene side effects.
            takes = btk.FbxUtils.apply_takes_from_node()
            if takes:
                self.logger.info(f"Animation: realized {takes} declared take(s).")

        try:
            if bool(params.get("INCLUDE_MATERIALS", True)):
                btk.FbxUtils.export_selection_fbx(
                    filepath=fbx_path, objects=list(objects) + carrier, **fbx_opts
                )
                return

            # Strip-materials path: export shader-less copies, leave originals alone.
            import bpy

            src = [
                bpy.data.objects.get(o) if isinstance(o, str) else o for o in objects
            ]
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
                    filepath=fbx_path,
                    objects=[d[0] for d in dups] + carrier,
                    **fbx_opts,
                )
            finally:
                for obj, copied_data in dups:
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    except Exception:
                        pass
                    # Drop the orphaned copied datablock so the strip leaves no residue.
                    if (
                        copied_data is not None
                        and getattr(copied_data, "users", 0) == 0
                    ):
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
        finally:
            if wants_animation:
                # Armed takes are sticky until cleared (``apply_takes``' own
                # contract): left armed, the next export would split a file the
                # caller never asked to split. Covers BOTH export paths.
                btk.FbxUtils.reset_takes()

    def _usd_options(
        self, params: Dict[str, Any], objects: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """Blender ``wm.usd_export`` options derived from the bridge params.

        :attr:`btk.UsdUtils.INTERCHANGE_EXPORT_OPTIONS` (the pull route's
        live-verified set, reasons documented there), not a new guess.

        Params map where USD has a native answer and are reported where it has
        none: ``INCLUDE_MATERIALS`` off is ``export_materials=False`` (no strip
        copies needed); ``INCLUDE_ANIMATION`` samples only the frames that carry
        motion (:meth:`btk.UsdUtils.sampling_frame_range`, threaded through as
        ``frame_range``); ``TRIANGULATE`` maps to ``triangulate_meshes``;
        ``EMBED_TEXTURES`` has no USD equivalent and is logged as inert. Option
        names this Blender doesn't know are dropped by the engine's RNA filter.
        """
        options = dict(
            btk.UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
            export_materials=bool(params.get("INCLUDE_MATERIALS", True)),
            triangulate_meshes=bool(params.get("TRIANGULATE", False)),
            # The FBX twin of this knob (``apply_unit_scale``): Maya reads a USD
            # layer in its own cm without converting (mayaUsd 0.30 has no unit
            # conversion on import -- probed), so a Maya-bound layer is written
            # in centimeters. The exporter encodes it exactly as the FBX route
            # lands: a scale of 100 on the ROOT transforms, children untouched,
            # world bounds correct. Off keeps meters (the raw numeric values).
            convert_scene_units=(
                "CENTIMETERS" if params.get("APPLY_UNIT_SCALE", True) else "METERS"
            ),
        )
        if bool(params.get("INCLUDE_ANIMATION", False)):
            frame_range = btk.UsdUtils.sampling_frame_range(objects)
            if frame_range:
                options["export_animation"] = True
                options["frame_range"] = frame_range
        if not bool(params.get("EMBED_TEXTURES", True)):
            self.logger.info(
                "USD references the original texture files; EMBED_TEXTURES does "
                "not apply (a .usdz package would embed them)."
            )
        return options

    def _export_usd(
        self, objects: List[Any], usd_path: str, params: Dict[str, Any]
    ) -> None:
        """Export *objects* to *usd_path* with USD options derived from *params*.

        The USD twin of :meth:`_export_fbx`. No strip path: ``INCLUDE_MATERIALS``
        off is a native exporter flag, so the originals are never copied. The
        ``data_export`` carrier joins the export set as for FBX, with
        ``export_custom_properties`` forced on so its properties ride as USD
        ``userProperties`` (the mirror of the FBX ``use_custom_props`` force).

        Refuses linked duplicates unless :attr:`usd_flattens_instances`: the
        export is flat, so every duplicate would arrive as its own independent
        mesh -- the silent structural loss FBX never has, and the reason the USD
        carrier stays opt-in. The refusal names the meshes and the route that
        works.
        """
        linked = self._linked_duplicates(objects)
        if linked:
            copies = sum(len(users) for users in linked.values())
            detail = f"{copies} object(s) share {len(linked)} mesh(es): " + ", ".join(
                sorted(linked)
            )
            if not self.usd_flattens_instances:
                raise RuntimeError(
                    "The USD carrier exports FLAT -- linked duplicates would not "
                    f"survive the hand-off ({detail}). Send via FBX, which carries "
                    "instancing natively, or make the duplicates single-user first."
                )
            self.logger.warning(
                f"USD carrier: linked duplicates are flattened for this hand-off ({detail})."
            )

        usd_opts = self._usd_options(params, objects)
        frame_range = usd_opts.pop("frame_range", None)
        carrier = self._data_export_carrier()
        if carrier:
            usd_opts["export_custom_properties"] = True
            self.logger.warning(
                "The data_export carrier rides the USD payload as userProperties; "
                "whether the target reads them is not yet verified on this route."
            )
        btk.UsdUtils.export(
            filepath=usd_path,
            objects=list(objects) + carrier,
            selection_only=True,
            frame_range=frame_range,
            **usd_opts,
        )

    @staticmethod
    def _linked_duplicates(objects) -> Dict[str, List[str]]:
        """``{mesh datablock name: [object names sharing it]}`` among *objects*.

        Sharing WITHIN the export set only: a mesh also worn by an object outside
        the set still leaves as one prim, which is no loss. Outside Blender (the
        engine-surface tests) there is nothing to inspect and the answer is
        empty.
        """
        try:
            import bpy
        except ImportError:
            return {}
        users: Dict[str, List[str]] = {}
        for o in objects or []:
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            data = getattr(obj, "data", None)
            if obj is None or data is None or getattr(obj, "type", None) != "MESH":
                continue
            users.setdefault(data.name, []).append(obj.name)
        return {mesh: names for mesh, names in users.items() if len(names) > 1}
