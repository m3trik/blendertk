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
from blendertk.core_utils._core_utils import selected_objects


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
        from uitk.widgets.mixins.tooltip_mixin import fmt

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
            fmt(
                title="Shell Xform",
                body="Move, flip, rotate, and straighten / mirror / distribute "
                "the selected UV shells.",
                steps=[
                    "Select mesh object(s).",
                    "<b>Move</b> nudges the selection's UVs by whole tiles "
                    "(set how many with <i>Step</i>).",
                    "<b>Flip / Rotate</b> mirrors or spins the UV maps about "
                    "their center (rotation amount = the angle field).",
                    "<b>Straighten / Mirror / Distribute</b> each expose their "
                    "own options in the option box (▸).",
                ],
            )
        )

    def _mesh_selection(self):
        """Selected mesh objects, or a message + empty list when there are none."""
        objects = [o for o in selected_objects() if o.type == "MESH"]
        if not objects:
            self.sb.message_box("Nothing selected.")
        return objects

    # ------------------------------------------------------------------ move to UV space (b023-b026)
    @btk.undoable
    def b023(self):
        """Move To UV Space: Left"""
        btk.move_uvs(selected_objects(), du=-1.0)

    @btk.undoable
    def b024(self):
        """Move To UV Space: Down"""
        btk.move_uvs(selected_objects(), dv=-1.0)

    @btk.undoable
    def b025(self):
        """Move To UV Space: Up"""
        btk.move_uvs(selected_objects(), dv=1.0)

    @btk.undoable
    def b026(self):
        """Move To UV Space: Right"""
        btk.move_uvs(selected_objects(), du=1.0)

    # ------------------------------------------------------------------ flip / rotate (b034-b037)
    @btk.undoable
    def b034(self):
        """Flip U: mirror the selection's UV maps horizontally about their bbox center."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, flip_u=True)

    @btk.undoable
    def b035(self):
        """Flip V: mirror the selection's UV maps vertically about their bbox center."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, flip_v=True)

    @btk.undoable
    def b036(self):
        """Rotate the selection's UV maps counter-clockwise by the s041 angle."""
        objects = self._mesh_selection()
        if objects:
            btk.transform_uvs(objects, angle=float(self.ui.s041.value()))

    @btk.undoable
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
            "QSpinBox", setPrefix="Angle: ", setObjectName="s001",
            set_limits=[0, 360], setValue=30,
            setToolTip="Maximum angle used for straightening UVs.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Straighten UV", setObjectName="chk018", setChecked=True,
            setToolTip="Snap near-horizontal UV edges flat.",  # Maya's label for the U axis
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Straighten V", setObjectName="chk019", setChecked=True,
            setToolTip="Snap near-vertical UV edges flat.",
        )
        # chk020 reuses the Maya objectName + label (same option, cross-DCC rule): a native
        # Follow Active Quads pass stands in for texStraightenShell (btk.straighten_uv_shells).
        widget.option_box.menu.add(
            "QCheckBox", setText="Straighten Shell", setObjectName="chk020",
            setToolTip="Rectangularize the whole shell by unfolding around a selected UV's "
            "edge loop (Follow Active Quads).",
        )

    @btk.undoable
    def tb005(self, widget):
        """Straighten UV (selected UV edges within the angle threshold snap flat; optionally
        rectangularize the whole shell)."""
        m = widget.option_box.menu
        snapped = btk.straighten_uvs(
            selected_objects(),
            u=m.chk018.isChecked(), v=m.chk019.isChecked(), angle=m.s001.value(),
        )
        straightened = 0
        if m.chk020.isChecked():
            straightened = btk.straighten_uv_shells(selected_objects())
        if not snapped and not straightened:
            self.sb.message_box(
                "<strong>Nothing straightened.</strong><br>Select UV edges in Edit Mode "
                "within the angle threshold."
            )

    # ------------------------------------------------------------------ tb006  Distribute
    def tb006_init(self, widget):
        widget.option_box.menu.setTitle("Distribute")
        widget.option_box.menu.add(
            "QRadioButton", setText="Distribute U", setObjectName="chk023", setChecked=True,
            setToolTip="Distribute along U.",
        )
        widget.option_box.menu.add(
            "QRadioButton", setText="Distribute V", setObjectName="chk024",
            setToolTip="Distribute along V.",
        )

    @btk.undoable
    def tb006(self, widget):
        """Distribute (space the targeted UV shells evenly along U or V)."""
        axis = "u" if widget.option_box.menu.chk023.isChecked() else "v"
        moved = btk.distribute_uv_shells(selected_objects(), axis=axis)
        if not moved:
            self.sb.message_box(
                "<strong>Nothing distributed.</strong><br>Needs three or more UV shells "
                "(in Edit Mode, shells touched by the selection)."
            )

    # ------------------------------------------------------------------ tb008  Mirror
    def tb008_init(self, widget):
        widget.option_box.menu.setTitle("Mirror UVs")
        widget.option_box.menu.add(
            "QRadioButton", setText="Mirror U", setObjectName="chk031", setChecked=True,
            setToolTip="Mirror across U. Default mode preserves the UV footprint.",
        )
        widget.option_box.menu.add(
            "QRadioButton", setText="Mirror V", setObjectName="chk032",
            setToolTip="Mirror across V. Default mode preserves the UV footprint.",
        )
        # chk033 + cmb_mirror_mode reuse the Maya objectNames (same options, cross-DCC rule).
        widget.option_box.menu.add(
            "QCheckBox", setText="Per Shell", setObjectName="chk033", setChecked=True,
            setToolTip="If enabled, mirrors each UV shell independently.",
        )
        # Preserve Footprint vs Geometric Mirror are two distinct algorithms, not a
        # modifier — a combobox names both states.
        mode = widget.option_box.menu.add(
            "QComboBox", setObjectName="cmb_mirror_mode",
            setToolTip="Preserve Footprint: keeps the exact UV point set via one-to-one "
            "reassignment.\nGeometric Mirror: reflects the UVs around the pivot.",
        )
        mode.addItems(["Preserve Footprint", "Geometric Mirror"])
        mode.setCurrentText("Preserve Footprint")  # preserve prior default (checkbox on)

    @btk.undoable
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
            objects, axis="u" if mirror_u else "v",
            per_shell=per_shell, preserve_position=preserve_position,
        )

    # ------------------------------------------------------------------ Align
    def _align(self, axis, mode):
        """Shared body for the Align buttons — dispatch to :func:`btk.align_uvs` and warn when the
        selection yields nothing to align (mirrors the Maya twin's ``performAlignUV`` group)."""
        if not btk.align_uvs(selected_objects(), axis=axis, mode=mode):
            self.sb.message_box(
                "<strong>Nothing aligned.</strong><br>Select UVs (Edit Mode) — or a mesh in "
                "Object Mode — to align."
            )

    @btk.undoable
    def align_u_min(self):
        """Align the selected UVs to their minimum U (left)."""
        self._align("u", "min")

    @btk.undoable
    def align_u_avg(self):
        """Align the selected UVs to their average U (center)."""
        self._align("u", "avg")

    @btk.undoable
    def align_u_max(self):
        """Align the selected UVs to their maximum U (right)."""
        self._align("u", "max")

    @btk.undoable
    def align_v_min(self):
        """Align the selected UVs to their minimum V (bottom)."""
        self._align("v", "min")

    @btk.undoable
    def align_v_avg(self):
        """Align the selected UVs to their average V (center)."""
        self._align("v", "avg")

    @btk.undoable
    def align_v_max(self):
        """Align the selected UVs to their maximum V (top)."""
        self._align("v", "max")

    @btk.undoable
    def linear_align(self):
        """Linearly align the selected UVs between their two end points."""
        self._align("u", "linear")  # axis is ignored for linear (projects onto the endpoint line)

    # ------------------------------------------------------------------ Orient
    @btk.undoable
    def orient_shells(self):
        """Orient each shell to run parallel with its nearest U/V axis (Align Rotation)."""
        if not btk.orient_uv_shells(selected_objects()):
            self.sb.message_box(
                "<strong>Nothing oriented.</strong><br>Enter Edit Mode and select UV shells."
            )

    @btk.undoable
    def orient_edges(self):
        """Orient the shell so its selected edge runs along U or V."""
        if not btk.orient_uv_shells(selected_objects(), to_edge=True):
            self.sb.message_box(
                "<strong>Nothing oriented.</strong><br>Enter Edit Mode and select a UV edge to "
                "orient the shell to."
            )

    @btk.undoable
    def gather_shells(self):
        """Gather the selected shells together toward the 0-1 UV space."""
        if not btk.gather_uv_shells(selected_objects()):
            self.sb.message_box(
                "<strong>Nothing gathered.</strong><br>Select shells sitting outside the 0-1 tile."
            )

    @btk.undoable
    def randomize_shells(self):
        """Randomly offset the selected shells. Each click advances a per-instance seed so repeated
        clicks re-shuffle (matching Maya's ``RandomizeShells``) rather than re-applying one offset;
        the engine helper stays deterministic for a given seed (testable)."""
        seed = getattr(self, "_randomize_seed", 0)
        self._randomize_seed = seed + 1
        if not btk.randomize_uv_shells(selected_objects(), seed=seed):
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
