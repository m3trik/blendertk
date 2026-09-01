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

import contextlib
import json
import os
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.light_utils._light_utils import LightUtils
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
        samples: int = 64,
        denoise: bool = True,
        device: Optional[str] = None,
        bounces: int = 4,
        include_environment: bool = True,
    ):
        super().__init__()
        # Bake the scene's world (an HDRI environment) along with its lights.
        # ON is the scene as authored -- the historical behaviour. OFF detaches
        # the world for the duration (see :meth:`_muted_environment`): an HDRI
        # is often a backdrop / look-dev convenience rather than the room's
        # real lighting, and baking it in is a flat ambient lift that cannot be
        # removed afterwards. Twin of mayatk's, where the same toggle mutes the
        # aiSkyDomeLight.
        self.include_environment = bool(include_environment)
        # The generic Cycles bake-to-texture primitive (mat_utils) owns resolution/samples; this
        # workflow (UV2, commit/revert, engine metadata) composes it — mirror of mayatk's
        # TextureBaker / LightmapBaker split. ``resolution``/``samples`` stay readable/settable on
        # the baker (below) as a single source of truth (no drift between the two objects).
        # ``denoise``/``device`` are Cycles quality/throughput knobs owned by the same primitive.
        # ``samples`` is Cycles PATHS, deliberately NOT mayatk's 5 (Arnold AA
        # samples) -- see TextureBaker.__init__ for why mirroring the API does
        # not mean mirroring the number across a unit change.
        self._texture_baker = TextureBaker(
            resolution, samples, denoise, device, bounces
        )
        # Latch for the pre-bake unlit-scene guard (warn once per instance).
        self._warned_no_lights = False

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

    @property
    def bounces(self) -> int:
        """Diffuse bounces the bake integrates -- role-twin of mayatk's ``gi_depth``."""
        return self._texture_baker.bounces

    @bounces.setter
    def bounces(self, value: int) -> None:
        self._texture_baker.bounces = int(value)

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
        """Construct a baker from a preset (``resolution`` / ``samples`` / ``bounces``).

        ``overrides`` win over the preset; extra preset keys (``description``) are ignored.
        Built-ins (Cycles samples, denoised): ``preview`` (256/64), ``quest`` (1024/256),
        ``desktop`` (2048/512), ``hero`` (4096/1024). The tiers name an ATLAS size, and an
        atlas is shared by a whole material group -- a 40-piece room on one material gets
        1/40th of it each, which is why an environment needs a tier above its per-object
        intuition.

        ``bounces`` rides the tier for the same reason mayatk's ``gi_depth`` does: in a
        closed room it is the biggest quality-per-second lever, and a preset that named
        only resolution and samples would leave the bake at whatever the scene last
        rendered with. The tiers are NOT mayatk's Arnold depths, though -- measured on
        one production room, Cycles at 4 bounces already sits at 0.76x an Arnold
        ``gi_depth`` 2 bake of the same scene, so the two renderers' depth numbers are
        not interchangeable and the level difference is method (Arnold bakes through a
        white card) rather than bounce count. Every tier but ``preview`` therefore
        keeps Cycles' own default of 4: pinning is here to make a bake REPRODUCIBLE,
        not to restyle one that was already being produced at the factory default,
        and ``quest`` is the default tier on both the panel and the Maya bridge.
        Only ``preview``, which advertises speed, trades bounces for it.
        """
        store = cls.preset_store()
        if not store.exists(name):
            raise ValueError(
                f"Unknown lightmap preset {name!r}. Available: {store.list()}"
            )
        data = {**store.load(name), **overrides}
        kwargs: Dict[str, Any] = {
            k: int(data[k]) for k in ("resolution", "samples", "bounces") if k in data
        }
        # Constructor args a preset does not carry but a caller may override --
        # previously dropped silently, so from_preset(name, device="CPU") built a
        # GPU baker and nothing said so.
        for key in ("denoise", "device", "include_environment"):
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
        heal: bool = True,
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

            heal: Refill each map's background and rendered-dead texels before
                returning it (see :meth:`_heal_dead_texels`). Leave it on for any
                map that is a DELIVERABLE. :meth:`bake_atlas` turns it off because
                its maps are intermediates that :meth:`_assemble_atlas_exr` masks
                with the same rule while it composites them -- healing there is a
                full load/save round trip per tile for an answer thrown away.

        Returns ``{object_name: lightmap_path}`` for each successful bake.
        """
        meshes = TextureBaker.resolve_meshes(objects)
        if not meshes:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        self._warn_if_unlit_scene()

        uv_set = uv_set or LIGHTMAP_UV_SET
        if create_uvs:
            UvUtils.create_lightmap_uvs(meshes, uv_set=uv_set, quiet=True)

        with self._muted_environment():
            result = self._texture_baker.bake(
                meshes,
                bake_type="DIFFUSE",
                pass_filter={"DIRECT", "INDIRECT"},
                use_pass_color=False,  # lighting-only excludes albedo (native white-card)
                output_dir=(
                    output_dir or TextureBaker.default_output_dir("baked_lighting")
                ),
                prefix=prefix,
                suffix=suffix,
                margin=margin,
                # Per-object: target the object's own lightmap UV (robust to a
                # pre-existing, differently-named lightmap layer; falls back to
                # the standard set name).
                uv_set=lambda o: UvUtils.find_lightmap_uv_set(o) or uv_set,
                stem=stem,
                size=size,
                on_progress=on_progress,
            )
        # EVERY delivered map, not just the ones an atlas consumes. Cycles'
        # native margin extends each island by a fixed few texels (~16 at 1024)
        # and leaves the REST of the map exact black -- which every mip level
        # averages back into the island as a dark halo the moment the texture is
        # minified, i.e. a visible seam on tiled geometry at distance. mayatk's
        # twin heals every separated map; here only the atlas path did, so the
        # default Per-Object bake -- the panel's own default -- shipped the halo,
        # and the same object came back different depending on the packing mode.
        if heal:
            for path in result.values():
                self._heal_dead_texels(path)
        return result

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
                # Locate hint for manifest-only consumers, in the PORTABLE
                # spelling (``//``-relative when inside the project -- the rule
                # textures follow; mirrors mayatk): a teammate's machine mounts
                # the cloud project elsewhere, and an absolute folder resolves
                # nowhere there. Expanded to absolute when the manifest is
                # published, on the publishing machine.
                "dir": self._portable_dir(path),
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
                # The tiles are intermediates: the assembly masks them with the
                # SAME dead-texel rule (``_signal_mask``) while compositing, so
                # healing each one first is a load/save round trip per object for
                # an answer that is recomputed and discarded. A solo group, which
                # skips the assembly, is healed by ``_pack_group`` instead.
                heal=False,
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
            obj.name
            for obj in sorted(meshes, key=lambda o: o.name)  # deterministic order
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
            plan[key] = [(n, [float(v) for v in rect]) for n, rect in zip(group, rects)]
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
        # A map the layout does not name still has to come out the other side:
        # this method's contract is that a bake is never lost. It reaches here
        # when a name no longer resolves to a mesh (renamed or deleted between
        # bake and pack) or when a caller hands in a plan built from a
        # different set -- both of which the layout walk below would otherwise
        # drop in silence. Each becomes its own single-object group, i.e. its
        # own map with the identity rect, which is what a solo group means.
        laid_out = {n for entries in plan.values() for n, _rect in entries}
        orphans = [n for n in sorted(mapping) if n not in laid_out]
        if orphans:
            self.logger.warning(
                "Atlas: %d map(s) are not in the layout; keeping each as its "
                "own map (identity rect): %s",
                len(orphans),
                ", ".join(orphans[:5]),
            )
            plan = dict(plan)
            for name in orphans:
                plan[name] = [(name, list(self._IDENTITY_SCALE_OFFSET))]

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
            # Idempotent safety net: a map baked HERE was already healed on
            # the way out of the bake, but ``pack_atlas`` is public and may be
            # handed maps from anywhere.
            self._heal_dead_texels(path)
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
                    ptk.ImgUtils.inset_rects_to_texel_centers([so], self.resolution)[0]
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

    @classmethod
    def _signal_mask(cls, rgb):
        """Bool mask of the texels in *rgb* that are this bake's own lighting.

        THE definition of signal for this module, shared by the per-map heal
        (:meth:`_heal_dead_texels`) and the atlas assembly's per-tile rescue
        (:meth:`_assemble_atlas_exr`) so a map cannot mean one thing on its own
        and another inside an atlas. Everything at or below
        ``max(_DEAD_TEXEL_ABS, _DEAD_TEXEL_FRACTION * the map's own lit median)``
        is background or rendered-dead occlusion, not lighting.

        Parameters:
            rgb: HxWx3 float array (a linear lightmap's colour channels).

        Returns:
            The mask, or ``None`` when nothing in *rgb* is lit at all -- there is
            no median to calibrate against, and the caller decides what an
            entirely dark map means.
        """
        import numpy as np

        lum = rgb.max(axis=-1)
        lit = lum > cls._DEAD_TEXEL_ABS
        if not lit.any():
            return None
        floor_ = max(
            cls._DEAD_TEXEL_ABS,
            cls._DEAD_TEXEL_FRACTION * float(np.median(lum[lit])),
        )
        return lum > floor_

    def _heal_dead_texels(self, path: str) -> None:
        """Refill *path*'s non-signal texels from their nearest lit ones, in place.

        Two populations are not this object's lighting and must not survive into a
        delivered map, because every mip level averages them back into the island
        as a dark halo -- a visible seam on tiled geometry at distance:

        * **background** -- exact zeros beyond the reach of Cycles' native margin
          (which extends each island only a fixed few texels).
        * **rendered-dead** -- texels the bake DID render at ~no radiance because
          their geometry is occluded (below a floor slab, behind trim, inside a
          panel overlap). Cut at :attr:`_DEAD_TEXEL_FRACTION` of the map's own lit
          median, the same rule :meth:`_assemble_atlas_exr` applies per tile, so a
          per-object map and an atlased one agree about what counts as signal.

        A fully-dark map is left alone (a black bake is a faithful render of an
        unlit scene; the panel guard warns), as is a map with nothing to heal.
        Idempotent: a healed map has no dead texels left to find.
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
            valid = self._signal_mask(rgb)
            if valid is None or valid.all() or not valid.any():
                return
            px[..., :3] = ptk.ImgUtils.fill_empty_texels(rgb, mask=valid)
            img.pixels.foreach_set(px.reshape(-1))
            img.filepath_raw = path
            img.file_format = "OPEN_EXR"
            img.save()
        except Exception as e:  # never lose a finished bake to a heal
            self.logger.warning("Dead-texel heal skipped for %s: %s", path, e)
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
            tile_signal = self._signal_mask(tile_rgb)
            # ``None`` == an all-dark tile: content wholesale (a black bake is a
            # faithful render of an unlit scene; the panel guard warns).
            mask[r0:r1, c0:c1] = True if tile_signal is None else tile_signal

        # Gutter fill via the SHARED pythontk primitives (the twin of mayatk's
        # atlas step -- one implementation, not two that drift). The previous
        # hand-rolled ``np.roll`` dilation WRAPPED at the image border: a rect
        # touching the atlas frame pulled its "neighbor" content from the
        # OPPOSITE edge of the atlas -- another object's lighting, or black.
        rgb = ptk.ImgUtils.dilate_image(
            atlas[..., :3], mask=mask, iterations=gutter + 1
        )
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

    # ------------------------------------------------------------------
    # Lightmap dependencies -- the maps the markers name, on disk NOW
    # ------------------------------------------------------------------
    #
    # Mirror of mayatk's ``LightmapBaker.lightmap_dependencies`` /
    # ``search_dirs`` / ``heal_lightmap_paths`` / ``relocate_lightmaps`` /
    # ``repath_lightmaps`` (same names, same record shape; objects are
    # datablocks or names here). A committed lightmap is a texture dependency
    # no Image datablock references: the marker records a basename plus the
    # folder the bake was COMMITTED from, and that folder is history. These
    # are the one lightmap-side answer the Texture Path Editor, the exporter's
    # path check and the GLB conversion consume.

    #: How a dependency was located: ``"hint"`` (the marker's own folder),
    #: ``"search"`` (elsewhere -- the hint is stale), ``None`` (nowhere).
    FOUND_BY_HINT: str = "hint"
    FOUND_BY_SEARCH: str = "search"

    @staticmethod
    def _portable_dir(path: str) -> str:
        """The folder of *path* in the spelling a marker STORES: ``//``-relative
        when the map sits inside the project (:func:`btk.to_project_relative`,
        the rule textures follow), absolute otherwise -- so a project mounted
        elsewhere on a teammate's machine still resolves it."""
        from blendertk.mat_utils._mat_utils import MatUtils

        return os.path.dirname(
            MatUtils.to_project_relative(os.path.abspath(path))
        ).replace("\\", "/")

    @staticmethod
    def _resolved_dir(folder: str, basename: str) -> str:
        """*folder* (a marker's stored spelling) as an absolute folder on THIS
        machine -- a ``//`` path resolved against the open .blend the way an
        image path is (``bpy.path.abspath``). ``""`` when nothing is recorded."""
        import bpy

        if not folder:
            return ""
        joined = os.path.join(folder, basename or "_")
        try:
            resolved = bpy.path.abspath(joined)
        except Exception:
            resolved = joined
        return os.path.dirname(os.path.normpath(resolved)).replace("\\", "/")

    def normalize_lightmap_paths(self, objects=None, relative: bool = True) -> int:
        """Rewrite every in-scope marker's folder to its portable (or absolute) spelling.

        The lightmap half of the Texture Path Editor's *Normalize Paths* /
        *Make Paths Absolute* (mirror of mayatk): files are never touched, the
        folder is re-spelled ``//``-relative when it lies inside the project
        (``relative=True``) or expanded to absolute (``relative=False``), and
        the manifest is republished. Returns how many markers changed.
        """
        dirs_by_map: Dict[str, str] = {}
        for _obj, info in self._marker_records(objects):
            basename = os.path.basename(str(info.get("map") or ""))
            folder = self._resolved_dir(str(info.get("dir") or ""), basename)
            if folder:
                dirs_by_map[basename.lower()] = folder
        if not dirs_by_map:
            return 0
        return self.repath_lightmaps(dirs_by_map, objects, relative=relative)

    def _marker_records(self, objects=None) -> List[Tuple[Any, Dict[str, Any]]]:
        """``[(object, marker info)]`` for every marked object in scope.

        *objects* (names or datablocks) scopes to those objects AND their
        descendants (an export set names roots; the lightmapped meshes sit
        under them). ``None`` is the whole scene; an empty list is nothing.
        """
        import bpy

        if objects is None:
            scoped = list(self._marked_objects(self.LIGHTMAP_INFO_PROP, None))
        else:
            seen: set = set()
            scoped = []
            for o in ptk.make_iterable(objects):
                root = bpy.data.objects.get(o) if isinstance(o, str) else o
                if root is None:
                    continue
                for obj in (root, *root.children_recursive):
                    if obj.name in seen or self.LIGHTMAP_INFO_PROP not in obj:
                        continue
                    seen.add(obj.name)
                    scoped.append(obj)
        records: List[Tuple[Any, Dict[str, Any]]] = []
        for obj in sorted(scoped, key=lambda o: o.name):
            try:
                info = json.loads(obj[self.LIGHTMAP_INFO_PROP] or "{}")
            except (ValueError, TypeError):
                continue
            if info.get("map"):
                records.append((obj, info))
        return records

    def lightmap_dependencies(
        self, objects=None, search_dirs=None, walk: bool = True
    ) -> List[Dict[str, Any]]:
        """Every lightmap the scene's markers name, resolved on disk NOW.

        One record per unique map::

            {"map": basename, "dir": recorded folder, "objects": [object names],
             "path": absolute path or None, "found_by": "hint" | "search" | None,
             "note": "" | why an unresolved map stayed unresolved}

        Resolution order is the GLB applier's (``ptk.MeshConvert.apply_glb_lightmaps``)
        so the two can never disagree about a map: the marker's own ``dir``
        hint, then *search_dirs* (default :meth:`EnvUtils.texture_search_dirs`),
        each a plain join. With *walk* a map still missing is looked for under
        the whole textures folder; a UNIQUE hit resolves it (``found_by`` =
        ``"search"``), several same-named files leave it unresolved with the
        count in ``note`` rather than guessed at.
        """
        from blendertk.env_utils._env_utils import EnvUtils

        records = self._marker_records(objects)
        if not records:
            return []
        if search_dirs is None:
            search_dirs = EnvUtils.texture_search_dirs()

        deps: Dict[str, Dict[str, Any]] = {}
        for obj, info in records:
            basename = os.path.basename(str(info.get("map") or ""))
            dep = deps.get(basename.lower())
            if dep is None:
                dep = deps[basename.lower()] = {
                    "map": basename,
                    "dir": str(info.get("dir") or "").replace("\\", "/"),
                    "objects": [],
                    "path": None,
                    "found_by": None,
                    "note": "",
                }
            dep["objects"].append(obj.name)

        for dep in deps.values():
            attempts = [
                (self.FOUND_BY_HINT, self._resolved_dir(dep["dir"], dep["map"]))
            ]
            attempts.extend((self.FOUND_BY_SEARCH, d) for d in search_dirs)
            for found_by, folder in attempts:
                candidate = os.path.join(folder, dep["map"]) if folder else ""
                if candidate and os.path.isfile(candidate):
                    dep["path"] = os.path.abspath(candidate).replace("\\", "/")
                    dep["found_by"] = found_by
                    break

        pending = [d for d in deps.values() if d["path"] is None]
        root = EnvUtils.source_images_dir()
        if walk and pending and root and os.path.isdir(root):
            wanted = {d["map"].lower() for d in pending}
            by_name: Dict[str, List[str]] = {}
            for folder, _dirs, files in os.walk(root):
                for name in files:
                    if name.lower() in wanted:
                        by_name.setdefault(name.lower(), []).append(
                            os.path.join(folder, name)
                        )
            for dep in pending:
                candidates = by_name.get(dep["map"].lower()) or []
                if len(candidates) == 1:
                    dep["path"] = os.path.abspath(candidates[0]).replace("\\", "/")
                    dep["found_by"] = self.FOUND_BY_SEARCH
                elif candidates:
                    dep["note"] = (
                        f"ambiguous: {len(candidates)} same-named files under "
                        f"{root} -- not guessing"
                    )
        return list(deps.values())

    @classmethod
    def search_dirs(cls, objects=None) -> List[str]:
        """Where this scene's lightmaps can be found NOW, for a consumer that joins.

        :meth:`EnvUtils.texture_search_dirs` plus the folder of every map the
        markers name that was found somewhere else -- so the GLB applier's
        ``search_dirs`` (a basename JOINED against a list) reaches a map the
        walk had to go looking for. Existing folders, deduplicated.
        """
        from blendertk.env_utils._env_utils import EnvUtils

        dirs = list(EnvUtils.texture_search_dirs())
        seen = {os.path.normcase(os.path.abspath(d)) for d in dirs}
        for dep in cls().lightmap_dependencies(objects, search_dirs=dirs):
            if not dep["path"]:
                continue
            folder = os.path.dirname(dep["path"])
            key = os.path.normcase(os.path.abspath(folder))
            if key not in seen and os.path.isdir(folder):
                seen.add(key)
                dirs.append(folder)
        return dirs

    def heal_lightmap_paths(self, objects=None) -> Dict[str, Any]:
        """Rewrite stale marker hints to where the maps actually are; republish.

        The lightmap half of the exporter's *Resolve Invalid Texture Paths*
        task: a map found by search has a hint that resolves nowhere, and a
        consumer holding only the manifest would miss it. Files are never
        touched. Returns ``{"healed": [(map, old_dir, new_dir)], "missing":
        [records]}``.
        """
        deps = self.lightmap_dependencies(objects)
        moves: Dict[str, str] = {}
        healed: List[Tuple[str, str, str]] = []
        for dep in deps:
            if dep["path"] and dep["found_by"] == self.FOUND_BY_SEARCH:
                new_dir = os.path.dirname(dep["path"])
                moves[dep["map"].lower()] = new_dir
                healed.append((dep["map"], dep["dir"], new_dir))
        if moves:
            self.repath_lightmaps(moves, objects)
        return {"healed": healed, "missing": [d for d in deps if not d["path"]]}

    def relocate_lightmaps(
        self,
        dest_dir: str,
        source_dir: str = "",
        mode: str = "copy",
        objects=None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Gather the scene's lightmaps into *dest_dir* and repoint the markers.

        The lightmap half of the Texture Path Editor's *Find & Copy*: a map
        that resolves is its own source; one that does not is searched for
        under *source_dir* (recursively; the newest same-named file wins). A
        source already in *dest_dir* needs no file operation and still gets its
        hint rewritten. Relocation goes through the panel's own collision
        policy (``_safe_relocate``: same-size = reuse, different-size = skip).

        Returns::

            {"relocate": [(src, dst)], "in_place": [src], "missing": [records],
             "copied": [(src, dst)], "updated": markers rewritten}
        """
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        result: Dict[str, Any] = {
            "relocate": [],
            "in_place": [],
            "missing": [],
            "copied": [],
            "updated": 0,
        }
        deps = self.lightmap_dependencies(objects)
        if not deps:
            return result
        dest_dir = dest_dir.replace("\\", "/")

        sources: Dict[str, str] = {}
        for dep in deps:
            if dep["path"]:
                sources[dep["map"].lower()] = dep["path"]
        pending = {d["map"].lower() for d in deps if d["map"].lower() not in sources}
        if pending and source_dir and os.path.isdir(source_dir):
            newest: Dict[str, Tuple[float, str]] = {}
            for folder, _dirs, files in os.walk(source_dir):
                for name in files:
                    key = name.lower()
                    if key not in pending:
                        continue
                    hit = os.path.join(folder, name)
                    try:
                        mtime = os.path.getmtime(hit)
                    except OSError:
                        mtime = 0.0
                    if key not in newest or mtime > newest[key][0]:
                        newest[key] = (mtime, hit)
            for key, (_mtime, hit) in newest.items():
                sources[key] = os.path.abspath(hit).replace("\\", "/")
        result["missing"] = [d for d in deps if d["map"].lower() not in sources]

        dest_key = os.path.normcase(os.path.abspath(dest_dir))
        for src in sources.values():
            if os.path.normcase(os.path.dirname(os.path.abspath(src))) == dest_key:
                result["in_place"].append(src)
            else:
                dst = os.path.join(dest_dir, os.path.basename(src)).replace("\\", "/")
                result["relocate"].append((src, dst))
        if dry_run:
            return result

        if result["relocate"]:
            os.makedirs(dest_dir, exist_ok=True)
            for src, dst in result["relocate"]:
                if _MatUtilsInternal._safe_relocate(src, dst, mode) in (
                    "relocated",
                    "rebind",
                ):
                    result["copied"].append((src, dst))
        landed = {os.path.basename(dst).lower() for _src, dst in result["copied"]}
        landed.update(os.path.basename(p).lower() for p in result["in_place"])
        if landed:
            result["updated"] = self.repath_lightmaps(
                {key: dest_dir for key in landed}, objects
            )
        return result

    def repath_lightmaps(
        self, dirs_by_map: Dict[str, str], objects=None, relative: bool = True
    ) -> int:
        """Point every in-scope marker naming a map in *dirs_by_map* at its new folder.

        Keys are lower-case basenames. The manual repath (Browse for File / a
        typed path on a lightmap row) and the last step of
        :meth:`heal_lightmap_paths` and :meth:`relocate_lightmaps`. Files are
        never touched. The folder is stored in its portable spelling
        (``//``-relative when inside the project) unless ``relative=False``
        -- the Make Paths Absolute case. The manifest is republished once.
        Returns how many markers changed; a marker already recording that
        folder is untouched.
        """
        count = 0
        for obj, info in self._marker_records(objects):
            basename = os.path.basename(str(info.get("map") or ""))
            new_dir = dirs_by_map.get(basename.lower())
            if new_dir is None:
                continue
            if relative:
                spelling = self._portable_dir(os.path.join(new_dir, basename))
            else:
                spelling = os.path.abspath(new_dir).replace("\\", "/")
            if str(info.get("dir") or "").replace("\\", "/") == spelling:
                continue
            info["dir"] = spelling
            obj[self.LIGHTMAP_INFO_PROP] = json.dumps(info)
            count += 1
        if count:
            self._publish_lightmap_metadata()
        return count

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
        # Expanded to ABSOLUTE here, on the machine publishing it: the markers
        # keep the portable (``//``-relative) spelling, but a manifest reader
        # has no .blend to resolve it against.
        counts = Counter(
            self._resolved_dir(str(m.get("dir") or ""), str(m.get("map") or ""))
            for m in marker_infos
            if m.get("dir")
        )
        counts.pop("", None)
        if len(counts) == 1:
            payload["dir"] = next(iter(counts))
        if counts:
            # Mirror of mayatk's: EVERY folder the markers name, not only the
            # unanimous case. ``dir`` (singular) is unchanged for Unity's
            # reader; publishing only that meant a scene whose maps live in two
            # folders published NO hint, and the consumer then found a map by
            # basename alone -- which on a real project is how a stale atlas
            # from an earlier bake gets bound under rects from the current one.
            # Ordered by marker count, not alphabetically: the reader takes the
            # first folder holding a file of the right BASENAME, so the order is
            # a priority and the folder most of the bake landed in must lead.
            payload["dirs"] = [
                d for d, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ]
        return DataNodes.set_export_string(self.LIGHTMAP_METADATA, json.dumps(payload))

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

    # ------------------------------------------------------------------ guards
    # ------------------------------------------------------------------
    # Level check -- did the finished bake land in a plausible range?
    # ------------------------------------------------------------------

    #: The brightest map's mean RGB, below which a bake is not a dark look but an
    #: unlit render (mirrors mayatk's guard; measured there: unlit 0.008 vs properly
    #: lit 1.0+ -- two orders of magnitude apart).
    BLACK_BAKE_MEAN: float = 0.02
    #: ...and above which it is not a bright room but a unit error upstream. A
    #: lightmap is scene-relative irradiance, so a correctly translated room lands
    #: within a few multiples of 1.0 whatever its exposure; this sits ~2 orders above
    #: a hot-but-real bake and ~4 below the failure it was written for (a Maya area
    #: light that crossed the bridge at 5.4e8 W -- see mayatk's CHANGELOG 2026-08-29).
    BLOWN_BAKE_MEAN: float = 64.0
    #: The exact value a saturated texel holds: EXRs are written half-float, so a map
    #: sitting AT this has not merely gone bright, it has lost data.
    HALF_FLOAT_MAX: float = 65504.0

    @classmethod
    def map_levels(cls, paths) -> Dict[str, Tuple[float, float]]:
        """``{path: (mean RGB, fraction of channels at the half-float ceiling)}``.

        The shared measurement behind every "is this bake usable" question -- the
        panel's black and blown warnings and the Maya bridge's headless bake template
        all ask it, and each was otherwise going to carry its own copy of the same
        ``foreach_get`` dance. A bake has no correct ABSOLUTE level, so measuring the
        RESULT is the only thing that separates a dark look from an unlit scene, or a
        bright room from a broken unit conversion upstream.

        Read through ``bpy``'s own image IO (Blender ships no cv2) into a fresh
        datablock that is dropped again, so a map already open in the session is
        neither reused nor disturbed. An unreadable map is SKIPPED rather than raised
        on: this runs after a finished bake and must never be what loses it.

        Parameters:
            paths: Image paths. Duplicates are collapsed -- an atlas shared by 46
                objects is read once.

        Returns:
            ``{path: (mean, saturated)}`` for the maps that could be read; empty when
            none could. ``saturated`` is a fraction of RGB channels, not of texels.
        """
        import bpy
        import numpy as np

        levels: Dict[str, Tuple[float, float]] = {}
        for path in sorted(set(paths or ())):
            image = None
            try:
                image = bpy.data.images.load(path, check_existing=False)
                # foreach_get, not pixels[:] -- the slice materializes a Python float
                # list (a 4K atlas is ~67M floats, seconds of stall right after the
                # bake); the bulk copy is C-speed.
                buf = np.empty(len(image.pixels), dtype=np.float32)
                image.pixels.foreach_get(buf)
                rgb = buf.reshape(-1, image.channels)[:, :3]
                if rgb.size:
                    levels[path] = (
                        float(rgb.mean()),
                        float((rgb >= cls.HALF_FLOAT_MAX).mean()),
                    )
            except Exception:
                continue
            finally:
                if image is not None:
                    bpy.data.images.remove(image)
        return levels

    @classmethod
    def peak_level(cls, paths) -> Optional[Tuple[str, float, float]]:
        """``(path, mean, saturated)`` for the BRIGHTEST map, or ``None`` if unreadable.

        Both level guards judge a bake by its brightest map -- a black one because a
        single lit map disproves "unlit", a blown one because the worst offender is
        what the artist has to be shown -- so the reduction lives here once.
        """
        levels = cls.map_levels(paths)
        if not levels:
            return None
        path = max(levels, key=lambda p: levels[p][0])
        return (path, *levels[path])

    @contextlib.contextmanager
    def _muted_environment(self):
        """Detach the scene's world for the bake when asked to.

        ``include_environment=False`` means "bake the room's own lights, not
        the world": ``scene.world`` is unset for the duration and restored
        after, so the scene is handed back exactly as it was found. Detaching
        rather than zeroing a strength input because a world can be an
        arbitrary node graph -- there is no one input to zero, and the
        datablock itself is the thing the toggle is about.

        Twin of mayatk's, which hides the ``aiSkyDomeLight`` instead; both mean
        the same thing to the artist and to the unlit-scene guard.
        """
        prev = None
        detached = False
        if not self.include_environment:
            try:
                import bpy

                scene = bpy.context.scene
                prev = None if scene is None else scene.world
                if prev is not None:
                    scene.world = None
                    detached = True
                    self.logger.info(
                        "Include Environment is off: world %r detached for this bake.",
                        prev.name,
                    )
            except Exception as e:  # never fail a bake over the toggle
                self.logger.warning("Could not mute the world environment: %s", e)
        try:
            yield
        finally:
            if detached:
                try:
                    import bpy

                    bpy.context.scene.world = prev
                except Exception as e:  # never leave the scene changed silently
                    self.logger.error("Could not restore the world: %s", e)

    def _warn_if_unlit_scene(self) -> None:
        """Warn (once per instance) when the scene has no light source to bake.

        A lightless bake silently produces a black lightmap -- worth a loud hint BEFORE the
        rays are spent rather than only after (the panel's post-bake ``_level_warning``
        reads the finished maps; this fires for scripted callers too, which is why it lives
        on the workflow rather than the Slots). Twin of mayatk's guard, with the Arnold
        light-type probe replaced by Blender's own: a ``LIGHT`` object, or a world background
        that emits (Cycles' analogue of ``aiSkyDomeLight`` -- blendertk ships an HDR Manager,
        so an HDRI-only scene is a genuinely lit scene and must not trip this).

        Emissive-material-only scenes still trip it; it is a warning, not a gate -- so an
        unreadable scene stays SILENT rather than raising into the bake it precedes.
        """
        if self._warned_no_lights:
            return
        try:
            import bpy

            scene = bpy.context.scene
            if scene is None:
                return
            if any(obj.type == "LIGHT" for obj in scene.objects):
                return
            # A world the bake is about to DETACH is not a light source for it,
            # so an HDRI-only scene still gets the warning when Include
            # Environment is off -- which is exactly when it is needed.
            if self.include_environment and LightUtils.world_emits(scene.world):
                return
        except Exception:  # no runtime / unreadable scene -- nothing to warn about
            return
        self._warned_no_lights = True
        self.logger.warning(
            "No lights found in the scene -- the lightmap will bake black "
            "(unless emissive materials are the only light source). Add a light, "
            "or set a world environment (light_utils' HDR Manager / "
            "LightUtils.set_world_environment)."
        )

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


class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin):
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
    Resolution / Samples dials (the source of truth at bake time); the traffic runs both
    ways, so a dial moved off the tier flips the combobox to *Custom*
    (:meth:`_preset_for_dials`, wired as one ``sb.value_from`` rule). The Packing combobox
    (``cmb002``) picks how the maps are laid out — Per-Object or Atlas by
    Material (:meth:`~LightmapBaker.bake_atlas`); both are live.

    Tentacle-independent (``ptk`` mixins only); the Qt-only ``uitk`` ``fmt`` helper is
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

    # Label for the Quality combobox row that means "whatever the dials say".
    # NOT a stored preset -- ``_apply_preset`` declines it; it is the answer
    # ``_preset_for_dials`` gives when Resolution / Samples match no tier, so
    # the combo can never keep naming a preset the bake is no longer using.
    _CUSTOM_PRESET_LABEL = "Custom"

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
        # Dial signature -> preset name, built by cmb000_init from the same
        # listing that fills the combo; _preset_for_dials reads it back.
        self._preset_by_dials: Dict[Tuple[int, int], str] = {}

        # Deferred: the switchboard builds this mid-load, before the combos are wired onto
        # self.ui — sync the dials to the shown preset on the next tick.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        self._apply_preset(self.ui.cmb000.currentText())
        # Quality follows the dials from here on: move Resolution or Samples off
        # the tier and the combo says *Custom* rather than keep naming a preset
        # the bake is no longer using. Wired AFTER the preset is applied -- the
        # rule applies immediately, and at widget-registration time the dials
        # still hold the .ui defaults, so an earlier wire-up would open on Custom.
        self.sb.value_from(
            self.ui,
            "cmb000",
            ["cmb_resolution", "spn_samples"],
            self._preset_for_dials,
        )

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
                    "preset (fills Resolution / Samples; override either to taste — the "
                    "preset then reads <i>Custom</i>). <b>Device</b> picks what Cycles "
                    "bakes on — <i>Auto</i> takes the GPU per object where it pays and "
                    "the CPU for tiles too small to repay a GPU session.",
                    "Leave <b>Include Environment</b> on to bake the scene as authored. "
                    "Off detaches the world for the bake (and restores it after), so you "
                    "get the room's own lights without the environment's flat ambient "
                    "lift — which cannot be taken back out of a map once it is in.",
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
        """Populate the Quality combobox from the shared preset store.

        A trailing *Custom* row is appended for the dials-match-no-tier case,
        and the dial-signature lookup :meth:`_preset_for_dials` reads is built
        from the same listing that fills the combo, so the two cannot disagree.
        """
        store = LightmapBaker.preset_store()
        names = store.list()
        self._preset_by_dials = {}
        for name in names:
            data = store.load(name)
            if "resolution" in data and "samples" in data:
                key = (int(data["resolution"]), int(data["samples"]))
                self._preset_by_dials.setdefault(key, name)
        widget.clear()
        # The store's user tier is free-form, so a saved preset may already be
        # named "Custom" -- appending blindly would show the row twice.
        rows = list(names)
        if self._CUSTOM_PRESET_LABEL not in rows:
            rows.append(self._CUSTOM_PRESET_LABEL)
        widget.addItems(rows)
        idx = widget.findText("quest")
        if idx >= 0:
            widget.setCurrentIndex(idx)

    def cmb000(self, index, widget) -> None:
        """Apply the selected preset's dials to Resolution / Samples.

        *Custom* is not a stored preset -- it is what the dials say when they
        match no tier -- so it applies nothing and just reports that the dials
        are in charge.
        """
        name = widget.currentText()
        if self._apply_preset(name):
            self.ui.footer.setText(f"Preset: {name}")
        elif name == self._CUSTOM_PRESET_LABEL:
            self.ui.footer.setText("Quality: Custom — Resolution / Samples as set.")

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

    #: Cycles bake device, ``(label, value)`` — mirror of mayatk's ``_DEVICES``.
    _DEVICES = (("Auto", "AUTO"), ("GPU", "GPU"), ("CPU", "CPU"))

    def cmb_device_init(self, widget) -> None:
        """Populate the Device combobox (value carried as item data); default Auto."""
        widget.clear()
        for label, value in self._DEVICES:
            widget.addItem(f"Device:\t{label}", value)
        widget.setCurrentIndex(0)  # Auto

    def _device(self) -> str:
        """The selected bake device from cmb_device (its item data)."""
        return self.ui.cmb_device.currentData() or self._DEVICES[0][1]

    def _include_environment(self) -> bool:
        """Whether the bake keeps the scene's environment (chk_environment)."""
        return bool(self.ui.chk_environment.isChecked())

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
        # Explicit key: ``txt000`` is generic enough that another panel in the
        # same host would share the auto-derived namespace.
        widget.option_box.set_affix(
            default="auto",
            settings_key="lightmap_baker_affix",
            # Fourth, custom state: take the lightmap affix from the shared
            # naming convention instead of this one field.
            convention_key="lightmap",
        )

    def _preset_for_dials(self, resolution: int, samples: int) -> str:
        """The preset whose dials are exactly these, else :attr:`_CUSTOM_PRESET_LABEL`.

        The resolver behind the ``sb.value_from`` rule wired in
        :meth:`_initialize_ui`. A pure dict lookup (built once in
        :meth:`cmb000_init`), so it costs nothing to re-run on every arrow-press
        in the Samples spinbox.
        """
        return self._preset_by_dials.get(
            (int(resolution), int(samples)), self._CUSTOM_PRESET_LABEL
        )

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
        # Bounce depth has no panel widget -- Resolution and Samples do, so the tier
        # reaches the bake through THEM, and anything the tier carries besides them
        # has to be carried by hand. Without this the preset's ``bounces`` silently
        # no-ops for every panel bake (exactly the failure mayatk's ``_preset_gi``
        # comment records for gi_depth/gi_samples), leaving the panel on the
        # constructor default whichever tier is showing.
        self._preset_gi = {k: int(data[k]) for k in ("bounces",) if k in data}
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
            device=self._device(),
            include_environment=self._include_environment(),
            # Dials the tier carries but the panel does not show (mirrors mayatk).
            **getattr(self, "_preset_gi", {}),
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
            f"{self._last_output_dir}. {tail}" + self._level_warning(result)
        )

    def _level_warning(self, mapping) -> str:
        """A footer warning when the committed maps are unlit OR blown out, else ''.

        Both directions, because a lightmap has no correct ABSOLUTE level and each
        failure is a *successful* render of a wrong scene: nothing upstream errors,
        and the artist otherwise finds out in the web preview, where it reads as a
        pipeline bug. The black half caught an unlit room; the blown half was missing
        until a Maya-bridge send crossed at 5.4e8 W per fixture, saturated every atlas
        at the half-float ceiling and reported success (mayatk CHANGELOG 2026-08-29) --
        the same silent failure is reachable from this panel with hot enough lights.

        Measurement is :meth:`LightmapBaker.peak_level` (the engine owns it, so the
        bridge's headless template asks the same question the same way); an unreadable
        map is skipped there -- the guard must never break a finished bake.
        """
        try:
            peak = LightmapBaker.peak_level(mapping.values())
        except Exception:
            return ""
        if peak is None:
            return ""
        _path, mean, saturated = peak
        if mean < LightmapBaker.BLACK_BAKE_MEAN:
            self.logger.warning(
                "Bake is essentially BLACK (brightest map mean %.4f). The bake "
                "renders the scene's own lights: check light power (W), that the "
                "lights are visible to the RENDER (not just the viewport), and "
                "that the world background is not black -- an emissive material "
                "lights a Cycles bake only while its object is render-visible.\n"
                "Scene lights at bake time:\n%s",
                mean,
                self._light_audit(),
            )
            return (
                "  WARNING: bake is essentially BLACK — check light power "
                "(see the console)."
            )
        if mean >= LightmapBaker.BLOWN_BAKE_MEAN:
            self.logger.warning(
                "Bake is BLOWN OUT (brightest map mean %.4g%s). A lightmap is "
                "scene-relative irradiance and should land within a few multiples "
                "of 1.0 whatever the exposure, so this is a light-POWER problem "
                "rather than a bright room.\nScene lights at bake time:\n%s",
                mean,
                ", %.0f%% of it at the half-float ceiling — data lost"
                % (saturated * 100.0)
                if saturated > 0.001
                else "",
                self._light_audit(),
            )
            return "  WARNING: bake is BLOWN OUT — check light power (see the console)."
        return ""

    @staticmethod
    def _light_audit() -> str:
        """One line per scene light: the attrs that decide whether a bake is lit.

        Attached to the black-bake warning so a dark result carries its own diagnosis --
        power, scale, render visibility and the world strength are exactly the dials a black
        bake traces back to, and none of them are visible in the bake output itself. Twin of
        mayatk's ``_light_audit`` (Arnold's intensity/exposure/normalize -> Cycles' watts).

        Total-failure tolerant: it is evaluated as an argument to the black-bake warning,
        which sits OUTSIDE that guard's try/except, so a raise here would propagate out of a
        finished bake -- the one thing the guard promises never to do.
        """
        try:
            import bpy

            scene = bpy.context.scene
            if scene is None:
                return "  <no scene>"
            rows = LightmapBakerSlots._light_rows(scene)  # staticmethod: no self here
            world = scene.world
            rows.append(
                f"  <world>: emits={LightUtils.world_emits(world)}"
                if world is not None
                else "  <world>: none"
            )
            return "\n".join(rows)
        except Exception:
            return "  <scene unreadable>"

    @staticmethod
    def _light_rows(scene) -> List[str]:
        """One ``  <name>: k=v ...`` row per light in *scene* (the audit's per-light half).

        Each light is read under its own guard, so a single unreadable one costs its row
        rather than the whole table.
        """
        rows: List[str] = []
        for obj in scene.objects:
            if obj.type != "LIGHT":
                continue
            try:
                data = obj.data
                sx, sy, _sz = obj.scale
                energy = getattr(data, "energy", float("nan"))
                bits = [
                    f"type={data.type}",
                    # Blender's own label for the dial, so the artist reads the same word
                    # the UI shows: a SUN's energy is irradiance (W/m2, "Strength"), every
                    # other type's is radiant power in watts ("Power").
                    f"strength={energy:g}"
                    if data.type == "SUN"
                    else f"power={energy:g}W",
                    f"scale={sx:g}x{sy:g}",
                    # hide_render is what the BAKE obeys; hide_viewport is not enough to
                    # explain a black bake on its own, so report both separately.
                    f"render_visible={not obj.hide_render}",
                    f"viewport_visible={obj.visible_get()}",
                ]
                if data.type == "AREA":
                    bits.append(f"size={data.size:g}")
                rows.append(f"  {obj.name}: " + "  ".join(bits))
            except Exception:
                rows.append(f"  {getattr(obj, 'name', '?')}: <unreadable>")
        return rows or ["  <no lights in the scene>"]

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
