# !/usr/bin/python
# coding=utf-8
"""Dedicated UV shell-transform panel (Blender).

Mirror of ``mayatk.uv_utils.shell_xform``. Provides :class:`ShellXformSlots`
for the ``shell_xform.ui`` panel: the four move-to-UV-space arrows (with a
tile step), Flip / Rotate, the Straighten / Mirror / Distribute tools, and the
Align / Orient shell helpers.

Full cross-DCC parity — every Maya shell op has a Blender realization: Align
(min/avg/max/linear) and Gather are bmesh helpers (:func:`btk.align_uvs` /
:func:`btk.gather_uv_shells`), Orient and Randomize wrap the native
``uv.align_rotation`` / ``uv.randomize_uv_transform`` operators
(:func:`btk.orient_uv_shells` / :func:`btk.randomize_uv_shells`). Only the
back-facing / overlapping / unmapped *select* filters stay Maya-only (removed
from the panel 2026-07-08; see ``tentacle/docs/parity_map.py``).

Co-located with its engine (:mod:`blendertk.uv_utils`) and discovered by
``BlenderUiHandler`` (``marking_menu.show("shell_xform")``). The Qt-only ``uitk``
imports are deferred into the methods that use them so the module stays importable
under headless Blender (``--background``, no Qt binding).
"""

import pythontk as ptk
import blendertk as btk
from blendertk.core_utils._core_utils import CoreUtils


class ShellXformSlots(ptk.LoggingMixin):
    """Switchboard slots for the Shell Xform panel (``shell_xform.ui``).

    Composition over inheritance: the slots dispatch to :mod:`blendertk.uv_utils`
    and resolve the selection via :func:`btk.selected_objects` (tentacle-independent,
    exactly like the other co-located blendertk tool panels). Widget names match the
    Maya twin for the shared ops so the parity sweep diffs them 1:1.
    """

    # SVG arrow icon installed on each move-pad button (Rotate keeps its glyphs).
    _MOVE_ICONS = {
        "b023": "arrow_left",
        "b025": "arrow_up",
        "b024": "arrow_down",
        "b026": "arrow_right",
    }

    # Move-pad scope -> step in UV units, carried as the combo item's *data* so
    # the label and the step it means cannot drift apart. `None` = derived at
    # click time from the selection's own UV bounds.
    _MOVE_SCOPES = {
        "Tile": 1.0,
        "Half Tile": 0.5,
        "Quarter Tile": 0.25,
        "Selection Bounds": None,
    }

    # Snap modes for the move pad's option-box button, in cycle order (the
    # indices ARE the cycle positions, so `_snap_states` must list them in this
    # order). One tri-state button rather than two: the modes are mutually
    # exclusive answers to a single question — snap to what?
    _SNAP_OFF, _SNAP_GRID, _SNAP_SHELL = range(3)

    # A UV extent at or below this is treated as collapsed: dividing by it would
    # blow the grid math up, so the arrow falls back to a whole tile.
    _MIN_EXTENT = 1e-6

    # Map size the tile border padding derives from. The normalized margin is
    # map-size-invariant (``uv_tile_margin`` == 1/512 at every resolution), so
    # this only names the rule — the panel needs no map-size control.
    _MAP_SIZE = 4096

    def __init__(self, switchboard, log_level: str = "WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shell_xform

        # Icons install on the next tick: the switchboard builds this slots
        # instance mid-load, so the child widgets aren't wired onto self.ui
        # until register_children runs after __init__.
        self.sb.QtCore.QTimer.singleShot(0, self._initialize_ui)

    def _initialize_ui(self):
        """Install the move-pad arrow icons (deferred; see __init__)."""
        from uitk import IconManager

        for name, icon in self._MOVE_ICONS.items():
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setText("")
                IconManager.set_icon(widget, icon, size=(16, 16))

    def header_init(self, widget):
        """Header menu — Open UV Editor + panel help."""
        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.menu.add(
            "QPushButton",
            setText="Open UV Editor",
            setObjectName="open_uv_editor",
            setToolTip="Open Blender's UV Editor to inspect the result.",
        )
        widget.menu.open_uv_editor.clicked.connect(self.open_uv_editor)
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Shell Xform",
                body="Move, flip, rotate, and straighten / mirror / distribute "
                "the selected UV shells.",
                steps=[
                    "Select mesh object(s).",
                    "<b>Move</b> nudges the selection's UVs by one <i>scope</i> — "
                    "a whole tile, a fraction of one, or the selection's own "
                    "size.",
                    "The snap button beside the scope cycles three modes: "
                    "<b>grey ▦</b> off (offset by the scope, keeping any drift), "
                    "<b>blue ▦</b> snap to the scope's grid inset by the border "
                    "padding, and <b>amber ⌖</b> snap to the next <i>shell</i> — "
                    "park against the nearest neighbour in that direction, "
                    "skipping gaps too small to fit and falling back to the grid "
                    "when nothing lies ahead.",
                    "<b>Gather to Tile</b> moves shells sitting outside the "
                    "selection's UDIM tile into it — whole-tile offsets keep "
                    "each shell's sub-tile position (no repack).",
                    "<b>Flip / Rotate</b> mirrors or spins the UV maps about "
                    "their center (rotation amount = the angle field).",
                    "<b>Straighten / Mirror / Distribute</b> each expose their "
                    "own options in the option box (▸).",
                ],
            )
        )

    def _mesh_selection(self):
        """Selected mesh objects, or a message + empty list when there are none."""
        objects = [o for o in CoreUtils.selected_objects() if o.type == "MESH"]
        if not objects:
            self.sb.message_box("Nothing selected.")
        return objects

    # ------------------------------------------------------------------ move to UV space (b023-b026)
    def cmb_move_scope_init(self, widget):
        """Move scope — how far one arrow press travels, plus the snap button.

        Items are built from ``_MOVE_SCOPES`` with the step as item data, so the
        step is read straight off the current item — a label edited in one place
        can no longer mean a different distance somewhere else. Snap rides along
        as an option-box button rather than extra items, because the mode
        composes with every scope instead of replacing one.
        """
        if widget.is_initialized:
            return
        widget.add(self._MOVE_SCOPES)
        # The ActionOption owns its own index persistence, so the restored mode
        # is whatever the user left it on. A fresh key: the old one held a bool
        # and would restore as a state index.
        self._snap_action = widget.option_box.set_action(
            states=self._snap_states(),
            settings_key="shell_xform_move_snap_mode",
        )

    def _snap_states(self):
        """Option-box cycle states for the snap button, in `_SNAP_*` order.

        Icon *and* tint change per state: two enabled modes are easy to confuse
        by colour alone, and the off state has to read as inert at a glance.
        Colours come from the shared status palette so they track the theme.
        """
        status = ptk.Palette.status()
        return [
            {
                "icon": "grid",
                "color": status["locked"][0],
                "tooltip": "Snap: off. Arrows offset by the scope, keeping any "
                "sub-tile drift. Click to snap to the grid.",
            },
            {
                "icon": "grid",
                "color": status["info"][0],
                "tooltip": "Snap: grid. Arrows land the selection on the scope's "
                "grid, inset by the tile border padding. Click to snap to shells.",
            },
            {
                "icon": "target",
                "color": status["warn"][0],
                "tooltip": "Snap: shell. Arrows park the selection against the "
                "next shell in that direction, keeping the border padding, and "
                "skip gaps too small to fit. Falls back to the grid when nothing "
                "lies ahead. Click to turn snapping off.",
            },
        ]

    def _snap_mode(self) -> int:
        """Current snap mode — `_SNAP_OFF`, `_SNAP_GRID`, or `_SNAP_SHELL`."""
        action = getattr(self, "_snap_action", None)
        return self._SNAP_OFF if action is None else int(action.current_state)

    def _move_step(self, bounds) -> tuple:
        """Per-axis ``(step_u, step_v)`` for the current scope.

        The step comes from the current item's data; ``None`` means "derive it
        from the selection", which is what *bounds* — a ``(u_min, v_min, u_max,
        v_max)`` tuple — is for. A degenerate extent (a shell collapsed on one
        axis) falls back to a whole tile so the arrow still does something.
        """
        step = self.ui.cmb_move_scope.currentData()
        if step is not None:
            return (step, step)

        u_min, v_min, u_max, v_max = bounds
        width, height = u_max - u_min, v_max - v_min
        return (
            width if width > self._MIN_EXTENT else 1.0,
            height if height > self._MIN_EXTENT else 1.0,
        )

    def _move(self, du: int, dv: int):
        """Nudge the selected UVs one step along ``(du, dv)``.

        The snap mode picks the rule: ``_SNAP_OFF`` offsets by the scope,
        ``_SNAP_GRID`` lands on the scope's padded grid, and ``_SNAP_SHELL``
        parks against the next neighbouring shell. Shell snap ignores the scope
        entirely — the neighbour sets the distance — and degrades to the grid
        rule whenever nothing lies ahead, so the arrow never reads as dead.
        """
        objects = self._mesh_selection()
        if not objects:
            return

        bounds = btk.get_uv_bounds(objects)
        if bounds is None:
            self.sb.message_box("<b>No UVs found.</b><br>Select a mesh with UVs.")
            return

        mode = self._snap_mode()
        snap = mode != self._SNAP_OFF
        margin = self._border_margin() if snap else 0.0

        offset_u = offset_v = None
        if mode == self._SNAP_SHELL:
            blockers = btk.get_neighbor_shell_bounds(objects)
            # Only the travelled axis can resolve — the other's direction is 0,
            # which `next_clear_offset` reports as None.
            offset_u = ptk.MathUtils.next_clear_offset(
                bounds, blockers, 0, du, margin=margin
            )
            offset_v = ptk.MathUtils.next_clear_offset(
                bounds, blockers, 1, dv, margin=margin
            )

        if offset_u is None and offset_v is None:
            # Snap anchors on the selection's lower-left corner, so "up" means the
            # shell's bottom edge lands on the next grid line — what the eye expects.
            # The grid is offset by the tile border padding, so a snapped shell sits
            # just inside the line rather than on it (a shell exactly on a tile seam
            # bleeds across it at render time). Snapping the *unpadded* anchor and
            # adding the margin back keeps the grid uniform in both directions —
            # padding the result instead would strand the reverse press on the
            # margin it just added, and the arrow would read as dead.
            step_u, step_v = self._move_step(bounds)
            offset_u = ptk.MathUtils.step_offset(
                bounds[0] - margin, step_u, du, snap=snap
            )
            offset_v = ptk.MathUtils.step_offset(
                bounds[1] - margin, step_v, dv, snap=snap
            )

        btk.move_uvs(objects, du=offset_u or 0.0, dv=offset_v or 0.0)

    def _border_margin(self) -> float:
        """Normalized tile border the snap keeps clear.

        Gather derives the same margin inside the engine from the same
        ``_MAP_SIZE``, so both routes inset by an identical amount.
        """
        return ptk.MathUtils.uv_tile_margin(self._MAP_SIZE)

    @CoreUtils.undoable
    def b023(self):
        """Move To UV Space: Left"""
        self._move(-1, 0)

    @CoreUtils.undoable
    def b024(self):
        """Move To UV Space: Down"""
        self._move(0, -1)

    @CoreUtils.undoable
    def b025(self):
        """Move To UV Space: Up"""
        self._move(0, 1)

    @CoreUtils.undoable
    def b026(self):
        """Move To UV Space: Right"""
        self._move(1, 0)

    @CoreUtils.undoable
    def gather_to_udim(self):
        """Move shells sitting outside the selection's UDIM tile into it.

        The cheap counterpart to a repack: each stray shell keeps its
        sub-tile position, inset by the same border padding the snap uses.
        The target tile is the one most of the selection's shells already
        occupy, so the majority stays put.
        """
        objects = self._mesh_selection()
        if not objects:
            return

        moved = btk.gather_to_udim(objects, map_size=self._MAP_SIZE)
        if moved is None:
            self.sb.message_box("<b>No UVs found.</b><br>Select a mesh with UVs.")
        elif not moved:
            self.sb.message_box(
                "<strong>Nothing to gather.</strong><br>Every shell is in the tile."
            )

    # ------------------------------------------------------------------ flip / rotate (b034-b037)
    @CoreUtils.undoable
    def b034(self):
        """Flip U: mirror the selection's UV maps horizontally about their bbox center."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, flip_u=True)

    @CoreUtils.undoable
    def b035(self):
        """Flip V: mirror the selection's UV maps vertically about their bbox center."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, flip_v=True)

    @CoreUtils.undoable
    def b036(self):
        """Rotate the selection's UV maps counter-clockwise by the s041 angle."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, angle=float(self.ui.s041.value()))

    @CoreUtils.undoable
    def b037(self):
        """Rotate the selection's UV maps clockwise by the s041 angle."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, angle=-float(self.ui.s041.value()))

    def s041(self, value, widget):
        """Rotate Angle — passive input; read by the Rotate buttons (b036/b037). Nothing to do."""

    # ------------------------------------------------------------------ tb005  Straighten
    def tb005_init(self, widget):
        widget.option_box.menu.setTitle("Straighten")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Angle: ",
            setObjectName="s001",
            set_limits=[0, 360],
            setValue=30,
            setToolTip="Maximum angle used for straightening UVs.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Straighten UV",
            setObjectName="chk018",
            setChecked=True,
            setToolTip="Snap near-horizontal UV edges flat.",  # Maya's label for the U axis
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Straighten V",
            setObjectName="chk019",
            setChecked=True,
            setToolTip="Snap near-vertical UV edges flat.",
        )
        # chk020 reuses the Maya objectName + label (same option, cross-DCC rule): a native
        # Follow Active Quads pass stands in for texStraightenShell (btk.straighten_uv_shells).
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Straighten Shell",
            setObjectName="chk020",
            setToolTip="Rectangularize the whole shell by unfolding around a selected UV's "
            "edge loop (Follow Active Quads).",
        )

    @CoreUtils.undoable
    def tb005(self, widget):
        """Straighten UV (selected UV edges within the angle threshold snap flat; optionally
        rectangularize the whole shell)."""
        m = widget.option_box.menu
        snapped = btk.straighten_uvs(
            CoreUtils.selected_objects(),
            u=m.chk018.isChecked(),
            v=m.chk019.isChecked(),
            angle=m.s001.value(),
        )
        straightened = 0
        if m.chk020.isChecked():
            straightened = btk.straighten_uv_shells(CoreUtils.selected_objects())
        if not snapped and not straightened:
            self.sb.message_box(
                "<strong>Nothing straightened.</strong><br>Select UV edges in Edit Mode "
                "within the angle threshold."
            )

    # ------------------------------------------------------------------ tb006  Distribute
    def tb006_init(self, widget):
        widget.option_box.menu.setTitle("Distribute")
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Distribute U",
            setObjectName="chk023",
            setChecked=True,
            setToolTip="Distribute along U.",
        )
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Distribute V",
            setObjectName="chk024",
            setToolTip="Distribute along V.",
        )

    @CoreUtils.undoable
    def tb006(self, widget):
        """Distribute (space the targeted UV shells evenly along U or V)."""
        axis = "u" if widget.option_box.menu.chk023.isChecked() else "v"
        moved = btk.distribute_uv_shells(CoreUtils.selected_objects(), axis=axis)
        if not moved:
            self.sb.message_box(
                "<strong>Nothing distributed.</strong><br>Needs three or more UV shells "
                "(in Edit Mode, shells touched by the selection)."
            )

    # ------------------------------------------------------------------ tb008  Mirror
    def tb008_init(self, widget):
        widget.option_box.menu.setTitle("Mirror UVs")
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Mirror U",
            setObjectName="chk031",
            setChecked=True,
            setToolTip="Mirror across U. Default mode preserves the UV footprint.",
        )
        widget.option_box.menu.add(
            "QRadioButton",
            setText="Mirror V",
            setObjectName="chk032",
            setToolTip="Mirror across V. Default mode preserves the UV footprint.",
        )
        # chk033 + cmb_mirror_mode reuse the Maya objectNames (same options, cross-DCC rule).
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Per Shell",
            setObjectName="chk033",
            setChecked=True,
            setToolTip="If enabled, mirrors each UV shell independently.",
        )
        # Preserve Footprint vs Geometric Mirror are two distinct algorithms, not a
        # modifier — a combobox names both states.
        mode = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_mirror_mode",
            setToolTip="Preserve Footprint: keeps the exact UV point set via one-to-one "
            "reassignment.\nGeometric Mirror: reflects the UVs around the pivot.",
        )
        mode.addItems(["Preserve Footprint", "Geometric Mirror"])
        mode.setCurrentText(
            "Preserve Footprint"
        )  # preserve prior default (checkbox on)

    @CoreUtils.undoable
    def tb008(self, widget):
        """Mirror UVs (footprint-preserving reassignment by default; per-shell by default)."""
        objects = self._mesh_selection()
        if not objects:
            return
        m = widget.option_box.menu
        mirror_u = m.chk031.isChecked()
        per_shell = m.chk033.isChecked()
        preserve_position = m.cmb_mirror_mode.currentText() == "Preserve Footprint"
        btk.mirror_uvs(
            objects,
            axis="u" if mirror_u else "v",
            per_shell=per_shell,
            preserve_position=preserve_position,
        )

    # ------------------------------------------------------------------ Align
    def _align(self, axis, mode):
        """Shared body for the Align buttons — dispatch to :func:`btk.align_uvs` and warn when the
        selection yields nothing to align (mirrors the Maya twin's ``performAlignUV`` group)."""
        if not btk.align_uvs(CoreUtils.selected_objects(), axis=axis, mode=mode):
            self.sb.message_box(
                "<strong>Nothing aligned.</strong><br>Select UVs (Edit Mode) — or a mesh in "
                "Object Mode — to align."
            )

    @CoreUtils.undoable
    def align_u_min(self):
        """Align the selected UVs to their minimum U (left)."""
        self._align("u", "min")

    @CoreUtils.undoable
    def align_u_avg(self):
        """Align the selected UVs to their average U (center)."""
        self._align("u", "avg")

    @CoreUtils.undoable
    def align_u_max(self):
        """Align the selected UVs to their maximum U (right)."""
        self._align("u", "max")

    @CoreUtils.undoable
    def align_v_min(self):
        """Align the selected UVs to their minimum V (bottom)."""
        self._align("v", "min")

    @CoreUtils.undoable
    def align_v_avg(self):
        """Align the selected UVs to their average V (center)."""
        self._align("v", "avg")

    @CoreUtils.undoable
    def align_v_max(self):
        """Align the selected UVs to their maximum V (top)."""
        self._align("v", "max")

    @CoreUtils.undoable
    def linear_align(self):
        """Linearly align the selected UVs between their two end points."""
        self._align(
            "u", "linear"
        )  # axis is ignored for linear (projects onto the endpoint line)

    # ------------------------------------------------------------------ Orient
    @CoreUtils.undoable
    def orient_shells(self):
        """Orient each shell to run parallel with its nearest U/V axis (Align Rotation)."""
        if not btk.orient_uv_shells(CoreUtils.selected_objects()):
            self.sb.message_box(
                "<strong>Nothing oriented.</strong><br>Enter Edit Mode and select UV shells."
            )

    @CoreUtils.undoable
    def orient_edges(self):
        """Orient the shell so its selected edge runs along U or V."""
        if not btk.orient_uv_shells(CoreUtils.selected_objects(), to_edge=True):
            self.sb.message_box(
                "<strong>Nothing oriented.</strong><br>Enter Edit Mode and select a UV edge to "
                "orient the shell to."
            )

    @CoreUtils.undoable
    def gather_shells(self):
        """Gather the selected shells together toward the 0-1 UV space."""
        if not btk.gather_uv_shells(CoreUtils.selected_objects()):
            self.sb.message_box(
                "<strong>Nothing gathered.</strong><br>Select shells sitting outside the 0-1 tile."
            )

    @CoreUtils.undoable
    def randomize_shells(self):
        """Randomly offset the selected shells. Each click advances a per-instance seed so repeated
        clicks re-shuffle (matching Maya's ``RandomizeShells``) rather than re-applying one offset;
        the engine helper stays deterministic for a given seed (testable)."""
        seed = getattr(self, "_randomize_seed", 0)
        self._randomize_seed = seed + 1
        if not btk.randomize_uv_shells(CoreUtils.selected_objects(), seed=seed):
            self.sb.message_box(
                "<strong>Nothing randomized.</strong><br>Enter Edit Mode and select UV shells."
            )

    # ------------------------------------------------------------------ header
    def open_uv_editor(self):
        """Open Blender's UV Editor."""
        btk.open_editor("UV Editor")


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
