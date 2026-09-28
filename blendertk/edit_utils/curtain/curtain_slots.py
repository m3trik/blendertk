# !/usr/bin/python
# coding=utf-8
"""Curtain panel — the Switchboard slots for ``curtain.ui`` (Blender).

Mirror of mayatk's ``curtain_slots``: drives the curtain engine
(:mod:`._curtain`: :class:`Rail`, :class:`CurtainMesh`) through the snapshot
:class:`~blendertk.core_utils.preview.Preview`, with an in-panel preset combo
over the shipped ``presets/`` (identical to mayatk's). ``import bpy`` and the
Qt-only ``uitk`` helpers stay deferred into the call bodies.
"""

from pathlib import Path
from typing import Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.core_utils.preview import Preview
from blendertk.edit_utils.curtain._curtain import CurtainMesh, Rail
from blendertk.xform_utils._xform_utils import XformUtils

# Shipped, read-only curtain presets (UI-state snapshots). Identical to mayatk's — the panel
# shares the Maya widget names AND the vendored CurtainDrape engine, so a preset drapes the same.
_PRESETS_DIR = Path(__file__).resolve().parent / "presets"


class CurtainSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the curtain UI (live preview + rail resolution + presets).

    Blender port of mayatk's ``CurtainSlots``: the drape math is the vendored
    :class:`CurtainDrape` engine (code-identical with mayatk's copy — drift fails
    extapps' ``test_vendor_sync.py``), so every parameter behaves identically across
    DCCs — only the mesh build differs (``CurtainMesh.build``, bmesh). The rail resolves
    from the selection (edit-mode edges / a curve object / 2+ object positions) or,
    when nothing usable is selected, from a generated driver curve built from the
    Width/Curvature/Position/Closed fields — :meth:`_ensure_rail` builds and selects
    it the moment Preview is toggled on, mirroring mayatk's auto-rail so ``Preview``
    never requires an unrelated selection first. The same in-panel **preset combo** as
    Maya (shared ``uitk.PresetManager`` + the identical built-in presets). The
    wire-deformer rig is the :class:`CurtainRig` engine class (Maya's ``CurtainRig``);
    like Maya it is **not** wired into this panel — it is an engine-level capability
    with no tentacle nav button (Maya exposes it the same way).

    One real divergence from mayatk, driven by :class:`~blendertk.core_utils.preview.Preview`'s
    snapshot/restore design (vs. mayatk's node-diff ``CleanupContract``): a node mayatk's Preview
    didn't create survives a rollback untouched, so mayatk resyncs the driver curve's shape on
    every rail-field change and discards it on a plain cancel. Here, any object captured at
    ``enable()`` time is restored (even recreated) by every rollback, so a mid-preview resync would
    be immediately undone and a cancel-time delete would just be recreated by Preview's own
    restore — see :meth:`_ensure_rail` / :meth:`_sync_driver`. The curtain **mesh** itself is
    unaffected (it re-reads the live field values on every refresh); only the driver curve's own
    on-screen shape can lag behind the dialed Rail fields until the next Preview toggle, and a
    cancelled (not committed) session's generated driver is reclaimed on the next use rather than
    deleted immediately. Commit still drops it (see :meth:`_finalize`).

    Self-contained (``ptk.LoggingMixin`` only) so blendertk carries no back-dependency
    on tentacle — the selection comes from ``btk.selected_objects``, not a tentacle base.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curtain
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curtain] ")
        self.last_curtain: Optional[str] = None
        self.presets = None  # in-panel PresetManager (wired in cmb000_init)
        # Auto-rail state: when nothing usable is selected we own a generated driver
        # curve (``_driver``, its object name) built from the Width/Curvature/Hanging-
        # Points/Closed fields. ``_generated`` flags that mode.
        self._driver: Optional[str] = None
        self._generated: bool = False

        # Ensure a rail exists the moment Preview is toggled on (and, on commit,
        # discard our generated one). Connected BEFORE Preview so it runs first and
        # Preview.enable() finds a selection — you never have to select an unrelated
        # object.
        self.ui.chk000.toggled.connect(self._ensure_rail)

        # Per-parameter reset buttons must precede connect_multi/Preview — wrapping
        # reparents the widgets and invalidates any already-deferred wrapper. The X/Y/Z
        # Position triplet is skipped — it already shares a tight row with the Get button.
        self.sb.add_reset_buttons(self.ui, skip=("s025", "s026", "s027"))

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            finalize_func=self._finalize,
            message_func=self.sb.message_box,
            undo_message="Create Curtain",
        )
        # Re-drape live as any numeric field changes; Closed/Invert are pure re-drapes
        # (see _on_param_changed for why the driver curve itself isn't resynced here).
        self.sb.connect_multi(
            self.ui, "s000-27", "valueChanged", self._on_param_changed
        )
        self.sb.connect_multi(self.ui, "chk001,chk004", "clicked", self.preview.refresh)

        # The Position fields dropped their "X "/"Y "/"Z " prefixes; color-code the
        # values red/green/blue instead (axis convention) so the row stays compact
        # while still reading per-axis at a glance.
        self._color_code_position_fields()

        # Footer doubles as a stats readout (the result's tri count) once a curtain is
        # built; show a hint until then.
        try:
            self.ui.footer.setDefaultStatusText("Toggle Preview to drape a curtain.")
        except Exception:
            pass

    # --------------------------------------------------------------- header

    def header_init(self, widget):
        """Configure header help text (the preset combo lives in the panel)."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Curtain",
                body="Drape a pleated cloth curtain from a <b>rail</b> — a "
                "selected curve object, edit-mode mesh edge loop, or chain of "
                "objects, or a generated straight rail when nothing usable is "
                "selected.",
                steps=[
                    "Toggle <b>Preview</b> (a rail is auto-created from "
                    "Width/Curvature if you haven't selected your own).",
                    "Set <b>Hanging Points</b> (the pleats/pins) and <b>Fullness</b>.",
                    "Dial <b>Gravity</b> — how far the fabric falls between "
                    "hanging points.",
                    "Press <b>Create</b> to commit.",
                ],
                sections=[
                    (
                        "Model",
                        [
                            "Each <b>Hanging Point</b> is a pleat where the fabric "
                            "pins to the rail — one clean gather at the rail — and "
                            "bellies into a full fold between consecutive points, so "
                            "the count maps roughly 1:1 to the folds you see. The "
                            "spans sag down a real <b>catenary</b> (cosh).",
                            "<b>Gravity</b> sets the sag depth (wider gaps fall "
                            "further); <b>Catenary Tension</b> shapes that curve.",
                            "<b>Taper</b> gathers the pleats at the top and flares "
                            "them toward the hem.",
                            "<b>Mid Folds</b> fork V-folds down from some hang "
                            "points (seed varies which), breaking the plain in/out "
                            "belly; <b>Creases</b> add diagonal V break-lines; "
                            "<b>Sway</b> randomly leans a subset of the folds left "
                            "or right along the rail (not just in/out); the "
                            "<b>Ends</b> group bends each end; <b>Round</b> softens "
                            "the hooks.",
                        ],
                    ),
                ],
                notes=[
                    "The <b>preset</b> combo loads built-in looks "
                    "(Stage Swag, Shower Curtain) and saves your own.",
                    "<b>Select Result</b> selects the finished curtain on "
                    "<b>Create</b> so you can see the result.",
                    "Same engine as the Maya panel — identical settings drape "
                    "identically.",
                ],
            )
        )
        # Align every spinbox's value column once the panel's fonts/styles are settled
        # (deferred a tick so QFontMetrics sees the themed font).
        try:
            from qtpy import QtCore

            QtCore.QTimer.singleShot(0, self._align_spinbox_prefixes)
        except Exception as e:
            self.logger.debug(f"Prefix alignment deferral failed: {e}")

    def cmb000_init(self, widget):
        """Wire the in-panel preset selector (built-in + user tiers) — mirror of the Maya panel.

        A curtain preset is a UI-state snapshot of the drape fields; because the panel shares the
        Maya widget names *and* the vendored ``CurtainDrape`` engine, the built-in JSONs are
        identical across DCCs (shipped in ``edit_utils/curtain/presets``). Loading a preset resyncs
        the generated driver to the loaded fields, then refreshes the preview in one shot."""
        # Wrap construction + wiring (not just the import) so any failure degrades to "no presets,
        # panel still works" with a clean warning rather than a raw slot-init error.
        try:
            from uitk.managers.preset_manager import PresetManager

            self.presets = PresetManager(
                parent=self.ui,
                state=self.ui.state,
                preset_dir="blendertk/curtain",
                builtin_dir=str(_PRESETS_DIR),
            )
            self.presets.wire_combo(widget, on_loaded=self._on_param_changed)
        except Exception as e:  # uitk missing / older / wiring failure — non-fatal
            self.logger.warning(f"Preset combo unavailable: {e}")

    # ------------------------------------------------- spinbox value alignment

    def _color_code_position_fields(self) -> None:
        """Tint the rail Position values red/green/blue for X/Y/Z.

        Mirrors mayatk: the fields dropped their "X "/"Y "/"Z " prefixes (see curtain.ui);
        the axis-coded value text now carries that meaning at a glance, with the tooltips
        naming the axis as a textual fallback. Colors come from the shared
        ``pythontk.Palette.axes()`` (RGB axis convention), applied via the uitk
        ``DoubleSpinBox`` ``set_text_color`` helper.
        """
        try:
            axes = ptk.Palette.axes()
        except Exception as e:
            self.logger.debug(f"Position color-coding unavailable: {e}")
            return
        for name, key in (("s025", "x"), ("s026", "y"), ("s027", "z")):
            setter = getattr(getattr(self.ui, name, None), "set_text_color", None)
            if callable(setter):
                setter(axes[key].hex)

    def _align_spinbox_prefixes(self) -> None:
        """Pad each spinbox prefix so the values line up within each group.

        The custom spin widgets add a single ``\\t`` after the prefix, which only lands
        on one tab stop — long prefixes ("Catenary Tension:") then overflow past short
        ones ("Seed:"), so the value columns don't align. Here we measure the widest
        prefix per section (with the widget's own font metrics) and right-pad the rest
        with spaces to match (font-correct to within a space width), bypassing the
        ``\\t``. Aligning per group — keyed on each spinbox's container — keeps
        short-labelled sections tight instead of indenting them to clear a long label
        elsewhere.
        """
        try:
            from qtpy import QtWidgets, QtGui
        except Exception:
            return

        # Bucket the spinboxes by their titled group. Walk up to the nearest
        # CollapsableGroup rather than using the immediate parent, since the option-box
        # "disable" wrapping reparents each spinbox into its own container — grouping on
        # that would defeat the per-section alignment.
        try:
            from uitk.widgets.collapsableGroup import CollapsableGroup
        except Exception:
            CollapsableGroup = ()

        def _group_of(w):
            p = w.parentWidget()
            while p is not None:
                if CollapsableGroup and isinstance(p, CollapsableGroup):
                    return p
                p = p.parentWidget()
            return w.parentWidget()

        groups = {}
        for sb in self.ui.findChildren(QtWidgets.QAbstractSpinBox):
            # The AUTHORED label, not the rendered one: uitk's PrefixColumnMixin
            # collapses (and past a point elides) the prefix to keep the value
            # visible in a narrow field, so prefix() can be a truncated form.
            # Falls back for a plain QSpinBox and for a re-run over prefixes
            # this method already padded (which the mixin leaves verbatim).
            base = getattr(sb, "prefix_label", lambda: "")() or sb.prefix().rstrip()
            if not base:
                continue
            groups.setdefault(_group_of(sb), []).append(
                (sb, base, QtGui.QFontMetrics(sb.font()))
            )

        for entries in groups.values():
            max_w = max(fm.horizontalAdvance(base) for _, base, fm in entries)
            for sb, base, fm in entries:
                space_w = fm.horizontalAdvance(" ") or 1
                gap = max_w + 2 * space_w - fm.horizontalAdvance(base)
                text = base + " " * max(1, round(gap / space_w))
                # Bypass the custom setPrefix: an exact, hand-composed prefix is
                # the one form PrefixColumnMixin leaves alone, and routing it
                # through the override would strip this padding back off.
                if isinstance(sb, QtWidgets.QDoubleSpinBox):
                    QtWidgets.QDoubleSpinBox.setPrefix(sb, text)
                else:
                    QtWidgets.QSpinBox.setPrefix(sb, text)

    # ----------------------------------------------------------- rail / driver

    def _on_param_changed(self, *_):
        """A field changed: re-drape.

        Mirrors mayatk's hook name/point, but — unlike mayatk — doesn't resync the
        driver curve's shape here; see the class docstring and :meth:`_sync_driver` for
        why (blendertk's Preview would immediately undo it on the next refresh). The
        curtain mesh itself always tracks the live field values (``perform_operation``
        re-reads them every refresh), so the drape is correct regardless.
        """
        self.preview.refresh()

    def _field_rail(self):
        """The generated rail from the Width / Curvature / Position / Closed fields."""
        return Rail.make(
            width=self.ui.s001.value(),
            curvature=self.ui.s002.value(),
            closed=self.ui.chk001.isChecked(),
            center=(self.ui.s025.value(), self.ui.s026.value(), self.ui.s027.value()),
        )

    def _build_driver(self, points, closed) -> str:
        """Build a low-CV rail curve whose CVs sit at the hanging points.

        Blender mirror of mayatk's ``cmds.curve`` driver build — used as the preview's
        visible rail, resampled to ``hanging_points`` control points so it reads as the
        line of pins the cloth gathers on. Returns the new object's name.
        """
        import bpy

        n = max(2, int(self.ui.s003.value()))
        ctrl = Rail.resample(points, n)
        curve_data = bpy.data.curves.new("curtain_rail", type="CURVE")
        curve_data.dimensions = "3D"
        spline = curve_data.splines.new("POLY")
        spline.points.add(len(ctrl) - 1)
        for i, p in enumerate(ctrl):
            spline.points[i].co = (*p, 1.0)
        spline.use_cyclic_u = bool(closed)
        obj = bpy.data.objects.new("curtain_rail", curve_data)
        bpy.context.collection.objects.link(obj)
        return obj.name

    def _sync_driver(self) -> None:
        """(Re)build the owned driver curve from the current Rail fields and select it.

        Only called on preview-enable (see :meth:`_ensure_rail`) — not on every
        rail-field change like mayatk's version. blendertk's Preview snapshots the
        captured selection at ``enable()`` time and restores it on *every* rollback,
        so a mid-preview rebuild here would be overwritten by the very next refresh;
        see the class docstring.
        """
        import bpy

        if self._driver and self._driver in bpy.data.objects:
            self._discard_driver()
        points, closed = self._field_rail()
        self._driver = self._build_driver(points, closed)
        # the window's view layer + context (see _finalize)
        with CoreUtils.window_context_override():
            bpy.ops.object.select_all(action="DESELECT")
            obj = bpy.data.objects[self._driver]
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

    def _discard_driver(self) -> None:
        """Delete the generated driver curve we own (orphan-rail cleanup), if it exists."""
        import bpy

        if self._driver and self._driver in bpy.data.objects:
            obj = bpy.data.objects[self._driver]
            data = obj.data
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and data.users == 0:
                    bpy.data.curves.remove(data)
            except Exception:
                pass
        self._driver = None

    def _user_selection(self):
        """Current selection minus our own driver curve."""
        return [o for o in CoreUtils.selected_objects() if o.name != self._driver]

    def _ensure_rail(self, state: bool) -> None:
        """On preview-enable, guarantee a usable rail; a no-op on disable.

        If the user has their own rail selected we hang on that (Width/Curvature are
        ignored), discarding any generated driver we still own. Otherwise we enter
        *generated* mode and build/select a driver curve — both to satisfy Preview's
        selection gate and to show the rail the cloth hangs on.

        Nothing is discarded here on ``state=False``: unlike mayatk's node-diff
        Preview, blendertk's Preview restores (even recreates) whatever it captured at
        ``enable()`` time on every rollback, so deleting our driver on a plain cancel
        would just have Preview's own rollback bring it straight back. It is instead
        reclaimed the next time this fires (discarded-and-rebuilt, or discarded outright
        if a real rail is now selected) or dropped for good on commit — see
        :meth:`_finalize`.
        """
        if not state:
            return
        if Rail.from_selection(self._user_selection()) is not None:
            self._discard_driver()
            self._generated = False
            return
        self._generated = True
        self._sync_driver()

    def _resolve_rail(self, objects):
        """Rail points for the current drape.

        Generated mode reads the Width/Curvature/Closed fields live (so they take
        effect on every refresh); selected mode resolves the user's rail.
        """
        if not self._generated:
            rail = Rail.from_selection(
                [o for o in objects if getattr(o, "name", o) != self._driver]
            )
            if rail is not None:
                points, closed = rail
                return points, closed or self.ui.chk001.isChecked()
        return self._field_rail()

    # --------------------------------------------------------------- buttons

    def b001_init(self, widget):
        """Reset to Defaults, on uitk's shared reset grammar (Shift+Click saves the
        current values as the defaults, Ctrl+Shift+Click forgets them)."""
        from uitk.managers.reset_gesture import ResetGesture

        self._reset_gesture = ResetGesture(widget)

    def b002(self):
        """Set Position to the bounding-box center of the selected object(s).

        Centers the generated rail on whatever is selected (its combined world
        bounding box). Ignores the panel's own auto-rail driver and the curtain it's
        building, so Get centers on the *external* target. The three Position fields
        are set in one shot (signals blocked) and a single re-drape is fired, so the
        curtain re-centers immediately.
        """
        ours = {self._driver, self.last_curtain}
        sel = [o for o in CoreUtils.selected_objects() if o.name not in ours]
        if not sel:
            self.sb.message_box("Select object(s) to center the rail on.")
            return
        boxes = [XformUtils.get_world_bbox(o) for o in sel]
        mn = [min(b[0][i] for b in boxes) for i in range(3)]
        mx = [max(b[1][i] for b in boxes) for i in range(3)]
        for widget, value in zip(
            (self.ui.s025, self.ui.s026, self.ui.s027),
            ((mn[i] + mx[i]) / 2.0 for i in range(3)),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._on_param_changed()

    # ------------------------------------------------------------- operation

    def perform_operation(self, objects):
        """Build the curtain from the resolved rail (Preview entry point)."""
        points, closed = self._resolve_rail(objects)

        obj = CurtainMesh(
            points,
            height=self.ui.s000.value(),
            hanging_points=int(self.ui.s003.value()),
            hang_jitter=self.ui.s023.value(),
            hang_seed=int(self.ui.s024.value()),
            gravity=self.ui.s004.value(),
            tension=self.ui.s005.value(),
            round_points=self.ui.s013.value(),
            round_gather=self.ui.s022.value(),
            fullness=self.ui.s006.value(),
            taper=self.ui.s007.value(),
            mid_folds=self.ui.s019.value(),
            mid_fold_seed=int(self.ui.s010.value()),
            creases=self.ui.s014.value(),
            crease_seed=int(self.ui.s015.value()),
            sway=self.ui.s020.value(),
            sway_seed=int(self.ui.s021.value()),
            end_bend_left=self.ui.s016.value(),
            end_bend_right=self.ui.s017.value(),
            end_bend_falloff=self.ui.s018.value(),
            irregularity=self.ui.s008.value(),
            density=self.ui.s009.value(),
            reduce=self.ui.s012.value(),
            thickness=self.ui.s011.value(),
            invert=self.ui.chk004.isChecked(),
            closed=closed,
        ).build()
        self.last_curtain = obj.name
        self._update_footer()
        # Select Result is applied manually in _finalize (blendertk's Preview has no
        # built-in select_result_checkbox/result_provider like mayatk's) -- see there.

    def _update_footer(self):
        """Show the result's triangle count in the footer; clears to the default hint
        when there is no result. Updates live as the preview re-drapes."""
        import bpy

        try:
            footer = self.ui.footer
        except Exception:
            return
        obj = self.last_curtain and bpy.data.objects.get(self.last_curtain)
        if obj is None or obj.type != "MESH":
            footer.setStatusText("")  # falls back to the default hint
            return
        obj.data.calc_loop_triangles()
        tris = len(obj.data.loop_triangles)
        footer.setStatusText(f"{tris:,} tris")

    def _finalize(self):
        """On commit, drop the preview's auto-rail, then apply Select Result.

        The auto-rail is only a preview aid (it shows where the cloth hangs and
        satisfies Preview's selection gate); it isn't wanted in the committed scene.
        Safe to delete outright here (unlike a plain cancel — see the class
        docstring): ``Preview.commit()`` drops its own captured-object bookkeeping
        before calling this, with no rollback in between, so there's nothing left to
        resurrect it. The next preview recomputes the rail mode from the live
        selection, so the mode flag is cleared here too. Select Result is applied
        *after* the discard (which can change the active selection), so the result
        wins.
        """
        import bpy

        self._generated = False
        self._discard_driver()
        obj = self.last_curtain and bpy.data.objects.get(self.last_curtain)
        if obj is None or not self.ui.chk005.isChecked():
            return
        # the window's view layer: windowless, select_all / select_set / the active
        # write address the scene's default layer, not the one the window shows
        with CoreUtils.window_context_override():
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("curtain", reload=True)
    ui.show(pos="screen", app_exec=True)
