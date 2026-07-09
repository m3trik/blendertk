# !/usr/bin/python
# coding=utf-8
"""Blender world-HDRI environment manager.

Blender counterpart of mayatk's HDR Manager: :class:`HdrManagerSlots` — the Switchboard slots
class for the co-located ``hdr_manager.ui`` panel — drives the **world environment** (Environment
Texture → Mapping → Background → World Output) via the ``_light_utils`` engine, instead of
mayatk's Arnold ``aiSkyDomeLight`` + ``file`` texture network. Self-contained (``ptk.LoggingMixin``
only) so blendertk carries no back-dependency on tentacle; ``bpy``/Qt-only imports are deferred
into method bodies (headless Blender ships no Qt binding).

Knobs the panel drives (mirrors mayatk's per-knob list):

* ``hdr_env``    — the image loaded into the world's Environment Texture node.
* ``intensity``  — linear multiplier on the world's light output.
* ``exposure``   — photographic stops (log2); combines with intensity as
  ``strength = intensity * 2**exposure`` (Blender's world has a single scalar Strength; the
  two are round-tripped via custom properties — see ``_light_utils.set_world_hdri``).
* ``rotation``   — Z rotation (azimuth) on the world's Mapping node.
* ``visibility`` — render-background visibility (``film_transparent``); the world still lights
  the scene either way.
* ``diffuse`` / ``specular`` — whether the world contributes to diffuse / glossy(specular)
  lighting (Cycles ray visibility — the boolean analogue of Arnold's continuous
  ``aiDiffuse`` / ``aiSpecular`` scale, which has no Blender equivalent; EEVEE ignores it).

* ``resolution`` — importance-sampling map size. The Cycles analogue of Arnold's skydome
  importance-sampling **Resolution**: ``spn_resolution`` sets ``world.cycles.sampling_method =
  'MANUAL'`` + ``sample_map_resolution`` (Cycles-only; EEVEE ignores it).

Genuinely Arnold-only and dropped (no Blender analogue — Cycles has no per-light/per-world count):
light **Samples** (``spn_samples`` — disabled in the .ui, not merely hidden, so the panel stays a
structural mirror of mayatk's). The rotation slider's viewport-only preview toggle
(Arnold's ``skyRadius`` display sphere) is likewise disabled — Blender has no scene-level
"show in viewport, not in render" flag for the world background, only a per-3D-viewport shading
preference that isn't reliably scriptable/headless-safe. mayatk's select-skydome / -transform /
-file-node context actions are disabled too — a Blender world isn't a selectable scene object.

Maya's project ``sourceimages`` workspace (which mayatk's dropdown auto-scans) has no Blender
analogue, so the HDR folder here is an explicit, persisted choice — header menu ▸ **Set HDR
Folder…** — rather than auto-resolved.
"""
import os
import shutil
from typing import Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import undoable
from blendertk.core_utils.script_job_manager import ScriptJobManager
from blendertk.light_utils._light_utils import (
    get_world_hdri,
    set_world_hdri,
    clear_world_hdri,
    set_world_ray_visibility,
    get_world_ray_visibility,
    set_world_importance_resolution,
    get_world_importance_resolution,
)


class HdrManagerSlots(ptk.LoggingMixin):
    """Switchboard slots for the HDR Manager UI.

    Composition over inheritance: routes events through the module-level ``_light_utils``
    engine functions rather than carrying business logic. The dropdown is a *live mirror* of
    the world's HDR environment — it always shows the active map (or ``None`` when no
    btk-managed environment is wired). File load / undo / redo all re-sync it via
    :class:`ScriptJobManager`, so it never drifts from reality.
    """

    # File-dialog filter for the browse button — same format mayatk's uses.
    HDR_FILTER: str = "HDR Images (*.exr *.hdr);;All Files (*.*)"
    HDR_PATTERNS = ["*.exr", "*.hdr"]

    # Sentinel userData for the dropdown's explicit "None" entry — selecting it removes the
    # world's HDR environment. Distinct from "nothing picked yet" (the placeholder / an unset
    # combo, which carry no userData / None) so the slot can tell an intentional clear from an
    # empty selection.
    NONE_TOKEN: str = "<none>"
    NONE_LABEL: str = "None"

    # Add-HDR mode picker — index -> (label, token). Index is the source of truth so reordering
    # the labels can't silently rename the dispatch token. Mirrors mayatk's _ADD_MODES; the
    # destination is the user-set HDR folder here (sourceimages there).
    _ADD_MODES: tuple = (
        ("Copy to HDR folder", "copy"),
        ("Move to HDR folder", "move"),
        ("Link to original location", "link"),
    )

    # Filesystem op per import mode (single source for both the single-file and folder-batch
    # import paths).
    _IO_OPS = {"copy": shutil.copy2, "move": shutil.move}

    def __init__(self, switchboard, log_level: str = "WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hdr_manager
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[hdr_manager] ")

        # Option-box plugins on the rotation slider, wired in _setup_rotation_slider (deferred):
        # an inline degrees value plus a render-visibility toggle. Held so _sync_ui_to_scene can
        # push live scene state into them. The viewport-visibility toggle mayatk also carries
        # (Arnold's skyRadius preview) has no Blender analogue — see _setup_rotation_slider.
        self._rotation_value = None
        self._viewport_toggle = None
        self._render_toggle = None

        # Keep the dropdown a live mirror of the world's HDR environment. File load swaps the
        # whole environment -> re-scan disk + re-sync. Undo / Redo only flip which network is
        # live (the files on disk are untouched) -> re-sync widgets + selection, but skip the
        # disk re-scan so a recursive folder walk doesn't run on every undo of anything. Each
        # subscribe is guarded so an event unavailable on this Blender build can't block the
        # panel open ("SceneImported" has no Blender backing — ScriptJobManager.subscribe raises,
        # caught below, exactly like an unavailable Maya scriptJob on some Maya build).
        mgr = ScriptJobManager.instance()
        for events, handler in (
            (("SceneOpened", "NewSceneOpened", "SceneImported"), self._on_scene_changed),
            (("Undo", "Redo"), self._sync_ui_to_scene),
        ):
            for event in events:
                try:
                    mgr.subscribe(event, handler, owner=self)
                except Exception as e:
                    self.logger.debug(
                        "HDR Manager: event %r unavailable (%s)", event, e
                    )
        mgr.connect_cleanup(self.ui, owner=self)

        # Initial population is deferred to the next event-loop tick. The switchboard constructs
        # this slots instance *mid-load* — child widgets (footer, spinboxes, slider) aren't wired
        # onto self.ui until register_children runs after __init__ returns, so touching them now
        # hits AttributeError on None. By the next tick the UI is fully wired.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self) -> None:
        """Populate the combobox and sync widgets from the world.

        Deferred from __init__ (see there) so the full UI is registered before any
        ``self.ui.<widget>`` access.
        """
        # Per-field reset buttons (uitk option-box) on the Intensity / Exposure / Diffuse /
        # Specular / Resolution spin boxes: click resets a field to its default; Alt/Ctrl+click
        # bypasses it (greyed, restorable). Only Samples is excluded — it stays disabled (no
        # Blender analogue; see the .ui), so a reset button on it would be dead chrome. Done here
        # rather than __init__ because the spin boxes aren't wired onto self.ui until
        # register_children runs (see __init__); wrap before the _sync_ui_to_scene reads below,
        # since wrapping reparents the widgets.
        self.sb.add_reset_buttons(self.ui, skip=(self.ui.spn_samples,))
        self._setup_rotation_slider()

        self._refresh_combo()
        self._sync_ui_to_scene()

    def _setup_rotation_slider(self) -> None:
        """Wire the rotation slider's option box: inline degrees value + a render-visibility toggle.

        Done here (deferred) rather than a ``slider000_init`` hook because wrapping a widget in
        its option box reparents it — and the switchboard only self-heals such deferred wrappers
        after ``register_children`` (the same reason :meth:`_initialize_ui`'s
        ``add_reset_buttons`` call runs here, see ``__init__``).

        Mirrors mayatk's slider000 option box (inline exact-angle field + two view toggles), with
        one toggle disabled: mayatk carries a **viewport-visibility** toggle for Arnold's
        ``skyRadius`` display sphere (show the HDR in the viewport without affecting the render).
        Blender has no scene-level equivalent — only a per-3D-viewport shading preference
        (``space_data.shading.use_scene_world``), which is per-editor-window state, not scene
        data, and isn't reliably scriptable/headless-safe. Kept in the option box (disabled, with
        an explanatory tooltip) purely so the panel stays a structural mirror of mayatk's — a
        future pass can find it by grepping for the tag below.
        """
        from uitk.widgets.optionBox.options.toggle import ToggleOption
        from uitk.widgets.optionBox.options.value import ValueOption

        ob = self.ui.slider000.option_box
        # ValueOption sorts ahead of the buttons (see OptionBox._option_order), so the exact-angle
        # field sits flush against the slider, left of both toggles — same order as mayatk.
        ob.add_value(width=46, suffix="°")

        # TODO(blender-parity): no Blender analogue for Arnold's viewport-only skyRadius preview
        # (a per-3D-viewport shading preference, not scriptable scene state) — disabled.
        self._viewport_toggle = ToggleOption(
            wrapped_widget=self.ui.slider000,
            icon="screen",
            tooltip_on="No Blender equivalent of Arnold's viewport-only HDR preview.",
            tooltip_off="No Blender equivalent of Arnold's viewport-only HDR preview.",
            initial=False,
            settings_key=False,
        )
        self._render_toggle = ToggleOption(
            wrapped_widget=self.ui.slider000,
            icon="camera",
            tooltip_on="HDR visible as the render background. Click to hide.",
            tooltip_off="HDR hidden from the render background (lighting only). Click to show.",
            initial=True,
            settings_key=False,
        )
        self._render_toggle.toggled.connect(self._on_render_visible)
        ob.add_option(self._viewport_toggle)
        ob.add_option(self._render_toggle)
        # Force the lazy wrap now so the field + toggles render with the panel.
        _ = ob.container
        self._viewport_toggle.widget.setEnabled(False)
        self._rotation_value = ob.find_option(ValueOption)

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def _notify(
        self,
        message: str,
        *,
        level: str = "info",
        detail: Optional[str] = None,
        dialog: bool = False,
        dialog_text: Optional[str] = None,
    ) -> None:
        """Surface feedback consistently across the panel.

        Routes a single call to three places so the user sees the right amount of detail in the
        right place: the **footer** (short, colour-coded by *level*), the **console** (full
        *detail* at the matching log level), and — only when *dialog* is True — a modal
        **message box** for failures that need the user to act before retrying.
        """
        self.ui.footer.setText(message, level=level)

        full = detail or message
        logger = self.logger
        {
            "error": logger.error,
            "warning": logger.warning,
        }.get(level, logger.info)(full)

        if dialog:
            prefix = {
                "error": "Error:",
                "warning": "Warning:",
                "success": "Result:",
            }.get(level, "Note:")
            body = dialog_text or message
            self.sb.message_box(f"{prefix} {body}")

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget) -> None:
        """Configure header menu and refresh button."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.config_buttons("refresh", "menu", "collapse", "hide")
        widget.refresh_requested.connect(self._refresh_and_sync_combo)
        widget.menu.add("Separator", setTitle="HDR Folder")
        widget.menu.add(
            "QPushButton",
            setText="Set HDR Folder…",
            setObjectName="set_hdr_folder",
            setToolTip=(
                "Choose the folder scanned for HDR maps (.hdr / .exr) — Blender has no "
                "project workspace to auto-resolve one, unlike Maya's sourceimages."
            ),
        ).clicked.connect(self.set_hdr_folder)
        widget.menu.add(
            "QPushButton",
            setText="Open HDR Folder",
            setObjectName="open_sourceimages",
            setToolTip="Open the HDR folder in the OS file manager.",
        ).clicked.connect(self.open_sourceimages)
        widget.menu.add("Separator", setTitle="Network")
        widget.menu.add(
            "QPushButton",
            setText="Clear Network",
            setObjectName="clear_network",
            setToolTip="Remove the world HDRI environment (Environment Texture / Mapping nodes).",
        ).clicked.connect(self.clear_network)
        widget.set_help_text(
            fmt(
                title="HDR Manager",
                body="Manage the scene's world HDR environment lighting "
                "(Environment Texture + Mapping + Background world-shader network).",
                steps=[
                    "Header menu (▸) → <b>Set HDR Folder…</b> once — Blender has no project "
                    "workspace to auto-resolve a folder from, unlike Maya's sourceimages.",
                    "Pick an HDR / EXR from the dropdown to light the scene; pick <b>None</b> "
                    "to remove the HDR environment.",
                    "Open the dropdown's option menu (▸) → <b>Add HDR(s)…</b> to add images — "
                    "one dialog picks loose files and/or a whole folder; the import mode is "
                    "set just below it.",
                    "Adjust <b>Intensity</b> (linear) and <b>Exposure</b> (stops).",
                    "Drag the rotation slider to spin the environment around Z. Its option box "
                    "(▸) holds the exact angle plus a <b>render-visibility</b> toggle (the HDR "
                    "as the render background; lighting is unaffected either way).",
                ],
                sections=[
                    ("Advanced Options (collapsible)", [
                        "<b>Diffuse</b> / <b>Specular</b> — whether the world contributes to "
                        "diffuse vs glossy lighting (Cycles ray visibility; EEVEE ignores this). "
                        "Any value &gt;0 enables it, 0 disables it — Arnold's continuous scale "
                        "has no Blender equivalent, only on/off.",
                        "<b>Resolution</b> / <b>Samples</b> — disabled: Arnold-only concepts "
                        "(HDR importance-sampling resolution, per-light sample count); Cycles "
                        "handles both automatically and globally, with no per-light override.",
                    ]),
                    ("Add HDR(s)… (option-box menu ▸)", [
                        "One dialog picks <b>loose files and/or a whole folder</b>; folders are "
                        "expanded to their .hdr/.exr contents. Incomplete/corrupt files are "
                        "skipped.",
                        "Files already inside the HDR folder (any subfolder) are used in place "
                        "— never duplicated; the dropdown lists them automatically.",
                        "<b>Copy</b> — duplicate an <i>external</i> file into the HDR folder "
                        "(default; keeps the folder self-contained).",
                        "<b>Move</b> — relocate an external file into the HDR folder.",
                        "<b>Link</b> — wire each in at its original path.",
                    ]),
                    ("Dropdown right-click", [
                        "Reveal the texture in the OS file manager.",
                    ]),
                    ("Header menu", [
                        "<b>Set HDR Folder…</b> — choose the folder scanned for HDR maps.",
                        "<b>Open HDR Folder</b> — OS file manager shortcut.",
                        "<b>Clear Network</b> — delete the world HDRI environment nodes.",
                    ]),
                ],
                notes=[
                    "Applying an HDR does not change the active render engine — Blender's world "
                    "shader renders in both EEVEE and Cycles (unlike Arnold's aiSkyDomeLight, "
                    "which requires Arnold as the active renderer).",
                ],
            )
        )

    def cmb000_init(self, widget) -> None:
        """Wire the HDR dropdown: option-box plugins, context menu, auto-refresh."""
        # Re-scan disk AND re-point at the live world HDR every time the user opens the dropdown,
        # so newly-added HDRs appear and the active map is highlighted without hitting the header
        # refresh.
        widget.before_popup_shown.connect(self._refresh_and_sync_combo)

        # The dropdown mirrors the LIVE world HDR, never a persisted UI value — opt out of
        # cross-session state restore. Otherwise the switchboard restores the last index on panel
        # open and (signals unblocked) fires cmb000, auto-loading a stale HDR onto a fresh scene
        # instead of showing the true state. ComboBox.add() resets restore_state on every populate
        # (= not has_header), so _refresh_combo re-asserts this False each time; this initial set
        # covers the pre-refresh window.
        widget.restore_state = False

        # Right-click -> context menu (MenuMixin on uitk ComboBox). Kept separate from the
        # option-box menu below (different Menu instance).
        widget.configure_menu(trigger_button="right")
        # TODO(blender-parity): a Blender world isn't a selectable scene object — no analogue for
        # Maya's select-skydome/-transform/-file-node context actions. Kept (disabled) so the
        # context menu stays a structural mirror of mayatk's.
        for text, name in (
            ("Select Skydome", "ctx_select_skydome"),
            ("Select Transform", "ctx_select_transform"),
            ("Select File Node", "ctx_select_file_node"),
        ):
            btn = widget.menu.add(
                "QPushButton",
                setText=text,
                setObjectName=name,
                setToolTip="No Blender equivalent — a world isn't a selectable scene object.",
            )
            btn.setEnabled(False)
        widget.menu.add("Separator")
        widget.menu.add(
            "QPushButton",
            setText="Reveal in Explorer",
            setObjectName="ctx_reveal_in_explorer",
            setToolTip="Open the HDR file's containing folder in the OS file manager.",
        ).clicked.connect(self.ctx_reveal_in_explorer)

        # Option-box menu (▸) on the combobox — the panel's sole add affordance. The "Add HDR(s)…"
        # button sits ABOVE the import-mode combo: one dialog picks loose files and/or a whole
        # folder, imported per the mode below. (Non-slot objectName + explicit connect avoids a
        # double-fire if the menu auto-wires buttons to slots by objectName.)
        widget.option_box.menu.setTitle("Add HDR")
        add_btn = widget.option_box.menu.add(
            "QPushButton",
            setText="Add HDR(s)…",
            setObjectName="add_hdr_btn",
            setToolTip=(
                "Add HDR/EXR images — opens one dialog where you can pick loose files and/or a "
                "whole folder.\nEach is imported using the mode below. Incomplete/corrupt files "
                "are skipped."
            ),
        )
        add_btn.clicked.connect(self.add_hdr)
        widget.option_box.menu.add("Separator")
        widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_add_mode",
            setToolTip=(
                "What 'Add HDR(s)…' does with the picked file(s):\n"
                "  • Copy — duplicate into the HDR folder (default, keeps the folder self-contained).\n"
                "  • Move — relocate into the HDR folder (originals are removed).\n"
                "  • Link — leave in place and wire the original path directly\n"
                "    (won't appear in the dropdown afterward)."
            ),
            addItems=[label for label, _token in self._ADD_MODES],
        )

    # ------------------------------------------------------------------
    # HDR folder (Blender has no Maya-style project workspace to auto-resolve one)
    # ------------------------------------------------------------------

    def _hdr_folder(self) -> str:
        """Persisted HDR folder (set via the header menu's Set HDR Folder…), or ''."""
        return str(self.ui.settings.value("hdr_folder") or "")

    def set_hdr_folder(self) -> None:
        """Header-menu action — choose the folder scanned for HDR maps.

        Mayatk's dropdown auto-scans the Maya project's ``sourceimages`` folder; Blender has no
        equivalent project workspace, so the folder is an explicit, persisted choice instead.
        """
        folder = self.sb.QtWidgets.QFileDialog.getExistingDirectory(
            self.ui, "HDR Folder", self._hdr_folder()
        )
        if folder:
            self.ui.settings.setValue("hdr_folder", folder)
            self._refresh_and_sync_combo()

    # ------------------------------------------------------------------
    # Scene -> UI sync
    # ------------------------------------------------------------------

    def _on_scene_changed(self) -> None:
        self._refresh_combo()
        self._sync_ui_to_scene()

    def _refresh_combo(self) -> None:
        """Repopulate the HDR combobox from the persisted HDR folder.

        Preserves the user's current selection across rebuilds (by data, not index) so
        refreshing on dropdown-open doesn't snap them back to the first entry.
        """
        previous_data = self.ui.cmb000.currentData()
        src = self._hdr_folder()

        if not src or not os.path.isdir(src):
            # Block signals so the rebuild doesn't fire cmb000 -> set the environment.
            self.ui.cmb000.blockSignals(True)
            try:
                self.ui.cmb000.add([], clear=True)
                # Mirror the LIVE world, never a persisted pick — re-assert the opt-out
                # (ComboBox.add resets restore_state on every populate).
                self.ui.cmb000.restore_state = False
                self._prepend_none_item()
            finally:
                self.ui.cmb000.blockSignals(False)
            # High-frequency path (fires on every dropdown open); colour the footer but skip
            # _notify so we don't spam the console log.
            self.ui.footer.setText(
                "No HDR folder set (header menu ▸ Set HDR Folder…).", level="warning"
            )
            return

        # Recursive so HDRs kept in a subfolder list in the dropdown — they're already in the
        # folder, so the add flow leaves them in place rather than duplicating them into the root.
        hdr_info = ptk.get_dir_contents(
            src,
            ["filename", "filepath"],
            recursive=True,
            inc_files=self.HDR_PATTERNS,
            group_by_type=True,
        )
        count = len(hdr_info["filename"])
        self.ui.footer.setText(
            f"{count} HDR{'s' if count != 1 else ''} in {os.path.basename(src)}."
        )

        # Skip the destructive clear+repopulate when the listed HDRs are unchanged. _refresh_combo
        # runs on EVERY dropdown-open (before_popup_shown); rebuilding the item model right before
        # the popup shows can leave the popup view's selection desynced so the first click is
        # dropped. The disk listing is the only input, so an unchanged listing -> leave it intact.
        if self._listed_paths_match(hdr_info["filepath"]):
            return

        # Block signals so the rebuild doesn't fire cmb000 -> set the environment -> re-trigger
        # refresh while we're still rebuilding.
        self.ui.cmb000.blockSignals(True)
        try:
            # ComboBox.add() drives both userData and visible text. No header is used: a header
            # would paint a fixed label at index -1 and (via ComboBox.check_index) snap the
            # selection back to -1 after every pick — hiding the active map AND zeroing
            # currentData() so the apply silently no-ops. The combo must instead display the live
            # selection.
            self.ui.cmb000.add(
                zip(hdr_info["filename"], hdr_info["filepath"]),
                ascending=False,
                clear=True,
            )
            self.ui.cmb000.restore_state = False
            # Explicit "None" entry at the top so the user can clear the HDR environment from the
            # same dropdown that sets it.
            self._prepend_none_item()

            # Restore prior selection by data, not index.
            if previous_data:
                idx = self.ui.cmb000.findData(previous_data)
                if idx >= 0:
                    self.ui.cmb000.setCurrentIndex(idx)
        finally:
            self.ui.cmb000.blockSignals(False)

    def _listed_paths_match(self, new_paths) -> bool:
        """True if the dropdown already lists exactly *new_paths* (order-independent; skips the
        ``None`` sentinel)."""
        combo = self.ui.cmb000
        current = set()
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data != self.NONE_TOKEN:
                current.add(self._norm_path(data))
        return current == {self._norm_path(p) for p in new_paths}

    def _prepend_none_item(self) -> None:
        """Insert the explicit 'None' entry at the top of the HDR dropdown.

        Selecting it removes the world's HDR environment (see :meth:`cmb000` /
        :meth:`_apply_selection`). Carries :attr:`NONE_TOKEN` as userData so it's distinguishable
        from a real HDR path and from the unset placeholder. Callers run with the combo's signals
        blocked, so the implicit index shift from inserting at row 0 fires no slot.
        """
        self.ui.cmb000.insertItem(0, self.NONE_LABEL, self.NONE_TOKEN)

    def _select_combo_path(self, path: str) -> bool:
        """Select the dropdown entry whose file matches *path* (path-normalized + case-folded
        compare — the combo stores raw ``get_dir_contents`` filepaths, which can mix slash
        styles). Returns True on a hit (and selects it, signals blocked); False if no entry
        matches (e.g. a Link-mode file that lives outside the HDR folder)."""
        target = self._norm_path(path)
        combo = self.ui.cmb000
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and self._norm_path(data) == target:
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(i)
                finally:
                    combo.blockSignals(False)
                return True
        return False

    def _sync_ui_to_scene(self) -> None:
        """Pull live world state into the UI widgets.

        When no btk-managed environment exists (fresh scene, after :meth:`clear_network`), falls
        back to neutral defaults so the controls don't display stale values from a removed
        network.
        """
        try:
            state = get_world_hdri()
        except ModuleNotFoundError:
            # No Blender runtime (Qt-only harness): nothing to sync.  This runs
            # from a deferred QTimer callback — letting the ImportError escape
            # the native event dispatch hard-crashes PySide6 (access violation),
            # so degrade instead; the panel-load contract is bpy-free.
            return
        has_env = state is not None
        rotation = int(round(state["rotation"])) % 360 if has_env else 0
        intensity = state["intensity"] if has_env else 1.0
        exposure = state["exposure"] if has_env else 0.0
        visible = state["visible"] if has_env else True
        rv = get_world_ray_visibility() or {}
        diffuse = 1.0 if rv.get("diffuse", True) else 0.0
        specular = 1.0 if rv.get("glossy", True) else 0.0
        # Manual importance-sampling resolution when set, else the .ui default (automatic mode).
        resolution = get_world_importance_resolution()
        if resolution is None:
            resolution = self.ui.spn_resolution.value()

        for widget, setter, value in (
            (self.ui.slider000, "setSliderPosition", rotation),
            (self.ui.spn_intensity, "setValue", intensity),
            (self.ui.spn_exposure, "setValue", exposure),
            (self.ui.spn_diffuse, "setValue", diffuse),
            (self.ui.spn_specular, "setValue", specular),
            (self.ui.spn_resolution, "setValue", resolution),
        ):
            widget.blockSignals(True)
            try:
                getattr(widget, setter)(value)
            finally:
                widget.blockSignals(False)

        # The render-visibility toggle and the inline degrees field live in the rotation slider's
        # option box, not the .ui — push live state into them directly. The toggle sets silently
        # (emit=False, no scene write); the value field is refreshed since the blocked
        # setSliderPosition above didn't fire the slider's valueChanged that normally mirrors it.
        if self._render_toggle is not None:
            self._render_toggle.set_on(visible, emit=False)
        if self._rotation_value is not None:
            self._rotation_value.refresh()

        # Reflect the LIVE world HDR in the dropdown so the combo always shows what's actually
        # lighting the scene (it's restore_state=False, so it never shows a persisted pick).
        self._select_active_in_combo(has_env=has_env, filepath=state["filepath"] if has_env else None)

    def _select_active_in_combo(
        self, has_env: Optional[bool] = None, filepath: Optional[str] = None
    ) -> None:
        """Point the dropdown at the world's active HDR — or ``None`` if absent.

        Keeps the combo a live mirror of the environment: a listed file selects that row; an
        unlisted file (e.g. a Link-mode HDR living outside the HDR folder) is surfaced as a
        transient row so the combo still shows the *real* active map, not a misleading ``None``;
        no environment selects the explicit ``None`` entry. Signals are blocked throughout so
        re-pointing never re-fires :meth:`cmb000`. Any transient row is rebuilt away by the next
        :meth:`_refresh_combo` and re-added here if the link is still active.
        """
        if has_env is None:
            state = get_world_hdri()
            has_env = state is not None
            filepath = state["filepath"] if has_env else None
        current_path = filepath if has_env else None
        if current_path and self._select_combo_path(current_path):
            return
        combo = self.ui.cmb000
        combo.blockSignals(True)
        try:
            if current_path:
                combo.addItem(os.path.basename(current_path), current_path)
                combo.setCurrentIndex(combo.count() - 1)
            else:
                idx = combo.findData(self.NONE_TOKEN)
                combo.setCurrentIndex(idx if idx >= 0 else -1)
        finally:
            combo.blockSignals(False)

    def _refresh_and_sync_combo(self) -> None:
        """Repopulate the list from disk, then re-point at the active HDR.

        Wired to the dropdown's pre-popup signal and the header Refresh action, so opening or
        refreshing the list always lands on (and highlights) the environment that's actually live
        in the scene. Only the combo is touched — the level/rotation widgets are left alone so a
        mid-edit value isn't clobbered just by opening the dropdown.
        """
        self._refresh_combo()
        self._select_active_in_combo()

    # ------------------------------------------------------------------
    # Read-only convenience
    # ------------------------------------------------------------------

    @property
    def hdr_map(self) -> Optional[str]:
        """Selected HDR file path from the combobox."""
        return self.ui.cmb000.currentData()

    @property
    def hdr_map_visibility(self) -> bool:
        """Render 'Visible' flag — read from the rotation slider's render toggle.

        Falls back to ``True`` if the toggle isn't wired yet (e.g. before
        :meth:`_setup_rotation_slider`)."""
        return bool(self._render_toggle.is_on) if self._render_toggle else True

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def cmb000(self, index, widget) -> None:
        """HDR map selection — the panel's sole apply action.

        Unlike mayatk (which must defer the apply to Maya idle to escape a combobox-popup-
        teardown re-entrancy hazard with a live Arnold IPR — see
        ``mayatk.HdrManagerSlots.cmb000``), Blender's world-shader edit has no such hazard, so
        the apply runs synchronously here.
        """
        path = widget.currentData()
        if not path:  # placeholder / nothing picked
            return
        if path == self.NONE_TOKEN:
            self.ui.footer.setText("Removing HDR…", level="info")
        else:
            self.ui.footer.setText(f"Applying {os.path.basename(path)}…", level="info")
        self._apply_selection()

    def _on_render_visible(self, state) -> None:
        """Toggle the world's render-background visibility — the rotation slider's toggle.

        Sets ``film_transparent`` (the HDR as a backdrop in the render). Applied synchronously,
        like the other live controls; the world still lights the scene either way."""
        self._apply_levels()

    def slider000(self, value, widget) -> None:
        """Rotate the HDR around Z."""
        self._apply_levels()

    def spn_intensity(self, value, widget) -> None:
        """Set the world's HDR intensity (brightness multiplier)."""
        self._apply_levels()

    def spn_exposure(self, value, widget) -> None:
        """Set the world's HDR exposure (in stops)."""
        self._apply_levels()

    def _apply_levels(self) -> None:
        """Re-apply intensity/exposure/rotation/visibility to an already-set environment
        (no-op until a map has been selected)."""
        if get_world_hdri() is None:
            return
        set_world_hdri(
            None,
            intensity=self.ui.spn_intensity.value(),
            exposure=self.ui.spn_exposure.value(),
            rotation=float(self.ui.slider000.value()),
            visible=self.hdr_map_visibility,
        )

    def spn_resolution(self, value, widget) -> None:
        """Importance-sampling map resolution — switches the world to manual Cycles sampling and
        applies the map size (the Blender analogue of Arnold's skydome Resolution; Cycles-only,
        no-op off-Cycles)."""
        set_world_importance_resolution(self.ui.spn_resolution.value())

    def spn_diffuse(self, value, widget) -> None:
        """Diffuse contribution — any value >0 enables it, 0 disables it (Cycles ray
        visibility; Arnold's continuous scale has no Blender equivalent)."""
        self._apply_ray_visibility()

    def spn_specular(self, value, widget) -> None:
        """Specular/glossy contribution — any value >0 enables it, 0 disables it (Cycles ray
        visibility; Arnold's continuous scale has no Blender equivalent)."""
        self._apply_ray_visibility()

    def _apply_ray_visibility(self) -> None:
        """Push the Diffuse / Specular spin boxes to the world's Cycles ray visibility
        (no-op off-Cycles)."""
        set_world_ray_visibility(
            diffuse=self.ui.spn_diffuse.value() > 0.0,
            glossy=self.ui.spn_specular.value() > 0.0,
        )

    def _validate_or_warn(self, path: str, *, dialog: bool = True) -> bool:
        """True if *path* is safe to wire into the environment.

        A missing file is allowed through; an existing but incomplete/corrupt image (e.g. a
        truncated or partially synced cloud HDR) is refused, since Blender's image loader can
        choke on it. On refusal the failure is surfaced via :meth:`_notify`."""
        if not os.path.isfile(path):
            return True
        ok, reason = ptk.ImgUtils.validate_image_integrity(path)
        if ok:
            return True
        name = os.path.basename(path)
        self._notify(
            f"{name} is incomplete on disk ({reason})",
            level="error",
            detail=(
                f"HDR not loaded — only part of the file is on disk ({reason}):\n"
                f"{path}\n\n"
                "Common causes: it's a cloud file (Dropbox / OneDrive) still syncing, a "
                "download or export that was interrupted, or the disk filled up mid-write. "
                "If it's a cloud file, make sure your sync app is running and let it finish "
                "downloading; otherwise free up disk space and re-export or re-download it. "
                "Then retry."
            ),
            dialog=dialog,
            dialog_text=(
                f"{name} is incomplete on disk ({reason}).\n\n"
                "Common causes: a cloud file (Dropbox / OneDrive) still syncing, an interrupted "
                "download or export, or a full disk. If it's a cloud file, make sure your sync "
                "app is running and it finishes downloading; otherwise free up space and "
                "re-export or re-download it, then retry."
            ),
        )
        return False

    def _clear_environment(self, absent_msg: str, *, absent_level: str = "info") -> bool:
        """Remove the world HDRI network, resync the UI, and report.

        Single owner of the clear path — shared by the deferred apply's "None" selection
        (:meth:`_apply_selection`) and the header's Clear Network action (:meth:`clear_network`).
        Returns True when a network was cleared; when none is present, reports *absent_msg* at
        *absent_level* and returns False."""
        if get_world_hdri() is None:
            self._notify(absent_msg, level=absent_level)
            return False
        clear_world_hdri()
        self._sync_ui_to_scene()
        self._notify("HDR environment cleared.", level="success")
        return True

    def _set_environment(self, path: str) -> None:
        """Light the scene from *path* at the current level widgets + ray visibility.

        The shared apply primitive behind the combo selection (:meth:`_apply_selection`) and the
        Add-HDR Link / off-list paths (:meth:`_apply_path`); un-decorated so each caller pushes
        exactly one undo step (``@undoable`` wraps the entry points, not this)."""
        set_world_hdri(
            path,
            intensity=self.ui.spn_intensity.value(),
            exposure=self.ui.spn_exposure.value(),
            rotation=float(self.ui.slider000.value()),
            visible=self.hdr_map_visibility,
        )
        self._apply_ray_visibility()

    @undoable
    def _apply_selection(self) -> None:
        """Apply the current dropdown selection — clear / swap / build.

        Re-reads the dropdown so it always applies the latest pick:

        * **None** -> remove the world HDRI network;
        * a map -> build/update the network from the current UI state (Blender's world node rig
          is find-or-create by fixed name, so swapping the map is already a cheap in-place update
          — see ``_light_utils.set_world_hdri``).
        """
        path = self.hdr_map
        if path == self.NONE_TOKEN:
            self._clear_environment("HDR is set to None — no environment to clear.")
            return
        if not path:
            self._notify("Pick or browse for an HDR first.", level="warning")
            self._select_active_in_combo()
            return
        # Casual dropdown selection -> surface a bad file in the footer/console, but don't
        # interrupt with a modal (applying is a single click). On rejection re-point the dropdown
        # at the live world HDR so the combo doesn't lie about what's actually lighting the scene.
        if not self._validate_or_warn(path, dialog=False):
            self._select_active_in_combo()
            return
        self._set_environment(path)
        self._sync_ui_to_scene()
        self._notify(f"HDR: {os.path.basename(path)}", level="success")

    def add_hdr(self) -> None:
        """Add HDR(s) from one dialog — pick loose files and/or a whole folder.

        Option-box menu action (the panel's sole add affordance). Selected directories are
        expanded to their ``.hdr`` / ``.exr`` contents; loose files are taken as-is. Everything is
        imported per the current mode. Picking a *single* loose file gets the careful UX (modal on
        a bad file, overwrite prompt); a folder or several files is a bulk add (skip+count)."""
        start = self._hdr_folder()
        selected = self._pick_hdr_paths(start)
        if not selected:
            return

        dirs = [p for p in selected if os.path.isdir(p)]
        files = [p for p in selected if os.path.isfile(p)]
        paths = list(files)
        for d in dirs:
            paths.extend(
                ptk.get_dir_contents(d, "filepath", inc_files=self.HDR_PATTERNS) or []
            )

        # One explicit loose file -> careful; a folder or multiple -> bulk.
        careful = len(files) == 1 and not dirs
        if dirs and not files:
            where = os.path.basename(dirs[0].rstrip("/\\")) or dirs[0]
        else:
            where = "selection"
        self._add_hdrs(paths, where=where, careful=careful)

    def _pick_hdr_paths(self, start: str) -> list:
        """Open one dialog that selects HDR/EXR files *and/or* folders.

        Qt has no native "files or directories" mode, so this drives a non-native
        ``QFileDialog`` with the internal item views switched to extended (multi) selection —
        letting the user pick loose files, a folder, or a mix, all returned by
        ``selectedFiles()``. Returns ``[]`` on cancel."""
        QtW = self.sb.QtWidgets
        dialog = QtW.QFileDialog(
            self.ui, "Add HDR(s) — pick files and/or a folder", start
        )
        dialog.setFileMode(QtW.QFileDialog.ExistingFiles)
        dialog.setOption(QtW.QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilters(self.HDR_FILTER.split(";;"))
        views = dialog.findChildren(QtW.QListView) + dialog.findChildren(QtW.QTreeView)
        for view in views:
            view.setSelectionMode(QtW.QAbstractItemView.ExtendedSelection)
        if dialog.exec_():
            return dialog.selectedFiles() or []
        return []

    def _add_hdrs(self, paths: list, *, where: str, careful: bool) -> None:
        """Import HDR/EXR *paths* (loose files and/or folder contents) per mode.

        Single importer behind every add flow. *careful* selects the UX:

          * ``True`` — an explicit single-file pick: a bad file raises the actionable modal, and a
            real same-named collision in the HDR folder prompts before overwrite.
          * ``False`` — a bulk add (a folder or several files): incomplete / corrupt files are
            skipped into a count (no per-file dialog), and an existing same-named file is reused
            rather than clobbered.

        Copy / Move bring each file into the HDR folder (so it lists in the dropdown); Link wires
        it in place. Wires the last good HDR into the environment and reports a summary."""
        if not paths:
            self._notify(f"No HDR/EXR files in {where}.", level="warning")
            return

        mode = self._add_mode()
        # Only Copy/Move need a destination; Link wires files in place.
        src = self._hdr_folder() if mode != "link" else None
        if mode != "link" and (not src or not os.path.isdir(src)):
            self._notify(
                "Set the HDR folder first (header menu ▸ Set HDR Folder…).",
                level="error",
                dialog=True,
            )
            return

        added, skipped, last, did_io = 0, 0, None, False
        for path in paths:
            if careful:
                if not self._validate_or_warn(path):
                    return
            elif not ptk.ImgUtils.validate_image_integrity(path)[0]:
                skipped += 1
                continue

            if mode == "link":
                last, added = path, added + 1
                continue

            # A file already inside the HDR folder (root OR any subfolder) is used in place —
            # never duplicated into the root. The recursive dropdown lists it regardless of
            # depth, so Copy/Move on an already-listed file is a no-op rather than a duplicate.
            if self._is_under_dir(path, src):
                last, added = os.path.normpath(path), added + 1
                continue

            final = os.path.normpath(os.path.join(src, os.path.basename(path)))
            if os.path.exists(final):
                if careful:
                    if not self._confirm_overwrite(final):
                        self._notify(f"{mode.capitalize()} cancelled.", level="info")
                        return
                    # confirmed -> fall through and overwrite
                elif ptk.ImgUtils.validate_image_integrity(final)[0]:
                    last, added = final, added + 1  # reuse a usable existing file
                    continue
                else:
                    skipped += 1  # existing is corrupt; never clobber in bulk
                    continue
            try:
                self._IO_OPS[mode](path, final)
            except OSError as e:
                self.logger.error("Add — %s of %s failed: %s", mode, path, e)
                if careful:
                    self._notify(
                        f"{mode.capitalize()} failed: {e}",
                        level="error",
                        detail=f"{mode} of {path} -> {final} failed: {e}",
                        dialog=True,
                    )
                    return
                skipped += 1
                continue
            last, added, did_io = final, added + 1, True

        self._refresh_combo()
        if last:
            self._select_combo_path(last)
            self._apply_path(last)

        self._notify_add_result(
            added, skipped, last, where=where, careful=careful, mode=mode, did_io=did_io
        )

    @undoable
    def _apply_path(self, path: str) -> None:
        """Light the scene from *path* directly (the Add-HDR apply step).

        Separate, ``@undoable``-wrapped entry point so the import's own file I/O (not a Blender
        undo-tracked mutation) and the world-shader network change (which is) don't share one
        push with :meth:`_apply_selection`'s combo-driven path."""
        self._set_environment(path)
        self._sync_ui_to_scene()

    def _notify_add_result(
        self, added, skipped, last, *, where, careful, mode, did_io
    ) -> None:
        """Footer summary for an add — single rich line vs. bulk count."""
        if careful and added:
            name = os.path.basename(last)
            if mode == "link":
                verb = "Linked"
            elif did_io:
                verb = {"copy": "Copied & set", "move": "Moved & set"}[mode]
            else:
                verb = "Set"  # reused an existing HDR-folder copy
            self._notify(f"{verb}: {name}", level="success")
        elif added:
            msg = f"Added {added} HDR{'s' if added != 1 else ''} from {where}"
            if skipped:
                msg += f" ({skipped} skipped — incomplete/corrupt)"
            self._notify(msg, level="success")
        else:
            self._notify(
                f"No usable HDRs in {where} ({skipped} incomplete/corrupt).",
                level="warning",
            )

    # ------------------------------------------------------------------
    # Header-menu actions
    # ------------------------------------------------------------------

    def open_sourceimages(self) -> None:
        """Open the HDR folder in the OS file manager."""
        src = self._hdr_folder()
        if not src or not os.path.isdir(src):
            self._notify("No HDR folder set (header menu ▸ Set HDR Folder…).", level="warning")
            return
        try:
            ptk.FileUtils.reveal_in_file_manager(src)
        except (FileNotFoundError, OSError) as e:
            self._notify(str(e), level="error")

    @undoable
    def clear_network(self) -> None:
        """Delete the world HDRI network and reset the UI to defaults."""
        self._clear_environment("No HDR network in scene.")

    # ------------------------------------------------------------------
    # Context-menu actions (right-click on cmb000)
    # ------------------------------------------------------------------

    def ctx_reveal_in_explorer(self) -> None:
        """Reveal the environment's HDR texture file in the OS file manager."""
        state = get_world_hdri()
        path = state["filepath"] if state else None
        if path and os.path.exists(path):
            try:
                ptk.FileUtils.reveal_in_file_manager(path)
            except (FileNotFoundError, OSError) as e:
                self._notify(str(e), level="error")
            return
        # Fall back to the HDR folder so the user can still navigate from there when the texture
        # is missing/unsaved.
        src = self._hdr_folder()
        if src and os.path.isdir(src):
            try:
                ptk.FileUtils.reveal_in_file_manager(src)
            except (FileNotFoundError, OSError) as e:
                self._notify(str(e), level="error")
        else:
            self._notify("HDR file not found and no HDR folder set.", level="warning")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_path(path) -> str:
        """Case-folded, separator-normalized path for robust comparison."""
        return os.path.normcase(os.path.normpath(str(path)))

    @staticmethod
    def _is_under_dir(path: str, directory: str) -> bool:
        """True if *path* lies inside *directory* (root or any depth)."""
        p = HdrManagerSlots._norm_path(path)
        d = HdrManagerSlots._norm_path(directory)
        return p == d or p.startswith(d + os.sep)

    def _add_mode(self) -> str:
        """Return the active Add-HDR mode token: ``copy`` / ``move`` / ``link``.

        Falls back to ``copy`` if the option-box widget hasn't been attached yet (e.g. during
        early init or tests)."""
        try:
            idx = self.ui.cmb000.option_box.menu.cmb_add_mode.currentIndex()
        except AttributeError:
            return "copy"
        if 0 <= idx < len(self._ADD_MODES):
            return self._ADD_MODES[idx][1]
        return "copy"

    def _confirm_overwrite(self, target_path: str) -> bool:
        """Ask the user before overwriting an existing file in the HDR folder."""
        return self.sb.message_box(
            f"{os.path.basename(target_path)} already exists in the HDR folder.\nOverwrite it?",
            "Yes",
            "No",
        ) == "Yes"


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("hdr_manager", reload=True)
    ui.show(pos="screen", app_exec=True)
