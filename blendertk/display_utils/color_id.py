# !/usr/bin/python
# coding=utf-8
"""Color ID tool panel — Switchboard slot wiring for the co-located ``color_id.ui``.

Blender port of mayatk's Color ID: a swatch palette that color-codes scene objects across
channels. Maya's four channels (material / outliner / wireframe / vertex) map to three working
Blender channels — **Material** (an ID material's base color), **Object Color** (``obj.color``,
Blender's per-object viewport tint shown in Object-color shading; the analogue of Maya's
*Outliner* channel, ``chk013``), and **Vertex** (a mesh color attribute, ``chk015``). Maya's
*Wireframe* channel (``chk012``, an ``overrideColorRGB`` draw-override) has no Blender
analogue — the checkbox stays in the ``.ui`` for structural parity but is disabled; see the
``# TODO(blender-parity)`` note below. Apply to / select by / reset across any combination of
the enabled channels; save/restore swatch palettes as presets.

The engine (``ColorId``) lives next to its panel + ``.ui`` (mirror of mayatk's
``display_utils.color_id``); served by ``BlenderUiHandler`` (``marking_menu.show
("color_id")``). Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helpers are
deferred into the methods that use them (headless Blender ships no Qt binding). ``import bpy``
is deferred too.
"""
import random
from typing import List, Optional, Sequence, Tuple

import pythontk as ptk

import blendertk as btk

Color = Tuple[float, float, float]


class ColorId:
    """Engine: apply / select-by / reset object colors across material, object-color, and vertex
    channels. Operates on ``bpy.types.Object`` references (Blender idiom), not name strings."""

    # Desaturated defaults so swatches aren't all white on first launch (mirrors mayatk).
    DEFAULT_SWATCH_COLORS = [
        (180, 120, 120), (180, 150, 120), (180, 180, 120), (120, 180, 120),
        (120, 180, 160), (120, 180, 180), (120, 150, 180), (120, 120, 180),
        (150, 120, 180), (180, 120, 180), (180, 120, 150), (160, 160, 160),
    ]

    # ── apply ──────────────────────────────────────────────────────────────
    @staticmethod
    def assign_id_material(obj, color: Color):
        """Assign an ID material named ``ID_<HEX>`` with ``color`` as its base color to ``obj``
        (created once, reused thereafter). Replaces the object's material slots — this is a flat
        ID-color pass, so the whole object takes the one color (mirrors Maya's assign-by-color)."""
        import bpy

        name = "ID_" + "".join(f"{int(max(0.0, min(1.0, c)) * 255):02X}" for c in color)
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        rgba = (color[0], color[1], color[2], 1.0)
        mat.diffuse_color = rgba  # viewport (Solid / Material-preview flat)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf and "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = rgba
        if hasattr(obj.data, "materials"):  # mesh/curve/surface/text/meta hold material slots
            obj.data.materials.clear()
            obj.data.materials.append(mat)
        return mat

    @staticmethod
    def set_object_color(obj, color: Color):
        """Set the object's viewport display color (``obj.color`` — Object-color shading)."""
        obj.color = (color[0], color[1], color[2], 1.0)

    @staticmethod
    def set_vertex_color(obj, color: Color, name: str = "Color"):
        """Write ``color`` to every corner of a mesh color attribute (created/reused, set active)."""
        if obj.type != "MESH":
            return
        mesh = obj.data
        attr = mesh.color_attributes.get(name)
        if attr is None:
            attr = mesh.color_attributes.new(name=name, type="BYTE_COLOR", domain="CORNER")
        rgba = (color[0], color[1], color[2], 1.0)
        for d in attr.data:
            d.color = rgba
        try:
            mesh.color_attributes.active_color = attr
        except Exception:
            pass
        mesh.update()

    @classmethod
    def apply_color(
        cls,
        objects: Sequence,
        color: Optional[Color] = None,
        apply_to_material: bool = False,
        apply_to_object: bool = False,
        apply_to_vertex: bool = False,
    ) -> None:
        """Apply ``color`` (random when None) to each object across the enabled channels."""
        if color is None:
            color = (random.random(), random.random(), random.random())
        for obj in objects:
            if obj is None:
                continue
            if apply_to_object:
                cls.set_object_color(obj, color)
            if apply_to_vertex:
                cls.set_vertex_color(obj, color)
            if apply_to_material:
                cls.assign_id_material(obj, color)

    # ── read ───────────────────────────────────────────────────────────────
    @staticmethod
    def get_object_color(obj) -> Optional[Color]:
        """The object's viewport display color (``obj.color`` RGB), or None."""
        c = getattr(obj, "color", None)
        return (c[0], c[1], c[2]) if c is not None else None

    @staticmethod
    def get_material_color(obj) -> Optional[Color]:
        """Base color of the object's active material (Principled base, else diffuse), or None."""
        mat = getattr(obj, "active_material", None)
        if mat is None:
            return None
        if mat.use_nodes:
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf and "Base Color" in bsdf.inputs:
                v = bsdf.inputs["Base Color"].default_value
                return (v[0], v[1], v[2])
        d = mat.diffuse_color
        return (d[0], d[1], d[2])

    @staticmethod
    def get_average_vertex_color(obj) -> Optional[Color]:
        """Average of the active mesh color attribute, or None when there is none."""
        if obj.type != "MESH":
            return None
        attr = obj.data.color_attributes.active_color
        if attr is None or not len(attr.data):
            return None
        n = len(attr.data)
        acc = [0.0, 0.0, 0.0]
        for d in attr.data:
            acc[0] += d.color[0]
            acc[1] += d.color[1]
            acc[2] += d.color[2]
        return (acc[0] / n, acc[1] / n, acc[2] / n)

    @staticmethod
    def color_difference(c1: Color, c2: Color) -> float:
        """Average absolute per-channel RGB difference."""
        return sum(abs(a - b) for a, b in zip(c1, c2)) / 3.0

    @classmethod
    def get_objects_by_color(
        cls,
        target_color: Color,
        threshold: float = 0.1,
        check_material: bool = False,
        check_object: bool = False,
        check_vertex: bool = False,
    ) -> List:
        """View-layer mesh objects whose color (on any enabled channel) is within ``threshold``.

        Iterates ``view_layer.objects`` (not ``scene.objects``) so every match is selectable —
        an object in a view-layer-excluded collection can't be selected, so the caller's
        ``select_set`` would otherwise raise on it."""
        import bpy

        out = []
        for obj in bpy.context.view_layer.objects:
            if obj.type != "MESH":
                continue
            matched = False
            if check_material and not matched:
                c = cls.get_material_color(obj)
                matched = c is not None and cls.color_difference(c, target_color) <= threshold
            if check_object and not matched:
                c = cls.get_object_color(obj)
                matched = c is not None and cls.color_difference(c, target_color) <= threshold
            if check_vertex and not matched:
                c = cls.get_average_vertex_color(obj)
                matched = c is not None and cls.color_difference(c, target_color) <= threshold
            if matched:
                out.append(obj)
        return out

    # ── reset ──────────────────────────────────────────────────────────────
    @classmethod
    def reset_colors(
        cls,
        objects: Sequence,
        reset_material: bool = True,
        reset_object: bool = True,
        reset_vertex: bool = True,
    ) -> None:
        """Clear color assignments on ``objects`` for the chosen channels."""
        for obj in objects:
            if obj is None:
                continue
            if reset_object:
                obj.color = (1.0, 1.0, 1.0, 1.0)
            if reset_material and hasattr(obj.data, "materials"):
                # Drop ID materials this tool assigned; leave any user materials in place.
                for i in range(len(obj.data.materials) - 1, -1, -1):
                    mat = obj.data.materials[i]
                    if mat is not None and mat.name.startswith("ID_"):
                        obj.data.materials.pop(index=i)
            if reset_vertex:
                cls.reset_vertex_colors(obj)

    @staticmethod
    def reset_vertex_colors(obj) -> None:
        """Remove every color attribute from a mesh object."""
        if obj.type != "MESH":
            return
        mesh = obj.data
        for attr in list(mesh.color_attributes):
            try:
                mesh.color_attributes.remove(attr)
            except (RuntimeError, ReferenceError):
                pass
        mesh.update()


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class ColorIdSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Color ID panel (swatch palette + channels + presets).

    Channel checkboxes: ``chk013`` Object Color · ``chk014`` Material · ``chk015`` Vertex.
    ``chk012`` Wireframe ships in the ``.ui`` (structural parity with mayatk) but is disabled —
    see the ``# TODO(blender-parity)`` note on :meth:`_channels`. Self-contained
    (``ptk.LoggingMixin`` only)."""

    # Storage key mirrors mayatk's own preset dir shape ("<pkg>/<tool>"); the swatch-preset
    # mechanism (uitk's PresetManager) is DCC-agnostic — colors live in Qt widgets, not bpy.
    _PRESET_DIR = "blendertk/color_id"
    _DEFAULT_PRESET = "default"

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.color_id
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[color_id] ")

        self.button_grp = self.sb.create_button_groups(self.ui, "chk000-11")
        # Note: mayatk's __init__ also migrates away from a legacy bug where colorSwatch
        # loadColor fell back to "#ffffff" and auto-saved it over every swatch. Blender's
        # swatches never had that bug, so there is nothing to migrate here.
        for i, button in enumerate(self.button_grp.buttons()):
            button._initialColor = self.sb.QtGui.QColor(
                *ColorId.DEFAULT_SWATCH_COLORS[i % len(ColorId.DEFAULT_SWATCH_COLORS)]
            )
            button.keep_square = True  # square swatches that track column width
            button.settings = self.ui.settings
        self.ui.chk000.setChecked(True)

    # ── Preset I/O ─────────────────────────────────────────────────────────

    def _export_swatch_colors(self) -> dict:
        """``PresetManager.metadata_provider`` — capture current swatch colors."""
        return {"swatches": [btn.color.name() for btn in self.button_grp.buttons()]}

    def _import_swatch_colors(self, meta: dict) -> None:
        """``PresetManager.on_metadata_loaded`` — apply colors from a preset."""
        colors = (meta or {}).get("swatches") or []
        for btn, hex_color in zip(self.button_grp.buttons(), colors):
            btn.color = self.sb.QtGui.QColor(hex_color)

    @staticmethod
    def _hex_from_rgb(rgb) -> str:
        r, g, b = rgb
        return f"#{int(r):02X}{int(g):02X}{int(b):02X}"

    def _ensure_default_preset(self, presets) -> None:
        """Write the factory-default preset on first use if it's missing."""
        if presets.exists(self._DEFAULT_PRESET):
            return
        original = presets.metadata_provider
        presets.metadata_provider = lambda: {
            "swatches": [self._hex_from_rgb(rgb) for rgb in ColorId.DEFAULT_SWATCH_COLORS]
        }
        try:
            presets.save(self._DEFAULT_PRESET)
        finally:
            presets.metadata_provider = original

    def header_init(self, widget):
        """Configure header help text and preset combobox."""
        from uitk.widgets.mixins.tooltip_mixin import fmt, kbd

        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.set_help_text(
            fmt(
                title="Color ID",
                body="Color-code scene objects across three channels: an ID "
                "<b>Material</b>, the <b>Object Color</b> (viewport tint), and "
                "<b>Vertex</b> colors.",
                steps=[
                    "Click a palette swatch to pick the active color (right-click a "
                    "swatch to change its color).",
                    "Enable the channels to apply via the <b>Object Color</b> / "
                    "<b>Material</b> / <b>Vertex</b> checkboxes.",
                    "Select objects and press <b>Set Color</b>.",
                    "Use <b>Select By Color</b> to find objects matching the active "
                    "color across the enabled channels.",
                ],
                sections=[
                    ("Notes", [
                        f"<b>Reset</b> clears assignments on the selection (or every "
                        f"object with {kbd('Ctrl')}-click).",
                        "Object Color shows in the viewport's <b>Object</b> color "
                        "shading mode (Solid display ▸ Color ▸ Object).",
                        "<b>Material</b> assigns a flat ID material (replaces the "
                        "object's material slots).",
                        "<b>Wireframe</b> is disabled — Blender has no per-object "
                        "wireframe-color override to mirror Maya's channel.",
                    ]),
                    ("Presets", [
                        "The header menu's preset combo saves / restores swatch "
                        "palettes. Use <b>Save</b> to capture the current colors; "
                        "pick a preset to restore them.",
                    ]),
                ],
            )
        )
        # Preset combobox — swatches aren't standard widgets, so colors are carried in
        # metadata rather than per-widget value reads.
        widget.menu.add_presets = True
        widget.menu.presets.preset_dir = self._PRESET_DIR
        widget.menu.presets.metadata_provider = self._export_swatch_colors
        widget.menu.presets.on_metadata_loaded = self._import_swatch_colors
        self._ensure_default_preset(widget.menu.presets)

    # ── helpers ──────────────────────────────────────────────────────────────
    @property
    def selected_objects(self) -> List:
        """Return the currently selected objects, or an empty list if none are selected."""
        objects = btk.selected_objects()
        if not objects:
            self.sb.message_box("No objects selected.")
        return objects

    @property
    def selected_button(self):
        """Return the currently checked swatch button in the palette group."""
        for button in self.button_grp.buttons():
            if button.isChecked():
                return button
        return None

    @property
    def target_color(self) -> Optional[Color]:
        """Return the color of the selected swatch, or None if no swatch is selected."""
        button = self.selected_button
        if not button:
            return None
        color = button.color
        if isinstance(color, self.sb.QtGui.QColor):
            return (color.redF(), color.greenF(), color.blueF())
        # already an (r, g, b) 0-1 tuple
        return tuple(color[:3])

    def _channels(self) -> dict:
        # TODO(blender-parity): mayatk's chk012 (Wireframe — an overrideColorRGB draw-override)
        # has no Blender analogue; Object Color (chk013) is the closest per-object color tag, so
        # it stands in for both Maya's Outliner and Wireframe channels. chk012 stays in the .ui,
        # disabled, for structural parity — there is nothing to wire it to here.
        return {
            "object": self.ui.chk013.isChecked(),
            "material": self.ui.chk014.isChecked(),
            "vertex": self.ui.chk015.isChecked(),
        }

    # ── buttons ──────────────────────────────────────────────────────────────
    def b000(self) -> None:
        """Reset Colors (Ctrl+click resets every object in the scene)."""
        import bpy

        if self.sb.app.keyboardModifiers() == self.sb.QtCore.Qt.ControlModifier:
            objects = list(bpy.context.scene.objects)
        else:
            objects = self.selected_objects
        if not objects:
            return
        ColorId.reset_colors(objects)

    def b001(self) -> None:
        """Set Color — apply the active color to the selected objects on the enabled channels."""
        objects = self.selected_objects
        color = self.target_color
        if not objects or color is None:
            return
        ch = self._channels()
        ColorId.apply_color(
            objects,
            color=color,
            apply_to_object=ch["object"],
            apply_to_material=ch["material"],
            apply_to_vertex=ch["vertex"],
        )

    def b002(self) -> None:
        """Select By Color — select scene objects matching the active color (enabled channels)."""
        import bpy

        color = self.target_color
        if color is None:
            return
        ch = self._channels()
        found = ColorId.get_objects_by_color(
            color,
            check_object=ch["object"],
            check_material=ch["material"],
            check_vertex=ch["vertex"],
        )
        # Direct select_set (not bpy.ops.object.select_all) so Select-By-Color works in any
        # mode — the object operator poll-fails in edit mode (Maya's selects anywhere).
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj in found)
        bpy.context.view_layer.objects.active = found[0] if found else None

    def b003(self) -> None:
        """Get Color — read the active object's color into the selected swatch.

        Reads whichever enabled channel has a color (Object Color → Material → Vertex).
        (Mayatk's b003 is a fixed wireframe-color eyedropper — no Blender analogue for that
        channel, so this reads the active object's color from the enabled channels instead.)"""
        import bpy

        obj = bpy.context.view_layer.objects.active
        button = self.selected_button
        if obj is None or button is None:
            self.sb.message_box("Select an object and a swatch first.")
            return
        ch = self._channels()
        color = None
        if ch["object"]:
            color = ColorId.get_object_color(obj)
        if color is None and ch["material"]:
            color = ColorId.get_material_color(obj)
        if color is None and ch["vertex"]:
            color = ColorId.get_average_vertex_color(obj)
        if color is None:
            self.sb.message_box("No color found on the active object's enabled channels.")
            return
        button.color = self.sb.QtGui.QColor(
            int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("color_id", reload=True)
    ui.show(pos="screen", app_exec=True)
