# !/usr/bin/python
# coding=utf-8
"""Bake an object's shaded surface (material under scene lighting) to a texture — the Blender
counterpart of mayatk's ``mat_utils.texture_baker`` (``btk.TextureBaker`` ↔ ``mtk.TextureBaker``).

The low-level, generic **bake primitive**: it renders each object's shaded appearance to a
per-object EXR (with optional UV-set targeting), independent of any higher-level pipeline. The
lightmap *workflow* on top of it (UV2 generation, dilation/margin, engine-export prep, presets,
commit/revert) is :class:`blendertk.LightmapBaker`, which **composes** this class; use this
directly for one-off / preview bakes.

Where Maya's ``TextureBaker`` wraps Arnold RTT / ``convertSolidTx`` MEL, **Blender ships the whole
bake natively in Cycles**, so this is a thin adapter over ``bpy.ops.object.bake``:

* ``bake_type='COMBINED'`` — albedo × lighting (what the render shows), the mayatk-parity default.
* ``bake_type='DIFFUSE'`` + ``pass_filter={'DIRECT','INDIRECT'}`` (``use_pass_color=False``) —
  native white-card irradiance (lighting only, no material swap needed).
* ``scene.render.bake.margin`` — native gutter/seam padding.

The engine surface is Qt-free and defers ``import bpy`` (headless-importable).
"""

import logging
import os
import contextlib
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pythontk as ptk

# Module logger for the classmethod paths, where there is no instance to log through.
_logger = logging.getLogger(__name__)


class TextureBaker(ptk.LoggingMixin):
    """Generic Cycles bake-to-texture primitive (mirror of mayatk's ``TextureBaker``).

    Usage::

        baker = TextureBaker(resolution=1024, samples=8)
        out = baker.bake(objects)                  # {object_name: exr_path}, COMBINED
        out = baker.bake(objects, bake_type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"},
                         use_pass_color=False)      # lighting-only irradiance
    """

    #: ``"AUTO"`` device policy: an object bakes on the GPU only when its
    #: ``width * height * samples`` reaches this many path samples. Every
    #: ``bpy.ops.object.bake`` creates and frees its own Cycles session -- the
    #: objects of ONE multi-object call included (measured: 3 objects in one op
    #: = 3 resets + 3 frees, identical to 3 calls) -- and on the GPU that
    #: session is ~0.35 s of device setup + teardown per object where the CPU's
    #: is ~2 ms. A 50-instance atlas at 4 samples took 25.3 s on the GPU and
    #: 5.4 s on the CPU; at 256 samples the GPU won, 38 s to 77 s. The crossover
    #: sat at ~2-4 M samples per object (RTX 3070 Ti Laptop vs a 14-core i7);
    #: 1 M keeps the CPU only where it is clearly ahead. The CPU's cheap session
    #: holds only while the compute PREFERENCE is off: with OptiX enabled in
    #: preferences a CPU-device session still paid 0.16 s (device enumeration),
    #: so :meth:`_apply_device` flips ``compute_device_type`` per object as well.
    GPU_MIN_WORK: int = 1_000_000

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 64,
        denoise: bool = True,
        device: Optional[str] = None,
        bounces: int = 4,
    ):
        super().__init__()
        self.resolution = int(resolution)
        #: Diffuse bounces the bake integrates -- the Cycles counterpart of the
        #: ``GIDiffuseDepth`` mayatk's ``LightmapBaker`` pins on every Arnold bake, and
        #: pinned here for the same reason: left alone, a bake runs at whatever the
        #: SCENE last rendered with (4 on a factory startup, anything in a saved .blend),
        #: so the same scene bakes to different brightness in two sessions. In a closed
        #: room it is also the single biggest lightmap lever -- each extra bounce adds
        #: another rho^n term: measured in a rho~0.7 room, 1 -> 4 bounces is 1.65x
        #: (x1.29 / x1.16 / x1.10 per step), with nothing in the output to say why.
        #: That is a CYCLES-internal figure -- it is not a conversion to Arnold's
        #: ``GIDiffuseDepth``, whose numbers are measurably not interchangeable with
        #: these (see ``LightmapBaker.from_preset``).
        #:
        #: There is deliberately no ``gi_samples`` twin: Arnold samples GI separately,
        #: Cycles path-traces everything from :attr:`samples`, and cargo-culting the
        #: name would imply a dial that does not exist (blendertk mirrors mayatk's
        #: public API where the CONCEPTS meet, not where they diverge).
        #:
        #: The default is CYCLES' own (4), not the ``preview`` tier's, unlike
        #: :attr:`samples`: pinning is here to make a bake reproducible, not to
        #: restyle one, and the bare constructor is what a scripted caller and the
        #: panel's revert-only instance get. Anything lower here would silently
        #: darken every bake that never named a tier.
        self.bounces = int(bounces)
        #: Cycles PATHS per texel -- NOT mayatk's ``samples=5``, which is Arnold
        #: AA samples (~25 camera rays, each spawning GI rays: hundreds of
        #: effective diffuse samples). Mirroring the API does not mean mirroring
        #: a number across a unit change: 5 Cycles paths is pure noise, and the
        #: bare constructor is the one path that does not go through a preset
        #: (64 == the ``preview`` tier).
        self.samples = int(samples)
        #: Denoise each baked map. On by default because a *baked* texture is permanent —
        #: unlike a noisy render preview, the grain ships — and denoising buys more
        #: apparent quality per sample than any other single setting.
        #:
        #: Note ``scene.cycles.use_denoising`` does NOT do this: it is a *render* setting,
        #: is already ``True`` by factory default, and ``scene.render.bake`` exposes no
        #: denoise flag at all — a bake comes back with its raw sampling noise regardless.
        #: (Measured: 256-sample bakes were visibly grainy with it on.) So the denoise is
        #: applied as a post-pass over the saved EXR, via :meth:`denoise_image`.
        self.denoise = bool(denoise)
        #: ``"GPU"`` / ``"CPU"`` / ``"AUTO"`` / ``None`` (leave the scene's own device
        #: alone). ``"AUTO"`` decides per object -- see :attr:`GPU_MIN_WORK`.
        self.device = device
        #: The compute devices the last :meth:`_configure_bake_scene` enabled (empty on
        #: the CPU) -- what ``"AUTO"`` and the GPU denoise decide on -- and the Cycles
        #: backend (``"OPTIX"`` / ``"CUDA"`` / ...) they were found under.
        self._gpu_devices: List[str] = []
        self._gpu_backend: str = ""

    def bake(
        self,
        objects=None,
        *,
        bake_type: str = "COMBINED",
        pass_filter: Optional[set] = None,
        use_pass_color: bool = True,
        output_dir: Optional[str] = None,
        prefix: str = "",
        suffix: str = "",
        margin: Optional[int] = None,
        uv_set=None,
        stem: Optional[Any] = None,
        size: Optional[Any] = None,
        on_progress: Optional[Callable[[int, int, str], bool]] = None,
        colorspace: str = "Non-Color",
    ) -> Dict[str, str]:
        """Bake each object's shaded surface to a per-object EXR.

        Parameters:
            objects: Mesh objects (refs or names). Defaults to the current selection.
            bake_type: Cycles bake type (``COMBINED`` / ``DIFFUSE`` / …) passed to
                ``bpy.ops.object.bake``.
            pass_filter: Optional pass set for typed bakes (e.g. ``{'DIRECT','INDIRECT'}`` for a
                lighting-only DIFFUSE bake).
            use_pass_color: ``scene.render.bake.use_pass_color`` — ``False`` excludes albedo
                (native white-card irradiance).
            output_dir: Output directory (created if missing). Defaults to
                :meth:`default_output_dir`.
            prefix / suffix: Name affix wrapped around the object's stem.
            margin: Native gutter width in px. ``None`` → a size-scaled default (per object).
            uv_set: UV layer to make active before baking — a name (str), a ``callable(obj)->str``
                (resolved per object), or ``None`` (bake on the current active UV).
            stem: Output base-name resolver — ``{name: stem}`` dict, ``callable(obj)->str``, or
                ``None`` (default :meth:`texture_set_stem`, falling back to the object name).
            size: Per-object output size resolver — ``{name: (w, h) | px}`` dict,
                ``callable(obj) -> (w, h) | px``, or ``None`` (square :attr:`resolution`). Bake
                cost is linear in pixels, so a caller that already knows an object will only ever
                occupy part of an atlas bakes it at that footprint instead of paying for a
                full-resolution map it is about to downscale away.
            on_progress: ``(done, total, name) -> bool`` per-object callback (return ``False`` to
                cancel) so a UI can drive a progress bar.
            colorspace: Image colorspace (``Non-Color`` for a linear HDR map).

        Returns ``{object_name: texture_path}`` for each successful bake.
        """
        meshes = self.resolve_meshes(objects)
        if not meshes:
            self.logger.error("Nothing to bake. Pass objects= or select a mesh.")
            return {}

        output_dir = output_dir or self.default_output_dir()

        prev_state = self._configure_bake_scene(use_pass_color)
        # ``_bake_one`` makes each target the sole selected+active object, so
        # without this a batch leaves ONLY the last one selected -- and the
        # panel's Revert to Source reverts "the selection", i.e. one object of
        # the N just baked (or, when it lands empty, every baked object in the
        # scene).
        prev_selection = self._selection_state()
        used: set = set()
        result: Dict[str, str] = {}
        total = len(meshes)
        try:
            for i, obj in enumerate(meshes):
                if on_progress and on_progress(i, total, obj.name) is False:
                    break
                try:
                    path = self._bake_one(
                        obj,
                        output_dir,
                        prefix,
                        suffix,
                        stem,
                        used,
                        bake_type=bake_type,
                        pass_filter=pass_filter,
                        uv_set=uv_set,
                        colorspace=colorspace,
                        size=self._resolve_size(obj, size),
                        margin=margin,
                    )
                    if path:
                        result[obj.name] = path
                except Exception as e:  # one bad mesh must not abort the batch
                    self.logger.warning("Bake skipped for %s: %s", obj.name, e)
            if on_progress:
                on_progress(total, total, "")
        finally:
            self._restore_bake_scene(prev_state)
            self._restore_selection(prev_selection)
        # ONE compositor build and ONE engine flip for the whole batch, after
        # the bake loop rather than inside it: the per-map work is a render, and
        # the graph build, the engine swing and the format pinning around it are
        # fixed cost a per-map call pays N times. On the GPU the bake enabled,
        # OIDN denoises a 1024 map in ~0.25 s against ~1.3 s on the CPU (measured).
        # A CPU bake leaves the scene's own denoise device alone (None) rather
        # than pinning the CPU: a user who set GPU denoising keeps it.
        if self.denoise and result:
            self.denoise_images(
                result.values(), gpu=True if self._gpu_devices else None
            )
        return result

    @staticmethod
    def _selection_state():
        """``(selected objects, active object)`` -- the bake's restore point."""
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        return (
            list(CoreUtils.selected_objects()),
            bpy.context.view_layer.objects.active,
        )

    @staticmethod
    def _restore_selection(state) -> None:
        """Put back the selection + active object captured by :meth:`_selection_state`.

        Deleted objects are skipped (a bake cannot delete one, but a caller's
        progress callback can), and every access is guarded: a restore failure
        must never mask the bake's own result.
        """
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        objects, active = state
        try:
            for obj in CoreUtils.selected_objects():
                obj.select_set(False)
            for obj in objects:
                try:
                    obj.select_set(True)
                except (ReferenceError, RuntimeError):
                    continue  # deleted, or no longer in the view layer
            try:
                bpy.context.view_layer.objects.active = active
            except (ReferenceError, RuntimeError):
                pass
        except Exception as error:  # noqa: BLE001
            _logger.debug("Selection restore skipped: %s", error)

    def _bake_one(
        self,
        obj,
        output_dir: str,
        prefix: str,
        suffix: str,
        stem,
        used: set,
        *,
        bake_type: str,
        pass_filter: Optional[set],
        uv_set,
        colorspace: str,
        size: Tuple[int, int],
        margin: Optional[int],
    ) -> Optional[str]:
        """Bake a single object into a fresh EXR; returns its path (cleans up temp nodes)."""
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        if uv_set is not None:  # optional UV-set targeting (e.g. a lightmap UV channel)
            name = uv_set(obj) if callable(uv_set) else uv_set
            if name and name in obj.data.uv_layers:
                obj.data.uv_layers[name].active = True

        materials, temp_material = self._ensure_materials(obj)
        base = self._resolve_stem(obj, stem) or obj.name
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        path = self._unique_path(output_dir, name, used)

        width, height = size
        self._apply_device(width * height * self.samples)
        # Derived from this map's OWN size, not the batch's. The margin is how far each
        # island's edge pixels are extended into the surrounding empty space, so it only
        # makes sense relative to the map it is extending across: a value picked for a
        # full-size atlas dilates a small tile out of all proportion, and the old flat
        # 8px floor overshot even a 256px map (its islands sit ~2% apart, i.e. ~5px, so
        # 8px of extension had neighbours bleeding into each other).
        bake_settings = bpy.context.scene.render.bake
        bake_settings.margin = (
            max(4, min(width, height) // 64) if margin is None else int(margin)
        )

        image = bpy.data.images.new(
            os.path.basename(os.path.splitext(path)[0]),
            width,
            height,
            float_buffer=True,
        )
        image.colorspace_settings.name = colorspace

        # Add a selected+active image-texture node to every material so Cycles bakes into it.
        added = []
        for mat in materials:
            nt = mat.node_tree
            node = nt.nodes.new("ShaderNodeTexImage")
            node.image = image
            node.select = True
            nt.nodes.active = node
            added.append((nt, node))

        for x in CoreUtils.selected_objects():
            x.select_set(False)

        try:
            bake_kwargs = {
                "type": bake_type,
                "use_clear": True,
                "save_mode": "INTERNAL",
            }
            if pass_filter:
                bake_kwargs["pass_filter"] = set(pass_filter)
            # ONE object revealed, for its own bake only: Cycles skips a
            # ``hide_render`` object entirely, so a hidden mesh baked to an
            # exact-black map that then read as a lighting bug. Revealing the
            # whole batch instead would let hidden geometry occlude and bounce
            # into every OTHER object's bake -- a lighting change, not a fix.
            with CoreUtils.visible_override(obj):
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.bake(**bake_kwargs)
            os.makedirs(output_dir, exist_ok=True)
            image.filepath_raw = path
            image.file_format = "OPEN_EXR"
            image.save()
            # Denoising is deliberately NOT done here -- see ``bake``, which
            # runs one batched pass after the loop.
        finally:
            for nt, node in added:
                nt.nodes.remove(node)  # non-destructive: leave the material as it was
            # The pixels are on disk now; drop the in-memory datablock so repeated bakes
            # don't accumulate orphans (consumers reload it fresh from the file).
            bpy.data.images.remove(image)
            if temp_material is not None:
                # A material invented purely so Cycles had a node tree to bake
                # through. Leaving it assigned contradicts the workflow's
                # non-destructive contract (and re-baking would accumulate one
                # per run). Removing the datablock unassigns it everywhere --
                # but it leaves the SLOT behind, empty, on a mesh that had
                # none, so the slot goes too. Only when it is the lone empty
                # one: anything else means something during the bake gave the
                # object a material of its own, which is not ours to drop.
                with ptk.CoreUtils.teardown_guard(
                    self.logger, f"temporary bake material on {obj.name}"
                ):
                    data = getattr(obj, "data", None)
                    bpy.data.materials.remove(temp_material)
                    slots = getattr(data, "materials", None)
                    if slots is not None and len(slots) == 1 and slots[0] is None:
                        slots.clear()
        return path

    def _configure_bake_scene(self, use_pass_color: bool) -> Dict[str, Any]:
        """Switch the scene to a deterministic Cycles bake config; return the prior state.

        Overrides every ``scene.render.bake`` field the bake depends on (not just the passes)
        so a user's leftover settings can't corrupt it — e.g. ``use_selected_to_active`` would
        bake one object onto another, ``target='VERTEX_COLORS'`` would write to vertex colors
        instead of the image. All are restored by :meth:`_restore_bake_scene`.

        Nothing here can amortize the per-object Cycles session: ``bpy.ops.object.bake``
        creates and frees the engine for every object it bakes, one multi-object call
        included, so each object pays a full scene sync (and on the GPU the device
        setup + teardown) whatever ``use_persistent_data`` says -- measured 51 resets and
        51 frees for 51 objects with it on, so it is deliberately NOT pinned. What this
        can do is put small bakes on the device whose session is cheap: ``"AUTO"``
        (see :attr:`GPU_MIN_WORK`), applied per object by :meth:`_apply_device`.
        """
        import bpy

        scene = bpy.context.scene
        bake = scene.render.bake
        new_bake = {
            "use_pass_direct": True,
            "use_pass_indirect": True,
            "use_pass_color": use_pass_color,  # False excludes albedo (native white-card)
            "use_selected_to_active": False,  # bake each object onto itself
            "target": "IMAGE_TEXTURES",  # never vertex colors
            # EXTEND replicates island-edge shading outward -- the same
            # semantics as Arnold RTT's extend_edges on the mayatk side. Left
            # unpinned, a scene saved with ADJACENT_FACES pads margins from
            # geometrically-adjacent faces instead, so an interactive bake and
            # a --factory-startup bridge bake disagreed at every island edge.
            "margin_type": "EXTEND",
        }
        has_cycles = hasattr(scene, "cycles")
        prev = {
            "engine": scene.render.engine,
            "samples": getattr(scene.cycles, "samples", None) if has_cycles else None,
            # Film exposure multiplies every pixel Cycles writes, a bake
            # included, so a scene left at a non-1.0 viewing exposure silently
            # rescales the lightmap -- and ``commit_lightmap``'s own intensity
            # then compounds it. A baked map is radiometric data, not a view.
            "film_exposure": getattr(scene.cycles, "film_exposure", None)
            if has_cycles
            else None,
            "use_denoising": getattr(scene.cycles, "use_denoising", None)
            if has_cycles
            else None,
            # Bounce depth: pinned for the bake and put back afterwards, so a bake
            # never inherits (or leaves behind) the scene's render bounce budget.
            "diffuse_bounces": getattr(scene.cycles, "diffuse_bounces", None)
            if has_cycles
            else None,
            "max_bounces": getattr(scene.cycles, "max_bounces", None)
            if has_cycles
            else None,
            "device": getattr(scene.cycles, "device", None) if has_cycles else None,
            # Margin is set per object (sizes differ), so capture it here to restore.
            "bake": {k: getattr(bake, k) for k in (*new_bake, "margin")},
        }
        scene.render.engine = "CYCLES"
        # Per run: a CPU/None bake after an AUTO one must not inherit the backend
        # the earlier run found and hand it back at restore time.
        self._gpu_devices = []
        self._gpu_backend = ""
        if has_cycles:
            scene.cycles.samples = self.samples
            scene.cycles.use_denoising = self.denoise
            if prev["diffuse_bounces"] is not None:
                scene.cycles.diffuse_bounces = self.bounces
                # max_bounces is the global ceiling, so a scene set below the
                # requested diffuse depth would silently clamp it: raise it to at
                # least what we asked for, never lower the user's own budget.
                if prev["max_bounces"] is not None:
                    scene.cycles.max_bounces = max(prev["max_bounces"], self.bounces)
            if prev["film_exposure"] is not None:
                scene.cycles.film_exposure = 1.0
            if self.device in ("GPU", "AUTO"):
                self._gpu_devices = self._enable_gpu_devices()
                # AUTO re-decides per object (_apply_device); until then, and for a
                # plain GPU request, the scene device follows what was found.
                scene.cycles.device = "GPU" if self._gpu_devices else "CPU"
            elif self.device:
                scene.cycles.device = self.device
        for k, v in new_bake.items():
            setattr(bake, k, v)
        return prev

    def _choose_device(self, work: int) -> str:
        """The device the ``"AUTO"`` policy bakes *work* path samples on.

        ``"GPU"`` only when a compute device was enabled AND the object's work
        clears :attr:`GPU_MIN_WORK`; a small bake finishes on the CPU before the
        GPU session has even been set up.
        """
        return "GPU" if self._gpu_devices and work >= self.GPU_MIN_WORK else "CPU"

    def _apply_device(self, work: int) -> None:
        """Point Cycles where ``"AUTO"`` sends *work* samples: the scene device AND
        the compute preference.

        A no-op for every other :attr:`device` value. Cheap to do per object:
        the session is rebuilt per bake anyway, so switching costs nothing extra.
        The preference matters as much as the device: with a GPU backend enabled,
        even a CPU-device session enumerates the compute devices on every reset
        (measured 0.16 s per object; ~2 ms with the backend set to ``NONE``).
        """
        if self.device != "AUTO":
            return
        import bpy

        scene = bpy.context.scene
        if not hasattr(scene, "cycles"):
            return
        target = self._choose_device(work)
        if self._gpu_backend:
            self._set_compute_backend(self._gpu_backend if target == "GPU" else "NONE")
        if scene.cycles.device != target:  # an unchanged RNA set still tags an update
            scene.cycles.device = target

    def _set_compute_backend(self, backend: str) -> None:
        """Switch Cycles' ``compute_device_type`` to *backend* (``"NONE"`` = CPU only),
        re-asserting the device ``use`` flags a real backend needs."""
        import bpy

        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
        except (KeyError, AttributeError):
            return
        if prefs.compute_device_type == backend:
            return
        try:
            prefs.compute_device_type = backend
        except TypeError:  # backend not valid on this build -- leave it
            return
        if backend != "NONE":
            self._use_backend_devices(prefs, backend)

    @classmethod
    def denoise_image(
        cls, path: str, output: Optional[str] = None, gpu: Optional[bool] = None
    ) -> Optional[str]:
        """Denoise a baked EXR in place (or to *output*) with OpenImageDenoise.

        Cycles will not denoise a bake itself -- ``use_denoising`` is a render setting and
        ``scene.render.bake`` has no equivalent -- so the map is pushed back through
        Blender's own compositor, which does expose OIDN as ``CompositorNodeDenoise``.
        This is the single largest quality lever available to a lightmap: indirect light in
        a small interior needs thousands of samples to resolve clean by brute force, and
        stays grainy at any sample count a production loop can afford.

        Measured on a 512-sample interior bake: mean |laplacian| 0.389 -> 0.008.

        For more than one map use :meth:`denoise_images`, which pays this method's
        (substantial) scene setup once for the whole set.

        Returns the written path, or ``None`` if denoising was unavailable (the caller
        keeps the raw bake -- noisy beats missing).
        """
        return cls.denoise_images(
            [path], outputs=[output] if output else None, gpu=gpu
        ).get(path)

    @classmethod
    def denoise_images(
        cls,
        paths: Iterable[str],
        outputs: Optional[Iterable[Optional[str]]] = None,
        gpu: Optional[bool] = None,
    ) -> Dict[str, str]:
        """Denoise several baked EXRs through ONE compositor build and ONE engine flip.

        The per-image work is a ``render()``; everything around it -- creating the
        compositor graph, forcing the engine to Workbench, pinning the output format and
        view transform, and putting all of that back -- is fixed cost that a per-image
        call pays N times over. A lightmap bake produces N maps at once, so batching
        here is worth more than the compositor time it saves.

        Parameters:
            paths: The EXRs to denoise.
            outputs: Optional per-path destinations (``None`` entries denoise in place).
            gpu: Allow OpenImageDenoise on the GPU (``True``) or pin the CPU
                (``False``); ``None`` leaves the scene's own
                ``compositor_denoise_device`` alone. Blender's default is ``AUTO``,
                which measured as the CPU here: 1.3 s per 1024 map against 0.25 s on
                the GPU. Per map, the GPU is used only from
                :attr:`DENOISE_GPU_MIN_TEXELS` up -- below that its fixed per-call
                cost loses to the CPU. :meth:`bake` passes what its own device probe
                found, so a GPU bake gets a GPU denoise for free.

        Returns:
            ``{input path: written path}`` for each map that was denoised. A map that
            could not be denoised is simply absent -- the raw bake stays on disk.
        """
        import bpy

        sources = [p for p in paths if isinstance(p, str)]
        destinations = list(outputs) if outputs is not None else []
        destinations += [None] * (len(sources) - len(destinations))
        pairs = [
            (path, output or path)
            for path, output in zip(sources, destinations)
            if os.path.isfile(path)
        ]
        if not pairs:
            return {}

        scene = bpy.context.scene
        done: Dict[str, str] = {}
        try:
            with cls._denoise_session(scene, gpu=gpu) as image_node:
                for path, output in pairs:
                    written = cls._denoise_one(scene, image_node, path, output, gpu=gpu)
                    if written:
                        done[path] = written
        except Exception as error:  # noqa: BLE001 -- a denoise failure must not lose a bake
            # Module logger, not ``cls().logger``: constructing an instance inside the
            # handler would raise for any subclass with required __init__ args, replacing
            # the real failure with a confusing one.
            _logger.warning("Denoise unavailable (%s); keeping the raw bake(s).", error)
        return done

    #: Compositor surfaces, newest first. Blender 5.x moved the scene compositor to
    #: ``scene.compositing_node_group`` -- a real node *group*, terminating in a
    #: ``NodeGroupOutput`` fed by an interface socket, with ``CompositorNodeComposite``
    #: gone entirely. 4.x is ``scene.use_nodes`` + ``scene.node_tree`` + a
    #: ``CompositorNodeComposite``. blendertk supports both, and probing the attribute
    #: is the only honest test: assigning the 5.x property on 4.x raises, which is how
    #: the biggest quality lever in this module came to be silently dead there.
    _COMPOSITOR_GROUP_ATTR: str = "compositing_node_group"

    #: Where the compositor's Denoise node runs (Blender 4.2+; older builds have
    #: no such setting and the denoise simply stays where the scene left it).
    _DENOISE_DEVICE_ATTR: str = "compositor_denoise_device"
    #: Smallest map (texels) worth denoising on the GPU. Measured: a 1024 map
    #: takes 0.25 s on the GPU vs 1.3 s on the CPU, but a 135x138 atlas tile
    #: takes 0.09 s on the GPU vs 0.047 s on the CPU -- the GPU pays ~0.085 s per
    #: call before it touches a texel, so the crossover sits near 50k texels.
    DENOISE_GPU_MIN_TEXELS: int = 256 * 256

    @classmethod
    @contextlib.contextmanager
    def _denoise_session(cls, scene, gpu: Optional[bool] = None):
        """Own the compositor graph + render settings for a run of denoises.

        Yields the graph's ``CompositorNodeImage``, whose ``.image`` each pass
        swaps; restores every setting it pinned, and removes every node it
        added, on any exit. *gpu* pins the denoise device (see
        :meth:`denoise_images`); the compositor itself stays on the CPU -- its
        GPU path measured a 3 s first-run shader compile for no gain on the
        denoise, which is the only node in the graph.
        """
        import bpy

        modern = hasattr(scene, cls._COMPOSITOR_GROUP_ATTR)
        pin_device = gpu is not None and hasattr(scene.render, cls._DENOISE_DEVICE_ATTR)
        prior = {
            "denoise_device": getattr(scene.render, cls._DENOISE_DEVICE_ATTR, None)
            if pin_device
            else None,
            "group": getattr(scene, cls._COMPOSITOR_GROUP_ATTR, None)
            if modern
            else None,
            "use_nodes": None if modern else scene.use_nodes,
            "engine": scene.render.engine,
            "filepath": scene.render.filepath,
            "format": scene.render.image_settings.file_format,
            "depth": scene.render.image_settings.color_depth,
            "color_mode": scene.render.image_settings.color_mode,
            "res_x": scene.render.resolution_x,
            "res_y": scene.render.resolution_y,
            "pct": scene.render.resolution_percentage,
            "view": scene.view_settings.view_transform,
            # Without these the render can bypass or overwrite the compositor's
            # answer entirely: use_compositing off writes the raw Workbench frame
            # OVER the input map, a VSE strip supersedes the compositor, a stamp
            # burns text into the data, and a border renders a crop.
            "use_compositing": scene.render.use_compositing,
            "use_sequencer": scene.render.use_sequencer,
            "use_stamp": scene.render.use_stamp,
            "use_border": scene.render.use_border,
        }
        tree = (
            bpy.data.node_groups.new("btk_denoise", "CompositorNodeTree")
            if modern
            else None
        )
        added: List[Any] = []
        muted: List[Any] = []
        try:
            if modern:
                tree.interface.new_socket(
                    "Image", in_out="OUTPUT", socket_type="NodeSocketColor"
                )
                image_node = tree.nodes.new("CompositorNodeImage")
                denoise = tree.nodes.new("CompositorNodeDenoise")
                output_node = tree.nodes.new("NodeGroupOutput")
                tree.links.new(image_node.outputs["Image"], denoise.inputs["Image"])
                tree.links.new(denoise.outputs["Image"], output_node.inputs[0])
                setattr(scene, cls._COMPOSITOR_GROUP_ATTR, tree)
            else:  # Blender 4.x: the scene's own node tree, terminating in Composite
                # ADDITIVE, never a clear: this is the user's own compositor
                # graph, not a scratch group, and wiping it to make room for
                # three nodes would destroy work a bake has no business
                # touching. Any existing Composite is muted for the duration so
                # ours is the one output, and only what we added is removed.
                scene.use_nodes = True
                tree = scene.node_tree
                muted = [n for n in tree.nodes if n.type == "COMPOSITE" and not n.mute]
                for node in muted:
                    node.mute = True
                image_node = tree.nodes.new("CompositorNodeImage")
                denoise = tree.nodes.new("CompositorNodeDenoise")
                output_node = tree.nodes.new("CompositorNodeComposite")
                added = [image_node, denoise, output_node]
                tree.links.new(image_node.outputs["Image"], denoise.inputs["Image"])
                tree.links.new(denoise.outputs["Image"], output_node.inputs["Image"])
            # Workbench: the compositor is the point, the 3D render is a formality.
            scene.render.engine = "BLENDER_WORKBENCH"
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "OPEN_EXR"
            scene.render.image_settings.color_depth = "32"
            scene.render.image_settings.color_mode = "RGBA"
            scene.render.use_compositing = True
            scene.render.use_sequencer = False
            scene.render.use_stamp = False
            scene.render.use_border = False
            # The denoise device itself is set per map (``_denoise_one``): the GPU's
            # fixed per-call cost makes it the slower choice for a small tile.
            # Standard, not the default view transform: the lightmap is linear data and a
            # filmic/AgX curve would bake a tone mapping into it.
            scene.view_settings.view_transform = "Standard"
            yield image_node
        finally:
            if pin_device:
                try:
                    setattr(
                        scene.render, cls._DENOISE_DEVICE_ATTR, prior["denoise_device"]
                    )
                except (AttributeError, TypeError):
                    pass
            if modern:
                try:
                    setattr(scene, cls._COMPOSITOR_GROUP_ATTR, prior["group"])
                except (AttributeError, TypeError):  # older/newer compositor surface
                    pass
                if tree is not None:
                    bpy.data.node_groups.remove(tree)
            else:
                for node in added:
                    try:
                        tree.nodes.remove(node)
                    except (ReferenceError, RuntimeError):
                        pass
                for node in muted:
                    try:
                        node.mute = False
                    except (ReferenceError, RuntimeError):
                        pass
                scene.use_nodes = prior["use_nodes"]
            scene.render.engine = prior["engine"]
            scene.render.filepath = prior["filepath"]
            scene.render.image_settings.file_format = prior["format"]
            scene.render.image_settings.color_depth = prior["depth"]
            scene.render.image_settings.color_mode = prior["color_mode"]
            scene.render.resolution_x = prior["res_x"]
            scene.render.resolution_y = prior["res_y"]
            scene.render.resolution_percentage = prior["pct"]
            scene.view_settings.view_transform = prior["view"]
            scene.render.use_compositing = prior["use_compositing"]
            scene.render.use_sequencer = prior["use_sequencer"]
            scene.render.use_stamp = prior["use_stamp"]
            scene.render.use_border = prior["use_border"]

    @classmethod
    def _denoise_device(cls, gpu: Optional[bool], texels: int) -> Optional[str]:
        """The ``compositor_denoise_device`` for a *texels*-sized map, or ``None`` to leave it.

        ``"GPU"`` only for a map of at least :attr:`DENOISE_GPU_MIN_TEXELS` when
        *gpu* is on; a smaller map is faster on the CPU than the GPU's fixed
        per-call cost. ``gpu=None`` -> ``None`` (the scene's own setting stands).
        """
        if gpu is None:
            return None
        return "GPU" if gpu and texels >= cls.DENOISE_GPU_MIN_TEXELS else "CPU"

    @classmethod
    def _denoise_one(
        cls, scene, image_node, path: str, output: str, gpu: Optional[bool] = None
    ) -> Optional[str]:
        """Render one map through the session's denoise graph; return the written path."""
        import bpy

        source = None
        try:
            source = bpy.data.images.load(os.path.abspath(path))
            width, height = source.size
            device = cls._denoise_device(gpu, width * height)
            if device is not None and hasattr(scene.render, cls._DENOISE_DEVICE_ATTR):
                setattr(scene.render, cls._DENOISE_DEVICE_ATTR, device)
            image_node.image = source
            scene.render.resolution_x = width
            scene.render.resolution_y = height
            scene.render.filepath = os.path.abspath(output)
            bpy.ops.render.render(write_still=True)
        except Exception as error:  # noqa: BLE001 -- one bad map must not lose the rest
            _logger.warning("Denoise skipped for %s: %s", os.path.basename(path), error)
            return None
        finally:
            image_node.image = None
            if source is not None:
                bpy.data.images.remove(source)

        # render() appends the format extension when the path has none.
        for candidate in (output, output + ".exr"):
            if os.path.isfile(candidate):
                if candidate != output:
                    os.replace(candidate, output)
                return output
        return None

    def _enable_gpu_devices(self) -> List[str]:
        """Turn on Cycles' compute devices; return the names enabled (empty -> CPU).

        ``scene.cycles.device = 'GPU'`` alone silently falls back to the CPU when no device
        is enabled in preferences, and ``--factory-startup`` (which every headless bridge
        run uses, by the session-safety rule) starts with exactly that state. So the
        addon preferences have to be configured in-process, per run.
        """
        import bpy

        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
        except (KeyError, AttributeError):
            self.logger.warning("Cycles preferences unavailable; baking on the CPU.")
            return []

        enabled: List[str] = []
        self._gpu_backend = ""
        prior = prefs.compute_device_type
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = backend
            except TypeError:  # not a valid backend on this build/platform
                continue
            try:
                prefs.get_devices()
            except Exception:  # noqa: BLE001 — probing devices must never abort a bake
                continue
            found = [d for d in prefs.devices if d.type == backend]
            if not found:
                continue
            self._use_backend_devices(prefs, backend)
            enabled = [d.name for d in found]
            self._gpu_backend = backend
            self.logger.info("Cycles %s device(s): %s", backend, ", ".join(enabled))
            break

        if not enabled:
            # The probe leaves the preference on the last backend it tried; put it
            # back, or every CPU session is charged that backend's enumeration.
            try:
                prefs.compute_device_type = prior
            except TypeError:
                pass
            self.logger.warning("No GPU compute device found; baking on the CPU.")
        return enabled

    @staticmethod
    def _use_backend_devices(prefs, backend: str) -> None:
        """Enable every *backend* compute device plus the CPU; leave the rest off."""
        for device in prefs.devices:
            device.use = device.type in (backend, "CPU")

    def _restore_bake_scene(self, prev: Dict[str, Any]) -> None:
        import bpy

        # ``AUTO`` may have left the compute preference on NONE for its last
        # object; hand the backend back enabled, as ``_enable_gpu_devices`` left
        # it, so the user's next GPU render is not silently a CPU one.
        if self._gpu_backend:
            self._set_compute_backend(self._gpu_backend)
        scene = bpy.context.scene
        scene.render.engine = prev["engine"]
        if hasattr(scene, "cycles"):
            # Every Cycles field the configure step touched -- leaving the user's scene
            # pinned to the baker's device / denoising would silently change how their
            # next *render* behaves, which reads as a Blender bug rather than ours.
            for attr in (
                "samples",
                "use_denoising",
                "device",
                "film_exposure",
                "diffuse_bounces",
                "max_bounces",
            ):
                if prev.get(attr) is not None:
                    setattr(scene.cycles, attr, prev[attr])
        bake = scene.render.bake
        for k, v in prev["bake"].items():
            setattr(bake, k, v)

    # ------------------------------------------------------------------
    # Helpers (generic — shared with the lightmap workflow that composes this)
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_meshes(objects) -> List[Any]:
        """Normalize ``objects`` (refs / names / None=selection) to mesh objects."""
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        if objects is None:
            objects = CoreUtils.selected_objects()
        pool = []
        for o in ptk.make_iterable(objects):
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None and getattr(obj, "type", None) == "MESH":
                pool.append(obj)
        return pool

    @staticmethod
    def _ensure_materials(obj) -> Tuple[List[Any], Any]:
        """Every material slot uses nodes (Cycles needs a node tree); create one if absent.

        Returns ``(materials, temporary_material)`` -- the second is the
        material invented for a bare object, for the caller to remove once the
        bake is written, and ``None`` when the object had its own. A bake must
        leave the object's material state exactly as it found it.
        """
        from blendertk.mat_utils._mat_utils import MatUtils

        # Dedupe by identity: a material shared across two slots must get one bake node, not two.
        materials = list(
            dict.fromkeys(s.material for s in obj.material_slots if s.material)
        )
        temporary = None
        if not materials:
            temporary = MatUtils.create_mat("standard", name=f"{obj.name}_mat")
            MatUtils.assign_mat(obj, temporary)
            materials = [temporary]
        for mat in materials:
            if not mat.use_nodes:
                mat.use_nodes = True
        return materials, temporary

    def _resolve_stem(self, obj, stem) -> Optional[str]:
        if isinstance(stem, dict):
            return stem.get(obj.name)
        if callable(stem):
            return stem(obj)
        if stem is None:
            return self.texture_set_stem(obj)
        return str(stem)

    def _resolve_size(self, obj, size) -> Tuple[int, int]:
        """``(width, height)`` for *obj*'s map — same resolver shapes as ``stem``.

        Anything unresolved falls back to the square :attr:`resolution`, so a partial
        ``{name: size}`` map is safe: an object the caller had no plan for still gets a
        full map rather than a 1px one.
        """
        value = size.get(obj.name) if isinstance(size, dict) else size
        if callable(value):
            value = value(obj)
        if value is None:
            value = self.resolution
        if isinstance(value, (int, float)):
            value = (value, value)
        width, height = value
        return max(1, int(width)), max(1, int(height))

    @staticmethod
    def texture_set_stem(obj) -> Optional[str]:
        """Base name of *obj*'s existing texture set (e.g. ``Plants_Metal_Base_01``).

        So a baked map follows the material's texture-set naming (``<base>_Lightmap``) instead of
        the object name. Scans the first file-backed image node and strips the map-type suffix via
        ``ptk.MapFactory.get_base_texture_name`` (same helper the game shader uses). Returns
        ``None`` (fall back to the object name) on any failure.
        """
        import bpy

        try:
            for slot in getattr(obj, "material_slots", []):
                mat = slot.material
                if not mat or not mat.use_nodes:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image and node.image.filepath:
                        base = bpy.path.basename(node.image.filepath)
                        return ptk.MapFactory.get_base_texture_name(base) or None
        except Exception:
            return None
        return None

    @staticmethod
    def default_output_dir(subdir: str = "baked_textures") -> str:
        """``<subdir>`` next to the saved .blend, else under the OS temp dir."""
        import bpy
        import tempfile

        blend = bpy.data.filepath
        root = os.path.dirname(blend) if blend else tempfile.gettempdir()
        return os.path.join(root, subdir)

    @staticmethod
    def _unique_path(output_dir: str, name: str, used: set) -> str:
        """``<output_dir>/<name>.exr`` made unique within one bake (shared stems -> ``_1`` …)."""
        candidate = os.path.join(output_dir, f"{name}.exr")
        k = 1
        while candidate in used:
            candidate = os.path.join(output_dir, f"{name}_{k}.exr")
            k += 1
        used.add(candidate)
        return candidate
