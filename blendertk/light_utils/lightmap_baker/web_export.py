# !/usr/bin/python
# coding=utf-8
"""Ship a committed lightmap bake in a web (GLB) deliverable.

**Export-time only.** The bake itself is
:class:`~blendertk.light_utils.lightmap_baker.lightmap_baker.LightmapBaker`'s job, and it is
platform-agnostic: it commits to the *scene* (markers + the ``lightmaps`` manifest
on the ``data_export`` carrier), which the Unity/FBX path and Maya round trip read without
this module. This module is the **web consumer** of that committed state -- what runs when a
GLB is exported from Blender natively:

* **Encode** -- :meth:`~LightmapWebExport.encode_for_web`: linear HDR EXR -> sRGB PNG.
  glTF carries only PNG/JPEG (KTX2 by extension), so the EXR cannot ship as-is.
* **Carry** -- :meth:`~LightmapWebExport.wire_lightmaps`: glTF has no lightmap slot, so the
  map rides a real texture slot on ``TEXCOORD_1`` and the viewer rebinds it.
* **Export** -- :meth:`~LightmapWebExport.export_glb` plus the ``lightmaps`` block the
  viewer reads (``ptk.MeshConvert.lightmaps_block``, the one builder of its shape), the
  written file then stated as every deliverable states itself
  (``ptk.MeshConvert.finish_glb``); :meth:`~LightmapWebExport.wired_for_export` wraps all
  of it around any native glTF export, feeding itself from the committed markers.
* **Probe** -- :meth:`~LightmapWebExport.embed_probe`: the bake's reflection probe
  (``LightmapBaker.bake_probe``), embedded in the written GLB as the manifest's
  ``probe``, through Blender's own Radiance writer.

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
by the map's peak, held between its 99.5th percentile and four times that
(``ptk.ImgUtils.encode_hdr_pixels_for_web``), records that divisor per map, and the viewer
multiplies it back through three.js's ``lightMapIntensity``. Dividing by the percentile
itself clipped lit surfaces, not light sources: a production room's backsplash and floor
pools went flat. Only what passes the cap clips, scaled whole so its hue survives.

The engine surface is Qt-free and defers ``import bpy`` (headless-importable).
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pythontk as ptk

from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
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

    #: The key the ``lightmaps`` block rides under: the lightmaps record's own
    #: (``ptk.SceneRecords.LIGHTMAPS.key``), as every GLB block takes its record's --
    #: a scene custom property the native exporter writes into the first scene's
    #: ``extras``, which ``ptk.MeshConvert.finish_glb`` moves into the file's root
    #: ``extras``, where every reader looks. Retired here: it restated the key.
    EXTRAS_KEY = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.key,
        "ptk.SceneRecords.LIGHTMAPS.key",
        remove_in="0.21.0",
        since="2026-10-24",
    )
    #: The block's schema version: the registry's, which its builder stamps.
    MANIFEST_VERSION = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.glb.version,
        "ptk.SceneRecords.LIGHTMAPS.glb.version",
        remove_in="0.21.0",
        since="2026-10-24",
    )

    #: Which real glTF texture slot carries the lightmap (see :meth:`wire_lightmaps`):
    #: the ones the block declares (``ptk.LightmapsBlock.CARRIERS``).
    CARRIERS: Tuple[str, ...] = ptk.LightmapsBlock.CARRIERS

    #: Custom property stamped on a wired lightmap image datablock, so the web texture
    #: budget can tell it apart from a source map (see :meth:`_downsize_images`).
    LIGHTMAP_IMAGE_MARKER: str = "btk_lightmap"

    #: The floor of the encode divisor, as a percentile of a map's lit values: pythontk's
    #: ``ImgUtils.HDR_WEB_PERCENTILE``, whose encode this is (see :meth:`encode_for_web`).
    DEFAULT_PERCENTILE: float = ptk.ImgUtils.HDR_WEB_PERCENTILE

    #: Name Blender's glTF exporter looks for to route a value into ``occlusionTexture``.
    _GLTF_OUTPUT_GROUP: str = "glTF Material Output"

    def __init__(
        self,
        baker: Optional[LightmapBaker] = None,
        resolution: int = 1024,
        samples: int = 128,
        denoise: bool = True,
        device: Optional[str] = "AUTO",
    ):
        super().__init__()
        # GPU wherever it pays, by default: a web deliverable is baked at production
        # sample counts, and the difference between a minutes-long and an hours-long
        # bake is the whole iteration loop -- while a tiny atlas tile finishes on the
        # CPU before a GPU session is even up (``TextureBaker.GPU_MIN_WORK``). Falls
        # back to the CPU with a warning when no compute device is available.
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
        PNG, not six). The pixels go through ``ptk.ImgUtils.encode_hdr_pixels_for_web``
        (the divisor is the map's peak, held between its :attr:`DEFAULT_PERCENTILE` and
        ``ptk.ImgUtils.HDR_WEB_HEADROOM`` times that; the hue-keeping clip; the dither),
        so a Blender map encodes as a cv2 one does; the divisor is returned so the viewer
        can multiply it back and recover the original intensity.

        Blender's save-time colour management is the trap here, and it is not symmetric:
        the destination image's colorspace must be set to ``Non-Color`` **before** its
        pixels are written. Setting it afterwards discards the buffer (the map saves pure
        black); leaving it unset makes Blender apply its own linear->sRGB on top of this
        one, double-encoding the map. Both were measured, not assumed.

        Parameters:
            mapping: ``{object_name: exr_path}`` from :meth:`LightmapBaker.bake_separated`
                (or the atlas paths from :meth:`LightmapBaker.pack_atlas`).
            output_dir: Destination. Defaults to a fresh ``TempArtifacts``
                folder (``lightmap_web``): the PNGs are export intermediates,
                never files beside the authored EXRs.
            percentile: Divide by this percentile of the lit values instead (the
                encode before 2026-10-17). Default: the peak, as above.
            suffix: Appended to the PNG stem.

        Returns:
            ``{object_name: (png_path, scalar)}`` -- multiply the sampled colour by
            ``scalar`` to recover linear intensity.
        """
        if not output_dir:
            output_dir = ptk.TempArtifacts("lightmap_web").dir_path()
        encoded: Dict[str, Tuple[str, float]] = {}
        done: Dict[str, Tuple[str, float]] = {}  # abspath -> result, encode once

        for name, src in mapping.items():
            key = os.path.abspath(src)
            if key in done:
                encoded[name] = done[key]
                continue
            if not os.path.isfile(src):
                continue

            stem = os.path.splitext(os.path.basename(src))[0] + suffix
            os.makedirs(output_dir, exist_ok=True)
            dst = os.path.join(output_dir, f"{stem}.png")
            scalar = cls._encode_png(key, dst, percentile)

            done[key] = (dst, scalar)
            encoded[name] = done[key]

        return encoded

    @classmethod
    def _encode_png(cls, src: str, dst: str, percentile: Optional[float]) -> float:
        """Encode the linear HDR image *src* as the sRGB PNG *dst*; its scalar.

        One map of :meth:`encode_for_web` (see there): Blender reads the
        pixels, ``ptk.ImgUtils.encode_hdr_pixels_for_web`` encodes them, and
        Blender saves the codes ``Non-Color`` -- set BEFORE the pixels.

        Raises:
            ValueError: Blender could not read *src*.
        """
        import bpy
        import numpy as np

        try:
            image = bpy.data.images.load(src, check_existing=False)
        except RuntimeError as e:
            raise ValueError(f"Unreadable image: {src} ({e})") from e
        try:
            width, height = image.size
            buf = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(buf)
            px = buf.reshape(-1, image.channels)
            # Quantized HERE (stochastic rounding, inside the pythontk encode):
            # Blender's own 8-bit save rounds to nearest, which terraced smooth
            # walls into contour bands; exact codes survive that save unchanged.
            codes, scalar = ptk.ImgUtils.encode_hdr_pixels_for_web(
                px[:, :3], percentile
            )
            out_rgb = codes / 255.0
        finally:
            bpy.data.images.remove(image)

        out = bpy.data.images.new(
            os.path.basename(dst), width=width, height=height, alpha=False
        )
        try:
            # BEFORE the pixel write -- see encode_for_web's docstring.
            out.colorspace_settings.name = "Non-Color"
            flat = np.ones((out_rgb.shape[0], 4), dtype=np.float32)
            flat[:, :3] = out_rgb
            out.pixels.foreach_set(flat.reshape(-1))
            out.filepath_raw = dst
            out.file_format = "PNG"
            out.save()
        finally:
            bpy.data.images.remove(out)
        return scalar

    @classmethod
    def encode_hdr_for_web(
        cls, path: str, percentile: Optional[float] = None
    ) -> Tuple[bytes, float]:
        """A linear HDR image as sRGB PNG bytes for the web, and its scalar.

        The twin of ``ptk.ImgUtils.encode_hdr_for_web`` -- the same encode
        (``ptk.ImgUtils.encode_hdr_pixels_for_web``; both suites pin it) --
        through Blender's own image I/O, so with :meth:`encode_hdr_radiance`
        this class is the HDR codec a GLB built in Blender hands the pipeline
        (``ptk.GlbPipeline.build(hdr_codec=)``): Blender's Python has no cv2,
        pythontk's only float-image codec, and without it a Scene Exporter GLB
        or a WebXR push of a baked scene shipped unlit (measured 2026-10-17).

        Parameters:
            path: An EXR/HDR file.
            percentile: See :meth:`encode_for_web` (default: the peak).

        Returns:
            ``(png_bytes, scalar)`` -- multiply the decoded colour by ``scalar``
            to recover linear intensity.

        Raises:
            ValueError: Blender could not read the file.
        """
        with ptk.TempArtifacts("lightmap_web_png", policy="scoped") as tmp:
            dst = os.path.join(tmp.dir_path(), "lightmap.png")
            scalar = cls._encode_png(path, dst, percentile)
            with open(dst, "rb") as fh:
                return fh.read(), scalar

    # ------------------------------------------------------------------ carry

    def wire_lightmaps(
        self,
        encoded: Dict[str, Tuple[str, float]],
        carrier: str = "occlusion",
        uv_set: Optional[str] = None,
        rects: Optional[Dict[str, List[float]]] = None,
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

        *rects* carries the atlas ``scaleOffset`` per object, and it is what makes an
        atlased bake correct. After atlas-by-material packing the MAP is a material-level
        property but the RECT is not: every object sharing the material owns a different
        window into the one image. Bound without it, each object samples the whole atlas
        through its own [0,1] unwrap and wears every other object's lighting -- which reads
        as blocky patches rather than as a missing map, so nothing downstream reports it.
        A non-identity rect therefore binds per OBJECT: the material is cloned, the clone
        gets a ``Mapping`` node ahead of the texture, and the clone rides an object-linked
        slot -- the one place per-instance data can live when linked duplicates share a
        mesh. Blender's exporter turns that node into a glTF ``KHR_texture_transform``
        (applying the bottom-up -> top-down V flip itself), so the deliverable matches
        ``ptk.MeshConvert.apply_glb_lightmaps``, which ships the same rect the same way.

        The same per-object binding applies with no rect at all, whenever a material is
        shared by objects baked into DIFFERENT maps -- a secondary material on two baked
        bodies, or a Per-Object bake of linked duplicates. A glTF material carries one
        lightmap: the first object keeps the material, each later one binds a clone.
        And whenever an object this wiring does not light -- one the bake never saw --
        wears the material too: every baked object then binds a clone, and the material
        stays as authored for the rest, which would otherwise export lit by another
        object's bake.

        Returns a restore token for :meth:`unwire_lightmaps`; the wiring is a transport
        detail, not a change the artist asked for.
        """
        import bpy

        if carrier not in self.CARRIERS:
            raise ValueError(f"carrier must be one of {self.CARRIERS}, got {carrier!r}")

        token: Dict[str, Any] = {
            "carrier": carrier,
            "materials": [],
            "slots": [],
            "copies": [],
        }
        wired: set = set()
        # One clone per distinct (material, map, rect): instances that happen to
        # share a rect share a clone, and a scene with no atlas makes none at all.
        clones: Dict[tuple, Any] = {}

        def not_wired(material_name, error):
            """A transport failure must not lose the bake -- say so and carry on."""
            self.logger.warning(
                "Lightmap not wired into %s (%s); it still ships as a file.",
                material_name,
                error,
            )

        def window(rect):
            """*rect* as this object's window into the map, or None for the whole map."""
            if rect is None:
                return None
            rect = [float(v) for v in rect]
            return None if rect == list(LightmapBaker._IDENTITY_SCALE_OFFSET) else rect

        windows = {name: window((rects or {}).get(name)) for name in encoded}
        # A glTF material carries ONE lightmap, so the source material carries in
        # place only the map of the first object to claim it without a rect. Any
        # other binding is that object's alone and rides a clone: a per-instance
        # rect, or a DIFFERENT map on a material another object already claimed --
        # a secondary material two baked objects share (one GLASS on two machine
        # bodies), or a Per-Object bake of linked duplicates (one mesh, one
        # material behind every instance). Wired in place, those later objects
        # sampled the first one's map through their own UV2. The twin of
        # ``ptk.MeshConvert.apply_glb_lightmaps``, which binds the same way.
        in_place: Dict[str, str] = {}  # material -> the map it carries in place
        # Materials an object this wiring does not light also wears. Wired in
        # place, such a material exported that object lit by a baked one's map
        # (through its own UV2, or one texel of it with none) -- the applier's
        # ``unlit_users`` rule. By NAME: bpy hands out a fresh wrapper per
        # access, so an identity test never matches.
        lit = set(encoded)
        shared_with_unlit = {
            slot.material.name
            for other in bpy.data.objects
            if other.name not in lit
            for slot in getattr(other, "material_slots", []) or []
            if slot.material is not None
        }
        bindings = []
        for name, (png, _scalar) in encoded.items():
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            lm = uv_set or UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            rect = windows[name]
            for index, slot in enumerate(getattr(obj, "material_slots", []) or []):
                material = slot.material
                if material is None:
                    continue
                own = (
                    rect is not None
                    or material.name in shared_with_unlit
                    or in_place.setdefault(material.name, png) != png
                )
                bindings.append((obj, index, slot, material, png, lm, rect, own))

        # Every clone BEFORE any in-place wiring, because a clone has to be taken
        # from a PRISTINE material: wired in place first, the copy carries that
        # wiring too -- two lightmap textures and two glTF-output groups, of which
        # the exporter picks one arbitrarily (measured: an atlased object shipped
        # the UNTRANSFORMED binding, the very bug the rect is here to fix).
        # sorted() is stable, so objects keep their order within each pass.
        try:
            for obj, index, slot, material, png, lm, rect, own in sorted(
                bindings, key=lambda binding: not binding[-1]
            ):
                if not own:
                    if material.name in wired:
                        continue
                    try:
                        added = self._wire_material(material, png, lm, carrier)
                    except Exception as error:
                        not_wired(material.name, error)
                        continue
                    wired.add(material.name)
                    token["materials"].append(
                        {"material": material.name, "nodes": added}
                    )
                    continue

                window_key = tuple(float(v) for v in rect) if rect is not None else None
                key = (material.name, png, window_key)
                clone = clones.get(key)
                if clone is None:
                    clone = material.copy()
                    clone.name = f"{material.name}~lm{len(clones)}"
                    try:
                        self._wire_material(clone, png, lm, carrier, rect=rect)
                    except Exception as error:
                        not_wired(material.name, error)
                        bpy.data.materials.remove(clone)
                        continue
                    clones[key] = clone
                    token["copies"].append(clone.name)
                # A slot holds BOTH a data-level and an object-level material and
                # the link only says which one is live, so the object-level one
                # has to be read THROUGH the link we are about to take over --
                # reading it as `slot.material` under a DATA link returns the
                # mesh's and restore then clears a material the object was
                # carrying (measured).
                prior_link = slot.link
                slot.link = "OBJECT"
                prior = slot.material
                token["slots"].append(
                    {
                        "object": obj.name,
                        "index": index,
                        "link": prior_link,
                        "material": prior.name if prior is not None else None,
                    }
                )
                slot.material = clone
        except BaseException:
            # Past the per-material guard (a slot that refuses the re-link, an
            # interrupt): this runs on the artist's live scene, so the clones
            # and slot overrides made so far go back before the error does.
            self.unwire_lightmaps(token)
            raise

        return token

    def _wire_material(
        self,
        material,
        png: str,
        uv_name: str,
        carrier: str,
        rect: Optional[List[float]] = None,
    ) -> List[str]:
        """Add the lightmap image + UVMap nodes to *material*; return the node names added.

        *rect* is an atlas ``scaleOffset``: with one, a ``Mapping`` node between the UV and
        the texture narrows the sample to that object's window (see :meth:`wire_lightmaps`).
        """
        import bpy

        # Through the one ``use_nodes`` gate: on 4.x a material can carry a tree
        # while nodes are off, and both the renderer and the glTF exporter ignore
        # it there -- the lightmap nodes would land in a tree nothing reads.
        from blendertk.mat_utils._mat_utils import MatUtils

        nt = MatUtils.get_node_tree(material, create=True)

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
        added = [tex.name, uvmap.name]
        if rect is None:
            nt.links.new(uvmap.outputs["UV"], tex.inputs["Vector"])
        else:
            # POINT: uv' = uv * scale + location -- the scaleOffset convention
            # exactly (Unity lightmapScaleOffset), so the rect needs no rewriting
            # here and no V flip: Blender's glTF exporter converts this node to
            # KHR_texture_transform and flips V on the way out (measured).
            sx, sy, ox, oy = (float(v) for v in rect)
            mapping = nt.nodes.new("ShaderNodeMapping")
            mapping.vector_type = "POINT"
            mapping.inputs["Scale"].default_value = (sx, sy, 1.0)
            mapping.inputs["Location"].default_value = (ox, oy, 0.0)
            nt.links.new(uvmap.outputs["UV"], mapping.inputs["Vector"])
            nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            added.append(mapping.name)

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
        """Undo :meth:`wire_lightmaps`: restore the slots, drop the clones, unwire the rest.

        Ordered that way because an object-linked slot holds a user count on the clone it
        points at, so the datablock cannot be freed while the slot still names it.
        """
        import bpy

        restored: List[str] = []
        # Slots first: an object-linked override holds a user count on the clone,
        # so the datablock cannot be freed while the slot still points at it.
        for record in (token or {}).get("slots", []):
            obj = bpy.data.objects.get(record.get("object", ""))
            if obj is None:
                continue
            index = record.get("index", 0)
            if index >= len(obj.material_slots):
                continue
            slot = obj.material_slots[index]
            slot.link = "OBJECT"
            slot.material = bpy.data.materials.get(record.get("material") or "")
            slot.link = record.get("link", "DATA")
        for name in (token or {}).get("copies", []):
            clone = bpy.data.materials.get(name)
            if clone is not None:
                bpy.data.materials.remove(clone)
        for record in (token or {}).get("materials", []):
            material = bpy.data.materials.get(record.get("material", ""))
            if (
                material is None or material.node_tree is None
            ):  # not use_nodes: deprecated
                continue
            nt = material.node_tree
            for node_name in record.get("nodes", []):
                node = nt.nodes.get(node_name)
                if node is not None:
                    nt.nodes.remove(node)
            restored.append(material.name)
        return restored

    # ------------------------------------------------------------------ export

    @ptk.Deprecation.parameter(
        "lighting",
        drop=True,
        remove_in="0.21.0",
        since="2026-10-24",
        reason="The block states no lighting: the file's handoff states the lighting "
        "it was approved under.",
    )
    def build_manifest(
        self,
        encoded: Dict[str, Tuple[str, float]],
        carrier: str,
    ) -> Dict[str, Any]:
        """The ``lightmaps`` block the viewer reads to rebind the carrier slot, built by
        ``ptk.MeshConvert.lightmaps_block`` -- the one statement of its shape, whichever
        exporter writes the file.

        Keyed by *material* rather than object, because glTF materials are what the viewer
        actually walks. After atlas consolidation the MAP is material-level but the per-
        instance RECT is not, so an atlased object appears here under the name of the clone
        :meth:`wire_lightmaps` gave it -- which is also the name the GLB carries. Each
        material states its intensity alone: its map is the texture bound in its *carrier*
        slot, read on the UV set that texture's ``texCoord`` names -- which the file states.
        """
        import bpy

        maps: Dict[str, str] = {}
        intensities: Dict[str, float] = {}
        for name, (png, scalar) in encoded.items():
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            for slot in getattr(obj, "material_slots", []) or []:
                if slot.material is None:
                    continue
                material, map_name = slot.material.name, os.path.basename(png)
                claimed = maps.setdefault(material, map_name)
                if claimed == map_name:
                    intensities.setdefault(material, scalar)
                    continue
                # A glTF material carries exactly one lightmap, so the second one has
                # nowhere to go. wire_lightmaps gives every map past a material's first
                # a per-object clone, so reaching here means that clone could not be
                # wired (logged there) or the manifest was built without the wiring.
                # Say so — the symptom is one object wearing another's lighting, which
                # looks like a bad bake rather than a dropped map.
                self.logger.warning(
                    "Material %r already carries %s, so %s (from %s) cannot be "
                    "published — that object will sample the first map. Re-run with "
                    "atlas packing, or split the material.",
                    material,
                    claimed,
                    map_name,
                    name,
                )
        return ptk.MeshConvert.lightmaps_block(intensities, carrier)

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
        lightmap. *manifest* rides the scene's custom properties into the first scene's
        ``extras``, so the deliverable is self-describing. The written file is then
        stated as every deliverable states itself (``ptk.MeshConvert.finish_glb``): the
        block moves to its root ``extras`` as an object, the handoff its carrier's record
        implies is embedded (when the carrier ships: the whole scene, or *objects* naming
        it), and no record is left on a node -- the lightmap markers and manifest the
        exporter wrote as the scene holds them included, so the file states its lightmaps
        once. Last, the scene's reflection probe joins the block (:meth:`embed_probe`),
        which every reader finds in the root ``extras``.

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

        from blendertk.core_utils._core_utils import CoreUtils

        path = path if path.lower().endswith(".glb") else path + ".glb"
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

        scene = bpy.context.scene
        prior = scene.get(ptk.SceneRecords.LIGHTMAPS.key)
        if manifest is not None:
            scene[ptk.SceneRecords.LIGHTMAPS.key] = json.dumps(manifest)

        # The window's view layer + context: windowless, select_all / select_set address
        # the scene's default layer, and the glTF exporter raised outright (measured,
        # use_selection on 5.1). The selection and active object come back after, as
        # FbxUtils.export / UsdUtils.export put theirs back.
        with CoreUtils.window_context_override(), CoreUtils.preserved_selection():
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
                        del scene[ptk.SceneRecords.LIGHTMAPS.key]
                    else:
                        scene[ptk.SceneRecords.LIGHTMAPS.key] = prior
        # Finished as the FBX route's build finishes its GLB -- the block lifted
        # into the root extras, the records and markers (written as the scene
        # holds them: the EXR at 1.0 beside its authoring folder) off the nodes
        # -- and only then the probe, into the block where readers look.
        ptk.MeshConvert.finish_glb(path)
        self.embed_probe(path)
        return path

    # ------------------------------------------------------------------ probe

    def embed_probe(self, glb_path: str) -> Optional[Dict[str, Any]]:
        """Embed the file's reflection probe in a natively exported GLB; its entry, or ``None``.

        The probe is the room the bake lit, as an HDR
        (``LightmapBaker.bake_probe``): what the lightmaps cannot hold -- the
        reflections on every baked surface, which are a metal's whole look,
        and the light on everything unbaked. The viewer reads it from the
        ``lightmaps`` manifest's ``probe`` (pythontk's ``kernel/probe.js``),
        as Radiance bytes in a view of the file's own buffer -- which exists
        only once the export has written the file, so this runs after it
        (``ptk.MeshConvert.embed_glb_lightmap_probe``; the FBX route's
        ``apply_glb_lightmaps`` embeds it the same way as it binds). The file
        is found where the scene's records say
        (:meth:`LightmapRecords.search_dirs`) and encoded by Blender's own
        Radiance writer (:meth:`encode_hdr_radiance`): Blender ships no cv2.

        A file with no ``lightmaps`` (nothing wired), or a scene with no
        probe, is left as found; one that carries the probe already keeps it.
        The point and box are read at the native exporter's unit -- one Blender
        unit, one metre -- not the record's ``unit_scale``, which is the FBX
        route's: Blender's glTF exporter ignores ``scale_length``.

        Parameters:
            glb_path: The GLB the native export wrote (modified in place).

        Returns:
            The ``lightmaps`` probe entry, or ``None``.
        """
        probe = LightmapRecords.probe()
        if not probe or not glb_path or not os.path.isfile(glb_path):
            return None
        # In the native GLB's own units: Blender's glTF exporter writes one
        # Blender unit as one metre whatever the scene's scale_length
        # (measured 2026-10-17: a 1 BU = 1 cm scene's node at 100 BU wrote
        # 100), so the record's unit_scale -- right for the FBX route, which
        # honours it -- put a centimetre scene's probe 100x too near the origin.
        probe = dict(probe, unit_scale=1.0)
        return ptk.MeshConvert.embed_glb_lightmap_probe(
            glb_path,
            probe,
            LightmapRecords.search_dirs(),
            hdr_codec=type(self),
        )

    @staticmethod
    def encode_hdr_radiance(path: str) -> bytes:
        """An HDR image file (EXR/HDR) as Radiance ``.hdr`` bytes, by Blender's own writer.

        The twin of ``ptk.ImgUtils.encode_hdr_radiance``, which reads through
        cv2 -- absent from Blender's Python; with :meth:`encode_hdr_for_web`
        this class is the HDR codec a GLB built in Blender hands the pipeline.
        The pixels stay as stored (a float image is written linear, no view
        transform); the rows run top-down (``-Y h +X w``), as every Radiance
        reader -- three.js' ``RGBELoader`` included -- expects.

        Raises:
            ValueError: Blender could not read the file or write the HDR.
        """
        import bpy

        try:
            image = bpy.data.images.load(path, check_existing=False)
        except RuntimeError as e:
            raise ValueError(f"Unreadable image: {path} ({e})") from e
        try:
            # A loaded image reads its file lazily: repointed first, it saves
            # "no image data". Its pixels are read here, from *path*.
            if not len(image.pixels) or not image.has_data:
                raise ValueError(f"Unreadable image: {path}")
            with ptk.TempArtifacts("probe_radiance", policy="scoped") as tmp:
                out = os.path.join(tmp.dir_path(), "probe.hdr")
                image.filepath_raw = out
                image.file_format = "HDR"
                try:
                    image.save()
                except RuntimeError as e:
                    raise ValueError(f"Radiance HDR encode failed for {path}") from e
                with open(out, "rb") as fh:
                    return fh.read()
        finally:
            bpy.data.images.remove(image)

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
        glb_path: Optional[str] = None,
    ) -> Iterator[Optional[Dict[str, Any]]]:
        """The scene's COMMITTED lightmaps, wired for a native glTF export.

        The Blender-native counterpart of ``ptk.MeshConvert.apply_glb_lightmaps``: feeds
        itself from the markers :meth:`LightmapRecords.commit` stamped (the map's
        basename; its folder from the scene's lightmap record), encodes, wires the carrier
        slot on the lightmap UV, and yields the ``lightmaps`` manifest -- pass it to
        :meth:`export_glb`, or export with ``bpy.ops.export_scene.gltf(export_extras=True)``
        directly (the manifest is also stamped on the scene for the duration, so either
        route carries it). Materials are restored on every exit -- a manifest that cannot
        be built included -- wired only for the export's lifetime.

        A scene with no committed bake yields ``None`` and touches nothing in the SCENE,
        which is what makes it safe to wrap around *every* GLB export unconditionally: the
        exporter needs no knowledge of whether, or how, the scene was baked.

        *glb_path* is the GLB the block writes, and on a clean exit that file is always
        stated as every deliverable states itself (``ptk.MeshConvert.finish_glb``: its
        blocks in its root ``extras``, the handoff its carrier's record implies, no record
        on a node) -- baked or not: an unbaked scene's records ride its carrier too.
        Baked, the records it strips include each object's marker and the data_export
        manifest, which Blender's exporter ships as the scene holds them (the EXR at
        1.0 beside its authoring folder), so the file states its lightmaps once, in its
        ``lightmaps`` block -- which is then given the scene's reflection probe
        (:meth:`embed_probe`). :meth:`export_glb` does this for its own file; a caller
        exporting directly passes *glb_path*. An export that raises inside the block
        finishes nothing.
        """
        import bpy

        mapping: Dict[str, str] = {}
        rects: Dict[str, List[float]] = {}
        # Where each map is NOW, by the record's own resolution -- the map's
        # recorded folder, then the host's search folders, then a walk -- the rule
        # the Scene Exporter's GLB build and its path check already use. Joining
        # the recorded folder alone shipped an object unlit the moment that
        # folder went stale (a moved project, a renamed folder).
        located = {
            str(dep["map"]).lower(): dep["path"]
            for dep in LightmapRecords.lightmap_dependencies(objects)
            if dep.get("path")
        }
        hints = LightmapRecords._folder_hints()
        libraries: Dict[int, Dict[str, str]] = {}  # a linked library's own record
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
            # The recorded folder is in Blender's portable '//'-relative
            # spelling, which os.path.join cannot resolve -- joined raw it never
            # names a real file, so every committed lightmap was dropped and the
            # GLB shipped unlit with no TEXCOORD_1 carrier. The resolved location
            # first; the map's recorded folder through _resolved_dir otherwise.
            path = located.get(str(basename).lower()) or os.path.join(
                LightmapRecords._resolved_dir(
                    LightmapRecords._folder_hint(info, hints, obj, libraries), basename
                ),
                basename,
            )
            if not os.path.isfile(path):
                self.logger.warning(
                    "%s: committed lightmap %r not found at %r; not shipped.",
                    obj.name,
                    basename,
                    path,
                )
                continue
            mapping[obj.name] = path
            # The per-instance window into an atlased map. Dropping it is not a
            # degradation but a wrong bind: the object then samples the whole
            # atlas (see :meth:`wire_lightmaps`).
            scale_offset = info.get("scaleOffset")
            if scale_offset:
                rects[obj.name] = [float(v) for v in scale_offset]

        if not mapping:
            yield None
            # Reached only on a clean exit (a raising export ends the generator at
            # the yield), as in the baked branch: the scene is untouched, and the
            # file is finished all the same -- its carrier ships records too.
            if glb_path and os.path.isfile(glb_path):
                ptk.MeshConvert.finish_glb(glb_path)
            return

        # The PNGs are throwaway export intermediates -- never litter the maps'
        # home -- read by the export inside this bracket and released with it.
        png_store = ptk.TempArtifacts.scoped("lightmap_web")
        png_dir = png_store.dir_path()
        encoded = self.encode_for_web(
            mapping, output_dir=png_dir, percentile=percentile
        )
        scene = bpy.context.scene
        prior = scene.get(ptk.SceneRecords.LIGHTMAPS.key)
        token = self.wire_lightmaps(encoded, carrier=carrier, rects=rects)
        # Everything after the wiring is inside the bracket that takes it back.
        try:
            manifest = self.build_manifest(encoded, carrier=carrier)
            scene[ptk.SceneRecords.LIGHTMAPS.key] = json.dumps(manifest)
            yield manifest
            if glb_path and os.path.isfile(glb_path):
                ptk.MeshConvert.finish_glb(glb_path)
                self.embed_probe(glb_path)
        finally:
            self.unwire_lightmaps(token)
            if prior is None:
                try:
                    del scene[ptk.SceneRecords.LIGHTMAPS.key]
                except KeyError:
                    pass
            else:
                scene[ptk.SceneRecords.LIGHTMAPS.key] = prior
            png_store.cleanup()
