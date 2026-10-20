# !/usr/bin/python
# coding=utf-8
"""Read named sections of live-scene state for transport.

Mirror of mayatk's ``env_utils.scene_state.SceneState``
(``btk.SceneState`` <-> ``mtk.SceneState``): the Blender *reader column* of
the scene-data grid. Every section of scene state that FBX translation drops
is read here, once, and handed to whichever carrier the caller is filling
(the WebXR preview's in-process envelope, the Scene Exporter's GLB
conversion, embedded in the GLB's ``extras``). The matching *applier column*
is :attr:`pythontk.MeshConvert.SIDECAR_APPLIERS`, and the envelope wire
format is :meth:`pythontk.MeshConvert.build_scene_sidecar` -- a new kind of
extended setup is one reader here (mirrored in mayatk) plus one applier row
there.

Boundary with ``btk.DataNodes``: a section belongs here only when it
*repairs FBX translation loss*, derived read-only from the live scene.
Tool-authored semantic metadata ships **inside** the FBX via the
``data_export`` carrier and must never be duplicated into a sidecar section
-- see the boundary section in mayatk's ``docs/data_nodes.md`` (the SSoT for
the shared system).

``bpy`` is never imported at module scope, so the package surface still
resolves without a running Blender.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class SceneState:
    """Section-registry reader of scene state the FBX cannot express.

    >>> sections = btk.SceneState.read(bpy.context.selected_objects)
    >>> envelope = ptk.MeshConvert.build_scene_sidecar(
    ...     sections, source=btk.SceneState.source()
    ... )
    """

    #: Section -> the classmethod that reads it. The extension point: a new
    #: section is one row here plus its reader, with the same
    #: ``(materials, textures)`` signature.
    READERS: Dict[str, str] = {
        "base_color": "_read_base_color",
        "emissive": "_read_emissive",
        "metallic_roughness": "_read_metallic_roughness",
        "alpha_mode": "_read_alpha_mode",
        "unlit": "_read_unlit",
    }

    @staticmethod
    def source() -> Dict[str, str]:
        """This host's identity for the envelope's ``source`` key."""
        import bpy

        return {"application": "blender", "version": bpy.app.version_string}

    @classmethod
    def read(
        cls,
        objects: List[Any],
        include_textures: bool = True,
        sections: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Scene state the FBX cannot express, one key per requested section.

        The material list and the texture manifest are resolved **once** and
        shared by every reader -- ``MatManifest.build`` walks every assigned
        node tree, and paying for that per section is pure waste on
        operations that are meant to feel immediate.

        Parameters:
            objects: Objects whose materials define the set (the closed
                export set, typically -- a group Empty ships its descendants,
                and their materials must travel with them).
            include_textures: Mirrors an export's ``EMBED_TEXTURES``: off
                promises a fast, flat-material result, and the sidecar must
                not smuggle maps back in as data URIs after the FBX skipped
                them -- the readers then carry constants only.
            sections: Subset of :attr:`READERS` to read; ``None`` reads all.

        Returns:
            ``{section: data}`` -- a section that finds nothing is omitted.
        """
        from blendertk.mat_utils._mat_utils import MatUtils
        from blendertk.mat_utils.mat_manifest import MatManifest

        materials = MatUtils.get_mats(objects) or []
        textures = (
            (MatManifest.build(objects).get("materials", {}) or {})
            if include_textures
            else {}
        )

        result: Dict[str, Any] = {}
        for section, reader in cls.READERS.items():
            if sections is not None and section not in sections:
                continue
            data = getattr(cls, reader)(materials, textures)
            if data:
                result[section] = data
        return result

    @classmethod
    def _read_base_color(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"color": (r, g, b)}}`` for constant base colours.

        Mirror of the Maya reader, and needed for the same reason there: a
        preview whose surfaces are all flat white leaves emissive nothing to
        read against. Only the *constant* is carried -- a texture-driven base
        colour already travels through the FBX as a real image, so re-embedding
        it would only bloat the payload. (*textures* is accepted for the
        uniform reader signature; this section has no use for it.)

        Its opacity's scalar part rides as ``alpha`` (:meth:`_opacity_factor`),
        the factor glTF multiplies into the texture's alpha.
        """
        result: Dict[str, Dict[str, Any]] = {}
        for material in materials:
            entry: Dict[str, Any] = {}
            node = cls._principled(material)
            socket = node.inputs.get("Base Color") if node is not None else None
            if socket is not None and not socket.is_linked:
                entry["color"] = list(socket.default_value)[:3]
            alpha = cls._opacity_factor(material)
            if alpha is not None:
                entry["alpha"] = alpha
            if entry:
                result[material.name] = entry
        return result

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
    def _surface_shader(tree: Any) -> Optional[Any]:
        """The node feeding the ACTIVE material output's Surface (the first
        output when none is flagged active), or None when nothing does. A tree
        may hold several outputs -- a detached shadow plane's keeps an inactive
        one for the exporters -- and only the active one renders
        (``MatUtils``' own read, ``_tree_surface_node``)."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        return _MatUtilsInternal._tree_surface_node(tree)

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
    def _read_alpha_mode(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"mode": "MASK"|"BLEND", "cutoff": float}}`` for *materials*.

        The FBX cannot say which glTF ``alphaMode`` a material wants, and
        FBX2glTF decides from the base colour's alpha channel alone, so a
        transparent material reaches WebXR OPAQUE with its silhouette embedded
        and renders as a solid square. Measured on the Maya side 2026-09-02;
        the Blender side ships two producers down the same route -- the shadow
        rig's plane and the Horizon rig's -- and the plane's fade is exactly
        what a viewer without the shim falls back to.

        A material with nothing to say is OMITTED, which leaves the converter's
        ``OPAQUE`` in place. Getting that boundary right is the whole job here,
        because Blender's defaults are not neutral: a brand-new material
        already reads ``blend_method='HASHED'`` and
        ``surface_render_method='DITHERED'`` (probed on 5.1.2), so "not
        OPAQUE" would mark every material in the scene transparent. Only
        deliberate authoring counts, and both of this package's own producers
        are covered: ``MatUtils`` sets ``CLIP`` for a cutout (with
        ``alpha_threshold`` as the cutoff) and wires the Principled ``Alpha``,
        while the shadow rigs mix a Transparent BSDF into the surface output.
        """
        result: Dict[str, Dict[str, Any]] = {}
        for material in materials:
            entry = cls._alpha_mode_of(material)
            if entry:
                result[material.name] = entry
        return result

    #: How ``MatUtils`` marks a cutout in the graph -- a labelled ``GREATER_THAN``
    #: Math node thresholding the alpha, whose second input is the cutoff.
    MASK_NODE_LABEL = "Mask Threshold"

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
        first = SceneState._surface_shader(tree)
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

    @classmethod
    def _read_unlit(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {}}`` for the *materials* the scene shades unlit.

        Mirror of the Maya reader. Two kinds: Blender's own unlit model
        (:meth:`_is_unlit` -- an Emission shader, alone or mixed with a
        Transparent BSDF, on the active output), and the material of every
        projected shadow plane (``ShadowRig.plane_materials``), whatever it
        wears -- a shadow is darkness laid over what is under it, never a
        surface the environment lights. The FBX cannot say either: FBX2glTF
        makes each one lit PBR, and a shadow plane in WebXR took the
        environment's specular and read BRIGHTER than the floor it lay on
        (measured on the Maya side 2026-10-15). Applied as
        ``KHR_materials_unlit`` (``MeshConvert.set_glb_unlit``).
        """
        result: Dict[str, Dict[str, Any]] = {}
        for material in materials:
            if cls._is_unlit(material):
                result[material.name] = {}
        try:
            from blendertk.rig_utils.shadow_rig._shadow_rig import ShadowRig

            wanted = {m.name for m in materials}
            for material in ShadowRig.plane_materials():
                if material.name in wanted:
                    result[material.name] = {}
        except Exception as error:  # noqa: BLE001 -- a section never fails a push
            import logging

            logging.getLogger(__name__).debug("Shadow planes unread: %s", error)
        return result

    #: Shader nodes an unlit surface may route through between its Emission
    #: and the output: the shader inputs of a mix / add, or a reroute.
    _UNLIT_ROUTES = ("MIX_SHADER", "ADD_SHADER", "REROUTE")

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

    @classmethod
    def _read_emissive(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"color": (r, g, b), "texture": path}}`` for *materials*.

        *textures* comes from :class:`MatManifest`, which already owns the
        ``Emission Color`` / ``Emission`` socket rename across Blender
        versions; the constant beside it is read from the same socket.
        Materials with no emission are omitted, so an unlit scene contributes
        no section (the envelope still ships, with empty sections -- the
        "requested, nothing to carry" signal the panel summary reads).

        *materials* is enumerated by the caller rather than derived from
        *textures*, because the manifest drops any material whose slot dict
        comes back empty -- i.e. every colour-only emissive, the common case.
        Measured: iterating the manifest found nothing for a Principled BSDF
        with an emission colour and no emission map.
        """
        result: Dict[str, Dict[str, Any]] = {}

        for material in materials:
            node = cls._principled(material)
            if node is None:
                continue

            strength = cls._emission_strength(node)
            texture = (textures.get(material.name) or {}).get("emission")
            # Strength 0: the material emits nothing. With a map connected that
            # is still something to carry -- the FBX hands the map on and the
            # converter glows it at 1.0 unless the factor says 0. Mirror of the
            # Maya twin (measured there: a production room's ceiling panels lit
            # WebXR at full, 2026-10-17). A colour-only one is left out.
            if strength == 0.0:
                if texture:
                    result[material.name] = {
                        "texture": texture,
                        "color": [0.0, 0.0, 0.0],
                    }
                continue

            entry: Dict[str, Any] = {}
            if texture:
                entry["texture"] = texture
                # The map supplies the colour; only the scale is left to carry.
                if strength != 1.0:
                    entry["color"] = [strength, strength, strength]
            else:
                color = cls._emission_color(node)
                if color and any(c > 0.0 for c in color):
                    entry["color"] = [c * strength for c in color]

            if entry:
                result[material.name] = entry
        return result

    @classmethod
    def _read_metallic_roughness(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"metallic": path, "roughness": path}}`` for *materials*.

        Mirror of the Maya reader, for the same failure measured there: the
        FBX->glTF chain packs a solid-white ORM when it cannot resolve the real
        maps, which renders the material metallic=1 -- zero diffuse, so a baked
        lightmap (a diffuse-only term) lights nothing and a lightmapped viewer
        shows black. Texture paths only: the scalar sockets survive the FBX as
        factors, so carrying them would re-assert values that already arrived.
        """
        result: Dict[str, Dict[str, Any]] = {}
        for material in materials:
            if cls._principled(material) is None:
                continue
            slots = textures.get(material.name) or {}
            entry = {
                key: slots[key] for key in ("metallic", "roughness") if slots.get(key)
            }
            if entry:
                result[material.name] = entry
        return result

    @staticmethod
    def _principled(material):
        """The material's Principled BSDF node, or ``None``."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        return _MatUtilsInternal._principled_node(material)

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
