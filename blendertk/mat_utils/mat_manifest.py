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

# (slot key) -> candidate Principled-BSDF input socket names, tried in order (covers the
# Blender-version rename of "Emission" -> "Emission Color").
_SLOT_SOCKETS: Dict[str, tuple] = {
    "baseColor": ("Base Color",),
    "emission": ("Emission Color", "Emission"),
    "specular": ("Specular IOR Level", "Specular"),
    "roughness": ("Roughness",),
    "metallic": ("Metallic",),
    "opacity": ("Alpha",),
    "normal": ("Normal",),
}


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
        from blendertk.mat_utils._mat_utils import get_mats

        manifest: Dict[str, Any] = {"materials": {}}
        for mat in get_mats(objects):
            data = cls._process_material(mat)
            if data:
                manifest["materials"][mat.name] = data
        return manifest

    @classmethod
    def _process_material(cls, mat) -> Dict[str, str]:
        """Resolve texture paths for every mapped slot of a single material's Principled BSDF."""
        from blendertk.mat_utils._mat_utils import _principled_node, _abspath

        node = _principled_node(mat)
        if node is None:
            return {}

        data: Dict[str, str] = {}
        for slot, socket_names in _SLOT_SOCKETS.items():
            img = cls._trace_image(node, socket_names)
            if img is None:
                continue
            path = _abspath(img)
            if path:
                data[slot] = path
        return data

    @staticmethod
    def _trace_image(node, socket_names):
        """Walk back from the first matching input socket to its feeding image, if any.

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
            return src.image
        if src.type in ("NORMAL_MAP", "BUMP"):
            inner = src.inputs.get("Color") or src.inputs.get("Height")
            if inner is not None and inner.is_linked:
                up = inner.links[0].from_node
                if up.type == "TEX_IMAGE":
                    return up.image
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
        from blendertk.mat_utils._mat_utils import _principled_node

        key = source_mat_name or mat_name
        mat_data = manifest.get("materials", {}).get(key, {})
        if not mat_data:
            return 0

        mat = bpy.data.materials.get(mat_name)
        node = _principled_node(mat)
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

            img_node = nt.nodes.new("ShaderNodeTexImage")
            img_node.image = img
            if slot == "normal":
                norm_node = nt.nodes.new("ShaderNodeNormalMap")
                nt.links.new(img_node.outputs["Color"], norm_node.inputs["Color"])
                nt.links.new(norm_node.outputs["Normal"], socket)
            else:
                out_key = "Alpha" if slot == "opacity" else "Color"
                nt.links.new(img_node.outputs[out_key], socket)
            restored += 1

        return restored

    @staticmethod
    def _find_or_load_image(tex_path: str):
        """Return an existing ``bpy.data.images`` entry for *tex_path*, or load a new one."""
        import os
        import bpy

        norm = os.path.normpath(tex_path)
        for img in bpy.data.images:
            if img.filepath and os.path.normpath(bpy.path.abspath(img.filepath)) == norm:
                return img
        try:
            return bpy.data.images.load(tex_path)
        except RuntimeError:
            return None
