# !/usr/bin/python
# coding=utf-8
"""High-level lightmap baking workflow for Blender -> game engines (Unity-first).

Blender counterpart of mayatk's ``LightmapBaker``. Where the Maya workflow had to
orchestrate Arnold RTT and an alpha-mask seam dilation, **Blender ships the bake
natively in Cycles** — so this is a much thinner adapter over ``bpy.ops.object.bake``:

* :func:`UvUtils.create_lightmap_uvs` -- packed, non-overlapping lightmap UV (UV2).
* ``bpy.ops.object.bake`` -- Cycles bakes straight into an image-texture node:
    * **Lighting only** = ``type='DIFFUSE'`` with ``pass_filter={'DIRECT','INDIRECT'}``
      (no ``'COLOR'``), each target rendered through a white card (:meth:`_white_card`,
      handed to ``TextureBaker.bake(shader=)``) as mayatk's is, so the map is the
      GEOMETRY's irradiance: dividing the colour out of the target's own material lost
      a metal's light and aliased its normal map into the lightmap.
      (There is no albedo-fused level: ``COMBINED`` is not lightmapping.)
* ``scene.render.bake.margin`` -- native gutter/seam padding, only a few texels wide:
  :meth:`_heal_dead_texels` and the atlas assembly fill the rest.
* ``ptk.SceneRecords.LIGHTMAPS`` -- the export manifest record (a custom prop on the
  ``data_export`` Empty through ``DataNodes``, rides the FBX; no sidecar file). Informational
  -- the mesh's UV2 samples the map in any engine; unitytk's optional editor helper reads it
  to auto-bind Unity's native slots.

**One bake level, and it is real lightmapping**, non-destructive and exposed in the panel:
:meth:`LightmapBaker.bake` bakes lighting-only irradiance onto the lightmap UV (channel 1)
and records it (:class:`LightmapRecords`). The object's full PBR material and texture UV0
are **kept untouched** -- the engine composites ``albedo x lightmap``. It is the whole
workflow, as the panel runs it; :meth:`bake_separated` / :meth:`bake_atlas` are its two
bake mechanisms, for a caller that records the maps itself.

:meth:`revert` (== :meth:`revert_lightmap`) undoes it. A *fused unlit* level (albedo x
lighting flattened behind an Emission material) was removed: it is not lightmapping, it
discards every other map, and it only ever added a mode to choose wrongly from. Quality
tiers come from :meth:`from_preset` (pythontk ``PresetStore``). HDR EXR throughout.

The engine surface is Qt-free and defers ``import bpy`` (headless-importable); only the
panel (``lightmap_baker_slots.LightmapBakerSlots``) touches Qt, lazily.

The ``.ui`` shares mayatk's objectNames control for control, except where Cycles has
no counterpart (Arnold's GI Samples: one Cycles sample count covers every ray -- ledgered
in ``tentacle/docs/parity_map.py``). Now that uitk host-namespaces the QSettings branch per
DCC (``Switchboard.add_ui`` / ``MainWindow._relative_state`` via ``context_tags``),
identical objectNames across the two copies of the same panel no longer collide in the
shared "uitk"/"shared" registry root, so there is no need to renumber widgets to dodge it.
Both ``cmb002`` packing modes are live: "Atlas by Material" (per-material consolidation via
:meth:`LightmapBaker.pack_atlas`, the Blender port of mayatk's atlas packer) and
"Per-Object" (one full-resolution map each).
"""

import contextlib
import hashlib
import json
import math
import os
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.light_utils._light_utils import LightUtils
from blendertk.mat_utils.bake_sets import LightmapExcludeSet, LightmapIsolateSet
from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from blendertk.light_utils.lightmap_baker._probe_placement import ProbePlacement
from blendertk.uv_utils._uv_utils import UvUtils, LIGHTMAP_UV_SET
from blendertk.mat_utils.texture_baker import TextureBaker


@dataclass
class LightmapBakeResult:
    """What one :meth:`LightmapBaker.bake` did -- the same shape in mayatk and blendertk.

    Attributes:
        maps: ``{object: map path}``, every map the bake wrote and recorded.
        rects: ``{object: [scaleX, scaleY, offsetX, offsetY]}``, each object's
            engine binding into its map (the identity for a map of its own).
        excluded: Objects the scene's lightmap exclusion set left out. They
            keep any map they already had.
        hidden: Objects left out because the renderer shows nothing of them.
            Always empty here: Cycles bakes a hidden object like any other
            (:meth:`LightmapBaker.bake_targets`); mayatk's Arnold bake is the
            one that has to leave them out.
        unbaked: Objects the bake was asked for and produced nothing for (a
            cancel, a failed render). They keep any map they already had.
        retired: Map files the bake superseded and set aside (the Recycle
            Bin, or the ``superseded`` temp store) -- what its objects
            read before, that nothing reads now
            (:meth:`LightmapRecords.superseding`).
        refused: Why nothing was baked, as a sentence for the artist, or
            ``None``.
        verdict: A warning about the finished maps' level (an unlit or a
            blown-out bake), as a sentence, or ``None``.
        probe: The reflection probe the bake captured of the room it lit
            (:meth:`LightmapBaker.bake_probe`), or ``None``.
        in_place: Objects written back into the cells they own in the atlas
            they read (:meth:`LightmapBaker._in_place_plan`): every other
            object reading it keeps every texel it had.
        repacked: ``{atlas file name: why}`` -- the atlases whose whole
            group re-baked into a fresh layout rather than in place, and why
            (a cell that no longer fits, a member new to it, one baked before
            cells were recorded).
        report: What :meth:`LightmapBaker.rebake_changed` found changed and
            re-baked (``ptk.StaleReport``), its ``in_place`` / ``repack`` as
            the bake did them; ``None`` for any other bake.
    """

    maps: Dict[str, str] = field(default_factory=dict)
    rects: Dict[str, List[float]] = field(default_factory=dict)
    excluded: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)
    unbaked: List[str] = field(default_factory=list)
    retired: List[str] = field(default_factory=list)
    refused: Optional[str] = None
    verdict: Optional[str] = None
    probe: Optional[str] = None
    in_place: List[str] = field(default_factory=list)
    repacked: Dict[str, str] = field(default_factory=dict)
    report: Optional["ptk.StaleReport"] = None

    def __bool__(self) -> bool:
        return bool(self.maps)

    @property
    def files(self) -> List[str]:
        """The distinct map files, sorted: an atlas 40 objects share counts once."""
        return sorted(set(self.maps.values()))

    @property
    def folders(self) -> List[str]:
        """The distinct folders the maps landed in, compared the way the disk does."""
        spelled: Dict[str, str] = {}
        for path in self.maps.values():
            folder = os.path.dirname(path)
            spelled.setdefault(os.path.normcase(os.path.abspath(folder)), folder)
        return sorted(spelled.values())


@dataclass
class _BakeLights:
    """Which of the scene's light objects one bake renders with, by name.

    :meth:`LightmapBaker._bake_lights` answers it once for the bake, its
    reflection probe, the all-lights-off refusal and the light audit, so none
    of them can disagree about what lights a map. Mirror of mayatk's. The world is not in it: a
    world is not a light object, and ``include_environment`` alone decides it
    (:meth:`LightmapBaker._muted_environment`).

    Attributes:
        lights: Every light object the bake renders with, on or off as
            authored: the scene's, or the Scope's with Include Scene Lighting
            off.
        outside: The lights the Scope leaves out (Include Scene Lighting off),
            hidden from the render for the bake.
        hidden: The *lights* hidden from the render as authored, at a non-zero
            power, that Include Hidden Lights switches on (empty with it off).
    """

    lights: List[str] = field(default_factory=list)
    outside: List[str] = field(default_factory=list)
    hidden: List[str] = field(default_factory=list)


@dataclass
class _InPlacePlan:
    """Which of a bake's targets write back into their own atlas cells.

    :meth:`LightmapBaker._in_place_plan` decides it once per bake: the one
    decision point between writing an atlas in place and re-packing its whole
    group. Mirror of mayatk's.

    Attributes:
        in_place: ``{atlas path: [targets]}`` -- written into their cells.
        readers: ``{atlas path: [objects]}`` -- every object reading each of
            those atlases, the targets among them.
        entries: ``{object: baseline entry}`` for every reader -- its cell,
            extent and size (``ptk.BakeBaseline``).
        rects: ``{object: [sx, sy, ox, oy]}`` -- each reader's published
            binding (its marker's ``scaleOffset``): where a held cell's seam
            is read.
        repack: ``{atlas file name: why}`` -- the atlases whose whole group
            re-bakes instead, and why.
        expand: The members those groups add to the bake.
    """

    in_place: Dict[str, List[str]] = field(default_factory=dict)
    readers: Dict[str, List[str]] = field(default_factory=dict)
    entries: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    rects: Dict[str, List[float]] = field(default_factory=dict)
    repack: Dict[str, str] = field(default_factory=dict)
    expand: List[str] = field(default_factory=list)

    @property
    def placed(self) -> List[str]:
        """Every target written in place."""
        return [obj for objs in self.in_place.values() for obj in objs]


@dataclass
class _AtlasBake:
    """What :meth:`LightmapBaker._bake_atlas` did -- what the public
    :meth:`~LightmapBaker.bake_atlas` returns of it is :attr:`packed`.
    Mirror of mayatk's.

    Attributes:
        packed: ``{object: (map path, rect)}``.
        cells: ``{object: {"cell", "extent", "size", "packing"}}`` -- where
            in its map each object was written, for the bake baseline
            (:meth:`LightmapRecords.commit`'s ``cells``).
        in_place: Objects written back into their own cells.
        repacked: ``{atlas file name: why}`` -- groups re-packed whole.
    """

    packed: Dict[str, Tuple[str, List[float]]] = field(default_factory=dict)
    cells: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    in_place: List[str] = field(default_factory=list)
    repacked: Dict[str, str] = field(default_factory=dict)


class LightmapBaker(ptk.LoggingMixin):
    """Orchestrate the Blender lightmap workflow: UV2 -> Cycles bake -> engine export prep.

    Usage::

        baker = LightmapBaker.from_preset("mobile")       # or (resolution=, samples=)
        result = baker.bake(objects)                       # atlas by material, recorded
        result.maps                                        # {obj_name: exr_path}
        # The object keeps its full material; the lightmap rides UV channel 1 and the
        # wiring rides the FBX on the data_export Empty -- nothing is destroyed.

    ``packing="atlas"`` (the default) gives one shared map per material instead of one
    per object -- and the faster path, since it plans the atlas before baking and sizes
    every bake to its footprint. The rect is the per-instance engine binding (Unity
    ``lightmapScaleOffset``), so instances/linked duplicates are first-class.
    """

    # The scene record's names, kept here for the callers that read them off the baker;
    # the record itself is :class:`LightmapRecords`.
    #: The kind folder lightmaps go to under the scene project's texture
    #: folder when no output directory is given -- by the API and by the
    #: panel's empty field alike, in both DCCs (``TextureBaker.default_output_dir``).
    OUTPUT_SUBDIR: str = "lightmaps"
    LIGHTMAP_INFO_PROP: str = LightmapRecords.LIGHTMAP_INFO_PROP
    LIGHTMAP_INFO_ATTR: str = LightmapRecords.LIGHTMAP_INFO_ATTR  # mayatk's name
    LIGHTMAP_METADATA: str = LightmapRecords.LIGHTMAP_METADATA
    LIGHTMAP_METADATA_VERSION: int = LightmapRecords.LIGHTMAP_METADATA_VERSION
    FOUND_BY_HINT: str = LightmapRecords.FOUND_BY_HINT
    FOUND_BY_SEARCH: str = LightmapRecords.FOUND_BY_SEARCH

    # Identity atlas transform: the object's 0-1 lightmap UVs map to the whole texture.
    _IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (
        LightmapRecords.IDENTITY_SCALE_OFFSET
    )

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

    # An atlas tile is cropped to its lightmap island's bounds when either axis
    # covers less than this (``ptk.ImgUtils.crop_to_uv_bbox``; twin of mayatk
    # LightmapBaker._CROP_MAX_COVERAGE). A generated unwrap spans the whole
    # square and is never cropped; a REUSED one need not -- the production
    # walls' Maya set spans u 0..1/3, which uncropped left two thirds of every
    # wall cell to the margin fill.
    _CROP_MAX_COVERAGE: float = 0.85

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 1024,
        denoise: bool = True,
        device: Optional[str] = "AUTO",
        bounces: int = 4,
        include_environment: bool = True,
        adaptive: bool = True,
        beside_textures: bool = False,
        include_scene_lights: bool = True,
        include_hidden_lights: bool = False,
        reflection_probe: bool = True,
    ):
        super().__init__()
        # Which of the scene's lights light a bake (see :meth:`_bake_lights`).
        # Every one, wherever it sits, by default. Off, only the lights in the
        # objects handed to the bake or under them -- the rest are hidden from
        # the render for the bake and handed back. The world answers to
        # ``include_environment`` alone. Mirror of mayatk's.
        self.include_scene_lights = bool(include_scene_lights)
        # Capture the room each bake lit as a reflection probe too (see
        # :meth:`bake_probe`): what a lightmap cannot hold -- the reflections
        # that are a baked metal's whole look, and the light on everything the
        # bake left out. Its size follows the tier (half the resolution,
        # within :attr:`PROBE_WIDTH_RANGE`). Mirror of mayatk's.
        self.reflection_probe = bool(reflection_probe)
        # Switch the hidden lights of that set ON for the bake, and back off
        # after -- one its collection hides too, without revealing what else
        # the collection holds (see :meth:`_switched_on`). Off by default.
        self.include_hidden_lights = bool(include_hidden_lights)
        # Save each finished map in the folder its material's texture maps live
        # in, named after that texture set, instead of all of them in one output
        # folder (see :meth:`_texture_homes`). A material whose images have no
        # file on this machine has no such folder, so its map falls back to the
        # bake's ``output_dir``. Mirror of mayatk's.
        self.beside_textures = bool(beside_textures)
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
        # ``denoise``/``device``/``adaptive`` are Cycles quality/throughput knobs owned by
        # the same primitive.
        # ``samples`` is Cycles PATHS, deliberately NOT mayatk's 5 (Arnold AA
        # samples) -- see TextureBaker.__init__ for why mirroring the API does
        # not mean mirroring the number across a unit change.
        self._texture_baker = TextureBaker(
            resolution, samples, denoise, device, bounces, adaptive
        )
        # Latch for the pre-bake unlit-scene guard (warn once per instance).
        self._warned_no_lights = False
        # The lights the bake in progress renders with (see :meth:`_lighting`);
        # ``None`` outside a bake. The last bake's stay readable for the light
        # audit a verdict on its maps carries.
        self._lit: Optional[_BakeLights] = None
        self._last_lit: Optional[_BakeLights] = None
        # The recorded lighting rule a re-bake lights by (see :meth:`_lit_as`);
        # ``None``: the switches above and the Scope a bake is handed.
        self._rule: Optional[Dict[str, Any]] = None

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
        """``"AUTO"`` (the default; the panel's), ``"GPU"``, ``"CPU"``, or ``None``
        for the scene's own -- mirror of mayatk's, whose scene-own default baked a
        CPU-saved production room at 48 minutes for 9 of 49 objects."""
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

    @property
    def adaptive(self) -> bool:
        """Whether the bake spends its samples adaptively: a texel stops once its
        noise is under the threshold, so the shadows take what the flat texels
        leave. Lives on the bake primitive (:attr:`TextureBaker.adaptive`, which
        records what it measured); mirror of mayatk's."""
        return self._texture_baker.adaptive

    @adaptive.setter
    def adaptive(self, value: bool) -> None:
        self._texture_baker.adaptive = bool(value)

    # ------------------------------------------------------------------
    # Quality-tier presets (pythontk PresetStore: built-in + user tiers)
    # ------------------------------------------------------------------

    #: What a preset may carry, by type: the quality dials, then the switches
    #: (mirror of mayatk's). The panel's preset template saves exactly these
    #: (plus ``packing``, the panel's choice between :meth:`bake_separated` and
    #: :meth:`bake_atlas`, which no constructor takes), so a preset saved in the
    #: panel builds the same baker through :meth:`from_preset`. The device is
    #: deliberately not one: it names one machine's hardware, and a preset
    #: travels. ``bounces`` is mayatk's ``gi_depth`` under Cycles' own name, and
    #: there is no ``gi_samples``: Cycles traces every path from ``samples``.
    PRESET_INT_KEYS: Tuple[str, ...] = ("resolution", "samples", "bounces")
    PRESET_BOOL_KEYS: Tuple[str, ...] = (
        "adaptive",
        "include_scene_lights",
        "include_environment",
        "include_hidden_lights",
        "denoise",
        "beside_textures",
        "reflection_probe",
    )
    #: Retired built-in tier names -> the current one, warning until they go
    #: (mirrors mayatk). ``"quest"`` (until 2026-09-23) named one headset for a
    #: tier that serves every mobile / standalone-VR target; a script, an older
    #: Maya bridge or the panel's preset pointer may still say it.
    #: :meth:`from_preset` resolves only a name the store lacks, so a user preset
    #: saved under a retired name still wins.
    _resolve_retired_preset = staticmethod(
        ptk.Deprecation.values(
            {"quest": "mobile"},
            what="LightmapBaker preset",
            remove_in="0.12.0",
            since="2026-09-23",
            reason="The tier was renamed; its settings are unchanged.",
        )
    )

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
        """Construct a baker from a named preset.

        A preset is a small JSON dict of :attr:`PRESET_INT_KEYS` (the quality dials
        every built-in stores) and :attr:`PRESET_BOOL_KEYS` (the switches a preset
        saved from the panel adds). ``overrides`` win over the preset (e.g.
        ``from_preset("mobile", resolution=1536)``); extra preset keys
        (``description``, the panel's ``packing``) are ignored.
        Built-ins (denoised): ``preview``, ``mobile``, ``desktop`` and ``hero``, whose
        dials are the ``presets/*.json`` beside this module -- read them there, not from
        a copy here. The tiers name an ATLAS size, and an atlas is shared by a whole
        material group -- a 40-piece room on one material gets 1/40th of it each, which
        is why an environment needs a tier above its per-object intuition.

        ``bounces`` rides the tier for the same reason mayatk's ``gi_depth`` does: in a
        closed room it is the biggest quality-per-second lever, and a preset that named
        only resolution and samples would leave the bake at whatever the scene last
        rendered with. Each tier bakes mayatk's depth for the tier of the same name:
        both renderers count bounces alike -- a calibration room on one lambert agreed
        with Arnold and the analytic value to 2% -- and the gap once measured on a
        production room was the scene (no white card then, :meth:`_white_card` now, and
        mis-crossed area lights), not the depth. The samples are Cycles paths, matched
        to the Arnold tier's shadow noise rather than copied from its AA count.
        ``mobile`` is the default tier on both the panel and the Maya bridge. A retired
        tier name (``quest``) still resolves, with a notice.
        """
        store = cls.preset_store()
        if not store.exists(name):
            name = cls._resolve_retired_preset(name)
        if not store.exists(name):
            raise ValueError(
                f"Unknown lightmap preset {name!r}. Available: {store.list()}"
            )
        data = {**store.load(name), **overrides}
        # Pass only the keys the preset provides; absent ones fall back to the
        # constructor's own defaults (no duplicated default literals to drift).
        kwargs: Dict[str, Any] = {
            k: int(data[k]) for k in cls.PRESET_INT_KEYS if k in data
        }
        kwargs.update({k: bool(data[k]) for k in cls.PRESET_BOOL_KEYS if k in data})
        # ...and the knob a preset never stores but an override legitimately
        # passes. Filtering to the preset keys alone silently dropped it, so
        # from_preset(name, device="CPU") built a GPU baker and nothing said so.
        if "device" in overrides:
            kwargs["device"] = overrides["device"]
        return cls(**kwargs)

    @classmethod
    def bake_targets(cls, objects=None) -> List[str]:
        """The names of the meshes a bake of *objects* acts on (default: the selection).

        :meth:`TextureBaker.resolve_meshes` -- the one definition of a bakeable
        mesh -- minus the file's :class:`LightmapExcludeSet`, a member counting
        everything under it. An excluded mesh is only left without a map of its
        own: it stays in the render, so it still casts shadows and bounces light
        onto the meshes that bake. Every bake entry point filters through here, so
        the panel, a headless bake and a preset run of the same file all skip the
        same objects. Mirror of mayatk's.

        Unlike mayatk's, a HIDDEN mesh is not left out: Arnold renders no hidden
        object, but :meth:`TextureBaker.bake` reveals each object for its own bake,
        so here a hidden mesh bakes like any other.
        """
        return cls._targets(objects)[0]

    @classmethod
    def _targets(cls, objects=None) -> Tuple[List[str], List[str]]:
        """``(targets, excluded)`` names for *objects*: the one scoping every
        bake entry runs -- :meth:`TextureBaker.resolve_meshes` (sets, groups,
        anything not a mesh), minus the Exclude set (mirror of mayatk's, which
        also partitions out the hidden)."""
        meshes = TextureBaker.resolve_meshes(objects, descendants=True)
        if not meshes:
            return [], []
        in_set = set(LightmapExcludeSet.meshes())
        excluded = [obj.name for obj in meshes if obj in in_set]
        if excluded:
            cls.logger.info(
                "Skipping %d object(s) in the lightmap exclusion set (%s); they "
                "still light the bake: %s",
                len(excluded),
                LightmapExcludeSet.SET_NAME,
                ", ".join(excluded[:8]) + (" ..." if len(excluded) > 8 else ""),
            )
        return [obj.name for obj in meshes if obj not in in_set], excluded

    # ------------------------------------------------------------------
    # The workflow -- what the panel runs, and what a script should
    # ------------------------------------------------------------------

    def bake(
        self,
        objects=None,
        packing: str = "atlas",
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        intensity: float = 1.0,
        **kwargs,
    ) -> LightmapBakeResult:
        """Bake *objects*' lightmaps and record them: the whole workflow, as the panel runs it.

        Mirror of mayatk's :meth:`LightmapBaker.bake`:

        1. The mesh objects in *objects* (default: the selection), minus the
           file's :class:`LightmapExcludeSet` (:meth:`bake_targets`).
        2. :meth:`preflight` -- a refusal when the scene has lights and none of
           them can light the bake.
        3. The bake: :meth:`bake_atlas` (``packing="atlas"``, one shared map
           per material) or :meth:`bake_separated` (``"per_object"``). An
           atlas bake of part of a material group writes those objects back
           into the cells they own in the atlas the rest of the group reads
           (:meth:`_in_place_plan`, ``in_place``), every other cell kept
           texel for texel; a target that no longer fits its cell, or is new
           to the group, re-bakes the whole group into a fresh layout
           instead (``repacked``, logged with why). Both
           migrate the targets' legacy markers first
           (:meth:`LightmapRecords.migrate_legacy`). The scene's lights are
           set for it as :meth:`_bake_lights` says -- every one, or the
           Scope's (:attr:`include_scene_lights`), the world by its own
           switch, the hidden ones switched on (:attr:`include_hidden_lights`);
           inside :meth:`_lit_as`, as a recorded rule says -- and handed back
           after.
        4. *intensity*, when not 1.0, scaled into the maps this bake just
           wrote -- once, so re-recording them can never apply it twice; a
           cell written in place is scaled as its tile, never the atlas
           around it.
        5. :meth:`LightmapRecords.commit` records each map with its rect, and
           in the bake baseline its cell, the lighting it rendered with and
           the rule that chose those lights (:meth:`_lighting_state`) -- what
           :meth:`rebake_changed` diffs against and re-lights by. The maps the
           baked objects read before -- when this file
           wrote them and nothing reads them now -- are set aside
           (:meth:`LightmapRecords.superseding`).
        6. :meth:`bake_verdict` reads the finished maps' level.
        7. :meth:`bake_probe` captures the room as a reflection probe
           (:attr:`reflection_probe`) -- not after a cancel, and a probe that
           fails is logged, never raised: the bake is committed by then.

        Nothing is reverted first. An object the bake does not finish keeps the
        map it had, and that map is intact: a bake never changes the texels
        another object reads. It replaces a file only the objects it rewrote
        read (:meth:`LightmapRecords.claims`), or writes the cells those
        objects own in an atlas they share -- and the only files it sets aside
        are ones no object reads any more.

        Parameters:
            objects: Mesh objects; ``None`` for the selection. Anything without
                a mesh of its own (an Empty, a light) bakes nothing; a light
                among them, or under them, is the Scope's own -- what lights
                the bake with :attr:`include_scene_lights` off.
            packing: ``"atlas"`` or ``"per_object"``.
            output_dir: Where the maps go (see :meth:`bake_separated`).
            prefix / suffix: Name affix around each map's texture-set stem.
            on_progress: ``(done, total, name) -> bool`` per object; return
                ``False`` to cancel the rest (and the probe).
            intensity: A multiplier baked into the texels; recorded in the
                markers, informationally.
            kwargs: Forwarded to the bake mechanism.

        Returns:
            :class:`LightmapBakeResult`: the maps and rects, the
            excluded and unbaked objects, the reflection probe, and the
            ``refused`` / ``verdict`` sentences for the artist.

        Raises:
            ValueError: *packing* is neither ``"atlas"`` nor ``"per_object"``.
        """
        if packing not in ("atlas", "per_object"):
            raise ValueError(
                f"packing must be 'atlas' or 'per_object', got {packing!r}"
            )
        result = LightmapBakeResult()
        # Read once: the Scope's lights are found in what the bake was handed,
        # and the bake itself selects along the way.
        if objects is None:
            from blendertk.core_utils._core_utils import CoreUtils

            objects = CoreUtils.selected_objects()
        # The Exclude set comes off BEFORE anything else touches the scene.
        targets, result.excluded = self._targets(objects)
        if not (targets or result.excluded):
            result.refused = "Nothing to bake: no mesh among the given objects."
            return result
        if not targets:
            count = len(result.excluded)
            result.refused = (
                "Nothing to bake: "
                + ("the object is" if count == 1 else f"all {count} objects are")
                + " in the Exclude set."
            )
            return result
        result.refused = self.preflight(objects)
        if result.refused:
            return result

        # A cancel ends the bake, not just its renders: the maps it finished
        # are committed, but no reflection probe follows -- one more render,
        # and one the artist cannot stop.
        cancelled = False

        def tick(done: int, total: int, name: str) -> bool:
            nonlocal cancelled
            keep = on_progress(done, total, name)
            cancelled = cancelled or keep is False
            return keep

        common = dict(
            output_dir=output_dir,
            prefix=prefix,
            suffix=suffix,
            on_progress=tick if on_progress is not None else None,
            **kwargs,
        )
        # The lighting the maps are about to see, as the baseline records it
        # (:meth:`_lighting_state`): read as authored, before the bake
        # switches anything.
        try:
            lighting = self._lighting_state(objects)
        except Exception as e:  # the baseline is never what loses a bake
            self.logger.warning("Bake lighting not recorded: %s", e)
            lighting = None
        # Lit for what the bake was HANDED: the mechanisms below see meshes
        # alone, and keep this (re-entrant) rather than re-reading the Scope.
        with self._lighting(objects):
            if packing == "atlas":
                atlas = self._bake_atlas(targets, intensity=intensity, **common)
                packed, cells = atlas.packed, atlas.cells
                result.in_place, result.repacked = atlas.in_place, atlas.repacked
            else:
                packed = {
                    n: (path, None)
                    for n, path in self.bake_separated(targets, **common).items()
                }
                cells = {
                    n: {"packing": "per_object", "size": int(self.resolution)}
                    for n in packed
                }
        result.maps = {n: path for n, (path, _rect) in packed.items()}
        result.rects = {
            n: [float(v) for v in (rect or self._IDENTITY_SCALE_OFFSET)]
            for n, (_path, rect) in packed.items()
        }
        result.unbaked = [n for n in targets if n not in result.maps]
        if result.unbaked:
            self.logger.warning(
                "%d object(s) were not baked (cancelled, or failed); they keep "
                "the lightmap they had: %s",
                len(result.unbaked),
                ", ".join(result.unbaked[:8])
                + (" ..." if len(result.unbaked) > 8 else ""),
            )
        if not result.maps:
            return result
        # An atlas bake scaled its own maps -- a cell written in place, into
        # the tile alone (:meth:`_bake_atlas`).
        if float(intensity) != 1.0 and packing != "atlas":
            self._apply_intensity(result.maps.values(), intensity)
        with LightmapRecords.superseding(list(result.maps)) as retired:
            LightmapRecords.commit(
                result.maps,
                scale_offsets=result.rects,
                intensity=intensity,
                cells={n: cells[n] for n in result.maps if n in cells},
                lighting=lighting,
            )
        result.retired = retired
        result.verdict = self.bake_verdict(result.maps.values())
        if self.reflection_probe and not cancelled:
            # After the commit, so it must never be what loses the bake: a
            # render that fails, or a probe file held open, costs the probe.
            # Lit as the maps were: a probe of other lights is another room.
            try:
                with self._lighting(objects):
                    result.probe = self.bake_probe(result.maps)
            except Exception as e:
                self.logger.warning("Reflection probe not captured: %s", e)
        return result

    # ------------------------------------------------------------------
    # Re-bake Changed -- what changed since the last bake, and only that
    # ------------------------------------------------------------------

    def stale_report(self, objects=None, reach: Optional[float] = None):
        """What a re-bake of the changed objects would redo, and why -- the dry run.

        Mirror of mayatk's. The file (:meth:`LightmapRecords.scene_state`)
        read against what each map was baked against
        (:meth:`LightmapRecords.bake_state`) by ``ptk.BakeBaseline.assess``,
        the stale rule both DCCs share. Stale: an object whose own state
        changed (moved, deformed, remodelled, its lightmap UVs re-laid, its
        material regrouped, shown to or hidden from the render), whose map is
        missing, or whose lighting changed (:meth:`_lighting_state`: the
        lights its bake rendered with, the world when it was included, and
        every setting that changes the light) -- and every static object a
        change reaches, a neighbour. Each map's lighting is read again by the
        rule it was baked by (:meth:`_lit_as`: its switches and, Scope-lit,
        its lights by name) whatever this baker's switches say, so a light
        renamed, deleted, changed, or added where the rule reaches is a
        lighting change for what it lit. The report's ``in_place`` /
        ``repack`` say which targets would be written back into their atlas
        cells and which atlases re-pack whole (:meth:`_in_place_plan`).
        Nothing is rendered or written.

        Not seen: an emissive material's strength and an albedo edit change
        the bounce, not any state the baseline records. An object hidden from
        the render reads as one that cannot bake (the shared rule), though a
        Selected bake still bakes it (:meth:`bake_targets`).

        Parameters:
            objects: Report only these objects (names or datablocks) and what
                lies under them; ``None`` reports the file. Causes come from
                the whole file either way. A lighting recorded before rules
                were is read by this baker's switches over them (every baked
                object for ``None``), and reads as changed once.
            reach: A multiplier on how far a change reaches its neighbours
                (``ptk.BakeBaseline.STALE_REACH``); 0 names none. ``None``: 1.

        Returns:
            ``ptk.StaleReport`` -- ``summary()`` for a footer, ``lines()`` for
            a log.
        """
        return self._stale_report(objects, reach)

    def _stale_report(
        self,
        objects=None,
        reach: Optional[float] = None,
        lighting: bool = True,
        intensity: Optional[float] = None,
    ):
        """:meth:`stale_report`; *lighting* ``False`` leaves the lights out,
        *intensity* is the one the re-bake applies (``None``: not asked)."""
        ids = None
        if objects is not None:
            ids = [
                obj.name
                for obj in TextureBaker.resolve_meshes(list(objects), descendants=True)
            ]
        scope = (
            list(objects) if objects is not None else LightmapRecords.baked_objects()
        )

        def lit(rule):
            # The lighting now, read by a recorded rule as a re-bake lights by
            # it; ``None`` (recorded without one): this baker's over *scope*.
            with self._lit_as(rule):
                return self._lighting_state(scope)

        report = ptk.BakeBaseline.assess(
            LightmapRecords.bake_state(),
            LightmapRecords.scene_state(),
            lighting=lit if lighting else None,
            missing=LightmapRecords._missing_maps(),
            reach=1.0 if reach is None else float(reach),
            objects=ids,
        )
        if not report.targets:
            return report
        placing = self._in_place_plan(list(report.targets), intensity=intensity)
        return replace(
            report,
            in_place=tuple(sorted(placing.placed)),
            repack=dict(placing.repack),
        )

    def rebake_changed(
        self,
        packing: Optional[str] = None,
        reach: Optional[float] = None,
        lighting: bool = True,
        **bake_kwargs,
    ) -> LightmapBakeResult:
        """Re-bake what changed since the last bake, and what that change shades.

        Mirror of mayatk's. :meth:`stale_report`'s targets through
        :meth:`bake`, so every check, the record and the verdict are the
        bake's own. Each target re-bakes the way it was RECORDED as baked:
        packed so (*packing* forces one way for all) and lit so -- by the rule
        its lighting recorded (:meth:`_lit_as`), whatever this baker's
        lighting switches say, so the next dry run reads what it rendered. An
        atlas target is written back into the cell it owns where it still
        fits (:meth:`_in_place_plan`), so each material keeps ONE atlas and
        every other cell keeps its texels; each way of baking is one
        :meth:`bake` call, the reflection probe captured after the last. A
        lighting change stales everything lit by it, said before anything
        renders.

        Parameters:
            packing: ``"atlas"`` or ``"per_object"`` for every target;
                ``None`` re-bakes each the way it was baked (an atlas when no
                record says).
            reach: As :meth:`stale_report`.
            lighting: Whether a lighting change counts. ``False`` re-bakes
                only what changed in the file itself.
            bake_kwargs: Forwarded to :meth:`bake` (``output_dir``,
                ``prefix``, ``suffix``, ``on_progress``, ``intensity``, ...).

        Returns:
            :class:`LightmapBakeResult` -- its ``report`` the dry run it acted
            on, ``in_place`` / ``repack`` as done. With nothing changed: no
            maps, and ``refused`` says so.

        Raises:
            ValueError: *packing* is not ``None``, ``"atlas"`` or
                ``"per_object"``.
        """
        if packing not in (None, "atlas", "per_object"):
            raise ValueError(
                f"packing must be None, 'atlas' or 'per_object', got {packing!r}"
            )
        report = self._stale_report(
            reach=reach,
            lighting=lighting,
            intensity=float(bake_kwargs.get("intensity", 1.0)),
        )
        result = LightmapBakeResult(report=report)
        if not report.targets:
            result.refused = report.summary()
            return result
        self.logger.info("Re-bake Changed: %s", report.summary())
        for line in report.lines():
            self.logger.info("  %s", line)
        recorded = LightmapRecords.bake_state()
        rules = ptk.BakeBaseline.lighting_rules(recorded)
        # One bake per way of baking -- packed (atlases first) and lit (the
        # recorded rule; ``None``: none recorded) as each target was.
        passes: List[Tuple[str, Optional[Dict[str, Any]], List[str]]] = []
        for target in report.targets:
            chosen = packing or (recorded["objects"].get(target) or {}).get("packing")
            chosen = chosen if chosen in ("atlas", "per_object") else "atlas"
            rule = rules.get(target)
            same = next((p for p in passes if p[:2] == (chosen, rule)), None)
            if same is None:
                passes.append((chosen, rule, [target]))
            else:
                same[2].append(target)
        passes.sort(key=lambda p: p[0] != "atlas")

        on_progress = bake_kwargs.pop("on_progress", None)
        cancelled = False

        def tick(done: int, total: int, name: str) -> bool:
            nonlocal cancelled
            keep = on_progress(done, total, name)
            cancelled = cancelled or keep is False
            return keep

        probe = self.reflection_probe
        try:
            for i, (chosen, rule, targets) in enumerate(passes):
                # One probe, of the room as the LAST bake leaves it.
                self.reflection_probe = probe and i == len(passes) - 1
                with self._lit_as(rule):
                    part = self.bake(
                        targets,
                        packing=chosen,
                        on_progress=tick if on_progress is not None else None,
                        **bake_kwargs,
                    )
                self._merge_result(result, part)
                if cancelled or (part.refused and not part.maps):
                    break
        finally:
            self.reflection_probe = probe
        if result.maps:
            result.refused = None
        result.report = replace(
            report, in_place=tuple(result.in_place), repack=dict(result.repacked)
        )
        return result

    @staticmethod
    def _merge_result(into: LightmapBakeResult, part: LightmapBakeResult) -> None:
        """Fold one of :meth:`rebake_changed`'s bakes into its result."""
        into.maps.update(part.maps)
        into.rects.update(part.rects)
        for name in ("excluded", "hidden", "unbaked", "retired", "in_place"):
            getattr(into, name).extend(getattr(part, name))
        into.repacked.update(part.repacked)
        into.refused = into.refused or part.refused
        into.verdict = part.verdict or into.verdict
        into.probe = part.probe or into.probe

    def _lighting_state(self, scope=None) -> Dict[str, Any]:
        """The lighting a bake of *scope* renders with, as the bake baseline records it.

        Mirror of mayatk's: ``ptk.BakeBaseline.lighting_state`` over the
        lights :meth:`_bake_lights` resolves -- the one answer the bake, its
        probe and its refusal read, so the baseline fingerprints exactly the
        lights the maps saw -- each by its object (:meth:`_light_state`):
        world matrix, the light's RNA values and node tree, the files feeding
        it, and whether it lights the bake (rendered as authored, or switched
        on by Include Hidden Lights). The world rides apart while
        :attr:`include_environment` takes it in (:meth:`_world_state`); beside
        them the setting that changes the light, :attr:`bounces`, and the rule
        that chose the lights (:meth:`_light_rule`). Read as authored -- call
        it outside :meth:`_lighting`, which switches lights for the bake.
        """
        import bpy

        plan = self._bake_lights(scope)
        settings = {"bounces": int(self.bounces)}
        scene = bpy.context.scene
        if scene is None:
            return ptk.BakeBaseline.lighting_state(
                {}, None, settings, rule=self._light_rule([])
            )
        rendered = self._rendered_collections()
        lights: Dict[str, Any] = {}
        for name in plan.lights:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            lit = self._renders(obj, rendered) or (
                name in plan.hidden and obj.library is None
            )
            key, state = self._light_state(obj, lit)
            lights[key] = state
        return ptk.BakeBaseline.lighting_state(
            lights,
            self._world_state(scene.world) if self.include_environment else None,
            settings,
            rule=self._light_rule(lights),
        )

    #: The lighting switches a recorded rule carries, by attribute name.
    _RULE_SWITCHES: Tuple[str, ...] = (
        "include_scene_lights",
        "include_environment",
        "include_hidden_lights",
    )

    def _light_rule(self, ids) -> Dict[str, Any]:
        """The rule a bake lights by, as the baseline records it (``@rule``).

        The lighting switches, and with Include Scene Lighting off the
        Scope's lights by id (*ids*: those the bake resolved, the environment
        not among them) -- what :meth:`_lit_as` re-applies, so a re-bake and
        a dry run read the lights this bake did. Inside :meth:`_lit_as`, the
        rule in force, as recorded. Mirror of mayatk's: one shape in both.
        """
        held = getattr(self, "_rule", None)
        if held is not None:
            return dict(held)
        rule: Dict[str, Any] = {k: bool(getattr(self, k)) for k in self._RULE_SWITCHES}
        rule["scope"] = None if self.include_scene_lights else sorted(ids)
        return rule

    @contextlib.contextmanager
    def _lit_as(self, rule: Optional[Dict[str, Any]]):
        """Light the bakes in this block by a recorded *rule* -- handed back after.

        Its switches (:attr:`_RULE_SWITCHES`), whatever this baker's say, and
        with Include Scene Lighting off its Scope's lights by id
        (:meth:`_bake_lights`), whatever a bake is handed: how a re-bake
        lights each object the way it was baked, and a dry run reads the
        lights that bake used. ``None`` (no rule recorded): this baker's own.
        Mirror of mayatk's.
        """
        if not rule:
            yield
            return
        was = {k: getattr(self, k) for k in self._RULE_SWITCHES}
        held = getattr(self, "_rule", None)
        try:
            for k in self._RULE_SWITCHES:
                if k in rule:
                    setattr(self, k, bool(rule[k]))
            self._rule = dict(rule)
            yield
        finally:
            for k, value in was.items():
                setattr(self, k, value)
            self._rule = held

    #: A light's RNA values that are bookkeeping, not light (:meth:`_rna_values`).
    _NOT_LIGHT: Tuple[str, ...] = ("name", "use_fake_user", "use_extra_user")
    #: The ray visibility a light object carries in Cycles: what it lights.
    _RAY_VISIBILITY: Tuple[str, ...] = (
        "visible_camera",
        "visible_diffuse",
        "visible_glossy",
        "visible_transmission",
        "visible_volume_scatter",
        "visible_shadow",
    )

    @classmethod
    def _light_state(cls, obj, lit: bool) -> Tuple[str, Dict[str, Any]]:
        """``(name, state)`` of one light object for :meth:`_lighting_state`.

        The state: the object's world matrix (column by column, the baseline's
        order), every value of its light (``energy``, ``color``, the shape and
        cone, the Cycles light settings) and its ray visibility, a digest of
        the light's node tree, the files upstream of it (an IES profile, an
        image), and *lit*. Mirror of mayatk's.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        light = obj.data
        values = cls._rna_values(light, skip=cls._NOT_LIGHT)
        cycles = getattr(light, "cycles", None)
        if cycles is not None:
            values.update(
                {f"cycles.{k}": v for k, v in cls._rna_values(cycles).items()}
            )
        values.update(
            {k: bool(getattr(obj, k)) for k in cls._RAY_VISIBILITY if hasattr(obj, k)}
        )
        tree = MatUtils.get_node_tree(light)
        if tree is not None:
            values["@nodes"] = cls._node_tree_digest(tree)
        m = obj.matrix_world
        return obj.name, {
            "matrix": [float(m[r][c]) for c in range(4) for r in range(4)],
            "attrs": values,
            "files": cls._tree_files(tree),
            "on": bool(lit),
        }

    @classmethod
    def _world_state(cls, world) -> Dict[str, Any]:
        """The world as :meth:`_lighting_state` records it: a digest of its node
        tree (an HDRI's file, its rotation, its strength), its colour, and the
        files feeding it; ``{}`` for none."""
        from blendertk.mat_utils._mat_utils import MatUtils

        if world is None:
            return {}
        tree = MatUtils.get_node_tree(world)
        return {
            "tree": cls._node_tree_digest(tree) if tree is not None else None,
            "color": [float(c) for c in getattr(world, "color", ())],
            "files": cls._tree_files(tree),
        }

    @staticmethod
    def _rna_values(struct, skip=()) -> Dict[str, Any]:
        """*struct*'s editable bool / int / float / enum values, by identifier.

        Arrays (a colour) as lists, an enum flag as its sorted members --
        JSON-plain, so the baseline can digest and compare them. Read-only
        values (users, flags Blender keeps for itself) are not authored and
        are left out, as are pointers, collections and a deprecated value
        (reading one warns; the node-tree flag of 5.x is pinned anyway).
        """
        values: Dict[str, Any] = {}
        for prop in struct.bl_rna.properties:
            key = prop.identifier
            if (
                key == "rna_type"
                or key in skip
                or prop.is_readonly
                or getattr(prop, "is_deprecated", False)
                or prop.type not in ("BOOLEAN", "INT", "FLOAT", "ENUM")
            ):
                continue
            try:
                value = getattr(struct, key)
            except Exception:  # noqa: BLE001 -- an unreadable value is no state
                continue
            if isinstance(value, (set, frozenset)):
                value = sorted(value)
            elif hasattr(value, "__len__") and not isinstance(value, str):
                value = [float(v) for v in value]
            values[key] = value
        return values

    @classmethod
    def _node_tree_digest(cls, tree) -> Optional[str]:
        """blake2b-16 of a node tree as it renders: every node's own values and
        unlinked inputs, its image, and the links -- never where it sits in the
        editor. ``None`` for no tree."""
        import bpy

        if tree is None:
            return None
        base = {p.identifier for p in bpy.types.Node.bl_rna.properties} - {"mute"}
        nodes = []
        for node in tree.nodes:
            inputs = []
            for socket in node.inputs:
                value = getattr(socket, "default_value", None)
                if value is None:
                    continue
                if hasattr(value, "__len__") and not isinstance(value, str):
                    value = [float(v) for v in value]
                inputs.append((socket.identifier, value))
            image = getattr(node, "image", None)
            nodes.append(
                (
                    node.bl_idname,
                    node.name,
                    cls._rna_values(node, skip=base),
                    inputs,
                    getattr(image, "filepath", None),
                )
            )
        links = sorted(
            (lk.from_node.name, lk.from_socket.identifier)
            + (lk.to_node.name, lk.to_socket.identifier)
            for lk in tree.links
        )
        text = json.dumps(
            [sorted(nodes, key=lambda n: n[1]), links], sort_keys=True, default=str
        )
        return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()

    @staticmethod
    def _tree_files(tree) -> List[str]:
        """The files a node tree reads (images, an IES profile), sorted."""
        if tree is None:
            return []
        files = set()
        for node in tree.nodes:
            image = getattr(node, "image", None)
            for path in (
                getattr(image, "filepath", None),
                getattr(node, "filepath", None),
            ):
                if isinstance(path, str) and path:
                    files.add(path)
        return sorted(files)

    # ------------------------------------------------------------------
    # The reflection probe -- the room the bake lit, as an HDR
    # ------------------------------------------------------------------

    #: The probe's width bounds in texels (its height is half): half the bake's
    #: resolution, so it follows the tier -- 512 for mobile, 1024 for desktop.
    PROBE_WIDTH_RANGE: Tuple[int, int] = (256, 1024)

    def bake_probe(self, maps: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Capture the room the file's bake lit as a reflection probe, and record it.

        Mirror of mayatk's. What a lightmap cannot hold: a lightmap is diffuse
        irradiance with no direction, so a baked METAL -- which has no diffuse
        -- shows only what it reflects, and an object the bake left out (one
        that moves) is lit by nothing of the room at all. Measured on the Maya
        side against Arnold's render of a production soldering table: under
        the WebXR preview's studio its metal housing at 0.12 of Arnold's
        luminance; reflecting this probe, 0.62.

        An equirectangular HDR Cycles renders from one point with this baker's
        own settings (:meth:`TextureBaker.render_panorama`), placed by
        :class:`ProbePlacement` (``ptk.ProbePlacement``, shared with mayatk) so
        it holds in any scene the baker meets: in open air over what moves,
        else the room's middle, at eye level, clear of every surface; the box
        its reflections project onto measured face by face against what the
        render sees, an open face read as distant.

        **What it sees.** The room is what is baked and the file's
        :class:`LightmapExcludeSet`, as the render shows them (a mesh hidden
        from the render is no wall). The other meshes the render shows move,
        and are out of its render as they are out of the bake's -- a probe
        that carried them, or their shadows, would show them where they no
        longer are -- save the unbaked ones that surround the bake (a sky
        dome, a ground nobody baked): the room's surroundings, in it. Every
        light is visible to it, the world too; a world or a light the bake
        left out (:meth:`_lighting`) is left out of it as well.

        **Its axes.** Blender is Z-up and the probe's readers (the WebXR
        preview's equirect, Unity) read glTF's Y-up axes, so the render is
        laid out for them (:attr:`TextureBaker.PANORAMA_TURN`) and the
        capture point and box are recorded in them, ``(x, z, -y)``
        (:meth:`_gltf_axes`) -- the axes both exporters write. mayatk's
        refusal of a Z-up scene has no twin here: the conversion is always
        the same one.

        Written beside the bake's maps as ``<file>_Probe.exr`` and recorded
        (:meth:`LightmapRecords.commit_probe`); the lightmap manifest carries
        it into a GLB (``ptk.MeshConvert.apply_glb_lightmaps``, and the native
        exporter's :meth:`LightmapWebExport.embed_probe`) and Unity.

        Parameters:
            maps: ``{object: map path}`` this bake wrote; the probe lands in the
                folder most of them did, and their objects are the room
                whether or not they are recorded yet. Default: the first
                folder the file's recorded maps are found in.

        Returns:
            The probe's path, or ``None`` when nothing baked shows in the
            render, no folder holds the maps, or Cycles wrote nothing (logged).
        """
        import bpy

        scene = bpy.context.scene
        rendered = self._rendered_collections()

        def shows(obj) -> bool:
            return (
                obj is not None and obj.type == "MESH" and self._renders(obj, rendered)
            )

        # What the render sees: a baked object hidden from it is no wall, and
        # an isolated one moves (LightmapIsolateSet) -- out of the probe as it
        # is out of every other object's bake.
        isolated = {obj.name for obj in LightmapIsolateSet.meshes()}
        # What is recorded, and what *maps* names: a caller of the bake
        # mechanisms that records its maps itself (the Maya bridge's bake) has
        # baked them all the same.
        baked = [
            name
            for name in dict.fromkeys([*LightmapRecords.baked_objects(), *(maps or {})])
            if name not in isolated and shows(bpy.data.objects.get(name))
        ]
        if not baked:
            return None
        # The Exclude set is the room too: static, in the bake's render.
        baked += [
            obj.name
            for obj in LightmapExcludeSet.meshes()
            if obj.name not in baked and obj.name not in isolated and shows(obj)
        ]
        room = set(baked)
        # Every other mesh the render shows is unbaked: what moves, or what
        # surrounds the bake (ProbePlacement tells them apart). A linked
        # duplicate of a baked mesh is an object of its own here -- the render
        # hides objects, not meshes -- so it moves with the rest, as the bake's
        # own render (:meth:`_out_of_render`) treats it.
        unbaked = [
            obj.name for obj in scene.objects if obj.name not in room and shows(obj)
        ]
        # Lit as the maps were (:meth:`_lighting` -- inside :meth:`bake`, by
        # what that bake was handed; called alone, by what is baked): a world
        # the bake left out stays out of its probe, detached for the placement
        # (a detached world is no sky to see) and the render, and so do the
        # lights outside its Scope.
        with self._lighting(baked):
            site = ProbePlacement(baked, unbaked).place()
            if site is None:
                return None
            folder = self._probe_folder(maps)
            if not folder:
                self.logger.warning(
                    "No reflection probe: no folder holds this bake's maps."
                )
                return None
            stem = os.path.splitext(os.path.basename(bpy.data.filepath or ""))[0]
            path = os.path.join(folder, f"{stem or 'untitled'}_Probe.exr")
            low, high = self.PROBE_WIDTH_RANGE
            width = max(low, min(high, int(self.resolution) // 2))
            # Out of the render, not only see-through: what moves shades
            # neither the maps nor the room they reflect. ``hide`` stays for
            # one whose render switch could not be set: see-through at least.
            with self._switched_off(site.hide, "unbaked mesh"):
                written = self._texture_baker.render_panorama(
                    site.position, path, width=width, hide=site.hide
                )
        if not written:
            return None
        position = self._gltf_axes(site.position)
        box = None
        if site.box is not None:
            # -y swaps a box's ends on that axis: each corner converted, then
            # the box's least and most again.
            corners = [self._gltf_axes(corner) for corner in site.box]
            box = [
                [min(a, b) for a, b in zip(*corners)],
                [max(a, b) for a, b in zip(*corners)],
            ]
        LightmapRecords.commit_probe(written, position, box)
        if site.box is None:
            reflects = "an open scene, read as distant"
        elif site.open_faces:
            reflects = (
                f"projected onto the room, {site.open_faces} open "
                f"face{'s' if site.open_faces > 1 else ''} read as distant"
            )
        else:
            reflects = "projected onto the room"
        self.logger.info(
            "Reflection probe: %s (%d px, %s at %s, %s).",
            os.path.basename(written),
            width,
            site.reason,
            [round(c, 2) for c in site.position],
            reflects,
        )
        for note in site.notes:
            self.logger.info("Reflection probe: %s.", note)
        return written

    @staticmethod
    def _gltf_axes(point) -> List[float]:
        """A Blender (Z-up) point in glTF's Y-up axes, ``(x, z, -y)`` -- the
        conversion Blender's glTF and FBX exporters both make."""
        x, y, z = (float(c) for c in point)
        return [x, z, 0.0 - y]  # never -0.0 for a y of 0

    @staticmethod
    def _probe_folder(maps: Optional[Dict[str, str]]) -> Optional[str]:
        """The folder most of *maps* landed in, else the file's first lightmap folder."""
        from collections import Counter

        folders = Counter(os.path.dirname(path) for path in (maps or {}).values())
        if folders:
            return folders.most_common(1)[0][0]
        found = LightmapRecords.search_dirs()
        return found[0] if found else None

    def preflight(self, objects=None) -> Optional[str]:
        """Why this file cannot bake *objects* now, or ``None`` (mirror of mayatk's).

        Cycles ships with Blender, so there is no renderer to load. What is
        left is mayatk's lights rule: a scene whose lights all exist but none
        can light the bake -- every one hidden from the render or at zero
        power, and no emitting world the bake keeps (Include Environment) --
        is refused rather than baked at full cost to come back dark (measured
        on the Maya side: four hidden area lights baked a room 147x dimmer than
        its previous bake). The world counts because mayatk's skydome does: it
        is a light there. "No lights at all" is NOT refused: a world or an
        emissive material lights a Cycles bake with no light object in it;
        :meth:`_warn_if_unlit_scene` warns and :meth:`bake_verdict` judges
        the result.

        A light is hidden from the render by its COLLECTIONS too -- a
        collection's render toggle, or one excluded from the view layer, is
        the usual Blender idiom for switching a group of lights off -- and
        Cycles bakes through a render depsgraph where those lights are gone
        (mayatk's rule reads inherited visibility the same way).

        It reads the lights the bake renders with (:meth:`_bake_lights`), not
        the file's: with :attr:`include_scene_lights` off a light outside the
        Scope is hidden for the bake, so it cannot stand in for the Scope's;
        with :attr:`include_hidden_lights` on a hidden light the bake will
        switch on is a light.

        Parameters:
            objects: What the bake is handed (``None``: the selection) -- its
                lights are the Scope's own (:meth:`_scope_lights`), all that
                counts with :attr:`include_scene_lights` off.
        """
        try:
            import bpy

            scene = bpy.context.scene
            plan = self._bake_lights(objects)
            world_lights = self.include_environment and LightUtils.world_emits(
                getattr(scene, "world", None)
            )
            # the layer the bake renders: the window's (windowless, the context's is
            # the scene's default layer -- measured, a light the window's layer
            # excludes passed the check there)
            rendered = self._rendered_collections()
            lit = [
                name
                for name in plan.lights
                if (
                    self._renders(bpy.data.objects[name], rendered)
                    and self._power(bpy.data.objects[name]) > 0.0
                )
                or (name in plan.hidden and bpy.data.objects[name].library is None)
            ]
        except Exception:  # no runtime / unreadable scene -- nothing to refuse
            return None
        if plan.lights and not lit and not world_lights:
            where = "in the scene" if self.include_scene_lights else "in the Scope"
            self.logger.warning(
                "Bake refused: all %d light(s) %s are hidden from the render or "
                "at zero power, so Cycles would bake no direct light and the maps "
                "would come back essentially black. Include Hidden Lights "
                "switches them on for the bake.\n"
                "Lights the bake renders with:\n%s",
                len(plan.lights),
                where,
                self._light_audit(plan),
            )
            return (
                f"Bake skipped: all {len(plan.lights)} light(s) {where} are hidden "
                "from the render or at zero power (see the console)."
            )
        return None

    @staticmethod
    def _rendered_collections() -> set:
        """:meth:`_render_collections` of the layer a bake renders: the window's
        (windowless, the scene's default layer)."""
        from blendertk.core_utils._core_utils import CoreUtils

        return LightmapBaker._render_collections(CoreUtils._active_view_layer())

    @staticmethod
    def _renders(obj, rendered: set) -> bool:
        """Whether *obj* reaches a render: its own flag, and a collection of
        *rendered* (:meth:`_render_collections`) holding it."""
        return not obj.hide_render and any(
            c.name in rendered for c in obj.users_collection
        )

    @staticmethod
    def _power(obj) -> float:
        """A light object's power (``energy``; 0.0 when unreadable)."""
        return float(getattr(obj.data, "energy", 0.0) or 0.0)

    @staticmethod
    def _render_collections(view_layer) -> set:
        """Names of the collections whose objects reach a render of *view_layer*.

        A collection renders when it and every collection above it are enabled
        for render and none is excluded from the layer; the scene collection
        (an object linked to the scene directly) always does.
        """
        names: set = set()

        def walk(layer_collection, enabled: bool) -> None:
            enabled = (
                enabled
                and not layer_collection.exclude
                and not layer_collection.collection.hide_render
            )
            if enabled:
                names.add(layer_collection.collection.name)
            for child in layer_collection.children:
                walk(child, enabled)

        walk(view_layer.layer_collection, True)
        return names

    def bake_verdict(self, paths) -> Optional[str]:
        """A warning about a finished bake's level, or ``None`` when it is plausible.

        Both directions, because a lightmap has no correct ABSOLUTE level and
        each failure is a *successful* render of a wrong scene: nothing
        upstream errors, and the artist otherwise finds out in the web preview,
        where it reads as a pipeline bug. The black half caught an unlit room;
        the blown half was missing until a Maya-bridge send crossed at 5.4e8 W
        per fixture, saturated every atlas at the half-float ceiling and
        reported success (mayatk CHANGELOG 2026-08-29). Measured by
        :meth:`peak_level`; logged with a light audit, so a bad result carries
        its own diagnosis. Mirror of mayatk's, which calls its lower line UNLIT
        (it sits higher there, where dim-but-lit maps are the failure).
        """
        try:
            peak = self.peak_level(paths)
        except Exception:  # the check must never break a finished bake
            return None
        if peak is None:
            return None
        _path, mean, saturated = peak
        if mean < self.BLACK_BAKE_MEAN:
            self.logger.warning(
                "Bake is essentially BLACK (brightest map mean %.4f). The bake "
                "renders the scene's own lights: check light power (W), that the "
                "lights are visible to the RENDER (not just the viewport), and "
                "that the world background is not black -- an emissive material "
                "lights a Cycles bake only while its object is render-visible.\n"
                "Scene lights at bake time:\n%s",
                mean,
                self._light_audit(getattr(self, "_last_lit", None)),
            )
            return "bake is essentially BLACK — check light power (see the console)."
        if mean >= self.BLOWN_BAKE_MEAN:
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
                self._light_audit(getattr(self, "_last_lit", None)),
            )
            return "bake is BLOWN OUT — check light power (see the console)."
        return None

    @staticmethod
    def _light_audit(plan: Optional[_BakeLights] = None) -> str:
        """One line per light the bake renders with: the attrs that decide whether it is lit.

        Attached to the level verdict and the lights-off refusal so a dark
        result carries its own diagnosis -- power, scale, render visibility and
        the world strength are exactly the dials a black bake traces back to,
        and none of them are visible in the bake output itself. Twin of
        mayatk's ``_light_audit`` (Arnold's intensity/exposure/normalize ->
        Cycles' watts).

        The lights of *plan* (:meth:`_bake_lights`) -- the set the refusal
        counts and the render sees -- with the ones it leaves out named on a
        line of their own; every scene light without one.

        Total-failure tolerant: it is evaluated as an argument to a warning,
        so a raise here would propagate out of a finished bake -- the one
        thing the checks promise never to do.
        """
        try:
            import bpy

            scene = bpy.context.scene
            if scene is None:
                return "  <no scene>"
            if plan is None:
                rows = LightmapBaker._light_rows(scene)
            else:
                rows = LightmapBaker._light_rows(
                    scene, names=plan.lights, hidden=plan.hidden
                )
                if not plan.lights and plan.outside:
                    rows = ["  <no lights in the Scope>"]
                if plan.outside:
                    rows.append(
                        f"  ({len(plan.outside)} light(s) outside the Scope, "
                        "hidden from the render for the bake -- Include Scene "
                        "Lighting is off: "
                        + ", ".join(plan.outside[:8])
                        + (" ..." if len(plan.outside) > 8 else "")
                        + ")"
                    )
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
    def _light_rows(scene, names=None, hidden=()) -> List[str]:
        """One ``  <name>: k=v ...`` row per light in *scene* (the audit's per-light half).

        Each light is read under its own guard, so a single unreadable one costs its row
        rather than the whole table. *names* keeps the rows to those lights (``None``:
        every one); a light in *hidden* is marked as switched on for the bake.
        """
        from blendertk.core_utils._core_utils import CoreUtils

        vl = CoreUtils._active_view_layer()  # the window's (windowless: the default)
        keep = None if names is None else set(names)
        rows: List[str] = []
        for obj in scene.objects:
            if obj.type != "LIGHT" or (keep is not None and obj.name not in keep):
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
                    f"viewport_visible={obj.visible_get(view_layer=vl)}",
                ]
                if data.type == "AREA":
                    bits.append(f"size={data.size:g}")
                if obj.name in hidden:
                    bits.append("switched on for the bake (Include Hidden Lights)")
                rows.append(f"  {obj.name}: " + "  ".join(bits))
            except Exception:
                rows.append(f"  {getattr(obj, 'name', '?')}: <unreadable>")
        return rows or ["  <no lights in the scene>"]

    # ------------------------------------------------------------------
    # Bake mechanisms
    # ------------------------------------------------------------------

    def bake_separated(
        self, objects=None, prefix: str = "lightmap_irr_", **kwargs
    ) -> Dict[str, str]:
        """Bake a **lighting-only** irradiance lightmap per object -- THE bake.

        Cycles ``type='DIFFUSE'`` with ``pass_filter={'DIRECT','INDIRECT'}`` (no ``'COLOR'``),
        each target rendered through mayatk's white card (:meth:`_white_card`), so albedo
        stays on its own UV/texture and the lightmap holds the geometry's lighting only, to
        be combined ``albedo x lightmap`` by the engine. The card is object-linked for the
        target's own bake (``TextureBaker.bake(shader=)``): the neighbours bounce light off
        their real materials, and every slot comes back after.
        Pairs with :meth:`commit_lightmap`. Returns ``{object_name: exr_path}``.

        The objects go through :meth:`bake_targets`, so a member of the file's
        :class:`LightmapExcludeSet` gets no map (it still lights the rest). No map
        takes a file name another object reads (:meth:`LightmapRecords.claims`);
        an object's own map keeps its name. With :attr:`beside_textures` the maps
        are baked into a swept work dir and each is then placed in its texture
        set's folder (:meth:`_texture_homes`), *output_dir* taking any object
        without one -- placed, not baked there, so a bake that fails partway
        leaves no stray file in a texture folder. Mirror of mayatk's.
        """
        # What was handed stays the Scope its lights are read from (:meth:`_lighting`).
        scope = self._handed(objects)
        targets = self.bake_targets(scope)
        if not targets:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}
        claims = kwargs.pop("claims", None)
        if claims is None:
            claims = LightmapRecords.claims()
        if not self.beside_textures:
            with self._lighting(scope):
                return self._bake(targets, prefix=prefix, claims=claims, **kwargs)

        homes = self._texture_homes(targets)
        output_dir = kwargs.pop("output_dir", None) or TextureBaker.default_output_dir(
            self.OUTPUT_SUBDIR
        )
        with (
            self._lighting(scope),
            ptk.TempArtifacts("lightmap_bake", policy="scoped") as tmp,
        ):
            baked = self._bake(
                targets,
                output_dir=tmp.dir_path(),
                prefix=prefix,
                claims=claims,
                **kwargs,
            )
            # Moved out before the work dir is swept on exit.
            placed = self._place_unpacked(
                {name: (path, None) for name, path in baked.items()},
                output_dir,
                homes,
                claims=claims,
            )
        return {name: path for name, (path, _rect) in placed.items()}

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
        claims: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Bake one HDR lightmap per object into the lightmap UV channel.

        Adds the lightmap *workflow* (packed UV2, the lighting-only pass, lightmap output
        dir) on top of the generic :class:`TextureBaker` primitive it composes.

        Parameters:
            objects: Mesh objects (refs or names). Defaults to current selection.
            output_dir: Output directory (created if missing). Defaults to
                ``<file project texture folder>/lightmaps`` (:attr:`OUTPUT_SUBDIR`,
                ``TextureBaker.default_output_dir``; mayatk's folder too).
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
            claims: :meth:`LightmapRecords.claims` -- the file names other
                objects read, which no map of this bake may take (forwarded to
                :meth:`TextureBaker.bake`). ``None`` for maps that are not
                deliverables: an atlas's tiles, baked into a work dir.

        Returns ``{object_name: lightmap_path}`` for each successful bake.
        """
        meshes = TextureBaker.resolve_meshes(objects)
        if not meshes:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        # A LEGACY atlas commit squeezed the lightmap UVs into its rect; restore
        # the unit square before anything reads or bakes them, folding the rect
        # into the binding so the object's current map still samples right.
        LightmapRecords.migrate_legacy([obj.name for obj in meshes])
        self._warn_if_unlit_scene()

        uv_set = uv_set or LIGHTMAP_UV_SET
        if create_uvs:
            UvUtils.create_lightmap_uvs(meshes, uv_set=uv_set, quiet=True)

        # Every target in the render of every other's: a bake's whole list
        # reaches here once, whether its maps ship apart or as one atlas --
        # save the isolated ones (:class:`LightmapIsolateSet`), each baked in
        # a pass of its own and out of every other pass's render.
        passes, isolated = self._isolation_passes(meshes)
        result: Dict[str, str] = {}
        done_before = 0
        cancelled = False

        def tick(done: int, _total: int, name: str) -> bool:
            # One count across the passes, so a progress bar runs once.
            nonlocal cancelled
            keep = on_progress(done_before + done, len(meshes), name)
            cancelled = cancelled or keep is False
            return keep

        # The scene's lights as this bake renders them (:meth:`_lighting`):
        # already set by the entry point that was handed the Scope, else set
        # here from the meshes (and what is under them).
        with self._lighting(meshes), self._white_card() as card:
            for members, keep in passes:
                if cancelled:
                    break
                # The whole bake's targets stay static in every pass: one not
                # isolated is not baked before until the commit at the end.
                with self._out_of_render(meshes, isolated, keep):
                    result.update(
                        self._texture_baker.bake(
                            members,
                            bake_type="DIFFUSE",
                            pass_filter={"DIRECT", "INDIRECT"},
                            use_pass_color=False,  # lighting-only excludes albedo
                            shader=card,
                            output_dir=(
                                output_dir
                                or TextureBaker.default_output_dir(self.OUTPUT_SUBDIR)
                            ),
                            prefix=prefix,
                            suffix=suffix,
                            margin=margin,
                            # Per-object: target the object's own lightmap UV
                            # (robust to a pre-existing, differently-named
                            # lightmap layer; falls back to the standard set
                            # name).
                            uv_set=lambda o: UvUtils.find_lightmap_uv_set(o) or uv_set,
                            stem=stem,
                            size=size,
                            on_progress=(
                                tick
                                if on_progress is not None and len(passes) > 1
                                else on_progress
                            ),
                            # A pass names its maps apart from the earlier
                            # passes' too: TextureBaker keeps one batch's names
                            # apart, and a door sharing its cabinet's material
                            # (one stem) wrote over the cabinet's map.
                            claims=self._claimed_by(claims, result),
                        )
                        or {}
                    )
                done_before += len(members)
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
    # Atlas consolidation ("Atlas by Material" packing — cmb002 index 1)
    # ------------------------------------------------------------------
    # :meth:`bake_atlas` is the entry point to prefer -- it plans the layout first and bakes
    # each object straight to its footprint. The two-call form (``bake_separated`` then
    # ``pack_atlas``) remains for callers that already hold maps they did not bake here.
    #
    # Group the per-object lightmaps by primary material, give each object (each INSTANCE --
    # linked duplicates are first-class) a packed rect at the group's one texel density, and
    # assemble ONE shared EXR per group. UVs are never rewritten: every mesh keeps its shared [0,1] unwrap and the
    # rect is committed as the per-instance ``scaleOffset`` binding -- the industry-standard
    # model (Unity ``Renderer.lightmapScaleOffset``; glTF ``KHR_texture_transform``), and the
    # only one instances can express (per-instance data cannot live in shared UV data).
    # The DCC-agnostic layout math is REUSED from pythontk
    # (``ptk.ImgUtils.pack_atlas_rects`` / ``atlas_extent`` / ``uv_axis_scale`` /
    # ``atlas_pixel_rects`` — all pure-Python, no cv2, the same helpers mayatk uses, with
    # ``compute_atlas_layout`` the fallback for a group too big to pack); only the EXR assembly is
    # Blender-native (bpy image I/O + a numpy paste/dilate, since Blender's runtime ships no
    # cv2). Legacy commits that DID repack UVs recorded the rect as the marker's ``uvRect``;
    # :meth:`revert_lightmap` still inverts those.

    def bake_atlas(
        self,
        objects=None,
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        claims: Optional[Dict[str, Any]] = None,
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
        texture folder with files the caller has no use for. With :attr:`beside_textures`
        the atlases are packed in the work dir too and each is placed in its material
        group's texture folder (:meth:`_texture_homes`), *output_dir* taking a group
        without one. The plan resolves through :meth:`bake_targets`, so members of the
        file's :class:`LightmapExcludeSet` take no cell (they still light the rest).

        Extra ``kwargs`` are forwarded to the bake core (:meth:`_bake`). *prefix* / *suffix* name
        both the tiles and the atlas (the ``lightmap_irr_`` prefix a loose
        ``bake_separated`` call defaults to is pointless here — the tiles never
        leave the work dir). *claims* are the file names other objects read
        (:meth:`LightmapRecords.claims`, the default) that the finished maps must
        not take; they name the deliverables, never the work-dir tiles -- passed
        through to both, they collided on ``bake_separated``'s own and raised.

        **Part of a group** already in an atlas -- a Selected bake of some of
        its objects, or :meth:`rebake_changed`'s -- is written back into the
        cells those objects own in it (:meth:`_in_place_plan`,
        :meth:`_pack_in_place`): baked at its cell's size, the atlas read,
        their cells replaced, the seams they share with the rest stitched
        one-sided, and every other object's texels kept. So each material
        keeps ONE atlas at one texel density. A target that no longer fits its
        cell (its atlas extent drifted past :attr:`_IN_PLACE_TOLERANCE`), one
        new to the group, an atlas baked before cells were recorded or split
        by an earlier partial bake: the WHOLE group re-bakes into a fresh
        layout instead, logged with why, once -- the next partial bake goes in
        place. A member that cannot bake now (in the Exclude set) keeps
        reading the old atlas, and the rest take the next free name. Mirror of
        mayatk's.

        Returns :meth:`pack_atlas`'s ``{object_name: (atlas_path, rect)}``.
        """
        return self._bake_atlas(
            objects,
            output_dir=output_dir,
            prefix=prefix,
            suffix=suffix,
            claims=claims,
            **kwargs,
        ).packed

    def _bake_atlas(
        self,
        objects=None,
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "_Lightmap",
        claims: Optional[Dict[str, Any]] = None,
        intensity: float = 1.0,
        **kwargs,
    ) -> _AtlasBake:
        """:meth:`bake_atlas`, with what :meth:`bake` records beside the maps.

        *intensity* is scaled into what this writes, once: an atlas laid out
        anew after it is written, a cell written in place as its tile before
        it lands -- the atlas around it carries it already. Mirror of
        mayatk's.
        """
        done = _AtlasBake()
        # What was handed stays the Scope its lights are read from (:meth:`_lighting`).
        scope = self._handed(objects)
        targets = self.bake_targets(scope)
        if not targets:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return done
        # The layouts are settled BEFORE the plan reads them (mirror of mayatk's): the plan
        # sizes and shapes each cell from the lightmap layout that renders, and Smart UV
        # Project scales a generated layout's islands to fill the square -- a stretch only
        # a cell planned from that layout undoes. A legacy atlas marker's squeezed UVs are
        # restored first, as ``_bake`` restores them.
        create_uvs = kwargs.pop("create_uvs", True)
        uv_set = kwargs.get("uv_set") or LIGHTMAP_UV_SET

        def prepare(names: List[str]) -> None:
            meshes = TextureBaker.resolve_meshes(names)
            LightmapRecords.migrate_legacy([obj.name for obj in meshes])
            if create_uvs:
                UvUtils.create_lightmap_uvs(meshes, uv_set=uv_set, quiet=True)

        prepare(targets)
        output_dir = output_dir or TextureBaker.default_output_dir(self.OUTPUT_SUBDIR)
        if claims is None:
            claims = LightmapRecords.claims()
        # Read BEFORE the bake, as mayatk's is (there a target wears the white card
        # while it bakes, and its textures cannot be read off it).
        homes = self._texture_homes(targets) if self.beside_textures else None
        with (
            self._lighting(scope),
            ptk.TempArtifacts("lightmap_bake", policy="scoped") as tmp,
        ):
            # Part of a group already in an atlas goes back into the cells it
            # owns there; a group that cannot re-bakes whole (_in_place_plan).
            placing = self._in_place_plan(
                targets,
                intensity=intensity,
                folders={t: (homes or {}).get(t) or output_dir for t in targets},
            )
            for name, why in placing.repack.items():
                self.logger.info("%s: re-packed whole -- %s", name, why)
            if placing.expand:
                extra, excluded = self._targets(placing.expand)
                if excluded:
                    self.logger.warning(
                        "%d member(s) of an atlas that re-packs whole cannot bake "
                        "now (in the Exclude set) and keep reading it, so the "
                        "rest of their group takes the next free name: %s",
                        len(excluded),
                        ", ".join(excluded),
                    )
                if extra:
                    prepare(extra)
                    if homes is not None:
                        homes.update(self._texture_homes(extra))
                    targets = targets + extra
            done.repacked = dict(placing.repack)
            in_place = set(placing.placed)
            rest = [t for t in targets if t not in in_place]
            plan = self.atlas_plan(rest) if rest else {}
            planned = [name for entries in plan.values() for name, _rect in entries]
            planned += placing.placed
            cells = self.plan_sizes(plan)
            for obj in placing.placed:
                row0, row1, col0, col1 = ptk.ImgUtils.atlas_pixel_rects(
                    [placing.entries[obj]["cell"]], self.resolution
                )[0]
                cells[obj] = (max(1, col1 - col0), max(1, row1 - row0))
            # The bake core, not bake_separated: these are tiles for the pack, which
            # Beside Material Textures must not scatter into texture folders.
            baked = self._bake(
                planned,
                output_dir=tmp.dir_path(),
                prefix=prefix,
                suffix=suffix,
                # Resolved per object AT BAKE TIME, after the lightmap UVs exist:
                # the island the tile is cropped to decides how far it renders
                # above its cell.
                size=lambda obj: self._tile_size(obj, cells.get(obj.name)),
                # The tiles are intermediates: the assembly masks them with the
                # SAME dead-texel rule (``_signal_mask``) while compositing, so
                # healing each one first is a load/save round trip per object for
                # an answer that is recomputed and discarded. A solo group, which
                # skips the assembly, is healed by ``_pack_group`` instead.
                heal=False,
                # Named in the work dir, which nobody reads: the pack names the
                # deliverables, against the file's claims.
                claims=None,
                create_uvs=False,  # built above, before the plan
                **kwargs,
            )
            if not baked:
                return done
            for atlas, objs in placing.in_place.items():
                tiles = {o: baked[o] for o in objs if o in baked}
                if not tiles:
                    continue
                try:
                    written = self._pack_in_place(atlas, tiles, placing, intensity)
                except Exception as e:  # held open, unreadable: keep the old
                    self.logger.error(
                        "%s could not be written in place (%s); %s keep the map "
                        "they had.",
                        os.path.basename(atlas),
                        e,
                        ", ".join(tiles),
                    )
                    continue
                self.logger.info(
                    "%s: %d cell(s) written in place, every other one kept: %s",
                    os.path.basename(atlas),
                    len(written),
                    ", ".join(written),
                )
                done.packed.update(written)
                done.in_place.extend(written)
                for obj in written:
                    entry = placing.entries[obj]
                    done.cells[obj] = {
                        key: entry[key] for key in ("cell", "extent", "size", "packing")
                    }
            anew = {o: p for o, p in baked.items() if o not in in_place}
            if not anew:
                return done
            packed = self.pack_atlas(
                anew,
                # Beside the textures, the atlases are staged in a folder of their
                # own -- never among the tiles, whose names they share -- and placed
                # per group below.
                output_dir=(
                    os.path.join(tmp.dir_path(), "atlas")
                    if homes is not None
                    else output_dir
                ),
                prefix=prefix,
                suffix=suffix,
                plan=plan,
                claims=claims,
            )
            if homes is not None:
                # The work dir is swept on exit, so nothing may still point into it.
                packed = self._place_unpacked(packed, output_dir, homes, claims=claims)
            if packed and float(intensity) != 1.0:
                self._apply_intensity([p for p, _rect in packed.values()], intensity)
            done.packed.update(packed)
            done.cells.update(self._plan_cells(plan, packed))
        return done

    def _plan_cells(
        self,
        plan: Dict[str, List[Tuple[str, List[float]]]],
        packed: Dict[str, Tuple[str, List[float]]],
    ) -> Dict[str, Dict[str, Any]]:
        """``{object: {"cell", "extent", "size", "packing"}}`` for the bake baseline.

        Each packed object's plan cell, the :meth:`_atlas_extent` it was sized
        for and the atlas size -- what a later bake checks before writing it
        back in place (:meth:`_in_place_plan`). A group whose members did not
        land in one file (its pack failed, each kept its own map) is recorded
        per-object, with no cell. Mirror of mayatk's.
        """
        import bpy

        cells: Dict[str, Dict[str, Any]] = {}
        for entries in plan.values():
            files = {packed[n][0] for n, _rect in entries if n in packed}
            for name, rect in entries:
                if name not in packed:
                    continue
                if len(files) == 1:
                    cells[name] = {
                        "cell": [float(v) for v in rect],
                        "extent": [
                            float(v)
                            for v in self._atlas_extent(bpy.data.objects.get(name))
                        ],
                        "size": int(self.resolution),
                        "packing": "atlas",
                    }
                else:
                    cells[name] = {
                        "packing": "per_object",
                        "size": int(self.resolution),
                    }
        return cells

    #: How far, per axis, a re-baked object's atlas extent may drift from the
    #: one its cell was sized for and still be written back into it
    #: (``ptk.BakeBaseline.cell_fits``); ``None`` is the baseline's own
    #: ``CELL_TOLERANCE`` (10%). Mirror of mayatk's.
    _IN_PLACE_TOLERANCE: Optional[float] = None

    def _in_place_plan(
        self,
        targets: List[str],
        intensity: Optional[float] = None,
        folders: Optional[Dict[str, str]] = None,
        expand: bool = True,
    ) -> _InPlacePlan:
        """Which *targets* write back into their own atlas cells -- the one decision point.

        Mirror of mayatk's. Each material group (:meth:`_primary_material`,
        the atlas group) is decided whole, against the atlases it owns -- an
        atlas is the group's most of whose readers are. One that owns none
        bakes as any group does. One that owns ONE goes in place when every
        target of it can (:meth:`_in_place_refusal`): the atlas is on disk at
        this bake's :attr:`resolution` and in the folder the bake writes the
        group to, every object reading it has a recorded cell (the bake
        baseline, :meth:`LightmapRecords.bake_state`) at the intensity this
        bake applies, and each target reads it already and still fits its
        cell.

        Otherwise -- or when the group owns two atlases, a split an earlier
        partial bake left -- its WHOLE group re-bakes into a fresh layout: with
        *expand*, every object reading those atlases whose primary material is
        still the group's joins the bake (``repack`` says why, once). That
        heals a legacy split, and a legacy atlas baked before cells were
        recorded, once; their next partial re-bake goes in place.

        Read-only: nothing is baked or written.

        Parameters:
            targets: The mesh object names the bake renders.
            intensity: The multiplier the bake applies; ``None`` does not ask
                (the dry run, which does not know it).
            folders: ``{target: folder}`` the bake writes each to; ``None``
                does not ask.
            expand: Whether a group that cannot go in place brings its other
                members into the bake (a pack of maps already rendered cannot).
        """
        import bpy

        plan = _InPlacePlan()
        if not targets:
            return plan
        markers = {obj.name: info for obj, info in LightmapRecords._marker_records()}
        path_of = {name: p for name, _n, p in LightmapRecords._reads() if p}
        by_file: Dict[str, List[str]] = {}
        spelled: Dict[str, str] = {}
        for reader in sorted(markers):
            path = path_of.get(reader)
            if path:
                key = os.path.normcase(os.path.abspath(path))
                by_file.setdefault(key, []).append(reader)
                spelled.setdefault(key, path)
        atlases = {f for f, readers in by_file.items() if len(readers) > 1}
        if not atlases:
            return plan  # every map is its object's own
        record = LightmapRecords.bake_state()["objects"]
        entries = {k: e for k, e in record.items() if e["removed_at"] is None}
        groups: Dict[str, str] = {}

        def group(name: str) -> str:
            if name not in groups:
                groups[name] = (
                    self._primary_material(bpy.data.objects.get(name))
                    or "__no_material__"
                )
            return groups[name]

        # Each atlas is the group's most of its readers belong to: a reader
        # whose material changed since (Texture Combine's merge, a reassigned
        # object) is new to its NEW group's atlas, never the owner of its old.
        group_files: Dict[str, set] = {}
        for f in atlases:
            votes: Dict[str, int] = {}
            for reader in by_file[f]:
                votes[group(reader)] = votes.get(group(reader), 0) + 1
            owner = min(votes, key=lambda g: (-votes[g], g))
            group_files.setdefault(owner, set()).add(f)
        targets = list(dict.fromkeys(getattr(t, "name", t) for t in targets))
        chosen = set(targets)
        for key in dict.fromkeys(group(t) for t in targets):
            files = sorted(group_files.get(key, ()))
            if not files:
                continue
            members = [t for t in targets if group(t) == key]
            if len(files) > 1:
                why = (
                    f"{len(files)} atlases split this material ("
                    + ", ".join(os.path.basename(spelled[f]) for f in files)
                    + ")"
                )
            else:
                why = self._in_place_refusal(
                    spelled[files[0]],
                    members,
                    by_file[files[0]],
                    entries,
                    markers,
                    intensity,
                    folders,
                )
            if why is None:
                atlas = spelled[files[0]]
                plan.in_place[atlas] = members
                plan.readers[atlas] = list(by_file[files[0]])
                for reader in by_file[files[0]]:
                    plan.entries[reader] = entries[reader]
                    plan.rects[reader] = [
                        float(v)
                        for v in markers[reader].get(
                            "scaleOffset", self._IDENTITY_SCALE_OFFSET
                        )
                    ]
                continue
            extra = list(
                dict.fromkeys(
                    r
                    for f in files
                    for r in by_file[f]
                    if r not in chosen and group(r) == key
                )
            )
            if expand and extra:
                n = len(extra)
                why += f"; its {n} other member{'s' if n != 1 else ''} re-bake with it"
                plan.expand.extend(r for r in extra if r not in plan.expand)
            for f in files:
                plan.repack[os.path.basename(spelled[f])] = why
        return plan

    def _in_place_refusal(
        self,
        atlas: str,
        members: List[str],
        readers: List[str],
        entries: Dict[str, Dict[str, Any]],
        markers: Dict[str, Dict[str, Any]],
        intensity: Optional[float],
        folders: Optional[Dict[str, str]],
    ) -> Optional[str]:
        """Why *members* cannot be written back into their cells of *atlas*, or ``None``.

        The checks :meth:`_in_place_plan` documents, cheapest first, the
        first that fails worded for the log and the report. Mirror of mayatk's.
        """
        import bpy

        name = os.path.basename(atlas)
        res = int(self.resolution)
        size = ptk.ImgUtils.get_image_size(atlas)
        if not size:
            return f"{name} cannot be read"
        if tuple(size) != (res, res):
            return f"it is {size[0]} x {size[1]} px, this bake {res} px"
        if folders:
            home = os.path.normcase(os.path.dirname(os.path.abspath(atlas)))
            wanted = {
                os.path.normcase(os.path.abspath(folders[m]))
                for m in members
                if folders.get(m)
            }
            if wanted and wanted != {home}:
                return "this bake writes its maps to another folder"
        untracked = [r for r in readers if not (entries.get(r) or {}).get("cell")]
        if untracked:
            return (
                f"{len(untracked)} of its {len(readers)} readers have no recorded "
                "cell (baked before change tracking)"
            )
        other = [
            r
            for r in readers
            if entries[r].get("packing") != "atlas"
            or entries[r].get("size") != res
            or entries[r].get("map") != name.lower()
        ]
        if other:
            return f"{len(other)} of its readers were recorded against another map"
        if intensity is not None:
            for reader in readers:
                was = float(markers[reader].get("intensity", 1.0))
                if abs(was - float(intensity)) > 1e-6 * max(1.0, abs(was)):
                    return f"it holds intensity {was:g}, this bake {float(intensity):g}"
        for member in members:
            if member not in readers:
                return f"{member} is new to it"
            recorded = entries[member]["extent"]
            now = [float(v) for v in self._atlas_extent(bpy.data.objects.get(member))]
            if not ptk.BakeBaseline.cell_fits(recorded, now, self._IN_PLACE_TOLERANCE):
                area = (now[0] * now[1]) / max(
                    (recorded or [1.0, 1.0])[0] * (recorded or [1.0, 1.0])[1], 1e-12
                )
                return f"{member}: its cell no longer fits ({area - 1.0:+.0%} area)"
        return None

    def _pack_in_place(
        self,
        atlas: str,
        tiles: Dict[str, str],
        placing: _InPlacePlan,
        intensity: float = 1.0,
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
    ) -> Dict[str, Tuple[str, List[float]]]:
        """Write *tiles* back into their own cells of *atlas*; every other cell stays.

        Mirror of mayatk's. Each tile is finished for its recorded cell exactly
        as a fresh pack finishes one (:meth:`_assemble_atlas_exr`: cropped to
        its island, resampled onto the cell's border-texel centres, its dead
        texels refilled) and scaled by *intensity* there -- the atlas around it
        carries it already -- then placed onto the atlas as read (``base``).
        The seams it shares with a cell left as it was are stitched ONE-SIDED
        (``keep``, ``ptk.ImgUtils.stitch_seams`` ``hold``): the new cell meets
        the held one, which keeps every texel. The gutters are then refilled as
        a fresh pack fills them, the atlas is written beside its staging and
        swapped in whole (:meth:`_move_into_place` -- ``save_render`` is not
        atomic), and every image datablock of that file is reloaded
        (:meth:`_reload_images`). A bake never changes the texels another
        object reads.

        Parameters:
            atlas: The atlas's path, as :meth:`_in_place_plan` named it.
            tiles: ``{object: tile path}`` -- this bake's renders of its
                targets there.
            placing: The plan (cells, readers, published rects).
            intensity: The multiplier the atlas already carries.
            regions: As :meth:`pack_atlas`.

        Returns:
            ``{object: (atlas, published rect)}`` for each cell written.

        Raises:
            RuntimeError: The atlas cannot be read or is another size.
            OSError: It cannot be written (held open): nothing is.
        """
        res = int(self.resolution)
        base = self._read_exr_top_down(atlas)  # raises when unreadable
        if base.shape[:2] != (res, res):
            raise RuntimeError(
                f"{os.path.basename(atlas)} is {base.shape[1]} x {base.shape[0]} px, "
                f"this bake {res} px"
            )
        regions = regions or {}
        readers = placing.readers[atlas]
        cell_of = {r: [float(v) for v in placing.entries[r]["cell"]] for r in readers}
        placements: List[Tuple[str, List[float], Any]] = []
        names: List[str] = []
        for obj in readers:
            if obj not in tiles:
                continue
            if not os.path.exists(tiles[obj]):
                self.logger.warning("Atlas: missing map for %s; skipping.", obj)
                continue
            placements.append(
                (
                    tiles[obj],
                    cell_of[obj],
                    self._in_region(self._lightmap_uv_bbox(obj), regions.get(obj)),
                )
            )
            names.append(obj)
        if not placements:
            return {}
        # Held: every reader not written, unless it shares a written cell (a
        # rebound copy reads its source's cell, and follows it).
        written_cells = {tuple(cell) for _src, cell, _bbox in placements}
        held = [
            r
            for r in readers
            if r not in names and tuple(cell_of[r]) not in written_cells
        ]
        with ptk.TempArtifacts("lightmap_in_place", policy="scoped") as tmp:
            staged = os.path.join(tmp.dir_path(), os.path.basename(atlas))
            published = self._assemble_atlas_exr(
                staged,
                placements,
                self._atlas_gutter(),
                edges=[self._boundary_edges(n, regions.get(n)) for n in names],
                base=base,
                keep=[
                    (cell_of[r], placing.rects[r], self._boundary_edges(r))
                    for r in held
                ],
                intensity=intensity,
            )
            self._move_into_place(staged, atlas)
        self._reload_images(atlas)
        return {
            n: (atlas, self._onto_layout(self._aimed_rect(rect, bounds), n, regions))
            for n, (rect, bounds) in zip(names, published)
        }

    def _cells_mask(self, cells: List[List[float]]) -> Any:
        """``(resolution, resolution)`` bool, rows top-down: the texels of *cells*
        -- exact (``ptk.ImgUtils.atlas_pixel_rects``, the rounding placement
        uses), clamped to the frame both ways. Mirror of mayatk's."""
        import numpy as np

        mask = np.zeros((self.resolution, self.resolution), dtype=bool)
        h, w = mask.shape
        for row0, row1, col0, col1 in ptk.ImgUtils.atlas_pixel_rects(
            cells, self.resolution
        ):
            mask[
                max(row0, 0) : min(max(row1, 0), h), max(col0, 0) : min(max(col1, 0), w)
            ] = True
        return mask

    @staticmethod
    def _edges_in_atlas(edges, rect):
        """:meth:`_boundary_edges` rows with their UVs taken through *rect* --
        atlas space, so members with different rects can be matched as one.
        Mirror of mayatk's."""
        sx, sy, ox, oy = (float(v) for v in rect)
        out = edges.copy()
        out[:, [6, 8]] = edges[:, [6, 8]] * sx + ox
        out[:, [7, 9]] = edges[:, [7, 9]] * sy + oy
        return out

    @staticmethod
    def _reload_images(path: str) -> List[str]:
        """Reload every image datablock that reads *path*; their names.

        A file rewritten under a loaded image (an atlas written in place, a
        viewer showing it, a material wired to it for an export) keeps showing
        the old pixels until it is reloaded -- Blender reads a file once.
        """
        import bpy

        target = os.path.normcase(os.path.abspath(path))
        reloaded = []
        for image in bpy.data.images:
            if image.source != "FILE" or image.packed_file is not None:
                continue
            try:
                here = os.path.normcase(
                    os.path.abspath(bpy.path.abspath(image.filepath))
                )
            except Exception:  # noqa: BLE001 -- an unreadable path is no match
                continue
            if here == target:
                image.reload()
                reloaded.append(image.name)
        return reloaded

    def atlas_plan(self, objects) -> Dict[str, List[Tuple[str, List[float]]]]:
        """``{material: [(object_name, rect), ...]}`` — the atlas layout, decided before baking.

        Groups the meshes by primary material and PACKS each group into its atlas
        (``ptk.ImgUtils.pack_atlas_rects``): every object a cell of its layout box's shape ON
        THE SURFACE, at one texel density for the whole group, each cell a whole number of texels at
        least :meth:`_atlas_gutter` from every neighbour and the atlas edge, its padded slot
        on whole :attr:`_ATLAS_BLOCK` blocks (a solo group keeps the identity rect: it is
        already its own atlas). Objects that share a mesh (linked duplicates / instances)
        are FIRST-CLASS: each stands somewhere different and receives different light, so
        each gets its own cell over the one shared [0,1] unwrap — the rect travels as the
        per-instance scaleOffset binding, never into the shared UVs — and copies take equal
        cells in name order. A cell's size is :meth:`_atlas_extent`: world area over the
        layout's island coverage, so a scaled copy earns proportional texels, in a shape that
        undoes the layout's stretch (one too long for the atlas at the group's density folds
        toward square rather than lowering that density for everyone). Twin of
        mayatk's ``atlas_plan``, which replaced the same squarified treemap: it bent cells
        away from their content's shape and lost the gutter on small cells at a low
        resolution. A group too big to fit at :attr:`_ATLAS_MIN_TEXELS` per cell falls back
        to that tiling, warned.

        Pure bookkeeping: nothing is baked, read from disk or written, which is what lets
        :meth:`bake_atlas` size each bake from it. The meshes come from
        :meth:`bake_targets`, so an excluded one takes no cell.
        """
        import bpy

        names: List[str] = sorted(self.bake_targets(objects))  # deterministic order

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
            # The gutter is the room the pack's dilation fills, so mip levels / bilinear
            # taps can't bleed across neighbours; cells are whole texels, so the
            # assembler's placement and the published rect agree. Twin of mayatk's.
            rects = ptk.ImgUtils.pack_atlas_rects(
                [self._atlas_extent(bpy.data.objects.get(n)) for n in group],
                self.resolution,
                gutter=gutter,
                min_texels=self._ATLAS_MIN_TEXELS,
                align=self._ATLAS_BLOCK,
            )
            if rects is None:
                self.logger.warning(
                    "Atlas: the %d objects of %s do not fit a %d px atlas even at %d "
                    "texels each; tiling them instead (cells may lose their gutter). "
                    "Bake them at a higher resolution, or split them across materials.",
                    len(group),
                    key,
                    self.resolution,
                    self._ATLAS_MIN_TEXELS,
                )
                rects = ptk.ImgUtils.snap_atlas_rects(
                    ptk.ImgUtils.inset_atlas_rects(
                        ptk.ImgUtils.compute_atlas_layout(
                            [self._surface_area(bpy.data.objects.get(n)) for n in group]
                        ),
                        self.resolution,
                        gutter,
                    ),
                    self.resolution,
                )
            plan[key] = [(n, [float(v) for v in rect]) for n, rect in zip(group, rects)]
        return plan

    #: Smallest atlas cell, in texels per axis (mirror of mayatk's).
    _ATLAS_MIN_TEXELS: int = 4

    #: The slot grid every atlas cell's padded slot lies on: the 4 x 4 blocks BC6H /
    #: UASTC / ASTC encode, so no block mixes two objects' light (mirror of mayatk's).
    _ATLAS_BLOCK: int = 4

    #: Island coverage assumed for an object with no lightmap layer yet.
    _UNWRAP_FILL: float = 0.75

    def _atlas_extent(self, obj) -> Tuple[float, float]:
        """*obj*'s natural atlas extent -- the group's texel density (twin of mayatk's).

        ``ptk.ImgUtils.atlas_layout_extent`` of its world area and its lightmap layer's
        triangles -- the box the pack places (the islands' bbox when :meth:`_pack_group`
        crops to it, by the same :attr:`_CROP_MAX_COVERAGE`) and their coverage -- turned
        to that box's shape on the surface (``ptk.ImgUtils.uv_axis_scale``), so a layout
        stretched to fill its square still gets square texels on the mesh.
        """
        area = self._surface_area(obj) if obj is not None else 1.0
        tris = points = None
        if obj is not None:
            tris, points = UvUtils.get_uv_world_triangles(
                obj, UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            )
        scale = ptk.ImgUtils.uv_axis_scale(points, tris)
        return ptk.ImgUtils.atlas_layout_extent(
            area,
            tris,
            stretch=scale[0] / scale[1] if scale else 1.0,
            max_coverage=self._CROP_MAX_COVERAGE,
            fill=self._UNWRAP_FILL,
        )

    def plan_sizes(
        self, plan: Dict[str, List[Tuple[str, List[float]]]]
    ) -> Dict[str, Tuple[int, int]]:
        """``{object_name: (width, height)}`` — the pixel footprint each object occupies.

        The cell each tile is fitted into: assembling the atlas resizes each tile's island
        into these dimensions, so :meth:`bake_atlas` renders a full-coverage island at
        exactly this size and a partial one only as far above it as its crop takes
        (:meth:`_tile_size`). Derived through ``ptk.ImgUtils.atlas_pixel_rects``, the same
        rounding SSoT :meth:`_assemble_atlas_exr` places with, so a full-coverage tile
        never needs rescaling.
        """
        sizes: Dict[str, Tuple[int, int]] = {}
        for entries in plan.values():
            pixel_rects = ptk.ImgUtils.atlas_pixel_rects(
                [rect for _n, rect in entries], self.resolution
            )
            for (name, _rect), (row0, row1, col0, col1) in zip(entries, pixel_rects):
                sizes[name] = (max(1, col1 - col0), max(1, row1 - row0))
        return sizes

    def _tile_size(
        self, obj, cell: Optional[Tuple[int, int]]
    ) -> Optional[Tuple[int, int]]:
        """The bake size that lands *obj*'s island at its *cell*'s density, or ``None``.

        The cell over the island's coverage (:meth:`_pack_group` crops the tile
        to the island and fits THAT to the cell), capped at :attr:`resolution`.
        ``None`` -- no plan for *obj* -- bakes the full square.
        """
        if cell is None:
            return None
        eu, ev = ptk.ImgUtils.uv_crop_extent(
            self._lightmap_uv_bbox(obj), self._CROP_MAX_COVERAGE
        )
        res = int(self.resolution)
        return (
            min(res, int(math.ceil(cell[0] / eu))),
            min(res, int(math.ceil(cell[1] / ev))),
        )

    @staticmethod
    def _lightmap_uv_bbox(obj) -> Optional[Tuple[float, float, float, float]]:
        """``(u0, v0, u1, v1)`` of *obj*'s lightmap-layer triangles, or ``None``.

        The layer the bake renders through (:meth:`_bake`'s resolution), so the
        crop and the bake can never describe different layouts. ``None`` -- no
        object, layer or faces -- means "don't crop".
        """
        import bpy

        if isinstance(obj, str):
            obj = bpy.data.objects.get(obj)
        if obj is None:
            return None
        tris = UvUtils.get_uv_triangles(
            obj, UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
        )
        if not len(tris):
            return None
        flat = tris.reshape(-1, 2)
        (u0, v0), (u1, v1) = flat.min(axis=0), flat.max(axis=0)
        return (float(u0), float(v0), float(u1), float(v1))

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
        claims: Optional[Dict[str, Any]] = None,
        regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None,
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
        against so the layout can't be re-derived differently; ``None`` computes it here,
        after writing back in place what can be: called without a plan (the two-call
        form), objects whose atlas cells are recorded go back into them, in the atlas they
        read wherever it is (:meth:`_in_place_plan`, :meth:`_pack_in_place` --
        *output_dir* is for the rest), every other object's texels kept.

        Otherwise no atlas lands on a file name an object outside its group reads
        (*claims*, :meth:`LightmapRecords.claims`; ``None`` reads the file's now): a
        partial re-pack of a group -- or one whose member failed -- gets an atlas of its
        own, and the member left out keeps sampling the old one (:meth:`bake_atlas`
        re-bakes the whole group instead).

        *regions* (``{object_name: (u0, v0, u1, v1)}``) names the maps that cover only that
        part of their object's lightmap layout -- a cell cut out of another atlas (Texture
        Combine's re-pack): each is read in its region's frame and its rect composed back onto
        the layout, the fallback's identity rect included. Mirror of mayatk's.

        Returns ``{object_name: (atlas_path, [scaleX, scaleY, offsetX, offsetY])}`` — the rect
        is the ENGINE BINDING to publish per instance (``commit_lightmap(scale_offsets=...)``;
        identity for solo/fallback), not an applied UV remap.
        """
        if not mapping:
            return {}
        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))
        out: Dict[str, Tuple[str, List[float]]] = {}
        if plan is None:
            # The two-call form re-packs maps rendered whole: what owns a
            # recorded cell goes back into it, at intensity 1 (a pack applies
            # none), and only the rest is laid out below (mirror of mayatk's).
            placing = self._in_place_plan(list(mapping), intensity=1.0, expand=False)
            for atlas, objs in placing.in_place.items():
                try:
                    written = self._pack_in_place(
                        atlas, {o: mapping[o] for o in objs}, placing, 1.0, regions
                    )
                except Exception as e:
                    self.logger.warning(
                        "Atlas: %s could not be written in place (%s); its "
                        "objects are packed anew.",
                        os.path.basename(atlas),
                        e,
                    )
                    continue
                out.update(written)
                for obj in written:  # consumed, as a packed group's sources are
                    try:
                        os.remove(mapping[obj])
                    except OSError:
                        pass
            mapping = {o: p for o, p in mapping.items() if o not in out}
            if not mapping:
                return out
            plan = self.atlas_plan(list(mapping))
        if claims is None:
            claims = LightmapRecords.claims()

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
                    claims,
                    regions,
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
                            path = self._place(
                                mapping[n], output_dir, used, claims=claims, owners=(n,)
                            )
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
                        out[n] = (
                            path,
                            self._onto_layout(self._IDENTITY_SCALE_OFFSET, n, regions),
                        )
        return out

    @staticmethod
    def _to_region_rect(region: Tuple[float, float, float, float]) -> List[float]:
        """The ``scaleOffset`` taking layout uv into *region*'s own 0-1 frame."""
        u0, v0, u1, v1 = region
        su, sv = u1 - u0, v1 - v0
        return [1.0 / su, 1.0 / sv, -u0 / su, -v0 / sv]

    @classmethod
    def _onto_layout(cls, rect, name, regions) -> List[float]:
        """*rect*, which maps the map *name*'s own 0-1 (its region's, when
        *regions* names it), as the binding the engine applies to LAYOUT uv
        (twin of mayatk ``_pack_group``'s ``onto_layout``)."""
        if not regions or name not in regions:
            return list(rect)
        return list(ptk.ImgUtils.compose_rect(rect, cls._to_region_rect(regions[name])))

    @classmethod
    def _in_region(cls, bbox, region):
        """*bbox* ``(u0, v0, u1, v1)`` in layout uv, in *region*'s frame --
        as given without one."""
        if bbox is None or region is None:
            return bbox
        sx, sy, ox, oy = cls._to_region_rect(region)
        u0, v0, u1, v1 = bbox
        return (u0 * sx + ox, v0 * sy + oy, u1 * sx + ox, v1 * sy + oy)

    def _pack_group(
        self,
        key,
        entries,
        mapping,
        all_sources,
        output_dir,
        prefix,
        suffix,
        out,
        used,
        claims=None,
        regions=None,
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`).

        The atlas name is the group's to take only where the group's own members
        are all that read it (*claims*). A map in *regions* covers only that part of
        its layout: its island is read in the region's frame and its published rect
        composed back onto the layout."""
        regions = regions or {}
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
                claims=claims,
                owners=names,
            )
            # Idempotent safety net: a map baked HERE was already healed on
            # the way out of the bake, but ``pack_atlas`` is public and may be
            # handed maps from anywhere.
            self._heal_dead_texels(path)
            out[names[0]] = (
                path,
                self._onto_layout(self._IDENTITY_SCALE_OFFSET, names[0], regions),
            )
            return

        placements: List[Tuple[str, List[float], str]] = []
        for n, rect in entries:
            if not os.path.exists(mapping[n]):
                self.logger.warning("Atlas: missing map for %s; skipping.", n)
                continue
            placements.append((mapping[n], [float(v) for v in rect], n))
        if not placements:
            return

        # Reserved for the members that are actually IN it: a member whose tile
        # is missing gets no new map and keeps reading its old one, so counted as
        # an owner it let the group take that very file.
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        atlas_path = self._unique_atlas_path(
            output_dir,
            name,
            used,
            foreign,
            claims,
            owners=[n for _src, _so, n in placements],
        )

        published = self._assemble_atlas_exr(
            atlas_path,
            [
                (p, so, self._in_region(self._lightmap_uv_bbox(n), regions.get(n)))
                for p, so, n in placements
            ],
            self._atlas_gutter(),
            edges=[
                self._boundary_edges(n, regions.get(n)) for _p, _so, n in placements
            ],
        )

        for (src, _so, n), (rect, bounds) in zip(placements, published):
            # The rect is the deliverable, not a UV edit: the object's shared [0,1]
            # unwrap stays untouched and the engine applies the rect per instance
            # (Unity lightmapScaleOffset / glTF KHR_texture_transform). Published
            # aimed at border-texel CENTERS (:meth:`_aimed_rect`, the aim the
            # seam stitch read through); placement used the plan's cell.
            out[n] = (
                atlas_path,
                self._onto_layout(self._aimed_rect(rect, bounds), n, regions),
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
        claims: Optional[Dict[str, Any]] = None,
        owners=(),
    ) -> str:
        """Move a finished map into *output_dir* and return its new path.

        Only results belong in the destination -- a bake's intermediates stay in whatever
        work dir produced them. A same-named file already there is replaced when only the
        map's own *owners* read it (that is what re-baking means); a name anything else
        reads (*claims*, :meth:`LightmapRecords.claims`) is left alone, and so is a
        collision *within* one pack -- both take a numeric tail instead
        (:meth:`ptk.FileUtils.unique_path`). A move rather than a bare ``os.replace`` because
        the work dir is routinely on a different volume from the project.

        A destination that cannot be replaced takes an adjacent name instead of failing:
        the realistic cause is the previous map being held open by the DCC's own texture
        cache, and losing a finished bake over a file lock would be absurd. The swap is
        :meth:`_move_into_place`'s, so a move that fails never takes the old map with it.

        *stem* renames the map on the way in (default: keep the source's own). *avoid* is
        a set of abspaths that must not be overwritten -- another group's not-yet-consumed
        source maps, reachable only once *stem* is derived rather than inherited.
        """
        src_abs = os.path.abspath(src)
        os.makedirs(output_dir, exist_ok=True)
        src_stem, ext = os.path.splitext(os.path.basename(src))
        stem = stem or src_stem
        dst = ptk.FileUtils.unique_path(
            output_dir, stem, ext, used, claims=claims, owners=owners, avoid=avoid
        )
        if os.path.abspath(dst) != src_abs:
            try:
                LightmapBaker._move_into_place(src_abs, dst)
            except OSError:
                # Held open: the next spelling nothing occupies and nobody else
                # reads. The old file was never deleted, so it is still there.
                taken = set(used) | {
                    os.path.join(output_dir, n) for n in os.listdir(output_dir)
                }
                dst = ptk.FileUtils.unique_path(
                    output_dir,
                    stem,
                    ext,
                    taken,
                    claims=claims,
                    owners=owners,
                    avoid=avoid,
                )
                ptk.FileUtils.move_file(
                    src_abs, output_dir, new_name=os.path.basename(dst)
                )
        used.add(dst)
        return dst

    @staticmethod
    def _move_into_place(source: str, destination: str) -> None:
        """Move *source* onto *destination*, never deleting what is there first.

        ``ptk.FileUtils.move_file`` replaces it in one rename (across volumes it
        stages the source beside it and swaps it in), so a failure leaves the
        destination's old file as it was -- the object keeps its map -- and a
        swap that fails puts the source back, for the caller's next name.
        Mirror of mayatk's.

        Raises:
            OSError: The move or the swap failed; *destination* is untouched.
        """
        destination = os.path.abspath(destination)
        ptk.FileUtils.move_file(
            source, os.path.dirname(destination), new_name=os.path.basename(destination)
        )

    def _texture_homes(self, objects) -> Dict[str, str]:
        """``{object_name: folder}`` -- where :attr:`beside_textures` puts each map.

        The folder of the object's texture set (:meth:`TextureBaker.texture_set`),
        so ``<set>_Lightmap.exr`` lands beside ``<set>_BaseColor.png``. Only an
        absolute folder that exists on THIS machine qualifies: an image path that
        resolves nowhere (another drive, a moved library) must not have a bake
        create it, a packed image has no folder at all, and a ``//`` path in a
        .blend never saved resolves against Blender's working directory, which is
        no texture folder. And only one inside the file's project
        (``LightmapRecords.project_root``): a texture library two projects share
        took both projects' lightmaps under one name, each bake replacing the
        other's (mayatk measured it, 2026-10-03). An object without one is left
        out, and its map takes the bake's ``output_dir``. Mirror of mayatk's.
        """
        import bpy

        project = LightmapRecords.project_root() or ""
        names = [getattr(o, "name", o) for o in objects]
        homes: Dict[str, str] = {}
        for name in names:
            found = TextureBaker.texture_set(bpy.data.objects.get(name))
            folder = found[1] if found else ""
            if (
                folder
                and os.path.isabs(folder)
                and os.path.isdir(folder)
                and ptk.FileUtils.is_under(os.path.abspath(folder), project)
            ):
                homes[name] = folder
        if len(homes) != len(names):
            self.logger.info(
                "Beside textures: %d of %d object(s) have no texture folder on "
                "disk inside the project; their maps go to the output folder.",
                len(names) - len(homes),
                len(names),
            )
        return homes

    def _place_unpacked(
        self,
        packed: Dict[str, Tuple[str, Optional[List[float]]]],
        output_dir: str,
        homes: Optional[Dict[str, str]] = None,
        claims: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Tuple[str, Optional[List[float]]]]:
        """Move each map still outside its folder into it; return the fixed mapping.

        A map's folder is its object's entry in *homes* (the texture folders
        :attr:`beside_textures` places maps in), else *output_dir*. One move per
        unique file -- an atlas shared by six objects is not moved six times, and
        the first of them (plan order) decides its folder. No file lands on a name
        *claims* (:meth:`LightmapRecords.claims`) gives to an object the file is not
        placed for, nor over a file on disk the objects it is placed for do not ALL
        read: a texture folder other files share holds their maps under the same
        names, and replacing one hands that file this one's lighting. Mirror of
        mayatk's.

        A map that cannot be placed at all is left out of the returned mapping --
        the caller reports its objects unbaked, and they keep the map and marker
        they had. Handing back its work-dir path would point the marker at a file
        the caller is about to sweep.
        """
        homes = homes or {}
        claims = claims or {}
        failed: set = set()
        # The objects each source file is placed for: a name only they read is
        # theirs to replace, a name anyone else reads is not.
        placed_for: Dict[str, set] = {}
        for name, (path, _rect) in packed.items():
            placed_for.setdefault(os.path.abspath(path), set()).add(name)
        moved: Dict[str, str] = {}
        out: Dict[str, Tuple[str, Optional[List[float]]]] = {}
        for name, (path, rect) in packed.items():
            source = os.path.abspath(path)
            if source in moved:
                out[name] = (moved[source], rect)
                continue
            if source in failed:
                continue
            folder = homes.get(name) or output_dir
            if os.path.normcase(os.path.dirname(source)) == os.path.normcase(
                os.path.abspath(folder)
            ):
                moved[source] = path
                out[name] = (path, rect)
                continue
            stem, ext = os.path.splitext(os.path.basename(path))
            os.makedirs(folder, exist_ok=True)
            # An adjacent name rather than a refusal when the destination is held
            # open: the source sits in a work dir the caller is about to sweep, so
            # refusing loses the bake outright. Nothing placed earlier in this call
            # is replaced at all.
            placed = {os.path.normcase(p) for p in moved.values()}
            owners = placed_for.get(source, set())
            dst, error = None, None
            k = attempts = 0
            while attempts < 4:
                candidate = os.path.join(
                    folder, f"{stem}{ext}" if k == 0 else f"{stem}_{k}{ext}"
                )
                k += 1
                readers = claims.get(os.path.basename(candidate).lower())
                mine = bool(readers) and set(readers) <= owners
                if (
                    os.path.normcase(candidate) in placed
                    or (readers and not mine)
                    or (os.path.exists(candidate) and not mine)
                ):
                    continue
                attempts += 1
                try:
                    self._move_into_place(source, candidate)
                    dst = candidate
                    break
                except OSError as e:
                    error = e
            if dst is None:
                self.logger.error(
                    "Could not place %s in %s (%s); %s keep the map they had.",
                    os.path.basename(path),
                    folder,
                    error,
                    ", ".join(sorted(owners)) or name,
                )
                failed.add(source)
                continue
            if os.path.basename(dst) != f"{stem}{ext}":
                self.logger.warning(
                    "%s%s is held by another process or another file's map; "
                    "wrote %s instead.",
                    stem,
                    ext,
                    os.path.basename(dst),
                )
            moved[source] = dst
            out[name] = (dst, rect)
        return out

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

        A fully-dark map is left unfilled (a black bake is a faithful render of an
        unlit scene; the panel guard warns), as is a map with nothing to heal.
        Either way the map is rewritten as the delivered EXR
        (:meth:`_write_lightmap_exr`), so every deliverable leaves here sanitized
        and half-float. Idempotent: a healed map has no dead texels left to find.
        """
        import bpy
        import numpy as np

        img = None
        try:
            img = bpy.data.images.load(path)
            img.colorspace_settings.name = "Non-Color"  # read the raw linear data
            buf = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(buf)
            px = buf.reshape(img.size[1], img.size[0], img.channels)
            rgb = px[..., :3]
            valid = self._signal_mask(rgb)
            if valid is not None and valid.any() and not valid.all():
                px[..., :3] = ptk.ImgUtils.fill_empty_texels(rgb, mask=valid)
            self._write_lightmap_exr(path, px)
        except Exception as e:  # never lose a finished bake to a heal
            self.logger.warning("Dead-texel heal skipped for %s: %s", path, e)
        finally:
            if img is not None:
                bpy.data.images.remove(img)

    #: How a delivered lightmap is written: what mayatk's twin writes (half
    #: float, lossless ZIP). ``Image.save()`` cannot be told either -- it wrote
    #: uncompressed 32-bit, 16.8 MB per 1024 atlas against Arnold's ~2 MB for
    #: the same production room (2026-10-01). Applied in this order and put
    #: back in it: ``media_type`` leads because Blender 5.x offers only the
    #: ``file_format`` values its media type allows, so a scene saved with
    #: Video or Multi-Layer EXR output refused ``OPEN_EXR`` and no map was
    #: written. 4.x has no ``media_type``; a key the build lacks is skipped.
    _EXR_SETTINGS: Dict[str, str] = {
        "media_type": "IMAGE",
        "file_format": "OPEN_EXR",
        "color_depth": "16",
        "exr_codec": "ZIP",
        "color_mode": "RGBA",
    }

    @classmethod
    def _write_lightmap_exr(cls, path: str, pixels) -> None:
        """Write *pixels* (bottom-up ``(h, w, channels)`` float) as a delivered lightmap.

        The twin of mayatk's writer: non-finite RGB to 0, clamped to
        ``[0, 65504]`` (the half-float ceiling -- one NaN/Inf ray would otherwise
        ride into the engine's import as a garbage texel), saved half-float ZIP.
        ``save_render`` is the bpy route that honours a bit depth and codec; on
        a ``Non-Color`` float image it writes the linear values untouched (probed
        under AgX: max relative error 5e-4, half precision). The scene's own
        output settings are restored.
        """
        import bpy
        import numpy as np

        height, width, channels = pixels.shape
        rgba = np.ones((height, width, 4), dtype=np.float32)
        rgba[..., : min(channels, 4)] = pixels[..., :4]
        ceiling = cls.HALF_FLOAT_MAX
        rgba[..., :3] = np.clip(
            np.nan_to_num(rgba[..., :3], nan=0.0, posinf=ceiling, neginf=0.0),
            0.0,
            ceiling,
        )
        scene = bpy.context.scene
        settings = scene.render.image_settings
        pinned = {k: v for k, v in cls._EXR_SETTINGS.items() if hasattr(settings, k)}
        prior = {key: getattr(settings, key) for key in pinned}
        img = bpy.data.images.new(
            os.path.basename(path), width, height, alpha=True, float_buffer=True
        )
        try:
            # Colorspace BEFORE the pixel write: assigned later, the buffer is
            # discarded or the save goes through a view transform.
            img.colorspace_settings.name = "Non-Color"
            img.pixels.foreach_set(rgba.reshape(-1))
            for key, value in pinned.items():
                setattr(settings, key, value)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            img.save_render(path, scene=scene)
        finally:
            for key, value in prior.items():
                setattr(settings, key, value)
            bpy.data.images.remove(img)

    def _assemble_atlas_exr(
        self,
        atlas_path,
        placements,
        gutter,
        edges=None,
        base=None,
        keep=None,
        intensity: float = 1.0,
    ) -> List[Tuple[List[float], Tuple[float, float, float, float]]]:
        """Composite each ``(source_exr, cell, island_bbox)`` into one shared EXR at
        ``self.resolution`` via bpy image I/O (no cv2): load each source, crop it to its
        island (``ptk.ImgUtils.crop_to_uv_bbox``; a ``None`` bbox takes no crop), scale it
        into its cell's pixel rect, refill its rendered-dead texels, paste into a float
        atlas buffer, stitch the seams its members share, dilate content into the freed
        gutter, and save as OPEN_EXR. The pixel-rect mapping (incl. the UV bottom-up
        vs image top-down flip) comes from ``ptk.ImgUtils.atlas_pixel_rects`` — the same
        SSoT mayatk's cv2 assembler uses, so UV placement matches Unity's
        ``lightmapScaleOffset``.

        *edges* is each placement's open edges (:meth:`_boundary_edges`, its UVs in the
        source map's own frame), ``None`` for no stitch.

        A write back INTO an atlas (:meth:`_pack_in_place`) passes the atlas as read
        (*base*, top-down, its size this resolution) and the cells it keeps (*keep*:
        ``[(cell, published rect, edges)]``, one per held reader): every texel of a
        kept cell is held -- read by the seam stitch, never written
        (``ptk.ImgUtils.stitch_seams`` ``hold``), so a placed cell meets a kept one
        the whole way and the kept one stays byte-identical -- and the gutters are
        refilled from the union of the placed and kept cells, everything outside it
        zeroed first, as a fresh pack fills them. *intensity* scales each placed tile
        (the atlas around it already carries its own).

        Returns ``[(rect, bounds), ...]`` per placement: the crop-folded rect to publish
        (through :meth:`_aimed_rect`) and the uv range mapped onto the full cell (see
        ``crop_to_uv_bbox``)."""
        import numpy as np

        res = self.resolution
        pix_rects = ptk.ImgUtils.atlas_pixel_rects([so for _, so, _ in placements], res)
        atlas = np.zeros((res, res, 4), dtype=np.float32)
        atlas[..., 3] = 1.0
        if base is not None:
            base = np.asarray(base, dtype=np.float32)
            atlas[..., :3] = base[..., :3] if base.ndim == 3 else base[..., None]
        mask = np.zeros((res, res), dtype=bool)
        keep = list(keep or ())
        held = (
            self._cells_mask([cell for cell, _rect, _edges in keep]) if keep else None
        )
        published = []

        for (src, cell, bbox), (row0, row1, col0, col1) in zip(placements, pix_rects):
            w = max(1, col1 - col0)
            h = max(1, row1 - row0)
            tile, rect, bounds = ptk.ImgUtils.crop_to_uv_bbox(
                self._read_exr_top_down(src), bbox, cell, self._CROP_MAX_COVERAGE
            )
            published.append((rect, bounds))
            # Edges on border-texel centers: the mapping the rect is published
            # with (:meth:`_aimed_rect`). Scaled
            # edge to edge, every edge sample read the tile half a texel in --
            # a step on every 3D edge two cells share (twin of mayatk
            # _finish_tile).
            rgb, _covered = ptk.ImgUtils.resize_into_cell(
                tile[..., :3], (w, h), edge_centers=True
            )
            r0, r1 = max(row0, 0), min(row1, res)
            c0, c1 = max(col0, 0), min(col1, res)
            tile_rgb = rgb[: r1 - r0, : c1 - c0, :]
            # Rendered-dead rescue (see _DEAD_TEXEL_*): texels the bake
            # RENDERED but that carry ~no radiance are occluded geometry, not
            # signal -- refilled here from the tile's OWN lit texels (the rule
            # _heal_dead_texels applies to a whole map) instead of shipping hard
            # black borders, and before the seam stitch below reads them: a dead
            # border texel stitched as it was would pull its lit neighbour across
            # the seam halfway to black (mayatk heals each map before its pack).
            # ``None`` == an all-dark tile, kept as rendered (a black bake is a
            # faithful render of an unlit scene; the panel guard warns).
            tile_signal = self._signal_mask(tile_rgb)
            if tile_signal is not None and not tile_signal.all():
                tile_rgb = ptk.ImgUtils.fill_empty_texels(tile_rgb, mask=tile_signal)
            if float(intensity) != 1.0:
                tile_rgb = tile_rgb * float(intensity)
            atlas[r0:r1, c0:c1, :3] = tile_rgb
            atlas[r0:r1, c0:c1, 3] = 1.0
            mask[r0:r1, c0:c1] = True

        # Members that meet at a 3D edge in one plane -- a wall's grid of
        # panels, a floor's tiles, linked duplicates of one -- are separate
        # cells with their own sampling noise, so each edge they share read as
        # a step however well each cell was finished. Stitched: both read one
        # value there, through the rects as they are published (twin of
        # mayatk's _pack_group; the matching is ptk's, shared with it).
        # Kept cells join as ONE member in atlas space: their seams with each
        # other were stitched when they were packed, so only a seam with a
        # placed cell is paired (twin of mayatk's _seam_pairs ``hold``).
        if edges is not None:
            member_edges = list(edges)
            rects = [self._aimed_rect(rect, bounds) for rect, bounds in published]
            if keep:
                kept = [
                    self._edges_in_atlas(e, rect)
                    for _cell, rect, e in keep
                    if e is not None
                ]
                member_edges = [
                    self._edges_in_atlas(e, rect)
                    for e, rect in zip(member_edges, rects)
                    if e is not None
                ] + ([np.concatenate(kept)] if kept else [])
                rects = [list(self._IDENTITY_SCALE_OFFSET)] * len(member_edges)
            pairs = ptk.ImgUtils.atlas_seam_pairs(
                member_edges,
                rects,
                res,
                tolerance=self._SEAM_TOLERANCE,
                coplanar=self._SEAM_COPLANAR,
            )
            if len(pairs):
                atlas[..., :3] = ptk.ImgUtils.stitch_seams(
                    atlas[..., :3], pairs, hold=held
                )
        if held is not None:
            mask = mask | held
        if base is not None:
            atlas[~mask, :3] = 0.0  # the gutters as a fresh pack fills them

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
        atlas[..., :3] = ptk.ImgUtils.fill_empty_texels(
            rgb, mask=mask | (rgb > 0).any(axis=-1)
        )
        self._write_lightmap_exr(atlas_path, np.flipud(atlas))  # back to bottom-up
        return published

    @staticmethod
    def _read_exr_top_down(path):
        """*path*'s pixels as a float ``(H, W, C)`` array, row 0 at the TOP."""
        import bpy
        import numpy as np

        img = bpy.data.images.load(path)
        try:
            buf = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(buf)
            # bpy pixels are bottom-up; atlas rows (and crop rows) are top-down
            return np.flipud(buf.reshape(img.size[1], img.size[0], img.channels))
        finally:
            bpy.data.images.remove(img)

    def _aimed_rect(self, rect, bounds) -> List[float]:
        """*rect* as published: its *bounds* aimed at border-texel CENTERS.

        A rect edge published on a texel boundary splits every engine tap along
        a shared 3D edge onto the neighbouring cell's gutter, up to half its
        weight on another object's lighting (twin of mayatk ``_pack_group``).
        Aimed at the BOUNDS the crop mapped onto the cell, never the island bbox,
        whose sub-texel overhang past the crop would aim outside the cell. The
        one aim both the published rect and the seam stitch read through.
        """
        return list(
            ptk.ImgUtils.inset_rects_to_texel_centers(
                [rect], self.resolution, bboxes=[bounds]
            )[0]
        )

    #: Seam matching (``ptk.ImgUtils.atlas_seam_pairs``; mirror of mayatk's): two
    #: open edges are one 3D edge when both of one's endpoints lie this close to
    #: the other's line -- as a fraction of the atlas group's extent, never more
    #: than a thousandth of its median edge -- so float noise in a duplicate's
    #: transform passes and a baseboard ~1 cm in front of a wall's edge does not.
    #: Their faces must also face the same way within :attr:`_SEAM_COPLANAR` (a
    #: wall meeting a floor shares an edge but not its light) and lie on
    #: OPPOSITE sides of the edge (a decal along a panel's border is not its
    #: neighbour).
    _SEAM_TOLERANCE: float = 1e-4
    _SEAM_COPLANAR: float = 0.99

    @classmethod
    def _boundary_edges(cls, obj, region=None):
        """*obj*'s open mesh edges in world space with their lightmap UVs, or ``None``.

        ``(E, 16)`` rows ``p0(3) p1(3) uv0(2) uv1(2) normal(3) inward(3)`` -- what
        ``ptk.ImgUtils.atlas_seam_pairs`` matches; *inward* points from the edge into
        its face, in the face's plane. Placed by the object's own ``matrix_world``, so
        each linked duplicate stands where it stands; the UVs are the layer the bake
        renders through (:meth:`_lightmap_uv_bbox`'s), in *region*'s frame when the map
        covers only that part of the layout (:meth:`_in_region`). Numpy over the mesh
        arrays: the open edges are the ones a single face loop uses, each face's normal
        is its own (Newell's). Twin of mayatk's ``_read_boundary_edges``. ``None`` -- no
        mesh, layer or open edge, or any read failure -- leaves the object unstitched:
        a bake is never lost to a seam.
        """
        import bpy
        import numpy as np

        if isinstance(obj, str):
            obj = bpy.data.objects.get(obj)
        me = getattr(obj, "data", None)
        layers = getattr(me, "uv_layers", None)
        if not layers or not len(getattr(me, "polygons", ())):
            return None
        layer = layers.get(UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET)
        if layer is None:
            return None
        try:
            n_faces, n_loops = len(me.polygons), len(me.loops)
            co = np.empty(len(me.vertices) * 3, dtype=np.float32)
            me.vertices.foreach_get("co", co)
            m = np.array(obj.matrix_world, dtype=np.float64)
            points = co.astype(np.float64).reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
            vert = np.empty(n_loops, dtype=np.int64)
            me.loops.foreach_get("vertex_index", vert)
            edge = np.empty(n_loops, dtype=np.int64)
            me.loops.foreach_get("edge_index", edge)
            uv = np.empty(n_loops * 2, dtype=np.float32)
            layer.data.foreach_get("uv", uv)
            uv = uv.astype(np.float64).reshape(-1, 2)
            start = np.empty(n_faces, dtype=np.int64)
            me.polygons.foreach_get("loop_start", start)
            total = np.empty(n_faces, dtype=np.int64)
            me.polygons.foreach_get("loop_total", total)
            # Each loop's face (a face's loops are one contiguous run), and the
            # loop after it around that face; a loop's edge runs to that one.
            order = np.argsort(start, kind="stable")
            face = np.repeat(order, total[order])
            nxt = start[face] + (np.arange(n_loops) - start[face] + 1) % total[face]
            pa, pb = points[vert], points[vert[nxt]]
            cross = np.cross(pa, pb)
            normal = np.stack(
                [np.bincount(face, cross[:, k], minlength=n_faces) for k in range(3)],
                axis=1,
            )
            centre = (
                np.stack(
                    [np.bincount(face, pa[:, k], minlength=n_faces) for k in range(3)],
                    axis=1,
                )
                / np.maximum(total, 1)[:, None]
            )
            is_open = (np.bincount(edge, minlength=len(me.edges))[edge] == 1) & (
                np.linalg.norm(normal, axis=1)[face] > 0  # degenerate: faces no way
            )
            if not is_open.any():
                return None
            pa, pb, owner = pa[is_open], pb[is_open], face[is_open]
            along = (pb - pa) / np.maximum(
                np.linalg.norm(pb - pa, axis=1, keepdims=True), 1e-12
            )
            to_centre = centre[owner] - pa
            inward = to_centre - (to_centre * along).sum(axis=1, keepdims=True) * along
            uv0, uv1 = uv[is_open], uv[nxt[is_open]]
            if region is not None:
                sx, sy, ox, oy = cls._to_region_rect(region)
                uv0 = uv0 * (sx, sy) + (ox, oy)
                uv1 = uv1 * (sx, sy) + (ox, oy)
            return np.hstack([pa, pb, uv0, uv1, normal[owner], inward])
        except Exception:
            return None

    @staticmethod
    def _primary_material(obj) -> Optional[str]:
        """Name of the material covering the most faces of *obj* (its group key); ``None`` if
        nothing is assigned. A single-material object wins outright. The rule is
        :meth:`LightmapRecords._primary_material`, which the bake baseline records as each
        object's group (mirror of mayatk's)."""
        return LightmapRecords._primary_material(obj)

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

        ``ROOM_ENV_Base_color.png`` -> ``ROOM_ENV``, so the lightmap lands in
        sourceimages beside the maps it belongs to rather than under the material's own
        name (``MAT_ROOM_ENV_Lightmap.exr`` next to ``ROOM_ENV_Base_color.png`` reads
        as a stray from a different set). The material name is an authoring detail; the
        texture set is what the rest of the maps are keyed on.

        The vote is :meth:`ptk.MapFactory.dominant_texture_set`, the rule mayatk's twin
        names its maps by too: only a real material MAP votes (a noise texture, an
        environment cube or a lookup carries no map-type token), and the most common
        set wins, a tie going to the name that sorts first -- so one stray map cannot
        rename the whole set.
        """
        import bpy

        mat = bpy.data.materials.get(material_name or "")
        tree = getattr(mat, "node_tree", None) if mat is not None else None
        if tree is None:
            return None
        found = ptk.MapFactory.dominant_texture_set(
            TextureBaker.image_sources(
                getattr(node, "image", None) for node in tree.nodes
            )
        )
        return found[0] if found else None

    @staticmethod
    def _unique_atlas_path(
        output_dir, name, used, avoid=frozenset(), claims=None, owners=()
    ) -> str:
        """Atlas path for *name*, unique within one pack (``used``), clear of any other
        group's not-yet-consumed source maps (*avoid*, a set of abspaths), and of any
        file name an object outside *owners* still reads (*claims*,
        :meth:`LightmapRecords.claims`). Overwriting the group's OWN prior atlas is
        allowed (that's the point of consolidation). The rule is
        :meth:`ptk.FileUtils.unique_path` -- mayatk's twin calls it too, so a fix to
        one can no longer miss the other (it did: this copy never learned the claims)."""
        return ptk.FileUtils.unique_path(
            output_dir, name, ".exr", used, claims=claims, owners=owners, avoid=avoid
        )

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
                px = buf.reshape(img.size[1], img.size[0], img.channels)
                px[..., : min(3, img.channels)] *= float(intensity)
                self._write_lightmap_exr(path, px)
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
    # The record -- LightmapRecords, reached through the baker
    # ------------------------------------------------------------------
    #
    # Mirror of mayatk: what a bake leaves in the file is LightmapRecords'. The
    # workflow verbs a baker is asked for stay here as delegates; the
    # dependency and manifest calls moved there outright and warn here until
    # 0.11.0 -- they never needed a baker.

    @ptk.Deprecation.parameter(
        "uv_rects",
        remove_in="0.11.0",
        since="2026-09-23",
        reason="Only an old atlas pack squeezed UVs into a rect, and "
        "LightmapRecords.migrate_legacy now restores those losslessly.",
    )
    @ptk.Deprecation.parameter(
        "intensity",
        remove_in="0.11.0",
        since="2026-09-23",
        reason="Pass intensity to bake(), which scales the maps it has just "
        "written exactly once; committing a map twice here scaled it twice.",
    )
    def commit_lightmap(
        self,
        mapping: Dict[str, str],
        intensity: float = 1.0,
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        uv_rects: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, str]:
        """Record maps baked elsewhere: :meth:`LightmapRecords.commit`.

        :meth:`bake` records its own maps; this is for a caller that ran
        :meth:`bake_separated` / :meth:`bake_atlas` itself. *mapping* is
        ``{object_name: lightmap_path}`` and *scale_offsets* each object's atlas
        rect (see :meth:`pack_atlas`). Mirror of mayatk's.

        Two parameters are deprecated (removed in 0.11.0) and keep their old
        behaviour until then. ``intensity`` other than 1.0 is scaled into the
        texels -- each unique file once per call, so committing a map again
        scales it again; :meth:`bake`'s ``intensity`` applies it where the map
        is written. ``uv_rects`` records a remap an old pack had already
        squeezed INTO the UVs (the marker's ``uvRect``).
        """
        recorded = LightmapRecords.commit(
            mapping, scale_offsets=scale_offsets, intensity=intensity
        )
        for name, rect in (uv_rects or {}).items():
            if name in recorded:
                LightmapRecords._stamp_uv_rect(name, rect)
        if recorded and float(intensity) != 1.0:
            self._apply_intensity(recorded.values(), intensity)
        return recorded

    def revert(self, objects=None) -> List[str]:
        """Take the lightmaps off *objects*, or off every baked object for ``None``.

        :meth:`LightmapRecords.revert`: the markers go (a legacy UV remap is
        restored first) and the manifest is republished. The materials were
        never changed and the EXR files stay on disk. Returns the names cleared.
        """
        return LightmapRecords.revert(objects)

    def revert_lightmap(self, objects=None) -> List[str]:
        """:meth:`revert`, under its original name."""
        return LightmapRecords.revert(objects)

    def baked_objects(self, objects=None) -> List[str]:
        """The objects :meth:`revert` would take the lightmap from (names).

        :meth:`LightmapRecords.baked_objects` (mirror of mayatk's).
        """
        return LightmapRecords.baked_objects(objects)

    @ptk.Deprecation.symbol(
        "LightmapRecords.lightmap_dependencies", remove_in="0.11.0", since="2026-09-23"
    )
    def lightmap_dependencies(
        self, objects=None, search_dirs=None, walk: bool = True
    ) -> List[Dict[str, Any]]:
        """Moved to :meth:`LightmapRecords.lightmap_dependencies`."""
        return LightmapRecords.lightmap_dependencies(objects, search_dirs, walk)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.search_dirs", remove_in="0.11.0", since="2026-09-23"
    )
    def search_dirs(cls, objects=None) -> List[str]:
        """Moved to :meth:`LightmapRecords.search_dirs`."""
        return LightmapRecords.search_dirs(objects)

    @ptk.Deprecation.symbol(
        "LightmapRecords.heal_lightmap_paths", remove_in="0.11.0", since="2026-09-23"
    )
    def heal_lightmap_paths(self, objects=None) -> Dict[str, Any]:
        """Moved to :meth:`LightmapRecords.heal_lightmap_paths`."""
        return LightmapRecords.heal_lightmap_paths(objects)

    @ptk.Deprecation.symbol(
        "LightmapRecords.relocate_lightmaps", remove_in="0.11.0", since="2026-09-23"
    )
    def relocate_lightmaps(
        self,
        dest_dir: str,
        source_dir: str = "",
        mode: str = "copy",
        objects=None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Moved to :meth:`LightmapRecords.relocate_lightmaps`."""
        return LightmapRecords.relocate_lightmaps(
            dest_dir, source_dir, mode, objects, dry_run
        )

    @ptk.Deprecation.symbol(
        "LightmapRecords.repath_lightmaps", remove_in="0.11.0", since="2026-09-23"
    )
    def repath_lightmaps(
        self, dirs_by_map: Dict[str, str], objects=None, relative: bool = True
    ) -> int:
        """Moved to :meth:`LightmapRecords.repath_lightmaps`."""
        return LightmapRecords.repath_lightmaps(dirs_by_map, objects, relative)

    @ptk.Deprecation.symbol(
        "LightmapRecords.normalize_lightmap_paths",
        remove_in="0.11.0",
        since="2026-09-23",
    )
    def normalize_lightmap_paths(self, objects=None, relative: bool = True) -> int:
        """Moved to :meth:`LightmapRecords.normalize_lightmap_paths`."""
        return LightmapRecords.normalize_lightmap_paths(objects, relative)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.export_record", remove_in="0.11.0", since="2026-09-23"
    )
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """Moved to :meth:`LightmapRecords.export_record`."""
        return LightmapRecords.export_record(ctx)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.refresh_export_metadata",
        remove_in="0.11.0",
        since="2026-09-23",
    )
    def refresh_export_metadata(cls) -> Optional[str]:
        """Moved to :meth:`LightmapRecords.refresh_export_metadata`."""
        return LightmapRecords.refresh_export_metadata()

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

    @staticmethod
    @contextlib.contextmanager
    def _white_card():
        """A white Lambert (Kd 1, no bump) each target is baked through; removed after.

        mayatk's card, for the same answer: lighting-only irradiance off the
        GEOMETRY. Baked through its own material instead -- the pass colour
        divided out -- a target's map took its normal map (floor grout and
        ceiling-tile patterns aliased into a shared atlas) and a metal baked
        near black (no diffuse lobe to divide by): measured on the production
        office (2026-10-01), the lockers 0.25-0.41x and the whole room 0.78x of
        the Arnold bake, while a calibration room on one lambert agreed with
        Arnold and the analytic value to 2%.
        """
        import bpy

        from blendertk.mat_utils._mat_utils import MatUtils

        card = bpy.data.materials.new("lm_whitecard")
        tree = MatUtils.get_node_tree(card, create=True)
        tree.nodes.clear()
        bsdf = tree.nodes.new("ShaderNodeBsdfDiffuse")
        bsdf.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.0  # Lambert, as Arnold's card
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        try:
            yield card
        finally:
            bpy.data.materials.remove(card)

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

    # ------------------------------------------------------------------
    # Which lights light the bake (Include Scene Lighting / Hidden Lights)
    # ------------------------------------------------------------------

    @staticmethod
    def _handed(objects):
        """*objects*, or the selection when ``None``: what a bake was handed,
        read once (its lights are the Scope's own -- :meth:`_scope_lights`)."""
        if objects is not None:
            return objects
        from blendertk.core_utils._core_utils import CoreUtils

        return CoreUtils.selected_objects()

    def _bake_lights(self, scope=None) -> _BakeLights:
        """Which of the file's lights a bake of *scope* renders with (mirror of mayatk's).

        One answer for the bake, :meth:`preflight`'s refusal and
        :meth:`_light_audit`: every light object with
        :attr:`include_scene_lights`, else the Scope's own
        (:meth:`_scope_lights`; *scope* ``None`` reads the selection; inside
        :meth:`_lit_as`, the recorded rule's, by name) -- the rest *outside*,
        hidden from the render for the bake; and, with
        :attr:`include_hidden_lights`, those of them hidden from the render as
        authored -- their own flag or a collection's -- at a non-zero power,
        which the bake switches on (:meth:`_switched_on`). The world is not a
        light object here: :attr:`include_environment` alone decides it.
        """
        import bpy

        plan = _BakeLights()
        scene = bpy.context.scene
        if scene is None:
            return plan
        held = (getattr(self, "_rule", None) or {}).get("scope")
        if self.include_scene_lights:
            inside = None
        elif held is not None:
            # A re-bake lit as recorded (:meth:`_lit_as`): the Scope's lights
            # by name -- one renamed or deleted since is no longer among them.
            inside = set(held)
        else:
            inside = {obj.name for obj in self._scope_lights(scope)}
        for obj in scene.objects:
            if obj.type != "LIGHT":
                continue
            if inside is None or obj.name in inside:
                plan.lights.append(obj.name)
            else:
                plan.outside.append(obj.name)
        if self.include_hidden_lights:
            rendered = self._rendered_collections()
            plan.hidden = [
                name
                for name in plan.lights
                if not self._renders(bpy.data.objects[name], rendered)
                and self._power(bpy.data.objects[name]) > 0.0
            ]
        return plan

    @staticmethod
    def _scope_lights(objects=None) -> List[Any]:
        """The light objects in *objects* or under them: the Scope's own lights.

        What a bake renders with Include Scene Lighting off, read off what the
        bake was HANDED rather than its meshes: ``TextureBaker.resolve_meshes``
        keeps surfaces only. A collection stands for its objects
        (:meth:`NodeUtils.expand_sets`) and every object for everything under
        it. ``None`` reads the selection. Mirror of mayatk's.
        """
        import bpy

        from blendertk.node_utils._node_utils import NodeUtils

        if objects is None:
            from blendertk.core_utils._core_utils import CoreUtils

            objects = CoreUtils.selected_objects()
        given = [
            bpy.data.objects.get(o) if isinstance(o, str) else o
            for o in NodeUtils.expand_sets(objects or [])
        ]
        found: Dict[str, Any] = {}
        for obj in given:
            if obj is None or not hasattr(obj, "children_recursive"):
                continue
            for each in [obj, *obj.children_recursive]:
                if each.type == "LIGHT":
                    found.setdefault(each.name, each)
        return list(found.values())

    @contextlib.contextmanager
    def _lighting(self, scope=None):
        """Light the file for a bake of *scope* as :meth:`_bake_lights` says;
        hand it back as found after, however the bake ends (mirror of mayatk's).

        The world detached while :attr:`include_environment` is off
        (:meth:`_muted_environment`), the lights outside the Scope hidden from
        the render while :attr:`include_scene_lights` is (their own
        ``hide_render``: per object, so nothing else moves), and the hidden
        ones switched on while :attr:`include_hidden_lights` is
        (:meth:`_switched_on`). Re-entrant: the outermost entry decides, so
        :meth:`bake` sets it from what it was HANDED -- lights and Empties
        included -- and the mechanisms below it, which see meshes alone, keep
        that. Yields the :class:`_BakeLights` in force.
        """
        if getattr(self, "_lit", None) is not None:
            yield self._lit
            return
        plan = self._bake_lights(scope)
        self._lit = self._last_lit = plan
        try:
            with (
                self._muted_environment(),
                self._out_of_the_render(plan.outside),
                self._switched_on(plan.hidden),
            ):
                yield plan
        finally:
            self._lit = None

    @contextlib.contextmanager
    def _out_of_the_render(self, names: List[str]):
        """Hide the lights *names* from the render for the duration; hand them back.

        Include Scene Lighting off: the lights outside the Scope, by their own
        ``hide_render`` (what the bake's render depsgraph obeys). One the file
        already hides is left alone; a library-linked one refuses the write and
        is named instead of failing the bake.
        """
        with self._switched_off(names, "light") as off:
            if off:
                self.logger.info(
                    "Include Scene Lighting is off: %d light(s) outside the Scope "
                    "hidden from the render for this bake: %s",
                    len(off),
                    ", ".join(off[:8]) + (" ..." if len(off) > 8 else ""),
                )
            yield off

    @contextlib.contextmanager
    def _switched_off(self, names, what: str):
        """Hide the objects *names* from the render for the duration; yield those it took.

        Each by its own ``hide_render`` -- what the render depsgraph obeys,
        per object, so a linked duplicate or a child stays as it is. One the
        file already hides is left alone; a library-linked one refuses the
        write and is named as *what* instead of failing the bake. Handed back
        as found, however the body ends. Mirror of mayatk's.
        """
        import bpy

        off: List[str] = []
        for name in dict.fromkeys(names or ()):
            obj = bpy.data.objects.get(name)
            if obj is None or obj.hide_render:
                continue
            try:
                obj.hide_render = True
            except Exception as e:  # a library-linked object refuses the write
                self.logger.warning("Could not switch off %s %s: %s", what, name, e)
                continue
            off.append(name)
        try:
            yield off
        finally:
            for name in off:
                obj = bpy.data.objects.get(name)
                try:
                    obj.hide_render = False
                except Exception as e:  # never leave the scene changed silently
                    self.logger.error("Could not restore %s: %s", name, e)

    @contextlib.contextmanager
    def _switched_on(self, names: List[str]):
        """Switch the hidden lights *names* on for the duration -- and nothing else with them.

        :meth:`CoreUtils.visible_override`: each light's own flags cleared, and
        the light linked into the scene's master collection, which reveals it
        and nothing else its collections hide (measured -- clearing a
        collection's flags reveals every other member). Handed back as found,
        however the body ends. A library-linked light refuses the edit: named
        and left off, never a failed bake. Yields the lights it switched on.
        """
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        lights = []
        for name in names or []:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            if obj.library is not None:
                self.logger.warning(
                    "Include Hidden Lights: could not switch on %s -- it is linked "
                    "from a library. It stays off for this bake.",
                    name,
                )
                continue
            lights.append(obj)
        if not lights:
            yield []
            return
        with CoreUtils.visible_override(lights):
            self.logger.info(
                "Include Hidden Lights: %d hidden light(s) switched on for this "
                "bake: %s",
                len(lights),
                ", ".join(o.name for o in lights[:8])
                + (" ..." if len(lights) > 8 else ""),
            )
            yield [o.name for o in lights]

    def _isolation_passes(
        self, targets
    ) -> Tuple[List[Tuple[List[Any], List[Any]]], set]:
        """``(passes, isolated)``: *targets* as render passes, and every isolated mesh.

        Each pass is ``(bake, keep)``: the targets it bakes, and the isolated
        meshes it keeps in its render beside them. The targets no
        :class:`LightmapIsolateSet` member covers go together, then those of
        each member alone, with every mesh of that member in the render
        (:meth:`LightmapIsolateSet.groups` -- a door with the glass under it,
        baked or not). One pass, *targets* as given, when none of them is
        isolated. *isolated* holds the names of the set's meshes, targets or
        not: :meth:`_out_of_render` takes each pass's others out. Mirror of
        mayatk's.
        """
        groups = LightmapIsolateSet.groups()
        isolated = {obj.name for group in groups for obj in group}
        alone = []
        for group in groups:
            names = {obj.name for obj in group}
            bake = [t for t in targets if t.name in names]
            if bake:
                alone.append((bake, group))
        # By name: a collection's member order is its link order, and a log
        # should repeat.
        alone.sort(key=lambda pair: [obj.name for obj in pair[0]])
        if not alone:
            return [(list(targets), [])], isolated
        rest = [t for t in targets if t.name not in isolated]
        self.logger.info(
            "%d isolated object(s) (%s) bake in passes of their own, out of "
            "every other object's render: %s",
            len(alone),
            LightmapIsolateSet.SET_NAME,
            ", ".join(bake[0].name for bake, _ in alone[:8])
            + (" ..." if len(alone) > 8 else ""),
        )
        return ([(rest, [])] if rest else []) + alone, isolated

    @staticmethod
    def _claimed_by(claims, outputs: Dict[str, str]):
        """*claims* plus each map *outputs* (``{object: path}``) wrote, read by its object.

        What a later pass of one bake (:meth:`_isolation_passes`) hands
        ``ptk.FileUtils.unique_path``: its names stay clear of the earlier
        passes' maps as one batch's stay clear of each other. *claims* is a
        ``{file name: readers}`` map or ``None``; returned as given when there
        is nothing to add.
        """
        if not outputs:
            return claims
        return ptk.FileDependencies.claims(
            [
                (reader, name)
                for name, readers in (claims or {}).items()
                for reader in readers
            ]
            + list(outputs.items())
        )

    @contextlib.contextmanager
    def _out_of_render(self, targets, isolated: set = frozenset(), keep=()):
        """Take what moves out of the render of a bake of *targets*.

        A bake renders what it bakes, what was baked before
        (:meth:`LightmapRecords.baked_objects`) and the file's
        :class:`LightmapExcludeSet` -- the static scene. Any other mesh moves
        (an interactive prop, never baked), and a shadow or bounce it cast
        would stay in the maps wherever it went: props on a production cart
        shadowed the cart's map (2026-10-07). So does every *isolated* mesh
        (:class:`LightmapIsolateSet`, by name) but the pass's own member's
        (*keep*: a door and the glass under it), a target of this bake or
        baked before alike: a locker door's shadow on its cabinet. Each is
        switched off by its ``hide_render`` for the bake -- per object, so a
        linked duplicate of a mesh that stays, or a child under a mover, is
        untouched -- and handed back as found. Yields the names taken out.
        Mirror of mayatk's.
        """
        import bpy

        kept = {obj.name for obj in keep}
        others = set(isolated) - kept
        stay = (
            {obj.name for obj in targets}
            | kept
            | set(LightmapRecords.baked_objects())
            | {obj.name for obj in LightmapExcludeSet.meshes()}
        ) - others
        movers = [
            obj.name
            for obj in bpy.context.scene.objects
            if obj.type == "MESH" and obj.name not in stay
        ]
        with self._switched_off(movers, "unbaked mesh") as off:
            # Named by what moves: an isolated one was named by its pass.
            moved = [name for name in off if name not in others]
            if moved:
                self.logger.info(
                    "%d unbaked mesh(es) are out of this bake's render: neither "
                    "baked now, baked before, nor in the Exclude set (%s), they "
                    "move, so they cast no shadow into the maps. Add a static one "
                    "to the Exclude set to keep its shadow: %s",
                    len(moved),
                    LightmapExcludeSet.SET_NAME,
                    ", ".join(moved[:8]) + (" ..." if len(moved) > 8 else ""),
                )
            yield off

    def _warn_if_unlit_scene(self) -> None:
        """Warn (once per instance) when the scene has no light source to bake.

        A lightless bake silently produces a black lightmap -- worth a loud hint BEFORE the
        rays are spent rather than only after (:meth:`bake_verdict` reads the finished
        maps; this fires for every caller of the bake mechanisms, which is why it lives on
        the workflow rather than the Slots). Twin of mayatk's guard, with the Arnold
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
            # The lights this bake renders with (:meth:`_bake_lights`, the set
            # the refusal reads): a light outside the Scope with Include Scene
            # Lighting off is no light source for it.
            plan = getattr(self, "_lit", None) or self._bake_lights()
            if plan.lights:
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
            if self.include_scene_lights
            else "No lights in the Scope, and Include Scene Lighting is off: the "
            "file's other lights are hidden from the render for the bake -- the "
            "lightmap will bake black (unless emissive materials light it)."
        )
