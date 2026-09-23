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

The ``.ui`` shares mayatk's objectNames for the controls both panels have
(``cmb_scope``, ``cmb002``, ``cmb000``, ``cmb_resolution``, ``spn_samples``,
``txt_output_dir``, ``txt000``, ``b000``); mayatk's has since grown rows this one has not
(ledgered ``pending`` in ``tentacle/docs/parity_map.py``). Now that uitk host-namespaces
the QSettings branch per DCC (``Switchboard.add_ui`` / ``MainWindow._relative_state`` via
``context_tags``), identical objectNames across the two copies of the same panel no longer
collide in the shared "uitk"/"shared" registry root, so there is no need to renumber
widgets to dodge it. Both ``cmb002`` packing
modes are live: "Per-Object" (one full-resolution map each) and "Atlas by Material" (per-material
consolidation via :meth:`LightmapBaker.pack_atlas`, the Blender port of mayatk's atlas packer).
"""

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.light_utils._light_utils import LightUtils
from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
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
        unbaked: Objects the bake was asked for and produced nothing for (a
            cancel, a failed render). They keep any map they already had.
        refused: Why nothing was baked, as a sentence for the artist, or
            ``None``.
        verdict: A warning about the finished maps' level (an unlit or a
            blown-out bake), as a sentence, or ``None``.
    """

    maps: Dict[str, str] = field(default_factory=dict)
    rects: Dict[str, List[float]] = field(default_factory=dict)
    excluded: List[str] = field(default_factory=list)
    unbaked: List[str] = field(default_factory=list)
    refused: Optional[str] = None
    verdict: Optional[str] = None

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


class LightmapBaker(ptk.LoggingMixin):
    """Orchestrate the Blender lightmap workflow: UV2 -> Cycles bake -> engine export prep.

    Usage::

        baker = LightmapBaker.from_preset("quest")        # or (resolution=, samples=)
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

        1. The mesh objects in *objects* (default: the selection).
        2. :meth:`preflight` -- a refusal when the scene has lights and none of
           them can light the bake.
        3. The bake: :meth:`bake_atlas` (``packing="atlas"``, one shared map
           per material) or :meth:`bake_separated` (``"per_object"``). Both
           migrate the targets' legacy markers first
           (:meth:`LightmapRecords.migrate_legacy`).
        4. *intensity*, when not 1.0, scaled into the maps this bake just
           wrote -- once, so re-recording them can never apply it twice.
        5. :meth:`LightmapRecords.commit` records each map with its rect.
        6. :meth:`bake_verdict` reads the finished maps' level.

        Nothing is reverted first. An object the bake does not finish keeps the
        map it had, and that map is intact: a bake never writes a file another
        object reads (:meth:`LightmapRecords.claims`).

        Parameters:
            objects: Mesh objects; ``None`` for the selection. Anything without
                a mesh of its own (an Empty, a light) bakes nothing.
            packing: ``"atlas"`` or ``"per_object"``.
            output_dir: Where the maps go (see :meth:`bake_separated`).
            prefix / suffix: Name affix around each map's texture-set stem.
            on_progress: ``(done, total, name) -> bool`` per object; return
                ``False`` to cancel the rest.
            intensity: A multiplier baked into the texels; recorded in the
                markers, informationally.
            kwargs: Forwarded to the bake mechanism.

        Returns:
            :class:`LightmapBakeResult`: the maps and rects, the
            unbaked objects, and the ``refused`` / ``verdict`` sentences for
            the artist. (Blender has no exclusion set yet, so ``excluded`` is
            always empty.)

        Raises:
            ValueError: *packing* is neither ``"atlas"`` nor ``"per_object"``.
        """
        if packing not in ("atlas", "per_object"):
            raise ValueError(
                f"packing must be 'atlas' or 'per_object', got {packing!r}"
            )
        result = LightmapBakeResult()
        targets = [obj.name for obj in TextureBaker.resolve_meshes(objects)]
        if not targets:
            result.refused = "Nothing to bake: no mesh among the given objects."
            return result
        result.refused = self.preflight()
        if result.refused:
            return result

        common = dict(
            output_dir=output_dir,
            prefix=prefix,
            suffix=suffix,
            on_progress=on_progress,
            **kwargs,
        )
        if packing == "atlas":
            packed = self.bake_atlas(targets, **common)
        else:
            packed = {
                n: (path, None)
                for n, path in self.bake_separated(targets, **common).items()
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
        if float(intensity) != 1.0:
            self._apply_intensity(result.maps.values(), intensity)
        LightmapRecords.commit(
            result.maps, scale_offsets=result.rects, intensity=intensity
        )
        result.verdict = self.bake_verdict(result.maps.values())
        return result

    def preflight(self) -> Optional[str]:
        """Why this file cannot bake now, or ``None`` (mirror of mayatk's).

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
        """
        try:
            import bpy

            scene = bpy.context.scene
            lights = [o for o in scene.objects if o.type == "LIGHT"] if scene else []
            world_lights = self.include_environment and LightUtils.world_emits(
                getattr(scene, "world", None)
            )
            rendered = self._render_collections(bpy.context.view_layer)
        except Exception:  # no runtime / unreadable scene -- nothing to refuse
            return None
        lit = [
            o
            for o in lights
            if not o.hide_render
            and any(c.name in rendered for c in o.users_collection)
            and float(getattr(o.data, "energy", 0.0) or 0.0) > 0.0
        ]
        if lights and not lit and not world_lights:
            self.logger.warning(
                "Bake refused: all %d light(s) in the scene are hidden from the "
                "render or at zero power, so Cycles would bake no direct light "
                "and the maps would come back essentially black.\n"
                "Scene lights:\n%s",
                len(lights),
                self._light_audit(),
            )
            return (
                f"Bake skipped: all {len(lights)} scene light(s) are hidden from "
                "the render or at zero power (see the console)."
            )
        return None

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
                self._light_audit(),
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
                self._light_audit(),
            )
            return "bake is BLOWN OUT — check light power (see the console)."
        return None

    @staticmethod
    def _light_audit() -> str:
        """One line per scene light: the attrs that decide whether a bake is lit.

        Attached to the level verdict and the lights-off refusal so a dark
        result carries its own diagnosis -- power, scale, render visibility and
        the world strength are exactly the dials a black bake traces back to,
        and none of them are visible in the bake output itself. Twin of
        mayatk's ``_light_audit`` (Arnold's intensity/exposure/normalize ->
        Cycles' watts).

        Total-failure tolerant: it is evaluated as an argument to a warning,
        so a raise here would propagate out of a finished bake -- the one
        thing the checks promise never to do.
        """
        try:
            import bpy

            scene = bpy.context.scene
            if scene is None:
                return "  <no scene>"
            rows = LightmapBaker._light_rows(scene)
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

    # ------------------------------------------------------------------
    # Bake mechanisms
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

        No map takes a file name another object reads
        (:meth:`LightmapRecords.claims`); an object's own map keeps its name.
        """
        if "claims" not in kwargs:
            kwargs["claims"] = LightmapRecords.claims()
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
        claims: Optional[Dict[str, Any]] = None,
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
                claims=claims,
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
        texture folder with files the caller has no use for.

        Extra ``kwargs`` are forwarded to :meth:`bake_separated`. *prefix* / *suffix* name
        both the tiles and the atlas (the ``lightmap_irr_`` prefix a loose
        ``bake_separated`` call defaults to is pointless here — the tiles never
        leave the work dir). *claims* are the file names other objects read
        (:meth:`LightmapRecords.claims`, the default) that the finished maps must
        not take; they name the deliverables, never the work-dir tiles -- passed
        through to both, they collided on ``bake_separated``'s own and raised.
        Returns :meth:`pack_atlas`'s ``{object_name: (atlas_path, rect)}``.
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
        if claims is None:
            claims = LightmapRecords.claims()
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
                # Named in the work dir, which nobody reads: the pack names the
                # deliverables, against the file's claims.
                claims=None,
                **kwargs,
            )
            return self.pack_atlas(
                baked,
                output_dir=output_dir,
                prefix=prefix,
                suffix=suffix,
                plan=plan,
                claims=claims,
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
        claims: Optional[Dict[str, Any]] = None,
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

        No atlas lands on a file name an object outside its group reads (*claims*,
        :meth:`LightmapRecords.claims`; ``None`` reads the file's now): a partial
        re-bake of a group -- or one whose member failed -- gets an atlas of its own,
        and the member left out keeps sampling the old one.

        Returns ``{object_name: (atlas_path, [scaleX, scaleY, offsetX, offsetY])}`` — the rect
        is the ENGINE BINDING to publish per instance (``commit_lightmap(scale_offsets=...)``;
        identity for solo/fallback), not an applied UV remap.
        """
        if not mapping:
            return {}
        output_dir = output_dir or os.path.dirname(next(iter(mapping.values())))
        if plan is None:
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
                    claims,
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
                        out[n] = (path, list(self._IDENTITY_SCALE_OFFSET))
        return out

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
    ) -> None:
        """Pack one material group's maps into its atlas (see :meth:`pack_atlas`).

        The atlas name is the group's to take only where the group's own members
        are all that read it (*claims*)."""
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
            out[names[0]] = (path, list(self._IDENTITY_SCALE_OFFSET))
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
        claims: Optional[Dict[str, Any]] = None,
        owners=(),
    ) -> str:
        """Move a finished map into *output_dir* and return its new path.

        Only results belong in the destination -- a bake's intermediates stay in whatever
        work dir produced them. A same-named file already there is replaced when only the
        map's own *owners* read it (that is what re-baking means); a name anything else
        reads (*claims*, :meth:`LightmapRecords.claims`) is left alone, and so is a
        collision *within* one pack -- both take a numeric tail instead
        (:meth:`ptk.FileUtils.unique_path`). ``shutil`` rather than ``os.replace`` because
        the work dir is routinely on a different volume from the project.

        A destination that cannot be replaced takes an adjacent name instead of failing:
        the realistic cause is the previous map being held open by the DCC's own texture
        cache, and losing a finished bake over a file lock would be absurd.

        *stem* renames the map on the way in (default: keep the source's own). *avoid* is
        a set of abspaths that must not be overwritten -- another group's not-yet-consumed
        source maps, reachable only once *stem* is derived rather than inherited.
        """
        import shutil

        src_abs = os.path.abspath(src)
        os.makedirs(output_dir, exist_ok=True)
        src_stem, ext = os.path.splitext(os.path.basename(src))
        stem = stem or src_stem
        dst = ptk.FileUtils.unique_path(
            output_dir, stem, ext, used, claims=claims, owners=owners, avoid=avoid
        )
        if os.path.abspath(dst) != src_abs:
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except OSError:
                    # Held open: the next spelling nothing occupies and nobody
                    # else reads.
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
        reason="Only an old atlas pack squeezed UVs into a rect, and "
        "LightmapRecords.migrate_legacy now restores those losslessly.",
    )
    @ptk.Deprecation.parameter(
        "intensity",
        remove_in="0.11.0",
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

    @ptk.Deprecation.symbol("LightmapRecords.lightmap_dependencies", remove_in="0.11.0")
    def lightmap_dependencies(
        self, objects=None, search_dirs=None, walk: bool = True
    ) -> List[Dict[str, Any]]:
        """Moved to :meth:`LightmapRecords.lightmap_dependencies`."""
        return LightmapRecords.lightmap_dependencies(objects, search_dirs, walk)

    @classmethod
    @ptk.Deprecation.symbol("LightmapRecords.search_dirs", remove_in="0.11.0")
    def search_dirs(cls, objects=None) -> List[str]:
        """Moved to :meth:`LightmapRecords.search_dirs`."""
        return LightmapRecords.search_dirs(objects)

    @ptk.Deprecation.symbol("LightmapRecords.heal_lightmap_paths", remove_in="0.11.0")
    def heal_lightmap_paths(self, objects=None) -> Dict[str, Any]:
        """Moved to :meth:`LightmapRecords.heal_lightmap_paths`."""
        return LightmapRecords.heal_lightmap_paths(objects)

    @ptk.Deprecation.symbol("LightmapRecords.relocate_lightmaps", remove_in="0.11.0")
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

    @ptk.Deprecation.symbol("LightmapRecords.repath_lightmaps", remove_in="0.11.0")
    def repath_lightmaps(
        self, dirs_by_map: Dict[str, str], objects=None, relative: bool = True
    ) -> int:
        """Moved to :meth:`LightmapRecords.repath_lightmaps`."""
        return LightmapRecords.repath_lightmaps(dirs_by_map, objects, relative)

    @ptk.Deprecation.symbol(
        "LightmapRecords.normalize_lightmap_paths", remove_in="0.11.0"
    )
    def normalize_lightmap_paths(self, objects=None, relative: bool = True) -> int:
        """Moved to :meth:`LightmapRecords.normalize_lightmap_paths`."""
        return LightmapRecords.normalize_lightmap_paths(objects, relative)

    @classmethod
    @ptk.Deprecation.symbol("LightmapRecords.export_record", remove_in="0.11.0")
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """Moved to :meth:`LightmapRecords.export_record`."""
        return LightmapRecords.export_record(ctx)

    @classmethod
    @ptk.Deprecation.symbol(
        "LightmapRecords.refresh_export_metadata", remove_in="0.11.0"
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
