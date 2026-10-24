# !/usr/bin/python
# coding=utf-8
"""Read named sections of live-scene state for transport.

Retired 2026-10-20 (removed in blendertk 0.21.0): the FBX states every
material in its ``materials`` record
(:class:`~blendertk.env_utils.material_records.MaterialRecords`, the one
reader of the file's materials), and a GLB's handoff comes from the FBX's own
record -- so no export reads a section. Its readers still answer, through
MaterialRecords' helpers, until the removal.

Mirror of mayatk's ``env_utils.scene_state.SceneState``
(``btk.SceneState`` <-> ``mtk.SceneState``). It was the Blender *reader
column* of the scene-data grid: each section of scene state that FBX
translation drops, read once and handed to the carrier the caller filled
(the WebXR preview's in-process envelope, the Scene Exporter's GLB
conversion). The GLB build now applies the FBX's own ``materials`` record
(``ptk.MeshConvert.apply_glb_materials``) instead, so no section is read.

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

import pythontk as ptk

# Eager: SceneState names MaterialRecords' graph constants (the cutout node's
# label, the unlit routes) at class definition. The module imports no bpy
# until a reader runs.
from blendertk.env_utils.material_records import MaterialRecords


class SceneState:
    """Section-registry reader of scene state the FBX cannot express --
    retired with the sections it read (see the module's docstring)."""

    #: Section -> the classmethod that reads it. The extension point: a new
    #: section is one row here plus its reader, with the same
    #: ``(materials, textures)`` signature.
    _READERS: Dict[str, str] = {
        "base_color": "_read_base_color",
        "emissive": "_read_emissive",
        "metallic_roughness": "_read_metallic_roughness",
        "alpha_mode": "_read_alpha_mode",
        "unlit": "_read_unlit",
    }
    READERS = ptk.Deprecation.class_attribute(
        _READERS,
        "btk.MaterialRecords -- the materials record states every material",
        remove_in="0.21.0",
        since="2026-10-24",
    )

    # MaterialRecords' own: the reader of the file's materials.
    MASK_NODE_LABEL = MaterialRecords.MASK_NODE_LABEL
    _UNLIT_ROUTES = MaterialRecords._UNLIT_ROUTES

    @staticmethod
    @ptk.Deprecation.symbol(
        "btk.FbxUtils.export_context().source", remove_in="0.21.0", since="2026-10-24"
    )
    def source() -> Dict[str, str]:
        """This host's identity for the envelope's ``source`` key."""
        import bpy

        return {"application": "blender", "version": bpy.app.version_string}

    @classmethod
    @ptk.Deprecation.symbol(
        "btk.MaterialRecords.entries -- the materials record states every material",
        remove_in="0.21.0",
        since="2026-10-24",
    )
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
        for section, reader in cls._READERS.items():
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
        """:meth:`MaterialRecords._opacity_factor`."""
        return MaterialRecords._opacity_factor(material)

    @classmethod
    def _transparent_mix_factor(cls, material: Any) -> Optional[Any]:
        """:meth:`MaterialRecords._transparent_mix_factor`."""
        return MaterialRecords._transparent_mix_factor(material)

    @staticmethod
    def _surface_shader(tree: Any) -> Optional[Any]:
        """:meth:`MaterialRecords._surface_shader`."""
        return MaterialRecords._surface_shader(tree)

    @staticmethod
    def _scalar_of(socket: Any) -> Optional[float]:
        """:meth:`MaterialRecords._scalar_of`."""
        return MaterialRecords._scalar_of(socket)

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

    @classmethod
    def _alpha_mode_of(cls, material: Any) -> Optional[Dict[str, Any]]:
        """:meth:`MaterialRecords._alpha_mode_of`."""
        return MaterialRecords._alpha_mode_of(material)

    @classmethod
    def _mask_entry(cls, material: Any) -> Optional[Dict[str, Any]]:
        """:meth:`MaterialRecords._mask_entry`."""
        return MaterialRecords._mask_entry(material)

    @staticmethod
    def _surface_mixes_transparency(material: Any, limit: int = 64) -> bool:
        """:meth:`MaterialRecords._surface_mixes_transparency`."""
        return MaterialRecords._surface_mixes_transparency(material, limit)

    @classmethod
    def _read_unlit(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {}}`` for the *materials* the scene shades unlit
        (:meth:`MaterialRecords._unlit_materials`)."""
        return {name: {} for name in MaterialRecords._unlit_materials(materials)}

    @classmethod
    def _is_unlit(cls, material: Any, limit: int = 64) -> bool:
        """:meth:`MaterialRecords._is_unlit`."""
        return MaterialRecords._is_unlit(material, limit)

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
        """:meth:`MaterialRecords._principled`."""
        return MaterialRecords._principled(material)

    @staticmethod
    def _emission_strength(node) -> float:
        """:meth:`MaterialRecords._emission_strength`."""
        return MaterialRecords._emission_strength(node)

    @staticmethod
    def _emission_color(node) -> Optional[List[float]]:
        """:meth:`MaterialRecords._emission_color`."""
        return MaterialRecords._emission_color(node)
