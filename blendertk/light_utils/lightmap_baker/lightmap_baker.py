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
* ``DataNodes.set_export_string`` -- the Unity bridge (custom prop on the ``data_export``
  Empty, rides the FBX) for unitytk to set up native lightmaps with no sidecar file.

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
    # regenerated from the per-object markers and ridden into the FBX for unitytk.
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
    ) -> Dict[str, str]:
        """Record a lighting-only bake for the engine (changes nothing about the material/UVs).

        Per object stamps a small JSON marker (:attr:`LIGHTMAP_INFO_PROP`), then republishes
        the scene-wide manifest onto the shared ``data_export`` carrier so it rides the FBX for
        unitytk. ``mapping`` is ``{object_name: lightmap_path}``. Returns the recorded subset.
        """
        import bpy

        scale_offsets = scale_offsets or {}
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
            obj[self.LIGHTMAP_INFO_PROP] = json.dumps(info)
            recorded[name] = path

        if recorded:
            self._publish_lightmap_metadata()
        return recorded

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
            objects.append(
                {
                    "name": obj.name,  # the Unity GameObject join key
                    "map": info.get("map"),
                    "uvIndex": 1,
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
        """Undo :meth:`commit_lightmap` -- drop the markers + republish.

        Non-destructive by nature (material / UVs were never changed); the baked texture and
        its UV layer are left in place (harmless, reused by the next bake). ``objects=None``
        clears every marked object. Returns the names cleared.
        """
        cleared = []
        for obj in self._marked_objects(self.LIGHTMAP_INFO_PROP, objects):
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

    A thin driver over :class:`LightmapBaker` (composition; no bake logic here). **Bake
    Lightmaps** (``b000``) runs revert -> bake -> commit for the selection; the **Mode**
    combobox (``cmb001``) picks the level:

    * **Lighting Only** (default) — :meth:`~LightmapBaker.bake_separated` +
      :meth:`~LightmapBaker.commit_lightmap`. Keeps the full PBR material; bakes lighting onto
      UV1 and stamps Unity metadata on the shared ``data_export`` carrier.
    * **Fused Unlit** — :meth:`~LightmapBaker.bake_fused` +
      :meth:`~LightmapBaker.commit_unlit`. Bakes albedo×lighting into one map + an unlit
      material; the lowest-end / fully baked option.

    Either way ``b000`` first calls :meth:`~LightmapBaker.revert` to clear prior wiring so the
    bake samples the real material; the header menu's **Revert to Source** undoes it. The
    Quality combobox is populated from :meth:`~LightmapBaker.preset_store` and fills the
    Resolution / Samples dials (the source of truth at bake time).

    Tentacle-independent (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` ``fmt`` helper is
    deferred into the methods that use it (headless Blender ships no Qt binding).
    """

    _MODE_LABELS = ("Lighting Only (keep maps)", "Fused Unlit (single map)")

    # Fixed lightmap sizes (square, px) for the Resolution combobox
    # (cmb_resolution). Power-of-two atlas sizes; every Quality preset lands on
    # one of these. _resolution() reads the selection back as an int.
    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    # Scope labels for the Scope combobox (cmb_scope): which objects b000 bakes.
    # Selected (index 0, default) preserves the prior selection-only behavior;
    # _scope() / _scope_objects() resolve it to the mesh objects to bake.
    _SCOPE_LABELS = ("Selected", "Visible", "Scene")

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
        """Configure the header menu + help text."""
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
                    "Pick a <b>Mode</b> (see below) and a <b>Quality</b> preset (fills "
                    "Resolution / Samples; override either to taste).",
                    "Press <b>Bake Lightmaps</b>, then export the FBX with <b>Custom "
                    "Properties</b> enabled (so the hidden <i>data_export</i> Empty carries "
                    "the Unity wiring).",
                ],
                sections=[
                    ("Mode: Lighting Only — real lightmapping (default)", [
                        "Bakes <i>lighting only</i> (Cycles diffuse, no albedo) onto a second "
                        "UV channel; your full PBR material is <b>kept untouched</b>.",
                        "The lightmap is a <b>separate EXR</b>; the engine multiplies "
                        "albedo × lightmap at runtime and your normal map still works. The "
                        "wiring rides the FBX on the shared data Empty (no sidecar); unitytk "
                        "sets up Unity's native lightmaps on import.",
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
        prefix, suffix = self._resolve_affix(self.ui.txt000.text())
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
        else:
            self._baker.commit_lightmap(result)
            tail = "Maps kept; lightmap + Unity metadata stamped. Export the FBX."
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
        """Where bakes go: ``//textures`` next to the .blend, else a temp dir."""
        import bpy
        import tempfile

        blend = bpy.data.filepath
        root = os.path.dirname(blend) if blend else tempfile.gettempdir()
        return os.path.join(root, "textures")

    @staticmethod
    def _resolve_affix(text: str) -> Tuple[str, str]:
        """Parse the affix field: leading ``_`` → suffix, trailing ``_`` → prefix, else suffix.

        Default ``_Lightmap`` → ``("", "_Lightmap")`` so output is ``<object>_Lightmap``.
        """
        text = (text or "").strip()
        if not text:
            return "", ""
        if text.startswith("_"):
            return "", text
        if text.endswith("_"):
            return text, ""
        return "", "_" + text


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("lightmap_baker", reload=True)
    ui.show(pos="screen", app_exec=True)
