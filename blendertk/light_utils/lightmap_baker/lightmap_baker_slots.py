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
from typing import Any, Callable, Dict, List, Optional, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.mat_utils.bake_sets import (
    LightmapBakeSet,
    LightmapExcludeSet,
    LightmapIsolateSet,
)
from blendertk.mat_utils.texture_baker import TextureBaker
from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker


class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin):
    """Switchboard slots for the co-located ``lightmap_baker.ui`` panel.

    A thin driver over :class:`LightmapBaker` (composition; no bake logic here,
    and no bake policy), mirroring mayatk's ``LightmapBakerSlots`` control for
    control. **Bake Lightmaps** (``b000``) hands the objects in Scope and the
    dials to :meth:`LightmapBaker.bake`, which checks the scene, bakes, keeps the
    full PBR material, puts lighting on UV1 and stamps the engine metadata on the
    shared ``data_export`` carrier; the panel reports what came back.

    Nothing is reverted before a bake. An object in the file's Exclude set
    (:class:`LightmapExcludeSet`, edited by the Exclude row) keeps any map it has
    and still lights the rest, and so does an object the bake does not finish.
    An object in its Isolate set (:class:`LightmapIsolateSet`, the Isolate row)
    bakes in a pass of its own, out of every other object's render.
    The header menu's **Revert to Source** undoes the wiring, once confirmed.

    The **Preset** combo (``cmb000``) is uitk's preset template
    (:meth:`PresetManager.wire_combo`) in semantic mode over
    :meth:`LightmapBaker.preset_store` -- the store
    :meth:`LightmapBaker.from_preset` reads, so a preset saved here is also a
    headless bake recipe (and the Maya bridge's Quality tier). :meth:`_preset_fields`
    is the one map between its keys and the widgets, which stay the source of
    truth at bake time.
    """

    # Packing labels for the Packing combobox (cmb002). Atlas by Material
    # (index 1, the default) consolidates a material group into one shared EXR
    # + a per-object scaleOffset rect; Per-Object keeps one full-resolution map
    # each; _packing() reads it back.
    _PACKING_LABELS = ("Per-Object (one map each)", "Atlas by Material (shared map)")

    # Fixed lightmap sizes (square, px) for the Resolution combobox
    # (cmb_resolution). Power-of-two atlas sizes; every Quality preset lands on
    # one of these. _resolution() reads the selection back as an int.
    _RESOLUTIONS = (256, 512, 1024, 2048, 4096)

    # Scope labels for the Scope combobox (cmb_scope): which objects b000 bakes.
    # Selected (index 0, default) preserves the prior selection-only behavior;
    # _scope() / _scope_objects() resolve it to the mesh objects to bake.
    # Changed (LightmapBaker.rebake_changed: what changed since the last bake,
    # and what that shades) is APPENDED: the combo persists by INDEX, so a
    # saved choice of the first three reopens as itself (mirror of mayatk's).
    _SCOPE_LABELS = ("Selected", "Visible", "Scene", "Changed")

    #: The panel's switches, ``{preset key: (field, default)}``. Each rides the
    #: option box of the field it QUALIFIES rather than a checkbox row of its
    #: own: the lights that light the bake are part of what Scope gathers --
    #: every scene light or the Scope's own, the world, the hidden ones --
    #: adaptive sampling is how the Samples are spent, denoise is what the map
    #: ships at that Resolution, Beside Material Textures redirects the
    #: Output Directory, and the reflection probe is what the bake ships beside
    #: the maps Packing lays out (mirror of mayatk's). A field carrying several
    #: holds them in this order (:meth:`_toggle`).
    #:
    #: The keys are the preset store's (:attr:`LightmapBaker.PRESET_BOOL_KEYS`),
    #: so :meth:`_preset_fields` builds its entries straight from here and
    #: :meth:`_wire_toggle` derives each settings key from the same name.
    _TOGGLES: Dict[str, Tuple[str, bool]] = {
        "include_scene_lights": ("cmb_scope", True),
        "include_environment": ("cmb_scope", True),
        "include_hidden_lights": ("cmb_scope", False),
        "adaptive": ("spn_samples", True),
        "denoise": ("cmb_resolution", True),
        "beside_textures": ("txt_output_dir", False),
        "reflection_probe": ("cmb002", True),
    }

    #: The tier a panel opened for the first time shows: the .ui's dial
    #: defaults are its values, so naming it costs nothing and says which tier
    #: the untouched dials are.
    _DEFAULT_PRESET = "mobile"
    #: Settings key recording that the default preset was seeded once (see
    #: :meth:`cmb000_init`).
    _PRESET_SEEDED_KEY = "lightmap_baker_preset_seeded"

    # Footer tail for a per-object bake (mirrors mayatk's ``_LIGHTING_ONLY_TAIL``).
    _LIGHTING_ONLY_TAIL = (
        "Maps kept; lightmap + Unity metadata stamped. Export the FBX."
    )

    #: Bake-processor rows, label -> the value the baker takes (mirror of
    #: mayatk's ``_DEVICES``). Auto is first (the default): the GPU where it pays.
    _DEVICES = (("Auto", "AUTO"), ("GPU", "GPU"), ("CPU", "CPU"))

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[lightmap_baker] ")

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.lightmap_baker

        # Output dir of the most recent bake (reported in the footer).
        self._last_output_dir: Optional[str] = None
        # Workflow instance, rebuilt per bake from the current dials. commit /
        # revert persist their state on the object, so revert works even from a
        # fresh instance / reopened file.
        self._baker: Optional[LightmapBaker] = None
        # The Preset combo's manager, built by cmb000_init.
        self._presets = None

        # Deferred to the next tick: the switchboard builds this instance
        # mid-load, before child widgets (footer, combos) are wired onto self.ui.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Wire what reads several widgets at once, once all of them exist.

        Deferred from __init__ (QTimer) so every widget has run its ``*_init``
        and restored its session value first: the Preset combo's modified
        marker and the Exclude and Isolate counts. Unlike mayatk's, Adaptive Sampling is not
        gated on the processor: Cycles samples adaptively on the CPU and the GPU
        alike.
        """
        if self._presets is not None:
            # Semantic presets leave this wiring to the owner
            # (``PresetManager.connect_value_widgets`` is a no-op there): any
            # edit to a setting a preset stores re-evaluates the " *" marker.
            def refresh(*_):
                self._presets.refresh_modified_state()

            for control, _read, _write in self._preset_fields().values():
                # A panel widget announces a change on the switchboard's
                # default signal for its type; a field's switch is an
                # option-box option, not a widget, and has ``toggled``.
                signal = getattr(control, "default_signals", lambda: "toggled")()
                if control is not None and signal:
                    getattr(control, signal).connect(refresh)
            # The widgets restored their session values with signals blocked.
            self._presets.refresh_modified_state()

        # The Exclude and Isolate sets live in the file, so the count on each
        # label must follow the file: another one opened, a Set undone.
        # Blender reports a new file and an opened one as the same event
        # (``load_post``).
        from blendertk.core_utils.script_job_manager import ScriptJobManager

        jobs = ScriptJobManager.instance()
        for event in ("SceneOpened", "Undo", "Redo"):
            try:
                jobs.subscribe(event, self._refresh_scene_sets, owner=self)
            except Exception as error:  # noqa: BLE001 -- never block the panel
                self.logger.debug(f"scene event {event!r} unavailable ({error})")
        jobs.connect_cleanup(self.ui, owner=self)
        self._refresh_scene_sets()

    def _refresh_scene_sets(self) -> None:
        """Re-read the file sets onto their labels (Exclude, Isolate, Bake Set)."""
        for row in self._SET_ROWS:
            self._refresh_set_row(row)
        self._refresh_bake_set()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure the header chrome (menu / collapse / hide), menu, help text."""
        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Revert to Source",
            setObjectName="revert_to_source",
            setToolTip="Remove the lightmap wiring from the selected objects, or "
            "from every baked object when nothing is selected. Asks first; the "
            "baked EXR files stay on disk.",
        )
        widget.menu.add(
            "QPushButton",
            setText="List Changed Objects",
            setObjectName="list_changed_objects",
            setToolTip="Select what a Changed bake would re-bake, and list each "
            "object with why (moved, deformed, a neighbour changed, the "
            "lighting changed ...) in the console. Nothing is baked.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Open Output Folder",
            setObjectName="open_output",
            setToolTip="Open the folder the lightmaps are written to (where the "
            "last bake wrote, else the Output Directory field) in the file manager.",
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Lightmap Baker",
                body="Bakes the scene's lighting with Cycles into a lightmap per "
                "object (or an atlas per material) and wires it for the engine in "
                "one step. Materials and texture UVs are never changed: each mesh "
                "samples its lightmap on a second UV channel, and the engine "
                "multiplies it with the albedo.",
                steps=[
                    "<b>Scope</b>: the Selected meshes, every Visible one, the "
                    "whole Scene, or what <b>Changed</b> since the last bake -- "
                    "moved, deformed, re-lit, and the objects near enough to "
                    "be shaded by it -- each written back into its own atlas "
                    "cell (the footer says what, the menu lists it). Its "
                    "buttons say what lights the bake: "
                    "<b>Include Scene Lighting</b> (the light) bakes with every "
                    "scene light, off only the Scope's own; the sphere takes the "
                    "world (an HDRI environment) in or out; the eye switches "
                    "hidden lights on for the bake. <b>Exclude</b> keeps objects from "
                    "getting a map of their own but leaves them in the render: they "
                    "still cast shadows and bounce light onto everything that bakes. "
                    "<b>Isolate</b> is for moving parts that still get a map (a "
                    "door on its cabinet): each bakes in a pass of its own and "
                    "stays out of every other object's render, so it leaves no "
                    "shadow behind.",
                    "<b>Bake Set</b>: with <i>Save After Bake</i> on, every bake "
                    "stores the objects it acted on as a set in the file, so "
                    "selecting it re-runs that bake. Its tag button names the "
                    "set with the naming convention's Set suffix (the Naming "
                    "panel's, <i>_SET</i> by default).",
                    "<b>Packing</b>: one atlas per material (the default), or one "
                    "map per object. Its camera button also captures the room as a "
                    "<b>Reflection Probe</b>, for the reflections a lightmap cannot "
                    "hold. <b>Processor</b>: which one Cycles bakes on.",
                    "<b>Quality</b>: Resolution, Samples and Bounces. Each dial "
                    "carries its own switch: <b>Denoise</b> runs Blender's denoiser "
                    "over every map (Cycles does not denoise a bake itself), "
                    "<b>Adaptive Sampling</b> lets each texel stop once it is clean.",
                    "<b>Output</b>: empty writes to the workspace's texture folder; "
                    "the image button saves each map beside its material's texture "
                    "maps.",
                    "The group at the bottom runs the bake. <b>Preset</b>: pick "
                    "one, or set the dials above and save your own (the disk "
                    "icon). <b>Reset to Defaults</b> puts every setting back; "
                    "Shift+Click makes the current ones your defaults.",
                    "<b>Bake Lightmaps</b>, then export the FBX with <b>Custom "
                    "Properties</b> on, so the hidden <i>data_export</i> Empty "
                    "carries the engine wiring.",
                ],
                notes=[
                    "Re-baking replaces the earlier maps. <b>Revert to Source</b> "
                    "(this menu) removes the wiring; the EXR files stay on disk.",
                    "Cycles ships with Blender; the Maya bridge's lightmap bake "
                    "runs this same baker.",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Preset combobox (uitk preset template, semantic mode)
    # ------------------------------------------------------------------

    def cmb000_init(self, widget) -> None:
        """Wire the Preset combo: uitk's preset template over the shared store.

        Semantic mode (``value_provider`` / ``value_applier``): a preset is the
        store's ``{key: value}`` dict, not a widget snapshot, so the file this
        panel saves is the one :meth:`LightmapBaker.from_preset` reads -- and
        the shipped tiers keep working here. The combo restores only its
        SELECTION, from the store's active pointer; every dial restores its own
        session value, so nothing is re-applied at open. That pointer owns the
        selection outright: a restored index would second-guess it against a
        list that grows whenever a preset is saved. Mirror of mayatk's.
        """
        from uitk.managers.preset_manager import PresetManager

        store = LightmapBaker.preset_store()
        self._presets = PresetManager(
            preset_dir=str(store.user_dir),
            builtin_dir=str(store.builtin_dir) if store.builtin_dir else None,
            value_provider=self._preset_values,
            value_applier=self._apply_preset_values,
        )
        # The manager's user-facing lines (a preset that fails to load) reach
        # this panel's log rather than only the console.
        self._presets.use_logger(self.logger)
        # A pointer left on a retired tier name ("quest", now "mobile") names a
        # preset the store no longer has; follow the rename rather than open on
        # no selection.
        active = self._presets.active_preset
        if active and not self._presets.exists(active):
            current = LightmapBaker._resolve_retired_preset(active)
            if current != active:
                self._presets.active_preset = current
        # Seeded ONCE per machine: a reset (or deleting the active user preset)
        # clears the pointer, and read as "never set" it would be reseeded on
        # the next open -- the reset values then showing as that preset,
        # modified ("mobile *"), which is what ``_after_reset`` exists to stop.
        settings = getattr(self.ui, "settings", None)
        seeded = bool(settings and settings.value(self._PRESET_SEEDED_KEY, False))
        if (
            not seeded
            and self._presets.active_preset is None
            and self._presets.exists(self._DEFAULT_PRESET)
        ):
            self._presets.active_preset = self._DEFAULT_PRESET
        if settings is not None and not seeded:
            settings.setValue(self._PRESET_SEEDED_KEY, True)
        widget.restore_state = False
        self._presets.wire_combo(widget, placeholder="Preset…")

    def btn_reset_defaults_init(self, widget) -> None:
        """Wire Reset to Defaults to uitk's shared reset grammar.

        Click restores the defaults, Shift+Click makes the current values the
        defaults and Ctrl+Shift+Click forgets those -- the grammar a per-field
        reset option already teaches. The scope is every widget the window's
        state manages; the Exclude set is file data rather than a widget value,
        so a reset leaves it standing. Mirror of mayatk's.
        """
        from uitk.managers.reset_gesture import ResetGesture

        self._reset_gesture = ResetGesture(widget, on_performed=self._after_reset)

    def _after_reset(self, action: str) -> None:
        """Let go of the active preset when a reset moved the dials off it.

        The values are the defaults now, not the preset the combo still names;
        keeping the pointer would show them as that preset, modified. Saving the
        current values as the defaults (Shift+Click) moves no dial, so it keeps
        the selection.
        """
        from uitk.managers.reset_gesture import ResetGesture

        presets = getattr(self, "_presets", None)
        if presets is None or action == ResetGesture.SAVE:
            return
        presets.active_preset = None
        presets.refresh_combo()

    def _preset_fields(self) -> Dict[str, Tuple[Any, Callable[[], Any], Callable]]:
        """``{preset key: (control, read, write)}`` -- the one map between the
        preset store's keys and this panel's controls. Save reads through it
        (:meth:`_preset_values`), a load writes through it
        (:meth:`_apply_preset_values`) and each control's change re-evaluates
        the modified marker (:meth:`_initialize_ui`), so a key added here is
        saved, loaded and marked, or none of the three. The keys are
        :attr:`LightmapBaker.PRESET_INT_KEYS` / ``PRESET_BOOL_KEYS`` plus the
        panel's own ``packing``.
        """
        ui = self.ui
        fields: Dict[str, Tuple[Any, Callable[[], Any], Callable]] = {
            "packing": (ui.cmb002, self._packing, self._set_packing),
            "resolution": (ui.cmb_resolution, self._resolution, self._set_resolution),
            "samples": (
                ui.spn_samples,
                ui.spn_samples.value,
                lambda v: ui.spn_samples.setValue(int(v)),
            ),
            "bounces": (
                ui.spn_bounces,
                ui.spn_bounces.value,
                lambda v: ui.spn_bounces.setValue(int(v)),
            ),
        }
        for key in self._TOGGLES:
            fields[key] = (
                self._toggle(key),
                lambda key=key: self._toggle_state(key),
                lambda value, key=key: self._set_toggle_state(key, value),
            )
        return fields

    def _preset_values(self) -> Dict[str, Any]:
        """The panel's bake settings, keyed as the preset store keys them."""
        return {
            key: read()
            for key, (_control, read, _write) in self._preset_fields().items()
        }

    def _apply_preset_values(self, data: Dict[str, Any]) -> int:
        """Write a preset's values onto the widgets; returns how many applied.

        Overlay semantics: a key the preset lacks keeps its widget's value -- a
        shipped tier stores only the quality dials, so loading one leaves the
        switches as the user set them. Unknown keys (``description``) are
        skipped. Signals are left on, so the session state records the loaded
        values: that is what the next session restores the dials from.
        """
        fields = self._preset_fields()
        applied = 0
        for key, value in data.items():
            field = fields.get(key)
            if field is None:
                continue
            _control, _read, write = field
            try:
                write(value)
            except (TypeError, ValueError) as error:
                self.logger.warning(f"Preset value {key}={value!r} skipped: {error}")
                continue
            applied += 1
        return applied

    # ------------------------------------------------------------------
    # Switches (option-box toggles)
    # ------------------------------------------------------------------

    def _wire_toggle(self, widget, key: str, icon: str, on: str, off: str, **kwargs):
        """Hang the *key* switch off *widget*'s option box as a toggle button.

        One shape for every entry in :attr:`_TOGGLES` (mirror of mayatk's). Off
        takes the neutral "locked" tint rather than a toggle's default error red:
        none of these switches stops the panel working. The settings key is
        explicit and panel-scoped -- the auto-derived one is the field's own
        objectName, which another panel in the same host would share.

        Parameters:
            widget: The field the switch rides.
            key: Its :attr:`_TOGGLES` entry; names the default and the key.
            icon: uitk icon name for the button.
            on: Tooltip while on -- what it is doing, then what a click does.
            off: Tooltip while off, the same way round.
            kwargs: Forwarded to ``set_toggle`` (e.g. ``on_toggled``,
                ``icon_off``). A field's first switch replaces what it carried;
                the next are added beside it, in :attr:`_TOGGLES` order.
        """
        kwargs.setdefault("replace", self._field_switches(key)[0] == key)
        widget.option_box.set_toggle(
            icon=icon,
            tooltip_on=on,
            tooltip_off=off,
            initial=self._TOGGLES[key][1],
            disabled_color=ptk.Palette.status()["locked"][0],
            settings_key=f"lightmap_baker_{key}",
            **kwargs,
        )

    def _field_switches(self, key: str) -> List[str]:
        """The :attr:`_TOGGLES` keys riding *key*'s field, in their order."""
        field = self._TOGGLES[key][0]
        return [k for k, (f, _default) in self._TOGGLES.items() if f == field]

    def _toggle(self, key: str):
        """The :attr:`_TOGGLES` switch *key*, or ``None`` before its field's
        ``_init`` has built it (the preset machinery reads this map while the
        panel is still loading). A field carrying several switches (Scope's
        three) holds them in :attr:`_TOGGLES` order, so each is read by its
        place among the field's switches -- options with an on/off state."""
        from uitk.widgets.optionBox.options.toggle import ToggleOption

        widget = getattr(self.ui, self._TOGGLES[key][0], None)
        shared = self._field_switches(key)
        try:
            if len(shared) == 1:
                return widget.option_box.find_option(ToggleOption)
            switches = [
                option
                for option in widget.option_box.get_options()
                if hasattr(option, "is_on")
            ]
        except Exception:  # noqa: BLE001 -- no option box on a bare widget
            return None
        index = shared.index(key)
        return switches[index] if index < len(switches) else None

    def _toggle_state(self, key: str) -> bool:
        """Whether the *key* switch is on (its shipped default until wired)."""
        toggle = self._toggle(key)
        return self._TOGGLES[key][1] if toggle is None else bool(toggle.is_on)

    def _set_toggle_state(self, key: str, value: bool) -> None:
        """Set the *key* switch (a preset load)."""
        toggle = self._toggle(key)
        if toggle is not None:
            toggle.set_on(bool(value))

    # ------------------------------------------------------------------
    # Scope, Exclude, Isolate, Packing
    # ------------------------------------------------------------------

    def cmb002_init(self, widget) -> None:
        """Populate the Packing combobox (Atlas by Material is the default) and
        hang the Reflection Probe switch off it.

        One shared map per material is what an engine wants: a glTF material
        carries ONE lightmap, so per-object maps force a material copy per object
        downstream. Per-Object is the opt-out, for a hero asset that earns a full
        map of its own. Packing is what the bake ships, and the probe is the one
        file it ships beside the maps (:meth:`LightmapBaker.bake_probe`). Mirror
        of mayatk's (and of the Maya bridge's default).
        """
        widget.clear()
        widget.addItems(self._PACKING_LABELS)
        widget.setCurrentIndex(1)  # Atlas by Material — one shared map each
        self._wire_toggle(
            widget,
            "reflection_probe",
            icon="camera",
            on="Reflection Probe: the bake also renders the room it lit as an "
            "HDR from one point and ships it beside the maps. A lightmap holds "
            "no reflections, so a baked metal shows only what it reflects, and "
            "an object the bake leaves out is lit by nothing of the room: the "
            "WebXR preview and the GLB light both from the probe. Click to "
            "ship the maps alone.",
            off="No reflection probe: baked metals reflect the viewer's stock "
            "studio rather than this room, and objects the bake leaves out are "
            "lit by it. Click to capture the room with the bake.",
        )

    def _packing(self) -> str:
        """``"atlas"`` or ``"per_object"`` from the Packing combobox (default per_object)."""
        text = (self.ui.cmb002.currentText() or "").lower()
        return "atlas" if "atlas" in text else "per_object"

    def _set_packing(self, value: str) -> None:
        """Select the Packing row for ``"atlas"`` / ``"per_object"``."""
        self.ui.cmb002.setCurrentIndex(1 if value == "atlas" else 0)

    def _reflection_probe(self) -> bool:
        """Whether the bake captures the room as a reflection probe (the
        Packing switch)."""
        return self._toggle_state("reflection_probe")

    def cmb_scope_init(self, widget) -> None:
        """Populate the Scope combobox (Selected is the default) and hang the
        three lighting switches off it (mirror of mayatk's). Changed, last, is
        what changed since the last bake (:meth:`cmb_scope` says what in the
        footer).

        Scope is what the bake gathers, and what lights it is part of that
        (:meth:`LightmapBaker._bake_lights`): **Include Scene Lighting** --
        every scene light, or only the Scope's own -- **Include Environment**
        -- the world, by its own switch whatever the first says -- and
        **Include Hidden Lights** -- the hidden ones of that set switched on
        for the run. Each answers alone, so none greys another out.
        """
        widget.clear()
        widget.addItems(self._SCOPE_LABELS)
        widget.setCurrentIndex(0)  # Selected — the prior selection-only behavior
        self._wire_toggle(
            widget,
            "include_scene_lights",
            icon="light",
            on="Include Scene Lighting: every light in the scene lights the "
            "bake, wherever it sits. Click to bake with only the lights in the "
            "Scope (selected, or under what is selected).",
            off="Scope lighting only: the lights in the Scope -- selected, or "
            "under what is selected -- light the bake; every other scene light "
            "is hidden from the render for it and shown again after. The world "
            "has its own button. Click to bake with every scene light.",
        )
        self._wire_toggle(
            widget,
            "include_environment",
            icon="theme",
            on="Include environment: the scene's world (an HDRI environment) "
            "lights the bake along with its lights, in the Scope or not. Click "
            "to bake the room's own lights only.",
            off="Environment excluded: the world is detached for the bake and "
            "restored afterwards. An HDRI is often a backdrop or a look-dev "
            "convenience rather than the room's real lighting, and baking it in "
            "is a flat ambient lift that cannot be taken back out of the map. "
            "Click to bake it in.",
        )
        self._wire_toggle(
            widget,
            "include_hidden_lights",
            icon="eye",
            icon_off="eye_off",
            on="Include Hidden Lights: the hidden lights the bake uses are "
            "switched on for it -- one its collection hides too, without "
            "revealing anything else the collection holds -- and hidden again "
            "after. Click to leave hidden lights off.",
            off="Hidden lights stay off: a light hidden from the render, by its "
            "own flag or a collection's, does not light the bake. Click to "
            "switch them on for the bake.",
        )

    def _scope(self) -> str:
        """``"selected"`` (default), ``"visible"``, ``"scene"`` or ``"changed"``
        from cmb_scope."""
        return (self.ui.cmb_scope.currentText() or "Selected").split()[0].lower()

    def cmb_scope(self, index) -> None:
        """Choosing Changed says what a bake would redo, in the footer.

        The dry run (:meth:`LightmapBaker.stale_report`, built with the
        panel's dials -- :meth:`_make_baker` -- so it reads the lights the
        bake would): nothing is baked. The other scopes say nothing here.
        Mirror of mayatk's.
        """
        if self._scope() != "changed":
            return
        try:
            report = self._make_baker().stale_report()
        except Exception as error:  # noqa: BLE001 -- a combo change never raises
            self.logger.warning(f"Could not read what changed: {error}")
            self.ui.footer.setText("Could not read what changed (see the console).")
            return
        self.ui.footer.setText(report.summary())

    def _scope_objects(self) -> List[Any]:
        """The mesh objects the current Scope names (before the Exclude set).

        ``visible`` and ``scene`` gather mesh objects across the scene so a bake
        needn't be preceded by a manual select-all; ``selected`` takes the
        selection. Every scope resolves through ``TextureBaker.resolve_meshes``
        -- the baker's own "what counts as a bakeable mesh" -- so a selection
        that also holds the room's lights bakes the geometry, and a lights-only
        selection reads as nothing to bake.

        The words and their precedence are :class:`pythontk.HandoffScope`'s
        (``scene`` is its synonym for ``all``; anything unknown bakes the
        selection); this supplies only the scene reads. ``changed`` is the
        panel's own word, answered first: what
        :meth:`LightmapBaker.stale_report` names -- which Bake hands
        :meth:`LightmapBaker.rebake_changed` rather than a list.
        """
        if self._scope() == "changed":
            import bpy

            return [
                bpy.data.objects[name]
                for name in self._make_baker().stale_report().targets
                if name in bpy.data.objects
            ]

        def scene_meshes():
            import bpy

            return TextureBaker.resolve_meshes(list(bpy.context.scene.objects))

        def visible_meshes():
            # the window's layer (windowless, a bare visible_get reads the default),
            # resolved once: each resolve enters a context override
            vl = CoreUtils._active_view_layer()
            return [o for o in scene_meshes() if o.visible_get(view_layer=vl)]

        return ptk.HandoffScope.resolve(
            self._scope(),
            selected=lambda: TextureBaker.resolve_meshes(
                CoreUtils.selected_objects(), descendants=True
            ),
            all=scene_meshes,
            visible=visible_meshes,
        )

    def _scope_lights(self) -> List[Any]:
        """The light objects the current Scope holds -- what lights a bake with
        Include Scene Lighting off (mirror of mayatk's).

        The bake's targets are meshes (:meth:`_scope_objects`), so its lights
        are handed to it beside them: the lights selected or under the
        selection (:meth:`LightmapBaker._scope_lights`), every visible one, or
        every one in the scene -- the same words, read for lights.
        """

        def scene_lights():
            import bpy

            return [o for o in bpy.context.scene.objects if o.type == "LIGHT"]

        def visible_lights():
            vl = CoreUtils._active_view_layer()
            return [o for o in scene_lights() if o.visible_get(view_layer=vl)]

        return list(
            ptk.HandoffScope.resolve(
                self._scope(),
                selected=lambda: LightmapBaker._scope_lights(None),
                all=scene_lights,
                visible=visible_lights,
            )
            or []
        )

    #: The panel's file-set rows -- Exclude and Isolate -- by button: a label,
    #: one Set From Selection button, Select / Clear on its option box, and a
    #: live hover. One implementation (:meth:`_init_set_row` and the
    #: ``_*_set_row`` methods); a row is its set and its words. Mirror of
    #: mayatk's.
    _SET_ROWS: Dict[str, Dict[str, Any]] = {
        "set_exclusions": dict(
            scene_set=LightmapExcludeSet,
            label="lbl_exclude",
            title="Exclude",
            verb="excluded",
            effect="they still light the rest.",
            clear_tip="Clear the Exclude set: every object in Scope bakes again. "
            "The objects themselves are untouched.",
            cleared="Exclude set cleared — every object in Scope bakes.",
        ),
        "set_isolation": dict(
            scene_set=LightmapIsolateSet,
            label="lbl_isolate",
            title="Isolate",
            verb="isolated",
            effect="each bakes alone, out of every other object's render.",
            clear_tip="Clear the Isolate set: every object bakes in one pass, "
            "each in the others' render. The objects themselves are untouched.",
            cleared="Isolate set cleared — every object bakes in one pass.",
        ),
    }

    def _init_set_row(self, widget, row: str) -> None:
        """Hang Select / Clear off a file-set row's button, and make its hover live.

        The row mirrors mayatk's (and the Marmoset bridge's Bake Source row):
        Set From Selection is the button, Select and Clear its option-box icons.
        The set lives in the file, so the hover lists its CURRENT members
        instead of the ones the panel opened on; it wraps each widget's own help
        text.
        """
        spec = self._SET_ROWS[row]
        widget.option_box.add_action(
            callback=lambda: self._select_set_row(row),
            icon="select",
            tooltip=f"Select the {spec['verb']} objects.",
            settings_key=False,
        )
        widget.option_box.add_action(
            callback=lambda: self._clear_set_row(row),
            icon="clear",
            tooltip=spec["clear_tip"],
            settings_key=False,
        )
        for target in (widget, getattr(self.ui, spec["label"])):
            help_text = target.toolTip()
            self.sb.tooltip.bind(
                target, lambda text=help_text: self._set_row_tooltip(row, text)
            )

    def _set_row_tooltip(self, row: str, help_text: str) -> str:
        """*help_text* over the meshes a row's set holds, live.

        The meshes, not the set's members: an Empty stored in the set reads as
        what the bake will actually act on -- the same count the label shows.
        """
        spec = self._SET_ROWS[row]
        try:
            meshes = spec["scene_set"].meshes()
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return help_text
        return self.sb.tooltip.stored_items(
            meshes,
            body=help_text.replace("\n", "<br>"),
            formatter=lambda obj: obj.name,
            noun=f"mesh(es) {spec['verb']} in this file",
            empty_text=f"Nothing is {spec['verb']} in this file.",
        )

    def _refresh_set_row(self, row: str) -> None:
        """Show a row's set's mesh count on its label (``Exclude (3):``).

        The count is of MESHES -- what the bake acts on -- so an Empty reads as
        everything under it. Runs on the panel's own edits and on file open /
        undo / redo (see :meth:`_initialize_ui`).
        """
        spec = self._SET_ROWS[row]
        label = getattr(self.ui, spec["label"], None)
        if label is None:
            return
        try:
            count = len(spec["scene_set"].meshes())
        except Exception:  # noqa: BLE001 -- a label refresh must never raise
            count = 0
        title = spec["title"]
        label.setText(f"{title} ({count}):" if count else f"{title}:")

    def _define_set_row(self, row: str) -> None:
        """Make the selection a row's set; an empty selection clears it."""
        spec = self._SET_ROWS[row]
        members = spec["scene_set"].define()
        self._refresh_set_row(row)
        if not members:
            self.ui.footer.setText(
                f"Nothing selected — the {spec['title']} set is cleared."
            )
            return
        count = len(spec["scene_set"].meshes())
        self.ui.footer.setText(
            f"{count} mesh{'es' if count != 1 else ''} {spec['verb']}; {spec['effect']}"
            if count
            else f"{spec['title']} set stored, but it holds no meshes -- nothing "
            f"is {spec['verb']}."
        )

    def _select_set_row(self, row: str) -> None:
        """Select a row's set's members.

        A member outside the active view layer cannot be selected at all
        (``select_set`` raises there), and a hidden one refuses; both are
        counted rather than forced -- unhiding geometry to satisfy a *select*
        would change the scene the set promises to leave alone.
        """
        from blendertk.edit_utils.selection import Selection

        spec = self._SET_ROWS[row]
        members = spec["scene_set"].members()
        self._refresh_set_row(row)
        if not members:
            self.ui.footer.setText(f"Nothing is {spec['verb']} in this file.")
            return
        # The selection menu's own replace, which skips what cannot be selected;
        # a hidden member refuses without raising, so read back what landed. Both
        # in the window's view layer (windowless, the context's is the default).
        with CoreUtils.window_context_override():
            Selection._apply_selection_mode(members, "replace")
            selected = []
            for obj in members:
                try:
                    if obj.select_get():
                        selected.append(obj)
                except RuntimeError:  # outside the active view layer
                    continue
        unreachable = len(members) - len(selected)
        self.ui.footer.setText(
            f"Selected {len(selected)} {spec['verb']} object"
            f"{'s' if len(selected) != 1 else ''}."
            + (
                f" {unreachable} could not be selected (hidden, or outside the "
                f"view layer); they stay {spec['verb']}."
                if unreachable
                else ""
            )
        )

    def _clear_set_row(self, row: str) -> None:
        """Remove a row's set; its objects are left untouched."""
        spec = self._SET_ROWS[row]
        if not spec["scene_set"].exists():
            self.ui.footer.setText(f"Nothing is {spec['verb']} in this file.")
            return
        spec["scene_set"].clear()
        self._refresh_set_row(row)
        self.ui.footer.setText(spec["cleared"])

    # Exclude: no map of their own, still in the render (LightmapExcludeSet).
    def set_exclusions_init(self, widget) -> None:
        """Hang Select / Clear off the Exclude row (:meth:`_init_set_row`)."""
        self._init_set_row(widget, "set_exclusions")

    def set_exclusions(self) -> None:
        """Make the selection the Exclude set; an empty selection clears it."""
        self._define_set_row("set_exclusions")

    def select_exclusions(self) -> None:
        """Select the Exclude set's members (:meth:`_select_set_row`)."""
        self._select_set_row("set_exclusions")

    def clear_exclusions(self) -> None:
        """Remove the Exclude set; its objects are left untouched."""
        self._clear_set_row("set_exclusions")

    def _exclusions_tooltip(self, help_text: str) -> str:
        """*help_text* over the meshes the Exclude set keeps from baking, live."""
        return self._set_row_tooltip("set_exclusions", help_text)

    # Isolate: each bakes alone, out of every other's render (LightmapIsolateSet).
    def set_isolation_init(self, widget) -> None:
        """Hang Select / Clear off the Isolate row (:meth:`_init_set_row`)."""
        self._init_set_row(widget, "set_isolation")

    def set_isolation(self) -> None:
        """Make the selection the Isolate set; an empty selection clears it."""
        self._define_set_row("set_isolation")

    def select_isolation(self) -> None:
        """Select the Isolate set's members (:meth:`_select_set_row`)."""
        self._select_set_row("set_isolation")

    def clear_isolation(self) -> None:
        """Remove the Isolate set; its objects are left untouched."""
        self._clear_set_row("set_isolation")

    # ------------------------------------------------------------------
    # Bake Set
    # ------------------------------------------------------------------

    #: Settings keys of the Bake Set row's two switches (mayatk's own keys):
    #: Save After Bake (the global switch) and the naming-convention spelling.
    _BAKE_SET_SAVE_KEY = "lightmap_baker_save_bake_set"
    _BAKE_SET_CONVENTION_KEY = "lightmap_baker_bake_set_convention"

    def select_bake_set_init(self, widget) -> None:
        """Hang Save After Bake, the naming switch and Clear off the Bake Set row.

        Mirror of mayatk's row. **Save After Bake** (the disk) is the global
        switch: while it is on, every bake saves the objects it acted on as the
        file's :class:`LightmapBakeSet`. The **tag** names that collection plain
        (``lightmapBaker_baked``) or by the naming convention's Set affix,
        renaming one already in the file on the spot. The button selects the
        set; Clear removes it. Added in that order (:meth:`_bake_set_switch`).
        """
        locked = ptk.Palette.status()["locked"][0]
        widget.option_box.set_toggle(
            icon="save",
            tooltip_on="Save After Bake: every bake replaces this file's Bake Set "
            "with the objects it acted on, finished or not -- select it to "
            "re-run the bake. One setting for every file. Click to stop.",
            tooltip_off="Bakes save no set. Click to have every bake store the "
            "objects it acted on as this file's Bake Set.",
            initial=False,
            disabled_color=locked,
            settings_key=self._BAKE_SET_SAVE_KEY,
        )
        convention = ptk.NamingConvention.affix(LightmapBakeSet.CONVENTION_KEY)
        widget.option_box.add_toggle(
            icon="tag",
            tooltip_on="Named by the naming convention: "
            f"{LightmapBakeSet.conventional_name()} (the Naming panel's Set "
            f"suffix, {convention or 'empty'}). Click to name it plain: "
            f"{LightmapBakeSet.SET_NAME}.",
            tooltip_off=f"Named plain: {LightmapBakeSet.SET_NAME}. Click to name "
            f"it by the naming convention's Set suffix ({convention or 'empty'}).",
            initial=False,
            disabled_color=locked,
            settings_key=self._BAKE_SET_CONVENTION_KEY,
            on_toggled=self._respell_bake_set,
        )
        widget.option_box.add_action(
            callback=self.clear_bake_set,
            icon="clear",
            tooltip="Remove the file's Bake Set. The objects themselves are untouched.",
            settings_key=False,
        )
        for target in (widget, self.ui.lbl_bake_set):
            help_text = target.toolTip()
            self.sb.tooltip.bind(
                target, lambda text=help_text: self._bake_set_tooltip(text)
            )

    def _bake_set_switch(self, index: int) -> bool:
        """The Bake Set row's switch *index* (0 Save After Bake, 1 naming), on or off.

        A switch is an option with an on/off state (``is_on``), in the order
        :meth:`select_bake_set_init` added them. ``False`` before it is wired.
        """
        widget = getattr(self.ui, "select_bake_set", None)
        try:
            switches = [
                option
                for option in widget.option_box.get_options()
                if hasattr(option, "is_on")
            ]
        except Exception:  # noqa: BLE001 -- no option box on a bare widget
            return False
        return len(switches) > index and bool(switches[index].is_on)

    def _save_bake_set(self) -> bool:
        """Whether a bake saves its objects as the file's Bake Set (Save After Bake)."""
        return self._bake_set_switch(0)

    def _bake_set_conventional(self) -> bool:
        """Whether the Bake Set is named by the naming convention (the row's tag switch)."""
        return self._bake_set_switch(1)

    def _respell_bake_set(self, conventional: bool) -> None:
        """Rename the file's Bake Set to follow the tag switch, when there is one."""
        before = LightmapBakeSet.name()
        name = LightmapBakeSet.respell(bool(conventional))
        self._refresh_bake_set()
        if name and name != before:
            self.ui.footer.setText(f"Bake Set renamed: {before} -> {name}.")

    def _bake_set_tooltip(self, help_text: str) -> str:
        """*help_text* over the objects the file's Bake Set holds, live."""
        try:
            members = LightmapBakeSet.members()
            name = LightmapBakeSet.name()
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return help_text
        return self.sb.tooltip.stored_items(
            members,
            body=help_text.replace("\n", "<br>"),
            formatter=lambda obj: obj.name,
            noun=f"object(s) in {name}" if name else "object(s) in the Bake Set",
            empty_text="No Bake Set in this file.",
        )

    def _refresh_bake_set(self) -> None:
        """Show the Bake Set's member count on its label (``Bake Set (151):``)."""
        label = getattr(self.ui, "lbl_bake_set", None)
        if label is None:
            return
        try:
            count = len(LightmapBakeSet.members())
        except Exception:  # noqa: BLE001 -- a label refresh must never raise
            count = 0
        label.setText(f"Bake Set ({count}):" if count else "Bake Set:")

    def select_bake_set(self) -> None:
        """Select the Bake Set's members (as :meth:`select_exclusions` selects)."""
        from blendertk.edit_utils.selection import Selection

        members = LightmapBakeSet.members()
        self._refresh_bake_set()
        if not members:
            self.ui.footer.setText("No Bake Set in this file.")
            return
        with CoreUtils.window_context_override():
            Selection._apply_selection_mode(members, "replace")
            selected = []
            for obj in members:
                try:
                    if obj.select_get():
                        selected.append(obj)
                except RuntimeError:  # outside the active view layer
                    continue
        unreachable = len(members) - len(selected)
        self.ui.footer.setText(
            f"Selected {len(selected)} object{'s' if len(selected) != 1 else ''} "
            f"of {LightmapBakeSet.name()}."
            + (
                f" {unreachable} could not be selected (hidden, or outside the "
                "view layer)."
                if unreachable
                else ""
            )
        )

    def clear_bake_set(self) -> None:
        """Remove the file's Bake Set; its objects are left untouched."""
        if not LightmapBakeSet.exists():
            self.ui.footer.setText("No Bake Set in this file.")
            return
        name = LightmapBakeSet.name()
        LightmapBakeSet.clear()
        self._refresh_bake_set()
        self.ui.footer.setText(f"{name} removed; its objects are untouched.")

    def _record_bake_set(self, result) -> str:
        """Save *result*'s objects as the Bake Set when the panel asks to; the footer note.

        Every object the bake acted on -- baked or left unbaked -- so the set
        re-runs the bake (mirror of mayatk's). Nothing for a bake that refused
        or produced nothing.
        """
        if not (self._save_bake_set() and result):
            return ""
        objects = sorted(set(result.maps) | set(result.unbaked))
        try:
            members = LightmapBakeSet.define(
                objects, conventional=self._bake_set_conventional()
            )
        except Exception as error:  # noqa: BLE001 -- the bake is committed already
            self.logger.warning(f"Bake Set not saved: {error}")
            return " Bake Set not saved (see the log)."
        self._refresh_bake_set()
        return (
            f" Saved {len(members)} object{'s' if len(members) != 1 else ''} "
            f"as {LightmapBakeSet.name()}."
        )

    # ------------------------------------------------------------------
    # Quality
    # ------------------------------------------------------------------

    def cmb_resolution_init(self, widget) -> None:
        """Populate the Resolution combobox (value carried as item data,
        default 1024) and hang the Denoise switch off it.

        Denoise cleans the map at the size this combo sets, so the switch
        belongs to it (mirror of mayatk's).
        """
        widget.clear()
        for r in self._RESOLUTIONS:
            widget.addItem(f"Resolution:\t{r}", r)
        widget.setCurrentIndex(self._RESOLUTIONS.index(1024))
        self._wire_toggle(
            widget,
            "denoise",
            icon="filter",
            on="Denoise: every map goes through Blender's own denoiser "
            "(OpenImageDenoise) once it is baked — Cycles does not denoise a "
            "bake itself. Click to ship the bake as rendered.",
            off="Not denoising. Cycles does not denoise a bake, so each map ships "
            "its sampling noise, which reads as splotches up close. Click to "
            "denoise.",
        )

    def _resolution(self) -> int:
        """The selected lightmap resolution (px) from cmb_resolution (its item data)."""
        value = self.ui.cmb_resolution.currentData()
        return int(value) if value is not None else 1024

    def _set_resolution(self, value: int) -> None:
        """Select *value* in the Resolution combobox, snapping to the nearest fixed size."""
        nearest = min(self._RESOLUTIONS, key=lambda r: abs(r - int(value)))
        self.ui.cmb_resolution.setCurrentIndex(self._RESOLUTIONS.index(nearest))

    def spn_samples_init(self, widget) -> None:
        """Hang the Adaptive Sampling switch off the Samples field.

        Adaptive sampling does not change how MANY samples the bake may spend
        -- it decides where the Samples this field sets are spent -- so it
        qualifies this dial (mirror of mayatk's).
        """
        self._wire_toggle(
            widget,
            "adaptive",
            icon="activity",
            on="Adaptive Sampling: each texel stops once its noise is low enough, "
            "so the flat, lit ones finish early and the shadows take the rest of "
            "the Samples. Measured on a shadowed floor after the denoise every "
            "map gets: 32.6s where giving every texel the full budget took 71.7s "
            "at 1024 samples, for residual noise of 0.37% against 0.26%. Click to "
            "give every texel the full budget.",
            off="Every texel gets the full Samples: the cleanest map, and the "
            "slowest. Click to spend the budget adaptively.",
        )

    def cmb_device_init(self, widget) -> None:
        """Populate the Processor combobox (value carried as item data); default Auto."""
        widget.clear()
        for label, value in self._DEVICES:
            widget.addItem(f"Processor:\t{label}", value)
        widget.setCurrentIndex(0)  # Auto

    def _device(self) -> str:
        """The processor the bake runs on, from cmb_device (its item data)."""
        return self.ui.cmb_device.currentData() or self._DEVICES[0][1]

    def _adaptive(self) -> bool:
        """Whether the bake samples adaptively (the Samples field's switch)."""
        return self._toggle_state("adaptive")

    def _include_environment(self) -> bool:
        """Whether the bake keeps the scene's world (a Scope switch)."""
        return self._toggle_state("include_environment")

    def _include_scene_lights(self) -> bool:
        """Whether every scene light lights the bake, or only the Scope's (a Scope switch)."""
        return self._toggle_state("include_scene_lights")

    def _include_hidden_lights(self) -> bool:
        """Whether the bake switches the hidden lights on (a Scope switch)."""
        return self._toggle_state("include_hidden_lights")

    def _denoise(self) -> bool:
        """Whether the maps are denoised (the Resolution switch)."""
        return self._toggle_state("denoise")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def txt_output_dir_init(self, widget) -> None:
        """Add the folder browser and the Beside Material Textures toggle.

        No clear button (mirrors mayatk's twin): the value arrives from the
        browse dialog as often as it is typed, and a mis-click would drop a path
        the user picked and can't retype -- the field's *empty* default is one
        keystroke away anyway (see :meth:`_output_dir`).

        The toggle is a switch on the field it redirects (:attr:`_TOGGLES`):
        on, each map goes to its material's texture folder
        (:attr:`LightmapBaker.beside_textures`) and this field only takes the
        objects whose material has none.
        """
        widget.option_box.browse(
            mode="directory",
            title="Lightmap output directory",
            tooltip="Browse for the lightmap output directory…",
            start_dir=self._output_dir,
            callback=self._relativize_output_dir,
        )
        self._wire_toggle(
            widget,
            "beside_textures",
            icon="image",
            on="Beside material textures: each lightmap is saved in the folder "
            "its material's texture maps are in, named after that texture set. "
            "This field only takes the objects whose material has no texture "
            "folder inside the project (a packed or embedded image has none, and "
            "another project's is never written to). Click to save every map "
            "here instead.",
            off="Saving every lightmap to this folder. Click to save each one "
            "beside its material's texture maps instead.",
            on_toggled=self._show_output_mode,
        )
        self._show_output_mode(self._beside_textures())

    def _beside_textures(self) -> bool:
        """Whether each map is saved beside its material's texture maps."""
        return self._toggle_state("beside_textures")

    def _show_output_mode(self, beside: bool) -> None:
        """Say in the empty field where the maps will go."""
        self.ui.txt_output_dir.setPlaceholderText(
            "(blank: beside textures, else sourceimages/lightmaps)"
            if beside
            else "(blank: sourceimages/lightmaps)"
        )

    def _relativize_output_dir(self, path: str) -> None:
        """Store a browsed dir as the portable spelling of itself.

        The dialog can only hand back an absolute path; under the texture folder
        the relative one is what survives the project being moved (or a
        teammate's copy) -- see ``ptk.FileUtils.relativize_output_dir``, the
        exact inverse of the ``resolve_output_dir`` :meth:`_output_dir` reads the
        field with.
        """
        if not path:
            return
        self.ui.txt_output_dir.setText(
            ptk.FileUtils.relativize_output_dir(path, self._base_output_dir())
        )

    def _output_dir(self) -> Optional[str]:
        """The bake's output directory: the field, resolved against the texture folder.

        Empty field -> :meth:`_base_output_dir`'s ``lightmaps``
        (:attr:`LightmapBaker.OUTPUT_SUBDIR`). A subdirectory entry is joined
        onto the texture folder so the setting survives a project move; a full
        path is taken as-is -- with or without a project: it needs nothing to
        resolve against. The directory itself is created by the bake. ``None``
        for any other entry without a project (the engine's default decides,
        and refuses).
        """
        entry = self.ui.txt_output_dir.text()
        typed = ptk.FileUtils.resolve_output_dir(entry, None)
        if typed:
            return typed  # a full path
        base = self._base_output_dir()
        if not base:
            return None
        if not (entry or "").strip():
            return os.path.normpath(os.path.join(base, LightmapBaker.OUTPUT_SUBDIR))
        return ptk.FileUtils.resolve_output_dir(entry, base)

    @staticmethod
    def _base_output_dir() -> str:
        """What a relative Output Directory is relative to: the file project's
        texture folder (``DataNodes.scene_source_images_dir``), whose
        ``lightmaps`` (:attr:`LightmapBaker.OUTPUT_SUBDIR`) an empty field
        means -- exactly where a scripted bake lands on its own, and mayatk's.
        ``""`` without a project (an unsaved file, no workspace set): the
        panel then names no folder and the engine's default refuses."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_source_images_dir()

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

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _make_baker(self) -> LightmapBaker:
        """A :class:`LightmapBaker` built from the panel's dials and switches --
        the one every action builds, so the Changed dry run reads the lights,
        resolution and switches the bake will (mirror of mayatk's)."""
        return LightmapBaker(
            resolution=self._resolution(),
            samples=self.ui.spn_samples.value(),
            bounces=self.ui.spn_bounces.value(),
            device=self._device(),
            adaptive=self._adaptive(),
            include_environment=self._include_environment(),
            include_scene_lights=self._include_scene_lights(),
            include_hidden_lights=self._include_hidden_lights(),
            denoise=self._denoise(),
            beside_textures=self._beside_textures(),
            reflection_probe=self._reflection_probe(),
        )

    def b000(self) -> None:
        """Bake lightmaps for the Scope, minus the Exclude set (:meth:`LightmapBaker.bake`).

        Under Changed, re-bake what changed since the last bake and what it
        shades (:meth:`LightmapBaker.rebake_changed`), each object the way it
        was baked -- written back into its own atlas cell where it fits.
        """
        changed = self._scope() == "changed"
        objects = [] if changed else self._scope_objects()
        if not changed and not objects:
            self.ui.footer.setText(
                "Select one or more mesh objects to bake."
                if self._scope() == "selected"
                else f"No meshes found for scope '{self._scope()}'."
            )
            return

        self._baker = self._make_baker()

        out_dir = self._output_dir()
        if out_dir is None:
            self.ui.footer.setText(
                "No project to bake into — save the .blend or set a workspace."
            )
            return
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
        def progress(done, total, name):
            return update(
                None,
                f"Baking {name}…  ({min(done + 1, total)}/{total})"
                if done < total
                else f"Baked {total} object{'s' if total != 1 else ''}.",
            )

        with self.ui.footer.progress(text="Baking lightmaps…") as update:
            if changed:
                # Each the way it was baked (its recorded packing), so the
                # Packing row does not apply; the dials do.
                result = self._baker.rebake_changed(
                    output_dir=out_dir,
                    prefix=prefix,
                    suffix=suffix,
                    on_progress=progress,
                )
            else:
                result = self._baker.bake(
                    # The Scope's lights ride beside its meshes: what lights the
                    # bake with Include Scene Lighting off (the bake's targets
                    # stay the meshes).
                    list(objects)
                    + [light for light in self._scope_lights() if light not in objects],
                    packing=self._packing(),
                    output_dir=out_dir,
                    prefix=prefix,
                    suffix=suffix,
                    on_progress=progress,
                )
        self.ui.footer.setText(
            self._bake_report(result) + self._record_bake_set(result)
        )

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
        folders = result.folders
        where = (
            self._last_output_dir
            if len(folders) == 1
            else f"{len(folders)} folders (beside their textures)"
        )
        if result.in_place or result.repacked or self._packing() == "atlas":
            n = len(result.files)
            tail = (
                f"Consolidated into {n} atlas map{'s' if n != 1 else ''}; each "
                "object samples its own atlas rect at engine time. Export the FBX."
            )
        else:
            tail = self._LIGHTING_ONLY_TAIL
        count = len(result.maps)
        notes = [f"Baked {count} object{'s' if count != 1 else ''} → {where}. {tail}"]
        if result.in_place:
            n = len(result.in_place)
            notes.append(
                f" {n} written back into {'its' if n == 1 else 'their'} atlas "
                f"cell{'s' if n != 1 else ''} in place; every other object's "
                "texels kept."
            )
        if result.repacked:
            notes.append(
                " Re-packed whole: "
                + "; ".join(f"{name} ({why})" for name, why in result.repacked.items())
                + "."
            )
        if result.excluded:
            notes.append(f" {len(result.excluded)} excluded.")
        if result.retired:
            n = len(result.retired)
            notes.append(
                f" Moved {n} superseded map{'s' if n != 1 else ''} nothing reads "
                "any more to the Recycle Bin (or a temp folder kept 30 days)."
            )
        if result.unbaked:
            notes.append(
                f" {len(result.unbaked)} not baked (cancelled or failed); "
                "they keep the lightmap they had."
            )
        if result.verdict:
            notes.append(f"  WARNING: {result.verdict}")
        return "".join(notes)

    # ------------------------------------------------------------------
    # Header-menu actions
    # ------------------------------------------------------------------

    def revert_to_source(self) -> None:
        """Take the lightmaps off the selected objects (or every baked one), once confirmed.

        A header-menu item one row from the panel's other actions, and with
        nothing selected it reaches every baked object in the file -- so it says
        exactly what it will do and waits for OK (mirror of mayatk's). What it
        removes is only the wiring: the materials and texture UVs were never
        changed, and the EXR files stay on disk.
        """
        if self._baker is None:
            self._baker = LightmapBaker()
        # Mesh objects, by the bake's own rule (resolve_meshes): anything else
        # selected (an Empty, a light) has no lightmap to take off.
        raw = CoreUtils.selected_objects()
        selection = (
            [obj.name for obj in TextureBaker.resolve_meshes(raw, descendants=True)]
            if raw
            else None
        )
        targets = self._baker.baked_objects(selection) if selection != [] else []
        if not targets:
            self.ui.footer.setText(
                "None of the selected objects has a lightmap."
                if raw
                else "No baked objects to revert."
            )
            return
        count = len(targets)
        whose = "selected" if selection else "baked"
        confirmed = self.sb.confirm(
            f"<b>Revert to Source</b> &mdash; {count} {whose} "
            f"object{'s' if count != 1 else ''}"
            + ("" if selection else " (nothing is selected, so: all of them)")
            + "<br><br>Removes their lightmap wiring: each object's lightmap "
            "record and its entry in the file's lightmap export data. Their "
            "materials and texture UVs were never changed, and the baked EXR "
            "files stay on disk.<br><br>An export will carry no lightmap for "
            "them until they are baked again. One Undo restores the wiring.",
            yes="Ok",
            no="Cancel",
        )
        if not confirmed:
            self.ui.footer.setText("Revert to Source cancelled.")
            return
        reverted = self._baker.revert(selection)
        if reverted:
            self.ui.footer.setText(
                f"Reverted {count} object{'s' if count != 1 else ''} to source "
                "(lightmap wiring removed; the EXR files stay on disk)."
            )
        else:
            self.ui.footer.setText("No baked objects to revert.")

    def list_changed_objects(self) -> None:
        """Select what a Changed bake would re-bake, and list each with why.

        The dry run (:meth:`LightmapBaker.stale_report`, the panel's dials):
        one line per object in the console -- its own change, or the
        neighbour whose change reaches it -- then the lighting, the atlases
        that re-pack whole, what cannot bake and what has no baseline. The
        footer carries the one-line summary. Nothing is baked. Mirror of
        mayatk's.
        """
        try:
            report = self._make_baker().stale_report()
        except Exception as error:  # noqa: BLE001 -- a menu item never raises
            self.logger.warning(f"Could not read what changed: {error}")
            self.ui.footer.setText("Could not read what changed (see the console).")
            return
        summary = report.summary()
        lines = report.lines()
        if lines:
            self.logger.log_group(summary, lines)
        if report.targets:
            import bpy

            from blendertk.edit_utils.selection import Selection

            # The selection menu's own replace (the Exclude row's Select).
            with CoreUtils.window_context_override():
                Selection._apply_selection_mode(
                    [bpy.data.objects.get(n) for n in report.targets], "replace"
                )
        self.ui.footer.setText(summary)

    def open_output(self) -> None:
        """Open the bake's output folder in the file manager.

        The folder the last bake wrote to when there is one -- Beside Material
        Textures sends maps away from the Output Directory, which then showed
        nothing new -- else the Output Directory field's resolved target when it
        exists, else the texture folder it resolves against (mirror of mayatk's
        ``open_sourceimages``).
        """
        src = self._last_output_dir
        if not (src and os.path.isdir(src)):
            src = self._output_dir()
        if src and not os.path.isdir(src):  # not baked into yet
            src = self._base_output_dir()
        if not (
            src
            and os.path.isdir(src)
            and ptk.FileUtils.open_explorer(src, logger=self.logger)
        ):
            self.ui.footer.setText("No output folder yet — bake first.")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("lightmap_baker", reload=True)
    ui.show(pos="screen", app_exec=True)
