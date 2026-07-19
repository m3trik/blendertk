# !/usr/bin/python
# coding=utf-8
"""Shader Templates tool panel — Switchboard slot wiring for the co-located
``shader_templates.ui``.

Blender counterpart of mayatk's Shader Templates, mirroring its structure 1:1 (same objectNames,
same header-menu layout, same method shapes: ``b000``/``b001``/``b002``, ``lbl000``-``lbl002`` on
the combo's own submenu, ``lbl_open_templates_dir``/``lbl_graph_material`` on the header) so the
two panels are a true mirror of each other:

* **Save Template** (``b002``) captures the active material's node graph (``btk.serialize_material``
  — the Blender analogue of mayatk's ``GraphSaver``) under the fixed working name (``template_name``,
  same "test" placeholder mayatk uses); rename it via the combo's own **Rename** submenu item
  immediately after, exactly like the Maya panel's workflow.
* **Create Network** (``b000``) rebuilds the selected template into a fresh material
  (``btk.restore_material`` — the analogue of mayatk's ``GraphRestorer``), rebinding any loaded
  textures by map type, then assigns it to the current mesh selection (Blender materials are
  meaningless with no object to carry them, unlike a bare Maya shading network — the one place this
  port knowingly diverges from Maya's literal "just create nodes" behavior).
* **Load Texture Maps** (``b001``) picks texture files to rebind by map type on the next Create
  Network.

Saved templates live in the shared ``pythontk.PresetStore`` (JSON, user tier) rather than mayatk's
raw YAML-file-per-template directory — the same underlying *workflow* (pick from a combo; rename /
delete / open-file live on the combo's own submenu), backed by the ecosystem's shared preset
abstraction instead of hand-rolled file I/O.

Dropped relative to blendertk's previous (non-mirrored) version of this panel: the built-in
Principled-BSDF parameter presets (Metal / Glass / Emission / Skin …) and the "Apply to Selected"
button that wrote them onto an *existing* material in place. mayatk's Shader Templates has no
built-in-preset concept at all — only user-saved graph templates — so the verbatim structural
mirror has no button left for that in-place-apply workflow. The engine functions themselves
(``btk.get_shader_templates`` / ``create_shader_template`` / ``apply_shader_template``) are
untouched and still covered by ``test/test_mat_anim_utils.py``; they're simply no longer wired into
this particular panel.

The engine lives in ``blendertk.MatUtils`` (``serialize_material`` / ``restore_material`` /
``graph_materials``; in ``_mat_utils.py``); this is the thin driver. Self-contained
(``ptk.LoggingMixin`` + the Qt-free ``ptk.PresetStore``); ``import bpy`` and the Qt-only ``uitk``
helpers are deferred into the call bodies. Served by ``BlenderUiHandler``
(``marking_menu.show("shader_templates")``).
"""
import pythontk as ptk

import blendertk as btk


class ShaderTemplatesSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Shader Templates panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shader_templates

        self.image_files = None  # texture files loaded via "Load Texture Maps"
        self.last_restored_material = None  # analogue of mayatk's `last_restored_nodes`

        # User-preset store for saved graph templates (Qt-free / bpy-free -> safe at init); the
        # Blender analogue of mayatk's raw `templates/` YAML directory.
        self._store = ptk.PresetStore("shader_templates", package="blendertk")

        self.set_log_level(log_level)
        self.logger.hide_logger_name(True)
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt001)
        except Exception:
            pass
        self.ui.txt001.setText("Pick a template, then Create Network.")

    # Mirror of mayatk's `EnvUtils.get_env_info("workspace_dir")` / "sourceimages" — both route
    # through the current-workspace resolver (marked workspace.mel projects get their real
    # sourceImages rule; plain Blender projects keep the "textures" convention).
    # Resolved lazily: needs bpy (so panel load stays bpy-free) and tracks the current file.
    @property
    def workspace_dir(self) -> str:
        return btk.get_env_info("workspace")

    @property
    def source_images_dir(self) -> str:
        return btk.source_images_dir()

        # No Blender analogue of mayatk's `EnvUtils.load_plugin("shaderFXPlugin"/"mtoa")` — the
        # Principled BSDF shader is always available; there is no render-plugin load step.

    @property
    def template_name(self):
        return "test"

    # ------------------------------------------------------------------ header menu
    def header_init(self, widget):
        """Initialize the header widget."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.setTitle("Shader Templates")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_open_templates_dir",
            setText="Open Templates Directory",
            setToolTip="Open the directory containing shader templates.",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Graph Material",
            setToolTip="Open the last restored material in the Shader Editor.",
        )
        widget.set_help_text(
            fmt(
                title="Shader Templates",
                body="Save and restore shader networks as reusable templates. Templates live "
                "in blendertk's shared user-preset store.",
                steps=[
                    "Select an object with a material in the scene to capture its full "
                    "network.",
                    "Press <b>Save Template</b> to write the current network out under a new "
                    "name.",
                    "To restore, pick a template from the combo and press "
                    "<b>Create Network</b>.",
                ],
                sections=[
                    ("Menu options", [
                        "<b>Open Templates Directory</b> — reveal the templates folder in the "
                        "OS file manager.",
                        "<b>Graph Material</b> — open the most recently restored material in "
                        "the Shader Editor.",
                    ]),
                ],
            )
        )

    def lbl_graph_material(self):
        """Open the last restored material in the Shader Editor."""
        if self.last_restored_material is not None:
            btk.graph_materials(self.last_restored_material)
        else:
            self.logger.warning("No material has been restored yet.")

    def lbl_open_templates_dir(self):
        """Open the shader templates directory in the OS file manager."""
        ptk.open_explorer(str(self._store.user_dir), create_dir=True)

    # ------------------------------------------------------------------ template combo
    def cmb002_init(self, widget):
        """Initialize the ComboBox for shader templates."""
        if not widget.is_initialized:
            widget.restore_state = True
            widget.refresh_on_show = True
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl000",
                setText="Rename",
                setToolTip="Rename the current template.",
            )
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl001",
                setText="Delete",
                setToolTip="Delete the current template.",
            )
            widget.on_editing_finished.connect(
                lambda text: self.rename_template_safe(widget, text)
            )
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl002",
                setText="Open Template File",
                setToolTip="Open the selected template file in the default editor.",
            )
        self.refresh_templates(widget)

    def refresh_templates(self, widget):
        """Refresh the list of templates."""
        self._store.user_dir.mkdir(parents=True, exist_ok=True)
        widget.clear()
        for name in self._store.list(tier="user"):
            widget.addItem(name)

    def rename_template_safe(self, widget, new_name):
        """Safe rename that checks for None."""
        current = widget.currentText()
        if not current:
            self.logger.error("No template selected or data is missing.")
            return
        if self._store.rename(current, new_name):
            self.logger.info(f"Template renamed to: {new_name}")
            widget.init_slot()
        else:
            self.logger.error("Could not rename (name already in use?).")

    def lbl000(self):
        """Set the ComboBox as editable to allow renaming."""
        self.ui.cmb002.setEditable(True)
        self.ui.cmb002.menu.hide()

    def lbl001(self):
        """Delete the selected template."""
        template = self.ui.cmb002.currentText()
        if self._store.delete(template):
            self.logger.info(f"Template deleted: {template}")
        self.ui.cmb002.init_slot()

    def lbl002(self):
        """Open the selected template in the default editor."""
        template = self.ui.cmb002.currentText()
        ptk.open_explorer(str(self._store.path(template, "user")))

    # ------------------------------------------------------------------ buttons
    def b000(self):
        """Create shader network using selected template."""
        self.ui.txt001.clear()
        self.logger.info("Creating network based on template...")

        template = self.ui.cmb002.currentText()
        if not template:
            self.logger.error("No template selected.")
            return

        try:
            data = self._store.load(template)
        except (KeyError, ValueError) as e:
            self.logger.error(f"Could not load template '{template}': {e}")
            return

        mat = btk.restore_material(data, name=template, textures=self.image_files or [])
        self.last_restored_material = mat

        # Blender materials need an object to carry them (unlike a bare Maya shading network) —
        # assign to the current mesh selection when there is one.
        objects = [o for o in self._selected_objects() if o.type == "MESH"]
        if objects:
            btk.assign_mat(objects, mat)

        self.logger.info("COMPLETED.")

    def b001(self):
        """Load texture maps and update GUI."""
        image_files = self.sb.file_dialog(
            file_types=[f"*.{ext}" for ext in ptk.ImgUtils.texture_file_types],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
        )

        if image_files:
            self.image_files = image_files
            self.ui.txt001.clear()
            for img in image_files:
                self.logger.info(ptk.truncate(img, 60))

    def b002(self):
        """Save current graph as a new shader template."""
        if self._store.exists(self.template_name):
            self.logger.error("File already exists.")
            return

        mat = self._active_material()
        data = btk.serialize_material(mat)
        if not data.get("nodes"):
            self.logger.warning("No material selected or provided for template saving.")
            return

        self._store.save(self.template_name, data)
        self.logger.info(f"Shader template saved as: {self.template_name}")
        self.ui.cmb002.init_slot()

    # ------------------------------------------------------------------ helpers
    def _active_material(self):
        """The active object's active material (Save's capture source)."""
        obj = btk.active_object() or next(iter(self._selected_objects()), None)
        return getattr(obj, "active_material", None) if obj else None

    def _selected_objects(self):
        return btk.selected_objects()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("shader_templates", reload=True)
    ui.show(pos="screen", app_exec=True)
