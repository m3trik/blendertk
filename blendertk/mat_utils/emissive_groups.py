# !/usr/bin/python
# coding=utf-8
"""Emissive Groups' Blender half (mirror of mayatk's): a group's faces wear a
copy of their material made for the group (``Material.copy``: its node tree
copied, its images shared), its emission scalar keyed by the group's weight. A
copy is tagged with custom properties on its node tree: ``emissiveGroup`` (the
group), ``emissiveGroupBase`` (the base material, an ID pointer, so a rename
keeps the link) and ``emissiveGroupStrength`` (the base's emission strength it
was keyed against) -- on the tree, not the material, because Blender's FBX
exporter writes a material's custom properties into the file, an ID pointer as
its ``repr`` (a memory address: measured 2026-11-07, the export baseline
differed run to run), and never a node tree's.
``EmissiveGroups`` is the tool's API in Blender: pythontk's host-free
``EmissiveGroupsModel`` over this half; its panel is tentacle's.

The emission scalar is the Principled BSDF's ``Emission Strength``; its keys
live in the copy's node tree's own action. Refused by name: a material with
none (a Diffuse BSDF alone has no emission in glTF either; an Emission shader
delivers unlit, its glow its base colour, so a weight on it would play
nowhere -- as Maya refuses a surfaceShader), a strength a link drives (keying
its value would change nothing), a strength of 0 and a black emission colour. Blender's FBX exporter ships no material animation, so an export
stages each animated copy's weight on a ``<material>__emission`` Empty under
the ``data_export`` carrier (``FbxUtils.stage_curve_proxy``), removed after
the write.

A tint multiplies the Principled BSDF's unlinked ``Emission Color``: the copy
keeps the base's colour (``emissiveGroupColour`` on its tree) and its colour
channels are that colour times the tint, keyed per channel in its tree's
action; ``emissiveGroupStates`` holds its named states as JSON. A colour with
a black channel is refused a tint: no tint lights it.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

import json
from typing import Any, Dict, List, Optional, Sequence

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.node_utils.data_nodes import DataNodes

FaceMap = Dict[str, List[int]]


class _BlenderEmissiveGroupsInternal:
    """Implementation-detail base for :class:`BlenderEmissiveGroups`."""

    TAG_GROUP = "emissiveGroup"
    TAG_BASE = "emissiveGroupBase"
    TAG_STRENGTH = "emissiveGroupStrength"
    #: A tinted copy's base emission colour: what its tint multiplies.
    TAG_COLOUR = "emissiveGroupColour"
    #: A copy's named states, ``{name: {"tint", "weight"}}`` as JSON.
    TAG_STATES = "emissiveGroupStates"
    #: Marks the weight proxies this tool stages (beside the transport's own
    #: marker), so its cleanup takes only its own.
    PROXY_MARKER = "emissiveGroupProxy"
    #: Material settings a copy keeps in step with its base, beside its nodes.
    SETTINGS = ("surface_render_method", "blend_method", "use_backface_culling", "alpha_threshold")
    #: Node properties that are the editor's (where a node sits, how it is
    #: drawn), never the material's: left out of what a copy keeps in step.
    EDITOR_PROPS = frozenset(
        {
            "name", "label", "location", "width", "width_hidden", "height",
            "dimensions", "select", "show_options", "show_preview",
            "show_texture", "hide", "color", "use_custom_color", "parent",
            "location_absolute", "warning_propagation", "is_active_output",
        }
    )

    @classmethod
    def _node_settings(cls, node) -> Dict[str, Any]:
        """*node*'s own settings, generically: every editable plain property
        (an image node's interpolation and extension, a math node's
        operation, ``mute``) and a colour ramp's stops. A base whose ramp or
        interpolation changed read as in step when only sockets, images and
        links were compared."""
        out: Dict[str, Any] = {}
        for prop in node.bl_rna.properties:
            key = prop.identifier
            if key in cls.EDITOR_PROPS or key.startswith("bl_") or prop.is_readonly:
                continue
            if prop.type not in ("BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"):
                continue
            value = getattr(node, key, None)
            if isinstance(value, set):
                value = sorted(value)
            elif hasattr(value, "__len__") and not isinstance(value, str):
                value = [float(v) for v in value]
            out[key] = value
        ramp = getattr(node, "color_ramp", None)
        if ramp is not None:
            out["color_ramp"] = [ramp.interpolation, ramp.color_mode] + [
                [float(e.position)] + [float(c) for c in e.color] for e in ramp.elements
            ]
        return out

    @staticmethod
    def _mesh_objects(editable: bool = False):
        """Every mesh object in the file: one in an excluded collection or
        another scene still wears its materials (counted on the view layer
        alone, a copy only it wore was deleted from under it). *editable*:
        only those whose object and mesh no library holds."""
        import bpy

        return [
            o
            for o in bpy.data.objects
            if o.type == "MESH"
            and not (editable and (o.library is not None or o.data.library is not None))
        ]

    @staticmethod
    def _carrier():
        return DataNodes.get_export_node(create=False)

    @staticmethod
    def _mat(name: str):
        import bpy

        return bpy.data.materials.get(str(name))

    @staticmethod
    def _material_indices(obj) -> "list":
        """Each polygon's material slot index (edit-mode edits flushed first)."""
        import numpy as np

        if obj.mode == "EDIT":
            obj.update_from_editmode()
        values = np.empty(len(obj.data.polygons), dtype=np.int32)
        obj.data.polygons.foreach_get("material_index", values)
        return values

    @staticmethod
    def _set_indices(obj, indices, slot: int) -> None:
        """Faces *indices* of *obj* take material slot *slot* (edit mode too)."""
        if obj.mode == "EDIT":
            import bmesh

            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            for i in indices:
                bm.faces[i].material_index = slot
            bmesh.update_edit_mesh(obj.data)
            return
        polygons = obj.data.polygons
        for i in indices:
            polygons[i].material_index = slot
        obj.data.update()

    @staticmethod
    def _surface(mat):
        """``(node, strength socket, colour socket)`` of *mat*'s emission: its
        Principled BSDF's; ``None`` for none. An Emission surface is none: the
        delivery states it unlit (``_MatUtilsInternal._is_unlit``, the
        ``materials`` record's ``unlit`` family: its glow as the base colour,
        which glTF's unlit model shades alone), so an emission weight keyed
        on it plays nowhere -- refused by name, as mayatk refuses a
        surfaceShader."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        tree = getattr(mat, "node_tree", None) if mat is not None else None
        if tree is None or _MatUtilsInternal._is_unlit(mat):
            return None
        node = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if node is not None and node.inputs.get("Emission Strength") is not None:
            return node, node.inputs["Emission Strength"], node.inputs.get("Emission Color")
        return None

    @classmethod
    def _socket(cls, material):
        found = cls._surface(cls._mat(material))
        return found[1] if found else None

    @staticmethod
    def _path(socket) -> str:
        return f"{socket.path_from_id()}.default_value"

    @classmethod
    def _fcurve(cls, material: str):
        """The fcurve keying *material*'s emission scalar, or ``None``: its node
        tree's, read slot-aware and legacy-safe (``CoreUtils.animating_fcurves``)."""
        mat = cls._mat(material)
        socket = cls._socket(material)
        tree = getattr(mat, "node_tree", None)
        if socket is None or tree is None:
            return None
        path = cls._path(socket)
        return next((f for f in CoreUtils.animating_fcurves(tree) if f.data_path == path), None)

    @classmethod
    def _colour_socket(cls, material: str):
        """*material*'s Principled ``Emission Color`` socket, or ``None``."""
        found = cls._surface(cls._mat(material))
        return found[2] if found else None

    @classmethod
    def _tint_fcurves(cls, material: str) -> List[Any]:
        """The fcurves keying *material*'s emission colour, r, g and b
        (``None`` for a channel not keyed); empty when it has no colour."""
        mat = cls._mat(material)
        socket = cls._colour_socket(material)
        tree = getattr(mat, "node_tree", None)
        if socket is None or tree is None:
            return []
        path = cls._path(socket)
        found = {f.array_index: f for f in CoreUtils.animating_fcurves(tree) if f.data_path == path}
        return [found.get(c) for c in range(3)]

    @classmethod
    def _tint_reference(cls, material: str) -> List[float]:
        """What a tint multiplies: a tinted copy's stored base colour, else
        (a base, a copy never tinted) its own emission colour now."""
        tree = cls._mat(material).node_tree
        if cls.TAG_COLOUR in tree:
            return [float(c) for c in tree[cls.TAG_COLOUR]][:3]
        return [float(c) for c in list(cls._colour_socket(material).default_value)[:3]]

    @staticmethod
    def _over(values: Sequence[float], reference: Sequence[float]) -> List[float]:
        """*values* over *reference* per channel; a channel the reference
        leaves black shows no tint, and reads 1."""
        return [float(v) / r if r > 1e-9 else 1.0 for v, r in zip(values, reference)]

    @classmethod
    def _ensure_tint_reference(cls, material: str) -> None:
        """Keep *material*'s colour as what its tint multiplies, once."""
        tree = cls._mat(material).node_tree
        if cls.TAG_COLOUR not in tree:
            tree[cls.TAG_COLOUR] = cls._tint_reference(material)

    @staticmethod
    def _step_key(fc, frame: float) -> None:
        """Hold *fc*'s key at *frame* until the next (a state's switch)."""
        for point in fc.keyframe_points if fc is not None else ():
            if abs(point.co[0] - frame) < 1e-4:
                point.interpolation = "CONSTANT"
        if fc is not None:
            fc.update()

    @classmethod
    def _remove_fcurve(cls, material: str, fc) -> None:
        """Remove *fc* from *material*'s node tree's action (the kernel's
        ``CoreUtils.remove_animating_fcurve``)."""
        CoreUtils.remove_animating_fcurve(cls._mat(material).node_tree, fc)

    @classmethod
    def _fresh_copy(cls, base, name: str):
        """A copy of *base* named *name* whose node tree keys nothing of its
        base's: a copied tree shares its base's action, so keying the copy
        would key the base."""
        copy = base.copy()
        copy.name = name
        tree = getattr(copy, "node_tree", None)
        for key in (cls.TAG_GROUP, cls.TAG_BASE, cls.TAG_STRENGTH, cls.TAG_COLOUR, cls.TAG_STATES):
            if tree is not None and key in tree:
                del tree[key]
        ad = getattr(tree, "animation_data", None)
        if ad is not None and ad.action is not None:
            ad.action = ad.action.copy()
            fc = cls._fcurve(copy.name)
            if fc is not None:
                cls._remove_fcurve(copy.name, fc)
            # A keyed base colour stays its base's: the copy's colour channels
            # are its tint's to key, at the value they show now.
            socket = cls._colour_socket(copy.name)
            for fc in [f for f in cls._tint_fcurves(copy.name) if f is not None]:
                value = list(socket.default_value)
                cls._remove_fcurve(copy.name, fc)
                socket.default_value = value
        return copy

    @classmethod
    def _key_points(cls, src_fc, material: str, factor: float) -> None:
        """Write *src_fc*'s keys (times *factor*, shape kept) on *material*'s emission."""
        socket = cls._socket(material)
        for kp in sorted(src_fc.keyframe_points, key=lambda k: k.co[0]):
            socket.default_value = kp.co[1] * factor
            socket.keyframe_insert("default_value", frame=kp.co[0])
        dst = cls._fcurve(material)
        by_frame = {round(k.co[0], 4): k for k in src_fc.keyframe_points}
        for k in dst.keyframe_points if dst is not None else ():
            src = by_frame.get(round(k.co[0], 4))
            if src is None:
                continue
            k.interpolation = src.interpolation
            k.handle_left_type, k.handle_right_type = src.handle_left_type, src.handle_right_type
            k.handle_left = (src.handle_left[0], src.handle_left[1] * factor)
            k.handle_right = (src.handle_right[0], src.handle_right[1] * factor)
        if dst is not None:
            dst.update()

    @staticmethod
    def _scene_frame() -> float:
        layer = CoreUtils.active_view_layer()  # its scene, window-independently
        return float(layer.id_data.frame_current) if layer is not None else 1.0


class BlenderEmissiveGroups(_BlenderEmissiveGroupsInternal, ptk.PureEmissiveGroupsHost):
    """The Blender scene operations of the Emissive Groups model
    (``ptk.EmissiveGroupsHost``)."""

    data_nodes = DataNodes

    # Faces and materials -------------------------------------------------

    def faces(self, faces=None) -> FaceMap:
        import bpy

        if faces is None:  # the selection; an empty map is no faces, as in Maya
            out: FaceMap = {}
            for obj in CoreUtils.selected_objects():
                if obj.type != "MESH":
                    continue
                if obj.mode == "EDIT":
                    obj.update_from_editmode()
                    selected = [p.index for p in obj.data.polygons if p.select]
                else:
                    # A selected object is all its faces: the polygons' select
                    # flags are what an earlier edit left (Maya takes the mesh).
                    selected = list(range(len(obj.data.polygons)))
                if selected:
                    out[obj.name] = selected
            return out
        out = {}
        for name, indices in dict(faces).items():
            obj = bpy.data.objects.get(str(getattr(name, "name", name)))
            if obj is None or obj.type != "MESH":
                continue
            idx = [int(i) for i in indices]
            out[obj.name] = idx or list(range(len(obj.data.polygons)))
        return {k: v for k, v in out.items() if v}

    def face_materials(self, face_map: FaceMap) -> Dict[str, FaceMap]:
        import bpy

        out: Dict[str, FaceMap] = {}
        for name, indices in face_map.items():
            obj = bpy.data.objects[name]
            slots = [s.material for s in obj.material_slots]
            per_face = self._material_indices(obj)
            for i in indices:
                slot = int(per_face[i])
                mat = slots[slot] if 0 <= slot < len(slots) else None
                if mat is not None:
                    out.setdefault(mat.name, {}).setdefault(obj.name, []).append(int(i))
        return out

    def material_faces(self, material: str) -> FaceMap:
        import numpy as np

        out: FaceMap = {}
        for obj in self._mesh_objects():
            slots = [i for i, s in enumerate(obj.material_slots) if s.material and s.material.name == material]
            if not slots:
                continue
            hit = np.flatnonzero(np.isin(self._material_indices(obj), slots))
            if hit.size:
                out[obj.name] = hit.tolist()
        return out

    def face_count(self, face_map: FaceMap) -> int:
        """Faces by mesh: linked duplicates show one mesh's faces twice."""
        import bpy

        seen = set()
        for name, indices in face_map.items():
            obj = bpy.data.objects.get(name)
            mesh = obj.data.name_full if obj is not None else name
            seen.update((mesh, int(i)) for i in indices)
        return len(seen)

    def material(self, name: str) -> Optional[str]:
        mat = self._mat(name) if name else None
        return mat.name if mat is not None else None

    def assign(self, material: str, face_map: FaceMap) -> None:
        import bpy

        mat = self._mat(material)
        for name, indices in face_map.items():
            obj = bpy.data.objects[name]
            slot = next((i for i, s in enumerate(obj.material_slots) if s.material == mat), None)
            if slot is None:
                obj.data.materials.append(mat)
                slot = len(obj.material_slots) - 1
            self._set_indices(obj, indices, slot)

    def select(self, face_map: FaceMap) -> None:
        import bpy

        layer = CoreUtils.active_view_layer()
        for obj in list(layer.objects) if layer else []:
            # On the layer: a bare select_set addresses the context's layer,
            # which windowless is the scene's default, not the window's.
            obj.select_set(obj.name in face_map, view_layer=layer)
        for name, indices in face_map.items():
            wanted = set(indices)
            obj = bpy.data.objects[name]
            if obj.mode == "EDIT":
                # The edit-mesh is what Edit Mode shows; the mesh's flags are
                # overwritten from it.
                import bmesh

                bm = bmesh.from_edit_mesh(obj.data)
                # Every face off first: turning one off after a wanted
                # neighbour takes their shared vertices with it.
                for face in bm.faces:
                    face.select_set(False)
                for face in bm.faces:
                    if face.index in wanted:
                        face.select_set(True)
                bm.select_flush_mode()
                bmesh.update_edit_mesh(obj.data)
                continue
            mesh = obj.data
            verts = {v for p in mesh.polygons if p.index in wanted for v in p.vertices}
            for poly in mesh.polygons:
                poly.select = poly.index in wanted
            for edge in mesh.edges:
                edge.select = edge.vertices[0] in verts and edge.vertices[1] in verts
            for vert in mesh.vertices:
                vert.select = vert.index in verts
        if layer is not None:
            # The first of them the layer holds: one in another scene or an
            # excluded collection wears a group, but is no object of this layer.
            held = (layer.objects.get(name) for name in face_map)
            layer.objects.active = next((obj for obj in held if obj is not None), None)

    # Copies --------------------------------------------------------------

    def copies(self) -> Dict[str, Dict[str, Any]]:
        try:
            import bpy
        except ImportError:
            return {}  # without Blender: a panel builds before any scene is there to read
        out = {}
        for mat in bpy.data.materials:
            tags = getattr(mat, "node_tree", None)
            if tags is None or self.TAG_GROUP not in tags:
                continue
            base = tags.get(self.TAG_BASE)
            out[mat.name] = {
                "group": str(tags[self.TAG_GROUP]),
                "base": getattr(base, "name", None),
                "strength": float(tags.get(self.TAG_STRENGTH, 0.0)),
            }
        return out

    def make_copy(self, base: str, name: str) -> str:
        return self._fresh_copy(self._mat(base), name).name

    def tag(self, copy: str, group: str, base: str, strength: float) -> None:
        tags = self._mat(copy).node_tree  # never the material: see the module doc
        tags[self.TAG_GROUP] = str(group)
        tags[self.TAG_BASE] = self._mat(base)
        tags[self.TAG_STRENGTH] = float(strength)

    def delete_material(self, material: str) -> None:
        import bpy

        mat = self._mat(material)
        if mat is None:
            return
        for obj in self._mesh_objects(editable=True):
            for index in reversed([i for i, s in enumerate(obj.material_slots) if s.material == mat]):
                if obj.mode == "EDIT":
                    obj.update_from_editmode()
                if not (self._material_indices(obj) == index).any():
                    # Mesh.materials.pop re-indexes the faces above the slot.
                    obj.data.materials.pop(index=index)
        bpy.data.materials.remove(mat)

    def material_state(self, material: str) -> Dict[str, Any]:
        mat = self._mat(material)
        state: Dict[str, Any] = {}
        for attr in self.SETTINGS:
            if hasattr(mat, attr):
                state[attr] = getattr(mat, attr)
        tree = getattr(mat, "node_tree", None)
        if tree is None:
            return state
        surface = self._surface(mat)
        skip = {surface[1].path_from_id()} if surface else set()
        colour = surface[2] if surface else None
        if colour is not None and not colour.is_linked:
            # The colour a tint multiplies: a tinted copy's own is its base's
            # colour times its tint, never a value out of step.
            state["emission colour"] = self._tint_reference(material)
            skip.add(colour.path_from_id())
        for node in tree.nodes:
            state[f"{node.name}:type"] = node.bl_idname
            image = getattr(node, "image", None)
            if image is not None:
                state[f"{node.name}:image"] = image.name
            for key, value in self._node_settings(node).items():
                state[f"{node.name}:{key}"] = value
            for socket in node.inputs:
                if socket.is_linked or not hasattr(socket, "default_value"):
                    continue
                if socket.path_from_id() in skip:
                    continue
                value = socket.default_value
                state[f"{node.name}.{socket.identifier}"] = (
                    [float(v) for v in value] if hasattr(value, "__len__") else value
                )
        for link in tree.links:
            state[f"link:{link.from_node.name}.{link.from_socket.identifier}>{link.to_node.name}.{link.to_socket.identifier}"] = True
        return state

    def sync_copy(self, copy: str, base: str) -> None:
        old = self._mat(copy)
        info = self.copies().get(copy) or {}
        # Its own: the weight, the states, and a tint -- kept, re-keyed
        # against the base's colour now.
        tinted = self.TAG_COLOUR in old.node_tree
        tint_keys, tint = self.emission_tint_keys(copy), self.emission_tint(copy)
        states = self.emission_states(copy)
        fresh = self._fresh_copy(self._mat(base), f"{copy}.sync")
        name = old.name
        self.copy_emission(copy, fresh.name, 1.0)
        old.user_remap(fresh)
        import bpy

        bpy.data.materials.remove(old)
        fresh.name = name
        self.tag(fresh.name, info.get("group", ""), base, info.get("strength", 0.0))
        if states:
            self.set_emission_states(fresh.name, states)
        if tinted and self._colour_socket(fresh.name) is not None:
            for frame, value, stepped in tint_keys:
                self.key_emission_tint(fresh.name, value, frame, stepped)
            if not tint_keys:
                self.set_emission_tint(fresh.name, tint)

    # Emission ------------------------------------------------------------

    def emission(self, material: str) -> Optional[float]:
        socket = self._socket(material)
        if socket is None or socket.is_linked:
            return None  # none, or a strength a link drives: nothing to key
        return float(socket.default_value)

    def emission_color(self, material: str) -> Optional[List[float]]:
        surface = self._surface(self._mat(material))
        colour = surface[2] if surface else None
        if colour is None or colour.is_linked:
            return None
        return self._tint_reference(material)  # a tinted copy's: what its tint multiplies

    def set_emission(self, material: str, value: float) -> None:
        socket = self._socket(material)
        if socket is not None:
            socket.default_value = float(value)

    def emission_channel(self, material: str):
        """What keys *material*'s emission scalar: its fcurve (mayatk's twin
        names its plug), or ``None`` when it is not keyed."""
        return self._fcurve(material)

    def emission_keys(self, material: str):
        fc = self._fcurve(material)
        return [(float(k.co[0]), float(k.co[1])) for k in fc.keyframe_points] if fc else []

    def key_emission(
        self, material: str, value: float, frame: Optional[float] = None, stepped: bool = False
    ) -> None:
        socket = self._socket(material)
        frame = self._scene_frame() if frame is None else float(frame)
        socket.default_value = float(value)
        socket.keyframe_insert("default_value", frame=frame)
        if stepped:
            self._step_key(self._fcurve(material), frame)

    def stepped_emission_frames(self, material: str) -> List[float]:
        fc = self._fcurve(material)
        return [float(k.co[0]) for k in fc.keyframe_points if k.interpolation == "CONSTANT"] if fc else []

    def clear_emission_keys(self, material: str) -> None:
        fc = self._fcurve(material)
        if fc is None:
            return
        socket = self._socket(material)
        value = socket.default_value
        self._remove_fcurve(material, fc)
        socket.default_value = value

    def copy_emission(self, src: str, dst: str, factor: float) -> None:
        fc = self._fcurve(src)
        self.clear_emission_keys(dst)
        if fc is not None and len(fc.keyframe_points):
            self._key_points(fc, dst, factor)
        else:
            self.set_emission(dst, self.emission(src) * factor)

    def scale_emission(self, material: str, factor: float) -> None:
        fc = self._fcurve(material)
        if fc is None:
            self.set_emission(material, self.emission(material) * factor)
            return
        for k in fc.keyframe_points:
            k.co[1] *= factor
            k.handle_left[1] *= factor
            k.handle_right[1] *= factor
        fc.update()

    # Tint ----------------------------------------------------------------

    def tint_refusal(self, material: str) -> Optional[str]:
        mat = self._mat(material)
        if mat is None:
            return f"{material!r} is in no scene"
        socket = self._colour_socket(material)
        if socket is None:
            return "it has no Principled BSDF emission colour to tint"
        if socket.is_linked:
            return "its emission colour is a map (a link), which no tint multiplies here"
        if min(self._tint_reference(material)) <= 0.0:
            # A tint multiplies it: a black channel shows no tint, and the tint
            # it was given could never be read back.
            return (
                "its emission colour has a black channel, which no tint can light "
                "(white shows each tint as named)"
            )
        return None

    def emission_tint(self, material: str) -> List[float]:
        socket = self._colour_socket(material) if self._mat(material) is not None else None
        if socket is None or socket.is_linked:
            return [1.0, 1.0, 1.0]
        return self._over(list(socket.default_value)[:3], self._tint_reference(material))

    def emission_tint_channels(self, material: str) -> List[Any]:
        """What keys each channel of *material*'s tint: its colour's fcurves
        (``None`` for one not keyed; mayatk's twin names its plugs), in the
        colour's units -- over :meth:`emission_color`, the tint."""
        return self._tint_fcurves(material)

    def set_emission_tint(self, material: str, tint: Sequence[float]) -> None:
        self._ensure_tint_reference(material)
        socket = self._colour_socket(material)
        value = list(socket.default_value)
        for c, (ref, t) in enumerate(zip(self._tint_reference(material), tint)):
            value[c] = ref * float(t)
        socket.default_value = value

    def key_emission_tint(
        self, material: str, tint: Sequence[float], frame: Optional[float] = None, stepped: bool = False
    ) -> None:
        self._ensure_tint_reference(material)
        frame = self._scene_frame() if frame is None else float(frame)
        socket = self._colour_socket(material)
        self.set_emission_tint(material, tint)
        for c in range(3):
            socket.keyframe_insert("default_value", index=c, frame=frame)
        if stepped:
            for fc in self._tint_fcurves(material):
                self._step_key(fc, frame)

    def emission_tint_keys(self, material: str):
        curves = self._tint_fcurves(material) if self._mat(material) is not None else []
        keyed = [fc for fc in curves if fc is not None and len(fc.keyframe_points)]
        if not keyed:
            return []
        socket = self._colour_socket(material)
        reference = self._tint_reference(material)
        frames = sorted({round(float(k.co[0]), 4) for fc in keyed for k in fc.keyframe_points})
        out = []
        for frame in frames:
            values = [
                fc.evaluate(frame) if fc is not None and len(fc.keyframe_points) else socket.default_value[c]
                for c, fc in enumerate(curves)
            ]
            point = next(
                (k for fc in keyed for k in fc.keyframe_points if abs(k.co[0] - frame) < 1e-4), None
            )
            stepped = point is not None and point.interpolation == "CONSTANT"
            out.append((frame, self._over(values, reference), stepped))
        return out

    def clear_emission_tint_keys(self, material: str) -> None:
        curves = [fc for fc in self._tint_fcurves(material) if fc is not None]
        if not curves:
            return
        socket = self._colour_socket(material)
        value = list(socket.default_value)
        for fc in curves:
            self._remove_fcurve(material, fc)
        socket.default_value = value

    def emission_states(self, material: str) -> Dict[str, Dict[str, Any]]:
        mat = self._mat(material)
        tree = getattr(mat, "node_tree", None) if mat is not None else None
        if tree is None or self.TAG_STATES not in tree:
            return {}
        try:
            found = json.loads(str(tree[self.TAG_STATES]) or "{}")
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}

    def set_emission_states(self, material: str, states: Dict[str, Dict[str, Any]]) -> None:
        self._mat(material).node_tree[self.TAG_STATES] = json.dumps(states)

    # Export --------------------------------------------------------------

    def stage_emission_proxy(self, material: str, name: str, factor: float, tint: Optional[int] = None):
        from blendertk.env_utils.fbx_utils import FbxUtils

        if tint is None:
            fc = self._fcurve(material)
        else:  # the tint's channel: its colour channel over the base's
            fc = self._tint_fcurves(material)[tint]
            factor = factor / self._tint_reference(material)[tint]
        if fc is None:
            return None
        try:
            carrier = DataNodes.ensure_export()
        except DataNodes.WriteRefused:
            # No scene carrier to hang the proxy under: the publish says why.
            return None
        return FbxUtils.stage_curve_proxy(
            name, carrier, fc, self.PROXY_MARKER, value=lambda v: v * factor
        )

    def emission_proxies(self) -> List[str]:
        import bpy

        return [o.name for o in bpy.data.objects if o.get(self.PROXY_MARKER)]

    def remove_emission_proxies(self, names=None) -> List[str]:
        import bpy

        from blendertk.env_utils.fbx_utils import FbxUtils

        wanted = None if names is None else set(names)
        doomed = [
            o
            for o in bpy.data.objects
            if o.get(self.PROXY_MARKER) and (wanted is None or o.name in wanted)
        ]
        # Forgotten first: the writer ships the staged nodes, and one gone
        # from under it would drop the rest of its stager's.
        FbxUtils.unstage(doomed)
        removed = []
        for obj in doomed:
            data = getattr(obj, "animation_data", None)
            action = data.action if data is not None else None
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
            if action is not None and action.users == 0:
                bpy.data.actions.remove(action)
        return removed

    def undo_step(self, label: str):
        return CoreUtils.undo_chunk(label)

    # The old tool's data -------------------------------------------------

    def legacy_store_names(self, prefix: str) -> List[str]:
        names = []
        for obj in self._mesh_objects():
            for attr in obj.data.attributes:
                name = attr.name[len(prefix):]
                if attr.name.startswith(prefix) and name not in names:
                    names.append(name)
        return names

    def legacy_members(self, store: str) -> Optional[FaceMap]:
        import numpy as np

        out: Optional[FaceMap] = None
        for obj in self._mesh_objects():
            attr = obj.data.attributes.get(store)
            if attr is None:
                continue
            out = out if out is not None else {}
            values = np.empty(len(attr.data), dtype=bool)
            attr.data.foreach_get("value", values)
            indices = np.flatnonzero(values)
            if indices.size:
                out[obj.name] = indices.tolist()
        return out

    def adopt_legacy_weight(self, attr: str, copy: str, factor: float) -> bool:
        carrier = self._carrier()
        if carrier is None or attr not in carrier:
            return False
        path = f'["{attr}"]'
        fc = next((f for f in CoreUtils.animating_fcurves(carrier) if f.data_path == path), None)
        if fc is None or not len(fc.keyframe_points):
            return False
        self.clear_emission_keys(copy)
        self._key_points(fc, copy, factor)
        return True

    def remove_legacy(self, stores: List[str], attrs: List[str], color_set: str) -> List[str]:
        removed = []
        for obj in self._mesh_objects(editable=True):
            for store in stores:
                attr = obj.data.attributes.get(store)
                if attr is not None:
                    obj.data.attributes.remove(attr)
                    removed.append(f"{obj.name}: {store}")
            colours = obj.data.color_attributes.get(color_set)
            if colours is not None:
                obj.data.color_attributes.remove(colours)
                removed.append(f"{obj.name}: {color_set}")
        carrier = self._carrier()
        for attr in attrs:
            if carrier is None or attr not in carrier:
                continue
            path = f'["{attr}"]'
            fc = next((f for f in CoreUtils.animating_fcurves(carrier) if f.data_path == path), None)
            if fc is not None:
                try:
                    CoreUtils.remove_animating_fcurve(carrier, fc)
                except (RuntimeError, ReferenceError, ValueError):
                    pass
            del carrier[attr]
            removed.append(f'{carrier.name}["{attr}"]')
        return removed

    def extra_warnings(self) -> List[str]:
        """Leftover weight proxies: export-transient, so one outside an export
        means an interrupted run failed to clean up."""
        import bpy

        return [
            f"Leftover export curve proxy {obj.name!r} (an export was "
            "interrupted) -- remove_export_proxies() clears it."
            for obj in bpy.data.objects
            if obj.get(self.PROXY_MARKER)
        ]


class EmissiveGroups(ptk.EmissiveGroupsModel):
    """The Emissive Groups API in Blender: pythontk's model over the Blender half."""

    HOST = BlenderEmissiveGroups()
