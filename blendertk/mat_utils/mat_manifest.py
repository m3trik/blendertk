# !/usr/bin/python
# coding=utf-8
"""Material-to-texture manifest for bridge workflows -- mirror of mayatk's ``mat_utils.mat_manifest``.

Blender's PBR materials funnel through one universal shader (Principled BSDF), unlike Maya's
many shader types (lambert / blinn / aiStandardSurface / ...), so this needs no per-shader-type
registry like mayatk's ``ShaderAttributeMap`` -- one socket map covers every material.

``import bpy`` is deferred into the methods so the module resolves headlessly (mirrors the rest
of blendertk); the Qt-only bridge slots that call this stay Qt-free too.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pythontk as ptk

from blendertk.mat_utils._mat_utils import _PRINCIPLED_LOBES

# (slot key) -> candidate Principled-BSDF input socket names, tried in order (covers the
# Blender-version rename of "Emission" -> "Emission Color"). Keys are mayatk's
# ``ShaderAttrs`` fields, so a manifest crosses the bridge slot for slot -- the OpenPBR lobes
# included, taken from the Game Shader's Principled table so the two cannot drift.
# ``specular`` is the TINT (``Specular Tint``), as on Maya's surfaces; the level that
# ``Specular IOR Level`` holds -- and Blender < 4.0's ``Specular`` -- is ``specularWeight``.
# Filed under ``specular``, a level map crossed into Maya's specular COLOUR.
_SLOT_SOCKETS: Dict[str, tuple] = {
    "baseColor": ("Base Color",),
    "emission": ("Emission Color", "Emission"),
    "roughness": ("Roughness",),
    "metallic": ("Metallic",),
    "opacity": ("Alpha",),
    "normal": ("Normal",),
    "specularWeight": ("Specular IOR Level", "Specular"),
    **{
        channel: (socket,)
        for channel, socket in _PRINCIPLED_LOBES.items()
        if channel != "specularWeight"
    },
}

# Slots whose input is a tangent-space normal: restored through a Normal Map node.
_NORMAL_SLOTS = ("normal", "coatNormal")


class MatManifest(ptk.HelpMixin):
    """Builds and restores a material-to-texture manifest for bridge workflows.

    The output is a plain dict suitable for ``json.dump`` and consumption by external tools
    (Marmoset Toolbag, Substance Painter, Unreal, Unity, ...). Same manifest shape as mayatk's
    :class:`mayatk.mat_utils.mat_manifest.MatManifest` -- slot keys match
    :data:`_SLOT_SOCKETS` / mayatk's ``ShaderAttrs`` fields.
    """

    @classmethod
    def build(cls, objects: List) -> Dict[str, Any]:
        """Build a manifest from the materials assigned to *objects*.

        Parameters:
            objects: Blender objects (or object names).

        Returns:
            Manifest dict ready for serialisation.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        manifest: Dict[str, Any] = {"materials": {}}
        for mat in MatUtils.get_mats(objects):
            data = cls._process_material(mat)
            if data:
                manifest["materials"][mat.name] = data
        return manifest

    @classmethod
    def _process_material(cls, mat) -> Dict[str, str]:
        """Resolve texture paths for every mapped slot of a single material's Principled BSDF."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        data: Dict[str, str] = {}
        for slot, img_node in cls._channel_image_nodes(mat).items():
            path = _MatUtilsInternal._abspath(img_node.image)
            if path:
                data[slot] = path
        return data

    @classmethod
    def _channel_image_nodes(cls, mat) -> Dict[str, Any]:
        """``{slot: Image Texture node}`` feeding each mapped slot of *mat*'s
        Principled BSDF -- what :meth:`_process_material` reads its paths from
        (mirror of mayatk's ``MatManifest._channel_file_nodes``)."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        node = _MatUtilsInternal._principled_node(mat)
        if node is None:
            return {}
        nodes: Dict[str, Any] = {}
        for slot, socket_names in _SLOT_SOCKETS.items():
            img_node = cls._trace_image_node(node, socket_names)
            if img_node is not None and img_node.image is not None:
                nodes[slot] = img_node
        return nodes

    @staticmethod
    def _trace_image_node(node, socket_names):
        """Walk back from the first matching input socket to its feeding Image
        Texture node, if any.

        Handles a direct Image Texture link, or one hop through a Normal Map / Bump node
        (the usual Normal-input shape).
        """
        socket = next(
            (node.inputs[name] for name in socket_names if name in node.inputs), None
        )
        if socket is None or not socket.is_linked:
            return None
        src = socket.links[0].from_node
        if src.type == "TEX_IMAGE":
            return src
        if src.type in ("NORMAL_MAP", "BUMP"):
            inner = src.inputs.get("Color") or src.inputs.get("Height")
            if inner is not None and inner.is_linked:
                up = inner.links[0].from_node
                if up.type == "TEX_IMAGE":
                    return up
        return None

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    @classmethod
    def restore(
        cls,
        mat_name: str,
        manifest: Dict[str, Any],
        source_mat_name: Optional[str] = None,
    ) -> int:
        """Reconnect image textures to the material named *mat_name* from a manifest.

        Parameters:
            mat_name: The material to restore textures onto.
            manifest: A manifest dict as returned by :meth:`build`.
            source_mat_name: Key to look up in the manifest. Defaults to *mat_name*.

        Returns:
            Number of texture slots successfully reconnected.
        """
        import bpy
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        key = source_mat_name or mat_name
        mat_data = manifest.get("materials", {}).get(key, {})
        if not mat_data:
            return 0

        mat = bpy.data.materials.get(mat_name)
        node = _MatUtilsInternal._principled_node(mat)
        if node is None:
            return 0

        nt = mat.node_tree
        restored = 0
        for slot, tex_path in mat_data.items():
            socket_names = _SLOT_SOCKETS.get(slot)
            if not socket_names:
                continue
            socket = next(
                (node.inputs[n] for n in socket_names if n in node.inputs), None
            )
            if socket is None:
                continue

            img = cls._find_or_load_image(tex_path)
            if img is None:
                continue
            # Opacity is the image's ALPHA where it has one -- never colour
            # managed, so it leaves the colour space to the slot reading the
            # colour (a cutout shares its file with baseColor: retagged
            # Non-Color by whichever slot came last, the colour read as data).
            # Without alpha the output is a constant 1; the value is the
            # opacity then, read as data (mayatk's alphaIsLuminance rescue).
            from_alpha = slot == "opacity" and cls._has_alpha(img)
            # The slot's data, not Blender's load default: a fresh PNG loads as
            # sRGB, so every restored roughness / opacity / normal / lobe
            # weight was gamma-decoded. The registry's color_space says which.
            entry = ptk.MapRegistry().get(
                ptk.MapRegistry.resolve_type_from_channel(slot)
            )
            if entry is not None and not from_alpha:
                try:
                    img.colorspace_settings.name = (
                        "sRGB" if entry.color_space == "sRGB" else "Non-Color"
                    )
                except (AttributeError, TypeError):
                    pass

            img_node = nt.nodes.new("ShaderNodeTexImage")
            img_node.image = img
            if slot in _NORMAL_SLOTS:
                norm_node = nt.nodes.new("ShaderNodeNormalMap")
                nt.links.new(img_node.outputs["Color"], norm_node.inputs["Color"])
                nt.links.new(norm_node.outputs["Normal"], socket)
            else:
                out_key = "Alpha" if from_alpha else "Color"
                nt.links.new(img_node.outputs[out_key], socket)
            restored += 1

        return restored

    @staticmethod
    def _has_alpha(img) -> bool:
        """True when *img* carries an alpha channel.

        Every loaded image reports 4 ``channels``; the plane count tells: RGBA
        at 8 / 16 / 32 bits per channel is 32 / 64 / 128 (probed on Blender
        5.1: an RGBA PNG reads 32, an RGB one 24).
        """
        return getattr(img, "depth", 0) in (32, 64, 128)

    @staticmethod
    def _find_or_load_image(tex_path: str):
        """Return an existing ``bpy.data.images`` entry for *tex_path*, or load a new one."""
        import os
        import bpy

        norm = os.path.normpath(tex_path)
        for img in bpy.data.images:
            if (
                img.filepath
                and os.path.normpath(bpy.path.abspath(img.filepath)) == norm
            ):
                return img
        try:
            return bpy.data.images.load(tex_path)
        except RuntimeError:
            return None
