# !/usr/bin/python
# coding=utf-8
"""HDR Manager tool panel — Switchboard slot wiring for the co-located ``hdr_manager.ui``.

Blender counterpart of mayatk's HDR Manager: drives the **world environment** (Environment
Texture → Background) instead of an Arnold skydome, via the ``set_world_hdri`` /
``get_world_hdri`` engine in ``_light_utils``. The Slots class lives here next to that engine
(mirror of mayatk's ``light_utils`` panel home); served by ``BlenderUiHandler``
(``marking_menu.show("hdr_manager")``).

Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency on tentacle.
The Qt-only ``uitk``/``qtpy`` helpers are deferred into their methods (headless Blender ships
no Qt binding).
"""
import os

import pythontk as ptk

from blendertk.core_utils._core_utils import undoable
from blendertk.light_utils._light_utils import (
    get_world_hdri,
    set_world_hdri,
    set_world_ray_visibility,
)


class HdrManagerSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the HDR Manager panel.

    The map combo lists ``.hdr``/``.exr`` files from a user-set folder (``txt000`` — persisted
    by the shared QSettings store, so it survives sessions). The layout mirrors mayatk's
    (``main_group`` → HDR Map / HDR Levels / HDR Rotation sub-groups) with the same widget names
    (``cmb000``/``spn_intensity``/``spn_exposure``/``slider000``/``chk000``); the folder
    row (``txt000``/``b001``) is Blender-specific. Maya's ``aiDiffuse``/``aiSpecular`` skydome
    contribution **maps** to Cycles world ray visibility (``chk_diffuse``/``chk_glossy`` — boolean
    here vs Arnold's float; EEVEE ignores it). Genuinely Arnold-only and dropped: importance-sampling
    **Resolution** (Cycles does world MIS automatically) and skydome **Samples** (Cycles samples
    globally); Maya's select-skydome/transform/file-node context actions (a Blender world isn't a
    selectable scene object). Level changes apply live once an environment is set.
    """

    HDR_PATTERNS = ["*.hdr", "*.exr"]

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hdr_manager
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[hdr_manager] ")

    def header_init(self, widget):
        """Configure header menu (reveal / open folder) + help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add(
            "QPushButton", setText="Reveal Selected HDR", setObjectName="btn_reveal_hdr",
            setToolTip="Show the selected HDR file in the OS file manager.",
        ).clicked.connect(self.reveal_selected)
        widget.menu.add(
            "QPushButton", setText="Open HDR Folder", setObjectName="btn_open_folder",
            setToolTip="Open the HDR folder in the OS file manager.",
        ).clicked.connect(self.open_folder)
        widget.set_help_text(
            fmt(
                title="HDR Manager",
                body="Light the scene with an HDR environment (world shader).",
                steps=[
                    "Set the <b>HDR folder</b> (…) — .hdr / .exr files are listed.",
                    "Pick a map to light the scene.",
                    "Drive <b>Intensity</b> / <b>Exposure</b> / <b>Rotation</b> live.",
                ],
                sections=[
                    ("Options", [
                        "<b>Visible</b> off keeps the HDR lighting the scene but "
                        "renders a transparent background.",
                        "Strength = Intensity × 2^Exposure (stops).",
                    ]),
                ],
            )
        )

    # ------------------------------------------------------------------ helpers
    def _populate_maps(self):
        """Fill the map combo from the folder field (label → filepath)."""
        folder = self.ui.txt000.text().strip()
        files = (
            ptk.get_dir_contents(folder, "filepath", inc_files=self.HDR_PATTERNS)
            if folder and os.path.isdir(folder)
            else []
        )
        data = {os.path.basename(f): f for f in sorted(files)}
        # Block signals so the rebuild doesn't fire cmb000 → auto-apply an HDR
        # while we're merely repopulating the list (on init / folder change).
        self.ui.cmb000.blockSignals(True)
        try:
            self.ui.cmb000.add(data, header="HDR Map:", clear=True)
        finally:
            self.ui.cmb000.blockSignals(False)

    def _apply_levels(self):
        """Re-apply intensity/exposure/rotation/visibility to an already-set environment
        (no-op until a map has been selected)."""
        if get_world_hdri() is None:
            return
        set_world_hdri(
            None,
            strength=self._strength(),
            rotation=float(self.ui.slider000.value()),
            visible=self.ui.chk000.isChecked(),
        )

    def _strength(self):
        return self.ui.spn_intensity.value() * (2.0 ** self.ui.spn_exposure.value())

    # ------------------------------------------------------------------ slots
    def cmb000_init(self, widget):
        """Populate the HDR map combo (the folder field restores before first show)."""
        self._populate_maps()

    def txt000(self, text, widget):
        """HDR folder changed — re-scan the map list."""
        self._populate_maps()

    def b001(self):
        """Browse for the HDR folder."""
        from qtpy import QtWidgets

        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui, "HDR Folder", self.ui.txt000.text()
        )
        if folder:
            self.ui.txt000.setText(folder)  # textChanged repopulates the combo

    def cmb000(self, index, widget):
        """HDR map selection — build/update the world environment from the pick.

        Selecting a map applies it (the panel's sole apply action; mirrors
        mayatk, which dropped its separate **Set HDR** button). The combo's
        header row carries no data, so an empty pick is a no-op.
        """
        if self.ui.cmb000.currentData():
            self._apply_selection()

    @undoable
    def _apply_selection(self):
        """Build/update the world environment from the selected map.

        Invoked from :meth:`cmb000` on selection — re-reads the dropdown so it
        always applies the current pick.
        """
        path = self.ui.cmb000.currentData()
        if not path:
            self.sb.message_box("Pick an HDR map first (set the folder via <b>…</b>).")
            return
        set_world_hdri(
            path,
            strength=self._strength(),
            rotation=float(self.ui.slider000.value()),
            visible=self.ui.chk000.isChecked(),
        )
        self._apply_ray_visibility()
        self.sb.message_box(f"HDR set: <hl>{os.path.basename(path)}</hl>")

    def spn_intensity(self, value, widget):
        """Intensity — live update."""
        self._apply_levels()

    def spn_exposure(self, value, widget):
        """Exposure (stops) — live update."""
        self._apply_levels()

    def slider000(self, value, widget):
        """Rotation — live update."""
        self._apply_levels()

    def chk000(self, state, widget):
        """Visible — live update."""
        self._apply_levels()

    def _apply_ray_visibility(self):
        """Push the Diffuse / Glossy toggles to the world's Cycles ray visibility (no-op off-Cycles)."""
        set_world_ray_visibility(
            diffuse=self.ui.chk_diffuse.isChecked(),
            glossy=self.ui.chk_glossy.isChecked(),
        )

    def chk_diffuse(self, state, widget):
        """Diffuse contribution (Cycles ray visibility) — live update."""
        self._apply_ray_visibility()

    def chk_glossy(self, state, widget):
        """Glossy contribution (Cycles ray visibility) — live update."""
        self._apply_ray_visibility()

    def reveal_selected(self):
        """Reveal the selected HDR file in the OS file manager."""
        self._reveal(self.ui.cmb000.currentData(), "Pick an HDR map first.")

    def open_folder(self):
        """Open the HDR folder in the OS file manager."""
        self._reveal(self.ui.txt000.text().strip(), "Set the HDR folder first (via <b>…</b>).")

    def _reveal(self, path, empty_msg):
        """Reveal ``path`` in the OS file manager, or warn with ``empty_msg`` when it's blank."""
        if not path:
            self.sb.message_box(empty_msg)
            return
        try:
            ptk.FileUtils.reveal_in_file_manager(path)
        except (FileNotFoundError, OSError) as e:
            self.sb.message_box(str(e))


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("hdr_manager", reload=True)
    ui.show(pos="screen", app_exec=True)
