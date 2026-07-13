# !/usr/bin/python
# coding=utf-8
"""High-level lightmap baking workflow for Blender -> game engines (Unity-first).

Blender counterpart of mayatk's ``LightmapBaker``. Where the Maya workflow had to
orchestrate Arnold RTT, an alpha-mask seam dilation and a white-card material swap,
**Blender ships the whole bake natively in Cycles** — so this is a much thinner adapter
over ``bpy.ops.object.bake``:

* :func:`UvUtils.create_lightmap_uvs` -- packed, non-overlapping lightmap UV (UV2).
* ``bpy.ops.object.bake`` -- Cycles bakes straight into an image-texture node:
    * **Lighting only** = ``type='DIFFUSE'`` with ``pass_filter={'DIRECT','INDIRECT'}``
      (no ``'COLOR'``) — the *native* white-card irradiance, no material swap.
    * **Fused** = ``type='COMBINED'`` (albedo x lighting).
* ``scene.render.bake.margin`` -- native gutter/seam padding (no ``dilate_image`` needed).
* ``DataNodes.set_export_string`` -- the export manifest (custom prop on the ``data_export``
  Empty, rides the FBX; no sidecar file). Informational -- the mesh's UV2 samples the map in
  any engine; unitytk's optional editor helper reads it to auto-bind Unity's native slots.

Two bake levels, both non-destructive and exposed in the panel:

* **Lighting only** (default) -- :meth:`bake_separated` bakes lighting-only irradiance onto
  the lightmap UV (channel 1) and :meth:`commit_lightmap` records it. The object's full PBR
  material and texture UV0 are **kept untouched** -- the engine composites
  ``albedo x lightmap``. Reversible via :meth:`revert_lightmap`.
* **Fused** -- :meth:`bake_fused` bakes albedo x lighting into one HDR map and
  :meth:`commit_unlit` assigns an unlit (Emission) material sampling it, so the surface shows
  the baked result -- at the cost of dropping normals/specular and re-lighting. The lowest-end
  / fully baked option. Reversible via :meth:`revert_unlit`.

:meth:`revert` undoes whichever level an object is in. Quality tiers come from
:meth:`from_preset` (pythontk ``PresetStore``). HDR EXR throughout.

The engine surface is Qt-free and defers ``import bpy`` (headless-importable); only
:class:`LightmapBakerSlots` touches Qt, lazily.

The ``.ui`` is a verbatim copy of mayatk's — same objectNames (``cmb_scope``, ``cmb001``,
``cmb002``, ``cmb000``, ``cmb_resolution``, ``spn_samples``, ``txt000``, ``b000``) — now that
uitk host-namespaces the QSettings branch per DCC (``Switchboard.add_ui`` /
``MainWindow._relative_state`` via ``context_tags``), identical objectNames across mayatk's
and blendertk's copy of the same panel no longer collide in the shared "uitk"/"shared"
registry root, so there is no need to renumber widgets to dodge it. Both ``cmb002`` packing
modes are live: "Per-Object" (one full-resolution map each) and "Atlas by Material" (per-material
consolidation via :meth:`LightmapBaker.pack_atlas`, the Blender port of mayatk's atlas packer).
"""
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import selected_objects
from blendertk.uv_utils._uv_utils import (
    LIGHTMAP_UV_SET,
    create_lightmap_uvs,
    find_lightmap_uv_set,
)
from blendertk.node_utils.data_nodes import DataNodes
from blendertk.mat_utils.texture_baker import TextureBaker


class LightmapBaker(ptk.LoggingMixin):
    """Orchestrate the Blender lightmap workflow: UV2 -> Cycles bake -> engine export prep.

    Usage::

        baker = LightmapBaker.from_preset("quest")        # or (resolution=, samples=)
        baker.revert(objects)                              # bake the SOURCE material
        out = baker.bake_separated(objects)                # {obj_name: exr_path}
        baker.commit_lightmap(out)                         # mark + publish Unity metadata
        # The object keeps its full material; the lightmap rides UV channel 1 and the
        # wiring rides the FBX on the data_export Empty -- nothing is destroyed.
    """

    # Custom-property names stamped on a committed object (JSON). Persisting the restore
    # record on the object -- not in memory -- is what makes commit non-destructive across
    # save/reload and independent of the baker instance.
    COMMIT_PROP: str = "lightmapCommit"  # fused: original materials + created node
    LIGHTMAP_INFO_PROP: str = "lightmapInfo"  # lighting-only: map / uv / intensity marker

    # ``data_export`` channel: a scene-wide JSON manifest of every lighting-only lightmap,
    # regenerated from the per-object markers and ridden into the FBX (informational;
    # consumed by unitytk's optional Unity-native binder).
    LIGHTMAP_METADATA: str = "lightmap_metadata"
    LIGHTMAP_METADATA_VERSION: int = 1

    # Identity atlas transform: the object's 0-1 lightmap UVs map to the whole texture.
    _IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (1.0, 1.0, 0.0, 0.0)

    def __init__(self, resolution: int = 1024, samples: int = 5):
        super().__init__()
        # The generic Cycles bake-to-texture primitive (mat_utils) owns resolution/samples; this
        # workflow (UV2, commit/revert, engine metadata) composes it — mirror of mayatk's
        # TextureBaker / LightmapBaker split. ``resolution``/``samples`` stay readable/settable on
        # the baker (below) as a single source of truth (no drift between the two objects).
        self._texture_baker = TextureBaker(resolution, samples)

    @property
    def resolution(self) -> int:
        return self._texture_baker.resolution

    @resolution.setter
    def resolution(self, value: int) -> None:
        self._texture_baker.resolution = int(value)

    @property
    def samples(self) -> int:
        return self._texture_baker.samples

    @samples.setter
    def samples(self, value: int) -> None:
        self._texture_baker.samples = int(value)

    # ------------------------------------------------------------------
    # Quality-tier presets (pythontk PresetStore: built-in + user tiers)
    # ------------------------------------------------------------------

    @staticmethod
    def preset_store() -> "ptk.PresetStore":
        """Shared store of lightmap quality presets (built-in + user tiers).

        Built-ins ship as JSON in this subpackage's ``presets/`` dir; user presets live under
        the consolidated config root (the same one uitk's ``PresetManager`` uses), so headless
        and GUI paths resolve to one place.
        """
        builtin = os.path.join(os.path.dirname(__file__), "presets")
        return ptk.PresetStore("lightmap", package="blendertk", builtin_dir=builtin)

    @classmethod
    def from_preset(cls, name: str, **overrides) -> "LightmapBaker":
        """Construct a baker from a named quality preset (``resolution`` / ``samples``).

        ``overrides`` win over the preset; extra preset keys (``description``) are ignored.
        Built-ins: ``preview`` (256/2), ``quest`` (1024/4), ``desktop`` (2048/8).
        """
        store = cls.preset_store()
        if not store.exists(name):
            raise ValueError(
                f"Unknown lightmap preset {name!r}. Available: {store.list()}"
            )
        data = {**store.load(name), **overrides}
        kwargs = {k: int(data[k]) for k in ("resolution", "samples") if k in data}
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Bake
    # ------------------------------------------------------------------

    def bake_fused(self, objects=None, **kwargs) -> Dict[str, str]:
        """Bake a **fused** (albedo x lighting) HDR lightmap per object.

        Cycles ``type='COMBINED'`` into the lightmap UV. Pairs with :meth:`commit_unlit`.
        Parameters mirror :meth:`_bake` (``**kwargs``). Returns ``{object_name: exr_path}``.
        """
        return self._bake(objects, fused=True, **kwargs)

    def bake_separated(self, objects=None, prefix: str = "lightmap_irr_", **kwargs) -> Dict[str, str]:
        """Bake a **lighting-only** irradiance lightmap per object (the default path).

        Cycles ``type='DIFFUSE'`` with ``pass_filter={'DIRECT','INDIRECT'}`` (no ``'COLOR'``)
        — the native white-card irradiance, so albedo stays on its own UV/texture and the
        lightmap holds lighting only, to be combined ``albedo x lightmap`` by the engine.
        Unlike Maya this needs **no material swap** (Cycles excludes the color pass directly).
        ``prefix`` defaults to ``"lightmap_irr_"`` so it never clobbers fused output. Pairs
        with :meth:`commit_lightmap`. Returns ``{object_name: exr_path}``.
        """
        return self._bake(objects, fused=False, prefix=prefix, **kwargs)

    def _bake(
        self,
        objects=None,
        fused: bool = False,
        output_dir: Optional[str] = None,
        prefix: str = "lightmap_",
        suffix: str = "",
        margin: Optional[int] = None,
        create_uvs: bool = True,
        uv_set: Optional[str] = None,
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        stem: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Bake one HDR lightmap per object into the lightmap UV channel.

        Adds the lightmap *workflow* (packed UV2, lighting-only vs fused passes, lightmap output
        dir) on top of the generic :class:`TextureBaker` primitive it composes.

        Parameters:
            objects: Mesh objects (refs or names). Defaults to current selection.
            fused: ``True`` -> COMBINED (albedo x lighting); ``False`` -> DIFFUSE no-color
                (lighting-only / white-card irradiance).
            output_dir: Output directory (created if missing). Defaults to a
                ``baked_lighting`` dir next to the .blend (or the OS temp dir).
            prefix / suffix: Name affix wrapped around the object's stem (e.g. ``_Lightmap``).
            margin: Native gutter width in px. ``None`` -> a resolution-scaled default.
            create_uvs: Ensure a packed lightmap UV2 first (reuses a valid one).
            uv_set: Lightmap UV layer name. Default :data:`LIGHTMAP_UV_SET`.
            on_progress: ``(done, total, name) -> bool`` per-object callback (return ``False``
                to cancel) so a UI can drive a progress bar.
            stem: Output base-name resolver — ``{name: stem}`` dict, ``callable(obj)->str``, or
                ``None`` (default texture-set stem, falling back to the object name).

        Returns ``{object_name: lightmap_path}`` for each successful bake.
        """
        meshes = TextureBaker.resolve_meshes(objects)
        if not meshes:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        uv_set = uv_set or LIGHTMAP_UV_SET
        if create_uvs:
            create_lightmap_uvs(meshes, uv_set=uv_set, quiet=True)

        return self._texture_baker.bake(
            meshes,
            bake_type="COMBINED" if fused else "DIFFUSE",
            pass_filter=None if fused else {"DIRECT", "INDIRECT"},
            use_pass_color=fused,  # lighting-only excludes albedo (native white-card)
            output_dir=output_dir or TextureBaker.default_output_dir("baked_lighting"),
            prefix=prefix,
            suffix=suffix,
            margin=margin,
            # Per-object: target the object's own lightmap UV (robust to a pre-existing,
            # differently-named lightmap layer; falls back to the standard set name).
            uv_set=lambda o: find_lightmap_uv_set(o) or uv_set,
            stem=stem,
            on_progress=on_progress,
        )

    # ------------------------------------------------------------------
    # Commit: lighting-only (keep maps) -- fully non-destructive
    # ------------------------------------------------------------------

    def commit_lightmap(
        self,
        mapping: Dict[str, str],
        intensity: float = 1.0,
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        uv_rects: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Record a lighting-only bake for the engine (changes nothing about the material/UVs).

        Per object stamps a small JSON marker (:attr:`LIGHTMAP_INFO_PROP`), then republishes
        the scene-wide manifest onto the shared ``data_export`` carrier so it rides the FBX
        (informational; unitytk's optional editor helper auto-binds Unity's native lightmap
        slots from it). ``mapping`` is ``{object_name: lightmap_path}``. Returns the recorded
        subset.

        ``scale_offsets`` / ``uv_rects`` are the atlas-consolidation hooks (mirror mayatk's
        ``commit_lightmap``): ``{object_name: [scaleX, scaleY, offsetX, offsetY]}``.
        ``uv_rects`` records a rect :meth:`pack_atlas` already repacked into the lightmap UVs
        (marker key ``uvRect``; revert bookkeeping only — nothing engine-side); ``scale_offsets``
        is the legacy engine-applied rect. The "Atlas by Material" packing mode passes ``uv_rects``
        (the applied rects) with identity ``scale_offsets`` — the mesh samples the atlas directly
        through UV2, so nothing is bound engine-side; per-object bakes pass neither (identity).
        """
        import bpy

        if float(intensity) != 1.0:
            # Mirror of mayatk: Unity's native lightmaps have no per-map
            # multiplier, so a non-1.0 intensity is applied INTO the texels
            # here, once per unique file; the manifest field is informational
            # after that. (Float-EXR load->scale->save round-trip verified in
            # headless Blender 5.1, HDR >1 values included.)
            self._apply_intensity(mapping.values(), intensity)

        scale_offsets = scale_offsets or {}
        uv_rects = uv_rects or {}
        recorded: Dict[str, str] = {}
        for name, path in mapping.items():
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            lm = find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            so = scale_offsets.get(name) or self._IDENTITY_SCALE_OFFSET
            info = {
                "map": os.path.basename(path),
                "uv_set": lm,
                "intensity": float(intensity),
                "scaleOffset": [float(v) for v in so],
                "mode": "separated",
            }
            rect = uv_rects.get(name)
            if rect and [float(v) for v in rect] != list(
                self._IDENTITY_SCALE_OFFSET
            ):
                info["uvRect"] = [float(v) for v in rect]
            obj[self.LIGHTMAP_INFO_PROP] = json.dumps(info)
            recorded[name] = path

        if recorded:
            self._publish_lightmap_metadata()
        return recorded

    # ------------------------------------------------------------------
    # Atlas consolidation ("Atlas by Material" packing — cmb002 index 1)
    # ------------------------------------------------------------------
    # Blender port of mayatk's ``pack_atlas``: group the per-object lightmaps by primary
    # material, give each object an area-weighted rect, assemble ONE shared EXR per group, and
    # repack each object's lightmap UVs into its rect so the exported mesh samples the atlas
    # directly through UV2 (no engine scaleOffset binding). The DCC-agnostic layout math is
    # REUSED from pythontk (``ptk.ImgUtils.compute_atlas_layout`` / ``inset_atlas_rects`` /
    # ``atlas_pixel_rects`` — all pure-Python, no cv2, the same helpers mayatk uses); only the
    # EXR assembly + UV repack are Blender-native (bpy image I/O + a numpy paste/dilate, since
    # Blender's runtime ships no cv2). The applied rect is recorded as the marker's ``uvRect``
    # (revert bookkeeping) and undone by :meth:`revert_lightmap`.

    def pack_atlas(
        self,
        mapping: Dict[str, str],
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Consolidate ``{object_name: per_object_exr}`` into one atlas EXR per primary material.

        Post-process for the lighting-only path: takes the result of :meth:`bake_separated` and
        packs each material group into one shared, area-weighted atlas (bigger objects get more
        texels). Each object's lightmap UVs are repacked into its rect, so the mesh samples the
        atlas directly through UV2 — plug-and-play in any engine with no scaleOffset binding.
        A single-object group is left as its own map with an identity rect. Objects that share a
        mesh (linked duplicates) share one lightmap UV set, so only the first owns a rect. A
        group whose assembly fails keeps its per-object maps (identity rect) — never lose a bake.

        Returns ``{object_name: (atlas_path, [scaleX, scaleY, offsetX, offsetY])}`` — the rect is
        the remap already APPLIED to the object's lightmap UVs (identity for solo/fallback), i.e.
        bookkeeping for the ``uvRect`` marker, not an engine binding.
        """
        import bpy

        if not mapping:
            return {}
        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))

        # Linked duplicates share one mesh (one lightmap UV set), so only the first can own a
        # rect — remapping the shared set once per instance would compound the transform.
        by_mesh: Dict[str, str] = {}
        objects: List[str] = []
        for name in sorted(mapping):  # deterministic winner + rect order
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            key = obj.data.name if getattr(obj, "data", None) is not None else None
            if key and key in by_mesh:
                self.logger.warning(
                    "Atlas: %s shares a mesh with %s; instances share one lightmap rect.",
                    name, by_mesh[key],
                )
                continue
            if key:
                by_mesh[key] = name
            objects.append(name)

        groups: Dict[str, List[str]] = {}
        for name in objects:
            key = self._primary_material(bpy.data.objects.get(name)) or "__no_material__"
            groups.setdefault(key, []).append(name)

        all_sources = {os.path.abspath(p) for p in mapping.values()}
        out: Dict[str, Tuple[str, List[float]]] = {}
        used: set = set()
        for key, names in groups.items():
            try:
                self._pack_group(
                    key, names, mapping, all_sources, output_dir, prefix, suffix, out, used
                )
            except Exception as e:  # never lose a bake — fall the group back to per-object maps
                self.logger.warning(
                    "Atlas: packing group %r failed (%s); keeping per-object maps.", key, e
                )
                for n in names:
                    if n not in out and os.path.exists(mapping[n]):
                        out[n] = (mapping[n], list(self._IDENTITY_SCALE_OFFSET))
        return out

    def _pack_group(
        self, key, names, mapping, all_sources, output_dir, prefix, suffix, out, used
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`)."""
        import bpy

        if len(names) == 1:  # a solo group is already its own atlas (identity rect) — no atlas name
            out[names[0]] = (mapping[names[0]], list(self._IDENTITY_SCALE_OFFSET))
            return

        foreign = all_sources - {os.path.abspath(mapping[n]) for n in names}
        base = self._atlas_base(key, names)
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        atlas_path = self._unique_atlas_path(output_dir, name, used, foreign)

        objs = [bpy.data.objects.get(n) for n in names]
        weights = [self._surface_area(o) for o in objs]
        rects = ptk.ImgUtils.compute_atlas_layout(weights)
        # Inset each rect by a resolution-scaled gutter and later dilate content into the freed
        # border, so mip levels / bilinear taps can't bleed across neighbours. The INSET rect is
        # the applied UV rect, so sampling stays exact.
        gutter = max(2, self.resolution // 256)
        rects = ptk.ImgUtils.inset_atlas_rects(rects, self.resolution, gutter)

        placements: List[Tuple[str, List[float], str]] = []
        for n, rect in zip(names, rects):
            if not os.path.exists(mapping[n]):
                self.logger.warning("Atlas: missing map for %s; skipping.", n)
                continue
            placements.append((mapping[n], [float(v) for v in rect], n))
        if not placements:
            return

        self._assemble_atlas_exr(atlas_path, [(p, so) for p, so, _ in placements], gutter)

        for src, so, n in placements:
            obj = bpy.data.objects.get(n)
            lm = find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            try:
                self._transform_lightmap_uvs(obj, lm, so)
            except Exception as e:  # keep this object's own map — degraded but engine-correct
                self.logger.warning(
                    "Atlas: lightmap UVs for %s not repacked (%s); keeping its per-object map.",
                    n, e,
                )
                out[n] = (mapping[n], list(self._IDENTITY_SCALE_OFFSET))
                continue
            out[n] = (atlas_path, so)
            try:  # drop the now-consolidated per-object map
                if os.path.abspath(src) != os.path.abspath(atlas_path):
                    os.remove(src)
            except OSError:
                pass

    def _assemble_atlas_exr(self, atlas_path, placements, gutter) -> None:
        """Composite each ``(source_exr, inset_rect)`` into one shared EXR at ``self.resolution``
        via bpy image I/O (no cv2): load + native-scale each source into its pixel rect, paste
        into a float atlas buffer, dilate content into the freed gutter, and save as OPEN_EXR.
        The pixel-rect mapping (incl. the UV bottom-up vs image top-down flip) comes from
        ``ptk.ImgUtils.atlas_pixel_rects`` — the same SSoT mayatk's cv2 assembler uses, so UV
        placement matches Unity's ``lightmapScaleOffset``."""
        import bpy
        import numpy as np

        res = self.resolution
        pix_rects = ptk.ImgUtils.atlas_pixel_rects([so for _, so in placements], res)
        atlas = np.zeros((res, res, 4), dtype=np.float32)
        atlas[..., 3] = 1.0
        mask = np.zeros((res, res), dtype=bool)

        for (src, _so), (row0, row1, col0, col1) in zip(placements, pix_rects):
            w = max(1, col1 - col0)
            h = max(1, row1 - row0)
            img = None
            try:
                img = bpy.data.images.load(src)
                if tuple(img.size) != (w, h):
                    img.scale(w, h)
                buf = np.empty(len(img.pixels), dtype=np.float32)
                img.pixels.foreach_get(buf)
                tile = buf.reshape(img.size[1], img.size[0], img.channels)
                tile = np.flipud(tile)  # bpy pixels are bottom-up; atlas rows are top-down
                rgb = tile[..., :3]
            finally:
                if img is not None:
                    bpy.data.images.remove(img)
            r0, r1 = max(row0, 0), min(row1, res)
            c0, c1 = max(col0, 0), min(col1, res)
            atlas[r0:r1, c0:c1, :3] = rgb[: r1 - r0, : c1 - c0, :]
            atlas[r0:r1, c0:c1, 3] = 1.0
            mask[r0:r1, c0:c1] = True

        atlas = self._dilate_gutter(atlas, mask, gutter + 1)

        out = bpy.data.images.new(
            os.path.basename(atlas_path), width=res, height=res, float_buffer=True, alpha=True
        )
        try:
            flat = np.ascontiguousarray(np.flipud(atlas)).reshape(-1)  # top-down -> bottom-up
            out.pixels.foreach_set(flat)
            out.filepath_raw = atlas_path
            out.file_format = "OPEN_EXR"
            out.save()
        finally:
            bpy.data.images.remove(out)

    @staticmethod
    def _dilate_gutter(atlas, mask, iterations):
        """Grow placed content outward into the empty gutter by ``iterations`` px (numpy
        edge-copy dilation), so taps near a rect edge sample real content instead of the black
        border — mayatk's cv2 dilate step, done with numpy."""
        import numpy as np

        filled = atlas.copy()
        m = mask.copy()
        for _ in range(max(0, int(iterations))):
            added = np.zeros_like(m)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                shifted_m = np.roll(m, (dy, dx), axis=(0, 1))
                shifted_v = np.roll(filled, (dy, dx), axis=(0, 1))
                take = (~m) & (~added) & shifted_m
                filled[take] = shifted_v[take]
                added |= take
            if not added.any():
                break
            m = m | added
        return filled

    @staticmethod
    def _primary_material(obj) -> Optional[str]:
        """Name of the material covering the most faces of *obj* (its group key); ``None`` if
        nothing is assigned. A single-material object wins outright."""
        slots = getattr(obj, "material_slots", None)
        if not slots:
            return None
        mats = [s.material for s in slots]
        named = [m for m in mats if m is not None]
        if not named:
            return None
        if len(mats) == 1:
            return mats[0].name
        counts: Dict[str, int] = {}
        for p in obj.data.polygons:
            mi = p.material_index
            if 0 <= mi < len(mats) and mats[mi] is not None:
                counts[mats[mi].name] = counts.get(mats[mi].name, 0) + 1
        return max(counts, key=counts.get) if counts else named[0].name

    @staticmethod
    def _surface_area(obj) -> float:
        """World-space surface area of *obj* (atlas texel weight); 1.0 on failure."""
        import bmesh

        me = getattr(obj, "data", None)
        if me is None or not hasattr(me, "polygons"):
            return 1.0
        bm = bmesh.new()
        try:
            bm.from_mesh(me)
            bm.transform(obj.matrix_world)
            area = sum(f.calc_area() for f in bm.faces)
        finally:
            bm.free()
        return area if area > 0 else 1.0

    @staticmethod
    def _atlas_base(key, names) -> str:
        """A filesystem-safe name base for a group's atlas — its material name (fallback: the
        first object's name)."""
        import re

        base = key if key and key != "__no_material__" else names[0]
        return re.sub(r"[^\w.\-]", "_", str(base)) or "atlas"

    @staticmethod
    def _unique_atlas_path(output_dir, name, used, avoid=frozenset()) -> str:
        """Atlas path for *name*, unique within one pack (``used``) and clear of any other
        group's not-yet-consumed source maps (*avoid*, a set of abspaths). Overwriting the
        atlas's OWN prior file is allowed (that's the point of consolidation)."""
        candidate = os.path.join(output_dir, f"{name}.exr")
        k = 1
        while candidate in used or os.path.abspath(candidate) in avoid:
            candidate = os.path.join(output_dir, f"{name}_{k}.exr")
            k += 1
        used.add(candidate)
        return candidate

    @staticmethod
    def _transform_lightmap_uvs(obj, uv_set, rect, invert=False) -> None:
        """Affine-transform *obj*'s *uv_set* by a ``[sx, sy, ox, oy]`` rect. Forward maps the
        unit square into the rect (``uv' = uv*s + o`` — the atlas placement); ``invert=True``
        applies the exact inverse, restoring the original 0-1 layout (used by revert)."""
        import numpy as np

        sx, sy, ox, oy = (float(v) for v in rect)
        layer = obj.data.uv_layers.get(uv_set)
        if layer is None:
            raise RuntimeError(f"no lightmap UV set '{uv_set}'")
        data = layer.data
        buf = np.empty(len(data) * 2, dtype=np.float32)
        data.foreach_get("uv", buf)
        uv = buf.reshape(-1, 2)
        if invert:
            uv[:, 0] = (uv[:, 0] - ox) / sx
            uv[:, 1] = (uv[:, 1] - oy) / sy
        else:
            uv[:, 0] = uv[:, 0] * sx + ox
            uv[:, 1] = uv[:, 1] * sy + oy
        data.foreach_set("uv", buf.reshape(-1))
        obj.data.update()

    def _apply_intensity(self, paths, intensity: float) -> None:
        """Scale each unique lightmap file's texels by *intensity*, once.

        bpy-native float-EXR rewrite (no cv2 in Blender's runtime): load the
        image, scale RGB in the raw float pixel buffer (``pixels`` bypasses
        color management, so linear HDR data round-trips losslessly), save it
        back as OPEN_EXR, and drop the datablock. Files shared by several
        objects are deduped by abspath so they scale exactly once per commit.
        A file that can't be read is left untouched and logged -- the commit
        itself still proceeds. Note it mutates the file: re-committing the
        same bake with a non-1.0 intensity re-applies it (mirrors mayatk).
        """
        import bpy
        import numpy as np

        for path in {os.path.abspath(p) for p in paths}:
            img = None
            try:
                img = bpy.data.images.load(path)
                buf = np.empty(len(img.pixels), dtype=np.float32)
                img.pixels.foreach_get(buf)
                px = buf.reshape(-1, img.channels)
                px[:, : min(3, img.channels)] *= float(intensity)
                img.pixels.foreach_set(buf)
                img.filepath_raw = path
                img.file_format = "OPEN_EXR"
                img.save()
            except Exception as e:
                self.logger.warning(
                    "Intensity %.3f NOT applied to %s: %s",
                    intensity, os.path.basename(path), e,
                )
            finally:
                if img is not None:
                    bpy.data.images.remove(img)

    def _publish_lightmap_metadata(self) -> Optional[str]:
        """(Re)build the lightmap manifest on the shared ``data_export`` carrier.

        Scans every object carrying a :attr:`LIGHTMAP_INFO_PROP` marker and writes one JSON
        manifest (``{"version", "objects": [...]}``) to the carrier. Regenerating from the
        markers keeps incremental bakes additive and a revert subtractive; clears the channel
        when no lightmapped objects remain. camelCase keys match unitytk's ``LightmapRecord``.
        """
        import bpy

        objects: List[Dict[str, Any]] = []
        for obj in bpy.data.objects:
            if self.LIGHTMAP_INFO_PROP not in obj:
                continue
            try:
                info = json.loads(obj[self.LIGHTMAP_INFO_PROP] or "{}")
            except ValueError:
                continue
            # Publish the lightmap layer's REAL channel index (mirrors mayatk):
            # Unity's native lightmaps only sample uv2 (index 1), so anything
            # else is warned about instead of hidden behind a hardcoded 1.
            # (No duplicate-name check here -- unlike Maya DAG leaves, Blender
            # object names are globally unique, so the Unity join key can't
            # collide within one export.)
            uv_set = info.get("uv_set")
            uv_index = 1
            layers = getattr(getattr(obj, "data", None), "uv_layers", None)
            if layers is not None and uv_set:
                found = layers.find(uv_set)
                if found >= 0:
                    uv_index = found
                else:
                    self.logger.warning(
                        "%s: committed lightmap layer %r no longer exists; "
                        "publishing uvIndex 1 on faith. Re-run "
                        "create_lightmap_uvs if the layer was renamed or "
                        "removed.",
                        obj.name, uv_set,
                    )
            if uv_index != 1:
                self.logger.warning(
                    "%s: lightmap layer %r sits at UV index %d, but Unity "
                    "samples uv2 (index 1). Re-run create_lightmap_uvs before "
                    "exporting.",
                    obj.name, uv_set, uv_index,
                )
            objects.append(
                {
                    "name": obj.name,  # the Unity GameObject join key
                    "map": info.get("map"),
                    "uvIndex": uv_index,
                    "intensity": info.get("intensity", 1.0),
                    "scaleOffset": info.get(
                        "scaleOffset", list(self._IDENTITY_SCALE_OFFSET)
                    ),
                }
            )

        if not objects:
            DataNodes.set_export_string(self.LIGHTMAP_METADATA, "")
            return None
        manifest = json.dumps(
            {"version": self.LIGHTMAP_METADATA_VERSION, "objects": objects}
        )
        return DataNodes.set_export_string(self.LIGHTMAP_METADATA, manifest)

    def revert_lightmap(self, objects=None) -> List[str]:
        """Undo :meth:`commit_lightmap` -- restore any atlas UV remap, drop the markers, republish.

        The per-object path changes nothing about the material/UVs, so reverting it just drops the
        marker. An **atlas** commit repacked the object's lightmap UVs into its rect (recorded as
        the marker's ``uvRect``); that IS a UV change, so it is inverted here first — restoring the
        original 0-1 layout so a re-bake (which calls :meth:`revert` before baking) starts clean.
        The baked texture and UV layer are otherwise left in place. ``objects=None`` clears every
        marked object. Returns the names cleared.
        """
        cleared = []
        for obj in self._marked_objects(self.LIGHTMAP_INFO_PROP, objects):
            try:
                info = json.loads(obj[self.LIGHTMAP_INFO_PROP] or "{}")
            except (ValueError, TypeError):
                info = {}
            rect = info.get("uvRect")
            if rect and [float(v) for v in rect] != list(self._IDENTITY_SCALE_OFFSET):
                uv_set = info.get("uv_set") or find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
                try:
                    self._transform_lightmap_uvs(obj, uv_set, rect, invert=True)
                except Exception as e:
                    self.logger.warning(
                        "Could not restore atlased lightmap UVs on %s: %s", obj.name, e
                    )
            del obj[self.LIGHTMAP_INFO_PROP]
            cleared.append(obj.name)
        if cleared:
            self._publish_lightmap_metadata()
        return cleared

    # ------------------------------------------------------------------
    # Commit: fused -> unlit (single map)
    # ------------------------------------------------------------------

    def commit_unlit(self, mapping: Dict[str, str]) -> Dict[str, str]:
        """Make the fused bake each object's live appearance (non-destructive).

        Assigns an unlit (Emission) material sampling the fused EXR through the lightmap UV and
        marks that channel ``active_render`` (so it exports as the primary UV for a stock unlit
        shader). The original materials are kept in the scene (un-assigned) and a restore record
        is stamped via :attr:`COMMIT_PROP` so :meth:`revert_unlit` puts them back. Idempotent.
        ``mapping`` is ``{object_name: fused_exr_path}``. Returns ``{object_name: material}``.
        """
        import bpy

        wired: Dict[str, str] = {}
        for name, path in mapping.items():
            obj = bpy.data.objects.get(name)
            if obj is None or self.COMMIT_PROP in obj:
                continue
            lm = find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            prev_mats = [s.material.name if s.material else None for s in obj.material_slots]

            mat = self._make_unlit_material(f"{name}_unlit", path, lm)
            obj.data.materials.clear()
            obj.data.materials.append(mat)
            if lm in obj.data.uv_layers:
                obj.data.uv_layers[lm].active_render = True

            obj[self.COMMIT_PROP] = json.dumps(
                {"materials": prev_mats, "created": mat.name, "uv_render": lm}
            )
            wired[name] = mat.name
        return wired

    @staticmethod
    def _make_unlit_material(name: str, path: str, uv_name: str):
        """An Emission (unlit) material sampling ``path`` (raw linear HDR) via ``uv_name``."""
        import bpy

        image = bpy.data.images.load(os.path.abspath(path), check_existing=True)
        image.colorspace_settings.name = "Non-Color"

        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        emit = nt.nodes.new("ShaderNodeEmission")
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        uvmap = nt.nodes.new("ShaderNodeUVMap")
        uvmap.uv_map = uv_name
        nt.links.new(uvmap.outputs["UV"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
        nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
        return mat

    def revert_unlit(self, objects=None) -> List[str]:
        """Undo :meth:`commit_unlit` -- restore the source material slots + drop the marker.

        Reads the JSON record stamped by :meth:`commit_unlit`, so it works on any committed
        object (a fresh baker, a reopened .blend). ``objects=None`` reverts every committed
        object. Returns the names reverted.
        """
        import bpy

        reverted = []
        for obj in self._marked_objects(self.COMMIT_PROP, objects):
            try:
                record = json.loads(obj[self.COMMIT_PROP] or "{}")
            except ValueError:
                record = {}
            obj.data.materials.clear()
            for mname in record.get("materials") or []:
                obj.data.materials.append(
                    bpy.data.materials.get(mname) if mname else None
                )
            created = record.get("created")
            if created and created in bpy.data.materials:
                m = bpy.data.materials[created]
                if m.users == 0:
                    bpy.data.materials.remove(m)
            del obj[self.COMMIT_PROP]
            reverted.append(obj.name)
        return reverted

    def revert(self, objects=None) -> List[str]:
        """Undo any lightmap wiring -- fused commit and/or lighting-only marker.

        Used by the panel and the pre-bake clear; reverts whichever level each object is in.
        """
        return self.revert_unlit(objects) + self.revert_lightmap(objects)

    @staticmethod
    def _marked_objects(prop: str, objects) -> List[Any]:
        """Objects carrying *prop*: ``objects=None`` -> all in scene; else the given subset."""
        import bpy

        if objects is None:
            return [o for o in bpy.data.objects if prop in o]
        out = []
        for o in ptk.make_iterable(objects):
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None and prop in obj:
                out.append(obj)
        return out


# -----------------------------------------------------------------------------
# Switchboard panel
# -----------------------------------------------------------------------------


class LightmapBakerSlots(ptk.LoggingMixin):
    """Switchboard slots for the co-located ``lightmap_baker.ui`` panel.

    A thin driver over :class:`LightmapBaker` (composition; no bake logic here). Mirrors
    mayatk's ``LightmapBakerSlots`` 1:1 (same method names / signal-connection order); the one
    spot where the engines currently diverge is noted below. **Bake Lightmaps** (``b000``) runs
    revert -> bake -> commit for the selection; the **Mode** combobox (``cmb001``) picks the
    level:

    * **Lighting Only** (default) — :meth:`~LightmapBaker.bake_separated` +
      :meth:`~LightmapBaker.commit_lightmap`. Keeps the full PBR material; bakes lighting onto
      UV1 and stamps Unity metadata on the shared ``data_export`` carrier.
    * **Fused Unlit** — :meth:`~LightmapBaker.bake_fused` +
      :meth:`~LightmapBaker.commit_unlit`. Bakes albedo×lighting into one map + an unlit
      material; the lowest-end / fully baked option.

    Either way ``b000`` first calls :meth:`~LightmapBaker.revert` to clear prior wiring so the
    bake samples the real material; the header menu's **Revert to Source** undoes it. The
    Quality combobox is populated from :meth:`~LightmapBaker.preset_store` and fills the
    Resolution / Samples dials (the source of truth at bake time). The Packing combobox
    (``cmb002``) picks how a Lighting-Only bake's maps are laid out — Per-Object or Atlas by
    Material (:meth:`~LightmapBaker.pack_atlas`); both are live.

    Tentacle-independent (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` ``fmt`` helper is
    deferred into the methods that use it (headless Blender ships no Qt binding).
    """

    _MODE_LABELS = ("Lighting Only (keep maps)", "Fused Unlit (single map)")

    # Packing labels for the Packing combobox (cmb002). Per-Object (index 0, the default) keeps
    # one full-resolution map per object; Atlas by Material (index 1) consolidates a material
    # group into one shared EXR and repacks each object's lightmap UVs into its rect (standalone
    # atlas, no engine binding) via :meth:`LightmapBaker.pack_atlas`. _packing() reads it back.
    _PACKING_LABELS = ("Per-Object (one map each)", "Atlas by Material (shared map)")

    # Fixed lightmap sizes (square, px) for the Resolution combobox
    # (cmb_resolution). Power-of-two atlas sizes; every Quality preset lands on
    # one of these. _resolution() reads the selection back as an int.
    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    # Scope labels for the Scope combobox (cmb_scope): which objects b000 bakes.
    # Selected (index 0, default) preserves the prior selection-only behavior;
    # _scope() / _scope_objects() resolve it to the mesh objects to bake.
    _SCOPE_LABELS = ("Selected", "Visible", "Scene")

    # Footer tail for a plain (non-atlas) lighting-only commit. Shared by b000's per-object
    # branch and its atlas-fallback branch so the two can't drift (mirrors mayatk's
    # ``_LIGHTING_ONLY_TAIL``).
    _LIGHTING_ONLY_TAIL = "Maps kept; lightmap + Unity metadata stamped. Export the FBX."

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[lightmap_baker] ")

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.lightmap_baker

        self._last_output_dir: Optional[str] = None
        self._baker: Optional[LightmapBaker] = None

        # Deferred: the switchboard builds this mid-load, before the combos are wired onto
        # self.ui — sync the dials to the shown preset on the next tick.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        self._apply_preset(self.ui.cmb000.currentText())

    # ------------------------------------------------------------------ header
    def header_init(self, widget) -> None:
        """Configure the header chrome (menu / collapse / hide), menu, help text."""
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Revert to Source",
            setObjectName="revert_to_source",
            setToolTip="Undo the bake's wiring — restore the original material on the "
            "selected (or all baked) objects.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Open Output Folder",
            setObjectName="open_output",
            setToolTip="Open the folder the lightmaps were written to.",
        )
        try:
            from uitk.widgets.mixins.tooltip_mixin import fmt
        except ImportError:
            return
        widget.set_help_text(
            fmt(
                title="Lightmap Baker",
                body="Bake Blender scene lighting (Cycles) into a texture per object for game "
                "engines (Unity-first) and wire it up in one step — no manual export prep.",
                steps=[
                    "Choose a <b>Scope</b> — bake the <b>Selected</b> objects (default), all "
                    "<b>Visible</b> meshes, or the whole <b>Scene</b>.",
                    "Pick a <b>Mode</b> and <b>Packing</b> (see below) and a <b>Quality</b> "
                    "preset (fills Resolution / Samples; override either to taste).",
                    "Press <b>Bake Lightmaps</b>, then export the FBX with <b>Custom "
                    "Properties</b> enabled (so the hidden <i>data_export</i> Empty carries "
                    "the Unity wiring).",
                ],
                sections=[
                    ("Mode: Lighting Only — real lightmapping (default)", [
                        "Bakes <i>lighting only</i> (Cycles diffuse, no albedo) onto a second "
                        "UV channel; your full PBR material is <b>kept untouched</b>.",
                        "The lightmap is a <b>separate EXR</b>; the engine multiplies "
                        "albedo × lightmap at runtime and your normal map still works. "
                        "Self-contained export — UV2 samples the map directly in any "
                        "engine; a one-file Unity editor helper (optional, unitytk's "
                        "<i>LightmapMetadataController.cs</i>) auto-binds Unity's native "
                        "lightmap slots from the FBX wiring on the shared data Empty.",
                        "<b>Packing</b>: <i>Per-Object</i> gives each object its own full-"
                        "resolution lightmap. <i>Atlas by Material</i> consolidates every object "
                        "sharing a material into one shared, area-weighted EXR and repacks their "
                        "lightmap UVs into it — one texture set per material, sampled directly "
                        "through UV2 (no engine binding).",
                    ]),
                    ("Mode: Fused Unlit — flatten to one texture (NOT lightmapping)", [
                        "Bakes albedo × lighting into one HDR texture + an <i>unlit</i> "
                        "(Emission) material — normal / metallic / roughness are "
                        "<b>discarded</b> and it can't be re-lit. Only for things you intend "
                        "to flatten forever (skybox, far LOD, lowest-end prop).",
                    ]),
                    ("Non-destructive", [
                        "Nothing is deleted — the source material stays in the scene and the "
                        "restore data is stamped on the object.",
                        "<b>Revert to Source</b> (header menu) undoes the wiring; re-baking "
                        "auto-reverts first.",
                    ]),
                ],
                notes=[
                    "Cycles must be available (it ships with Blender). The bake runs on the "
                    "CPU/GPU; higher Samples = cleaner GI, slower bake.",
                ],
            )
        )

    # ------------------------------------------------------------------ combos
    def cmb000_init(self, widget) -> None:
        """Populate the Quality combobox from the shared preset store."""
        store = LightmapBaker.preset_store()
        widget.clear()
        widget.addItems(store.list())
        idx = widget.findText("quest")
        if idx >= 0:
            widget.setCurrentIndex(idx)

    def cmb000(self, index, widget) -> None:
        """Apply the selected preset's dials to Resolution / Samples."""
        if self._apply_preset(widget.currentText()):
            self.ui.footer.setText(f"Preset: {widget.currentText()}")

    def cmb001_init(self, widget) -> None:
        """Populate the bake-level (Mode) combobox; Lighting Only is the default."""
        widget.clear()
        widget.addItems(self._MODE_LABELS)
        widget.setCurrentIndex(0)

    def _mode(self) -> str:
        text = (self.ui.cmb001.currentText() or "").lower()
        return "fused" if "fused" in text else "separated"

    def cmb002_init(self, widget) -> None:
        """Populate the Packing combobox; Per-Object is the default (Atlas by Material also live)."""
        widget.clear()
        widget.addItems(self._PACKING_LABELS)
        widget.setCurrentIndex(0)  # Per-Object — one full-resolution map each

    def _packing(self) -> str:
        """``"atlas"`` or ``"per_object"`` from the Packing combobox (default per_object)."""
        text = (self.ui.cmb002.currentText() or "").lower()
        return "atlas" if "atlas" in text else "per_object"

    def cmb_scope_init(self, widget) -> None:
        """Populate the Scope combobox; Selected (current selection) is the default."""
        widget.clear()
        widget.addItems(self._SCOPE_LABELS)
        widget.setCurrentIndex(0)  # Selected — the prior selection-only behavior

    def _scope(self) -> str:
        """``"selected"`` (default), ``"visible"`` or ``"scene"`` from cmb_scope."""
        return (self.ui.cmb_scope.currentText() or "Selected").split()[0].lower()

    def _scope_objects(self):
        """The mesh objects to bake for the current Scope.

        ``selected`` is the raw selection (unchanged behavior); ``visible`` and
        ``scene`` gather mesh objects across the scene so a bake needn't be
        preceded by a manual select-all.
        """
        scope = self._scope()
        if scope == "selected":
            return selected_objects()
        import bpy

        # resolve_meshes is the baker's own "what counts as a bakeable mesh" SSoT,
        # so the scope's count matches what bake() will actually process.
        meshes = TextureBaker.resolve_meshes(list(bpy.context.scene.objects))
        if scope == "visible":
            return [o for o in meshes if o.visible_get()]
        return meshes  # scene

    def cmb_resolution_init(self, widget) -> None:
        """Populate the Resolution combobox (value carried as item data); default 1024."""
        widget.clear()
        for r in self._RESOLUTIONS:
            widget.addItem(f"Resolution:\t{r}", r)
        widget.setCurrentIndex(self._RESOLUTIONS.index(1024))

    def _resolution(self) -> int:
        """The selected lightmap resolution (px) from cmb_resolution (its item data)."""
        value = self.ui.cmb_resolution.currentData()
        return int(value) if value is not None else 1024

    def _set_resolution(self, value: int) -> None:
        """Select *value* in the Resolution combobox, snapping to the nearest fixed size."""
        nearest = min(self._RESOLUTIONS, key=lambda r: abs(r - value))
        cmb = self.ui.cmb_resolution
        cmb.blockSignals(True)
        try:
            cmb.setCurrentIndex(self._RESOLUTIONS.index(nearest))
        finally:
            cmb.blockSignals(False)

    def txt000_init(self, widget) -> None:
        """Add the Prefix / Suffix / Auto picker to the name-affix field."""
        widget.option_box.set_affix(default="auto")

    def _apply_preset(self, name: str) -> bool:
        store = LightmapBaker.preset_store()
        if not name or not store.exists(name):
            return False
        data = store.load(name)
        if "resolution" in data:
            self._set_resolution(int(data["resolution"]))
        if "samples" in data:
            spin = self.ui.spn_samples
            spin.blockSignals(True)
            try:
                spin.setValue(int(data["samples"]))
            finally:
                spin.blockSignals(False)
        return True

    # ------------------------------------------------------------------ actions
    def b000(self) -> None:
        """Bake lightmaps for the selection in the chosen Mode (revert → bake → commit)."""
        objects = self._scope_objects()
        if not objects:
            self.ui.footer.setText(
                "Select one or more mesh objects to bake."
                if self._scope() == "selected"
                else f"No meshes found for scope '{self._scope()}'."
            )
            return

        self._baker = LightmapBaker(
            resolution=self._resolution(),
            samples=self.ui.spn_samples.value(),
        )
        self._baker.revert(objects)  # clear prior wiring so we bake the real material

        out_dir = self._output_dir()
        # Name the output <object><affix> per the field (e.g. "<object>_Lightmap"), following
        # the texture-set convention; the field's affix picker forces Prefix / Suffix / Auto.
        prefix, suffix = self.ui.txt000.option_box.resolve_affix(default="suffix")
        fused = self._mode() == "fused"
        bake = self._baker.bake_fused if fused else self._baker.bake_separated

        with self.ui.footer.progress(text="Baking lightmaps…") as update:
            result = bake(
                objects,
                output_dir=out_dir,
                prefix=prefix,
                suffix=suffix,
                on_progress=lambda done, total, name: update(
                    None,
                    f"Baking {name}…  ({min(done + 1, total)}/{total})"
                    if done < total
                    else f"Baked {total} object{'s' if total != 1 else ''}.",
                ),
            )
        if not result:
            self._last_output_dir = None
            self.ui.footer.setText("Bake produced no output (see the console).")
            return

        if fused:
            self._baker.commit_unlit(result)
            tail = "Shows an unlit baked material. Revert to Source to undo."
        elif self._packing() == "atlas":
            # Consolidate the per-object maps into one shared EXR per primary material and repack
            # each object's lightmap UVs into its rect. commit records the applied rect as the
            # marker's uvRect (revert bookkeeping); the engine scaleOffset stays identity because
            # the UVs are already baked into the atlas layout (the mesh samples it via UV2).
            packed = self._baker.pack_atlas(
                result, output_dir=out_dir, prefix=prefix, suffix=suffix
            )
            if packed:
                result = {name: path for name, (path, _so) in packed.items()}
                uv_rects = {name: so for name, (_path, so) in packed.items()}
                self._baker.commit_lightmap(result, uv_rects=uv_rects)
                atlases = len(set(result.values()))
                tail = (
                    f"Consolidated into {atlases} atlas{'es' if atlases != 1 else ''} by "
                    f"material. {self._LIGHTING_ONLY_TAIL}"
                )
            else:  # nothing packed — keep the per-object maps rather than lose the bake
                self._baker.commit_lightmap(result)
                tail = self._LIGHTING_ONLY_TAIL
        else:
            self._baker.commit_lightmap(result)
            tail = self._LIGHTING_ONLY_TAIL
        self._last_output_dir = os.path.dirname(next(iter(result.values())))
        count = len(result)
        self.ui.footer.setText(
            f"Baked {count} object{'s' if count != 1 else ''} → "
            f"{self._last_output_dir}. {tail}"
        )

    # ------------------------------------------------------------------ header menu
    def revert_to_source(self) -> None:
        """Undo the bake wiring on the selected objects (or all baked ones)."""
        if self._baker is None:
            self._baker = LightmapBaker()
        selection = selected_objects() or None
        reverted = self._baker.revert(selection)
        if reverted:
            self.ui.footer.setText(
                f"Reverted {len(reverted)} object{'s' if len(reverted) != 1 else ''} to source."
            )
        else:
            self.ui.footer.setText("No baked objects to revert.")

    def open_output(self) -> None:
        """Open the most recent output folder in the file browser."""
        out = self._last_output_dir or self._output_dir()
        if out and os.path.isdir(out):
            os.startfile(out)
        else:
            self.ui.footer.setText("No output folder yet — bake first.")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _output_dir() -> str:
        """Where bakes go: ``//textures`` next to the .blend, else a temp dir.

        Blender has no Maya-style "sourceimages" project workspace to write into (the header
        menu's "Open Output Folder" — mayatk's counterpart is "Open Sourceimages Folder" —
        browses this instead); everything lands next to the .blend so it travels with the file.
        """
        import bpy
        import tempfile

        blend = bpy.data.filepath
        root = os.path.dirname(blend) if blend else tempfile.gettempdir()
        return os.path.join(root, "textures")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("lightmap_baker", reload=True)
    ui.show(pos="screen", app_exec=True)
