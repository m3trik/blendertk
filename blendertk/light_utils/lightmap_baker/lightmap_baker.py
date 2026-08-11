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
      (There is no albedo-fused level: ``COMBINED`` is not lightmapping.)
* ``scene.render.bake.margin`` -- native gutter/seam padding (no ``dilate_image`` needed).
* ``DataNodes.set_export_string`` -- the export manifest (custom prop on the ``data_export``
  Empty, rides the FBX; no sidecar file). Informational -- the mesh's UV2 samples the map in
  any engine; unitytk's optional editor helper reads it to auto-bind Unity's native slots.

**One bake level, and it is real lightmapping**, non-destructive and exposed in the panel:
:meth:`bake_separated` bakes lighting-only irradiance onto the lightmap UV (channel 1) and
:meth:`commit_lightmap` records it. The object's full PBR material and texture UV0 are
**kept untouched** -- the engine composites ``albedo x lightmap``.

:meth:`revert` (== :meth:`revert_lightmap`) undoes it. A *fused unlit* level (albedo x
lighting flattened behind an Emission material) was removed: it is not lightmapping, it
discards every other map, and it only ever added a mode to choose wrongly from. Quality
tiers come from :meth:`from_preset` (pythontk ``PresetStore``). HDR EXR throughout.

The engine surface is Qt-free and defers ``import bpy`` (headless-importable); only
:class:`LightmapBakerSlots` touches Qt, lazily.

The ``.ui`` is a verbatim copy of mayatk's — same objectNames (``cmb_scope``,
``cmb002``, ``cmb000``, ``cmb_resolution``, ``spn_samples``, ``txt_output_dir``, ``txt000``,
``b000``) — now that
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

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.uv_utils._uv_utils import UvUtils, LIGHTMAP_UV_SET
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

    One shared map per material instead of one per object -- and the faster path, since it
    plans the atlas before baking and sizes every bake to its footprint. The rect is the
    per-instance engine binding (Unity ``lightmapScaleOffset``), so instances/linked
    duplicates are first-class::

        packed = baker.bake_atlas(objects)                 # {obj_name: (atlas, rect)}
        baker.commit_lightmap({n: p for n, (p, _r) in packed.items()},
                              scale_offsets={n: r for n, (_p, r) in packed.items()})
    """

    # Custom-property names stamped on a committed object (JSON). Persisting the restore
    # record on the object -- not in memory -- is what makes commit non-destructive across
    # save/reload and independent of the baker instance.
    LIGHTMAP_INFO_PROP: str = (
        "lightmapInfo"  # lighting-only: map / uv / intensity marker
    )

    # ``data_export`` channel: a scene-wide JSON manifest of every lighting-only lightmap,
    # regenerated from the per-object markers and ridden into the FBX (informational;
    # consumed by unitytk's optional Unity-native binder).
    LIGHTMAP_METADATA: str = "lightmap_metadata"
    LIGHTMAP_METADATA_VERSION: int = 1

    # Identity atlas transform: the object's 0-1 lightmap UVs map to the whole texture.
    _IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (1.0, 1.0, 0.0, 0.0)

    # Rendered-dead rescue (twin of mayatk LightmapBaker._DEAD_TEXEL_*): Cycles
    # bakes every UV texel of the target regardless of world occlusion, so
    # geometry buried below a floor slab / behind trim bakes full-coverage
    # ~black. Tile texels at or below max(_DEAD_TEXEL_ABS,
    # _DEAD_TEXEL_FRACTION * the tile's median lit luminance) are treated as
    # empty and refilled from lit neighbors -- left in, the atlas downscale
    # and every bilinear/mip tap smear them into visible dark borders at the
    # junctions they hide behind. 1% of median sits ~20x under real contact
    # shadow.
    _DEAD_TEXEL_ABS: float = 1e-4
    _DEAD_TEXEL_FRACTION: float = 0.01

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 5,
        denoise: bool = True,
        device: Optional[str] = None,
    ):
        super().__init__()
        # The generic Cycles bake-to-texture primitive (mat_utils) owns resolution/samples; this
        # workflow (UV2, commit/revert, engine metadata) composes it — mirror of mayatk's
        # TextureBaker / LightmapBaker split. ``resolution``/``samples`` stay readable/settable on
        # the baker (below) as a single source of truth (no drift between the two objects).
        # ``denoise``/``device`` are Cycles quality/throughput knobs owned by the same primitive.
        self._texture_baker = TextureBaker(resolution, samples, denoise, device)

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

    @property
    def denoise(self) -> bool:
        return self._texture_baker.denoise

    @denoise.setter
    def denoise(self, value: bool) -> None:
        self._texture_baker.denoise = bool(value)

    @property
    def device(self) -> Optional[str]:
        return self._texture_baker.device

    @device.setter
    def device(self, value: Optional[str]) -> None:
        self._texture_baker.device = value

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
        Built-ins (Cycles samples, denoised): ``preview`` (256/64), ``quest`` (1024/256),
        ``desktop`` (2048/512), ``hero`` (4096/1024). The tiers name an ATLAS size, and an
        atlas is shared by a whole material group -- a 40-piece room on one material gets
        1/40th of it each, which is why an environment needs a tier above its per-object
        intuition.
        """
        store = cls.preset_store()
        if not store.exists(name):
            raise ValueError(
                f"Unknown lightmap preset {name!r}. Available: {store.list()}"
            )
        data = {**store.load(name), **overrides}
        kwargs: Dict[str, Any] = {
            k: int(data[k]) for k in ("resolution", "samples") if k in data
        }
        # Constructor args a preset does not carry but a caller may override --
        # previously dropped silently, so from_preset(name, device="CPU") built a
        # GPU baker and nothing said so.
        for key in ("denoise", "device"):
            if key in overrides:
                kwargs[key] = overrides[key]
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Bake
    # ------------------------------------------------------------------

    def bake_separated(
        self, objects=None, prefix: str = "lightmap_irr_", **kwargs
    ) -> Dict[str, str]:
        """Bake a **lighting-only** irradiance lightmap per object -- THE bake.

        Cycles ``type='DIFFUSE'`` with ``pass_filter={'DIRECT','INDIRECT'}`` (no ``'COLOR'``)
        — the native white-card irradiance, so albedo stays on its own UV/texture and the
        lightmap holds lighting only, to be combined ``albedo x lightmap`` by the engine.
        Unlike Maya this needs **no material swap** (Cycles excludes the color pass directly).
        Pairs with :meth:`commit_lightmap`. Returns ``{object_name: exr_path}``.
        """
        return self._bake(objects, prefix=prefix, **kwargs)

    def _bake(
        self,
        objects=None,
        output_dir: Optional[str] = None,
        prefix: str = "lightmap_",
        suffix: str = "",
        margin: Optional[int] = None,
        create_uvs: bool = True,
        uv_set: Optional[str] = None,
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        stem: Optional[Any] = None,
        size: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Bake one HDR lightmap per object into the lightmap UV channel.

        Adds the lightmap *workflow* (packed UV2, the lighting-only pass, lightmap output
        dir) on top of the generic :class:`TextureBaker` primitive it composes.

        Parameters:
            objects: Mesh objects (refs or names). Defaults to current selection.
            output_dir: Output directory (created if missing). Defaults to a
                ``baked_lighting`` dir next to the .blend (or the OS temp dir).
            prefix / suffix: Name affix wrapped around the object's stem (e.g. ``_Lightmap``).
            margin: Native gutter width in px. ``None`` -> a default scaled to each map's
                own size, since the margin extends islands across the map it sits in.
            create_uvs: Ensure a packed lightmap UV2 first (reuses a valid one).
            uv_set: Lightmap UV layer name. Default :data:`LIGHTMAP_UV_SET`.
            on_progress: ``(done, total, name) -> bool`` per-object callback (return ``False``
                to cancel) so a UI can drive a progress bar.
            stem: Output base-name resolver — ``{name: stem}`` dict, ``callable(obj)->str``, or
                ``None`` (default texture-set stem, falling back to the object name).
            size: Per-object map size resolver (see :meth:`TextureBaker.bake`). ``None`` bakes
                every object at the full square :attr:`resolution`; :meth:`bake_atlas` passes
                each object's atlas footprint instead.

        Returns ``{object_name: lightmap_path}`` for each successful bake.
        """
        meshes = TextureBaker.resolve_meshes(objects)
        if not meshes:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        uv_set = uv_set or LIGHTMAP_UV_SET
        if create_uvs:
            UvUtils.create_lightmap_uvs(meshes, uv_set=uv_set, quiet=True)

        return self._texture_baker.bake(
            meshes,
            bake_type="DIFFUSE",
            pass_filter={"DIRECT", "INDIRECT"},
            use_pass_color=False,  # lighting-only excludes albedo (native white-card)
            output_dir=output_dir or TextureBaker.default_output_dir("baked_lighting"),
            prefix=prefix,
            suffix=suffix,
            margin=margin,
            # Per-object: target the object's own lightmap UV (robust to a pre-existing,
            # differently-named lightmap layer; falls back to the standard set name).
            uv_set=lambda o: UvUtils.find_lightmap_uv_set(o) or uv_set,
            stem=stem,
            size=size,
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

        ``scale_offsets`` / ``uv_rects`` are the atlas hooks (mirror mayatk's
        ``commit_lightmap``): ``{object_name: [scaleX, scaleY, offsetX, offsetY]}``.
        ``scale_offsets`` is THE atlas binding — the per-instance rect the engine applies
        (Unity ``lightmapScaleOffset``; glTF ``KHR_texture_transform``); the "Atlas by
        Material" packing mode passes :meth:`pack_atlas`'s rects here. ``uv_rects`` is
        legacy-marker compat only (a rect an old commit repacked INTO the UVs, marker key
        ``uvRect``, revert bookkeeping) — new code never passes it. Per-object bakes pass
        neither (identity).
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
            lm = UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            so = scale_offsets.get(name) or self._IDENTITY_SCALE_OFFSET
            info = {
                "map": os.path.basename(path),
                # Locate hint for manifest-only consumers (mirrors mayatk).
                "dir": os.path.dirname(os.path.abspath(path)),
                "uv_set": lm,
                "intensity": float(intensity),
                "scaleOffset": [float(v) for v in so],
                "mode": "separated",
            }
            rect = uv_rects.get(name)
            if rect and [float(v) for v in rect] != list(self._IDENTITY_SCALE_OFFSET):
                info["uvRect"] = [float(v) for v in rect]
            obj[self.LIGHTMAP_INFO_PROP] = json.dumps(info)
            recorded[name] = path

        if recorded:
            self._publish_lightmap_metadata()
        return recorded

    # ------------------------------------------------------------------
    # Atlas consolidation ("Atlas by Material" packing — cmb002 index 1)
    # ------------------------------------------------------------------
    # :meth:`bake_atlas` is the entry point to prefer -- it plans the layout first and bakes
    # each object straight to its footprint. The two-call form (``bake_separated`` then
    # ``pack_atlas``) remains for callers that already hold maps they did not bake here.
    #
    # Group the per-object lightmaps by primary material, give each object (each INSTANCE --
    # linked duplicates are first-class) an area-weighted rect, and assemble ONE shared EXR
    # per group. UVs are never rewritten: every mesh keeps its shared [0,1] unwrap and the
    # rect is committed as the per-instance ``scaleOffset`` binding -- the industry-standard
    # model (Unity ``Renderer.lightmapScaleOffset``; glTF ``KHR_texture_transform``), and the
    # only one instances can express (per-instance data cannot live in shared UV data).
    # The DCC-agnostic layout math is REUSED from pythontk
    # (``ptk.ImgUtils.compute_atlas_layout`` / ``inset_atlas_rects`` / ``atlas_pixel_rects``
    # — all pure-Python, no cv2, the same helpers mayatk uses); only the EXR assembly is
    # Blender-native (bpy image I/O + a numpy paste/dilate, since Blender's runtime ships no
    # cv2). Legacy commits that DID repack UVs recorded the rect as the marker's ``uvRect``;
    # :meth:`revert_lightmap` still inverts those.

    def bake_atlas(
        self,
        objects=None,
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        **kwargs,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Bake a material-atlased lighting-only lightmap set — plan first, then bake to plan.

        The whole "Atlas by Material" path in one call, and the one that should be preferred
        over ``bake_separated`` + :meth:`pack_atlas` because it is the same result for a
        fraction of the work. The atlas layout depends only on **surface area and material
        assignment**, both known before a single ray is traced, so the plan is computed up
        front and each object is baked *directly at the pixel footprint it will occupy*.
        Baking every object at the full atlas resolution and then downscaling it into a small
        rect — what the two-call form does — spends N times the rays to supersample away
        noise that the denoise pass removes anyway, and the objects that share an atlas are
        exactly the ones whose maps get shrunk the most.

        Intermediates never reach *output_dir*: the per-object tiles are baked into a tracked
        temp dir and only the finished maps are placed, so a bake cannot litter a project's
        texture folder with files the caller has no use for.

        Extra ``kwargs`` are forwarded to :meth:`bake_separated`. *prefix* / *suffix* name
        both the tiles and the atlas (the ``lightmap_irr_`` prefix a loose
        ``bake_separated`` call defaults to is pointless here — the tiles never
        leave the work dir). Returns :meth:`pack_atlas`'s
        ``{object_name: (atlas_path, rect)}``.
        """
        # The plan reads only geometry and material assignment, so it is available before the
        # lightmap UVs exist -- which is precisely what lets it size the bake that creates them.
        # It also resolves the input, so it doubles as the "is there anything to bake" answer.
        plan = self.atlas_plan(objects)
        planned = [name for entries in plan.values() for name, _rect in entries]
        if not planned:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        output_dir = output_dir or TextureBaker.default_output_dir("baked_lighting")
        with ptk.TempArtifacts("lightmap_bake", policy="scoped") as tmp:
            baked = self.bake_separated(
                planned,
                output_dir=tmp.dir_path(),
                prefix=prefix,
                suffix=suffix,
                size=self.plan_sizes(plan),
                **kwargs,
            )
            return self.pack_atlas(
                baked,
                output_dir=output_dir,
                prefix=prefix,
                suffix=suffix,
                plan=plan,
            )

    def atlas_plan(self, objects) -> Dict[str, List[Tuple[str, List[float]]]]:
        """``{material: [(object_name, rect), ...]}`` — the atlas layout, decided before baking.

        Groups the meshes by primary material and gives each an area-weighted, gutter-inset
        rect (a solo group keeps the identity rect: it is already its own atlas). Objects that
        share a mesh (linked duplicates / instances) are FIRST-CLASS: each stands somewhere
        different and receives different light, so each gets its own rect over the one shared
        [0,1] unwrap — the rect travels as the per-instance scaleOffset binding, never into
        the shared UVs. Weights are per-instance world-space area, so a scaled copy earns
        proportional texels.

        Pure bookkeeping: nothing is baked, read from disk or written, which is what lets
        :meth:`bake_atlas` size each bake from it.
        """
        import bpy

        meshes = TextureBaker.resolve_meshes(objects)
        names: List[str] = [
            obj.name for obj in sorted(meshes, key=lambda o: o.name)  # deterministic order
        ]

        groups: Dict[str, List[str]] = {}
        for name in names:
            key = (
                self._primary_material(bpy.data.objects.get(name)) or "__no_material__"
            )
            groups.setdefault(key, []).append(name)

        gutter = self._atlas_gutter()
        plan: Dict[str, List[Tuple[str, List[float]]]] = {}
        for key, group in groups.items():
            if len(group) == 1:
                plan[key] = [(group[0], list(self._IDENTITY_SCALE_OFFSET))]
                continue
            weights = [self._surface_area(bpy.data.objects.get(n)) for n in group]
            # Inset each rect by a resolution-scaled gutter and later dilate content into the
            # freed border, so mip levels / bilinear taps can't bleed across neighbours. The
            # INSET rect is the applied UV rect, so sampling stays exact -- and it is then
            # SNAPPED to the texel grid: the assembler writes at rounded pixel edges, and
            # publishing the un-rounded float samples up to half a texel of gutter along
            # every rect edge (a thin dark border on each shared instance edge). Twin of
            # mayatk's ``_pack_group``.
            rects = ptk.ImgUtils.snap_atlas_rects(
                ptk.ImgUtils.inset_atlas_rects(
                    ptk.ImgUtils.compute_atlas_layout(weights), self.resolution, gutter
                ),
                self.resolution,
            )
            plan[key] = [
                (n, [float(v) for v in rect]) for n, rect in zip(group, rects)
            ]
        return plan

    def plan_sizes(
        self, plan: Dict[str, List[Tuple[str, List[float]]]]
    ) -> Dict[str, Tuple[int, int]]:
        """``{object_name: (width, height)}`` — the pixel footprint each object occupies.

        The bake size that makes an :meth:`atlas_plan` exact: assembling the atlas resizes
        each tile into these dimensions anyway, so producing them at any other size is work
        thrown away. Derived through ``ptk.ImgUtils.atlas_pixel_rects``, the same rounding
        SSoT :meth:`_assemble_atlas_exr` places with, so a tile never needs rescaling.
        """
        sizes: Dict[str, Tuple[int, int]] = {}
        for entries in plan.values():
            pixel_rects = ptk.ImgUtils.atlas_pixel_rects(
                [rect for _n, rect in entries], self.resolution
            )
            for (name, _rect), (row0, row1, col0, col1) in zip(entries, pixel_rects):
                sizes[name] = (max(1, col1 - col0), max(1, row1 - row0))
        return sizes

    def _atlas_gutter(self) -> int:
        """Bleed margin (px) freed around each rect, scaled to the atlas resolution."""
        return max(2, self.resolution // 256)

    def pack_atlas(
        self,
        mapping: Dict[str, str],
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Consolidate ``{object_name: per_object_exr}`` into one atlas EXR per primary material.

        Post-process for the lighting-only path: takes the result of :meth:`bake_separated` and
        packs each material group into one shared, area-weighted atlas (bigger objects get more
        texels). The object's lightmap UVs are NOT touched — every mesh keeps its shared [0,1]
        unwrap and the returned rect is the per-instance binding the engine applies (Unity
        ``lightmapScaleOffset``; glTF ``KHR_texture_transform``), which is what lets linked
        duplicates share one mesh while each samples its own patch of the atlas.
        A single-object group is left as its own map with an identity rect. A group whose
        assembly fails keeps its per-object maps (identity rect) — never lose a bake.

        **Every returned path lives in *output_dir***, including the solo and fallback maps, so
        the caller may bake its sources anywhere (:meth:`bake_atlas` uses a temp dir) and trust
        that what comes back is the finished set and nothing else. The one exception is a move
        that genuinely fails, which returns the source path and logs an error naming it — a
        caller staging in temp can still recover the map before the sweep reclaims it.

        *plan* is an :meth:`atlas_plan` computed earlier — pass the one the sources were baked
        against so the layout can't be re-derived differently; ``None`` computes it here.

        Returns ``{object_name: (atlas_path, [scaleX, scaleY, offsetX, offsetY])}`` — the rect
        is the ENGINE BINDING to publish per instance (``commit_lightmap(scale_offsets=...)``;
        identity for solo/fallback), not an applied UV remap.
        """
        if not mapping:
            return {}
        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))
        if plan is None:
            plan = self.atlas_plan(list(mapping))

        all_sources = {os.path.abspath(p) for p in mapping.values()}
        out: Dict[str, Tuple[str, List[float]]] = {}
        used: set = set()
        for key, entries in plan.items():
            entries = [(n, rect) for n, rect in entries if n in mapping]
            if not entries:
                continue
            try:
                self._pack_group(
                    key,
                    entries,
                    mapping,
                    all_sources,
                    output_dir,
                    prefix,
                    suffix,
                    out,
                    used,
                )
            except (
                Exception
            ) as e:  # never lose a bake — fall the group back to per-object maps
                self.logger.warning(
                    "Atlas: packing group %r failed (%s); keeping per-object maps.",
                    key,
                    e,
                )
                for n, _rect in entries:
                    if n not in out and os.path.exists(mapping[n]):
                        try:
                            path = self._place(mapping[n], output_dir, used)
                        except OSError as move_error:
                            # This is the never-lose-a-bake handler; it must not become
                            # the thing that loses it. Report where the map actually is
                            # so a caller staging in temp can still recover it.
                            self.logger.error(
                                "Atlas: %s's map could not be moved into %s (%s); "
                                "it is still at %s.",
                                n,
                                output_dir,
                                move_error,
                                mapping[n],
                            )
                            path = mapping[n]
                        out[n] = (path, list(self._IDENTITY_SCALE_OFFSET))
        return out

    def _pack_group(
        self, key, entries, mapping, all_sources, output_dir, prefix, suffix, out, used
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`)."""
        names = [n for n, _rect in entries]
        foreign = all_sources - {os.path.abspath(mapping[n]) for n in names}
        base = self._atlas_base(key, names)
        if len(names) == 1:
            # A solo group is already its own atlas (identity rect) — no atlas to assemble,
            # but it is still a RESULT, so it moves into place like one, under the same
            # texture-set name a multi-object group would get: the per-object tile name it
            # currently wears is an intermediate, not a deliverable. ``foreign`` matters
            # here precisely BECAUSE it is renamed — the old name was the source's own, so
            # the move was a no-op when packing in place; a derived one can land on another
            # group's not-yet-consumed tile, which ``_place`` would otherwise delete.
            path = self._place(
                mapping[names[0]],
                output_dir,
                used,
                stem=ptk.StrUtils.apply_affix(base, prefix, suffix),
                avoid=foreign,
            )
            # A solo map skips the assembly (and its rendered-dead rescue) —
            # heal its exact-zero texels here (twin of mayatk's adopt heal).
            self._heal_zero_texels(path)
            out[names[0]] = (path, list(self._IDENTITY_SCALE_OFFSET))
            return

        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        atlas_path = self._unique_atlas_path(output_dir, name, used, foreign)

        placements: List[Tuple[str, List[float], str]] = []
        for n, rect in entries:
            if not os.path.exists(mapping[n]):
                self.logger.warning("Atlas: missing map for %s; skipping.", n)
                continue
            placements.append((mapping[n], [float(v) for v in rect], n))
        if not placements:
            return

        self._assemble_atlas_exr(
            atlas_path, [(p, so) for p, so, _ in placements], self._atlas_gutter()
        )

        for src, so, n in placements:
            # The rect is the deliverable, not a UV edit: the object's shared [0,1]
            # unwrap stays untouched and the engine applies the rect per instance
            # (Unity lightmapScaleOffset / glTF KHR_texture_transform). Published
            # aimed at border-texel CENTERS (twin of mayatk _pack_group): a rect
            # edge on a texel boundary splits every tap along a shared 3D edge
            # onto the neighboring cell's gutter, up to half its weight on
            # another object's lighting. Placement above used the plan's cell
            # unchanged.
            # (Full-span bbox: blendertk islands cover their whole unwrap; the
            # island-bbox refinement rides the backlogged crop fold.)
            out[n] = (
                atlas_path,
                list(
                    ptk.ImgUtils.inset_rects_to_texel_centers(
                        [so], self.resolution
                    )[0]
                ),
            )
            try:  # drop the now-consolidated per-object map
                if os.path.abspath(src) != os.path.abspath(atlas_path):
                    os.remove(src)
            except OSError:
                pass

    @staticmethod
    def _place(
        src: str,
        output_dir: str,
        used: set,
        stem: Optional[str] = None,
        avoid: frozenset = frozenset(),
    ) -> str:
        """Move a finished map into *output_dir* and return its new path.

        Only results belong in the destination — a bake's intermediates stay in whatever work
        dir produced them. A same-named file already there is the PREVIOUS run's map for the
        same object and is replaced (that is what re-baking means); collisions *within* one
        pack get a numeric tail instead. ``shutil`` rather than ``os.replace`` because the work
        dir is routinely on a different volume from the project.

        A destination that cannot be replaced takes an adjacent name instead of failing: the
        realistic cause is the previous map being held open by the DCC's own texture cache,
        and losing a finished bake over a file lock would be absurd.

        *stem* renames the map on the way in (default: keep the source's own). *avoid* is a
        set of abspaths that must not be overwritten — another group's not-yet-consumed
        source maps, reachable only once *stem* is derived rather than inherited.
        """
        import shutil

        src_abs = os.path.abspath(src)
        os.makedirs(output_dir, exist_ok=True)
        src_stem, ext = os.path.splitext(os.path.basename(src))
        stem = stem or src_stem
        dst = os.path.join(output_dir, f"{stem}{ext}")
        k = 1
        while (dst in used or os.path.abspath(dst) in avoid) and os.path.abspath(
            dst
        ) != src_abs:
            dst = os.path.join(output_dir, f"{stem}_{k}{ext}")
            k += 1
        if os.path.abspath(dst) != src_abs:
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except OSError:
                    while dst in used or os.path.exists(dst):
                        dst = os.path.join(output_dir, f"{stem}_{k}{ext}")
                        k += 1
            shutil.move(src_abs, dst)
        used.add(dst)
        return dst

    def _heal_zero_texels(self, path: str) -> None:
        """Refill any exact-zero texel in *path* from its nearest non-zero one, in place.

        Solo maps skip the atlas assembly (and its rendered-dead rescue), but a bake
        target's unrendered background — and rendered-dead occluded geometry — ships
        exact zeros that every mip level averages into the island as a dark halo.
        A fully-black map is left alone (a black bake is a faithful render of an
        unlit scene; the panel guard warns), as is a map with nothing to heal.
        """
        import bpy
        import numpy as np

        img = None
        try:
            img = bpy.data.images.load(path)
            # Colorspace BEFORE any pixel write: assigned later, the save goes
            # through a view transform and a float EXR can come out black.
            img.colorspace_settings.name = "Non-Color"
            buf = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(buf)
            px = buf.reshape(img.size[1], img.size[0], img.channels)
            rgb = px[..., :3]
            valid = (rgb > 0).any(axis=-1)
            if valid.all() or not valid.any():
                return
            px[..., :3] = ptk.ImgUtils.fill_empty_texels(rgb, mask=valid)
            img.pixels.foreach_set(px.reshape(-1))
            img.filepath_raw = path
            img.file_format = "OPEN_EXR"
            img.save()
        except Exception as e:  # never lose a finished bake to a heal
            self.logger.warning("Zero-heal skipped for %s: %s", path, e)
        finally:
            if img is not None:
                bpy.data.images.remove(img)

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
                tile = np.flipud(
                    tile
                )  # bpy pixels are bottom-up; atlas rows are top-down
                rgb = tile[..., :3]
            finally:
                if img is not None:
                    bpy.data.images.remove(img)
            r0, r1 = max(row0, 0), min(row1, res)
            c0, c1 = max(col0, 0), min(col1, res)
            tile_rgb = rgb[: r1 - r0, : c1 - c0, :]
            atlas[r0:r1, c0:c1, :3] = tile_rgb
            atlas[r0:r1, c0:c1, 3] = 1.0
            # Rendered-dead rescue (see _DEAD_TEXEL_*): texels the bake
            # RENDERED but that carry ~no radiance are occluded geometry, not
            # signal -- excluded from the content mask, the dilate/fill below
            # replaces them with lit neighbors instead of shipping hard black
            # borders. An all-dark tile stays content wholesale (a black bake
            # is a faithful render of an unlit scene; the panel guard warns).
            lum = tile_rgb.max(axis=-1)
            lit = lum > self._DEAD_TEXEL_ABS
            if lit.any():
                floor_ = max(
                    self._DEAD_TEXEL_ABS,
                    self._DEAD_TEXEL_FRACTION * float(np.median(lum[lit])),
                )
                mask[r0:r1, c0:c1] = lum > floor_
            else:
                mask[r0:r1, c0:c1] = True

        # Gutter fill via the SHARED pythontk primitives (the twin of mayatk's
        # atlas step -- one implementation, not two that drift). The previous
        # hand-rolled ``np.roll`` dilation WRAPPED at the image border: a rect
        # touching the atlas frame pulled its "neighbor" content from the
        # OPPOSITE edge of the atlas -- another object's lighting, or black.
        rgb = ptk.ImgUtils.dilate_image(atlas[..., :3], mask=mask, iterations=gutter + 1)
        # Then fill everything left: any background texel that survives is
        # averaged into rect content by every coarser mip level the engine
        # generates -- a black background reads as a dark halo on each tile
        # at distance/grazing angles.
        rgb = ptk.ImgUtils.fill_empty_texels(rgb, mask=mask | (rgb > 0).any(axis=-1))
        # Sanitize before write (parity with mayatk ``_write_lightmap_exr``):
        # one NaN/Inf ray would otherwise ride into the engine's half-float
        # import as a garbage texel.
        atlas[..., :3] = np.clip(
            np.nan_to_num(rgb, nan=0.0, posinf=65504.0, neginf=0.0), 0.0, 65504.0
        )

        out = bpy.data.images.new(
            os.path.basename(atlas_path),
            width=res,
            height=res,
            float_buffer=True,
            alpha=True,
        )
        try:
            # Colorspace BEFORE the pixel write, and explicitly: an unset
            # colorspace lets Blender's default view transform touch the save
            # (the one write in this package that skipped it -- web_export's
            # docstring and the test fixture both call this out as the
            # black-map/double-transform gotcha).
            out.colorspace_settings.name = "Non-Color"
            flat = np.ascontiguousarray(np.flipud(atlas)).reshape(
                -1
            )  # top-down -> bottom-up
            out.pixels.foreach_set(flat)
            out.filepath_raw = atlas_path
            out.file_format = "OPEN_EXR"
            out.save()
        finally:
            bpy.data.images.remove(out)

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

    def _atlas_base(self, key, names) -> str:
        """A filesystem-safe name base for a group's atlas.

        Prefers the TEXTURE SET the group's material already wears
        (:meth:`_material_texture_base`), falling back to the material name and then to the
        first object's name. The same chain serves solo and multi-object groups, so one
        object's map is named by the rule that would have named its atlas.
        """
        import re

        base = self._material_texture_base(key) or (
            key if key and key != "__no_material__" else names[0]
        )
        return re.sub(r"[^\w.\-]", "_", str(base)) or "atlas"

    @staticmethod
    def _material_texture_base(material_name: Optional[str]) -> Optional[str]:
        """Base name of the texture SET *material_name* already wears, or ``None``.

        ``OFFICE_ENV_Base_color.png`` -> ``OFFICE_ENV``, so the lightmap lands in
        sourceimages beside the maps it belongs to rather than under the material's own
        name (``MAT_OFFICE_ENV_Lightmap.exr`` next to ``OFFICE_ENV_Base_color.png`` reads
        as a stray from a different set). The material name is an authoring detail; the
        texture set is what the rest of the maps are keyed on. Suffix matching is
        delegated to ``ptk.ImgUtils.get_base_texture_name`` — the map-suffix SSoT — so
        this cannot drift from how the other tools split a texture name.

        The most COMMON base across the material's image nodes wins, so one oddly-named
        map (a shared noise texture, a stray lookup) cannot rename the whole set.

        Mirrors mayatk's ``LightmapBaker._texture_set_stem``, which had this rule first --
        blendertk was the twin that drifted, naming atlases after the material.
        """
        import bpy

        mat = bpy.data.materials.get(material_name or "")
        tree = getattr(mat, "node_tree", None) if mat is not None else None
        if tree is None:
            return None
        counts: Dict[str, int] = {}
        for node in tree.nodes:
            img = getattr(node, "image", None)
            if img is None:
                continue
            # A packed or FBX-embedded image has no filepath but keeps the original
            # filename as its datablock name, which is what the import leaves behind.
            source = os.path.basename(str(img.filepath or "")) or str(img.name or "")
            base = ptk.ImgUtils.get_base_texture_name(source) if source else ""
            if base:
                counts[base] = counts.get(base, 0) + 1
        if not counts:
            return None
        # Sorted first so a tie breaks on the name rather than on node order.
        return max(sorted(counts), key=counts.get)

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
        unit square into the rect (``uv' = uv*s + o``); ``invert=True`` applies the exact
        inverse. RETAINED FOR LEGACY REVERT ONLY: new atlas commits never touch UVs (the rect
        is the engine binding), so the sole live caller is :meth:`revert_lightmap` undoing an
        old ``uvRect`` marker whose commit repacked the UVs in place."""
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
                    intensity,
                    os.path.basename(path),
                    e,
                )
            finally:
                if img is not None:
                    bpy.data.images.remove(img)

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Rebuild the ``lightmap_metadata`` export channel from the scene's markers.

        The no-arg producer entry point (``FbxUtils._KNOWN_PRODUCERS``, mirror
        of mayatk's): the manifest is regenerated purely from the per-object
        :attr:`LIGHTMAP_INFO_PROP` markers, so bake settings are irrelevant —
        a default-configured instance is just a namespace here.
        """
        return cls()._publish_lightmap_metadata()

    def _publish_lightmap_metadata(self) -> Optional[str]:
        """(Re)build the lightmap manifest on the shared ``data_export`` carrier.

        Scans every object carrying a :attr:`LIGHTMAP_INFO_PROP` marker and writes one JSON
        manifest (``{"version", "objects": [...]}``) to the carrier. Regenerating from the
        markers keeps incremental bakes additive and a revert subtractive; clears the channel
        when no lightmapped objects remain. camelCase keys match unitytk's ``LightmapRecord``.
        """
        import bpy

        objects: List[Dict[str, Any]] = []
        marker_infos: List[Dict[str, Any]] = []
        for obj in bpy.data.objects:
            if self.LIGHTMAP_INFO_PROP not in obj:
                continue
            try:
                info = json.loads(obj[self.LIGHTMAP_INFO_PROP] or "{}")
            except ValueError:
                continue
            marker_infos.append(info)
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
                        obj.name,
                        uv_set,
                    )
            if uv_index != 1:
                self.logger.warning(
                    "%s: lightmap layer %r sits at UV index %d, but Unity "
                    "samples uv2 (index 1). Re-run create_lightmap_uvs before "
                    "exporting.",
                    obj.name,
                    uv_set,
                    uv_index,
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
        payload: Dict[str, Any] = {
            "version": self.LIGHTMAP_METADATA_VERSION,
            "objects": objects,
        }
        # The maps' common home (mirrors mayatk): the locate hint for consumers
        # holding only the manifest (ptk.MeshConvert reads it back out of a
        # converted GLB). Optional and additive; Unity ignores unknown fields.
        dirs = {d for d in (m.get("dir") for m in marker_infos) if d}
        if len(dirs) == 1:
            payload["dir"] = next(iter(dirs))
        return DataNodes.set_export_string(
            self.LIGHTMAP_METADATA, json.dumps(payload)
        )

    def revert_lightmap(self, objects=None) -> List[str]:
        """Undo :meth:`commit_lightmap` -- restore any legacy UV remap, drop the markers, republish.

        Current commits change nothing about the material/UVs (the atlas rect is a
        ``scaleOffset`` binding, not a UV edit), so reverting them just drops the marker.
        A LEGACY atlas commit repacked the object's lightmap UVs into its rect (recorded as
        the marker's ``uvRect``); that was a UV change, so it is inverted here first —
        restoring the original 0-1 layout so a re-bake starts clean. The baked texture and
        UV layer are otherwise left in place. ``objects=None`` clears every marked object.
        Returns the names cleared.
        """
        cleared = []
        for obj in self._marked_objects(self.LIGHTMAP_INFO_PROP, objects):
            try:
                info = json.loads(obj[self.LIGHTMAP_INFO_PROP] or "{}")
            except (ValueError, TypeError):
                info = {}
            rect = info.get("uvRect")
            if rect and [float(v) for v in rect] != list(self._IDENTITY_SCALE_OFFSET):
                uv_set = (
                    info.get("uv_set")
                    or UvUtils.find_lightmap_uv_set(obj)
                    or LIGHTMAP_UV_SET
                )
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

    def revert(self, objects=None) -> List[str]:
        """Undo the lightmap wiring -- the spelling the panel and pre-bake use.

        Kept as its own name (rather than callers reaching for :meth:`revert_lightmap`)
        because it is the stable "undo whatever this workflow did" entry point.
        """
        return self.revert_lightmap(objects)

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
    revert -> bake -> commit for the selection: :meth:`~LightmapBaker.bake_separated` +
    :meth:`~LightmapBaker.commit_lightmap` keep the full PBR material, bake lighting onto
    UV1, and stamp Unity metadata on the shared ``data_export`` carrier.

    ``b000`` first calls :meth:`~LightmapBaker.revert` to clear prior wiring so the
    bake samples the real material; the header menu's **Revert to Source** undoes it. The
    Quality combobox is populated from :meth:`~LightmapBaker.preset_store` and fills the
    Resolution / Samples dials (the source of truth at bake time). The Packing combobox
    (``cmb002``) picks how the maps are laid out — Per-Object or Atlas by
    Material (:meth:`~LightmapBaker.bake_atlas`); both are live.

    Tentacle-independent (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` ``fmt`` helper is
    deferred into the methods that use it (headless Blender ships no Qt binding).
    """

    # Packing labels for the Packing combobox (cmb002). Per-Object (index 0, the default) keeps
    # one full-resolution map per object; Atlas by Material (index 1) consolidates a material
    # group into one shared EXR, each object's rect committed as its per-instance scaleOffset
    # binding via :meth:`LightmapBaker.bake_atlas`. _packing() reads it back.
    _PACKING_LABELS = ("Per-Object (one map each)", "Atlas by Material (shared map)")

    # Fixed lightmap sizes (square, px) for the Resolution combobox
    # (cmb_resolution). Power-of-two atlas sizes; every Quality preset lands on
    # one of these. _resolution() reads the selection back as an int.
    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    # Scope labels for the Scope combobox (cmb_scope): which objects b000 bakes.
    # Selected (index 0, default) preserves the prior selection-only behavior;
    # _scope() / _scope_objects() resolve it to the mesh objects to bake.
    _SCOPE_LABELS = ("Selected", "Visible", "Scene")

    # Footer tail common to every lighting-only commit -- b000's per-object branch states it
    # alone, its atlas branch appends it to the consolidation count, so the two can't drift
    # (mirrors mayatk's ``_LIGHTING_ONLY_TAIL``).
    _LIGHTING_ONLY_TAIL = (
        "Maps kept; lightmap + Unity metadata stamped. Export the FBX."
    )

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
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Lightmap Baker",
                body="Bake Blender scene lighting (Cycles) into a texture per object for game "
                "engines (Unity-first) and wire it up in one step — no manual export prep.",
                steps=[
                    "Choose a <b>Scope</b> — bake the <b>Selected</b> objects (default), all "
                    "<b>Visible</b> meshes, or the whole <b>Scene</b>.",
                    "Pick a <b>Mode</b> and <b>Packing</b> (see below) and a <b>Quality</b> "
                    "preset (fills Resolution / Samples; override either to taste).",
                    "Optionally set an <b>Output Directory</b> — empty writes to the "
                    "workspace's texture folder; a relative entry (e.g. <i>lightmaps</i>) "
                    "lands under it, so the setting travels with the project; an absolute "
                    "one is used as-is.",
                    "Press <b>Bake Lightmaps</b>, then export the FBX with <b>Custom "
                    "Properties</b> enabled (so the hidden <i>data_export</i> Empty carries "
                    "the Unity wiring).",
                ],
                sections=[
                    (
                        "Mode: Lighting Only — real lightmapping (default)",
                        [
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
                            "sharing a material into one shared, area-weighted EXR; each object's "
                            "rect is published as its per-instance <i>scaleOffset</i> (Unity's "
                            "native binding), so instanced/linked copies each get their own patch "
                            "while still sharing one mesh.",
                        ],
                    ),
                    (
                        "Non-destructive",
                        [
                            "Nothing is deleted — the source material stays in the scene and the "
                            "restore data is stamped on the object.",
                            "<b>Revert to Source</b> (header menu) undoes the wiring; re-baking "
                            "auto-reverts first.",
                        ],
                    ),
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
            return CoreUtils.selected_objects()
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

    def txt_output_dir_init(self, widget) -> None:
        """Add a directory browser to the optional output-directory field.

        No clear button (mirrors mayatk's twin): the value arrives from the
        browse dialog as often as it is typed, and a mis-click would drop a
        path the user picked and can't retype -- the field's *empty* default is
        one keystroke away anyway (see :meth:`_output_dir`).
        """
        widget.option_box.browse(
            mode="directory",
            title="Lightmap output directory",
            tooltip="Browse for the lightmap output directory…",
            start_dir=self._output_dir,
            callback=self._relativize_output_dir,
        )

    def _relativize_output_dir(self, path: str) -> None:
        """Store a browsed dir under the texture folder as a *relative* path.

        The dialog can only hand back an absolute path, but the portable form
        is the relative one: a project moved (or a teammate's copy) still bakes
        into the same subfolder. Anything outside the texture folder is left
        absolute -- that is what the user picked.
        """
        base = self._base_output_dir()
        if not (path and base and ptk.FileUtils.is_under(path, base)):
            return
        rel = ptk.FileUtils.convert_to_relative_path(path, base, prepend_base=False)
        self.ui.txt_output_dir.setText("" if rel == "." else rel)

    def txt000_init(self, widget) -> None:
        """Add the Prefix / Suffix / Auto picker to the name-affix field."""
        widget.option_box.clear_option = True
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
        # An empty field falls back to the placeholder default (the .ui's single source
        # for it), so a cleared field never bakes affix-less files.
        field = self.ui.txt000
        affix = field.text().strip() or field.placeholderText()
        prefix, suffix = field.option_box.resolve_affix(affix, default="suffix")
        atlas = self._packing() == "atlas"
        # Atlas packing is chosen BEFORE baking, not after: bake_atlas plans the layout up
        # front so each object bakes at the size it will occupy, instead of baking a full
        # map per object and downscaling most of it away.
        bake = self._baker.bake_atlas if atlas else self._baker.bake_separated

        # Indeterminate marquee + per-object text in OUR footer (mirrors mayatk's
        # twin): a Cycles bake reports no sub-progress, so a percentage would sit
        # at 0 and jump, but the text still says which object and how far in.
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

        if atlas:
            # One shared EXR per primary material; UVs stay the shared [0,1] unwrap and
            # each object's rect is committed as its scaleOffset — the per-instance engine
            # binding (Unity lightmapScaleOffset; glTF KHR_texture_transform), which is
            # what lets linked duplicates share one mesh yet own distinct patches.
            rects = {name: so for name, (_path, so) in result.items()}
            result = {name: path for name, (path, _so) in result.items()}
            self._baker.commit_lightmap(result, scale_offsets=rects)
            atlases = len(set(result.values()))
            tail = (
                f"Consolidated into {atlases} atlas{'es' if atlases != 1 else ''} by "
                f"material. {self._LIGHTING_ONLY_TAIL}"
            )
        else:
            self._baker.commit_lightmap(result)
            tail = self._LIGHTING_ONLY_TAIL
        self._last_output_dir = os.path.dirname(next(iter(result.values())))
        count = len(result)
        self.ui.footer.setText(
            f"Baked {count} object{'s' if count != 1 else ''} → "
            f"{self._last_output_dir}. {tail}"
            + self._black_bake_warning(result)
        )

    # A committed lightmap whose brightest map's mean sits below this is not a
    # dark look, it is an unlit render (mirrors mayatk's guard; measured there:
    # unlit 0.008 vs properly lit 1.0+ -- two orders of magnitude apart).
    _BLACK_BAKE_MEAN: float = 0.02

    def _black_bake_warning(self, mapping) -> str:
        """A footer warning when the committed maps are essentially unlit, else ''.

        A black bake is a FAITHFUL render of an unlit scene, so nothing
        upstream errors and the artist otherwise finds out in the web preview,
        where it reads as a pipeline bug. Reads each map through ``bpy``'s own
        image IO (Blender ships no cv2); any unreadable map is skipped -- the
        guard must never break a finished bake.
        """
        try:
            import bpy
            import numpy as np

            means = []
            for path in set(mapping.values()):
                try:
                    img = bpy.data.images.load(path, check_existing=False)
                except Exception:
                    continue
                try:
                    # foreach_get, not pixels[:] -- the slice materializes a
                    # Python float list (a 4K atlas is ~67M floats, seconds of
                    # stall right after the bake); the bulk copy is C-speed.
                    px = np.empty(len(img.pixels), dtype=np.float32)
                    img.pixels.foreach_get(px)
                    if px.size:
                        means.append(float(px.reshape(-1, 4)[:, :3].mean()))
                finally:
                    bpy.data.images.remove(img)
            if not means or max(means) >= self._BLACK_BAKE_MEAN:
                return ""
            peak = max(means)
        except Exception:
            return ""
        self.logger.warning(
            "Bake is essentially BLACK (brightest map mean %.4f). The bake "
            "renders the scene's own lights: check light power/visibility.",
            peak,
        )
        return (
            "  WARNING: bake is essentially BLACK — check light power "
            "(see the console)."
        )

    # ------------------------------------------------------------------ header menu
    def revert_to_source(self) -> None:
        """Undo the bake wiring on the selected objects (or all baked ones)."""
        if self._baker is None:
            self._baker = LightmapBaker()
        selection = CoreUtils.selected_objects() or None
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
            try:
                ptk.FileUtils.reveal_in_file_manager(out)
            except (FileNotFoundError, OSError) as e:
                self.ui.footer.setText(str(e))
        else:
            self.ui.footer.setText("No output folder yet — bake first.")

    # ------------------------------------------------------------------ helpers
    def _output_dir(self) -> str:
        """The bake's output directory: the field, resolved against the texture folder.

        Empty field -> :meth:`_base_output_dir` itself. A subdirectory entry is joined
        onto it so the setting survives a project move; a full path is taken as-is. The
        directory itself is created by the bake."""
        base = self._base_output_dir()
        return ptk.FileUtils.resolve_output_dir(self.ui.txt_output_dir.text(), base)

    @staticmethod
    def _base_output_dir() -> str:
        """What a relative Output Directory is relative to (and the default when it is
        empty): the workspace's texture folder (its ``sourceImages`` rule for a marked
        workspace.mel project, else ``textures`` next to the .blend), or a temp dir until
        the file has been saved. The header menu's "Open Output Folder" — mayatk's
        counterpart is "Open Sourceimages Folder" — browses the resolved output dir."""
        import tempfile

        from blendertk.env_utils._env_utils import EnvUtils

        return EnvUtils.source_images_dir() or os.path.join(
            tempfile.gettempdir(), "textures"
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("lightmap_baker", reload=True)
    ui.show(pos="screen", app_exec=True)
