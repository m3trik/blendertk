# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.

Batch find / rename / convert-case / strip-chars / suffix-by-location / suffix-by-type, each with an
option box (▸). Engine is :class:`~blendertk.edit_utils.naming._naming.Naming`. The ``Scope`` header
combo (Selection / All Objects) applies to every operation.

The Qt-only ``uitk`` imports (``Signals``/``fmt``) load with this slots module, which is only
imported in a Qt context (the UI handler / panel open), never by the headless engine path.
"""
import pythontk as ptk
from uitk import Signals

from blendertk.edit_utils.naming._naming import Naming


class NamingSlots(Naming, ptk.LoggingMixin):
    """Switchboard slots for the Naming panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.naming
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[naming] ")

    # ------------------------------------------------------------------ helpers
    def _scope_objects(self, selected_filter=None):
        """Objects in scope per the header combo. ``selected_filter`` (a type str) narrows to that
        object type. Returns a list of bpy objects."""
        import bpy

        use_all = self.ui.header.menu.cmb_scope.currentText() == "All Objects"
        objs = list(bpy.data.objects) if use_all else list(bpy.context.selected_objects or [])
        if selected_filter:
            objs = [o for o in objs if o.type == selected_filter]
        return objs

    @property
    def valid_suffixes(self):
        """Current type suffixes from the tb003 option-box fields (empties filtered out)."""
        try:
            m = self.ui.tb003.option_box.menu
            suffixes = [
                m.tb003_txt000.text(), m.tb003_txt001.text(), m.tb003_txt002.text(),
                m.tb003_txt003.text(), m.tb003_txt004.text(), m.tb003_txt005.text(),
                m.tb003_txt006.text(),
            ]
            return [s for s in suffixes if s]
        except (AttributeError, RuntimeError):
            return ["_GRP", "_LOC", "_JNT", "_GEO", "_CRV", "_CAM", "_LGT"]

    # ------------------------------------------------------------------ header
    def header_init(self, widget):
        """Scope combo + help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add("Separator", setTitle="Scope")
        widget.menu.add(
            "QComboBox", addItems=["Selection", "All Objects"], setObjectName="cmb_scope",
            setToolTip="Operate on the current Selection or All scene objects.",
        )
        widget.set_help_text(
            fmt(
                title="Naming",
                body="Batch find, rename, and suffix objects. Each operation button has an option "
                "box (▸) for its parameters; the header Scope applies to all of them.",
                sections=[
                    ("Operations", [
                        "<b>Find</b> — select objects by name pattern (wildcards or regex).",
                        "<b>Rename</b> — replace matched names with a new pattern (option box: "
                        "retain existing type suffix; ignore find).",
                        "<b>Convert Case</b> — upper / lower / title / capitalize / swapcase.",
                        "<b>Strip Chars</b> — remove leading or trailing characters.",
                        "<b>Suffix by Location</b> — auto-number by distance from a reference point.",
                        "<b>Suffix by Type</b> — append type suffixes (_GEO, _GRP, _CRV, …).",
                    ]),
                ],
            )
        )

    # ------------------------------------------------------------------ find
    def txt000_init(self, widget):
        widget.option_box.menu.setTitle("Find")
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox", setText="Ignore Case", setObjectName="chk000",
            setToolTip="Search case-insensitive.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Regular Expression", setObjectName="chk001",
            setToolTip="Use regex instead of the default '*' / '|' wildcards.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Empties Only", setObjectName="chk007",
            setToolTip="Limit the search to Empty objects (Blender's locator analogue).",
        )
        widget.option_box.set_action(
            callback=widget.returnPressed.emit, icon="search",
            tooltip="Find matching objects (same as pressing Enter).",
        )

    @Signals("returnPressed")
    def txt000(self, widget):
        """Find — select objects whose name matches the pattern."""
        import bpy

        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign = widget.ui.txt000.option_box.menu.chk000.isChecked()
        empties_only = widget.ui.txt000.option_box.menu.chk007.isChecked()
        text = widget.text()
        if not text:
            return
        objects = [
            o for o in bpy.data.objects if (o.type == "EMPTY" or not empties_only)
        ]
        found = ptk.find_str(text, [o.name for o in objects], regex=regex, ignore_case=ign)
        matched = [o for o in objects if o.name in found]
        bpy.ops.object.select_all(action="DESELECT")
        for o in matched:
            o.select_set(True)
        if matched:
            bpy.context.view_layer.objects.active = matched[0]
        self.ui.footer.setText(
            f"Found {len(matched)} object(s) matching '{text}'." if matched
            else f"No objects found matching '{text}'."
        )

    # ------------------------------------------------------------------ rename
    def txt001_init(self, widget):
        widget.option_box.menu.setTitle("Rename")
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox", setText="Retain Suffix", setObjectName="chk002",
            setToolTip="Re-append the object's existing type suffix (per Suffix By Type).",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Ignore Find", setObjectName="chk008",
            setToolTip="Ignore the Find field and rename all in-scope objects.",
        )
        widget.option_box.set_action(
            callback=widget.returnPressed.emit, icon="edit",
            tooltip="Rename matched objects (same as pressing Enter).",
        )

    @Signals("returnPressed")
    def txt001(self, widget):
        """Rename — replace matched names with the given pattern."""
        find = widget.ui.txt000.text()
        to = widget.text()
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign = widget.ui.txt000.option_box.menu.chk000.isChecked()
        retain_suffix = widget.ui.txt001.option_box.menu.chk002.isChecked()
        ignore_find = widget.ui.txt001.option_box.menu.chk008.isChecked()

        objects = self._scope_objects()
        if not objects:
            self.sb.message_box("Nothing in scope. Switch Scope to 'All Objects' to rename the scene.")
            return
        self.rename(
            objects, to, "" if ignore_find else find,
            regex=regex, ignore_case=ign, retain_suffix=retain_suffix,
            valid_suffixes=self.valid_suffixes if retain_suffix else None,
        )
        self.ui.footer.setText(f"Renamed {len(objects)} object(s) to pattern '{to}'.")

    # ------------------------------------------------------------------ convert case
    def tb000_init(self, widget):
        widget.option_box.menu.setTitle("Convert Case")
        widget.option_box.menu.add(
            "QComboBox", addItems=["capitalize", "upper", "lower", "swapcase", "title"],
            setObjectName="cmb001", setToolTip="Python string case operator to apply.",
        )

    def tb000(self, widget):
        """Convert Case."""
        objects = self._scope_objects()
        if not objects:
            self.sb.message_box("Nothing in scope.")
            return
        self.set_case(objects, widget.option_box.menu.cmb001.currentText())

    # ------------------------------------------------------------------ suffix by location
    def tb001_init(self, widget):
        widget.option_box.menu.setTitle("Suffix By Location")
        widget.option_box.menu.add(
            "QCheckBox", setText="First Object As Reference", setObjectName="chk006",
            setToolTip="Use the first selected object's center as the reference, else the origin.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Alphabetical", setObjectName="chk005",
            setToolTip="Use A/B/C suffixes when ≤26 objects, else integers.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Strip Trailing Integers", setObjectName="chk002",
            setChecked=True, setToolTip="Strip trailing integers (e.g. '123' of 'cube123').",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Strip Defined Suffixes", setObjectName="chk003",
            setChecked=True, setToolTip="Strip the Suffix-By-Type suffixes (_GRP, _LOC, …).",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Independent Groups", setObjectName="chk007",
            setToolTip="Group by base name and suffix each group independently.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Reverse", setObjectName="chk004",
            setToolTip="Reverse the order (farthest object first).",
        )

    def tb001(self, widget):
        """Suffix By Location."""
        m = widget.option_box.menu
        objects = self._scope_objects()
        if not objects:
            self.sb.message_box("Nothing in scope.")
            return
        self.append_location_based_suffix(
            objects,
            first_obj_as_ref=m.chk006.isChecked(),
            alphabetical=m.chk005.isChecked(),
            strip_trailing_ints=m.chk002.isChecked(),
            strip_defined_suffixes=m.chk003.isChecked(),
            valid_suffixes=self.valid_suffixes,
            reverse=m.chk004.isChecked(),
            independent_groups=m.chk007.isChecked(),
        )

    # ------------------------------------------------------------------ strip chars
    def tb002_init(self, widget):
        widget.option_box.menu.setTitle("Strip Chars")
        widget.option_box.menu.add(
            "QSpinBox", setPrefix="Num Chars:", setObjectName="s000", setValue=1,
            setToolTip="Number of characters to delete.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Trailing", setObjectName="chk005", setChecked=True,
            setToolTip="Delete from the end of the name instead of the start.",
        )

    def tb002(self, widget):
        """Strip Chars."""
        objects = self._scope_objects()
        if not objects:
            self.sb.message_box("Nothing in scope.")
            return
        self.strip_chars(
            objects,
            num_chars=widget.option_box.menu.s000.value(),
            trailing=widget.option_box.menu.chk005.isChecked(),
        )

    # ------------------------------------------------------------------ suffix by type
    def tb003_init(self, widget):
        widget.option_box.menu.setTitle("Suffix By Type")
        for obj_name, default, tip in (
            ("tb003_txt000", "_GRP", "Suffix for transform groups (Empties with children)."),
            ("tb003_txt001", "_LOC", "Suffix for locators (childless Empties)."),
            ("tb003_txt002", "_JNT", "Suffix for armatures (joints)."),
            ("tb003_txt003", "_GEO", "Suffix for meshes."),
            ("tb003_txt004", "_CRV", "Suffix for curves/surfaces."),
            ("tb003_txt005", "_CAM", "Suffix for cameras."),
            ("tb003_txt006", "_LGT", "Suffix for lights."),
        ):
            widget.option_box.menu.add(
                "QLineEdit", setText=default, setObjectName=obj_name, setToolTip=tip,
            )
        widget.option_box.menu.add("Separator", setTitle="Suffix Options")
        widget.option_box.menu.add(
            "QCheckBox", setText="Strip Trailing Padding", setObjectName="tb003_chk004",
            setChecked=True,
            setToolTip="Strip orphaned trailing underscores (and exposed digits) left by suffix removal.",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Strip Trailing Integers", setObjectName="tb003_chk002",
            setChecked=False, setToolTip="Strip trailing integers (e.g. '123' of 'cube123').",
        )
        widget.option_box.menu.add(
            "QCheckBox", setText="Strip Trailing Underscores", setObjectName="tb003_chk003",
            setChecked=False, setToolTip="Strip trailing underscores.",
        )

    def tb003(self, widget):
        """Suffix By Type."""
        objects = self._scope_objects()
        if not objects:
            self.sb.message_box("Nothing in scope.")
            return
        m = widget.option_box.menu
        self.suffix_by_type(
            objects,
            group_suffix=m.tb003_txt000.text(),
            locator_suffix=m.tb003_txt001.text(),
            joint_suffix=m.tb003_txt002.text(),
            mesh_suffix=m.tb003_txt003.text(),
            nurbs_curve_suffix=m.tb003_txt004.text(),
            camera_suffix=m.tb003_txt005.text(),
            light_suffix=m.tb003_txt006.text(),
            strip_trailing_ints=m.tb003_chk002.isChecked(),
            strip_trailing_underscores=m.tb003_chk003.isChecked(),
            strip_trailing_padding=m.tb003_chk004.isChecked(),
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("naming", reload=True)
    ui.show(pos="screen", app_exec=True)
