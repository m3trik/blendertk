# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Effects panel (``render_effects.ui``).

Provides ``RenderEffectsSlots`` -- two key tools, one per render-effect channel:
**Key Opacity Fade** and **Key Highlight Pulse**. Each creates its channel on the
selection when missing and keys it; lookdev is the WebXR push, which shows
the deliverable itself, so nothing in the scene changes for a preview. Each tool's
option box carries a remove action that strips that channel again -- there is
no separate Create / Manage section. Mirror of mayatk's ``render_effects_slots``
(same objectNames, same widget tree, same method names); delegates all logic to
:class:`RenderEffects`. Discovered by ``BlenderUiHandler``
(``marking_menu.show("render_effects")``). ``__init__`` is Qt-only (no ``bpy``)
so the panel loads under the workspace ``.venv`` -- the selection-changed
subscription is wrapped in a try/except that no-ops without a running Blender.
"""

import logging

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.mat_utils.render_opacity.render_effects import RenderEffects


class RenderEffectsSlots(ptk.LoggingMixin):
    """Switchboard slots for the Render Effects UI.

    Layout
    ------
    - **Header**: Title bar; menu holds Last Selected Only and Delete
      Visibility Keys.
    - **Key**: Key Opacity Fade (``tb000``) and Key Highlight Pulse (``tb001``);
      each option box = the tool's options plus a remove-channel action.
    - **Footer**: Status messages.
    """

    #: Channel name per key tool (mayatk keys these by ``ChannelSpec``).
    OPACITY = RenderEffects.ATTR_NAME
    HIGHLIGHT = RenderEffects.HIGHLIGHT_ATTR

    #: Default seconds for each pulse gap: one cycle's own transition at the
    #: default cadence (25% of a 2.86 s period), so the ends of the train are
    #: shaped like every beat inside it.
    PULSE_GAP_DEFAULT = 0.72

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_effects
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[render_effects] ")
        self._pulse_color = None  # (r, g, b) picked in the pulse option box
        self._remove_actions = {}  # channel name -> option-box ActionOption

        # Selection-changed job: the remove actions are live only while the
        # selection carries their channel (Blender counterpart of mayatk's
        # ScriptJobManager "SelectionChanged" subscription). Guarded:
        # ScriptJobManager only touches bpy the first time an event is actually
        # installed, which would raise under the headless workspace .venv used
        # for structural tests -- swallow and stay disabled there.
        self._is_updating = False  # Reentrancy guard
        self._sel_token = None
        try:
            from blendertk.core_utils.script_job_manager import ScriptJobManager

            mgr = ScriptJobManager.instance()
            self._sel_token = mgr.subscribe(
                "SelectionChanged",
                self._update_remove_enabled,
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
        widget.menu.add("Separator", setTitle="Options")
        widget.menu.add(
            "QCheckBox",
            setText="Last Selected Only",
            setObjectName="chk_last_selected",
            setChecked=False,
            setToolTip=self.sb.tooltip.fmt(
                body="Applies to every Key and Remove action.",
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
            setToolTip=self.sb.tooltip.fmt(
                body="When Key Opacity Fade first gives an object its opacity "
                "property:",
                bullets=[
                    "<b>On:</b> The object's existing render-visibility keys are "
                    "deleted first.",
                    "<b>Off:</b> They are kept; the fade's visibility mirror is "
                    "keyed over them.",
                ],
            ),
        )
        widget.menu.add("Separator", setTitle="Actions")
        btn = widget.menu.add(
            "QPushButton",
            setText="Highlight Colour…",
            setObjectName="b_highlight_color",
            setToolTip=self.sb.tooltip.fmt(
                body="Restate the highlight colour on objects that already "
                "carry the channel. The pulse keys are never touched, so a "
                "signed-off look can be revised without re-keying.",
                bullets=[
                    "<b>With a selection:</b> re-colours those objects.",
                    "<b>With none:</b> re-colours every highlighted object in "
                    "the scene, after confirming.",
                ],
            ),
        )
        btn.clicked.connect(self._revise_highlight_color)
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Render Effects",
                body="Key per-object render effects for engine-ready control: an "
                "<b>Opacity</b> fade (alpha in the GLB, a Unity controller via "
                "FBX) or a <b>Highlight</b> pulse (an additive emissive glow "
                "with a per-object colour). Each tool creates its channel on the "
                "selection when missing, so keying is the only step.",
                steps=[
                    "Select one or more objects.",
                    "Press <b>Key Opacity Fade</b> to key a fade, or "
                    "<b>Key Highlight Pulse</b> to key a repeating glow. Each "
                    "option box (▸) configures timing; the pulse box also sets "
                    "the colour.",
                    "The ⊗ action in each option box removes that channel "
                    "(property and keys) from the selection.",
                    "Preview the result with the WebXR push: it shows the "
                    "deliverable itself, so nothing in the scene is changed "
                    "for lookdev.",
                ],
                sections=[
                    (
                        "Header menu",
                        [
                            "<b>Last Selected Only</b> — only the active object "
                            "participates.",
                            "<b>Delete Visibility Keys</b> — clear an object's "
                            "render-visibility keys when it first receives the "
                            "opacity property.",
                            "<b>Highlight Colour…</b> — restate the colour on "
                            "already-keyed objects; the selection, or the whole "
                            "scene when nothing is selected.",
                        ],
                    ),
                ],
                notes=[
                    "Blender divergences: no StingrayPBS (Principled Alpha / "
                    "Emission carry both channels); the visibility channel is the "
                    "object's <i>hide_render</i> (m_Enabled analogue).",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    def _get_selected(self):
        """Return the effective selection, respecting 'Last Selected Only'.

        When the header checkbox is checked and the selection is non-empty,
        only the active object is returned.
        """
        objects = CoreUtils.selected_objects()
        if objects and self.ui.header.menu.chk_last_selected.isChecked():
            import bpy

            active = bpy.context.view_layer.objects.active
            return [active] if active in objects else objects[-1:]
        return objects

    def _key_options(self):
        """The header options every key tool forwards to the facade."""
        menu = self.ui.header.menu
        return {
            "delete_visibility_keys": menu.chk_delete_vis_keys.isChecked(),
        }

    def _add_remove_action(self, widget, channel: str):
        """Give a key tool's option box the action that removes its channel.

        Kept apart from the option items (an action, not a setting) and
        gated by the selection job: enabled only while the selection carries
        the channel.
        """
        self._remove_actions[channel] = widget.option_box.set_action(
            callback=lambda channel=channel: self._remove_channel(channel),
            icon="circle_remove",
            tooltip=self.sb.tooltip.fmt(
                title=f"Remove {channel.title()}",
                body=f"Strip the <b>{channel}</b> channel from the selection: "
                "the property, its keys and any material drivers.",
            ),
        )
        self._remove_actions[channel].widget.setEnabled(
            self._selection_carries(channel)
        )

    @staticmethod
    def _selection_carries(prop: str) -> bool:
        """Whether any selected object carries *prop* (bpy-free when Blender is absent)."""
        try:
            return any(prop in obj for obj in CoreUtils.selected_objects())
        except Exception:
            return False

    @staticmethod
    def _key_range(frames, ends_at_cursor):
        import bpy

        current = bpy.context.scene.frame_current
        if ends_at_cursor:
            return current - frames, current
        return current, current + frames

    @staticmethod
    def _label(objects):
        label = ", ".join(o.name for o in objects[:5])
        if len(objects) > 5:
            label += f" … (+{len(objects) - 5} more)"
        return label

    def _suppressed(self):
        """The selection job silenced for a scene-mutating block (None token = no-op)."""
        from blendertk.core_utils.script_job_manager import ScriptJobManager

        return ScriptJobManager.instance().suppressed(self._sel_token)

    # ------------------------------------------------------------------
    # Key: opacity fade
    # ------------------------------------------------------------------

    def tb000_init(self, widget):
        """Key Opacity Fade Init — configure option-box menu."""
        widget.option_box.menu.setTitle("Key Opacity Fade")
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
            setToolTip=self.sb.tooltip.fmt(
                bullets=[
                    "<b>On:</b> Fade ends at the playhead (current−frames → current).",
                    "<b>Off:</b> Fade starts at the playhead (current → current+frames).",
                ],
            ),
        )
        cmb = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_direction",
            setToolTip=self.sb.tooltip.fmt(
                title="Fade Direction",
                bullets=[
                    "<b>Fade In:</b> Key opacity 0 → 1.",
                    "<b>Fade Out:</b> Key opacity 1 → 0.",
                    # '<' must be escaped: Qt auto-detects this string as rich
                    # text, so a bare '<' opens a tag and swallows the rest of
                    # the bullet up to the next '>'.
                    "<b>Auto:</b> Detect from the previous key — if last value "
                    "≥ 0.5 → fade out; if &lt; 0.5 or no key → fade in.",
                ],
            ),
        )
        for text, data in [
            ("Fade In", "in"),
            ("Fade Out", "out"),
            ("Auto", "auto"),
        ]:
            cmb.addItem(text, data)
        self._add_remove_action(widget, self.OPACITY)

    @CoreUtils.undoable
    def tb000(self, widget):
        """Key Opacity Fade — key a fade on the opacity property (created if missing)."""
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>Select objects to fade."
            )
            return

        frames = widget.option_box.menu.s000.value()
        ends_at_cursor = widget.option_box.menu.chk000.isChecked()
        direction_mode = widget.option_box.menu.cmb_direction.currentData()
        start, end = self._key_range(frames, ends_at_cursor)

        # Suppress the SelectionChanged callback while we modify the scene
        # (props/drivers/keys) to prevent reentrant depsgraph evaluation.
        try:
            with self._suppressed():
                keyed = RenderEffects.key_fade(
                    objects,
                    start=start,
                    end=end,
                    direction=direction_mode,
                    channel=self.OPACITY,
                    **self._key_options(),
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        dirs = {"Fade In" if d == "in" else "Fade Out" for _, d in keyed}
        direction = " / ".join(sorted(dirs)) or "Fade"
        self.ui.footer.setText(
            f"{direction}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
        )
        self._update_remove_enabled()

    # ------------------------------------------------------------------
    # Key: highlight pulse
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Key Highlight Pulse Init — configure option-box menu."""
        widget.option_box.menu.setTitle("Key Highlight Pulse")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Frames: ",
            setObjectName="s001",
            setMinimum=1,
            setMaximum=100000,
            setValue=120,
            setToolTip="Length of the pulse, in frames.",
        )
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Period: ",
            setSuffix=" s",
            setObjectName="s002",
            setMinimum=0.1,
            setMaximum=60.0,
            setSingleStep=0.1,
            setDecimals=2,
            setValue=2.86,
            setToolTip="One bright/dim cycle, in seconds (2.86 s measured on the WebXR reference).",
        )
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Bright: ",
            setSuffix=" %",
            setObjectName="s003",
            setMinimum=1,
            setMaximum=99,
            setValue=59,
            setToolTip="Share of each cycle spent bright (59% measured on the reference).",
        )
        gaps = [
            widget.option_box.menu.add(
                "QDoubleSpinBox",
                setPrefix=prefix,
                setSuffix=" s",
                setObjectName=name,
                setMinimum=0.0,
                setMaximum=60.0,
                setSingleStep=0.1,
                setDecimals=2,
                setValue=self.PULSE_GAP_DEFAULT,
                setToolTip=self.sb.tooltip.fmt(
                    body=tip,
                    bullets=[
                        "The pulse starts and ends UNHIGHLIGHTED -- a curve "
                        "holds its first value backwards and its last forwards, "
                        "so a pulse that opened bright glowed for the whole "
                        "timeline before it.",
                        "The default matches one cycle's own transition, so the "
                        "ends read like every beat in between.",
                        "<b>0</b> cuts as hard as the frame grid allows -- one frame.",
                        "Unlock a field to set the two ends apart.",
                    ],
                ),
            )
            for name, prefix, tip in (
                (
                    "s004",
                    "Lead-in: ",
                    "Seconds the glow takes to come up at the start.",
                ),
                ("s005", "Lead-out: ", "Seconds it takes to fall away at the end."),
            )
        ]
        # One look, two ends: they move together unless the artist unlocks one.
        self.sb.link_spinboxes(self.ui, gaps, initial=True)
        widget.option_box.menu.add(
            "QCheckBox",
            setText="End at Playhead",
            setObjectName="chk001",
            setChecked=False,
            setToolTip=self.sb.tooltip.fmt(
                bullets=[
                    "<b>On:</b> Pulse ends at the playhead.",
                    "<b>Off:</b> Pulse starts at the playhead.",
                ],
            ),
        )
        btn = widget.option_box.menu.add(
            "QPushButton",
            setText="Colour…",
            setObjectName="b_pulse_color",
            setToolTip=self.sb.tooltip.fmt(
                body="Pick the colour written to the objects' highlightColor.",
                bullets=[
                    "<b>With a selection:</b> re-colours those objects now, "
                    "keys untouched.",
                    "<b>With none:</b> seeds the next Key Highlight Pulse.",
                ],
            ),
        )
        btn.clicked.connect(self._pick_pulse_color)
        self._add_remove_action(widget, self.HIGHLIGHT)

    @CoreUtils.undoable
    def _apply_highlight_color(self, objects, color) -> list:
        """One undo step for the whole re-colour, however many objects it spans."""
        return RenderEffects.set_channel_color(
            objects, color=color, channel=self.HIGHLIGHT
        )

    def _ask_highlight_color(self, objects=None):
        """The colour dialog, seeded from what *objects* already carry.

        Seeding from the authored value rather than the last pick is what makes
        this a revision rather than a guess: the dialog opens on the colour that
        is actually on the objects. ``None`` when the artist cancels.
        """
        from qtpy import QtGui, QtWidgets

        seed = None
        if objects:
            authored = RenderEffects.channel_colors(objects, channel=self.HIGHLIGHT)
            if authored:
                seed = next(iter(authored.values()))
        seed = seed or self._pulse_color or (0.2, 0.5, 1.0)
        # A colour property may legitimately hold >1 (HDR emission); the dialog
        # cannot, so the SEED is clamped while the authored value is left alone.
        initial = QtGui.QColor.fromRgbF(*(min(1.0, max(0.0, float(c))) for c in seed))
        color = QtWidgets.QColorDialog.getColor(initial, self.ui, "Highlight Colour")
        if not color.isValid():
            return None
        return (color.redF(), color.greenF(), color.blueF())

    def _revise_highlight_color(self):
        """Header action: re-colour authored highlights, selection or whole scene."""
        objects = self._get_selected()
        if not objects:
            objects = RenderEffects.objects_with_channel(self.HIGHLIGHT)
            if not objects:
                self.sb.message_box(
                    "<strong>Nothing to re-colour</strong>.<br>"
                    "No object in the scene carries the highlight channel."
                )
                return
            # Scene-wide is the point of this action, but it is also the one
            # shape a mis-click cannot undo by eye: say how many first.
            prompt = (
                f"Re-colour <strong>every</strong> highlighted object in the "
                f"scene ({len(objects)})?<br>"
                "Select objects first to narrow it."
            )
            if self.sb.message_box(prompt, "Yes", "No") != "Yes":
                return

        color = self._ask_highlight_color(objects)
        if color is None:
            return
        self._pulse_color = color
        written = self._apply_highlight_color(objects, color)
        self.ui.footer.setText(
            "Highlight colour "
            + ", ".join(f"{c:.2f}" for c in color)
            + f" — set on {len(written)} object(s)"
        )

    def _pick_pulse_color(self):
        """Pick the highlight colour: re-colour the selection, and seed the next pulse."""
        selected = self._get_selected()
        color = self._ask_highlight_color(selected)
        if color is None:
            return
        self._pulse_color = color
        text = "Highlight colour: " + ", ".join(f"{c:.2f}" for c in color)
        # A live selection means the artist is revising objects that are already
        # keyed, not setting up the next pulse: write it through so the change
        # lands now. The colour is its own property, so the keys are untouched.
        if selected:
            written = self._apply_highlight_color(selected, color)
            if written:
                text += f" — set on {len(written)} object(s)"
        self.ui.footer.setText(text)

    @CoreUtils.undoable
    def tb001(self, widget):
        """Key Highlight Pulse — key a repeating glow on the highlight property (created if missing)."""
        import bpy

        menu = widget.option_box.menu
        frames = menu.s001.value()
        period_seconds = menu.s002.value()
        bright = menu.s003.value() / 100.0
        lead_in = menu.s004.value()
        lead_out = menu.s005.value()
        ends_at_cursor = menu.chk001.isChecked()

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>Select objects to highlight."
            )
            return

        render = bpy.context.scene.render
        fps = float(render.fps) / float(render.fps_base or 1.0)
        start, end = self._key_range(frames, ends_at_cursor)
        try:
            with self._suppressed():
                keyed = RenderEffects.key_pulse(
                    objects,
                    start=start,
                    end=end,
                    period=period_seconds * fps,
                    bright_fraction=bright,
                    lead_in=lead_in * fps,
                    lead_out=lead_out * fps,
                    color=self._pulse_color,
                    channel=self.HIGHLIGHT,
                    **self._key_options(),
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        self.ui.footer.setText(
            f"Highlight pulse: {len(keyed)} object(s), frames "
            f"{int(start)}–{int(end)} @ {period_seconds:.2f} s"
        )
        self._update_remove_enabled()

    # ------------------------------------------------------------------
    # Remove (option-box actions)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def _remove_channel(self, channel: str):
        """Remove *channel*'s artifacts (property, drivers, keys) from the selection."""
        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                f"Select objects to remove {channel} from."
            )
            return

        try:
            with self._suppressed():
                RenderEffects.remove(objects, channel=channel)
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        self.ui.footer.setText(
            f"{channel.title()} removed from {len(objects)} object(s): "
            f"{self._label(objects)}"
        )
        self._update_remove_enabled()

    # ------------------------------------------------------------------
    # Selection job — gate the remove actions
    # ------------------------------------------------------------------

    def _update_remove_enabled(self):
        """Enable each remove action only while the selection carries its channel."""
        if getattr(self, "_is_updating", False):
            return
        self._is_updating = True
        try:
            # Guard: skip if the UI has been destroyed (prevents crash when the
            # callback fires after the widget is garbage-collected).
            if not self.ui or not self.ui.isVisible():
                return
            for channel, action in self._remove_actions.items():
                action.widget.setEnabled(self._selection_carries(channel))
        except RuntimeError:
            pass  # Deleted C++ object — swallow to prevent crash
        except Exception:
            logging.getLogger(__name__).debug(
                "_update_remove_enabled error", exc_info=True
            )
        finally:
            self._is_updating = False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("render_effects", reload=True)
    ui.show(pos="screen", app_exec=True)
