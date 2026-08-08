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
from typing import Any, Callable, Dict, List, Optional

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

    def __init__(
        self,
        resolution: int = 1024,
        samples: int = 5,
        denoise: bool = True,
        device: Optional[str] = None,
    ):
        super().__init__()
        self.resolution = int(resolution)
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
        #: ``"GPU"`` / ``"CPU"`` / ``None`` to leave the scene's own device alone.
        self.device = device

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
            margin: Native gutter width in px. ``None`` → a resolution-scaled default.
            uv_set: UV layer to make active before baking — a name (str), a ``callable(obj)->str``
                (resolved per object), or ``None`` (bake on the current active UV).
            stem: Output base-name resolver — ``{name: stem}`` dict, ``callable(obj)->str``, or
                ``None`` (default :meth:`texture_set_stem`, falling back to the object name).
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
        if margin is None:
            margin = max(8, self.resolution // 64)

        prev_state = self._configure_bake_scene(margin, use_pass_color)
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
                    )
                    if path:
                        result[obj.name] = path
                except Exception as e:  # one bad mesh must not abort the batch
                    self.logger.warning("Bake skipped for %s: %s", obj.name, e)
            if on_progress:
                on_progress(total, total, "")
        finally:
            self._restore_bake_scene(prev_state)
        return result

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
    ) -> Optional[str]:
        """Bake a single object into a fresh EXR; returns its path (cleans up temp nodes)."""
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        if uv_set is not None:  # optional UV-set targeting (e.g. a lightmap UV channel)
            name = uv_set(obj) if callable(uv_set) else uv_set
            if name and name in obj.data.uv_layers:
                obj.data.uv_layers[name].active = True

        materials = self._ensure_materials(obj)
        base = self._resolve_stem(obj, stem) or obj.name
        name = ptk.StrUtils.apply_affix(base, prefix, suffix)
        path = self._unique_path(output_dir, name, used)

        image = bpy.data.images.new(
            os.path.basename(os.path.splitext(path)[0]),
            self.resolution,
            self.resolution,
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
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        try:
            bake_kwargs = {
                "type": bake_type,
                "use_clear": True,
                "save_mode": "INTERNAL",
            }
            if pass_filter:
                bake_kwargs["pass_filter"] = set(pass_filter)
            bpy.ops.object.bake(**bake_kwargs)
            os.makedirs(output_dir, exist_ok=True)
            image.filepath_raw = path
            image.file_format = "OPEN_EXR"
            image.save()
            if self.denoise:
                # Post-pass over the saved file: Cycles cannot denoise the bake itself.
                self.denoise_image(path)
        finally:
            for nt, node in added:
                nt.nodes.remove(node)  # non-destructive: leave the material as it was
            # The pixels are on disk now; drop the in-memory datablock so repeated bakes
            # don't accumulate orphans (consumers reload it fresh from the file).
            bpy.data.images.remove(image)
        return path

    def _configure_bake_scene(
        self, margin: int, use_pass_color: bool
    ) -> Dict[str, Any]:
        """Switch the scene to a deterministic Cycles bake config; return the prior state.

        Overrides every ``scene.render.bake`` field the bake depends on (not just the passes)
        so a user's leftover settings can't corrupt it — e.g. ``use_selected_to_active`` would
        bake one object onto another, ``target='VERTEX_COLORS'`` would write to vertex colors
        instead of the image. All are restored by :meth:`_restore_bake_scene`.
        """
        import bpy

        scene = bpy.context.scene
        bake = scene.render.bake
        new_bake = {
            "margin": margin,
            "use_pass_direct": True,
            "use_pass_indirect": True,
            "use_pass_color": use_pass_color,  # False excludes albedo (native white-card)
            "use_selected_to_active": False,  # bake each object onto itself
            "target": "IMAGE_TEXTURES",  # never vertex colors
        }
        has_cycles = hasattr(scene, "cycles")
        prev = {
            "engine": scene.render.engine,
            "samples": getattr(scene.cycles, "samples", None) if has_cycles else None,
            "use_denoising": getattr(scene.cycles, "use_denoising", None)
            if has_cycles
            else None,
            "device": getattr(scene.cycles, "device", None) if has_cycles else None,
            "bake": {k: getattr(bake, k) for k in new_bake},
        }
        scene.render.engine = "CYCLES"
        if has_cycles:
            scene.cycles.samples = self.samples
            scene.cycles.use_denoising = self.denoise
            if self.device:
                scene.cycles.device = self.device
                if self.device == "GPU":
                    self._enable_gpu_devices()
        for k, v in new_bake.items():
            setattr(bake, k, v)
        return prev

    @classmethod
    def denoise_image(cls, path: str, output: Optional[str] = None) -> Optional[str]:
        """Denoise a baked EXR in place (or to *output*) with OpenImageDenoise.

        Cycles will not denoise a bake itself -- ``use_denoising`` is a render setting and
        ``scene.render.bake`` has no equivalent -- so the map is pushed back through
        Blender's own compositor, which does expose OIDN as ``CompositorNodeDenoise``.
        This is the single largest quality lever available to a lightmap: indirect light in
        a small interior needs thousands of samples to resolve clean by brute force, and
        stays grainy at any sample count a production loop can afford.

        Blender 5.x moved the scene compositor to ``scene.compositing_node_group`` (the old
        ``scene.node_tree`` is gone) and, being a real node *group*, it terminates in a
        ``NodeGroupOutput`` fed by an interface socket -- ``CompositorNodeComposite`` no
        longer exists at all. The write still goes through ``render()``, so the engine is
        forced to Workbench for the duration, making the 3D pass trivial while the
        compositor does the real work.

        Measured on a 512-sample interior bake: mean |laplacian| 0.389 -> 0.008.

        Returns the written path, or ``None`` if denoising was unavailable (the caller
        keeps the raw bake -- noisy beats missing).
        """
        import bpy

        if not os.path.isfile(path):
            return None
        output = output or path
        scene = bpy.context.scene

        prior = {
            "group": getattr(scene, "compositing_node_group", None),
            "engine": scene.render.engine,
            "filepath": scene.render.filepath,
            "format": scene.render.image_settings.file_format,
            "depth": scene.render.image_settings.color_depth,
            "res_x": scene.render.resolution_x,
            "res_y": scene.render.resolution_y,
            "pct": scene.render.resolution_percentage,
            "view": scene.view_settings.view_transform,
        }
        tree = bpy.data.node_groups.new("btk_denoise", "CompositorNodeTree")
        source = None
        try:
            source = bpy.data.images.load(os.path.abspath(path))
            width, height = source.size

            tree.interface.new_socket(
                "Image", in_out="OUTPUT", socket_type="NodeSocketColor"
            )
            image_node = tree.nodes.new("CompositorNodeImage")
            image_node.image = source
            denoise = tree.nodes.new("CompositorNodeDenoise")
            group_output = tree.nodes.new("NodeGroupOutput")
            tree.links.new(image_node.outputs["Image"], denoise.inputs["Image"])
            tree.links.new(denoise.outputs["Image"], group_output.inputs[0])

            scene.compositing_node_group = tree
            # Workbench: the compositor is the point, the 3D render is a formality.
            scene.render.engine = "BLENDER_WORKBENCH"
            scene.render.resolution_x = width
            scene.render.resolution_y = height
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "OPEN_EXR"
            scene.render.image_settings.color_depth = "32"
            # Standard, not the default view transform: the lightmap is linear data and a
            # filmic/AgX curve would bake a tone mapping into it.
            scene.view_settings.view_transform = "Standard"
            scene.render.filepath = os.path.abspath(output)

            bpy.ops.render.render(write_still=True)
        except Exception as error:  # noqa: BLE001 — a denoise failure must not lose a bake
            # Module logger, not ``cls().logger``: constructing an instance inside the
            # handler would raise for any subclass with required __init__ args, replacing
            # the real failure with a confusing one.
            _logger.warning("Denoise skipped for %s: %s", os.path.basename(path), error)
            return None
        finally:
            if source is not None:
                bpy.data.images.remove(source)
            try:
                scene.compositing_node_group = prior["group"]
            except (AttributeError, TypeError):  # older/newer compositor surface
                pass
            scene.render.engine = prior["engine"]
            scene.render.filepath = prior["filepath"]
            scene.render.image_settings.file_format = prior["format"]
            scene.render.image_settings.color_depth = prior["depth"]
            scene.render.resolution_x = prior["res_x"]
            scene.render.resolution_y = prior["res_y"]
            scene.render.resolution_percentage = prior["pct"]
            scene.view_settings.view_transform = prior["view"]
            bpy.data.node_groups.remove(tree)

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
            for device in prefs.devices:
                device.use = device.type in (backend, "CPU")
            enabled = [d.name for d in found]
            self.logger.info("Cycles %s device(s): %s", backend, ", ".join(enabled))
            break

        if not enabled:
            self.logger.warning("No GPU compute device found; baking on the CPU.")
        return enabled

    @staticmethod
    def _restore_bake_scene(prev: Dict[str, Any]) -> None:
        import bpy

        scene = bpy.context.scene
        scene.render.engine = prev["engine"]
        if prev["samples"] is not None and hasattr(scene, "cycles"):
            scene.cycles.samples = prev["samples"]
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
    def _ensure_materials(obj) -> List[Any]:
        """Every material slot uses nodes (Cycles needs a node tree); create one if absent."""
        from blendertk.mat_utils._mat_utils import MatUtils

        # Dedupe by identity: a material shared across two slots must get one bake node, not two.
        materials = list(
            dict.fromkeys(s.material for s in obj.material_slots if s.material)
        )
        if not materials:
            mat = MatUtils.create_mat("standard", name=f"{obj.name}_mat")
            MatUtils.assign_mat(obj, mat)
            materials = [mat]
        for mat in materials:
            if not mat.use_nodes:
                mat.use_nodes = True
        return materials

    def _resolve_stem(self, obj, stem) -> Optional[str]:
        if isinstance(stem, dict):
            return stem.get(obj.name)
        if callable(stem):
            return stem(obj)
        if stem is None:
            return self.texture_set_stem(obj)
        return str(stem)

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
