# !/usr/bin/python
# coding=utf-8
"""Audio Clips — scene-wide sound-strip management over Blender's Video Sequence Editor (VSE).

Blender counterpart of mayatk's ``audio_utils.audio_clips.AudioClipsSlots`` (name + role
mirrored; the underlying model is NOT mirrored — see :mod:`blendertk.audio_utils._audio_utils`
for why). Every action here is a thin pass-through to :class:`AudioUtils`.

Scope of this port
-------------------
Built for real: browse-to-add (one or more files, each becomes a clip at the current frame),
rename/replace/remove a clip, remove all, move the selected clip to the current frame, trim its
head/tail, sync the scene frame range to the loaded clips, and reveal a clip in the Sequencer
editor.

Dropped as architecturally inapplicable (see the parity ledger's ``audio_clips_slots`` entries
and the reasons in :mod:`blendertk.audio_utils._audio_utils`'s module docstring for the full
rationale): the Auto-Convert / Export-mode / Trim-Silence / Suffix-Time-Range / Channels-panel
header controls (all solve Maya's single-audio-slot Time-Slider + WAV-only playback problems that
the VSE doesn't have), and the Key-Audio-Event option box's Auto-End-None / Snap-To-Frame /
Next-Event / Key-All / Stagger controls plus Cleanup-Unused (all exist only to manage Maya's
two-phase "register a track, then key it" workflow and its single-slot composite; a VSE strip is
always both registered and placed, so there is no unplaced/unkeyed state to manage).

Panel discovery / launcher
---------------------------
Co-located ``audio_clips.ui`` + this module are discovered automatically by
``BlenderUiHandler``'s recursive scan (only the engine, :class:`~blendertk.audio_utils.AudioUtils`,
is registered in ``blendertk/__init__.py``'s ``DEFAULT_INCLUDE`` — mirrors every other tool panel).
Mayatk's launcher button lives inside the (not-yet-ported) Shot Manifest panel; since that panel
doesn't exist on the Blender side yet, this panel's launcher sits in
``tentacle/slots/blender/scene.py``'s header "Manage" section instead (``marking_menu.show
("audio_clips")``) until Shot Manifest lands.

``import bpy`` / the Qt-only ``uitk`` helpers are deferred into call bodies (headless Blender
ships no Qt binding; this module must also resolve with no Blender running).
"""
import os

import pythontk as ptk

from blendertk.audio_utils._audio_utils import AudioUtils


class AudioClipsSlots(ptk.LoggingMixin):
    """Switchboard slots for the Audio Clips panel.

    Tentacle-independent (``ptk.LoggingMixin`` only): ``self.sb`` / ``self.ui`` are set in
    ``__init__``, every action calls :class:`AudioUtils` directly. No selection concept applies
    here (clips are scene-wide VSE strips, not scene objects) — the combo (``cmb000``) is the
    only "selection", decoupled from Blender's own object/strip selection state.
    """

    AUDIO_GLOBS = ["*.wav", "*.aif", "*.aiff", "*.mp3", "*.ogg", "*.m4a", "*.flac", "*.aac", "*.opus"]
    AUDIO_FILTER = f"Audio Files ({' '.join(AUDIO_GLOBS)});;All Files (*)"

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.audio_clips
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[audio_clips] ")
        try:
            self.ui.footer.setDefaultStatusText(
                "Browse for audio files (folder icon) or select a clip to manage it."
            )
        except Exception as e:
            self.logger.debug(f"Footer default text unavailable: {e}")

    # ------------------------------------------------------------------ bpy availability
    @staticmethod
    def _has_bpy():
        try:
            import bpy

            return bpy is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Help text only — no header menu items apply (see module docstring)."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="Audio Clips",
                body="Scene-wide sound clips as native Video Sequence Editor strips — "
                "add/remove/trim them and keep the scene frame range in sync.",
                steps=[
                    "Click the <b>folder icon</b> on the clips combo to browse for audio "
                    "files. Each selected file becomes a new clip at the current frame.",
                    "Select a clip in the combo.",
                    "Move the playhead to where it should start, then press "
                    "<b>Move To Current Frame</b> to reposition it.",
                    "Use <b>Trim</b> to shave frames off either end without moving the clip.",
                    "Press <b>Sync Scene Range</b> to fit the scene's frame range to the "
                    "loaded clips.",
                ],
                sections=[
                    ("Clips option box (▸)", [
                        "<b>Rename Selected</b> / <b>Replace Selected</b> — rename the clip "
                        "or swap its source file (position/trim is preserved).",
                        "<b>Remove Selected</b> / <b>Remove All</b> — delete one clip or "
                        "every clip in the scene.",
                    ]),
                    ("Move To Current Frame option box (▸)", [
                        "<b>select icon</b> — reveal the selected clip in the Sequencer "
                        "editor.",
                        "<b>refresh icon</b> — same as the Sync Scene Range button.",
                    ]),
                ],
                notes=[
                    "Every clip is a real Sequencer sound strip — open the Sequencer/VSE "
                    "editor to see its waveform, drag it, or scrub audio during playback; "
                    "this panel does not duplicate that display.",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Clips combo
    # ------------------------------------------------------------------

    def cmb000_init(self, widget):
        """Browse option box + clip-management menu; refreshes on every dropdown open."""
        from uitk.widgets.optionBox.options.browse import BrowseOption

        if not getattr(widget, "is_initialized", False):
            widget.option_box.add_option(
                BrowseOption(
                    wrapped_widget=widget,
                    file_types=self.AUDIO_FILTER,
                    mode="files",
                    title="Select Audio Files",
                    tooltip="Browse for audio files to add as clips at the current frame.",
                    callback=self._browse_audio_files_cb,
                )
            )

            widget.option_box.menu.setTitle("Clips")
            btn_rename = widget.option_box.menu.add(
                "QPushButton", setText="Rename Selected", setObjectName="btn_rename_track",
                setToolTip="Rename the selected clip.",
            )
            btn_rename.clicked.connect(self.b001)
            btn_replace = widget.option_box.menu.add(
                "QPushButton", setText="Replace Selected", setObjectName="btn_replace_track",
                setToolTip="Swap the selected clip's audio file (position/trim is preserved).",
            )
            btn_replace.clicked.connect(self.b002)
            btn_remove_sel = widget.option_box.menu.add(
                "QPushButton", setText="Remove Selected", setObjectName="btn_remove_selected",
                setToolTip="Delete the selected clip.",
            )
            btn_remove_sel.clicked.connect(self.b005)
            btn_remove_all = widget.option_box.menu.add(
                "QPushButton", setText="Remove All", setObjectName="btn_remove_audio",
                setToolTip="Delete every clip in the scene.",
            )
            btn_remove_all.clicked.connect(self.b006)

            # Re-list on every dropdown open, so clips added/removed directly in the
            # Sequencer editor (outside this panel) are never stale.
            widget.before_popup_shown.connect(self._refresh_combo)
            widget.is_initialized = True

        self._refresh_combo()

    def _refresh_combo(self, *_args):
        """Repopulate the clips combo from the live scene; keep the current name selected.

        Degrades to an empty list under the Qt-only offscreen smoke test (no Blender running).
        """
        cmb = self.ui.cmb000
        current = cmb.currentText()
        names = [c["name"] for c in AudioUtils.list_clips()] if self._has_bpy() else []
        cmb.blockSignals(True)
        cmb.clear()
        if names:
            cmb.addItems(names)
        cmb.blockSignals(False)
        if current and current in names:
            idx = cmb.findText(current)
            if idx >= 0:
                cmb.setCurrentIndex(idx)
        cmb.repaint()
        self._sync_trim_spinboxes()

    def cmb000(self, index, widget):
        """Selection only informs Move/Trim/the option-box actions — no side effect."""
        self._sync_trim_spinboxes()

    def _sync_trim_spinboxes(self):
        """Reflect the selected clip's current trim into ``s000``/``s001`` (a live inspector,
        not just a one-shot default) so Apply Trim edits from the real current values."""
        current = self.ui.cmb000.currentText()
        info = AudioUtils.get_clip(current) if (current and self._has_bpy()) else None
        for spin, key in ((self.ui.s000, "trim_start"), (self.ui.s001, "trim_end")):
            spin.blockSignals(True)
            spin.setValue(int(info[key]) if info else 0)
            spin.blockSignals(False)

    def _browse_audio_files_cb(self, paths):
        """BrowseOption callback — add each selected file as a new clip at the current frame."""
        paths = [paths] if isinstance(paths, str) else list(paths or [])
        if not paths:
            return
        added = []
        errors = []
        for path in paths:
            try:
                added.append(AudioUtils.add_clip(path))
            except (FileNotFoundError, RuntimeError) as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                self.logger.warning(f"Could not add '{path}': {e}")
        self._refresh_combo()
        if added:
            idx = self.ui.cmb000.findText(added[-1])
            if idx >= 0:
                self.ui.cmb000.setCurrentIndex(idx)
        suffix = f" ({len(errors)} failed)" if errors else ""
        self.ui.footer.setText(
            f"Added {len(added)} clip(s){suffix}." if added else "No clips added."
        )

    # ------------------------------------------------------------------
    # Manage (option-box menu callbacks)
    # ------------------------------------------------------------------

    def b001(self):
        """Rename Selected — rename the clip currently shown in the combo."""
        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("Select a clip first.")
            return
        new_name = self.sb.input_dialog(
            title="Rename Clip",
            label=f"Rename '{current}' to:",
            text=current,
            parent=self.ui,
            placeholder="e.g. footstep_left",
            validate=lambda t: bool(t.strip()) and t.strip() != current,
            error_text="Name cannot be empty or unchanged.",
        )
        if not new_name:
            return
        result = AudioUtils.rename_clip(current, new_name)
        if result is None:
            self.ui.footer.setText(f"Clip '{current}' not found — refresh the combo.")
            return
        self._refresh_combo()
        idx = self.ui.cmb000.findText(result)
        if idx >= 0:
            self.ui.cmb000.setCurrentIndex(idx)
        self.ui.footer.setText(f"Renamed '{current}' -> '{result}'.")

    def b002(self):
        """Replace Selected — swap the selected clip's source file."""
        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("Select a clip first.")
            return
        path = self.sb.file_dialog(
            file_types=self.AUDIO_GLOBS,
            title="Replace Clip Audio",
            filter_description="Audio Files",
            allow_multiple=False,
        )
        if not path:
            return
        try:
            AudioUtils.replace_clip(current, path)
        except FileNotFoundError as e:
            self.ui.footer.setText(str(e))
            return
        self.ui.footer.setText(f"Replaced '{current}' with '{os.path.basename(path)}'.")

    def b005(self):
        """Remove Selected — delete the clip currently shown in the combo."""
        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("Select a clip first.")
            return
        if AudioUtils.remove_clip(current):
            self._refresh_combo()
            self.ui.footer.setText(f"Removed '{current}'.")
        else:
            self.ui.footer.setText(f"Clip '{current}' not found — refresh the combo.")

    def b006(self):
        """Remove All — delete every clip in the scene."""
        count = AudioUtils.remove_all_clips()
        self._refresh_combo()
        self.ui.footer.setText(
            f"Removed {count} clip(s)." if count else "No clips to remove."
        )

    # ------------------------------------------------------------------
    # Move To Current Frame
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Move option box — reveal-in-Sequencer + a Sync Scene Range shortcut."""
        widget.option_box.set_action(
            self._reveal_in_sequencer, icon="select",
            tooltip="Reveal the selected clip in the Sequencer editor.",
        )
        widget.option_box.add_action(
            self.b004, icon="refresh",
            tooltip="Sync the scene frame range to the loaded clips (same as the button below).",
        )

    def tb001(self, widget=None):
        """Move the selected clip so it starts at the current frame (trim is preserved)."""
        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("No clip selected — browse for audio files first.")
            return
        import bpy

        frame = bpy.context.scene.frame_current
        if AudioUtils.move_clip(current, frame):
            self.ui.footer.setText(f"Moved '{current}' to frame {frame}.")
        else:
            self.ui.footer.setText(f"Clip '{current}' not found — refresh the combo.")

    def _reveal_in_sequencer(self):
        """Select *current* in the VSE and frame it in an open Sequencer editor, if any."""
        import bpy

        name = self.ui.cmb000.currentText()
        if not name:
            self.ui.footer.setText("Select a clip first.")
            return
        seq_ed = bpy.context.scene.sequence_editor
        strip = seq_ed.strips_all.get(name) if seq_ed else None
        if strip is None:
            self.ui.footer.setText(f"Clip '{name}' not found — refresh the combo.")
            return
        for s in seq_ed.strips_all:
            s.select = False
        strip.select = True
        seq_ed.active_strip = strip
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "SEQUENCE_EDITOR":
                    with bpy.context.temp_override(window=window, area=area):
                        try:
                            bpy.ops.sequencer.view_selected()
                        except RuntimeError as e:
                            self.logger.debug(f"view_selected failed: {e}")
                    break
        self.ui.footer.setText(f"Selected '{name}' in the Sequencer.")

    # ------------------------------------------------------------------
    # Trim
    # ------------------------------------------------------------------

    def b003(self):
        """Apply Trim — trim the selected clip's head/tail to the ``s000``/``s001`` values."""
        current = self.ui.cmb000.currentText()
        if not current:
            self.ui.footer.setText("Select a clip first.")
            return
        offset_start = int(self.ui.s000.value())
        offset_end = int(self.ui.s001.value())
        if AudioUtils.trim_clip(current, offset_start=offset_start, offset_end=offset_end):
            info = AudioUtils.get_clip(current)
            self.ui.footer.setText(
                f"Trimmed '{current}' — {info['duration']} frame(s) visible."
            )
        else:
            self.ui.footer.setText(f"Clip '{current}' not found — refresh the combo.")

    # ------------------------------------------------------------------
    # Sync Scene Range
    # ------------------------------------------------------------------

    def b004_init(self, widget):
        widget.option_box.menu.setTitle("Sync Scene Range")
        # Extend Only vs Exact Fit is a two-valued mode, not a modifier — name both states.
        fit = widget.option_box.menu.add(
            "QComboBox", setObjectName="cmb_fit",
            setToolTip=(
                "Extend Only: only grow the range to cover clips that fall outside it.\n"
                "Exact Fit: fit the range exactly to the clips (can also shrink it)."
            ),
        )
        fit.addItems(["Extend Only", "Exact Fit"])
        fit.setCurrentText("Extend Only")  # preserve prior default (checkbox on = extend only)

    def b004(self, widget=None):
        """Fit the scene frame range to the loaded clips."""
        extend_only = True
        if widget is not None:
            menu = getattr(widget.option_box, "menu", None)
            cmb = getattr(menu, "cmb_fit", None) if menu else None
            if cmb is not None:
                extend_only = cmb.currentText() == "Extend Only"
        start, end = AudioUtils.sync_scene_range(extend_only=extend_only)
        self.ui.footer.setText(f"Scene range synced to clips: {start}-{end}.")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("audio_clips", reload=True)
    ui.show(pos="screen", app_exec=True)
