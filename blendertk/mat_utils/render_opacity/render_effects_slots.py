# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Render Effects panel (``render_effects.ui``).

Provides ``RenderEffectsSlots`` -- one page per render-effect channel, chosen in
the effect picker: **Opacity Fade** and **Highlight Pulse**. Each page is the
whole of its tool's settings -- a **Create / Revise** selector over a
**Settings** fold holding the fields the chosen mode writes -- and one row of
actions under the pages serves whichever is shown: the Key button, the action
that strips the channel again, and a **WebXR** preview that pushes the effect
at the page's settings without writing it. There is no second surface -- no
colour window, no separate Manage section -- because a look set in one place
and revised in another is two editors that drift. The Key button is the only thing that keys.

The fields that say HOW an effect is keyed -- the fade's length, the pulse's
cadence and, in Create, its colours -- are the scene's effect recipe
(``ptk.EffectRecipe``, the shot store's ``effect_recipe``), the one the Shot
Manifest's build keys with. Editing one changes the recipe, never a key.

The Shot Manifest opens a page FOCUSED (:meth:`RenderEffectsSlots.focus`): the
picker hides, the header names the object and shot, and Key re-keys that
object's behaviors where the build places them. Hiding the panel leaves focus.

Mirror of mayatk's ``render_effects_slots`` (same objectNames, same widget
tree, same method names); delegates all logic to :class:`RenderEffects`.
Discovered by ``BlenderUiHandler`` (``marking_menu.show("render_effects")``).
``__init__`` is Qt-only (no ``bpy``) so the panel loads under the workspace
``.venv`` -- the selection-changed subscription is wrapped in a try/except that
no-ops without a running Blender.
"""

import html
import logging

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

#: The two things Key can mean. They differ in WHO is acted on, which is the
#: part an artist has to know before pressing it: ``CREATE`` sets an effect up
#: on the selection, making the channel where it is missing; ``REVISE`` changes
#: an effect that is already there and reaches nothing else. A tool whose modes
#: also differ in WHAT they write says so by hiding the fields the mode cannot
#: use (see ``_bind_mode``), so the page always shows exactly what Key reads.
#: Mirror of mayatk's.
CREATE, REVISE = "create", "revise"


class RenderEffectsSlots(ptk.LoggingMixin):
    """Switchboard slots for the Render Effects UI.

    Layout
    ------
    - **Header**: Title bar; its menu holds Last Selected Only -- the one
      option every effect's actions share. An option one effect reads lives
      on that effect's page.
    - **Effect picker** (``cmb_effect``): which page shows. Hidden while the
      panel is focused on one object's effect.
    - **Pages** (``stk_effects``): Opacity Fade and Highlight Pulse; each = the
      tool's mode over a **Settings** fold holding the fields it writes.
    - **Actions**: Key (``b000``), remove-channel (``btn_remove``) and
      ``btn_webxr`` -- one row for whichever page shows.
    - **Footer**: What Key will do; an action's report.
    """

    #: Channel name per page (mayatk keys these by ``ChannelSpec``).
    OPACITY = RenderEffects.ATTR_NAME
    HIGHLIGHT = RenderEffects.HIGHLIGHT_ATTR

    #: The pages, in picker order: ``(channel, label, page, keyer, Key text)``
    #: -- the keyer is the method Key (``b000``) runs while the page shows.
    PAGES = (
        (OPACITY, "Opacity Fade", "page_fade", "_key_fade_page", "Key Opacity Fade"),
        (
            HIGHLIGHT,
            "Highlight Pulse",
            "page_pulse",
            "_key_pulse_page",
            "Key Highlight Pulse",
        ),
    )

    #: The page builder per channel: a channel that gains a page gains a row.
    BUILDERS = {"opacity": "_build_fade_page", "highlight": "_build_pulse_page"}

    #: The colours a pulse seeds from when the targets agree on none: the
    #: recipe's defaults, declared ONCE in ``ptk.EffectRecipe``.
    DEFAULT_BRIGHT = ptk.EffectRecipe().pulse_bright
    DEFAULT_DIM = ptk.EffectRecipe().pulse_dim

    #: Stand-in albedo for the fade preview. The real one is per object; what
    #: the preview is showing is the alpha riding over it. LINEAR, as a glTF
    #: baseColorFactor is (the preview encodes for display, where this is a
    #: light grey).
    PREVIEW_ALBEDO = (0.57, 0.60, 0.67)

    #: How each mode reads for each channel, in the footer's resting line.
    #: A table rather than branches: a channel that gains a mode gains a row.
    VERBS = {
        ("opacity", CREATE): "keys a fade on",
        ("opacity", REVISE): "re-keys the fade on",
        ("highlight", CREATE): "keys a pulse on",
        ("highlight", REVISE): "re-colours",
    }

    #: What Revise promises about the keys, per channel. Opacity's fade IS its
    #: keys, so revising one rewrites them; the highlight's colours are their
    #: own properties, so revising those leaves a signed-off cadence alone.
    REVISE_NOTE = {"opacity": "keys are rewritten", "highlight": "keys untouched"}

    #: The recipe field each page field edits: ``(field, widget, scale)`` --
    #: the widget shows ``value * scale`` (the duty is a percent of a fraction).
    RECIPE_FIELDS = (
        ("fade_frames", "s000", 1),
        ("pulse_period", "s002", 1),
        ("pulse_duty", "s003", 100),
        ("pulse_lead_in", "s004", 1),
        ("pulse_lead_out", "s005", 1),
    )

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.render_effects
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[render_effects] ")
        self._pages = {}  # channel -> its FormRows page
        self._mode_combos = {}  # channel -> its Create/Revise combo
        self._mode_fields = {}  # channel -> uitk FieldVisibility
        self._mode_hooks = {}  # channel -> callable run on mode/selection
        #: ``{"channel", "objects", "apply", "apply_text", "title"}`` while the
        #: manifest has the panel focused on one object's effect.
        self._focus = None
        self._recipe = None  # uitk ModelBinding over the scene's recipe
        self._unwatch_recipe = None

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
                self._on_scene_selection,
                owner=self,
                ephemeral=True,
            )
            mgr.connect_cleanup(self.ui, owner=self)
        except Exception:
            pass
        # Focus belongs to the moment the manifest asked for it.
        self.ui.on_hide.connect(self.unfocus)
        self.ui.on_show.connect(self._refit)
        self.ui.destroyed.connect(lambda *_: self._stop_watching())

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
                body="Applies to every effect's Key, WebXR and Remove.",
                bullets=[
                    "<b>On:</b> Only the active object is processed.",
                    "<b>Off:</b> All selected objects are processed.",
                ],
            ),
        )
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Render Effects",
                body="Key per-object render effects for engine-ready control: an "
                "<b>Opacity</b> fade (alpha in the GLB, a Unity controller via "
                "FBX) or a <b>Highlight</b> pulse (an additive emissive glow "
                "with a per-object colour). Each tool creates its channel on the "
                "selection when missing, so keying is the only step.",
                steps=[
                    "Pick the effect at the top; its page holds everything that "
                    "tool writes. Once they are set, fold <b>Settings</b>: the "
                    "page keeps its mode, the window its Key.",
                    "Select one or more objects and press <b>Key</b> under the "
                    "page -- it keys that effect at the playhead.",
                    "<b>WebXR</b>, beside it, pushes the selection with the "
                    "effect at the page's settings -- not the objects' own keys: "
                    "the deliverable's own GLB build, nothing in the scene "
                    "written, and whatever the objects already carry left out.",
                    "<b>Remove</b>, under WebXR, strips that effect's channel "
                    "(property and keys) from the selection.",
                ],
                sections=[
                    (
                        "The scene's recipe",
                        [
                            "The fade's frames, the pulse's period, duty and "
                            "leads -- and, in Create, its colours -- are the "
                            "scene's <b>effect recipe</b>: saved with the scene, "
                            "and what the Shot Manifest's Build keys its fades "
                            "and highlights with.",
                            "Editing one changes the recipe, never a key. Build "
                            "re-keys the manifest's effects made with an older "
                            "recipe (Assess flags them); a channel the Build "
                            "creates takes the recipe's colours.",
                            "A pulse's Length and End at Playhead are this "
                            "panel's own -- they place one keying.",
                        ],
                    ),
                    (
                        "Create and Revise",
                        [
                            "Each page opens on <b>Create</b>: the tool sets its "
                            "effect up on the selection, making the channel "
                            "where it is missing.",
                            "<b>Revise</b> changes an effect that is already "
                            "there — the objects in the selection that carry "
                            "the channel, or every such object in the scene "
                            "when nothing is selected.",
                            "The page shows only the fields the mode writes, and "
                            "the footer says what Key is about to do. After an "
                            "action it shows that action's report until your "
                            "next pick, page or mode.",
                        ],
                    ),
                    (
                        "From the Shot Manifest",
                        [
                            "Right-click an object row and choose its effect: "
                            "the panel opens on that effect alone, named for "
                            "the object and its shot, the object selected.",
                            "<b>Key</b> then re-keys the object's behaviors "
                            "where the manifest places them -- not at the "
                            "playhead. Hiding the panel ends the focus.",
                        ],
                    ),
                    (
                        "Header menu",
                        [
                            "<b>Last Selected Only</b> — only the active object "
                            "participates, in every effect's Key, WebXR and "
                            "Remove.",
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
    # Pages and the scene's recipe
    # ------------------------------------------------------------------

    def cmb_effect_init(self, widget):
        """The effect picker: one entry per page."""
        widget.clear()
        for channel, label, *_rest in self.PAGES:
            widget.addItem(label, channel)

    def cmb_effect(self, index, widget=None):
        """Show the picked effect's page."""
        stack = getattr(self.ui, "stk_effects", None)
        if stack is None or not 0 <= index < stack.count():
            return
        stack.setCurrentIndex(index)
        self._fit_stack()
        self._clear_report()
        self._sync_actions()
        channel = self.PAGES[index][0]
        if channel in self._mode_combos:
            self._sync_mode(channel, deep=True)

    def stk_effects_init(self, widget):
        """Build every page into the stack, bind the recipe, register the rows."""
        from uitk.managers.model_binding import ModelBinding
        from uitk.widgets.form_rows import FormRows

        # Refreshed as applied FOR the user: the linked leads re-baseline
        # rather than shove a delta into each other, and nothing persists.
        self._recipe = ModelBinding(
            read=self._recipe_values,
            write=self._write_recipe,
            applying=self.ui.state.suppress_save,
        )
        for channel, _label, page_name, *_rest in self.PAGES:
            page = getattr(self.ui, page_name)
            rows = FormRows(page)
            page.layout().addWidget(rows)
            self._pages[channel] = rows
            getattr(self, self.BUILDERS[channel])(rows)
        for field, name, scale in self.RECIPE_FIELDS:
            spin = self.ui_field(name)
            if spin is None:
                continue
            self._recipe.bind(
                field,
                spin,
                getter=lambda w=spin, k=scale: float(w.value()) / k,
                setter=lambda v, w=spin, k=scale: w.setValue(
                    self._spin_value(w, v * k)
                ),
            )
        # The rows join the window: state restore for the panel's own fields.
        self.ui.register_children(widget)
        self._on_recipe_changed()
        store = self._store_cls()
        if store is not None:
            self._unwatch_recipe = store.watch_settings(self._on_recipe_changed)
        self.cmb_effect(widget.currentIndex())

    @staticmethod
    def _spin_value(spin, value):
        """*value* as *spin* can hold it: the nearest whole number in a
        whole-number box, whose ``setValue`` truncates a float -- a 0.57 duty
        is 56.99... percent, and showed 56."""
        return round(value) if isinstance(spin.value(), int) else value

    def ui_field(self, name):
        """A page field by objectName, from whichever page holds it."""
        for rows in self._pages.values():
            widget = getattr(rows, name, None)
            if widget is not None:
                return widget
        return None

    @staticmethod
    def _store_cls():
        """This host's shot store, which holds the scene's effect recipe
        (``RenderEffects.scene_store``; ``None`` when unavailable)."""
        return RenderEffects.scene_store()

    def _recipe_obj(self):
        """The scene's effect recipe (the defaults when no store can be had)."""
        return RenderEffects.scene_recipe()

    def _recipe_values(self) -> dict:
        return self._recipe_obj().to_dict()

    def _write_recipe(self, field, value) -> None:
        """Store one recipe field -- a scene setting, written on edit; it keys
        nothing."""
        try:
            self._store_cls().active().update_effect_recipe(**{field: value})
        except Exception as e:
            self.ui.footer.setText(f"Recipe not saved: {e}")

    def _on_recipe_changed(self, *_args) -> None:
        """Re-read the recipe into every page (a panel edit, an undo, another
        panel, a file opened)."""
        if self._recipe is None:
            return
        self._recipe.refresh()
        ramp = getattr(self, "_pulse_ramp", None)
        if ramp is not None and self._mode(self.HIGHLIGHT) == CREATE:
            self._show_recipe_colors(ramp)
        self._update_cycle_readout()
        self._sync_pulse_shape()
        self._sync_fade_shape()

    def _show_recipe_colors(self, ramp) -> None:
        """Seed the colour row with the recipe's pulse colours (Create)."""
        blocked = ramp.blockSignals(True)
        try:
            ramp.set_colors(self._recipe_obj().colors)
            for index in range(len(ramp.editors)):
                ramp.set_mixed(index, False)
        finally:
            ramp.blockSignals(blocked)
        ramp.set_reference(None)

    def _on_pulse_color_committed(self, index, _qcolor) -> None:
        """Create: a colour the artist set is the recipe's -- what the next
        pulse, and a channel a Build creates, is coloured with. Revise stages
        it for Key instead (the targets' colours, not the recipe's)."""
        if self._mode(self.HIGHLIGHT) != CREATE:
            return
        color = self._pulse_ramp.decided()[index]
        if color is None:
            return
        self._write_recipe(("pulse_bright", "pulse_dim")[index], tuple(color))

    def _stop_watching(self) -> None:
        if self._unwatch_recipe is not None:
            self._unwatch_recipe()
            self._unwatch_recipe = None

    def _fit_stack(self) -> None:
        """Size the stack to the page it shows, then the window to the stack.

        Mirror of mayatk's: a ``QStackedWidget`` is as tall as its TALLEST
        page (its height-for-width asks every page and no size policy opts
        one out), so the pages not shown hold their rows hidden.
        """
        stack = getattr(self.ui, "stk_effects", None)
        if stack is None:
            return
        for rows in self._pages.values():
            rows.setVisible(rows.parentWidget() is stack.currentWidget())
        self._refit()

    def _refit(self) -> None:
        """Fit the window to its content once the layouts settle: a page, the
        focus, and every show (mayatk's has the reasons)."""
        from uitk.managers.window_height import WindowHeight

        WindowHeight.fit_host_later(self.ui)

    # ------------------------------------------------------------------
    # Focus -- one object's effect, opened from the Shot Manifest
    # ------------------------------------------------------------------

    def focus(self, channel, objects, title="", apply=None, apply_text=""):
        """Open on one effect for *objects*, as the manifest's row actions do.

        Mirror of mayatk's: the picker hides, the header names the effect and
        *title*, the objects are selected, and the page opens in Revise when
        every object already carries the channel, else in Create. *apply*
        (``() -> str``) is what Key runs instead of keying at the playhead --
        the manifest's re-key at its placement; the highlight's Revise still
        re-colours.
        """
        objects = self._resolve(objects)
        names = [p[0] for p in self.PAGES]
        index = names.index(channel)
        label = self.PAGES[index][1]
        self._focus = {
            "channel": channel,
            "objects": objects,
            "apply": apply,
            "apply_text": apply_text,
            "title": title,
        }
        picker = self.ui.cmb_effect
        picker.setCurrentIndex(index)
        self.cmb_effect(index)
        picker.setVisible(False)
        self.ui.header.setText(f"{label} · {title}".upper() if title else label.upper())
        self._select(objects)
        carrying = self._carrying(channel, objects)
        self._set_mode(
            channel, REVISE if objects and len(carrying) == len(objects) else CREATE
        )
        self._on_selection_changed()

    def unfocus(self, *_args) -> None:
        """Back to the standalone panel: the picker, its title, the Key texts
        and the footer's resting line."""
        if self._focus is None:
            return
        self._focus = None
        picker = getattr(self.ui, "cmb_effect", None)
        if picker is not None:
            picker.setVisible(True)
        self.ui.header.setText("RENDER EFFECTS")
        self._sync_actions()
        # Directly, not through the selection job: a hide ends the focus, and
        # the job skips a hidden panel, so the manifest's line outlived it.
        shown = self._shown_channel()
        if shown is not None:
            self._update_apply_readout(shown)
        self._refit()

    @staticmethod
    def _resolve(objects) -> list:
        """*objects* as live ``bpy`` objects (names resolved; missing dropped)."""
        try:
            import bpy
        except ImportError:
            return []
        out = []
        for obj in objects or ():
            node = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
            if node is not None:
                out.append(node)
        return out

    @staticmethod
    def _select(objects) -> None:
        """Make *objects* the selection (the first active) -- a view of them,
        as the manifest's Show in Outliner selects."""
        if not objects:
            return
        import bpy

        with CoreUtils.window_context_override():
            layer = bpy.context.view_layer
            for o in list(bpy.context.selected_objects):
                o.select_set(False)
            for o in objects:
                if o.name in layer.objects:
                    o.select_set(True)
            try:
                layer.objects.active = objects[0]
            except (AttributeError, RuntimeError, ReferenceError):
                pass

    def _focused_apply(self, channel: str):
        """The manifest's re-key while focused on *channel* in the mode it
        serves (any for opacity; Create for the highlight, whose Revise
        re-colours), else ``None``."""
        focus = self._focus
        if not focus or focus["channel"] != channel or focus["apply"] is None:
            return None
        if channel == self.HIGHLIGHT and self._mode(channel) == REVISE:
            return None
        return focus["apply"]

    def _run_focused(self, channel: str) -> bool:
        """Run the manifest's re-key when Key means it; True when it did."""
        apply = self._focused_apply(channel)
        if apply is None:
            return False
        try:
            report = apply()
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return True
        self.ui.footer.setText(report or "Re-keyed through the manifest.")
        self._on_selection_changed()
        return True

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
            active = CoreUtils.active_object()
            return [active] if active in objects else objects[-1:]
        return objects

    # ------------------------------------------------------------------
    # The action row -- one for whichever page shows
    # ------------------------------------------------------------------

    def b000(self, widget=None):
        """Key: the shown page's keyer (``PAGES``)."""
        page = self._shown_page()
        if page is not None:
            getattr(self, page[3])()

    def btn_remove(self, widget=None):
        """Strip the shown page's channel from the selection."""
        channel = self._shown_channel()
        if channel is not None:
            self._remove_channel(channel)

    def btn_webxr_init(self, widget):
        widget.setToolTip(
            self.sb.tooltip.fmt(
                title="Preview in WebXR",
                body="Push the selection to the WebXR preview with this page's "
                "effect at the settings above -- the page as it stands, not the "
                "objects' own keys.",
                bullets=[
                    "Nothing in the scene is written -- no property, key or "
                    "colour. The effect exists only in the pushed GLB.",
                    "Whatever the objects already carry is left out, so the "
                    "page plays this effect alone. To see what they ARE keyed "
                    "with, push them through the WebXR preview itself.",
                    "Built by the deliverable's own GLB pipeline, so it plays "
                    "as the keyed effect would ship: one looping clip from its "
                    "first frame (End at Playhead does not apply).",
                    "A field the mode hides keeps its last value for the preview.",
                ],
            )
        )

    def btn_webxr(self, widget=None):
        """Preview the shown page's effect in WebXR."""
        channel = self._shown_channel()
        if channel is not None:
            self._preview_webxr(channel)

    def _sync_actions(self, snapshot=None) -> None:
        """Point the action row at the page on show (mirror of mayatk's):
        Key's text, and Remove's channel -- live only while the selection
        carries it. The snapshot is also what keeps a build-time call working
        with no Blender running."""
        page = self._shown_page()
        if page is None:
            return
        channel, _label, _page, _keyer, key_text = page
        # The manifest's text only while Key runs the manifest's re-key: a
        # focused highlight's Revise re-colours, as the page's own Key does.
        if self._focused_apply(channel) is not None and self._focus["apply_text"]:
            key_text = self._focus["apply_text"]
        self.ui.b000.setText(key_text)
        carried = (snapshot if snapshot is not None else self._selection_snapshot())[1]
        remove = self.ui.btn_remove
        remove.setEnabled(bool(carried.get(channel)))
        remove.setToolTip(
            self.sb.tooltip.fmt(
                title=f"Remove {channel.title()}",
                body=f"Strip the <b>{channel}</b> channel from the selection: "
                "the property, its keys and any material drivers.",
            )
        )

    # ------------------------------------------------------------------
    # WebXR preview
    # ------------------------------------------------------------------

    #: Seconds a fade preview holds at each end, in the page's animated
    #: preview and in the WebXR one alike. Mirror of mayatk's.
    PREVIEW_HOLD_SECONDS = 0.6

    #: The planner per channel, by method name (mirror of mayatk's table).
    PREVIEW_PLANS = {
        "opacity": "_fade_preview_plan",
        "highlight": "_pulse_preview_plan",
    }

    def _fade_preview_plan(self, fps: float):
        """``(keys, None)``: the fade as the page stands, framed by its holds."""
        keys = ptk.RampKeys.fade_loop(
            self._recipe_obj().fade_frames,
            hold=self.PREVIEW_HOLD_SECONDS * fps,
            direction=self.ui_field("cmb_direction").currentData(),
        )
        return keys, None

    def _pulse_preview_plan(self, fps: float):
        """``(keys, (bright, dim))``: the pulse as the page stands, from frame 0.

        An undecided end (Revise, targets disagreeing) previews as the
        recipe's -- what Create would key.
        """
        recipe = self._recipe_obj()
        keys = recipe.plan("pulse", 0.0, self.ui_field("s001").value() * fps, fps)
        bright, dim = self._pulse_ramp.decided()
        return keys, (bright or recipe.pulse_bright, dim or recipe.pulse_dim)

    def _preview_webxr(self, channel: str):
        """Push the selection to the WebXR preview with *channel*'s effect as set.

        Mirror of mayatk's: the effect rides the push as an overlay on the GLB's
        in-band channels (``WebXrPreview.push(data_export=...)``), so the page
        plays what these settings would ship and the scene is never written.
        """
        from blendertk.env_utils.webxr_preview import WebXrPreview

        objects = self._get_selected()
        if not objects:
            self.sb.message_box(
                "<strong>Nothing selected</strong>.<br>"
                f"Select objects to preview the {channel} on."
            )
            return
        fps = float(RenderEffects._scene_fps() or 30.0)
        try:
            keys, colors = getattr(self, self.PREVIEW_PLANS[channel])(fps)
            overlay = RenderEffects.preview_channels(
                objects, channel=channel, keys=keys, colors=colors, fps=fps
            )
            # The export selects what it writes (and restores the selection
            # after), which would re-run the selection job mid-push.
            with self._suppressed():
                with self.sb.progress(text="WebXR preview: exporting…") as tick:
                    result = WebXrPreview().push(
                        objects=objects,
                        open_browser="auto",
                        data_export=overlay,
                        progress=lambda message: tick(text=message),
                    )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return
        if not result:
            self.ui.footer.setText("WebXR preview failed -- see the log for details.")
            return
        if ptk.MeshConvert.VISIBILITY_TRACKS_KEY not in (
            result.get("data_export") or ()
        ):
            # Mirror of mayatk's: published, but what it published is the
            # scene, not the page -- a bridge that never learned the overlay
            # knob sweeps ``data_export`` into the export bag and builds as if
            # nothing was asked, with no error anywhere.
            self.ui.footer.setText(
                f"WebXR preview v{result['version']} shows the scene, "
                f"not the {channel} settings."
            )
            self.sb.message_box(
                "<strong>Preview not applied</strong>.<br>The push published "
                f"without the {channel} overlay, so the page shows the objects "
                "as they are. Usually the preview bridge in this session predates "
                "the overlay: restart Blender (or reload pythontk) and push again."
            )
            return
        self.ui.footer.setText(
            f"{channel.title()} preview v{result['version']}: "
            f"{self._label(objects)} at {result['url']}"
        )

    @staticmethod
    def _carrying(channel: str, objects):
        """Those *objects* that already carry *channel*."""
        return [obj for obj in objects if channel in obj]

    def _selection_snapshot(self):
        """``(selection, {channel: those that carry it})`` -- read ONCE.

        Every consumer on the selection-changed path asks the same question,
        and asking it separately made picking objects cost a scene read and a
        per-object query for each of them. It reports the EFFECTIVE selection,
        which is what "Last Selected Only" narrows and what every action here
        operates on.
        """
        try:
            selected = self._get_selected()
        except Exception:  # no running Blender: the panel still has to build
            return [], {}
        return selected, {
            channel: self._carrying(channel, selected)
            for channel in (self.OPACITY, self.HIGHLIGHT)
        }

    # ------------------------------------------------------------------
    # Create / Revise
    # ------------------------------------------------------------------

    def _add_mode(self, rows, channel: str):
        """Open a page with its mode selector.

        Called FIRST in a page's build so it lands above the fields it gates.
        Mirror of mayatk's: what the mode means for Key is the footer's
        resting line (:meth:`_update_apply_readout`).
        """
        from uitk.managers.field_visibility import FieldVisibility

        cmb = rows.add(
            "QComboBox",
            setObjectName=f"cmb_mode_{channel}",
            setToolTip=self.sb.tooltip.fmt(
                title="What the Key button does",
                bullets=[
                    "<b>Create:</b> set this effect up on the selection, "
                    "making the channel on objects that lack it.",
                    f"<b>Revise:</b> change it where it already is — "
                    f"{self.REVISE_NOTE.get(channel, '')}. With nothing "
                    f"selected this reaches every {channel} object in the "
                    "scene, after confirming.",
                    "The page shows only what the mode writes.",
                ],
            ),
        )
        for text, data in (("Create", CREATE), ("Revise", REVISE)):
            cmb.addItem(text, data)
        # Every page opens on Create, as the help says.
        cmb.restore_state = False
        self._mode_combos[channel] = cmb
        # The combo drives the layout directly; `on_change` is the mode
        # switch, which ends the last report and is one of the moments the
        # tool may re-read the scene (see ``_sync_mode``).
        self._mode_fields[channel] = FieldVisibility(
            on_change=lambda _name, c=channel: self._on_mode_changed(c),
        )
        return cmb

    def _add_settings(self, rows, channel: str):
        """Fold the rest of a page under its mode selector; returns the fold.

        Called right after :meth:`_add_mode`. The fields are set once and then
        keyed with for a while, and folded the page is its mode and the window
        its Key. The mode stays out: it is what changes WHO Key acts on. Each
        page folds on its own and keeps the state per window.
        """
        settings = rows.add_section("Settings", setObjectName=f"grp_settings_{channel}")
        # A fold changes what the window holds, as a page or a mode does.
        settings.parentWidget().toggled.connect(lambda *_: self._refit())
        return settings

    def _on_mode_changed(self, channel) -> None:
        self._clear_report()
        self._sync_mode(channel, deep=True)
        self._sync_actions()  # Key's text is the mode's (a focus serves one)

    def _bind_mode(self, channel: str, fields=None, on_sync=None):
        """Declare what each mode writes, then show the opening one.

        Parameters:
            fields: ``{mode: (page widget name, ...)}``, resolved against the
                page. Anything named in no list is always shown. ``None`` --
                the usual case -- means the modes write the same fields and
                differ only in their target.
            on_sync: Run whenever the mode or the selection changes, for a
                tool that has to re-read the scene. Takes *deep* and
                *snapshot*, both as :meth:`_sync_mode` documents them.
        """
        if on_sync is not None:
            self._mode_hooks[channel] = on_sync
        visibility, rows = self._mode_fields[channel], self._pages[channel]
        for name in set().union(*fields.values()) if fields else ():
            field = getattr(rows, name, None)
            if field is not None:
                visibility.register(name, field)
        for mode in (CREATE, REVISE):
            visibility.define(mode, (fields or {}).get(mode, ()))
        # Binding applies the opening entry, which both shows the right fields
        # and takes the tool through its first `_sync_mode`.
        visibility.bind(self._mode_combos[channel])

    def _mode(self, channel: str) -> str:
        """The mode *channel*'s page is set to (``CREATE`` before it builds).

        The layout IS the mode: asking the combo separately would give two
        readers that disagree the moment anything sets the mode in code.
        """
        visibility = self._mode_fields.get(channel)
        return (visibility.mode if visibility is not None else None) or CREATE

    def _set_mode(self, channel: str, mode: str) -> None:
        """Switch *channel*'s page to *mode* (the combo drives the layout)."""
        cmb = self._mode_combos.get(channel)
        if cmb is None:
            return
        index = cmb.findData(mode)
        if index >= 0:
            cmb.setCurrentIndex(index)

    def _sync_mode(self, channel: str, deep: bool = False, snapshot=None):
        """Re-read the scene for *channel*'s page: its hook, then the readout.

        *deep* marks the infrequent moments -- a mode switch, a write that just
        landed -- where the hook may go past the selection and scan the scene.
        """
        hook = self._mode_hooks.get(channel)
        if hook is not None:
            hook(deep, snapshot)
        self._update_apply_readout(channel, snapshot)

    def _shown_page(self):
        """The ``PAGES`` row of the page the stack shows, or ``None``."""
        stack = getattr(self.ui, "stk_effects", None)
        index = stack.currentIndex() if stack is not None else -1
        return self.PAGES[index] if 0 <= index < len(self.PAGES) else None

    def _shown_channel(self):
        """The channel of the page the stack shows, or ``None``."""
        page = self._shown_page()
        return page[0] if page is not None else None

    def _update_apply_readout(self, channel: str, snapshot=None):
        """Rest the footer on what Key is about to do, and to how many objects.

        Mirror of mayatk's: only the page on show writes it, and an action's
        report stands over it until :meth:`_clear_report`. Counts the
        SELECTION rather than the scene: this runs on every selection change.
        *snapshot* is a :meth:`_selection_snapshot` pair, passed by the caller
        that already took one.
        """
        if channel != self._shown_channel():
            return
        self.ui.footer.setDefaultStatusText(self._apply_readout(channel, snapshot))

    def _apply_readout(self, channel: str, snapshot=None) -> str:
        """The footer's resting line for *channel*: one short line, its
        counts in bold (mayatk's has the reasons)."""
        if self._focused_apply(channel) is not None:
            title = html.escape(self._focus["title"] or "the object")
            return f"Re-keys <b>{title}</b> where the Shot Manifest places it"
        mode = self._mode(channel)
        verb = self.VERBS.get((channel, mode), "applies to").capitalize()
        selected, carried = (
            snapshot if snapshot is not None else (self._get_selected(), None)
        )
        if mode == CREATE:
            return (
                f"{verb} <b>{len(selected)}</b> selected"
                if selected
                else "Needs a selection"
            )
        if not selected:
            return f"{verb} <b>every</b> {channel} object in the scene"
        carrying = (
            carried.get(channel, ())
            if carried is not None
            else self._carrying(channel, selected)
        )
        return (
            f"{verb} <b>{len(carrying)} of {len(selected)}</b> selected"
            if carrying
            else f"None of the <b>{len(selected)}</b> selected carry {channel}"
        )

    def _clear_report(self) -> None:
        """Let the footer fall back to its resting line (mayatk's has why)."""
        footer = getattr(self.ui, "footer", None)
        if footer is not None and footer.statusText():
            footer.setText("")

    def _targets(self, channel: str):
        """The objects Key acts on in the current mode, or ``None`` to stop.

        ``None`` means the artist has already been told why, or declined the
        scene-wide confirmation, so a caller returns without a second message.
        """
        selected = self._get_selected()
        if self._mode(channel) == CREATE:
            if not selected:
                self.sb.message_box(
                    "<strong>Nothing selected</strong>.<br>"
                    f"Select objects to key their {channel}."
                )
                return None
            return selected

        if selected:
            carrying = self._carrying(channel, selected)
            if not carrying:
                self.sb.message_box(
                    "<strong>Nothing to revise</strong>.<br>None of the "
                    f"{len(selected)} selected object(s) carry the "
                    f"{channel} channel."
                )
                return None
            return carrying

        everywhere = RenderEffects.objects_with_channel(channel)
        if not everywhere:
            self.sb.message_box(
                "<strong>Nothing to revise</strong>.<br>"
                f"No object in the scene carries the {channel} channel."
            )
            return None
        # Scene-wide is a legitimate ask and also the one shape a mis-click
        # cannot be undone by eye, so it says how many first.
        prompt = (
            f"Revise <strong>every</strong> object carrying the {channel} "
            f"channel ({len(everywhere)})?<br>Select objects first to narrow it."
        )
        if self.sb.message_box(prompt, "Yes", "No") != "Yes":
            return None
        return everywhere

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
    # Page: opacity fade
    # ------------------------------------------------------------------

    def _build_fade_page(self, rows):
        """The Opacity Fade page (mirror of mayatk's)."""
        from uitk.widgets.editors.color_editor import FadeWaveform, RampPreview

        GLTF = ptk.GlbFades.CHANNELS
        self._add_mode(rows, self.OPACITY)
        settings = self._add_settings(rows, self.OPACITY)
        settings.add(
            "QSpinBox",
            setPrefix="Frames: ",
            setObjectName="s000",
            setMinimum=1,
            setMaximum=1000,
            setToolTip=self.sb.tooltip.fmt(
                body="Number of frames over which the fade occurs.",
                bullets=[
                    "The scene's effect recipe: the Shot Manifest's fades are "
                    "this long too.",
                ],
            ),
        )
        settings.add(
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
        cmb = settings.add(
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
        settings.add(
            "QCheckBox",
            setText="Delete Visibility Keys",
            setObjectName="chk_delete_vis_keys",
            setChecked=False,
            setToolTip=self.sb.tooltip.fmt(
                body="When Key first gives an object its opacity property:",
                bullets=[
                    "<b>On:</b> The object's existing render-visibility keys are "
                    "deleted first.",
                    "<b>Off:</b> They are kept; the fade's visibility mirror is "
                    "keyed over them.",
                    "Create only: Revise reaches objects that already carry "
                    "the property.",
                ],
            ),
        )
        # Mirror of mayatk's. `values` is the exporter's own function -- alpha
        # on the fourth lane of baseColorFactor -- so the preview cannot drift
        # from what ships.
        preview = RampPreview(
            waveform=FadeWaveform(hold=self.PREVIEW_HOLD_SECONDS),
            values=GLTF[self.OPACITY].values,
            linear=True,  # a baseColorFactor is; painted encoded, as the page does
        )
        preview.setObjectName("fade_preview")
        # A stand-in albedo: the real one is per object, and what is being
        # previewed is the ALPHA.
        preview.set_base(self.PREVIEW_ALBEDO)
        settings.add(
            preview,
            setToolTip=self.sb.tooltip.fmt(
                body="What the fade does to the object, at the length set above.",
                bullets=[
                    "The fill is the object's albedo at the alpha this channel "
                    "writes, over a transparency board -- the EXPORTER's own "
                    "arithmetic, so it is the deliverable's value rather than "
                    "a lookalike.",
                    "The holds either side are not padding: a curve holds its "
                    "first value backwards and its last forwards, so the "
                    "object really does sit there.",
                    "<b>Auto</b> ramps both ways, because the direction is "
                    "resolved per object from its last key when you key.",
                ],
            ),
        )
        self._fade_preview = preview
        rows.s000.valueChanged.connect(lambda *_: self._sync_fade_shape())
        rows.cmb_direction.currentIndexChanged.connect(
            lambda *_: self._sync_fade_shape()
        )
        # A fade IS its keys: Revise hides only Delete Visibility Keys, which
        # acts when Key GIVES an object the property -- something Revise never
        # does (mayatk's has the rest).
        self._bind_mode(
            self.OPACITY, fields={CREATE: ("chk_delete_vis_keys",), REVISE: ()}
        )

    def _sync_fade_shape(self):
        """Run the fade preview at the recipe's length and the page's direction.

        It declines rather than raises -- a preview must never be what stops a
        page from building.
        """
        preview = getattr(self, "_fade_preview", None)
        direction = self.ui_field("cmb_direction")
        if preview is None or direction is None:
            return
        try:
            fps = RenderEffects._scene_fps() or 30.0
            preview.set_shape(
                duration=float(self._recipe_obj().fade_frames) / fps,
                direction=direction.currentData(),
            )
        except Exception:  # no running Blender, or a field mid-rebuild
            return

    def _key_fade_page(self):
        """Key Opacity Fade -- or, focused from the manifest, re-key there."""
        if self._run_focused(self.OPACITY):
            return
        self._key_opacity_fade()

    @CoreUtils.undoable
    def _key_opacity_fade(self):
        """Key a fade on the opacity property (created if missing)."""
        objects = self._targets(self.OPACITY)
        if not objects:
            return

        ends_at_cursor = self.ui_field("chk000").isChecked()
        direction_mode = self.ui_field("cmb_direction").currentData()
        start, end = self._key_range(self._recipe_obj().fade_frames, ends_at_cursor)

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
                    delete_visibility_keys=self.ui_field(
                        "chk_delete_vis_keys"
                    ).isChecked(),
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        dirs = {"Fade In" if d == "in" else "Fade Out" for _, d in keyed}
        direction = " / ".join(sorted(dirs)) or "Fade"
        self.ui.footer.setText(
            f"{direction}: {len(keyed)} object(s), frames {int(start)}–{int(end)}"
        )
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Page: highlight pulse
    # ------------------------------------------------------------------

    def _build_pulse_page(self, rows):
        """The Highlight Pulse page (mirror of mayatk's)."""
        from qtpy import QtCore
        from uitk.widgets.editors.color_editor import ColorRampEditor

        GLTF = ptk.GlbFades.CHANNELS
        self._add_mode(rows, self.HIGHLIGHT)
        settings = self._add_settings(rows, self.HIGHLIGHT)
        settings.add(
            "QDoubleSpinBox",
            setPrefix="Length: ",
            setSuffix=" s",
            setObjectName="s001",
            setMinimum=0.1,
            setMaximum=3600.0,
            setSingleStep=0.5,
            setDecimals=2,
            setValue=4.0,
            setToolTip=self.sb.tooltip.fmt(
                body="How long the pulse runs, in seconds.",
                bullets=[
                    "This keying's own: a Shot Manifest highlight spans its shot."
                ],
            ),
        )
        settings.add(
            "QDoubleSpinBox",
            setPrefix="Period: ",
            setSuffix=" s",
            setObjectName="s002",
            setMinimum=ptk.EffectRecipe.LIMITS["pulse_period"][0],
            setMaximum=60.0,
            setSingleStep=0.1,
            setDecimals=2,
            setToolTip=self.sb.tooltip.fmt(
                body="One bright/dim cycle, in seconds (2.86 s measured on the "
                "WebXR reference).",
                bullets=["The scene's effect recipe -- the manifest keys it too."],
            ),
        )
        # "Duty", not "Bright": it is a share of TIME, and the page also
        # carries two colours, one of which is literally called Bright.
        settings.add(
            "QSpinBox",
            setPrefix="Duty: ",
            setSuffix=" %",
            setObjectName="s003",
            setMinimum=1,
            setMaximum=99,
            setToolTip=self.sb.tooltip.fmt(
                body="Share of each cycle spent at the bright end "
                "(59% measured on the WebXR reference).",
                bullets=["The scene's effect recipe -- the manifest keys it too."],
            ),
        )
        self._cycle_readout = settings.add(
            "QLabel",
            setObjectName="lbl_cycles",
            setAlignment=QtCore.Qt.AlignCenter,
            setToolTip=self.sb.tooltip.fmt(
                body="How many cycles the train holds: the length less the "
                "lead-in and lead-out.",
                bullets=[
                    "A train that is not a whole multiple of the period ends "
                    "MID-CYCLE: it is cut and the tail holds whatever value it "
                    "was interrupted at.",
                    "Nothing is wrong with that -- it is just invisible "
                    "without a number, so here is the number.",
                ],
            ),
        )
        gaps = [
            settings.add(
                "QDoubleSpinBox",
                setPrefix=prefix,
                setSuffix=" s",
                setObjectName=name,
                setMinimum=0.0,
                setMaximum=60.0,
                setSingleStep=0.1,
                setDecimals=2,
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
                        "The scene's effect recipe -- the manifest keys it too.",
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
        for name in ("s001", "s002", "s004", "s005"):
            getattr(rows, name).valueChanged.connect(
                lambda *_: self._update_cycle_readout()
            )
        settings.add(
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
        # Both ends of the ramp, on the page: the artist is choosing the
        # RELATIONSHIP between them, and one at a time hides it. Mirror of
        # mayatk's.
        ramp = ColorRampEditor(
            labels=("Bright", "Dim"),
            colors=self._recipe_obj().colors,
            advanced=("rgb",),
            preview=True,
            # The exporter's own function, so the preview and the deliverable
            # cannot disagree: additive over the material's emissive, clamped.
            values=GLTF[self.HIGHLIGHT].values,
            # The colours are LINEAR (the property's and the factor's space);
            # the swatches and previews show them encoded, as the page does.
            linear=True,
            # Light added over the object: a dim end at nothing shows the
            # board, not a black surface.
            additive=True,
        )
        ramp.setObjectName("pulse_colors")
        # Create: a committed colour is the recipe's. Revise: staged for Key,
        # written to the targets only. Never written while dragged.
        ramp.stopCommitted.connect(self._on_pulse_color_committed)
        settings.add(
            ramp,
            setToolTip=self.sb.tooltip.fmt(
                body="The two colours the pulse rides between.",
                bullets=[
                    "<b>Bright</b> is what the object reads at intensity 1, "
                    "<b>Dim</b> at 0.",
                    "In <b>Create</b> they are the scene's effect recipe: what "
                    "the next pulse is keyed with, and what a channel the Shot "
                    "Manifest's Build creates is coloured with.",
                    "In <b>Revise</b> they are the targets' own colours, written "
                    "to them by Key -- the look being replaced is shown beside "
                    "it, whenever the targets agree on one.",
                    "A dim end at nothing -- the board showing through -- is "
                    "the classic look: the glow fades away.",
                    "Colours are linear light, as the property and the GLB "
                    "factor are; the swatches show them display-encoded, which "
                    "is how the page shows them.",
                    "The preview runs the EXPORTER's own arithmetic at the "
                    "cadence set above, so it is the deliverable's value "
                    "rather than a lookalike.",
                ],
            ),
        )
        self._pulse_ramp = ramp
        for name in ("s002", "s003"):
            getattr(rows, name).valueChanged.connect(
                lambda *_: self._sync_pulse_shape()
            )
        # Revise shows the colours alone: re-timing IS re-keying, and that is
        # Create.
        self._bind_mode(
            self.HIGHLIGHT,
            fields={
                CREATE: (
                    "s001",
                    "s002",
                    "s003",
                    "lbl_cycles",
                    "s004",
                    "s005",
                    "chk001",
                    "pulse_colors",
                ),
                REVISE: ("pulse_colors",),
            },
            on_sync=self._sync_highlight_mode,
        )

    def _update_cycle_readout(self):
        """Restate how many cycles the train between the leads holds
        (``EffectRecipe.pulse_cycles``), in seconds like the fields.

        The readout is an aid, so it declines rather than raises when it cannot
        read the fields.
        """
        readout = getattr(self, "_cycle_readout", None)
        length = self.ui_field("s001")
        if readout is None or length is None:
            return
        try:
            cycles = self._recipe_obj().pulse_cycles(float(length.value()))
        except (TypeError, ValueError, AttributeError):
            return
        tail = "" if abs(cycles - int(cycles)) < 0.01 else " (last one is cut)"
        readout.setText(f"≈ {cycles:.1f} cycles{tail}")

    @CoreUtils.undoable
    def _apply_highlight_colors(self, objects, colors) -> list:
        """One undo step for the whole re-colour, however many ends it spans.

        *colors* is ``(bright, dim)``; an entry of ``None`` leaves that end
        alone.
        """
        written = []
        for stop, color in zip(("hi", "lo"), colors):
            if color is None:
                continue
            written = (
                RenderEffects.set_channel_color(
                    objects, color=color, channel=self.HIGHLIGHT, stop=stop
                )
                or written
            )
        return written

    def _authored_stops(self, objects):
        """``((bright, dim), mixed_flags)`` across *objects*.

        Mirror of mayatk's reader: where the objects DISAGREE the end is
        reported mixed rather than silently taking the first one's colour.
        """
        authored = (
            RenderEffects.channel_color_stops(objects, channel=self.HIGHLIGHT)
            if objects
            else {}
        )
        seeds, mixed = [], []
        for index, fallback in enumerate((self.DEFAULT_BRIGHT, self.DEFAULT_DIM)):
            found = [
                tuple(round(float(c), 6) for c in pair[index])
                for pair in authored.values()
                if index < len(pair) and pair[index] is not None
            ]
            unique = set(found)
            mixed.append(len(unique) > 1)
            seeds.append(next(iter(unique)) if len(unique) == 1 else fallback)
        # A colour property may legitimately hold >1 (HDR emission); the editor
        # cannot, so the SEED is clamped while the authored value is left alone.
        seeds = [tuple(min(1.0, max(0.0, c)) for c in rgb) for rgb in seeds]
        return tuple(seeds), tuple(mixed)

    def _sync_pulse_shape(self):
        """Run the preview at the cadence the recipe is set to."""
        ramp = getattr(self, "_pulse_ramp", None)
        if ramp is None:
            return
        recipe = self._recipe_obj()
        try:
            ramp.set_shape(period=recipe.pulse_period, duty=recipe.pulse_duty)
        except (TypeError, ValueError, AttributeError):
            return

    def _sync_highlight_mode(self, deep: bool = False, snapshot=None):
        """Point the colour row at whatever Key is about to write.

        Create shows the recipe's colours; Revise seeds from the targets'
        authored colours, an end they DISAGREE on reading mixed. Mirror of
        mayatk's.
        """
        ramp = getattr(self, "_pulse_ramp", None)
        if ramp is None:
            return
        if self._mode(self.HIGHLIGHT) == CREATE:
            self._show_recipe_colors(ramp)
            return

        selected, carried = (
            snapshot if snapshot is not None else (self._get_selected(), None)
        )
        if selected:
            targets = (
                carried.get(self.HIGHLIGHT, ())
                if carried is not None
                else self._carrying(self.HIGHLIGHT, selected)
            )
        elif deep:
            targets = RenderEffects.objects_with_channel(self.HIGHLIGHT)
        else:
            targets = []
        if not targets:
            ramp.set_reference(None)
            return
        (bright, dim), mixed = self._authored_stops(targets)
        blocked = ramp.blockSignals(True)
        try:
            ramp.set_colors((bright, dim))
            for index, is_mixed in enumerate(mixed):
                ramp.set_mixed(index, is_mixed)
        finally:
            ramp.blockSignals(blocked)
        ramp.set_reference(None if any(mixed) else (bright, dim))

    def _key_pulse_page(self):
        """Key Highlight Pulse — Create keys the glow, Revise re-colours it;
        focused from the manifest, Create re-keys there.

        Each branch opens its OWN undo step, so what is pushed is the thing that
        happened rather than the tool that did it.
        """
        if self._run_focused(self.HIGHLIGHT):
            return
        objects = self._targets(self.HIGHLIGHT)
        if not objects:
            return
        if self._mode(self.HIGHLIGHT) == REVISE:
            self._revise_highlight(objects)
            return
        self._key_highlight_pulse(objects)

    def _revise_highlight(self, objects):
        """Restate the colours on *objects*, leaving their pulse keys alone."""
        colors = self._pulse_ramp.decided()
        if not any(color is not None for color in colors):
            # Every end still reads mixed, so the artist has decided nothing.
            self.sb.message_box(
                "<strong>Nothing decided</strong>.<br>These objects disagree on "
                "both ends. Set an end to state what it should become."
            )
            return
        written = self._apply_highlight_colors(objects, colors)
        self.ui.footer.setText(
            "Highlight colours "
            + " / ".join(
                "unchanged" if c is None else ", ".join(f"{v:.2f}" for v in c)
                for c in colors
            )
            + f" — set on {len(written)} object(s)"
        )
        # deep: a scene-wide revision has no selection to re-read, and the
        # before/after should now show what was just written.
        self._sync_mode(self.HIGHLIGHT, deep=True)

    @CoreUtils.undoable
    def _key_highlight_pulse(self, objects):
        """Key the glow on *objects* from the scene's recipe, creating the
        property where it is missing."""
        recipe = self._recipe_obj()
        length_seconds = self.ui_field("s001").value()
        ends_at_cursor = self.ui_field("chk001").isChecked()
        fps = RenderEffects._scene_fps()
        start, end = self._key_range(length_seconds * fps, ends_at_cursor)
        bright, dim = self._pulse_ramp.decided()
        try:
            with self._suppressed():
                keyed = RenderEffects.key_pulse(
                    objects,
                    start=start,
                    end=end,
                    color=bright,
                    dim_color=dim,
                    channel=self.HIGHLIGHT,
                    recipe=recipe,
                )
        except Exception as e:
            self.sb.message_box(f"Error: {e}")
            return

        self.ui.footer.setText(
            f"Highlight pulse: {len(keyed)} object(s), frames "
            f"{int(start)}–{int(end)} @ {recipe.pulse_period:.2f} s "
            f"({recipe.pulse_cycles(length_seconds):.1f} cycles)"
        )
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Remove
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
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Selection job — gate the action row
    # ------------------------------------------------------------------

    def _on_scene_selection(self):
        """The selection job: a pick ends the last report, then the panel
        re-reads (mirror of mayatk's)."""
        try:
            if self.ui.isVisible():
                self._clear_report()
        except RuntimeError:
            return  # Deleted C++ object
        self._on_selection_changed()

    def _on_selection_changed(self):
        """Re-read the selection: the action row, and every mode readout.

        Both live on the same signal because both answer the same question --
        what does the selection already carry -- and asking it twice per change
        would double the property lookups for nothing.
        """
        if getattr(self, "_is_updating", False):
            return
        self._is_updating = True
        try:
            # Guard: skip if the UI has been destroyed (prevents crash when the
            # callback fires after the widget is garbage-collected).
            if not self.ui or not self.ui.isVisible():
                return
            snapshot = self._selection_snapshot()
            self._sync_actions(snapshot)
            for channel in (self.OPACITY, self.HIGHLIGHT):
                if channel in self._mode_combos:
                    self._sync_mode(channel, snapshot=snapshot)
        except RuntimeError:
            pass  # Deleted C++ object — swallow to prevent crash
        except Exception:
            logging.getLogger(__name__).debug(
                "_on_selection_changed error", exc_info=True
            )
        finally:
            self._is_updating = False


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("render_effects", reload=True)
    ui.show(pos="screen", app_exec=True)
