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
import os
from typing import Any, Callable, Dict, List, Optional

import pythontk as ptk


class TextureBaker(ptk.LoggingMixin):
    """Generic Cycles bake-to-texture primitive (mirror of mayatk's ``TextureBaker``).

    Usage::

        baker = TextureBaker(resolution=1024, samples=8)
        out = baker.bake(objects)                  # {object_name: exr_path}, COMBINED
        out = baker.bake(objects, bake_type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"},
                         use_pass_color=False)      # lighting-only irradiance
    """

    def __init__(self, resolution: int = 1024, samples: int = 5):
        super().__init__()
        self.resolution = int(resolution)
        self.samples = int(samples)

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
                        obj, output_dir, prefix, suffix, stem, used,
                        bake_type=bake_type, pass_filter=pass_filter,
                        uv_set=uv_set, colorspace=colorspace,
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
        self, obj, output_dir: str, prefix: str, suffix: str, stem, used: set,
        *, bake_type: str, pass_filter: Optional[set], uv_set, colorspace: str,
    ) -> Optional[str]:
        """Bake a single object into a fresh EXR; returns its path (cleans up temp nodes)."""
        import bpy

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

        for x in bpy.context.selected_objects:
            x.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        try:
            bake_kwargs = {"type": bake_type, "use_clear": True, "save_mode": "INTERNAL"}
            if pass_filter:
                bake_kwargs["pass_filter"] = set(pass_filter)
            bpy.ops.object.bake(**bake_kwargs)
            os.makedirs(output_dir, exist_ok=True)
            image.filepath_raw = path
            image.file_format = "OPEN_EXR"
            image.save()
        finally:
            for nt, node in added:
                nt.nodes.remove(node)  # non-destructive: leave the material as it was
            # The pixels are on disk now; drop the in-memory datablock so repeated bakes
            # don't accumulate orphans (consumers reload it fresh from the file).
            bpy.data.images.remove(image)
        return path

    def _configure_bake_scene(self, margin: int, use_pass_color: bool) -> Dict[str, Any]:
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
        prev = {
            "engine": scene.render.engine,
            "samples": getattr(scene.cycles, "samples", None) if hasattr(scene, "cycles") else None,
            "bake": {k: getattr(bake, k) for k in new_bake},
        }
        scene.render.engine = "CYCLES"
        if hasattr(scene, "cycles"):
            scene.cycles.samples = self.samples
        for k, v in new_bake.items():
            setattr(bake, k, v)
        return prev

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

        if objects is None:
            objects = bpy.context.selected_objects or []
        pool = []
        for o in ptk.make_iterable(objects):
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None and getattr(obj, "type", None) == "MESH":
                pool.append(obj)
        return pool

    @staticmethod
    def _ensure_materials(obj) -> List[Any]:
        """Every material slot uses nodes (Cycles needs a node tree); create one if absent."""
        from blendertk.mat_utils._mat_utils import create_mat, assign_mat

        # Dedupe by identity: a material shared across two slots must get one bake node, not two.
        materials = list(dict.fromkeys(s.material for s in obj.material_slots if s.material))
        if not materials:
            mat = create_mat("standard", name=f"{obj.name}_mat")
            assign_mat(obj, mat)
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
