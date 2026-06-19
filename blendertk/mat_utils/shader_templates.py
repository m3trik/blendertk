# !/usr/bin/python
# coding=utf-8
"""Shader Templates tool panel — Switchboard slot wiring for the co-located
``shader_templates.ui``.

Blender counterpart of mayatk's Shader Templates: a **graph save/restore** tool with a user-preset
store, plus quick built-in Principled-BSDF parameter presets. Mirrors Maya's PURPOSE (capture a
shader you like, reuse it later — rebinding fresh textures by map type) without cargo-culting Maya's
``.sfx``/Stingray YAML specifics:

* **Save Selected as Template** captures the active material's node graph (``serialize_material``)
  into the shared ``pythontk.PresetStore`` (JSON, user tier) — recording image **map types**, not
  paths.
* **Create New** rebuilds a template into a fresh material (``restore_material``); for a graph
  template it **rebinds the loaded textures by map type** (Maya's GraphRestorer), then assigns it.
* The fixed built-in presets (Metal / Glass / Emission / Skin …) remain as quick **parameter**
  presets (``create_shader_template`` / ``apply_shader_template``); they and saved graphs share one
  restore path.

The engine lives in ``blendertk.MatUtils`` (``serialize_material`` / ``restore_material`` /
``get_shader_templates`` / ``create_shader_template`` / ``apply_shader_template``); this is the thin
driver. Self-contained (``ptk.LoggingMixin`` + the Qt-free ``ptk.PresetStore``); ``import bpy`` and
the Qt-only ``uitk`` helpers are deferred into the call bodies. Served by ``BlenderUiHandler``
(``marking_menu.show("shader_templates")``).
"""
import pythontk as ptk

import blendertk as btk


class ShaderTemplatesSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Shader Templates panel."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shader_templates
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[shader_templates] ")
        # User-preset store for saved graph templates (Qt-free / bpy-free → safe at init).
        self._store = ptk.PresetStore("shader_templates", package="blendertk")
        self._textures = []  # texture files to rebind by map type on restore (Maya's "Load Textures")
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt001)
        except Exception:
            pass
        self.ui.txt001.setText("Pick a template, then Create New or Apply to Selected.")

    # ------------------------------------------------------------------ header menu
    def header_init(self, widget):
        """Build the header menu (Save / Load Textures / manage) + help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.menu.add("Separator", setTitle="Templates")
        widget.menu.add(
            "QPushButton", setText="Save Selected as Template…",
            setObjectName="btn_save_template",
            setToolTip="Capture the active material's node graph as a reusable template "
            "(image nodes are saved by map type, so Create New can rebind fresh textures).",
        ).clicked.connect(self.save_template)
        widget.menu.add(
            "QPushButton", setText="Load Textures…", setObjectName="btn_load_textures",
            setToolTip="Pick texture files to rebind (by map type) when a graph template is "
            "restored with Create New.",
        ).clicked.connect(self.load_textures)
        widget.menu.add("Separator", setTitle="Manage (saved templates)")
        widget.menu.add(
            "QPushButton", setText="Rename Template…", setObjectName="btn_rename_template",
            setToolTip="Rename the selected saved template.",
        ).clicked.connect(self.rename_template)
        widget.menu.add(
            "QPushButton", setText="Delete Template", setObjectName="btn_delete_template",
            setToolTip="Delete the selected saved template.",
        ).clicked.connect(self.delete_template)
        widget.menu.add(
            "QPushButton", setText="Open Templates Folder", setObjectName="btn_open_templates_dir",
            setToolTip="Reveal the saved-templates folder in the OS file manager.",
        ).clicked.connect(self.open_templates_folder)

        widget.set_help_text(
            fmt(
                title="Shader Templates",
                body="Save a material's shader graph as a reusable template and recreate it later, "
                "rebinding fresh textures by map type. Built-in parameter presets are also listed.",
                steps=[
                    "<b>Save Selected as Template…</b> (header) captures the active material's graph.",
                    "<b>Load Textures…</b> (header) picks textures to rebind on restore.",
                    "Pick a template; <b>Create New</b> rebuilds it and assigns it to the selection.",
                    "<b>Apply to Selected</b> writes a built-in parameter preset onto existing "
                    "materials (graph templates create a new material instead).",
                ],
                sections=[
                    ("Notes", [
                        "Built-in presets are <i>parameter</i> presets (Principled BSDF); saved "
                        "templates are full <i>graphs</i> — both restore through one path.",
                        "Image nodes are saved by <b>map type</b>, not path, so a template re-uses "
                        "on any texture set (Maya's GraphRestorer behavior).",
                    ]),
                ],
            )
        )

    def cmb002_init(self, widget):
        """Populate the template combo: built-in parameter presets + saved graph templates."""
        widget.add(self._all_template_names(), clear=True)

    # ------------------------------------------------------------------ helpers
    def _builtin_names(self):
        return list(btk.get_shader_templates())

    def _all_template_names(self):
        """Built-in parameter presets first, then user-saved graph templates."""
        builtins = self._builtin_names()
        user = [n for n in self._store.list(tier="user") if n not in builtins]
        return builtins + user

    def _template(self):
        return self.ui.cmb002.currentText()

    def _is_builtin(self, name):
        return name in self._builtin_names()

    def _active_material(self):
        """The active object's active material (Save's capture source)."""
        import bpy

        obj = bpy.context.active_object or next(iter(self._selected_objects()), None)
        return getattr(obj, "active_material", None) if obj else None

    def _selected_objects(self):
        return btk.selected_objects()

    def _refresh_combo(self, select=None):
        self.ui.cmb002.add(self._all_template_names(), clear=True)
        if select is not None:
            self.ui.cmb002.setCurrentText(select)

    # ------------------------------------------------------------------ create / apply slots
    def b000(self):
        """Create New — rebuild the template into a fresh material, assigned to the selection."""
        template = self._template()
        if not template:
            self.sb.message_box("Pick a template first.")
            return
        if self._is_builtin(template):
            mat = btk.create_shader_template(template)
        else:
            try:
                data = self._store.load(template)
            except (KeyError, ValueError) as e:
                self.sb.message_box(f"Could not load template <hl>{template}</hl>: {e}")
                return
            mat = btk.restore_material(data, name=template, textures=self._textures)
        objects = [o for o in self._selected_objects() if o.type == "MESH"]
        if objects:
            btk.assign_mat(objects, mat)
            self.logger.info(f"Created '{mat.name}' and assigned to {len(objects)} object(s).")
        else:
            self.logger.info(f"Created material '{mat.name}' (no selection to assign).")

    def b001(self):
        """Apply to Selected — write a built-in parameter preset onto the selection's materials."""
        template = self._template()
        if not self._is_builtin(template):
            self.sb.message_box(
                "Apply to Selected works on the built-in <b>parameter</b> presets. "
                "A saved graph template builds a new material — use <b>Create New</b>."
            )
            return
        mats = []
        for o in self._selected_objects():
            for slot in getattr(o, "material_slots", []):
                if slot.material and slot.material not in mats:
                    mats.append(slot.material)
        if not mats:
            self.sb.message_box("No materials on the selection to apply the template to.")
            return
        applied = sum(1 for m in mats if btk.apply_shader_template(m, template))
        self.logger.info(f"Applied '{template}' to {applied} material(s).")

    # ------------------------------------------------------------------ save / load / manage slots
    def save_template(self):
        """Capture the active material's node graph into the user store."""
        mat = self._active_material()
        if mat is None:
            self.sb.message_box("Select an object with a material to capture.")
            return
        data = btk.serialize_material(mat)
        if not data.get("nodes"):
            self.sb.message_box(f"<hl>{mat.name}</hl> has no node graph to capture.")
            return
        name = self.sb.input_dialog("Save Template", "Template name:", mat.name)
        if not name:
            return
        if self._is_builtin(name):
            self.sb.message_box(f"<hl>{name}</hl> is a built-in preset name — choose another.")
            return
        if self._store.exists(name) and self.sb.message_box(
            f"Overwrite saved template <hl>{name}</hl>?", "Yes", "No"
        ) != "Yes":
            return
        self._store.save(name, data)
        self._refresh_combo(select=name)
        self.logger.info(f"Saved template '{name}' ({len(data['nodes'])} nodes).")

    def load_textures(self):
        """Pick texture files to rebind by map type when a graph template is restored."""
        from qtpy import QtWidgets

        exts = " ".join(f"*.{e}" for e in ptk.ImgUtils.texture_file_types)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None, "Select textures to bind on restore", "", f"Textures ({exts});;All (*)"
        )
        self._textures = list(paths)
        if self._textures:
            self.logger.info(f"Loaded {len(self._textures)} texture(s) to bind on Create New.")

    def rename_template(self):
        """Rename the selected saved template."""
        name = self._template()
        if self._is_builtin(name) or not self._store.exists(name):
            self.sb.message_box("Pick a saved template to rename (built-ins can't be renamed).")
            return
        new = self.sb.input_dialog("Rename Template", "New name:", name)
        if not new or new == name:
            return
        if self._store.rename(name, new):
            self._refresh_combo(select=new)
            self.logger.info(f"Renamed '{name}' → '{new}'.")
        else:
            self.sb.message_box(f"Could not rename to <hl>{new}</hl> (name already in use?).")

    def delete_template(self):
        """Delete the selected saved template."""
        name = self._template()
        if self._is_builtin(name) or not self._store.exists(name):
            self.sb.message_box("Pick a saved template to delete (built-ins can't be deleted).")
            return
        if self.sb.message_box(f"Delete saved template <hl>{name}</hl>?", "Yes", "No") != "Yes":
            return
        if self._store.delete(name):
            self._refresh_combo()
            self.logger.info(f"Deleted template '{name}'.")

    def open_templates_folder(self):
        """Reveal the saved-templates folder in the OS file manager."""
        try:
            ptk.FileUtils.reveal_in_file_manager(str(self._store.user_dir))
        except (FileNotFoundError, OSError):
            # The dir is created lazily on first save — make it so there's something to open.
            self._store.user_dir.mkdir(parents=True, exist_ok=True)
            try:
                ptk.FileUtils.reveal_in_file_manager(str(self._store.user_dir))
            except (FileNotFoundError, OSError) as e:
                self.sb.message_box(str(e))


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("shader_templates", reload=True)
    ui.show(pos="screen", app_exec=True)
