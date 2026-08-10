# !/usr/bin/python
# coding=utf-8
"""Ship a committed lightmap bake in a web (GLB) deliverable.

**Export-time only.** The bake itself is
:class:`~blendertk.light_utils.lightmap_baker.lightmap_baker.LightmapBaker`'s job, and it is
platform-agnostic: it commits to the *scene* (markers + the ``lightmap_metadata`` manifest
on the ``data_export`` carrier), which the Unity/FBX path and Maya round trip read without
this module. This module is the **web consumer** of that committed state -- what runs when a
GLB is exported from Blender natively:

* **Encode** -- :meth:`~LightmapWebExport.encode_for_web`: linear HDR EXR -> sRGB PNG.
  glTF carries only PNG/JPEG (KTX2 by extension), so the EXR cannot ship as-is.
* **Carry** -- :meth:`~LightmapWebExport.wire_lightmaps`: glTF has no lightmap slot, so the
  map rides a real texture slot on ``TEXCOORD_1`` and the viewer rebinds it.
* **Export** -- :meth:`~LightmapWebExport.export_glb` plus the ``lightmap_web`` extras
  manifest the viewer reads; :meth:`~LightmapWebExport.wired_for_export` wraps all of it
  around any native glTF export, feeding itself from the committed markers.

(The Maya-side GLB path needs none of this: its deliverable converts through
``ptk.MeshConvert.fbx_to_glb``, whose ``apply_glb_lightmaps`` reads the same manifest back
out of the GLB itself. One committed bake, N consumers, none aware of each other.)

Two facts drive the design, both measured against Blender 5.1 rather than assumed:

**Atlas packing is required, not optional.** A lightmap is per-object but a glTF material is
shared: six objects sharing one material cannot carry six different lightmaps without
duplicating the material six times (bloating the GLB and breaking instancing). So the web
path consolidates through :meth:`LightmapBaker.pack_atlas` -- one lightmap per material
group, which is exactly one texture per glTF material.

**A lightmap's dynamic range does not fit in a PNG.** A real interior bake measured
0 -> 129.8 with a mean of 0.33 (emissive fixtures against a dim room), so clamping at 1.0
blows out the fixtures and crushes everything else. :meth:`encode_for_web` instead divides
by a high percentile, records that divisor per map, and the viewer multiplies it back
through three.js's ``lightMapIntensity``. The few texels above the percentile clip, which is
the correct trade -- they are the light sources themselves.

The engine surface is Qt-free and defers ``import bpy`` (headless-importable).
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pythontk as ptk

from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
from blendertk.uv_utils._uv_utils import UvUtils, LIGHTMAP_UV_SET


class LightmapWebExport(ptk.LoggingMixin):
    """Ship a scene's committed lightmaps in a natively-exported GLB.

    Composes :class:`LightmapBaker` rather than extending it: the bake is a DCC concern the
    Unity/FBX path shares, while everything here is specific to shipping a browser
    deliverable. Usage (a scene whose lightmaps are already committed)::

        web = LightmapWebExport()
        with web.wired_for_export() as manifest:
            web.export_glb("C:/out/office.glb", manifest=manifest)
    """

    #: glTF ``extras`` key holding the viewer manifest. Read by the bundled WebXR viewer to
    #: rebind the carrier slot to a real three.js ``lightMap``.
    EXTRAS_KEY: str = "lightmap_web"
    MANIFEST_VERSION: int = 1

    #: Which real glTF texture slot carries the lightmap (see :meth:`wire_lightmaps`).
    CARRIERS: Tuple[str, ...] = ("occlusion", "emissive")

    #: Custom property stamped on a wired lightmap image datablock, so the web texture
    #: budget can tell it apart from a source map (see :meth:`_downsize_images`).
    LIGHTMAP_IMAGE_MARKER: str = "btk_lightmap"

    #: Percentile used as the encode divisor. High enough that only genuine light sources
    #: clip, low enough that the mid-tones keep their 8-bit precision.
    DEFAULT_PERCENTILE: float = 99.5

    #: Name Blender's glTF exporter looks for to route a value into ``occlusionTexture``.
    _GLTF_OUTPUT_GROUP: str = "glTF Material Output"

    def __init__(
        self,
        baker: Optional[LightmapBaker] = None,
        resolution: int = 1024,
        samples: int = 128,
        denoise: bool = True,
        device: Optional[str] = "GPU",
    ):
        super().__init__()
        # GPU by default: a web deliverable is baked at production sample counts, and the
        # difference between a minutes-long and an hours-long bake is the whole iteration
        # loop. Falls back to the CPU with a warning when no compute device is available.
        self.baker = baker or LightmapBaker(
            resolution=resolution, samples=samples, denoise=denoise, device=device
        )

    # ------------------------------------------------------------------ encode

    @classmethod
    def encode_for_web(
        cls,
        mapping: Dict[str, str],
        output_dir: Optional[str] = None,
        percentile: Optional[float] = None,
        suffix: str = "",
    ) -> Dict[str, Tuple[str, float]]:
        """Encode linear HDR lightmap EXRs as sRGB PNGs the browser can load.

        Each unique source file is encoded once (an atlas shared by six objects costs one
        PNG, not six). The divisor is that map's *percentile* value, so the useful range
        fills the 8-bit encoding and only genuine light sources clip; it is returned so the
        viewer can multiply it back and recover the original intensity.

        Blender's save-time colour management is the trap here, and it is not symmetric:
        the destination image's colorspace must be set to ``Non-Color`` **before** its
        pixels are written. Setting it afterwards discards the buffer (the map saves pure
        black); leaving it unset makes Blender apply its own linear->sRGB on top of this
        one, double-encoding the map. Both were measured, not assumed.

        Parameters:
            mapping: ``{object_name: exr_path}`` from :meth:`LightmapBaker.bake_separated`
                (or the atlas paths from :meth:`LightmapBaker.pack_atlas`).
            output_dir: Destination. Defaults to each EXR's own directory.
            percentile: Encode divisor percentile. Default :attr:`DEFAULT_PERCENTILE`.
            suffix: Appended to the PNG stem.

        Returns:
            ``{object_name: (png_path, scalar)}`` -- multiply the sampled colour by
            ``scalar`` to recover linear intensity.
        """
        import bpy
        import numpy as np

        percentile = cls.DEFAULT_PERCENTILE if percentile is None else float(percentile)
        encoded: Dict[str, Tuple[str, float]] = {}
        done: Dict[str, Tuple[str, float]] = {}  # abspath -> result, encode once

        for name, src in mapping.items():
            key = os.path.abspath(src)
            if key in done:
                encoded[name] = done[key]
                continue
            if not os.path.isfile(src):
                continue

            image = None
            try:
                image = bpy.data.images.load(key)
                width, height = image.size
                buf = np.empty(len(image.pixels), dtype=np.float32)
                image.pixels.foreach_get(buf)
                px = buf.reshape(-1, image.channels)
                rgb = px[:, :3]

                lit = rgb[rgb > 0.0]
                scalar = float(np.percentile(lit, percentile)) if lit.size else 1.0
                scalar = max(scalar, 1e-6)

                out_rgb = cls._linear_to_srgb(np.clip(rgb / scalar, 0.0, 1.0))
            finally:
                if image is not None:
                    bpy.data.images.remove(image)

            stem = os.path.splitext(os.path.basename(src))[0] + suffix
            directory = output_dir or os.path.dirname(key)
            os.makedirs(directory, exist_ok=True)
            dst = os.path.join(directory, f"{stem}.png")

            out = bpy.data.images.new(
                os.path.basename(dst), width=width, height=height, alpha=False
            )
            try:
                # BEFORE the pixel write — see the docstring.
                out.colorspace_settings.name = "Non-Color"
                flat = np.ones((out_rgb.shape[0], 4), dtype=np.float32)
                flat[:, :3] = out_rgb
                out.pixels.foreach_set(flat.reshape(-1))
                out.filepath_raw = dst
                out.file_format = "PNG"
                out.save()
            finally:
                bpy.data.images.remove(out)

            done[key] = (dst, scalar)
            encoded[name] = done[key]

        return encoded

    @staticmethod
    def _linear_to_srgb(a):
        """Vectorized linear -> sRGB transfer (the IEC 61966-2-1 piecewise curve)."""
        import numpy as np

        a = np.clip(a, 0.0, 1.0)
        return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)

    # ------------------------------------------------------------------ carry

    def wire_lightmaps(
        self,
        encoded: Dict[str, Tuple[str, float]],
        carrier: str = "occlusion",
        uv_set: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Wire each lightmap into a real glTF texture slot on the lightmap UV.

        glTF 2.0 has no lightmap slot, and Blender's exporter only emits a UV layer that a
        texture actually references -- so a lightmap that is merely *present* reaches the
        browser with neither an image nor a ``TEXCOORD_1`` to sample it by. The map
        therefore rides a real slot:

        * ``occlusion`` (default) -- through the ``glTF Material Output`` group, giving
          ``occlusionTexture`` with its own ``texCoord``. Leaves the emissive slot free for
          the scene's authored emissive map, which matters whenever the light sources are
          emissive geometry. glTF calls occlusion single-channel, but the PNG carries full
          RGB and the bytes survive: a naive viewer applies it as grey AO (a sane
          degradation) while the bundled viewer rebinds it to a full-colour ``lightMap``.
        * ``emissive`` -- ``emissiveTexture``. Degrades more attractively in a third-party
          viewer (the scene shows lit rather than merely shaded) but *overwrites* an
          authored emissive map. Prefer it for fused/unlit content.

        Returns a restore token for :meth:`unwire_lightmaps`; the wiring is a transport
        detail, not a change the artist asked for.
        """
        import bpy

        if carrier not in self.CARRIERS:
            raise ValueError(f"carrier must be one of {self.CARRIERS}, got {carrier!r}")

        token: Dict[str, Any] = {"carrier": carrier, "materials": []}
        wired: set = set()

        for name, (png, _scalar) in encoded.items():
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            lm = uv_set or UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            for slot in getattr(obj, "material_slots", []) or []:
                material = slot.material
                if material is None or material.name in wired:
                    continue
                try:
                    added = self._wire_material(material, png, lm, carrier)
                except Exception as error:  # a transport failure must not lose the bake
                    self.logger.warning(
                        "Lightmap not wired into %s (%s); it still ships as a file.",
                        material.name,
                        error,
                    )
                    continue
                wired.add(material.name)
                token["materials"].append({"material": material.name, "nodes": added})

        return token

    def _wire_material(
        self, material, png: str, uv_name: str, carrier: str
    ) -> List[str]:
        """Add the lightmap image + UVMap nodes to *material*; return the node names added."""
        import bpy

        material.use_nodes = True
        nt = material.node_tree

        image = bpy.data.images.load(os.path.abspath(png), check_existing=True)
        image.colorspace_settings.name = "sRGB"
        # Exempt it from the web texture budget — the caller sized it via the bake
        # resolution, not by accident (see :meth:`_downsize_images`).
        image[self.LIGHTMAP_IMAGE_MARKER] = True

        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.label = "Lightmap"
        uvmap = nt.nodes.new("ShaderNodeUVMap")
        uvmap.uv_map = uv_name
        nt.links.new(uvmap.outputs["UV"], tex.inputs["Vector"])
        added = [tex.name, uvmap.name]

        if carrier == "emissive":
            bsdf = next(
                (n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"),
                None,
            )
            if bsdf is None:
                raise RuntimeError("no Principled BSDF to take the emissive slot")
            socket = bsdf.inputs.get("Emission Color")
            for link in list(socket.links):
                nt.links.remove(link)
            nt.links.new(tex.outputs["Color"], socket)
            strength = bsdf.inputs.get("Emission Strength")
            if strength is not None:
                strength.default_value = 1.0
            return added

        group = nt.nodes.new("ShaderNodeGroup")
        group.node_tree = self._gltf_output_group()
        group.name = self._GLTF_OUTPUT_GROUP
        nt.links.new(tex.outputs["Color"], group.inputs["Occlusion"])
        added.append(group.name)
        return added

    @classmethod
    def _gltf_output_group(cls):
        """The ``glTF Material Output`` node group, created on first use.

        Blender's glTF exporter routes a value connected to this group's ``Occlusion``
        input into ``occlusionTexture``; the group itself is a marker with no shading
        behaviour, so it never affects the Cycles render or a later bake.
        """
        import bpy

        group = bpy.data.node_groups.get(cls._GLTF_OUTPUT_GROUP)
        if group is None:
            group = bpy.data.node_groups.new(cls._GLTF_OUTPUT_GROUP, "ShaderNodeTree")
        if "Occlusion" not in [s.name for s in group.interface.items_tree]:
            group.interface.new_socket(
                "Occlusion", in_out="INPUT", socket_type="NodeSocketFloat"
            )
        return group

    @staticmethod
    def unwire_lightmaps(token: Dict[str, Any]) -> List[str]:
        """Remove the nodes :meth:`wire_lightmaps` added, restoring the source materials."""
        import bpy

        restored: List[str] = []
        for record in (token or {}).get("materials", []):
            material = bpy.data.materials.get(record.get("material", ""))
            if material is None or not material.use_nodes:
                continue
            nt = material.node_tree
            for node_name in record.get("nodes", []):
                node = nt.nodes.get(node_name)
                if node is not None:
                    nt.nodes.remove(node)
            restored.append(material.name)
        return restored

    # ------------------------------------------------------------------ export

    def build_manifest(
        self,
        encoded: Dict[str, Tuple[str, float]],
        carrier: str,
        lighting: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """The ``lightmap_web`` manifest the viewer reads to rebind the carrier slot.

        Keyed by *material* rather than object: after atlas consolidation the lightmap is a
        material-level property, and glTF materials are what the viewer actually walks.
        """
        import bpy

        materials: Dict[str, Dict[str, Any]] = {}
        for name, (png, scalar) in encoded.items():
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            for slot in getattr(obj, "material_slots", []) or []:
                if slot.material is None:
                    continue
                entry = {"map": os.path.basename(png), "intensity": round(scalar, 6)}
                claimed = materials.setdefault(slot.material.name, entry)
                if claimed["map"] != entry["map"]:
                    # A glTF material carries exactly one lightmap, so the second one has
                    # nowhere to go. Atlas packing is what normally prevents this; reaching
                    # here means it fell back to per-object maps (a packing failure, logged
                    # by pack_atlas). Say so — the symptom is one object wearing another's
                    # lighting, which looks like a bad bake rather than a dropped map.
                    self.logger.warning(
                        "Material %r already carries %s, so %s (from %s) cannot be "
                        "published — that object will sample the first map. Re-run with "
                        "atlas packing, or split the material.",
                        slot.material.name,
                        claimed["map"],
                        entry["map"],
                        name,
                    )
        return {
            "version": self.MANIFEST_VERSION,
            "carrier": carrier,
            "uv": 1,
            "encoding": "srgb",
            "materials": materials,
            "lighting": lighting or {},
        }

    def export_glb(
        self,
        path: str,
        objects=None,
        manifest: Optional[Dict[str, Any]] = None,
        texture_max_size: Optional[int] = 2048,
        image_format: str = "WEBP",
        image_quality: int = 85,
    ) -> str:
        """Export a GLB through Blender's native glTF exporter.

        Native rather than the FBX->FBX2glTF chain the Maya deliverable uses: the source is
        already Blender, so Principled BSDF maps straight onto ``pbrMetallicRoughness`` with
        no second translation, and the exporter is what emits ``TEXCOORD_1`` for the wired
        lightmap. *manifest* rides the scene's custom properties into the glTF root
        ``extras``, so the deliverable is self-describing.

        Passing *objects* exports **only those objects**: Blender's exporter has no
        "include ancestors" option, so a mesh list drops the group Empties above it. World
        transforms are baked, so placement is unaffected (verified against a bridged Maya
        room) -- what is lost is the grouping, which a static baked environment does not
        use and which costs nodes in a file whose whole point is to be small. Pass ``None``
        to export the scene as authored, hierarchy included.

        **The texture budget is the whole file size.** Measured on a real environment
        module, the lightmaps came to 0.8 MB and the source PBR set to 96 MB -- two 4096²
        normal maps alone were 50 MB. A headset streaming that over the network, then
        holding it in GPU memory uncompressed, is the difference between a deliverable and
        a demo that never loads. So the web path downsizes and recompresses by default:

        Parameters:
            texture_max_size: Longest edge any image may have; larger ones are scaled down
                before export and reloaded from disk afterwards (the scale is destructive
                in-memory). ``None`` keeps authored resolution.
            image_format: ``"WEBP"`` (default -- roughly an order of magnitude smaller than
                PNG at visually equal quality, and universally supported by WebXR-capable
                browsers), ``"JPEG"``, or ``"AUTO"`` to keep each image's own format.
            image_quality: Lossy quality for WEBP / JPEG.
        """
        import bpy

        path = path if path.lower().endswith(".glb") else path + ".glb"
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

        scene = bpy.context.scene
        prior = scene.get(self.EXTRAS_KEY)
        if manifest is not None:
            scene[self.EXTRAS_KEY] = json.dumps(manifest)

        use_selection = objects is not None
        if use_selection:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in ptk.make_iterable(objects):
                obj = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
                if obj is not None:
                    obj.select_set(True)

        resized = self._downsize_images(texture_max_size)
        try:
            bpy.ops.export_scene.gltf(
                filepath=path,
                export_format="GLB",
                use_selection=use_selection,
                export_extras=True,
                export_apply=False,
                export_yup=True,
                export_image_format=image_format,
                export_image_quality=image_quality,
            )
        finally:
            for image in resized:
                try:  # the scale was destructive; disk is the only way back
                    image.reload()
                except RuntimeError:
                    pass
            if manifest is not None:
                if prior is None:
                    del scene[self.EXTRAS_KEY]
                else:
                    scene[self.EXTRAS_KEY] = prior
        return path

    def _downsize_images(self, max_size: Optional[int]) -> List[Any]:
        """Scale every source image whose longest edge exceeds *max_size*; return those touched.

        Two exclusions, both deliberate:

        * **The lightmaps.** They are marked by :meth:`wire_lightmaps` and skipped —
          the caller sized them explicitly through the bake ``resolution``, so budgeting
          them here would silently deliver a 4096 bake at 2048 with nothing to show for it.
          (They are loaded from disk like any other image, so filepath alone does not tell
          them apart.) They are a rounding error in the file size regardless: 0.4 MB of a
          1.6 MB deliverable whose source PBR set was 96 MB.
        * **Images with no file backing.** The scale is destructive in-memory and disk is
          the only way back (see the ``reload`` in :meth:`export_glb`), so a generated
          image would be permanently altered for the rest of the session.
        """
        import bpy

        if not max_size:
            return []
        touched = []
        for image in bpy.data.images:
            size = tuple(image.size)
            width, height = size if len(size) == 2 else (0, 0)
            if not width or not height or max(width, height) <= max_size:
                continue
            if image.get(self.LIGHTMAP_IMAGE_MARKER):
                continue
            if not image.filepath:  # no file to reload from — never scale destructively
                continue
            scale = max_size / float(max(width, height))
            image.scale(max(1, int(width * scale)), max(1, int(height * scale)))
            touched.append(image)
            self.logger.info(
                "Downsized %s %dx%d -> %dx%d for the web deliverable.",
                image.name,
                width,
                height,
                *tuple(image.size),
            )
        return touched

    # ------------------------------------------------------------------ committed-state export

    @contextmanager
    def wired_for_export(
        self,
        objects=None,
        carrier: str = "occlusion",
        percentile: Optional[float] = None,
    ) -> Iterator[Optional[Dict[str, Any]]]:
        """The scene's COMMITTED lightmaps, wired for a native glTF export.

        The Blender-native counterpart of ``ptk.MeshConvert.apply_glb_lightmaps``: feeds
        itself from the markers :meth:`LightmapBaker.commit_lightmap` stamped (map basename
        + its ``dir`` locate hint), encodes, wires the carrier slot on the lightmap UV, and
        yields the ``lightmap_web`` manifest -- pass it to :meth:`export_glb`, or export
        with ``bpy.ops.export_scene.gltf(export_extras=True)`` directly (the manifest is
        also stamped on the scene for the duration, so either route carries it). Materials
        are restored on exit, wired only for the export's lifetime.

        A scene with no committed bake yields ``None`` and touches nothing, which is what
        makes it safe to wrap around *every* GLB export unconditionally: the exporter needs
        no knowledge of whether, or how, the scene was baked.
        """
        import bpy

        mapping: Dict[str, str] = {}
        for obj in objects or bpy.data.objects:
            obj = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
            if obj is None or LightmapBaker.LIGHTMAP_INFO_PROP not in obj:
                continue
            try:
                info = json.loads(obj[LightmapBaker.LIGHTMAP_INFO_PROP] or "{}")
            except ValueError:
                continue
            basename = info.get("map")
            if not basename:
                continue
            path = os.path.join(info.get("dir") or "", basename)
            if not os.path.isfile(path):
                self.logger.warning(
                    "%s: committed lightmap %r not found at %r; not shipped.",
                    obj.name,
                    basename,
                    path,
                )
                continue
            mapping[obj.name] = path

        if not mapping:
            yield None
            return

        # The PNGs are throwaway export intermediates -- never litter the maps' home.
        png_dir = ptk.TempArtifacts("lightmap_web").dir_path()
        encoded = self.encode_for_web(mapping, output_dir=png_dir, percentile=percentile)
        token = self.wire_lightmaps(encoded, carrier=carrier)
        manifest = self.build_manifest(encoded, carrier=carrier)

        scene = bpy.context.scene
        prior = scene.get(self.EXTRAS_KEY)
        scene[self.EXTRAS_KEY] = json.dumps(manifest)
        try:
            yield manifest
        finally:
            self.unwire_lightmaps(token)
            if prior is None:
                try:
                    del scene[self.EXTRAS_KEY]
                except KeyError:
                    pass
            else:
                scene[self.EXTRAS_KEY] = prior
