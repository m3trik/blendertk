# !/usr/bin/python
# coding=utf-8
"""Push the Blender selection to a live browser / WebXR preview.

Mirror of mayatk's ``env_utils.webxr_preview.WebXrPreview``
(``btk.WebXrPreview`` <-> ``mtk.WebXrPreview``).

The lightest of the hand-off bridges: there is no target application to
discover or launch, because the target is a browser tab the user already has
open. :class:`pythontk.PreviewDeliverer` converts the exported FBX to GLB and
publishes it to a loopback :class:`pythontk.PreviewServer`; a page already open
-- including one open inside a PC-tethered headset -- picks the new version up
on its next poll.

The mirror is literal rather than parallel: :class:`pythontk.PreviewBridge`
owns the export defaults and the public ``push`` / ``url`` / ``stop`` surface
for both packages, so only the selection read differs and
:class:`BlenderExportMixin` supplies that. ``bpy`` is never imported here (the
mixin defers it), so the package surface still resolves without a running
Blender.

Example:
    >>> preview = btk.WebXrPreview()
    >>> preview.push()              # opens a tab on the first call
    >>> preview.push()              # the open tab swaps to the new version
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pythontk as ptk

from blendertk.env_utils.handoff_export import BlenderExportMixin


class WebXrPreview(BlenderExportMixin, ptk.PreviewBridge):
    """Live browser / WebXR preview of the Blender selection.

    One :class:`pythontk.PreviewDeliverer` is shared by every instance, so the
    server -- and therefore the port and the tab pointed at it -- survives
    across pushes and across panel reopens for the life of the Blender session.
    """

    payload_prefix = "blender_webxr_preview"
    deliverer = ptk.PreviewDeliverer(title="Blender")

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX, then attach the scene sidecar the FBX can't carry.

        Mirror of the Maya producer: the skeleton's FBX payload plus a sidecar
        riding on ``Payload.extras``, written to a real file alongside it as
        well, because the point of the panel's toggle is being able to look at
        what travelled.
        """
        payload = super()._produce(objects, request)
        if payload is None or not request.params.get("SCENE_SIDECAR", True):
            return payload

        # The closed export set, not the raw selection: a group Empty ships its
        # descendants, and their materials must travel with them.
        sidecar = self._scene_sidecar(
            payload.extras.get("export_set") or objects,
            include_textures=request.params.get("EMBED_TEXTURES", True),
        )
        if sidecar:
            payload.extras["scene_sidecar"] = sidecar
            path = self._make_payload_path(extension=".scene.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sidecar, f, indent=2)
            payload.extras["scene_sidecar_path"] = path
            self.logger.info(
                "Scene sidecar (%s) -> %s", ", ".join(sorted(sidecar)), path
            )
        return payload

    def _scene_sidecar(
        self, objects: List[Any], include_textures: bool = True
    ) -> Dict[str, Any]:
        """Scene state the FBX cannot express, one key per section.

        Mirror of the Maya producer's hook. Most material data does survive the
        FBX round trip, so this is not a material channel -- it is where
        anything that *doesn't* travel goes. A new kind of extended setup
        (lights, environment, custom attributes) is added as one more section
        here plus one more applier in
        :meth:`pythontk.PreviewDeliverer._apply_sidecar`.

        *include_textures* mirrors the export's ``EMBED_TEXTURES``: unchecked
        promises a fast, flat-material push, and the sidecar must not smuggle
        maps back in as data URIs after the FBX skipped them -- constants only.
        """
        # Resolved ONCE and shared: both readers need the same material list
        # and the same texture manifest, and ``MatManifest.build`` walks every
        # assigned node tree -- paying for that twice per push is pure waste on
        # the one operation that is meant to feel immediate.
        from blendertk.mat_utils._mat_utils import MatUtils
        from blendertk.mat_utils.mat_manifest import MatManifest

        materials = MatUtils.get_mats(objects) or []
        textures = (
            (MatManifest.build(objects).get("materials", {}) or {})
            if include_textures
            else {}
        )

        sidecar: Dict[str, Any] = {}
        base_color = self._read_base_color(materials)
        if base_color:
            sidecar["base_color"] = base_color
        emissive = self._read_emissive(materials, textures)
        if emissive:
            sidecar["emissive"] = emissive
        return sidecar

    @classmethod
    def _read_base_color(cls, materials: List[Any]) -> Dict[str, Dict[str, Any]]:
        """``{material: {"color": (r, g, b)}}`` for constant base colours.

        Mirror of the Maya reader, and needed for the same reason there: a
        preview whose surfaces are all flat white leaves emissive nothing to
        read against. Only the *constant* is carried -- a texture-driven base
        colour already travels through the FBX as a real image, so re-embedding
        it would only bloat the payload.
        """
        result: Dict[str, Dict[str, Any]] = {}
        for material in materials:
            node = cls._principled(material)
            if node is None:
                continue
            socket = node.inputs.get("Base Color")
            if socket is None or socket.is_linked:
                continue
            result[material.name] = {"color": list(socket.default_value)[:3]}
        return result

    @classmethod
    def _read_emissive(
        cls, materials: List[Any], textures: Dict[str, Dict[str, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """``{material: {"color": (r, g, b), "texture": path}}`` for *materials*.

        *textures* comes from :class:`MatManifest`, which already owns the
        ``Emission Color`` / ``Emission`` socket rename across Blender
        versions; the constant beside it is read from the same socket.
        Materials with no emission are omitted, so an unlit scene adds no
        sidecar.

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
            # Strength 0 means the material emits nothing, so there is nothing
            # to carry. Mirror of the Maya twin: with a map connected, claiming
            # otherwise would show it at full brightness on a material that
            # renders black, and the panel would report a successful transfer.
            if strength == 0.0:
                continue

            entry: Dict[str, Any] = {}
            texture = (textures.get(material.name) or {}).get("emission")
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

    @staticmethod
    def _principled(material):
        """The material's Principled BSDF node, or ``None``."""
        if not getattr(material, "use_nodes", False) or material.node_tree is None:
            return None
        return next(
            (n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None
        )

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
