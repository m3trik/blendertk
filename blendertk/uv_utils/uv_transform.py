# !/usr/bin/python
# coding=utf-8
"""Dedicated UV shell-transform panel (Blender).

Mirror of ``mayatk.uv_utils.uv_transform``. Provides :class:`UvTransformSlots`
for the ``uv_transform.ui`` panel: the four move-to-UV-space arrows (with a
tile step), Flip / Rotate, and the Straighten / Mirror / Distribute tools.

This is the shared cross-DCC subset — Maya's align / orient / gather / randomize /
select ops have no bpy analogue, so the Blender panel omits those groups (see
``tentacle/docs/parity_map.py``).

Co-located with its engine (:mod:`blendertk.uv_utils`) and discovered by
``BlenderUiHandler`` (``marking_menu.show("uv_transform")``). The Qt-only ``uitk``
imports are deferred into the methods that use them so the module stays importable
under headless Blender (``--background``, no Qt binding).
"""
import pythontk as ptk
import blendertk as btk
from blendertk.core_utils._core_utils import selected_objects


class UvTransformSlots(ptk.LoggingMixin):
    """Switchboard slots for the UV Transform panel (``uv_transform.ui``).

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
        self.ui = self.sb.loaded_ui.uv_transform

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

        widget.config_buttons("menu", "collapse", "hide")
        widget.menu.add(
            "QPushButton",
            setText="Open UV Editor",
            setObjectName="open_uv_editor",
            setToolTip="Open Blender's UV Editor to inspect the result.",
        )
        widget.menu.open_uv_editor.clicked.connect(self.open_uv_editor)
        widget.set_help_text(
            fmt(
                title="UV Transform",
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
        # chk033/chk034 reuse the Maya objectNames + labels (same options, cross-DCC rule).
        widget.option_box.menu.add(
            "QCheckBox", setText="Per Shell", setObjectName="chk033", setChecked=True,
            setToolTip="If enabled, mirrors each UV shell independently.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Preserve Footprint", setObjectName="chk034", setChecked=True,
            setToolTip="If enabled, preserves the exact UV point set using one-to-one "
            "reassignment.\nIf disabled, performs a geometric mirror around the pivot.",
        )

    @btk.undoable
    def tb008(self, widget):
        """Mirror UVs (footprint-preserving reassignment by default; per-shell by default)."""
        objects = self._mesh_selection()
        if not objects:
            return
        m = widget.option_box.menu
        mirror_u = m.chk031.isChecked()
        per_shell = m.chk033.isChecked()
        preserve_position = m.chk034.isChecked()
        btk.mirror_uvs(
            objects, axis="u" if mirror_u else "v",
            per_shell=per_shell, preserve_position=preserve_position,
        )

    # ------------------------------------------------------------------ header
    def open_uv_editor(self):
        """Open Blender's UV Editor."""
        btk.open_editor("UV Editor")


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
