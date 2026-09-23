# !/usr/bin/python
# coding=utf-8
"""The Lightmap Baker panel: Switchboard slots for ``lightmap_baker.ui`` (Blender).

Mirror of mayatk's ``lightmap_baker_slots``: a thin driver over
:class:`~blendertk.LightmapBaker`, holding no bake logic and no bake policy --
the checks a bake runs and its verdict live on the engine's
:meth:`~blendertk.LightmapBaker.bake`, so a script bakes as safely as the
panel. ``BlenderUiHandler`` finds this class by name. Qt-only ``uitk`` helpers
are deferred into the methods that use them (headless Blender ships no Qt).
"""

import os
from typing import Dict, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.mat_utils.texture_baker import TextureBaker
from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker


class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the co-located ``lightmap_baker.ui`` panel.

    A thin driver over :class:`LightmapBaker` (composition; no bake logic here, and no
    bake policy). Mirrors mayatk's ``LightmapBakerSlots`` (same method names /
    signal-connection order). **Bake Lightmaps** (``b000``) hands the Scope and the dials
    to :meth:`~LightmapBaker.bake`, which checks the scene, bakes, keeps the full PBR
    material, puts lighting on UV1 and stamps Unity metadata on the shared ``data_export``
    carrier; the panel reports what came back.

    Nothing is reverted before a bake: an object the bake does not finish keeps the map it
    had. The header menu's **Revert to Source** undoes the wiring. The Quality combobox is populated from :meth:`~LightmapBaker.preset_store` and fills the
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
            setToolTip="Remove the lightmap wiring from the selected objects, or "
            "from every baked object when nothing is selected. The materials were "
            "never changed, and the baked EXR files stay on disk.",
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
                body="Bake Blender scene lighting (Cycles) into lightmaps — one per object, "
                "or one atlas per material — for game engines (Unity-first) and wire "
                "them up in one step — no manual export prep.",
                steps=[
                    "Choose a <b>Scope</b> — bake the <b>Selected</b> objects (default), all "
                    "<b>Visible</b> meshes, or the whole <b>Scene</b>.",
                    "Pick a <b>Packing</b> (see below) and a <b>Quality</b> "
                    "preset (fills Resolution / Samples; override either to taste — the "
                    "preset then reads <i>Custom</i>). <b>Device</b> picks what Cycles "
                    "bakes on — <i>Auto</i> takes the GPU per object where it pays and "
                    "the CPU for tiles too small to repay a GPU session.",
                    "Leave <b>Include Environment</b> on to bake the scene as authored. "
                    "Off detaches the world for the bake (and restores it after), so you "
                    "get the room's own lights without the environment's flat ambient "
                    "lift — which cannot be taken back out of a map once it is in.",
                    "Leave <b>Denoise</b> on: Cycles does not denoise a bake itself, "
                    "so each map goes through Blender's own denoiser after it is baked.",
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
                        "Lighting only — real lightmapping",
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
                            "<b>Revert to Source</b> (header menu) undoes the wiring. Re-baking "
                            "replaces the earlier maps; an object the bake doesn't finish keeps "
                            "the map it had.",
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

    def _denoise(self) -> bool:
        """Whether the bakes are denoised (chk_denoise; mirrors mayatk)."""
        return bool(self.ui.chk_denoise.isChecked())

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
        """Bake lightmaps for the Scope (:meth:`LightmapBaker.bake`; mirrors mayatk)."""
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
            denoise=self._denoise(),
            # Dials the tier carries but the panel does not show (mirrors mayatk).
            **getattr(self, "_preset_gi", {}),
        )

        out_dir = self._output_dir()
        # Name the output <object><affix> per the field (e.g. "<object>_Lightmap"), following
        # the texture-set convention; the field's affix picker forces Prefix / Suffix / Auto.
        # An empty field falls back to the placeholder default (the .ui's single source
        # for it), so a cleared field never bakes affix-less files.
        field = self.ui.txt000
        affix = field.text().strip() or field.placeholderText()
        prefix, suffix = field.option_box.resolve_affix(affix, default="suffix")

        # Indeterminate marquee + per-object text in OUR footer (mirrors mayatk's
        # twin): a Cycles bake reports no sub-progress, so a percentage would sit
        # at 0 and jump, but the text still says which object and how far in.
        with self.ui.footer.progress(text="Baking lightmaps…") as update:
            result = self._baker.bake(
                objects,
                packing=self._packing(),
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
        self.ui.footer.setText(self._bake_report(result))

    def _bake_report(self, result) -> str:
        """The footer line for a :class:`LightmapBakeResult` (mirrors mayatk).

        A refusal says why; an empty bake says where to look; a bake says how
        many objects it baked and where to, then what it left alone and
        whether its level looks wrong.
        """
        if result.refused:
            self._last_output_dir = None
            return result.refused
        if not result:
            self._last_output_dir = None
            return "Bake produced no output (see the console)."
        self._last_output_dir = os.path.dirname(next(iter(result.maps.values())))
        if self._packing() == "atlas":
            atlases = len(result.files)
            tail = (
                f"Consolidated into {atlases} atlas{'es' if atlases != 1 else ''} by "
                f"material. {self._LIGHTING_ONLY_TAIL}"
            )
        else:
            tail = self._LIGHTING_ONLY_TAIL
        count = len(result.maps)
        notes = [
            f"Baked {count} object{'s' if count != 1 else ''} → "
            f"{self._last_output_dir}. {tail}"
        ]
        if result.unbaked:
            notes.append(
                f" {len(result.unbaked)} not baked (cancelled or failed); "
                "they keep the lightmap they had."
            )
        if result.verdict:
            notes.append(f"  WARNING: {result.verdict}")
        return "".join(notes)

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
