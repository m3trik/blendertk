# !/usr/bin/python
# coding=utf-8
"""Ship a committed lightmap bake in a web (GLB) deliverable.

**Export-time only.** The bake itself is
:class:`~blendertk.light_utils.lightmap_baker.lightmap_baker.LightmapBaker`'s job, and it is
platform-agnostic: it commits to the *scene* (markers + the ``lightmaps`` record
on the ``data_export`` carrier), which the Unity/FBX path and Maya round trip read without
this module. This module is the **web consumer** of that committed state -- what runs when a
GLB is exported from Blender natively. One layout, two containers: a native GLB carries
what an FBX-built one does --

* **The record** -- :meth:`~LightmapWebExport.wired_for_export` stamps the scene's
  ``lightmaps`` record (``LightmapRecords._record``, its lengths labelled in the native
  writer's unit) on the scene for the export, which writes it into the first scene's
  ``extras``; ``ptk.MeshConvert.finish_glb`` lifts it into the file's root ``extras`` as it
  stands, and takes every record and marker off the nodes.
* **Its files** -- :meth:`~LightmapWebExport.embed_media`: each map the record lights an
  object of the file with rides it as the bufferView named by its file
  (``ptk.SceneRecords.GLB_MEDIA``: 8-bit sRGB PNG whose decoded texels times
  ``extras.scale`` are the map), the reflection probe (``LightmapBaker.bake_probe``) as
  Radiance -- through ``ptk.MeshConvert.apply_glb_lightmaps``, the FBX route's own pass,
  with Blender's image I/O as its codec (:meth:`encode_hdr_for_web`,
  :meth:`encode_hdr_radiance`): Blender's Python ships no cv2.

No material is wired, cloned or rebound: the second UV map reaches the file on its own
(Blender's glTF exporter writes every UV layer as a ``TEXCOORD_n``; measured on 5.1.2: a
second layer no texture reads ships as ``TEXCOORD_1``), and a reader binds each map from the
record to the object it names (pythontk's WebXR viewer, on a material copy of its own made at
load). The Maya-side GLB converts through ``ptk.MeshConvert.fbx_to_glb``, which runs the same
pass. One committed bake, N consumers, none aware of each other.

**A lightmap's dynamic range does not fit in a PNG.** A real interior bake measured
0 -> 129.8 with a mean of 0.33 (emissive fixtures against a dim room), so clamping at 1.0
blows out the fixtures and crushes everything else. :meth:`encode_hdr_for_web` instead
divides by the map's peak, held between its 99.5th percentile and four times that
(``ptk.ImgUtils.encode_hdr_pixels_for_web``), and the divisor rides the map's view as its
``extras.scale``, which the viewer multiplies back through three.js's ``lightMapIntensity``.
Only what passes the cap clips, scaled whole so its hue survives.

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


class LightmapWebExport(ptk.LoggingMixin):
    """Ship a scene's committed lightmaps in a natively-exported GLB.

    Composes :class:`LightmapBaker` rather than extending it: the bake is a DCC concern the
    Unity/FBX path shares, while everything here is specific to shipping a browser
    deliverable. Usage (a scene whose lightmaps are already committed)::

        web = LightmapWebExport()
        with web.wired_for_export() as record:
            web.export_glb("C:/out/office.glb", manifest=record)
    """

    #: The key the ``lightmaps`` record rides under: the record's own
    #: (``ptk.SceneRecords.LIGHTMAPS.key``), a scene custom property the native exporter
    #: writes into the first scene's ``extras``, which ``ptk.MeshConvert.finish_glb``
    #: moves into the file's root ``extras``, where every reader looks. Retired here: it
    #: restated the key.
    EXTRAS_KEY = ptk.Deprecation.class_attribute(
        ptk.SceneRecords.LIGHTMAPS.key,
        "ptk.SceneRecords.LIGHTMAPS.key",
        remove_in="0.21.0",
        since="2026-10-24",
    )
    #: Held until 0.25.0 (``blendertk/__init__.py``, "Held until 0.25.0"): the
    #: retired ``lightmaps`` block's schema version, which :meth:`build_manifest`
    #: stamps. A GLB carries the record as it stands (:meth:`native_record`).
    MANIFEST_VERSION = ptk.Deprecation.class_attribute(
        2,
        "ptk.SceneRecords.LIGHTMAPS.version -- a GLB carries the record as it stands",
        remove_in="0.21.0",
        since="2026-10-24",
    )

    #: The floor of the encode divisor, as a percentile of a map's lit values: pythontk's
    #: ``ImgUtils.HDR_WEB_PERCENTILE``, whose encode this is (see :meth:`encode_for_web`).
    DEFAULT_PERCENTILE: float = ptk.ImgUtils.HDR_WEB_PERCENTILE

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
        (``ptk.MeshConvert.apply_glb_lightmaps(hdr_codec=)``, ``ptk.GlbPipeline.build``):
        Blender's Python has no cv2, pythontk's only float-image codec, and without it a
        Scene Exporter GLB or a WebXR push of a baked scene shipped unlit (measured
        2026-10-17).

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

    # ------------------------------------------------------------------ the record

    @staticmethod
    def native_record(objects=None) -> Optional[Dict[str, Any]]:
        """The scene's ``lightmaps`` record as a native GLB carries it, or ``None``.

        ``LightmapRecords._record`` -- the FBX route's record, entry for entry --
        its lengths labelled in the native glTF writer's unit
        (``ptk.ExportContext.writer_unit``: Blender's glTF exporter writes one
        Blender unit as one metre whatever the scene's ``scale_length``; the
        FBX route's record carries the scene's, which that writer honours).
        ``None`` for a scene with no committed bake, and -- warned with its fix
        -- for one whose record is refused (a lightmap layer off the second UV
        map, ``ptk.LightmapRecord.UvSetRefused``).

        Parameters:
            objects: Scope it to these objects (``None``: the file), as a
                scoped export narrows the record.
        """
        from blendertk.core_utils.units import Units

        unit = ptk.ExportContext.writer_unit(
            "blender", "gltf", Units.metres_per("internal")
        )
        record = ptk.LightmapRecord.unless_refused(
            lambda: LightmapRecords._record(objects, unit_scale=unit),
            LightmapRecords.logger,
        )
        return dict(record.payload) if record is not None else None

    # ------------------------------------------------------------------ export

    # Held until 0.25.0 (``blendertk/__init__.py``, "Held until 0.25.0"): the
    # retired block builder, as v0.23.1 had it, for its ``lighting`` notice.
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
        """Retired 2026-11-07 (removed after 0.23.x): a GLB carries the
        ``lightmaps`` record as it stands (:meth:`native_record`).

        The retired ``lightmaps`` block, as v0.23.1 built it: each lightmapped
        material's intensity -- its objects' in *encoded* (``{object: (map,
        intensity)}``), the first map a material carries -- under the *carrier*
        slot, at :attr:`MANIFEST_VERSION`."""
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
                self.logger.warning(
                    "Material %r already carries %s, so %s (from %s) cannot be "
                    "published -- that object will sample the first map.",
                    material,
                    claimed,
                    map_name,
                    name,
                )
        return {
            "version": 2,
            "carrier": carrier,
            "materials": {
                name: {"intensity": round(float(level), 6)}
                for name, level in intensities.items()
            },
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
        no second translation, and every UV map ships (the lightmaps' second as
        ``TEXCOORD_1``). *manifest* -- the ``lightmaps`` record :meth:`wired_for_export`
        yields -- rides the scene's custom properties into the first scene's ``extras``.
        The written file is then stated as every deliverable states itself
        (``ptk.MeshConvert.finish_glb``): the record moves to its root ``extras`` as it
        stands, the handoff its carrier's record implies is embedded (when the carrier
        ships: the whole scene, or *objects* naming it), and no record is left on a node --
        the lightmap markers the exporter wrote as the scene holds them included. Last,
        the files the record names ride it (:meth:`embed_media`).

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
                in-memory). ``None`` keeps authored resolution. The lightmaps are no
                image of the export's: they ride as the record's media, at the size the
                bake wrote them.
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
        # Finished as the FBX route's build finishes its GLB -- the record lifted
        # into the root extras, the records and markers off the nodes -- and only
        # then the files it names, which ride the written file's own buffer.
        ptk.MeshConvert.finish_glb(path)
        self.embed_media(path, objects)
        return path

    def embed_media(
        self, glb_path: str, objects=None, percentile: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Carry the files a native GLB's ``lightmaps`` record names into it.

        ``ptk.MeshConvert.apply_glb_lightmaps`` -- the FBX route's own pass -- on
        the written file, with Blender's image I/O as its codec (this class: its
        Python ships no cv2): each map the record lights an object of the file
        with rides as the bufferView named by its file (8-bit sRGB PNG, its
        range as ``extras.scale``), the bake's reflection probe as Radiance, and
        the record stays in the root ``extras`` as it stands. A native GLB and an
        FBX-built one so carry the same layout. The files are found where the
        scene's records say (:meth:`LightmapRecords.lightmap_dependencies`'
        locations first, then :meth:`LightmapRecords.search_dirs`). The lights
        a native export wrote over the bake (``include_lights``:
        ``KHR_lights_punctual``) then leave it, as they leave an FBX-built one
        (``ptk.MeshConvert.strip_glb_baked_lights``): a reader that does not
        know they are baked would light the scene twice. A file with no
        record, or a path that is not there, is left as found; a second run
        adds nothing.

        Parameters:
            glb_path: The GLB the native export wrote (modified in place).
            objects: The export's objects, which scope where the maps are
                looked for (``None``: the file's).
            percentile: See :meth:`encode_for_web` (default: the peak).

        Returns:
            ``apply_glb_lightmaps``' bindings (one per object the record lights
            here); ``[]`` for nothing carried.
        """
        if not glb_path or not os.path.isfile(glb_path):
            return []
        dirs: List[str] = []
        for dep in LightmapRecords.lightmap_dependencies(objects):
            folder = os.path.dirname(dep["path"]) if dep.get("path") else None
            if folder and folder not in dirs:
                dirs.append(folder)
        for folder in LightmapRecords.search_dirs(objects):
            if folder and folder not in dirs:
                dirs.append(folder)
        bound = ptk.MeshConvert.apply_glb_lightmaps(
            glb_path, search_dirs=dirs, percentile=percentile, hdr_codec=type(self)
        )
        ptk.MeshConvert.strip_glb_baked_lights(glb_path)
        return bound

    def _downsize_images(self, max_size: Optional[int]) -> List[Any]:
        """Scale every source image whose longest edge exceeds *max_size*; return those touched.

        An image with no file backing is left alone: the scale is destructive in-memory
        and disk is the only way back (see the ``reload`` in :meth:`export_glb`), so a
        generated image would be permanently altered for the rest of the session. The
        lightmaps are never among them: they ride as the record's media, not as images
        of the export.
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

    @ptk.Deprecation.parameter(
        "carrier",
        drop=True,
        remove_in="0.25.0",
        since="2026-11-07",
        reason="No material slot carries a lightmap: the GLB carries the record and "
        "each map as the bufferView of its name.",
    )
    @contextmanager
    def wired_for_export(
        self,
        objects=None,
        *,
        percentile: Optional[float] = None,
        glb_path: Optional[str] = None,
    ) -> Iterator[Optional[Dict[str, Any]]]:
        """The scene's COMMITTED lightmaps, stated for a native glTF export.

        The Blender-native counterpart of ``ptk.MeshConvert.fbx_to_glb``'s lightmap
        pass: feeds itself from the markers :meth:`LightmapRecords.commit` stamped,
        stamps the scene's ``lightmaps`` record (:meth:`native_record`) on the scene for
        the duration -- the native exporter writes it into the first scene's ``extras``
        -- and yields it: pass it to :meth:`export_glb`, or export with
        ``bpy.ops.export_scene.gltf(export_extras=True)`` directly. Nothing in the scene
        is wired or copied: the record and the UV maps are all a native export needs.

        A scene with no committed bake yields ``None`` and touches nothing in the SCENE,
        which is what makes it safe to wrap around *every* GLB export unconditionally: the
        exporter needs no knowledge of whether, or how, the scene was baked.

        *glb_path* is the GLB the block writes, and on a clean exit that file is always
        stated as every deliverable states itself (``ptk.MeshConvert.finish_glb``: its
        records in its root ``extras``, the handoff its carrier's record implies, no
        record on a node) -- baked or not: an unbaked scene's records ride its carrier
        too. Baked, the files the record names then ride it (:meth:`embed_media`).
        :meth:`export_glb` does this for its own file; a caller exporting directly passes
        *glb_path*. An export that raises inside the block finishes nothing.

        Parameters:
            objects: Scope the record to these objects (``None``: the file).
            percentile: See :meth:`encode_for_web` (default: the peak).
            glb_path: The GLB the block's export writes, finished on a clean exit.
        """
        import bpy

        record = self.native_record(objects)
        if record is None:
            yield None
            # Reached only on a clean exit (a raising export ends the generator at
            # the yield), as in the baked branch: the scene is untouched, and the
            # file is finished all the same -- its carrier ships records too.
            if glb_path and os.path.isfile(glb_path):
                ptk.MeshConvert.finish_glb(glb_path)
            return

        scene = bpy.context.scene
        key = ptk.SceneRecords.LIGHTMAPS.key
        prior = scene.get(key)
        scene[key] = json.dumps(record)
        try:
            yield record
            if glb_path and os.path.isfile(glb_path):
                ptk.MeshConvert.finish_glb(glb_path)
                self.embed_media(glb_path, objects, percentile)
        finally:
            if prior is None:
                try:
                    del scene[key]
                except KeyError:
                    pass
            else:
                scene[key] = prior
