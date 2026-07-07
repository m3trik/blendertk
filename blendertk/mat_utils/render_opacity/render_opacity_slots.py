# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Opacity panel (``render_opacity.ui``).

Provides ``RenderOpacitySlots`` — a standalone window for creating, keying fades, and removing
per-object opacity in Blender. Mirror of mayatk's ``render_opacity_slots`` (same objectNames,
same widget tree, same method names/signal-connection order); delegates all logic to
:class:`RenderOpacity`. Discovered by ``BlenderUiHandler`` (``marking_menu.show("render_opacity")``).
``__init__`` is Qt-only (no ``bpy``) so the panel loads under the workspace ``.venv`` — the
selection-changed subscription is wrapped in a try/except that no-ops without a running Blender.
"""
import logging

import pythontk as ptk

from blendertk.core_utils._core_utils import selected_objects, undoable
from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity


class RenderOpacitySlots(ptk.LoggingMixin):
    """Switchboard slots for the Render Opacity UI.

    Layout
    ------
    - **Header**: Title bar.
    - **Apply**: Mode combo (Attribute/Material) + apply button.
    - **Key**: Frames spinner, fade in/out, end-at-playhead.
    - **Manage**: Remove opacity artifacts.
    - **Footer**: Status messages.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_opacity
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[render_opacity] ")

        # Wire plain QPushButton widgets (not auto-connected by switchboard)
        self.ui.b000.clicked.connect(self._apply_opacity)
        self.ui.b003.clicked.connect(self._remove_opacity)

        # Selection-changed job to enable/disable fade controls (Blender counterpart of mayatk's
        # ScriptJobManager "SelectionChanged" subscription). Guarded: ScriptJobManager only
        # touches bpy the first time an event is actually installed, which would raise under the
        # headless workspace .venv used for structural tests — swallow and stay disabled there.
        self._is_updating = False  # Reentrancy guard
        self._sel_token = None
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager

            mgr = ScriptJobManager.instance()
            self._sel_token = mgr.subscribe(
                "SelectionChanged",
                self._update_fade_enabled,
                owner=self,
                ephemeral=True,
            )
            mgr.connect_cleanup(self.ui, owner=self)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add("Separator", setTitle="Options")
        widget.menu.add(
            "QCheckBox",
            setText="Last Selected Only",
            setObjectName="chk_last_selected",
            setChecked=False,
            setToolTip=fmt(
                body="Applies to Create, Key, and Remove operations.",
                bullets=[
                    "<b>On:</b> Only the active object is processed.",
                    "<b>Off:</b> All selected objects are processed.",
                ],
            ),
        )
        widget.menu.add(
            "QCheckBox",
            setText="Delete Visibility Keys",
            setObjectName="chk_delete_vis_keys",
            setChecked=False,
            setToolTip=fmt(
                bullets=[
                    "<b>On:</b> Existing visibility keyframes are deleted before applying opacity.",
                    "<b>Off:</b> Objects with visibility keys are skipped with a warning.",
                ],
            ),
        )
        widget.set_help_text(
            fmt(
                title="Render Opacity",
                body="Add a keyable <b>opacity</b> custom property (0-1) to objects for "
                "engine-ready transparency control. The <b>Mode</b> combo is kept for parity "
                "with Maya's Material/Attribute split; <b>Key Render Opacity</b> drives fades "
                "in either mode and mirrors them onto render visibility so the FBX carries both "
                "channels (Unity rebuilds opacity from the visibility curve).",
                steps=[
                    "Select one or more objects.",
                    "Pick a <b>Mode</b> (both behave the same in Blender — see notes).",
                    "Press <b>Create</b>. If nothing is selected, a cube is created first.",
                    "Press <b>Key Render Opacity</b> to animate a fade. The option box (▸) "
                    "configures <b>Frames</b>, <b>End at Playhead</b>, and <b>Direction</b> "
                    "(Fade In / Fade Out / Auto).",
                ],
                sections=[
                    ("Header menu", [
                        "<b>Last Selected Only</b> — only the active object participates in "
                        "Create / Key / Remove.",
                        "<b>Delete Visibility Keys</b> — when on, existing visibility keys are "
                        "removed before Create; when off, objects with vis keys are skipped "
                        "with a warning.",
                    ]),
                ],
                notes=[
                    "Use <b>Remove Opacity</b> to clean up every artifact (property, Alpha "
                    "driver, keys) the tool added.",
                    "Blender divergences: no StingrayPBS (both modes drive Principled Alpha); "
                    "the visibility channel is the object's <i>hide_render</i> (m_Enabled "
                    "analogue); materials are made single-user so opacity is per-object.",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    @staticmethod
    def _select(objects):
        """Re-select *objects*, replacing the current selection.

        Mirror of mayatk's ``cmds.select(objects, replace=True)`` — keeps the user's
        selection normalized after Create/Remove even if the operation touched scene
        state (e.g. a material-slot swap) along the way.
        """
        import bpy

        for o in bpy.context.view_layer.objects:
            o.select_set(o in objects)
        if objects:
            bpy.context.view_layer.objects.active = objects[-1]

    def _get_selected(self):
        """Return the effective selection, respecting 'Last Selected Only'.

        When the header checkbox is checked and the selection is non-empty,
        only the active object is returned.
        """
        objects = selected_objects()
        if objects and self.ui.header.menu.chk_last_selected.isChecked():
            import bpy

            active = bpy.context.view_layer.objects.active
            return [active] if active in objects else objects[-1:]
        return objects

    @undoable
    def _apply_opacity(self):
        """Apply Render Opacity to selected objects (or create a cube first)."""
        import bpy

        mode = self.ui.cmb_mode.currentText().lower()

        objects = self._get_selected()
        if not objects:
            bpy.ops.mesh.primitive_cube_add()
            cube = bpy.context.active_object
            cube.name = "opacity_cube"
            objects = [cube]

        label = ", ".join(o.name for o in objects[:5])
        if len(objects) > 5:
            label += f" … (+{len(objects) - 5} more)"

        delete_vis = self.ui.header.menu.chk_delete_vis_keys.isChecked()

        try:
            results = RenderOpacity.create(
                objects, mode=mode, delete_visibility_keys=delete_vis
            )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            self._select(objects)

        self.ui.footer.setText(
            f"{mode.title()} opacity → {len(results)} object(s): {label}"
        )
        self._update_fade_enabled()

    # ------------------------------------------------------------------
    # Keyframe Fade
    # ------------------------------------------------------------------

    def tb000_init(self, widget):
        """Key Render Opacity Init — configure option-box menu."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.option_box.menu.setTitle("Key Render Opacity")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Frames: ",
            setObjectName="s000",
            setMinimum=1,
            setMaximum=1000,
            setValue=15,
            setToolTip="Number of frames over which the fade occurs.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="End at Playhead",
            setObjectName="chk000",
            setChecked=True,
            setToolTip=fmt(
                bullets=[
                    "<b>On:</b> Fade ends at the playhead (current−frames → current).",
                    "<b>Off:</b> Fade starts at the playhead (current → current+frames).",
                ],
            ),
        )
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_direction",
            setToolTip=fmt(
                title="Fade Direction",
                bullets=[
                    "<b>Fade In:</b> Key opacity 0 → 1.",
                    "<b>Fade Out:</b> Key opacity 1 → 0.",
                    "<b>Auto:</b> Detect from the previous key — if last value "
                    "≥ 0.5 → fade out; if < 0.5 or no key → fade in.",
                ],
            ),
        )
        for text, data in [
            ("Fade In", "in"),
            ("Fade Out", "out"),
            ("Auto", "auto"),
        ]:
            cmb.addItem(text, data)
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Create if Missing",
            setObjectName="chk_auto_create",
            setChecked=True,
            setToolTip=(
                "When checked, automatically creates the opacity property\n"
                "on selected objects that don't have one."
            ),
        )

    @undoable
    def tb000(self, widget):
        """Key Render Opacity — key a fade on the opacity property (+ mirror to visibility)."""
        import bpy

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                "Select objects with an <hl>opacity</hl> property."
            )
            return

        frames = widget.option_box.menu.s000.value()
        ends_at_cursor = widget.option_box.menu.chk000.isChecked()
        cmb = widget.option_box.menu.cmb_direction
        direction_mode = cmb.currentData()
        auto_create = widget.option_box.menu.chk_auto_create.isChecked()

        current = bpy.context.scene.frame_current

        if ends_at_cursor:
            start, end = current - frames, current
        else:
            start, end = current, current + frames

        # Suppress the SelectionChanged callback while we modify the scene (props/drivers/keys)
        # to prevent reentrant depsgraph evaluation — mirror of mayatk's ScriptJobManager.suppress.
        from blendertk.core_utils.script_job_manager import ScriptJobManager

        mgr = ScriptJobManager.instance()
        if self._sel_token is not None:
            mgr.suppress(self._sel_token)
        try:
            keyed = RenderOpacity.key_fade(
                objects,
                start=start,
                end=end,
                direction=direction_mode,
                auto_create=auto_create,
            )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            if self._sel_token is not None:
                mgr.resume(self._sel_token)

        if keyed:
            dirs = {"Fade In" if d == "in" else "Fade Out" for _, d in keyed}
            direction = " / ".join(sorted(dirs))
            self.ui.footer.setText(
                f"{direction}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
            )
        else:
            self.sb.message_box(
                "Warning: Selected objects have no <hl>opacity</hl> property.<br>"
                "Use <b>Create</b> first."
            )

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @undoable
    def _remove_opacity(self):
        """Remove all opacity artifacts from selected objects."""
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                "Select objects to remove opacity from."
            )
            return

        label = ", ".join(o.name for o in objects[:5])
        if len(objects) > 5:
            label += f" … (+{len(objects) - 5} more)"

        try:
            RenderOpacity.remove(objects)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        finally:
            self._select(objects)

        self.ui.footer.setText(
            f"Opacity removed from {len(objects)} object(s): {label}"
        )
        self._update_fade_enabled()

    # ------------------------------------------------------------------
    # Selection job — enable/disable fade controls
    # ------------------------------------------------------------------

    def _update_fade_enabled(self):
        """Enable/disable fade widgets based on whether selection has opacity.

        Also re-establishes the Alpha-driver connections that may have been lost (e.g. after a
        material reassignment) so the user always operates on a healthy object.
        """
        # Reentrancy guard — ensure_connections modifies node trees, which can trigger further
        # depsgraph updates and re-enter this callback.
        if self._is_updating:
            return
        self._is_updating = True
        try:
            # Guard: skip if the UI has been destroyed (prevents crash when the callback fires
            # after the widget is garbage-collected).
            if not self.ui or not self.ui.isVisible():
                return

            selected = selected_objects()
            if not selected:
                for item in self.ui.tb000.option_box.menu.get_items():
                    item.setEnabled(False)
                return

            # Defer scene-modifying work out of the depsgraph-update callback context to prevent
            # reentrant evaluation (Blender counterpart of Maya's cmds.evalDeferred).
            import bpy

            names = [o.name for o in selected]

            def _ensure(_names=names):
                objs = [bpy.data.objects.get(n) for n in _names]
                objs = [o for o in objs if o is not None]
                if objs:
                    RenderOpacity.ensure_connections(objs)

            bpy.app.timers.register(_ensure, first_interval=0.0)

            has_opacity = any(RenderOpacity.ATTR_NAME in obj for obj in selected)
            for item in self.ui.tb000.option_box.menu.get_items():
                item.setEnabled(has_opacity)
        except RuntimeError:
            pass  # Deleted C++ object — swallow to prevent crash
        except Exception:
            logging.getLogger(__name__).debug(
                "_update_fade_enabled error", exc_info=True
            )
        finally:
            self._is_updating = False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("render_opacity", reload=True)
    ui.show(pos="screen", app_exec=True)
