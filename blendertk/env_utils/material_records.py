# !/usr/bin/python
# coding=utf-8
"""The ``materials`` record's Blender producer -- mirror of mayatk's
``env_utils.material_records``.

Every material an export ships, read from the file as staged for the write
into the host-free model that states it (``ptk.MaterialFamilies``): the
statement the FBX carries (``ptk.SceneRecords.MATERIALS``), the GLB built
from that FBX applies, and unitytk shades from. Read here rather than from
the written file: Blender's FBX writer turns a Principled BSDF into a Phong
(metallic in ``ReflectionFactor``, roughness as ``((1 - r) * 10)^2``
shininess), which no reader of the file can tell from an authored one.

This module only GATHERS: a Principled BSDF's sockets in the ``pbr``
vocabulary, an Emission-only surface as ``unlit``, and the maps by logical
channel (:class:`~blendertk.mat_utils.mat_manifest.MatManifest`). What they
mean in glTF's terms is the model's. It is the one reader of the file's
materials -- the alpha mode a graph authors, Blender's unlit model, the
opacity's scalar part -- and the export's compatibility check asks it too.

``bpy`` is imported inside the methods, as the rest of blendertk does.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pythontk as ptk

logger = logging.getLogger(__name__)


class MaterialRecords(ptk.HelpMixin):
    """Blender's materials, stated for the ``materials`` record."""

    #: How ``MatUtils`` marks a cutout in the graph -- a labelled ``GREATER_THAN``
    #: Math node thresholding the alpha, whose second input is the cutoff.
    MASK_NODE_LABEL = "Mask Threshold"

    #: Shader nodes an unlit surface may route through between its Emission
    #: and the output: the shader inputs of a mix / add, or a reroute.
    _UNLIT_ROUTES = ("MIX_SHADER", "ADD_SHADER", "REROUTE")

    #: What else shades a material unlit whatever it wears, as ``(module,
    #: class, method)`` rows resolved on use (``FbxUtils._resolve_row``), as
    #: ``FbxUtils.PRODUCERS`` rows are: the method returns the materials, and a
    #: source that cannot be imported is skipped. Mirror of mayatk's, where
    #: the shadow rig ranks above this part and so is named, never imported.
    UNLIT_SOURCES: Tuple[Tuple[str, str, str], ...] = (
        ("blendertk.rig_utils.shadow_rig._shadow_rig", "ShadowRig", "plane_materials"),
    )

    # ---------------------------------------------------------------- record
    @classmethod
    def export_record(cls, ctx: "ptk.ExportContext") -> Optional["ptk.Record"]:
        """The ``materials`` record for this write, or ``None`` when no object
        wears a material -- the ``ptk.SceneRecords.MATERIALS`` producer
        (``FbxUtils.PRODUCERS``, mirror of mayatk's). Pure: it reads the
        staged file and never writes.

        Parameters:
            ctx: The export's decisions; ``ctx.scope`` (object names, each with
                its descendants) narrows the materials to those the write
                ships -- the whole file's when ``None`` -- and what a material
                carries that the record cannot state is noted on it.
        """
        objects = cls._export_objects(ctx.scope)
        if not objects:
            return None
        notes: List[str] = []
        entries = cls.entries(objects, notes=notes)
        for note in dict.fromkeys(notes):
            ctx.note(note)
        if not entries:
            return None
        # ``payload`` is the declaration's made payload already.
        return ptk.Record(
            ptk.SceneRecords.MATERIALS, ptk.MaterialFamilies.payload(entries)
        )

    @classmethod
    def entries(
        cls, objects: Iterable[Any], notes: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """The record entry of every material *objects* wear, in slot order.

        Parameters:
            objects: Blender objects.
            notes: Where a sentence about what the record cannot state goes.
        """
        from blendertk.mat_utils._mat_utils import MatUtils
        from blendertk.mat_utils.mat_manifest import MatManifest

        objects = list(objects)
        materials = MatUtils.get_mats(objects)
        # What the file shades unlit whatever it wears -- an Emission surface
        # and every projected shadow's plane (:meth:`_unlit_materials`).
        unlit = cls._unlit_materials(materials)
        found: List[Dict[str, Any]] = []
        for material in materials:
            node = cls._principled(material)
            if node is not None:
                family, values = "pbr", cls._principled_values(material, node)
            elif cls._is_unlit(material):
                family, values = "unlit", cls._emission_values(material)
            else:
                if notes is not None:
                    notes.append(
                        f"Materials: no family states {material.name} (neither a "
                        "Principled BSDF nor an Emission surface); every reader "
                        "takes the file's own material for it."
                    )
                continue
            found.append(
                ptk.MaterialFamilies.entry(
                    family,
                    material.name,
                    values,
                    # Its maps by channel, read off this material alone
                    # (``MatManifest.build`` would resolve the objects again).
                    MatManifest._process_material(material),
                    unlit=material.name in unlit,
                    notes=notes,
                )
            )
            if notes is not None and node is not None:
                notes.extend(cls._unstated(material, node))
        return found

    # ---------------------------------------------------------------- values
    @classmethod
    def _principled_values(cls, material: Any, node: Any) -> Dict[str, Any]:
        """A Principled BSDF in the ``pbr`` vocabulary: each socket's own
        value where nothing drives it, the emission strength as the shader
        applies it, the opacity's scalar part (a shadow plane's fade) with
        whether anything drives it, and the alpha mode the file authors
        (:meth:`_alpha_mode_of`: a thresholded alpha is MASK)."""

        def constant(name: str) -> Any:
            socket = node.inputs.get(name)
            if socket is None or socket.is_linked:
                return None
            value = socket.default_value
            try:
                return tuple(value)[:3]
            except TypeError:
                return float(value)

        alpha = node.inputs.get("Alpha")
        factor = cls._opacity_factor(material)
        authored = cls._alpha_mode_of(material) or {}
        return {
            "base_color": constant("Base Color"),
            "base_weight": 1.0,
            "metallic": constant("Metallic"),
            "roughness": constant("Roughness"),
            "emission_color": cls._emission_color(node),
            "emission_weight": cls._emission_strength(node),
            "opacity": 1.0 if factor is None else factor,
            "opacity_driven": bool(alpha is not None and alpha.is_linked)
            or cls._surface_mixes_transparency(material),
            "alpha_mode": authored.get("mode") or "OPAQUE",
            "alpha_cutoff": authored.get("cutoff"),
            "normal_scale": cls._normal_strength(node),
        }

    @classmethod
    def _emission_values(cls, material: Any) -> Dict[str, Any]:
        """Blender's unlit model -- an Emission surface, alone or mixed with a
        Transparent BSDF: its colour times its strength, and the mix's factor
        as the opacity."""
        from blendertk.mat_utils._mat_utils import MatUtils

        tree = MatUtils.get_node_tree(material)
        emission = next(
            (n for n in (tree.nodes if tree is not None else ()) if n.type == "EMISSION"),
            None,
        )
        color = strength = None
        if emission is not None:
            socket = emission.inputs.get("Color")
            if socket is not None and not socket.is_linked:
                color = tuple(socket.default_value)[:3]
            socket = emission.inputs.get("Strength")
            if socket is not None and not socket.is_linked:
                strength = float(socket.default_value)
        factor = cls._opacity_factor(material)
        return {
            "color": [c * (1.0 if strength is None else strength) for c in color]
            if color
            else None,
            "opacity": 1.0 if factor is None else factor,
            "opacity_driven": cls._surface_mixes_transparency(material),
        }

    @staticmethod
    def _normal_strength(node: Any) -> Optional[float]:
        """The Normal Map node's ``Strength`` behind the Principled ``Normal``
        -- glTF's normal scale -- or ``None`` when no Normal Map feeds it."""
        socket = node.inputs.get("Normal")
        if socket is None or not socket.is_linked:
            return None
        source = socket.links[0].from_node
        if source.type != "NORMAL_MAP":
            return None
        strength = source.inputs.get("Strength")
        if strength is None or strength.is_linked:
            return None
        return float(strength.default_value)

    @staticmethod
    def _unstated(material: Any, node: Any) -> List[str]:
        """What *material* renders with that the record has no field for: a
        Principled transmission (glass), which every reader then shows as its
        opacity alone."""
        socket = node.inputs.get("Transmission Weight") or node.inputs.get("Transmission")
        try:
            weight = 0.0 if socket is None or socket.is_linked else float(socket.default_value)
        except (TypeError, ValueError):
            weight = 0.0
        if weight > 0.0:
            return [
                f"Material {material.name}: its transmission is not stated (the "
                "record has no transmission); it reads as its opacity alone."
            ]
        return []

    @classmethod
    def _opacity_factor(cls, material: Any) -> Optional[float]:
        """The scalar part of *material*'s opacity -- what glTF states as
        ``baseColorFactor[3]`` -- or None at 1.0. Mirror of the Maya reader.

        The opacity is the active output's Mix Shader factor when that mixes a
        Transparent BSDF (on its first shader input) against the surface --
        the shadow rig's shape, which has no Principled BSDF on the surface --
        and the Principled ``Alpha`` otherwise. Two shapes of it carry a
        scalar: a bare constant, and a texture's alpha times a scalar through
        a Math ``MULTIPLY`` (a constant input or a Value node) -- the shadow
        rig's silhouette x its plane's ``opacity`` fade, read at the current
        frame (a keyed fade also ships as a fade track, which drives the same
        factor). A texture alone states nothing: its alpha rides the base
        colour texture -- a detached shadow plane, its fade baked into the
        PNG, whose second (inactive) output's Principled is never read. The
        FBX carries neither: a shadow plane held at 0.836 reached WebXR at
        alpha 1.0, solid black where the viewport drew it half-transparent
        (measured on the Maya side 2026-10-15).
        """
        socket = cls._transparent_mix_factor(material)
        if socket is None:
            node = cls._principled(material)
            socket = node.inputs.get("Alpha") if node is not None else None
        if socket is None:
            return None
        try:
            if not socket.is_linked:
                factor = float(socket.default_value)
            else:
                node = socket.links[0].from_node
                if node.type != "MATH" or node.operation != "MULTIPLY":
                    return None
                factor = None
                for texture_in, scalar_in in ((0, 1), (1, 0)):
                    upstream = node.inputs[texture_in]
                    if upstream.is_linked and upstream.links[
                        0
                    ].from_node.type.startswith("TEX_"):
                        factor = cls._scalar_of(node.inputs[scalar_in])
                        break
                if factor is None:
                    return None
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        factor = min(1.0, max(0.0, factor))
        return factor if factor < 1.0 - 1e-4 else None

    @classmethod
    def _transparent_mix_factor(cls, material: Any) -> Optional[Any]:
        """The ``Fac`` socket of the Mix Shader on the ACTIVE output's surface
        when its first shader input is a Transparent BSDF (``Fac`` is then the
        opacity), else None."""
        from blendertk.mat_utils._mat_utils import MatUtils

        tree = MatUtils.get_node_tree(material)
        if tree is None:
            return None
        mix = cls._surface_shader(tree)
        if mix is None:
            return None
        if mix.type != "MIX_SHADER" or not mix.inputs[1].is_linked:
            return None
        if mix.inputs[1].links[0].from_node.type != "BSDF_TRANSPARENT":
            return None
        return mix.inputs[0]

    @staticmethod
    def _scalar_of(socket: Any) -> Optional[float]:
        """A float socket's constant: its own value, or a Value node's feeding
        it; None for anything else (a texture, a graph)."""
        if not socket.is_linked:
            return float(socket.default_value)
        source = socket.links[0].from_node
        if source.type == "VALUE":
            return float(source.outputs[0].default_value)
        return None

    @classmethod
    def _alpha_mode_of(cls, material: Any) -> Optional[Dict[str, Any]]:
        """One material's glTF ``alphaMode``, or None to leave it OPAQUE.

        MASK is read from the GRAPH, not from ``blend_method``. On 4.2+ that
        property is a compatibility shim over ``surface_render_method`` and
        EEVEE Next kept only two methods: probed on 5.1.2, assigning ``CLIP``
        (or ``OPAQUE``) silently leaves ``HASHED``/``DITHERED``, and only
        ``BLEND`` survives the round trip. A cutout therefore cannot be stated
        at material level any more, which is why ``MatUtils`` thresholds the
        alpha in the node graph instead -- and that node is the durable signal.
        The property is still read first for a file authored on 4.1 or earlier,
        where ``CLIP`` does stick.
        """
        entry = cls._mask_entry(material)
        if entry:
            return entry
        if getattr(material, "blend_method", None) == "CLIP":
            entry = {"mode": "MASK"}
            cutoff = getattr(material, "alpha_threshold", None)
            if isinstance(cutoff, (int, float)):
                entry["cutoff"] = float(cutoff)
            return entry
        if (
            getattr(material, "blend_method", None) == "BLEND"
            or getattr(material, "surface_render_method", None) == "BLENDED"
        ):
            return {"mode": "BLEND"}
        if cls._surface_mixes_transparency(material):
            return {"mode": "BLEND"}
        node = cls._principled(material)
        if node is not None:
            alpha = node.inputs.get("Alpha")
            if alpha is not None:
                try:
                    if alpha.is_linked or float(alpha.default_value) < 1.0:
                        return {"mode": "BLEND"}
                except (TypeError, ValueError):
                    pass
        return None

    @classmethod
    def _mask_entry(cls, material: Any) -> Optional[Dict[str, Any]]:
        """MASK + cutoff when a threshold node drives the Principled alpha."""
        node = cls._principled(material)
        alpha = node.inputs.get("Alpha") if node is not None else None
        if alpha is None or not alpha.is_linked:
            return None
        source = alpha.links[0].from_node
        if source.type != "MATH" or source.operation != "GREATER_THAN":
            return None
        if (source.label or "") != cls.MASK_NODE_LABEL:
            return None
        entry: Dict[str, Any] = {"mode": "MASK"}
        try:
            entry["cutoff"] = float(source.inputs[1].default_value)
        except (AttributeError, IndexError, TypeError, ValueError):
            pass  # glTF's default cutoff (0.5) then applies
        return entry

    @staticmethod
    def _surface_mixes_transparency(material: Any, limit: int = 64) -> bool:
        """Does a Transparent BSDF reach the surface output through the graph?

        The shadow rigs build exactly this -- a Mix Shader blending a
        Transparent BSDF against the shaded result -- and set no Principled
        alpha at all, so the socket check alone would miss them. Walked from
        the ACTIVE output backwards and bounded, because a node tree may hold
        several outputs and disconnected experiments, and a group cycle must
        not hang an export.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        tree = MatUtils.get_node_tree(material)
        if tree is None:
            return False
        first = MaterialRecords._surface_shader(tree)
        if first is None:
            return False
        seen, queue = set(), [first]
        while queue and len(seen) < limit:
            node = queue.pop()
            if node is None or node.name in seen:
                continue
            seen.add(node.name)
            if node.type == "BSDF_TRANSPARENT":
                return True
            for socket in node.inputs:
                for link in socket.links:
                    queue.append(link.from_node)
        return False

    @staticmethod
    def _emission_strength(node) -> float:
        """The Principled BSDF's emission strength, or 1.0 when driven/absent.

        It defaults to **0** on a fresh Principled BSDF -- the same trap as
        Maya's separate emission weight -- so reading the colour without it
        reports emission on a material that renders black.
        """
        socket = node.inputs.get("Emission Strength")
        if socket is None or socket.is_linked:
            return 1.0
        return float(socket.default_value)

    @staticmethod
    def _emission_color(node) -> Optional[List[float]]:
        """The Principled BSDF's constant emission colour, or ``None``.

        A socket driven by a texture has a meaningless ``default_value``, so a
        linked socket yields nothing here -- the map is carried separately.
        """
        socket = node.inputs.get("Emission Color") or node.inputs.get("Emission")
        if socket is None or socket.is_linked:
            return None
        return list(socket.default_value)[:3]

    # ----------------------------------------------------------------- scene
    @classmethod
    def _unlit_materials(cls, materials: Iterable[Any]) -> Set[str]:
        """The names of the *materials* the file shades unlit whatever they
        wear.

        Mirror of the Maya reader. Two kinds: Blender's own unlit model
        (:meth:`_is_unlit` -- an Emission shader, alone or mixed with a
        Transparent BSDF, on the active output), and the material of every
        projected shadow plane (:attr:`UNLIT_SOURCES`:
        ``ShadowRig.plane_materials``, whose material objects are matched by
        name), whatever it wears -- a shadow is darkness laid over what is
        under it, never a surface the environment lights. The FBX cannot say
        either: FBX2glTF makes each one lit PBR, and a shadow plane in WebXR
        took the environment's specular and read BRIGHTER than the floor it
        lay on (measured on the Maya side 2026-10-15). Stated as the record's
        ``unlit``, which a GLB applies as ``KHR_materials_unlit``
        (``MeshConvert.apply_glb_materials``).
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        materials = list(materials)
        unlit = {material.name for material in materials if cls._is_unlit(material)}
        wanted = {material.name for material in materials}
        for row in cls.UNLIT_SOURCES:
            resolved = FbxUtils._resolve_row("Unlit source", *row)
            if not resolved or resolved[0] is None:
                continue
            try:
                unlit.update(m.name for m in resolved[0]() if m.name in wanted)
            except Exception as error:  # noqa: BLE001 -- the record states the rest
                logger.debug("Unlit source %s unread: %s", row[1], error)
        return unlit

    @classmethod
    def _is_unlit(cls, material: Any, limit: int = 64) -> bool:
        """Is *material* Blender's unlit model: Emission, with no other BSDF?

        Walked from the ACTIVE output's Surface back through mix / add shaders
        (their SHADER inputs only -- a mix factor is data, a texture's alpha):
        an Emission shader must be reached, and nothing but a Transparent BSDF
        beside it -- what the shadow rigs and a detached shadow plane build.
        Any other shader (a Principled BSDF, a group) is lit, or unknown, and
        leaves the material out. Bounded, as :meth:`_surface_mixes_transparency`.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        tree = MatUtils.get_node_tree(material)
        if tree is None:
            return False
        first = cls._surface_shader(tree)
        if first is None:
            return False
        emits = False
        seen, queue = set(), [first]
        while queue:
            node = queue.pop()
            if node is None or node.name in seen:
                continue
            seen.add(node.name)
            if len(seen) > limit:
                return False
            if node.type == "EMISSION":
                emits = True
            elif node.type in cls._UNLIT_ROUTES:
                for socket in node.inputs:
                    if node.type == "REROUTE" or socket.type == "SHADER":
                        queue.extend(link.from_node for link in socket.links)
            elif node.type != "BSDF_TRANSPARENT":
                return False
        return emits

    @staticmethod
    def _principled(material):
        """The material's Principled BSDF node, or ``None``."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        return _MatUtilsInternal._principled_node(material)

    @staticmethod
    def _surface_shader(tree: Any) -> Optional[Any]:
        """The node feeding the ACTIVE material output's Surface (the first
        output when none is flagged active), or None when nothing does. A tree
        may hold several outputs -- a detached shadow plane's keeps an inactive
        one for the exporters -- and only the active one renders
        (``MatUtils``' own read, ``_tree_surface_node``)."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        return _MatUtilsInternal._tree_surface_node(tree)

    @staticmethod
    def _export_objects(scope: Optional[Iterable[str]]) -> List[Any]:
        """*scope*'s objects (names, each with its descendants), else every
        object in the file."""
        import bpy

        if scope is None:
            return list(bpy.data.objects)
        found: Dict[str, Any] = {}
        for name in scope:
            root = bpy.data.objects.get(str(name).split("|")[-1])
            if root is None:
                continue
            for obj in (root, *root.children_recursive):
                found.setdefault(obj.name, obj)
        return list(found.values())
