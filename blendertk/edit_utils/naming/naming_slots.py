# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Naming panel — Blender port of mayatk's ``NamingSlots``.

Batch find / rename / convert-case / strip-chars / suffix-by-location / suffix-by-type, each with an
option box (▸). Engine is :class:`~blendertk.edit_utils.naming._naming.Naming`. Mirrors mayatk's
``NamingSlots`` 1:1 — same objectNames, same widget tree, same per-button scoping rules: the header
``Scope`` combo (Selection / All Objects) governs Rename and Convert Case only; Strip Chars, Suffix
by Location, and Suffix by Type always act on the current selection, exactly as in mayatk (this is
mayatk's own behavior, not a Blender-port simplification).

The Qt-only ``uitk`` imports (``Signals``/``fmt``) load with this slots module, which is only
imported in a Qt context (the UI handler / panel open), never by the headless engine path. ``import
bpy`` is deferred into method bodies per blendertk convention.
"""
import pythontk as ptk
from uitk import Signals

import blendertk as btk
from blendertk.edit_utils.naming._naming import Naming


class NamingSlots(Naming, ptk.LoggingMixin):
    """Switchboard slots for the Naming panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.naming
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[naming] ")

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu with tool description and workflow instructions."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.menu.add("Separator", setTitle="Scope")
        widget.menu.add(
            "QComboBox",
            addItems=["Selection", "All Objects"],
            setObjectName="cmb_scope",
            setToolTip=fmt(
                title="Scope",
                bullets=[
                    "<b>Selection</b> — Only the current selection.",
                    "<b>All Objects</b> — All scene objects.",
                ],
            ),
        )

        widget.set_help_text(
            fmt(
                title="Naming",
                body="Batch find, rename, and suffix scene objects. Each "
                "operation button has an option box (▸) for its parameters.",
                sections=[
                    ("Operations", [
                        "<b>Find</b> — select objects by name pattern "
                        "(wildcards or regex; case-sensitivity, Empties-only, "
                        "and regex toggles in the option box).",
                        "<b>Rename</b> — replace matched names with a new "
                        "pattern. Option box: retain existing type suffix.",
                        "<b>Convert Case</b> — upper / lower / title / "
                        "capitalize / swapcase the selected names.",
                        "<b>Strip Chars</b> — remove leading or trailing "
                        "characters.",
                        "<b>Suffix by Location</b> — auto-number objects by "
                        "distance from a reference point (alphabetical or "
                        "integer).",
                        "<b>Suffix by Type</b> — append type-based suffixes "
                        "(<code>_GEO</code>, <code>_GRP</code>, "
                        "<code>_JNT</code>, etc.). Suffix strings are "
                        "editable in the option box.",
                    ]),
                    ("Header menu", [
                        "<b>Scope</b> — <i>Selection</i> (the current "
                        "selection only) or <i>All Objects</i> (the whole "
                        "scene). Applies to every operation.",
                    ]),
                ],
            )
        )

    @property
    def valid_suffixes(self):
        """Get current valid suffixes from tb003 widget fields."""
        try:
            m = self.ui.tb003.option_box.menu
            suffixes = [
                m.tb003_txt000.text(),  # Group
                m.tb003_txt001.text(),  # Locator
                m.tb003_txt002.text(),  # Joint
                m.tb003_txt003.text(),  # Mesh
                m.tb003_txt004.text(),  # Nurbs Curve
                m.tb003_txt005.text(),  # Camera
                m.tb003_txt006.text(),  # Light
                m.tb003_txt007.text(),  # Display Layer (disabled — no Blender analogue, see tb003_init)
            ]
            # Filter out empty strings
            return [s for s in suffixes if s]
        except (AttributeError, RuntimeError):
            # Fallback if widgets not initialized or accessed before tb003 exists
            return ["_GRP", "_LOC", "_JNT", "_GEO", "_CRV", "_CAM", "_LGT", "_LYR"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _scope_objects(self):
        """Objects in scope per the header Scope combo. Used only by Rename and Convert Case,
        mirroring mayatk (Strip Chars / Suffix by Location / Suffix by Type read the selection
        directly, regardless of Scope, exactly as mayatk's do)."""
        import bpy

        use_all = self.ui.header.menu.cmb_scope.currentText() == "All Objects"
        return list(bpy.data.objects) if use_all else btk.selected_objects()

    # ------------------------------------------------------------------
    # Find
    # ------------------------------------------------------------------

    def txt000_init(self, widget):
        """Initialize Find"""
        widget.restore_state = False  # Don't persist the search text across sessions.
        widget.option_box.menu.setTitle("Find")
        # Add clear button to the menu option box
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Case",
            setObjectName="chk000",
            setToolTip="Search case insensitive.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Regular Expression",
            setObjectName="chk001",
            setToolTip="When checked, regular expression syntax is used instead of the default '*' and '|' wildcards.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Empties Only",
            setObjectName="chk007",
            setToolTip="Limit the search to Empty objects (Blender's locator analogue).",
        )
        widget.option_box.set_action(
            callback=widget.returnPressed.emit,
            icon="search",
            tooltip="Find matching objects (same as pressing Enter).",
        )

    @Signals("returnPressed")
    def txt000(self, widget):
        """Find: filter/select scene objects whose name matches the search pattern."""
        import bpy

        # An asterisk denotes startswith*, *endswith, *contains*
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign_case = widget.ui.txt000.option_box.menu.chk000.isChecked()
        empties_only = widget.ui.txt000.option_box.menu.chk007.isChecked()

        text = widget.text()
        if text:
            # Filter objects based on the empties_only option (Blender's locator analogue)
            objects = [o for o in bpy.data.objects if (o.type == "EMPTY" or not empties_only)]
            obj_names = [o.name for o in objects]
            found_names = ptk.find_str(
                text, obj_names, regex=regex, ignore_case=ign_case
            )
            found_objects = [
                o for o, name in zip(objects, obj_names) if name in found_names
            ]

            bpy.ops.object.select_all(action="DESELECT")
            for o in found_objects:
                o.select_set(True)
            if found_objects:
                bpy.context.view_layer.objects.active = found_objects[0]

            # Print user-friendly result
            object_type = "Empties" if empties_only else "objects"
            if found_objects:
                self.ui.footer.setText(
                    f"Found and selected {len(found_objects)} {object_type} matching '{text}'"
                )
            else:
                self.ui.footer.setText(f"No {object_type} found matching '{text}'")

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    def txt001_init(self, widget):
        """Initialize Rename"""
        widget.restore_state = False  # Don't persist the rename text across sessions.
        widget.option_box.menu.setTitle("Rename")
        # Add clear button to the menu option box
        widget.option_box.clear_option = True
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Retain Suffix",
            setObjectName="chk002",
            setToolTip="Retain the suffix of the selected object(s) if it matches one defined in Suffix By Type.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Find",
            setObjectName="chk008",
            setToolTip="Ignore the find field and rename all matched objects.",
        )
        widget.option_box.set_action(
            callback=widget.returnPressed.emit,
            icon="edit",
            tooltip="Rename matched objects (same as pressing Enter).",
        )

    # The LineEdit text parameter is not emitted on `returnPressed`
    @Signals("returnPressed")
    def txt001(self, widget):
        """Rename: rename matched objects (find → replace, with regex / suffix options)."""
        # An asterisk denotes startswith*, *endswith, *contains*
        find = widget.ui.txt000.text()
        to = widget.text()
        regex = widget.ui.txt000.option_box.menu.chk001.isChecked()
        ign_case = widget.ui.txt000.option_box.menu.chk000.isChecked()
        retain_suffix = widget.ui.txt001.option_box.menu.chk002.isChecked()
        ignore_find = widget.ui.txt001.option_box.menu.chk008.isChecked()

        # Get current valid suffixes from property if retain_suffix is enabled
        valid_suffixes = self.valid_suffixes if retain_suffix else None

        objects = self._scope_objects()
        if not objects:
            self.sb.message_box(
                "Nothing selected. Set Scope to 'All Objects' in the header menu to operate on the entire scene."
            )
            return

        # Count objects before rename
        object_count = len(objects)

        self.rename(
            objects,
            to,
            find if not ignore_find else "",
            regex=regex,
            ignore_case=ign_case,
            retain_suffix=retain_suffix,
            valid_suffixes=valid_suffixes,
        )

        # Print user-friendly result
        filter_info = f" matching '{find}'" if find and not ignore_find else ""
        suffix_info = " (with suffix retention)" if retain_suffix else ""
        self.ui.footer.setText(
            f"Renamed {object_count} object(s){filter_info} to pattern '{to}'{suffix_info}"
        )

    # ------------------------------------------------------------------
    # Convert Case
    # ------------------------------------------------------------------

    def tb000_init(self, widget):
        """Initialize Convert Case"""
        widget.option_box.menu.setTitle("Convert Case")
        widget.option_box.menu.add(
            "QComboBox",
            addItems=["capitalize", "upper", "lower", "swapcase", "title"],
            setObjectName="cmb001",
            setToolTip="Set desired python case operator.",
        )

    def tb000(self, widget):
        """Convert Case"""
        case = widget.option_box.menu.cmb001.currentText()

        objects = self._scope_objects()
        if not objects:
            self.sb.message_box(
                "Nothing selected. Set Scope to 'All Objects' in the header menu to operate on the entire scene."
            )
            return
        self.set_case(objects, case)

    # ------------------------------------------------------------------
    # Suffix By Location
    # ------------------------------------------------------------------

    def tb001_init(self, widget):
        """Initialize Suffix By Location"""
        widget.option_box.menu.setTitle("Suffix By Location")
        # Reference point is a choice between two named origins, not a modifier.
        ref = widget.option_box.menu.add(
            "QComboBox",
            setObjectName="cmb_reference",
            setToolTip="Scene Origin: measure from the world origin (0,0,0).\nFirst Object: measure from the first selected object.",
        )
        ref.addItems(["Scene Origin", "First Object"])
        ref.setCurrentText("Scene Origin")  # preserve prior default (checkbox off)
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Alphabetical",
            setObjectName="chk005",
            setToolTip="Use an alphabet character as a suffix when there is less than 26 objects, else use integers.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Integers",
            setObjectName="chk002",
            setChecked=True,
            setToolTip="Strip any trailing integers. ie. '123' of 'cube123'",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Defined Suffixes",
            setObjectName="chk003",
            setChecked=True,
            setToolTip="Strip any suffixes found in the 'Suffix By Type' settings (e.g. '_GRP', '_LOC').",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Independent Groups",
            setObjectName="chk007",
            setToolTip="Group objects by name type and suffix them independently.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Reverse",
            setObjectName="chk004",
            setToolTip="Reverse the naming order. (Farthest object first)",
        )

    def tb001(self, widget):
        """Suffix By Location"""
        first_obj_as_ref = (
            widget.option_box.menu.cmb_reference.currentText() == "First Object"
        )
        alphabetical = widget.option_box.menu.chk005.isChecked()
        strip_trailing_ints = widget.option_box.menu.chk002.isChecked()
        strip_defined_suffixes = widget.option_box.menu.chk003.isChecked()
        reverse = widget.option_box.menu.chk004.isChecked()
        independent_groups = widget.option_box.menu.chk007.isChecked()

        # Mirrors mayatk: reads the selection directly, ignoring the header Scope combo.
        selection = btk.selected_objects()
        self.append_location_based_suffix(
            selection,
            first_obj_as_ref=first_obj_as_ref,
            alphabetical=alphabetical,
            strip_trailing_ints=strip_trailing_ints,
            strip_defined_suffixes=strip_defined_suffixes,
            valid_suffixes=self.valid_suffixes,
            reverse=reverse,
            independent_groups=independent_groups,
        )

    # ------------------------------------------------------------------
    # Strip Chars
    # ------------------------------------------------------------------

    def tb002_init(self, widget):
        """Initialize Strip Chars"""
        widget.option_box.menu.setTitle("Strip Chars")
        widget.option_box.menu.add(
            "QSpinBox",
            setPrefix="Num Chars:",
            setObjectName="s000",
            setValue=1,
            setToolTip="The number of characters to delete.",
        )
        widget.option_box.menu.add(
            "QComboBox",
            addItems=["Leading", "Trailing"],
            setCurrentText="Trailing",
            setObjectName="cmb002",
            setToolTip="Which end of the name to delete characters from.",
        )

    def tb002(self, widget):
        """Strip Chars: remove a number of leading/trailing characters from the selected names."""
        # Mirrors mayatk: reads the selection directly, ignoring the header Scope combo.
        sel = btk.selected_objects()
        kwargs = {
            "num_chars": widget.option_box.menu.s000.value(),
            "trailing": widget.option_box.menu.cmb002.currentText() == "Trailing",
        }
        self.strip_chars(sel, **kwargs)

    # ------------------------------------------------------------------
    # Suffix By Type
    # ------------------------------------------------------------------

    def tb003_init(self, widget):
        """Initialize Suffix By Type"""
        widget.option_box.menu.setTitle("Suffix By Type")
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Group Suffix",
            setText="_GRP",
            setObjectName="tb003_txt000",
            setToolTip="Suffix for transform groups (Empties with children).",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Locator Suffix",
            setText="_LOC",
            setObjectName="tb003_txt001",
            setToolTip="Suffix for locators (childless Empties).",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Joint Suffix",
            setText="_JNT",
            setObjectName="tb003_txt002",
            setToolTip="Suffix for armatures (joints).",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Mesh Suffix",
            setText="_GEO",
            setObjectName="tb003_txt003",
            setToolTip="Suffix for meshes.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Nurbs Curve Suffix",
            setText="_CRV",
            setObjectName="tb003_txt004",
            setToolTip="Suffix for curves/surfaces.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Camera Suffix",
            setText="_CAM",
            setObjectName="tb003_txt005",
            setToolTip="Suffix for cameras.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Light Suffix",
            setText="_LGT",
            setObjectName="tb003_txt006",
            setToolTip="Suffix for lights.",
        )
        # TODO(blender-parity): mayatk's Display Layer suffix has no Blender object-type
        # analogue — a collection is a membership grouping, not a per-object type, so
        # suffix_by_type() has nothing to test against the way it tests MESH/CAMERA/etc.
        # Field kept (disabled) only for structural parity with mayatk's option box; it is
        # not read by tb003() below.
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Display Layer Suffix",
            setText="_LYR",
            setObjectName="tb003_txt007",
            setEnabled=False,
            setToolTip="No Blender equivalent: display layers are Maya-only (a collection is a membership group, not an object type suffix_by_type can assign).",
        )
        widget.option_box.menu.add(
            "Separator",
            setTitle="Suffix Options",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Padding",
            setObjectName="tb003_chk004",
            setChecked=True,
            setToolTip="Strip orphaned trailing underscores and, only when underscores were at the end, also strip exposed trailing digits. Preserves intentional '_02' numbering.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Integers",
            setObjectName="tb003_chk002",
            setChecked=False,
            setToolTip="Strip any trailing integers. ie. '123' of 'cube123'",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Strip Trailing Underscores",
            setObjectName="tb003_chk003",
            setChecked=False,
            setToolTip="Strip any trailing underscores after stripping integers (e.g. 'cube_01_' -> 'cube').",
        )

    def tb003(self, widget):
        """Suffix By Type"""
        # Mirrors mayatk: reads the selection directly, ignoring the header Scope combo.
        objects = btk.selected_objects()

        kwargs = {
            "group_suffix": widget.option_box.menu.tb003_txt000.text(),
            "locator_suffix": widget.option_box.menu.tb003_txt001.text(),
            "joint_suffix": widget.option_box.menu.tb003_txt002.text(),
            "mesh_suffix": widget.option_box.menu.tb003_txt003.text(),
            "nurbs_curve_suffix": widget.option_box.menu.tb003_txt004.text(),
            "camera_suffix": widget.option_box.menu.tb003_txt005.text(),
            "light_suffix": widget.option_box.menu.tb003_txt006.text(),
            # tb003_txt007 (Display Layer) intentionally excluded — see TODO(blender-parity)
            # above; the engine has no display_layer_suffix parameter to receive it.
            "strip_trailing_ints": widget.option_box.menu.tb003_chk002.isChecked(),
            "strip_trailing_underscores": widget.option_box.menu.tb003_chk003.isChecked(),
            "strip_trailing_padding": widget.option_box.menu.tb003_chk004.isChecked(),
        }
        self.suffix_by_type(objects, **kwargs)


# --------------------------------------------------------------------------------------------


if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("naming", reload=True)
    ui.show(pos="screen", app_exec=True)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
