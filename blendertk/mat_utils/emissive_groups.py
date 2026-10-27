# !/usr/bin/python
# coding=utf-8
"""Emissive groups — mirror of mayatk's ``mat_utils.emissive_groups``.

Same concept, contract, and manifest (pythontk's region-mask engine is the
shared model): named face groups whose emissive regions a game engine gates
independently via ``emission * dot(mask, weights)``. Public names and
behavior mirror :class:`mayatk.mat_utils.emissive_groups.EmissiveGroups`;
signatures are Blender-idiomatic where the scene models diverge:

- Membership: mayatk uses ``objectSet``s of face components; Blender has no
  component sets, so each group is a **boolean FACE-domain attribute**
  (``emissiveGroup_<name>``) on its member meshes — per-group booleans (not
  one int ID attribute) so overlapping membership stays expressible, exactly
  like Maya sets. ``faces`` arguments take ``{object_name: [face_indices]}``
  (or None = selected faces).
- Vertex-color bake: the ``emissiveGroups`` color attribute
  (``BYTE_COLOR`` / ``CORNER`` domain — per-corner keeps group boundaries
  hard, matching Maya's per-face-vertex write).
- Registry / manifest: the same records as mayatk's -- the private
  ``ptk.SceneRecords.EMISSIVE_REGISTRY`` and the deliverable
  ``EMISSIVE_GROUPS`` -- through the ``node_utils.data_nodes`` store
  (mirror). Blender's FBX exporter ships the export carrier's custom
  properties as FBX user properties (enable *Custom Properties*), which
  unitytk's ``EmissiveGroupController`` importer reads unchanged.
- Keyable weights (opt-in): same API as mayatk — one keyable 0-1 custom
  property per group (``emissiveGroup_<name>``) on the ``data_export``
  carrier, keyed with fcurves for in-Blender authoring. Blender's FBX
  exporter has no custom-property *animation* path (it bakes only
  transform / shape-key / camera channels — verified in the 5.1
  ``io_scene_fbx`` source), so the curves ride a channel it DOES bake:
  at export time the Scene Exporter stages one transient Empty per keyed
  group (:meth:`EmissiveGroups.create_export_curve_proxies`), named exactly
  the group's manifest ``attr``, whose ``scale.x`` carries the weight curve
  (scale is unitless — translation would pick up unit conversion). Unity's
  importer rebinds the proxy's ``m_LocalScale.x`` curve to the controller
  and deletes the node; the proxies are removed from the scene right after
  the FBX write, so a saved scene never carries one.

``import bpy`` is deferred into call bodies (no import side effects).
"""

import json
from typing import Dict, List, Optional

import pythontk as ptk

# From this package:
from blendertk.core_utils._core_utils import CoreUtils
from blendertk.node_utils.data_nodes import DataNodes


class _EmissiveGroupsInternal:
    """Implementation-detail base for :class:`EmissiveGroups`."""

    SET_PREFIX = "emissiveGroup_"
    COLOR_SET = "emissiveGroups"
    #: The deliverable manifest's channel (``ptk.SceneRecords.EMISSIVE_GROUPS``,
    #: on ``data_export``); the private registry keeps its own
    #: (``ptk.SceneRecords.EMISSIVE_REGISTRY``).
    DATA_CHANNEL = ptk.SceneRecords.EMISSIVE_GROUPS.key
    #: Custom-prop marker on export curve proxies — the precise handle the
    #: post-export cleanup (and validate's leftover sweep) matches on.
    PROXY_MARKER = "emissiveGroupCurveProxy"

    # ------------------------------------------------------------------
    # Registry — slot bookkeeping lives in the shared engine (identical to
    # mayatk's); this class only binds it to the scene carrier.
    # ------------------------------------------------------------------

    @classmethod
    def _registry(cls) -> "ptk.RegionGroupRegistry":
        return ptk.RegionGroupRegistry(
            load=lambda: ptk.SceneRecords.EMISSIVE_REGISTRY.read_text(DataNodes),
            save=lambda text: ptk.SceneRecords.EMISSIVE_REGISTRY.write_text(
                DataNodes, text
            ),
            logger=cls.logger,
        )

    @classmethod
    def _refresh_export_if_published(cls) -> None:
        """Keep an already-published manifest current — never create one.

        Authoring (add / remove / weight edits) must not stamp a
        ``data_export`` channel into a scene that has never been baked or
        exported; the export path regenerates it anyway, so creating it
        early is pure scene clutter.
        """
        if ptk.SceneRecords.EMISSIVE_GROUPS.is_present(DataNodes):
            cls.refresh_export_metadata()

    # ------------------------------------------------------------------
    # Keyable-weight helpers (props live on the ``data_export`` carrier)
    # ------------------------------------------------------------------

    @classmethod
    def _weight_attr_exists(cls, name: str) -> bool:
        obj = DataNodes.get_export_node(create=False)
        return obj is not None and cls._attr_name(name) in obj

    @classmethod
    def _weight_fcurve(cls, name: str):
        """The carrier's weight fcurve for *name*, or None (slot-aware, via
        the shared anim_utils helper — Blender 4.4+/5.x drop the legacy flat
        ``action.fcurves``)."""
        obj = DataNodes.get_export_node(create=False)
        if obj is None:
            return None
        from blendertk.anim_utils._anim_utils import AnimUtils

        data_path = f'["{cls._attr_name(name)}"]'
        for fc in AnimUtils.get_fcurves([obj]):
            if fc.data_path == data_path:
                return fc
        return None

    @classmethod
    def _delete_weight_attr(cls, name: str) -> bool:
        """Delete the carrier's weight prop (and its fcurve) for *name*."""
        obj = DataNodes.get_export_node(create=False)
        attr = cls._attr_name(name)
        if obj is None or attr not in obj:
            return False
        fc = cls._weight_fcurve(name)
        if fc is not None:
            from blendertk.anim_utils._anim_utils import AnimUtils

            ad = getattr(obj, "animation_data", None)
            if ad is not None and ad.action is not None:
                try:
                    AnimUtils._remove_fcurve(
                        ad.action, getattr(ad, "action_slot", None), fc
                    )
                except (RuntimeError, ReferenceError, ValueError):
                    pass
        del obj[attr]
        return True

    @classmethod
    def _sync_unkeyed_attr(cls, name: str, value: float) -> None:
        """Keep an *un-keyed* keyable weight prop in step with the group
        default (once keyed, the animation owns the value)."""
        obj = DataNodes.get_export_node(create=False)
        attr = cls._attr_name(name)
        if obj is None or attr not in obj:
            return
        if cls._weight_fcurve(name) is None:
            obj[attr] = float(value)

    # ------------------------------------------------------------------
    # Naming / membership helpers
    # ------------------------------------------------------------------

    @classmethod
    def _attr_name(cls, name: str) -> str:
        return f"{cls.SET_PREFIX}{name}"

    @staticmethod
    def _mesh_objects():
        """Every mesh object the view layer holds.

        Read through the view layer, never ``bpy.context.scene`` /
        ``bpy.context.selected_objects``: those are *screen-context* members
        that are empty — or absent entirely, raising ``AttributeError`` —
        whenever ``bpy.context.window`` is ``None``, which is exactly the
        state the Qt event-pump timer runs these slots in. See
        ``CoreUtils.selected_objects``.
        """
        vl = CoreUtils._active_view_layer()
        if vl is None:
            return []
        return [o for o in vl.objects if o and o.type == "MESH"]

    @classmethod
    def _faces_from(cls, faces=None) -> Dict[str, List[int]]:
        """Resolve *faces* (or the selection) to ``{object_name: [face_indices]}``.

        A whole-mesh entry (empty index list, or a selected object with no
        face selection while in object mode) resolves to every face.
        """
        import bpy

        if faces:
            out = {}
            for obj_name, indices in dict(faces).items():
                obj = bpy.data.objects.get(str(obj_name))
                if obj is None or obj.type != "MESH":
                    continue
                idx = [int(i) for i in indices]
                out[obj.name] = idx if idx else list(range(len(obj.data.polygons)))
            return {k: v for k, v in out.items() if v}
        out = {}
        for obj in CoreUtils.selected_objects():
            if obj.type != "MESH":
                continue
            obj.update_from_editmode()
            selected = [p.index for p in obj.data.polygons if p.select]
            if not selected and obj.mode == "OBJECT":
                selected = list(range(len(obj.data.polygons)))
            if selected:
                out[obj.name] = selected
        return out

    @classmethod
    def _member_map(cls, name: str) -> Dict[str, List[int]]:
        """``{object_name: [face_indices]}`` for a group's membership.

        Read through ``foreach_get`` rather than iterating ``attr.data``:
        the per-element Python loop costs one attribute access per FACE of
        every mesh carrying the attribute (not per member), which the panel
        would pay again for every group on each table refresh — seconds on a
        dense mesh. ``foreach_get`` fills the buffer in one C call.
        """
        import numpy as np

        attr_name = cls._attr_name(name)
        out = {}
        for obj in cls._mesh_objects():
            attr = obj.data.attributes.get(attr_name)
            if attr is None:
                continue
            values = np.empty(len(attr.data), dtype=bool)
            attr.data.foreach_get("value", values)
            indices = np.flatnonzero(values)
            if indices.size:
                out[obj.name] = indices.tolist()
        return out

    @classmethod
    def _member_meshes(cls, names) -> List[str]:
        meshes = []
        for name in names:
            for obj_name in cls._member_map(name):
                if obj_name not in meshes:
                    meshes.append(obj_name)
        return meshes

    # ------------------------------------------------------------------
    # UV harvest (channels encoding)
    # ------------------------------------------------------------------

    @classmethod
    def _harvest_mesh_uv_triangles(cls, obj, face_indices, uv_set: Optional[str]):
        """Triangulated UVs for *face_indices* of one mesh: (N, 3, 2) lists."""
        mesh = obj.data
        layer = mesh.uv_layers.get(uv_set) if uv_set else mesh.uv_layers.active
        if layer is None:
            return []
        members = set(face_indices)
        mesh.calc_loop_triangles()
        tris = []
        for tri in mesh.loop_triangles:
            if tri.polygon_index in members:
                tris.append([tuple(layer.data[lo].uv) for lo in tri.loops])
        return tris


class EmissiveGroups(_EmissiveGroupsInternal, ptk.LoggingMixin, ptk.HelpMixin):
    """Author, bake, and export named emissive face-groups (see module doc).

    Mirror of :class:`mayatk.mat_utils.emissive_groups.EmissiveGroups` —
    same public names, behavior, registry schema, and manifest wire format.
    All state lives in the scene, so every operation is a classmethod.
    """

    # ------------------------------------------------------------------
    # Authoring
    # ------------------------------------------------------------------

    @classmethod
    def add_group(cls, name: str, faces=None, default: float = 1.0) -> str:
        """Create a group from faces (or the selection), or extend an existing one.

        Parameters:
            name: Group name (sanitized to a safe attribute suffix).
            faces: ``{object_name: [face_indices]}`` ([] = all faces);
                defaults to the selected faces (whole meshes in object mode).
            default: Default gate weight consumers apply (1.0 = on).

        Returns:
            The membership attribute name (``emissiveGroup_<name>``).
        """
        import bpy

        name = ptk.RegionGroupRegistry.sanitize(name)
        face_map = cls._faces_from(faces)
        if not face_map:
            raise ValueError("No faces in the input or selection.")
        slot, is_new = cls._registry().add(name, default)
        attr_name = cls._attr_name(name)
        for obj_name, indices in face_map.items():
            mesh = bpy.data.objects[obj_name].data
            attr = mesh.attributes.get(attr_name)
            if attr is None:
                attr = mesh.attributes.new(attr_name, "BOOLEAN", "FACE")
            for i in indices:
                attr.data[i].value = True
        cls.logger.info(
            f"Added group {name!r} (slot {slot})."
            if is_new
            else f"Extended group {name!r}."
        )
        cls._refresh_export_if_published()
        return attr_name

    @classmethod
    def remove_group(cls, name: str) -> None:
        """Delete a group's membership, registry entry, and any keyable
        weight prop; its slot is retired (never auto-reused) so existing
        engine bindings stay valid."""
        cls._registry().remove(name)
        attr_name = cls._attr_name(name)
        for obj in cls._mesh_objects():
            attr = obj.data.attributes.get(attr_name)
            if attr is not None:
                obj.data.attributes.remove(attr)
        cls._delete_weight_attr(name)
        cls._refresh_export_if_published()
        cls.logger.info(f"Removed group {name!r}.")

    @classmethod
    def list_groups(cls) -> Dict[str, dict]:
        """``{name: {"slot", "default", "faces"(count), "missing", "attr"
        (keyable weight prop or None)}}`` in slot order. ``missing`` =
        registry entry with no membership attribute on any mesh."""
        out = {}
        for entry in cls._registry().groups():
            name = entry["name"]
            members = cls._member_map(name)
            out[name] = {
                "slot": entry["slot"],
                "default": entry["default"],
                "faces": sum(len(v) for v in members.values()),
                "missing": not members,
                "attr": entry.get("attr"),
            }
        return out

    @classmethod
    def select_group(cls, name: str) -> None:
        import bpy

        members = cls._member_map(name)
        if not members:
            raise ValueError(f"Group {name!r} has no members.")
        # View layer, not bpy.context.scene / view_layer — see _mesh_objects.
        vl = CoreUtils._active_view_layer()
        for obj in list(vl.objects) if vl else []:
            # on vl: a bare select_set addresses the CONTEXT's layer, which
            # windowless is the scene's default, not the window's vl is
            obj.select_set(obj.name in members, view_layer=vl)
        for obj_name, indices in members.items():
            obj = bpy.data.objects[obj_name]
            wanted = set(indices)
            for poly in obj.data.polygons:
                poly.select = poly.index in wanted
        if vl is not None:
            vl.objects.active = bpy.data.objects[next(iter(members))]

    @classmethod
    def set_default(cls, name: str, default: float) -> None:
        """Set the group's default gate weight (0-1; clamped). An un-keyed
        keyable weight prop follows the default; a keyed one is
        animation-owned and left alone."""
        value = cls._registry().set_default(name, default)
        cls._sync_unkeyed_attr(name, value)
        cls._refresh_export_if_published()

    # ------------------------------------------------------------------
    # Keyable weights (opt-in)
    # ------------------------------------------------------------------

    @classmethod
    def make_weights_keyable(cls, names=None) -> Dict[str, str]:
        """Add a keyable 0-1 custom property per group on the ``data_export``
        carrier — mirror of mayatk's ``make_weights_keyable``.

        Explicitly export-facing (this publishes the manifest, creating the
        carrier if needed). Key the props in the dope sheet / via
        :meth:`key_weight`. Note the divergence in the module doc: Blender's
        FBX exporter ships the static prop + manifest but not the curves.

        Parameters:
            names: Groups to make keyable; None = every group.

        Returns:
            ``{group_name: '<carrier>["<prop>"]'}`` for the affected groups.
        """
        registry = cls._registry()
        known = {entry["name"]: entry for entry in registry.groups()}
        if not known:
            raise ValueError("No emissive groups.")
        names = list(known) if names is None else [str(n) for n in names]
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"Unknown group(s): {unknown}.")
        obj = DataNodes.ensure_export()
        plugs = {}
        for name in names:
            attr = cls._attr_name(name)
            default = float(known[name]["default"])
            if attr not in obj:
                obj[attr] = default
            try:
                obj.id_properties_ui(attr).update(
                    min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, default=default
                )
            except (AttributeError, TypeError, KeyError):
                pass
            registry.set_attr(name, attr)
            plugs[name] = f'{obj.name}["{attr}"]'
        cls.refresh_export_metadata()
        cls.logger.info(f"Keyable weight prop(s): {sorted(plugs.values())}")
        return plugs

    @classmethod
    def remove_keyable_weights(cls, names=None) -> List[str]:
        """Delete the keyable weight props — including their fcurves — and
        clear the manifest's attr records. The groups themselves (membership,
        slots, defaults) are untouched. Mirror of mayatk's
        ``remove_keyable_weights``.

        Parameters:
            names: Groups to strip; None = every group.

        Returns:
            The group names that actually had a prop to remove.
        """
        registry = cls._registry()
        known = {entry["name"] for entry in registry.groups()}
        names = sorted(known) if names is None else [str(n) for n in names]
        removed = []
        for name in names:
            if cls._delete_weight_attr(name):
                removed.append(name)
            if name in known:
                registry.set_attr(name, None)
        cls._refresh_export_if_published()
        if removed:
            cls.logger.info(f"Removed keyable weight prop(s): {removed}")
        return removed

    @classmethod
    def key_weight(
        cls,
        name: str,
        value: Optional[float] = None,
        frame: Optional[float] = None,
        auto_keyable: bool = True,
    ) -> str:
        """Key one group's weight on its carrier prop — mirror of mayatk's
        ``key_weight``.

        Parameters:
            name: Group name.
            value: Weight to key (clamped 0-1); None = the prop's current
                value (key-current-state).
            frame: Frame to key at; None = the current frame (resolved
                window-independently via the active view layer's scene).
            auto_keyable: Make the group keyable first when it isn't yet.

        Returns:
            The keyed data path (``<carrier>["emissiveGroup_<name>"]``).
        """
        if not cls._weight_attr_exists(name):
            if not auto_keyable:
                raise ValueError(
                    f"Group {name!r} has no keyable weight; run "
                    f"make_weights_keyable([{name!r}])."
                )
            cls.make_weights_keyable([name])
        obj = DataNodes.get_export_node(create=False)
        attr = cls._attr_name(name)
        if value is not None:
            obj[attr] = max(0.0, min(1.0, float(value)))
        if frame is None:
            # View layer -> owning scene: window-independent (see _mesh_objects).
            vl = CoreUtils._active_view_layer()
            frame = vl.id_data.frame_current if vl is not None else 1
        obj.keyframe_insert(data_path=f'["{attr}"]', frame=frame)
        return f'{obj.name}["{attr}"]'

    # ------------------------------------------------------------------
    # Export curve proxies — the keyed-weight FBX transport
    # ------------------------------------------------------------------

    @classmethod
    def create_export_curve_proxies(cls) -> List:
        """Stage the keyed-weight FBX transport (the ``emissive_groups`` row of
        ``FbxUtils.STAGERS``, which every bracketed write stages and finishes).

        Blender's FBX exporter cannot ship custom-property animation, so each
        keyable group with a weight fcurve gets one transient Empty under the
        ``data_export`` carrier, named exactly the group's manifest ``attr``,
        whose ``scale.x`` is keyed with the weight curve — scale because it
        is unitless (translation would pick up the exporter's unit
        conversion; rotation its degree handling). Object transform animation
        is a channel every FBX consumer bakes natively; Unity's
        ``EmissiveGroupImporter`` rebinds the proxy's ``m_LocalScale.x``
        curve to the controller and deletes the node from the prefab.

        Strictly export-transient: marked with :attr:`PROXY_MARKER` and
        removed right after the FBX write
        (:meth:`remove_export_curve_proxies`), so a saved scene never
        carries one. Stale proxies from an interrupted export are pre-cleaned
        here and flagged by :meth:`validate`.

        Returns:
            The created proxy objects (empty when nothing is keyed).
        """

        cls.remove_export_curve_proxies()  # stale pre-clean (idempotent)
        carrier = DataNodes.get_export_node(create=False)
        if carrier is None:
            return []
        proxies = []
        for entry in cls._registry().groups():
            attr = entry.get("attr")
            if not attr:
                continue
            fc = cls._weight_fcurve(entry["name"])
            if fc is None or not len(fc.keyframe_points):
                continue
            # The shared transport (also RenderEffects'): stamped with this
            # tool's own marker for its cleanup AND the ecosystem marker the
            # GLB conversion strips on -- these proxies used to ship in the
            # GLB as stray animated children of the carrier.
            from blendertk.env_utils.fbx_utils import FbxUtils

            proxy = FbxUtils.stage_curve_proxy(attr, carrier, fc, cls.PROXY_MARKER)
            if proxy is None:
                cls.logger.warning(
                    f"Curve-proxy name {attr!r} is taken by an existing object "
                    f"— group {entry['name']!r}'s weight animation will not "
                    "ship this export."
                )
                continue
            proxies.append(proxy)
        if proxies:
            cls.logger.info(
                "Staged keyed-weight curve prox"
                + ("y" if len(proxies) == 1 else "ies")
                + ": "
                + ", ".join(o.name for o in proxies)
            )
        return proxies

    @classmethod
    def remove_export_curve_proxies(cls) -> List[str]:
        """Delete every staged curve proxy (marker-matched) and its action.

        Idempotent — safe when none exist. Doubles as
        :meth:`create_export_curve_proxies`'s stale pre-clean and the sweep
        for leftovers from an interrupted export.

        Returns:
            The removed proxy names.
        """
        import bpy

        removed = []
        for obj in [o for o in bpy.data.objects if o.get(cls.PROXY_MARKER)]:
            ad = getattr(obj, "animation_data", None)
            action = ad.action if ad is not None else None
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
            if action is not None and action.users == 0:
                bpy.data.actions.remove(action)
        if removed:
            cls.logger.debug(f"Removed curve prox(ies): {removed}")
        return removed

    @classmethod
    def compact_slots(cls) -> List[int]:
        """Reclaim retired slots. Explicit and binding-breaking — mirror of
        mayatk's ``compact_slots`` (see its docstring)."""
        reclaimed = cls._registry().compact()
        if reclaimed:
            cls.logger.warning(
                f"Reclaimed retired slot(s) {reclaimed}; re-bake and re-export "
                "— existing engine bindings for those slots are now invalid."
            )
        return reclaimed

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @classmethod
    def validate(cls) -> List[str]:
        """Non-fatal authoring warnings (empty list = clean)."""
        import bpy

        groups = cls._registry().groups()
        known = {entry["name"] for entry in groups}
        warnings = []
        face_owner: Dict[tuple, str] = {}
        for entry in groups:
            name = entry["name"]
            members = cls._member_map(name)
            if not members:
                warnings.append(f"Group {name!r} is empty.")
                continue
            overlap_reported = False
            for obj_name, indices in members.items():
                for i in indices:
                    key = (obj_name, i)
                    if key in face_owner and not overlap_reported:
                        warnings.append(
                            f"Group {name!r} overlaps {face_owner[key]!r} "
                            f"(e.g. {obj_name} face {i}); shared faces glow "
                            "when either group is on."
                        )
                        overlap_reported = True
                    face_owner.setdefault(key, name)
        # Orphan membership: attribute without a registry entry (e.g. appended).
        for obj in cls._mesh_objects():
            for attr in obj.data.attributes:
                if attr.name.startswith(cls.SET_PREFIX):
                    name = attr.name[len(cls.SET_PREFIX) :]
                    if name not in known:
                        warnings.append(
                            f"{obj.name}: attribute {attr.name!r} has no "
                            f"registry entry — re-add it with add_group({name!r}) "
                            "to assign a slot."
                        )
        # Leftover curve proxies: strictly export-transient — presence outside
        # an export means an interrupted run failed to clean up.
        for obj in bpy.data.objects:
            if obj.get(cls.PROXY_MARKER):
                warnings.append(
                    f"Leftover export curve proxy {obj.name!r} (an export was "
                    "interrupted) — remove_export_curve_proxies() clears it."
                )
        # Orphan weight props: an FBX REimport restores the carrier's keyable
        # props but not the registry (data_internal never rides the FBX).
        carrier = DataNodes.get_export_node(create=False)
        if carrier is not None:
            for key in carrier.keys():
                if (
                    key.startswith(cls.SET_PREFIX)
                    and key[len(cls.SET_PREFIX) :] not in known
                ):
                    warnings.append(
                        f"Carrier prop {key!r} has no registry entry — a stale "
                        "keyable weight (removed or imported group); re-add the "
                        f"group or remove_keyable_weights"
                        f"([{key[len(cls.SET_PREFIX) :]!r}])."
                    )
        # Foreign color attributes fight ours for Unity's single color stream.
        for obj_name in cls._member_meshes(known):
            mesh = bpy.data.objects[obj_name].data
            for cattr in mesh.color_attributes:
                if cattr.name != cls.COLOR_SET:
                    warnings.append(
                        f"{obj_name}: foreign color attribute {cattr.name!r} — "
                        "Unity imports only one color stream; vertex-color "
                        "encoding may not survive."
                    )
        for msg in warnings:
            cls.logger.warning(msg)
        return warnings

    # ------------------------------------------------------------------
    # Bakes
    # ------------------------------------------------------------------

    @classmethod
    def bake_vertex_colors(cls, force: bool = False) -> dict:
        """Bake membership into the ``emissiveGroups`` color attribute
        (``BYTE_COLOR`` / ``CORNER`` — per-corner keeps group boundaries
        hard). The whole mesh is zeroed first so re-bakes never leave stale
        membership behind. Mirror of mayatk's ``bake_vertex_colors``.

        Parameters:
            force: Proceed even when member meshes carry foreign color
                attributes (Unity imports a single color stream).

        Returns:
            The published manifest dict (vertex-color encoding).
        """
        import bpy
        import numpy as np

        registry = cls._registry()
        groups = registry.groups()
        if not groups:
            raise ValueError("No emissive groups to bake.")
        # Read each group's membership ONCE — `_member_map` walks the scene,
        # so resolving it per (mesh × group) inside the loop below rescanned
        # everything N² times.
        members = {entry["name"]: cls._member_map(entry["name"]) for entry in groups}
        mesh_names = []
        for group_map in members.values():
            for obj_name in group_map:
                if obj_name not in mesh_names:
                    mesh_names.append(obj_name)
        if not mesh_names:
            raise ValueError("No member meshes found (empty groups?).")

        foreign = {}
        for obj_name in mesh_names:
            mesh = bpy.data.objects[obj_name].data
            others = [c.name for c in mesh.color_attributes if c.name != cls.COLOR_SET]
            if others:
                foreign[obj_name] = others
        if foreign and not force:
            raise ValueError(
                f"Foreign color attribute(s) present: {foreign} — Unity imports "
                "only one color stream. Remove them, use the channels encoding, "
                "or pass force=True."
            )

        for obj_name in mesh_names:
            mesh = bpy.data.objects[obj_name].data
            cattr = mesh.color_attributes.get(cls.COLOR_SET)
            if cattr is None:
                cattr = mesh.color_attributes.new(
                    name=cls.COLOR_SET, type="BYTE_COLOR", domain="CORNER"
                )
            mesh.color_attributes.active_color = cattr
            # Build the whole per-corner buffer, then write it in one call.
            # Starting from zeros is what clears stale membership on re-bake;
            # a per-loop Python assignment loop here cost one attribute write
            # per corner of the mesh.
            colors = np.zeros((len(mesh.loops), 4), dtype=np.float32)
            for entry in groups:
                for i in members[entry["name"]].get(obj_name, []):
                    poly = mesh.polygons[i]
                    start, total = poly.loop_start, poly.loop_total
                    colors[start : start + total, entry["slot"]] = 1.0
            cattr.data.foreach_set("color", colors.ravel())
            mesh.update()

        registry.set_encoding("vertex-color")
        manifest = cls.refresh_export_metadata()
        cls.logger.info(
            f"Baked {len(groups)} group(s) into color attribute "
            f"{cls.COLOR_SET!r} on {len(mesh_names)} mesh(es)."
        )
        return json.loads(manifest)

    @classmethod
    def bake_mask(
        cls,
        output_path: Optional[str] = None,
        resolution: int = 512,
        padding_px: int = 4,
        uv_set: Optional[str] = None,
    ) -> dict:
        """Rasterize membership into an ``_EMask`` RGBA texture (channels
        encoding) via :class:`ptk.RegionMaskPacker`. Mirror of mayatk's
        ``bake_mask``.

        Parameters:
            output_path: Mask image path. Defaults to ``<file project texture
                folder>/<blend stem>_EMask.png`` (``DataNodes.scene_source_images_dir``;
                mayatk's folder for a scene in that project). Never temp: an
                unsaved file with no workspace set raises instead.
            resolution / padding_px: See :class:`ptk.RegionMaskPacker`.
            uv_set: UV layer name; defaults to each mesh's active layer.

        Returns:
            The published manifest dict (channels encoding).
        """
        import os

        import bpy

        registry = cls._registry()
        groups = registry.groups()
        if not groups:
            raise ValueError("No emissive groups to bake.")
        if output_path is None:
            from blendertk.node_utils.data_nodes import DataNodes

            folder = DataNodes.scene_source_images_dir()
            if not folder:
                raise ValueError(
                    "No project to write the mask into; save the .blend, set a "
                    "workspace, or pass output_path."
                )
            blend = bpy.data.filepath
            stem = os.path.splitext(os.path.basename(blend))[0] or "untitled"
            output_path = os.path.join(folder, f"{stem}_EMask.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        packer = ptk.RegionMaskPacker(resolution=resolution, padding_px=padding_px)
        for entry in groups:
            members = cls._member_map(entry["name"])
            tris = []
            for obj_name, indices in members.items():
                tris.extend(
                    cls._harvest_mesh_uv_triangles(
                        bpy.data.objects[obj_name], indices, uv_set
                    )
                )
            if not tris:
                cls.logger.warning(
                    f"Group {entry['name']!r} has no mapped UVs; skipped."
                )
                continue
            packer.add_group(
                entry["name"],
                tris,
                slot=entry["slot"],
                default=entry["default"],
                attr=entry.get("attr"),
            )
        packer.validate()
        manifest = packer.write(output_path, write_manifest=False)

        registry.set_encoding(
            "channels", mask=os.path.basename(output_path), uv_channel=0
        )
        published = cls.refresh_export_metadata()
        cls.logger.info(f"Baked mask: {output_path}")
        return json.loads(published) if published else manifest.to_dict()

    # ------------------------------------------------------------------
    # Scene-record crossings (``DataNodes.OWNERS``)
    # ------------------------------------------------------------------

    @classmethod
    def transfer_out(cls, ctx: "ptk.TransferContext") -> Optional[dict]:
        """The emissive registry's hand-off payload: the registry, each group's
        membership as ``{group: {object: [face index, ...]}}`` and each member
        object's face count -- the far side has no face attribute to read, and
        a face index names the same face only on a mesh whose face count
        survived the crossing.  Objects are spelled as the carrier writes them
        (``ctx.rename``) and scoped to ``ctx.objects``; ``None`` without
        groups.  Mirror of mayatk's, which reads the same payload."""
        import bpy

        registry = ptk.SceneRecords.EMISSIVE_REGISTRY.load(DataNodes)
        if not registry or not registry.get("groups"):
            return None
        spell = ctx.rename or str
        scope = None if ctx.objects is None else set(ctx.objects)
        members: Dict[str, Dict[str, List[int]]] = {}
        faces: Dict[str, int] = {}
        for name in registry["groups"]:
            by_object: Dict[str, List[int]] = {}
            for obj_name, indices in cls._member_map(name).items():
                if scope is not None and obj_name not in scope:
                    continue
                spelled = spell(obj_name)
                by_object[spelled] = sorted(int(i) for i in indices)
                faces.setdefault(spelled, len(bpy.data.objects[obj_name].data.polygons))
            if by_object:
                members[name] = by_object
        return {"registry": registry, "members": members, "faces": faces}

    @classmethod
    def transfer_in(cls, payload: dict, ctx: "ptk.TransferContext") -> None:
        """Land a received emissive-registry payload (:meth:`transfer_out`'s
        shape, from either DCC): the registry merges by its rule -- a group
        whose slot is taken here is re-slotted, noted -- and each group's faces
        join its membership on the object ``ctx.rename`` resolves.  An object
        whose face count changed in the crossing keeps no membership (noted):
        its indices would name other faces.  Mirror of mayatk's."""
        import bpy

        registry = (payload or {}).get("registry")
        if registry:
            ptk.RecordTransfer.merge_record(
                DataNodes, ptk.SceneRecords.EMISSIVE_REGISTRY, registry, ctx
            )
        counts = (payload or {}).get("faces") or {}
        known = {g["name"] for g in cls._registry().groups()}
        for name, by_object in ((payload or {}).get("members") or {}).items():
            if name not in known:
                continue  # the merge left it out (no free slot), and said so
            face_map: Dict[str, List[int]] = {}
            for spelled, indices in by_object.items():
                target = ctx.rename(spelled) if ctx.rename else spelled
                obj = bpy.data.objects.get(target) if target else None
                if obj is None or obj.type != "MESH":
                    ctx.note(
                        f"Emissive group {name!r}: {spelled!r} did not arrive; "
                        "its faces were not added."
                    )
                    continue
                count = len(obj.data.polygons)
                if counts.get(spelled) not in (None, count):
                    ctx.note(
                        f"Emissive group {name!r}: {spelled!r} has {count} faces "
                        f"here and had {counts[spelled]}; its membership was not "
                        "restored."
                    )
                    continue
                face_map[obj.name] = [int(i) for i in indices]
            if face_map:
                cls.add_group(name, face_map)

    # ------------------------------------------------------------------
    # Export carrier
    # ------------------------------------------------------------------

    @classmethod
    def export_record(cls, ctx: "ptk.ExportContext") -> Optional["ptk.Record"]:
        """The ``emissive_groups`` manifest record, or ``None`` when no group
        exists -- the ``ptk.SceneRecords.EMISSIVE_GROUPS`` producer
        (``FbxUtils.PRODUCERS``, mirror of mayatk's), read Unity-side by
        ``EmissiveGroupController``'s importer.  Pure: it reads the registry
        and never writes.

        Parameters:
            ctx: The export's decisions (unused: the manifest is a function of
                the registry alone).
        """
        manifest = cls._registry().manifest(color_set=cls.COLOR_SET)
        if manifest is None:
            return None
        return ptk.SceneRecords.EMISSIVE_GROUPS.make(manifest.to_dict())

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``emissive_groups`` channel on the ``data_export``
        carrier from the registry.

        The authoring-time publish of :meth:`export_record`, committed through
        ``FbxUtils.publish_authored`` (an export pipeline runs the producer
        itself: ``FbxUtils.PRODUCERS``; the bake / authoring paths call this).
        Clears the channel when no groups exist (no empty carrier left behind).

        Returns:
            The published JSON string, or None when cleared.
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        record = cls.export_record(ptk.ExportContext(mode=ptk.ExportContext.AUTHORING))
        FbxUtils.publish_authored({ptk.SceneRecords.EMISSIVE_GROUPS: record})
        return record.text if record is not None else None
