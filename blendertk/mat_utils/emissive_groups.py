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
- Registry / manifest: the same ``emissive_groups`` JSON channels on the
  ``data_internal`` / ``data_export`` carriers (``node_utils.data_nodes``
  mirror). Blender's FBX exporter ships the export carrier's custom
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
    DATA_CHANNEL = "emissive_groups"
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
            load=lambda: DataNodes.get_internal_string(cls.DATA_CHANNEL),
            save=lambda text: DataNodes.set_internal_string(cls.DATA_CHANNEL, text),
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
        if DataNodes.get_export_string(cls.DATA_CHANNEL) is not None:
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
        for obj in vl.objects if vl else []:
            obj.select_set(obj.name in members)
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
        """Stage the keyed-weight FBX transport (called by the Scene Exporter).

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
        import bpy

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
            if bpy.data.objects.get(attr) is not None:
                cls.logger.warning(
                    f"Curve-proxy name {attr!r} is taken by an existing object "
                    f"— group {entry['name']!r}'s weight animation will not "
                    "ship this export."
                )
                continue
            proxy = bpy.data.objects.new(attr, None)  # None data -> Empty
            proxy[cls.PROXY_MARKER] = True
            proxy.parent = carrier
            collections = list(carrier.users_collection)
            if not collections:
                vl = CoreUtils._active_view_layer()
                if vl is not None:
                    collections = [vl.id_data.collection]
            for coll in collections:
                coll.objects.link(proxy)
            # Key scale.x per authored keyframe (keyframe_insert rather than
            # action.fcurves.new — slot-aware across Blender 4.4+/5.x), then
            # copy each key's interpolation from the source curve.
            for kp in sorted(fc.keyframe_points, key=lambda k: k.co[0]):
                frame, value = kp.co
                proxy.scale[0] = value
                proxy.keyframe_insert(data_path="scale", index=0, frame=frame)
            from blendertk.anim_utils._anim_utils import AnimUtils

            dst = next(
                (
                    f
                    for f in AnimUtils.get_fcurves([proxy])
                    if f.data_path == "scale" and f.array_index == 0
                ),
                None,
            )
            if dst is not None:
                # Keyed on the frame rounded to 1e-4, not to a whole number:
                # sub-frame keys (0.4 / 0.6) would otherwise collide onto one
                # entry and one of them would inherit the wrong interpolation.
                interp = {
                    round(kp.co[0], 4): kp.interpolation for kp in fc.keyframe_points
                }
                for kp in dst.keyframe_points:
                    kp.interpolation = interp.get(round(kp.co[0], 4), kp.interpolation)
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
                    name = attr.name[len(cls.SET_PREFIX):]
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
                    and key[len(cls.SET_PREFIX):] not in known
                ):
                    warnings.append(
                        f"Carrier prop {key!r} has no registry entry — a stale "
                        "keyable weight (removed or imported group); re-add the "
                        f"group or remove_keyable_weights"
                        f"([{key[len(cls.SET_PREFIX):]!r}])."
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
            output_path: Mask image path. Defaults to ``<blend dir>/
                <blend stem>_EMask.png`` (falls back to the temp dir for an
                unsaved file).
            resolution / padding_px: See :class:`ptk.RegionMaskPacker`.
            uv_set: UV layer name; defaults to each mesh's active layer.

        Returns:
            The published manifest dict (channels encoding).
        """
        import os
        import tempfile

        import bpy

        registry = cls._registry()
        groups = registry.groups()
        if not groups:
            raise ValueError("No emissive groups to bake.")
        if output_path is None:
            blend = bpy.data.filepath
            root = os.path.dirname(blend) if blend else tempfile.gettempdir()
            stem = (
                os.path.splitext(os.path.basename(blend))[0] if blend else "untitled"
            )
            output_path = os.path.join(root, f"{stem}_EMask.png")
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
        manifest = packer.write(output_path)

        registry.set_encoding(
            "channels",
            mask=os.path.basename(output_path),
            resolution=int(resolution),
            uv_channel=0,
        )
        published = cls.refresh_export_metadata()
        cls.logger.info(f"Baked mask: {output_path}")
        return json.loads(published) if published else manifest.to_dict()

    # ------------------------------------------------------------------
    # Export carrier
    # ------------------------------------------------------------------

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``emissive_groups`` channel on the ``data_export``
        carrier from the registry — mirror of mayatk's
        ``refresh_export_metadata`` (there wired into
        ``FbxUtils._KNOWN_PRODUCERS``; here called by the bake/authoring
        paths and the scene exporter's carrier refresh). Clears the channel
        when no groups exist.

        Returns:
            The published JSON string, or None when cleared.
        """
        manifest = cls._registry().manifest(color_set=cls.COLOR_SET)
        if manifest is None:
            DataNodes.set_export_string(cls.DATA_CHANNEL, "")
            return None
        payload = manifest.to_json()
        DataNodes.set_export_string(cls.DATA_CHANNEL, payload)
        return payload


class EmissiveGroupsSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the ``emissive_groups.ui`` panel.

    Composition over inheritance: a thin driver over :class:`EmissiveGroups`
    — no authoring or bake logic lives here. The table lists groups in slot
    order; the **Weight** column is scrub- and click-editable (Maya
    channel-box idiom) and writes each group's default gate weight. **Bake**
    (``tb000``) runs the encoding chosen in its option box: *Vertex Color*
    (rides the FBX) or *Mask Texture* (an ``_EMask`` image for sub-face
    emissive detail).
    """

    COLUMNS = ("Group", "Slot", "Weight", "Faces")
    WEIGHT_COL = 2
    #: pixels of horizontal scrub per full 0-1 weight sweep
    SCRUB_PX_PER_UNIT = 150.0

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.emissive_groups
        self._updating = False
        self._scrub_start = None
        self._scrub_value = None

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Compact Retired Slots",
            setObjectName="compact_slots",
            setToolTip=self.sb.tooltip.fmt(
                title="Compact Retired Slots",
                body="Reclaim the channel slots left behind by removed groups.",
                notes=[
                    "Breaks any existing engine binding against those slots — "
                    "re-bake and re-export afterward.",
                ],
            ),
        )
        widget.menu.add(
            "QPushButton",
            setText="Republish Export Data",
            setObjectName="republish_export",
            setToolTip=self.sb.tooltip.fmt(
                title="Republish Export Data",
                body="Rewrite the <code>emissive_groups</code> channel on the "
                "<code>data_export</code> node from the current registry.",
                notes=["Runs automatically before every FBX export."],
            ),
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Emissive Groups",
                body=(
                    "Author named face groups whose emissive regions the "
                    "game engine can toggle or dim independently, sharing "
                    "one all-on emissive map."
                ),
                steps=[
                    "Select faces (or meshes), name the group, press Add.",
                    "Repeat per independently-controlled region (max 4).",
                    "Set each group's default Weight in the table.",
                    "Bake (option box picks Vertex Color or Mask Texture).",
                    "Export FBX as usual — the manifest rides along.",
                ],
                sections=[
                    (
                        "Encodings",
                        [
                            "<b>Vertex Color</b> — rides the FBX; claims "
                            "the mesh's single engine color stream.",
                            "<b>Mask Texture</b> — an _EMask image; use it "
                            "for emissive detail painted inside a face.",
                        ],
                    )
                ],
                notes=[
                    "Regions in no group keep glowing as baked — only "
                    "group what you intend to control.",
                    "Topology edits can shift face membership; re-run "
                    "Validate after modeling changes.",
                    "Baked-GI bounce light ignores runtime toggles.",
                    "Table menu &gt; Make Weights Keyable adds keyable "
                    "0-1 weights on the data_export carrier. Keyed curves "
                    "drive the Unity controller from both DCCs (Maya: FBX "
                    "custom-property curves; Blender: Scene Exporter "
                    "curve proxies).",
                ],
            )
        )

    def txt000_init(self, widget) -> None:
        """Group-name field — clearable back to the auto-derived name."""
        widget.option_box.clear_option = True

    def tbl000_init(self, widget) -> None:
        """Table setup: one-time construction, then (re)wire signals and populate.

        The signal wiring runs unconditionally because the ``tbl000`` QWidget
        can outlive this slots instance — a reload builds a NEW slots object
        on the SAME persisted widget, which already carries
        ``is_initialized``; without the re-wire the handlers stay bound to
        the orphaned instance and silently no-op. The context-menu ITEMS
        stay in the one-time block since they mutate the persisting widget
        (building them twice duplicates the entries).
        """
        if not widget.is_initialized:
            widget.is_initialized = True
            widget.refresh_on_show = True
            widget.setColumnCount(len(self.COLUMNS))
            widget.setHorizontalHeaderLabels(list(self.COLUMNS))

            QHeaderView = self.sb.QtWidgets.QHeaderView
            header = widget.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            for col in (1, self.WEIGHT_COL, 3):
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

            widget.setSelectionBehavior(
                self.sb.QtWidgets.QAbstractItemView.SelectRows
            )
            widget.setSelectionMode(
                self.sb.QtWidgets.QAbstractItemView.SingleSelection
            )
            # Weight edits like a Maya channel-box field: MMB-drag to scrub,
            # single click to type.
            widget.set_scrub_columns([self.WEIGHT_COL])
            widget.set_single_click_edit_columns([self.WEIGHT_COL])

            widget.menu.add("Separator", setTitle="Group")
            widget.menu.add(
                "QPushButton",
                setText="Select Members",
                setObjectName="select_members",
                setToolTip=self.sb.tooltip.fmt(
                title="Select Members",
                body="Select the faces belonging to the highlighted group.",
            ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Remove Group",
                setObjectName="remove_group",
                setToolTip=self.sb.tooltip.fmt(
                    title="Remove Group",
                    body="Delete the highlighted group.",
                    notes=[
                        "Its channel slot is <b>retired</b>, never reused, so every "
                        "existing engine binding stays valid.",
                    ],
                ),
            )
            widget.menu.add("Separator", setTitle="Weights")
            widget.menu.add(
                "QPushButton",
                setText="All On",
                setObjectName="weights_all_on",
                setToolTip=self.sb.tooltip.fmt(
                title="All On",
                body="Set every group's default weight to <b>1</b>.",
            ),
            )
            widget.menu.add(
                "QPushButton",
                setText="All Off",
                setObjectName="weights_all_off",
                setToolTip=self.sb.tooltip.fmt(
                title="All Off",
                body="Set every group's default weight to <b>0</b>.",
            ),
            )
            widget.menu.add("Separator", setTitle="Keyable")
            widget.menu.add(
                "QPushButton",
                setText="Make Weights Keyable",
                setObjectName="make_weights_keyable",
                setToolTip=self.sb.tooltip.fmt(
                    title="Make Weights Keyable",
                    body="Add one keyable 0–1 weight per group on the "
                    "<code>data_export</code> carrier, then publish the manifest.",
                    sections=[
                        (
                            "How the curves travel",
                            [
                                "<b>Maya</b> — shipped natively as FBX "
                                "custom-property curves.",
                                "<b>Blender</b> — shipped by the Scene Exporter as "
                                "transient curve proxies.",
                            ],
                        )
                    ],
                    notes=[
                        "This is the one authoring action that creates the export "
                        "carrier — it is opt-in for that reason.",
                    ],
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Key Weights @ Current Frame",
                setObjectName="key_weights",
                setToolTip=self.sb.tooltip.fmt(
                    title="Key Weights @ Current Frame",
                    body="Set a key on every keyable group's weight, at its current "
                    "value and the current frame.",
                ),
            )
            widget.menu.add(
                "QPushButton",
                setText="Remove Keyable Weights",
                setObjectName="remove_keyable_weights",
                setToolTip=self.sb.tooltip.fmt(
                    title="Remove Keyable Weights",
                    body="Delete the keyable weight attributes, <b>including any "
                    "animation on them</b>.",
                    notes=["Groups, slots and default weights are left untouched."],
                ),
            )

        self._wire_table_signals(widget)
        self._refresh_table()

    #: Table signal -> handler name. Bound in :meth:`_wire_table_signals`.
    _TABLE_SIGNALS = {
        "cellChanged": "_on_cell_changed",
        "cellScrubStarted": "_on_scrub_started",
        "cellScrubMoved": "_on_scrub_moved",
        "cellScrubFinished": "_on_scrub_finished",
    }

    def _wire_table_signals(self, widget) -> None:
        """Bind table signals to THIS instance, replacing only OUR bindings.

        Idempotent (see :meth:`tbl000_init`), and deliberately NOT a blanket
        ``signal.disconnect()``: that would also tear out the widget's own
        internal connections — uitk's ``TableWidget`` wires ``cellChanged``
        to its ``_on_cell_edited`` in its constructor — and PySide warns
        ("Failed to disconnect (None) from signal ...") whenever a signal
        has nothing attached. The previous binding is tracked on the widget
        instead and disconnected by reference; holding that reference also
        keeps the receiving slots instance alive for as long as the widget
        points at it.
        """
        previous = getattr(widget, "_emissive_group_handlers", {})
        handlers = {
            name: getattr(self, attr) for name, attr in self._TABLE_SIGNALS.items()
        }
        for name, handler in handlers.items():
            signal = getattr(widget, name)
            prior = previous.get(name)
            if prior is not None:
                try:
                    signal.disconnect(prior)
                except (TypeError, RuntimeError):
                    pass  # already gone (widget or receiver torn down)
            signal.connect(handler)
        widget._emissive_group_handlers = handlers

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        table = self.ui.tbl000
        self._updating = True
        try:
            groups = EmissiveGroups.list_groups()
            table.setRowCount(len(groups))
            QtWidgets = self.sb.QtWidgets
            Qt = self.sb.QtCore.Qt
            flags_ro = Qt.ItemIsSelectable | Qt.ItemIsEnabled
            for row, (name, data) in enumerate(groups.items()):
                label = f"{name} (missing)" if data["missing"] else name
                values = (
                    label,
                    str(data["slot"]),
                    f"{data['default']:g}",
                    str(data["faces"]),
                )
                for col, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    if col != self.WEIGHT_COL:
                        item.setFlags(flags_ro)
                    if col in (1, self.WEIGHT_COL, 3):
                        item.setTextAlignment(Qt.AlignCenter)
                    table.setItem(row, col, item)
        finally:
            self._updating = False

    def _group_at(self, row: int) -> Optional[str]:
        item = self.ui.tbl000.item(row, 0)
        return item.text().replace(" (missing)", "") if item else None

    def _selected_group(self) -> Optional[str]:
        row = self.ui.tbl000.currentRow()
        return self._group_at(row) if row >= 0 else None

    def _set_weight(self, row: int, weight: float) -> None:
        name = self._group_at(row)
        if not name:
            return
        EmissiveGroups.set_default(name, weight)
        self._refresh_table()

    def _on_cell_changed(self, row, col) -> None:
        if self._updating or col != self.WEIGHT_COL:
            return
        try:
            weight = float(self.ui.tbl000.item(row, col).text())
        except (TypeError, ValueError):
            self._refresh_table()  # revert an unparseable entry
            return
        self._set_weight(row, weight)

    # Scrub-edit (MMB drag over the Weight cell) --------------------------
    #
    # A drag emits a move per mouse event, so the scene write is deferred to
    # release: the moves only repaint the cell (each ``set_default`` writes
    # the registry node AND republishes the export manifest — doing that per
    # pixel would spam the undo queue and stall the drag).

    def _on_scrub_started(self, row, col) -> None:
        name = self._group_at(row)
        groups = EmissiveGroups.list_groups()
        self._scrub_start = groups[name]["default"] if name in groups else None
        self._scrub_value = None

    def _on_scrub_moved(self, row, col, dx, dy) -> None:
        if self._scrub_start is None:
            return
        weight = self._scrub_start + (dx / self.SCRUB_PX_PER_UNIT)
        self._scrub_value = max(0.0, min(1.0, weight))
        item = self.ui.tbl000.item(row, self.WEIGHT_COL)
        if item is None:
            return
        self._updating = True  # preview only — don't round-trip through the engine
        try:
            item.setText(f"{self._scrub_value:g}")
        finally:
            self._updating = False

    def _on_scrub_finished(self, row, col) -> None:
        if self._scrub_start is not None and self._scrub_value is not None:
            self._set_weight(row, self._scrub_value)
        self._scrub_start = None
        self._scrub_value = None

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def b000(self) -> None:
        """Add (or extend) a group from the selection."""
        name = self.ui.txt000.text().strip()
        if not name:
            name = f"group_{len(EmissiveGroups.list_groups())}"
        try:
            EmissiveGroups.add_group(name)
        except ValueError as error:
            self.sb.message_box(str(error))
            return
        self.ui.txt000.clear()
        self._refresh_table()

    def b001(self) -> None:
        """Remove the selected group (retires its slot)."""
        self.remove_group()

    def b002(self) -> None:
        """Select the group's member faces."""
        self.select_members()

    def b003(self) -> None:
        """Validate authoring state."""
        warnings = EmissiveGroups.validate()
        self.sb.message_box(
            "<br>".join(warnings) if warnings else "Emissive groups: clean."
        )
        self._refresh_table()

    # Bake (option box) ----------------------------------------------------

    def tb000_init(self, widget) -> None:
        """Initialize Bake."""
        widget.option_box.menu.setTitle("Bake")
        # Qt class name + addItems on the returned widget — passing a uitk
        # class name here silently yields a QLabel.
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb000",
            setToolTip=self.sb.tooltip.fmt(
                title="Encoding",
                body="How group membership is carried to the engine.",
                bullets=[
                    "<b>Vertex Color</b> — membership rides the FBX in a color "
                    "set. No textures, but it claims the mesh's single engine "
                    "color stream.",
                    "<b>Mask Texture</b> — membership is rasterized into an "
                    "<code>_EMask</code> image. Use it for emissive detail "
                    "painted inside a face.",
                ],
                notes=[
                    "Either way a group occupies one RGBA channel, so a model "
                    "carries at most 4 of them.",
                ],
            ),
        )
        cmb.addItems(["Vertex Color", "Mask Texture"])
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Resolution: ",
            setObjectName="s000",
            set_limits=[64, 8192],
            setValue=512,
            setToolTip=self.sb.tooltip.fmt(
                title="Resolution",
                body="Pixel size of the baked <code>_EMask</code> image.",
                notes=[
                    "Masks are chunky — 512 usually suffices, independent of "
                    "the emissive map's own resolution.",
                    "<b>Mask Texture</b> encoding only.",
                ],
            ),
        )
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Padding: ",
            setObjectName="s001",
            set_limits=[0, 64],
            setValue=4,
            setToolTip=self.sb.tooltip.fmt(
                title="Padding",
                body="Edge padding, in pixels, bled outside each region.",
                notes=[
                    "Keep it at or above the emissive bake's own padding, or "
                    "seams darken under mipping.",
                    "<b>Mask Texture</b> encoding only.",
                ],
            ),
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Force Over Foreign Color Set",
            setObjectName="chk000",
            setToolTip=self.sb.tooltip.fmt(
                title="Force Over Foreign Color Set",
                body="Bake even when a mesh already carries an unrelated "
                "color set.",
                notes=[
                    "The engine imports a single color stream, so the groups may "
                    "not survive the import.",
                    "<b>Vertex Color</b> encoding only.",
                ],
            ),
        )

    def tb000(self, widget) -> None:
        """Bake membership and publish the export manifest."""
        menu = widget.option_box.menu
        try:
            if menu.cmb000.currentIndex() == 0:
                EmissiveGroups.bake_vertex_colors(force=menu.chk000.isChecked())
                message = "Vertex colors baked and manifest published."
            else:
                manifest = EmissiveGroups.bake_mask(
                    resolution=menu.s000.value(), padding_px=menu.s001.value()
                )
                message = f"Mask baked: {manifest.get('mask', '')}"
        except ValueError as error:
            self.sb.message_box(str(error))
            return
        self.sb.message_box(message)

    # Table context menu ---------------------------------------------------

    def select_members(self) -> None:
        name = self._selected_group()
        if not name:
            self.sb.message_box("Select a group row first.")
            return
        try:
            EmissiveGroups.select_group(name)
        except ValueError as error:
            self.sb.message_box(str(error))

    def remove_group(self) -> None:
        name = self._selected_group()
        if not name:
            self.sb.message_box("Select a group row first.")
            return
        EmissiveGroups.remove_group(name)
        self._refresh_table()

    def weights_all_on(self) -> None:
        self._set_all_weights(1.0)

    def weights_all_off(self) -> None:
        self._set_all_weights(0.0)

    def _set_all_weights(self, weight: float) -> None:
        for name in EmissiveGroups.list_groups():
            EmissiveGroups.set_default(name, weight)
        self._refresh_table()

    def make_weights_keyable(self) -> None:
        try:
            plugs = EmissiveGroups.make_weights_keyable()
        except ValueError as error:
            self.sb.message_box(str(error))
            return
        self.sb.message_box(
            f"Keyable weight(s) added for {len(plugs)} group(s) on the "
            "data_export carrier; manifest published."
        )

    def key_weights(self) -> None:
        keyable = [
            name
            for name, data in EmissiveGroups.list_groups().items()
            if data.get("attr")
        ]
        if not keyable:
            self.sb.message_box(
                "No keyable weights — run Make Weights Keyable first."
            )
            return
        for name in keyable:
            EmissiveGroups.key_weight(name)
        self.sb.message_box(f"Keyed {len(keyable)} weight(s) at the current frame.")

    def remove_keyable_weights(self) -> None:
        removed = EmissiveGroups.remove_keyable_weights()
        self.sb.message_box(
            f"Removed keyable weight(s): {', '.join(removed)}."
            if removed
            else "No keyable weights."
        )

    # Header menu ----------------------------------------------------------

    def compact_slots(self) -> None:
        reclaimed = EmissiveGroups.compact_slots()
        self.sb.message_box(
            f"Reclaimed slot(s): {reclaimed}. Re-bake and re-export."
            if reclaimed
            else "No retired slots."
        )
        self._refresh_table()

    def republish_export(self) -> None:
        payload = EmissiveGroups.refresh_export_metadata()
        self.sb.message_box(
            "Manifest republished." if payload else "No groups; carrier cleared."
        )


if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("emissive_groups", reload=True)
    ui.show(pos="screen", app_exec=True)
