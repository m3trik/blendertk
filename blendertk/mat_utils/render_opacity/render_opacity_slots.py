# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Opacity panel (``render_opacity.ui``).

``RenderOpacitySlots`` — create per-object opacity, key fades (with visibility mirroring), and
remove it. Mirror of mayatk's ``render_opacity_slots``; delegates all logic to :class:`RenderOpacity`.
Discovered by ``BlenderUiHandler`` (``marking_menu.show("render_opacity")``). ``__init__`` is Qt-only
(no ``bpy``) so the panel loads under the workspace ``.venv``.
"""
import pythontk as ptk

from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity


class RenderOpacitySlots(ptk.LoggingMixin):
    """Slots for the Render Opacity panel (Create / Key fade / Remove)."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_opacity
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[render_opacity] ")
        self.ui.b000.clicked.connect(self._apply_opacity)
        self.ui.b003.clicked.connect(self._remove_opacity)

    # ------------------------------------------------------------------ header
    def header_init(self, widget):
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add("Separator", setTitle="Options")
        widget.menu.add(
            "QCheckBox", setText="Last Selected Only", setObjectName="chk_last_selected",
            setChecked=False,
            setToolTip="On: only the active object is processed (Create / Key / Remove). "
            "Off: all selected objects.",
        )
        widget.menu.add(
            "QCheckBox", setText="Delete Visibility Keys", setObjectName="chk_delete_vis_keys",
            setChecked=False,
            setToolTip="On: existing render-visibility keys are deleted before Create. "
            "Off: objects with visibility keys are skipped with a warning.",
        )
        widget.set_help_text(
            fmt(
                title="Render Opacity",
                body="Add a keyable <b>opacity</b> custom property (0-1) to objects for engine-ready "
                "transparency, driving each material's Principled BSDF Alpha for viewport feedback. "
                "<b>Key Render Opacity</b> animates a fade and mirrors it to render visibility so the "
                "FBX carries both channels (Unity rebuilds opacity from the visibility curve).",
                steps=[
                    "Select one or more objects.",
                    "Press <b>Create</b> to add the opacity property + Alpha driver.",
                    "Press <b>Key Render Opacity</b> to animate a fade. The option box (▸) sets "
                    "<b>Frames</b>, <b>End at Playhead</b>, and <b>Direction</b> (In / Out / Auto).",
                    "Use <b>Remove Opacity</b> to strip every artifact the tool added.",
                ],
                sections=[
                    ("Header menu", [
                        "<b>Last Selected Only</b> — only the active object participates.",
                        "<b>Delete Visibility Keys</b> — clear existing visibility keys before Create "
                        "(else such objects are skipped with a warning).",
                    ]),
                ],
                notes=[
                    "Blender divergences: no StingrayPBS (both modes drive Principled Alpha); the "
                    "visibility channel is the object's <i>hide_render</i> (m_Enabled analogue); "
                    "materials are made single-user so opacity is per-object.",
                ],
            )
        )

    # ------------------------------------------------------------------ selection
    def _get_selected(self):
        import bpy

        objects = [o for o in (bpy.context.selected_objects or []) if o]
        if objects and self.ui.header.menu.chk_last_selected.isChecked():
            active = bpy.context.view_layer.objects.active
            return [active] if active in objects else objects[-1:]
        return objects

    # ------------------------------------------------------------------ apply / remove
    def _apply_opacity(self):
        import bpy

        mode = self.ui.cmb_mode.currentText().lower()
        objects = self._get_selected()
        if not objects:
            self.sb.message_box("<strong>Nothing selected.</strong><br>Select object(s) first.")
            return
        delete_vis = self.ui.header.menu.chk_delete_vis_keys.isChecked()
        try:
            results = RenderOpacity.create(objects, mode=mode, delete_visibility_keys=delete_vis)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        self.ui.footer.setText(f"{mode.title()} opacity → {len(results)} object(s).")

    def _remove_opacity(self):
        objects = self._get_selected()
        if not objects:
            self.sb.message_box("<strong>Nothing selected.</strong>")
            return
        try:
            RenderOpacity.remove(objects)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        self.ui.footer.setText(f"Opacity removed from {len(objects)} object(s).")

    # ------------------------------------------------------------------ key fade (tb000 + option box)
    def tb000_init(self, widget):
        """Configure the Key Render Opacity option box."""
        widget.option_box.menu.setTitle("Key Render Opacity")
        widget.option_box.menu.add(
            "QSpinBox", setPrefix="Frames: ", setObjectName="s000",
            setMinimum=1, setMaximum=1000, setValue=15,
            setToolTip="Number of frames over which the fade occurs.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="End at Playhead", setObjectName="chk000", setChecked=True,
            setToolTip="On: fade ends at the playhead (current−frames → current). "
            "Off: fade starts at the playhead (current → current+frames).",
        )
        cmb = widget.option_box.menu.add(
            "QComboBox", setObjectName="cmb_direction",
            setToolTip="Fade In: opacity 0→1. Fade Out: 1→0. Auto: detect from the previous key.",
        )
        for text, data in (("Fade In", "in"), ("Fade Out", "out"), ("Auto", "auto")):
            cmb.addItem(text, data)
        widget.option_box.menu.add(
            "QCheckBox", setText="Create if Missing", setObjectName="chk_auto_create",
            setChecked=True,
            setToolTip="Create the opacity property on selected objects that lack it before keying.",
        )

    def tb000(self, widget):
        """Key Render Opacity — key a fade on the opacity property (+ mirror to visibility)."""
        import bpy

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected.</strong><br>Select objects with an "
                "<hl>opacity</hl> property."
            )
            return

        menu = widget.option_box.menu
        frames = menu.s000.value()
        ends_at_cursor = menu.chk000.isChecked()
        direction = menu.cmb_direction.currentData()
        auto_create = menu.chk_auto_create.isChecked()

        current = bpy.context.scene.frame_current
        start, end = (current - frames, current) if ends_at_cursor else (current, current + frames)

        try:
            keyed = RenderOpacity.key_fade(
                objects, start=start, end=end, direction=direction, auto_create=auto_create
            )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        if keyed:
            dirs = " / ".join(sorted({"Fade In" if d == "in" else "Fade Out" for _, d in keyed}))
            self.ui.footer.setText(
                f"{dirs}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
            )
        else:
            self.sb.message_box(
                "Warning: Selected objects have no <hl>opacity</hl> property.<br>Use "
                "<b>Create</b> first."
            )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("render_opacity", reload=True)
    ui.show(pos="screen", app_exec=True)
