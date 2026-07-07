# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Image to Plane UI — port of mayatk's ``ImageToPlaneSlots``.

Provides ``ImageToPlaneSlots`` — a standalone panel for batch-creating textured planes from image
files in Blender. Structurally mirrors mayatk's slot 1:1 (same objectNames, same method names, same
signal-connection order, same ``header_init``/``txt_suffix_init`` sections); the affix-mode picker is
the shared ``uitk`` option-box helper (``option_box.set_affix`` / ``option_box.affix_mode`` /
``option_box.resolve_affix``), so both toolkits wire it identically with no duplicated code.

Divergence: ``cmb_mat_type`` (Stingray PBS / Standard Surface) is disabled — Blender has a single
Principled BSDF shader, so there is no material-type choice to make (see the ``.ui`` and the
``TODO(blender-parity)`` note below). Engine is
:class:`~blendertk.mat_utils.image_to_plane._image_to_plane.ImageToPlane`. ``import bpy`` is
deferred into call bodies and the Qt-only ``uitk`` helper into ``header_init``.

Layout
------
- **Header**: Title bar.
- **Images**: Browse / file list / clear.
- **Settings**: Material type (disabled), suffix, plane height.
- **Create**: Main action button.
- **Manage**: Remove selected planes.
- **Footer**: Status messages.
"""
import pythontk as ptk

from blendertk.core_utils._core_utils import selected_objects, undoable
from blendertk.mat_utils.image_to_plane._image_to_plane import ImageToPlane


class ImageToPlaneSlots(ptk.LoggingMixin):
    """Switchboard slots for the Image to Plane panel."""

    IMAGE_FILTER = (
        "Image Files (*.png *.jpg *.jpeg *.tga *.bmp *.tif *.tiff *.exr *.hdr);;All Files (*.*)"
    )

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.image_to_plane
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[image_to_plane] ")

        # Wire plain QPushButton widgets
        self.ui.b000.clicked.connect(self._browse_images)
        self.ui.b001.clicked.connect(self._create_planes)
        self.ui.b002.clicked.connect(self._remove_selected)
        self.ui.b004.clicked.connect(self._clear_list)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add(
            "QCheckBox",
            setText="Group Result",
            setObjectName="chk_group_result",
            setChecked=False,
            setToolTip="Parent all created planes under a single Empty.",
        )

        widget.set_help_text(
            fmt(
                title="Image to Plane",
                body="Creates textured planes from image files — one "
                "plane per image, sized to its aspect ratio and assigned a "
                "Principled-BSDF material with the image as Base Color (and "
                "Alpha when present).",
                steps=[
                    "Press <b>Browse…</b> to select one or more image files.",
                    "Set the <b>Material Affix</b> (default <code>_MAT</code>). "
                    "The affix option box selects Suffix / Prefix / Auto.",
                    "Set the <b>Plane Height</b> in scene units (width is "
                    "derived from each image's aspect ratio).",
                    "Press <b>Create Planes</b>.",
                ],
                sections=[
                    ("Menu options", [
                        "<b>Group Result</b> — parent all created planes under "
                        "a single Empty.",
                    ]),
                ],
                notes=[
                    "Use <b>Remove Selected</b> to delete planes and their "
                    "auto-created materials together.",
                ],
            )
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def txt_suffix_init(self, widget):
        """Add a prefix/suffix/auto-mode picker to the affix field."""
        widget.option_box.set_affix(
            default="auto",
            on_change=lambda _mode, w=widget: self._apply_affix_placeholder(w),
        )
        self._apply_affix_placeholder(widget)

    @staticmethod
    def _apply_affix_placeholder(widget):
        mode = widget.option_box.affix_mode
        if mode == "prefix":
            widget.setPlaceholderText("Material Prefix")
            widget.setToolTip(
                'Prefix prepended to the image name for material naming.\n'
                'Example: image "brick" with prefix "MAT_" → material "MAT_brick".'
            )
        elif mode == "suffix":
            widget.setPlaceholderText("Material Suffix")
            widget.setToolTip(
                'Suffix appended to the image name for material naming.\n'
                'Example: image "brick" with suffix "_MAT" → material "brick_MAT".'
            )
        else:  # auto
            widget.setPlaceholderText("Material Affix")
            widget.setToolTip(
                "Material affix — placement inferred from '_' position.\n"
                "  '_MAT' → suffix (appended)\n"
                "  'MAT_' → prefix (prepended)"
            )

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def _browse_images(self):
        """Open a file dialog and populate the file list."""
        from qtpy.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self.ui,
            "Select Images",
            "",
            self.IMAGE_FILTER,
        )
        if not paths:
            return

        for path in paths:
            # Avoid duplicates
            existing = [
                self.ui.lst_files.item(i).text()
                for i in range(self.ui.lst_files.count())
            ]
            if path not in existing:
                self.ui.lst_files.addItem(path)

        self.ui.footer.setText(f"{self.ui.lst_files.count()} image(s) queued.")

    def _clear_list(self):
        """Clear the file list."""
        self.ui.lst_files.clear()
        self.ui.footer.setText("File list cleared.")

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @undoable
    def _create_planes(self):
        """Create textured planes from the queued images."""
        import bpy

        count = self.ui.lst_files.count()
        if count == 0:
            self.ui.footer.setText("No images queued. Use Browse to add files.")
            return

        image_paths = [self.ui.lst_files.item(i).text() for i in range(count)]

        # TODO(blender-parity): cmb_mat_type (Stingray PBS / Standard Surface) has no Blender
        # analogue — there is a single Principled BSDF shader. Disabled in the .ui; the engine
        # always builds a Principled material regardless of this widget.
        prefix, suffix = self.ui.txt_suffix.option_box.resolve_affix(default="suffix")
        if not prefix and not suffix:
            # Empty field — apply mode-aware default so a user who switched
            # to Prefix mode and cleared the text still gets a sensible value.
            if self.ui.txt_suffix.option_box.affix_mode == "prefix":
                prefix = "MAT_"
            else:
                suffix = "_MAT"
        plane_height = self.ui.spn_height.value()

        group = self.ui.header.menu.chk_group_result.isChecked()

        try:
            results = ImageToPlane.create(
                image_paths,
                suffix=suffix,
                prefix=prefix,
                plane_height=plane_height,
                group=group,
            )
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
            return

        planes = [v for k, v in results.items() if k != "__group__"]
        names = [k for k in results if k != "__group__"]
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" … (+{len(names) - 5} more)"

        self.ui.footer.setText(f"Created {len(planes)} plane(s): {label}")

        # Select the group (if created) or the individual planes
        bpy.ops.object.select_all(action="DESELECT")
        select = [results["__group__"]] if group and "__group__" in results else planes
        for o in select:
            o.select_set(True)
        if select:
            bpy.context.view_layer.objects.active = select[0]

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @undoable
    def _remove_selected(self):
        """Remove selected planes and their associated materials."""
        objects = selected_objects()
        if not objects:
            self.ui.footer.setText("Select planes to remove.")
            return

        try:
            removed = ImageToPlane.remove(objects)
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
            return

        self.ui.footer.setText(f"Removed {removed} plane(s).")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("image_to_plane", reload=True)
    ui.show(pos="screen", app_exec=True)
