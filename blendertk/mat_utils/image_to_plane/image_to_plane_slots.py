# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Image to Plane panel — port of mayatk's ``ImageToPlaneSlots``.

Batch-create textured planes from image files: browse → queue → create, plus remove. Engine is
:class:`~blendertk.mat_utils.image_to_plane._image_to_plane.ImageToPlane`.

Divergence: mayatk's affix-mode option menu (Prefix/Suffix/Auto via ``_affix_mode``) is simplified
to inline **auto** resolution (a trailing ``_`` → prefix, else suffix; empty → ``_MAT``); the
Material-Type combo is cosmetic (Blender builds a Principled material either way). ``import bpy`` is
deferred into call bodies and the Qt-only ``uitk`` helper into ``header_init``.
"""
import pythontk as ptk

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

        self.ui.b000.clicked.connect(self._browse_images)
        self.ui.b001.clicked.connect(self._create_planes)
        self.ui.b002.clicked.connect(self._remove_selected)
        self.ui.b004.clicked.connect(self._clear_list)

    def header_init(self, widget):
        """Configure header menu + help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add(
            "QCheckBox", setText="Group Result", setObjectName="chk_group_result",
            setChecked=False,
            setToolTip="Parent all created planes under a single Empty.",
        )
        widget.set_help_text(
            fmt(
                title="Image to Plane",
                body="Creates textured planes from image files — one plane per image, sized to its "
                "aspect ratio, with the image wired into a Principled-BSDF material's Base Color "
                "(and Alpha when present).",
                steps=[
                    "Press <b>Browse…</b> to queue one or more image files.",
                    "Set the <b>Material Affix</b> (default <code>_MAT</code>; a trailing "
                    "<code>_</code> makes it a prefix).",
                    "Set the <b>Plane Height</b> (width is derived from each image's aspect ratio).",
                    "Press <b>Create Planes</b>.",
                ],
                notes=[
                    "<b>Group Result</b> parents all created planes under one Empty.",
                    "<b>Remove Selected</b> deletes planes and their auto-created materials together.",
                ],
            )
        )

    # ------------------------------------------------------------------ images
    def _browse_images(self):
        from qtpy.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(self.ui, "Select Images", "", self.IMAGE_FILTER)
        if not paths:
            return
        existing = {self.ui.lst_files.item(i).text() for i in range(self.ui.lst_files.count())}
        for path in paths:
            if path not in existing:
                self.ui.lst_files.addItem(path)
        self.ui.footer.setText(f"{self.ui.lst_files.count()} image(s) queued.")

    def _clear_list(self):
        self.ui.lst_files.clear()
        self.ui.footer.setText("File list cleared.")

    # ------------------------------------------------------------------ create / remove
    @staticmethod
    def _resolve_affix(text):
        """Auto affix resolution → ``(prefix, suffix)``. A trailing ``_`` (and no leading ``_``)
        makes it a prefix; otherwise a suffix. Empty → default ``_MAT`` suffix."""
        text = (text or "").strip()
        if not text:
            return "", "_MAT"
        if text.endswith("_") and not text.startswith("_"):
            return text, ""
        return "", text

    def _create_planes(self):
        import bpy

        count = self.ui.lst_files.count()
        if count == 0:
            self.ui.footer.setText("No images queued. Use Browse to add files.")
            return
        image_paths = [self.ui.lst_files.item(i).text() for i in range(count)]
        prefix, suffix = self._resolve_affix(self.ui.txt_suffix.text())
        group = self.ui.header.menu.chk_group_result.isChecked()

        try:
            results = ImageToPlane.create(
                image_paths,
                suffix=suffix,
                prefix=prefix,
                plane_height=self.ui.spn_height.value(),
                group=group,
            )
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
            return

        planes = [v for k, v in results.items() if k != "__group__"]
        names = [k for k in results if k != "__group__"]
        label = ", ".join(names[:5]) + (f" … (+{len(names) - 5} more)" if len(names) > 5 else "")
        self.ui.footer.setText(f"Created {len(planes)} plane(s): {label}")

        bpy.ops.object.select_all(action="DESELECT")
        select = [results["__group__"]] if group and "__group__" in results else planes
        for o in select:
            o.select_set(True)
        if select:
            bpy.context.view_layer.objects.active = select[0]

    def _remove_selected(self):
        import bpy

        objects = list(bpy.context.selected_objects or [])
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
