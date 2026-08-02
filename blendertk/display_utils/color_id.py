# !/usr/bin/python
# coding=utf-8
"""Color ID tool panel — Switchboard slot wiring for the co-located ``color_id.ui``.

Blender port of mayatk's Color ID: a swatch palette that color-codes scene objects across
channels. Maya's four channels (material / outliner / wireframe / vertex) map onto Blender as:

- **Material** (``chk014``) — an ID material's base color.
- **Outliner** (``chk013``) — the object's **outliner text color**, same as Maya's. Blender
  publishes no such property, so :class:`~blendertk.display_utils.outliner_tint.OutlinerTint`
  stores it on the object and paints it into the outliner; see that module for the mechanism
  and its fail-closed guarantees.
- **Wireframe** (``chk012``) — ``obj.color``, drawn on the wires under
  ``shading.wireframe_color_type='OBJECT'``. (The engine can also show the same tint as the
  Solid fill — ``show_channels({"object": True})`` — but that engine channel keeps no checkbox.)
- **Vertex** (``chk015``) — a mesh color attribute.

**Set Per Color** (``chk016``, off by default — mirrors mayatk's twin) additionally groups each
applied color's objects into a container named ``ID_<HEX>``: a color-tagged **collection** here,
an **objectSet** in Maya. It is a grouping aid, orthogonal to the four color channels, so it
composes with any of them (or none).

Because Blender's Solid shading defaults to ``color_type='MATERIAL'``, writing a channel is only
half the job — :meth:`ColorId.show_channels` points the viewports at the channel being applied,
without which the tool writes correct data that renders nowhere. Apply to / select by / reset
across any combination of the enabled channels; save/restore swatch palettes as presets.

The engine (``ColorId``) lives next to its panel + ``.ui`` (mirror of mayatk's
``display_utils.color_id``); served by ``BlenderUiHandler`` (``marking_menu.show
("color_id")``). Self-contained (``ptk.LoggingMixin`` only); the Qt-only ``uitk`` helpers are
deferred into the methods that use them (headless Blender ships no Qt binding). ``import bpy``
is deferred too.
"""

import random
from typing import List, Optional, Sequence, Tuple

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.display_utils.outliner_tint import OutlinerTint

Color = Tuple[float, float, float]


class ColorId:
    """Engine: apply / select-by / reset object colors across the material, object-color (drawn
    as the solid fill or on the wires), vertex, and outliner (color-tagged ID collection)
    channels, and point the viewports at whichever channel was applied. Operates on
    ``bpy.types.Object`` references (Blender idiom), not name strings."""

    # Desaturated defaults so swatches aren't all white on first launch (mirrors mayatk).
    DEFAULT_SWATCH_COLORS = [
        (180, 120, 120),
        (180, 150, 120),
        (180, 180, 120),
        (120, 180, 120),
        (120, 180, 160),
        (120, 180, 180),
        (120, 150, 180),
        (120, 120, 180),
        (150, 120, 180),
        (180, 120, 180),
        (180, 120, 150),
        (160, 160, 160),
    ]

    # ── apply ──────────────────────────────────────────────────────────────
    @staticmethod
    def _id_name(color: Color) -> str:
        """``ID_<HEX>`` datablock name for ``color`` — shared by the ID materials and the
        outliner channel's ID collections (separate bpy namespaces, so the same name is fine)."""
        return "ID_" + "".join(
            f"{int(max(0.0, min(1.0, c)) * 255):02X}" for c in color[:3]
        )

    @classmethod
    def assign_id_material(cls, obj, color: Color):
        """Assign an ID material named ``ID_<HEX>`` with ``color`` as its base color to ``obj``
        (created once, reused thereafter). Replaces the object's material slots — this is a flat
        ID-color pass, so the whole object takes the one color (mirrors Maya's assign-by-color)."""
        import bpy

        name = cls._id_name(color)
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        rgba = (color[0], color[1], color[2], 1.0)
        mat.diffuse_color = rgba  # viewport (Solid / Material-preview flat)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf and "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = rgba
        if hasattr(
            obj.data, "materials"
        ):  # mesh/curve/surface/text/meta hold material slots
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
            attr = mesh.color_attributes.new(
                name=name, type="BYTE_COLOR", domain="CORNER"
            )
        rgba = (color[0], color[1], color[2], 1.0)
        for d in attr.data:
            d.color = rgba
        try:
            mesh.color_attributes.active_color = attr
        except Exception:
            pass
        mesh.update()

    # ── outliner text color ────────────────────────────────────────────────
    @staticmethod
    def set_outliner_color(objects: Sequence, color: Color) -> int:
        """Set the objects' outliner **text** color (Maya's ``outlinerColor`` analogue)."""
        return OutlinerTint.set_color(objects, color)

    @staticmethod
    def get_outliner_color(obj) -> Optional[Color]:
        """The object's stored outliner text color, or None."""
        return OutlinerTint.get_color(obj)

    @staticmethod
    def reset_outliner_colors(objects: Sequence) -> int:
        """Clear the objects' outliner text color."""
        return OutlinerTint.clear(objects)

    # ── Set Per Color (grouping aid — collections here, objectSets in Maya) ─
    # Groups each applied color's objects into an ``ID_<HEX>`` collection tagged with the
    # nearest of Blender's 8 theme collection-color swatches (its only per-row color). The
    # exact color is stamped (custom prop below) so select-by / get round-trip losslessly
    # despite that quantization. The stamp — not the ``ID_`` name — is what reset trusts, so a
    # user's own ``ID_*`` collection is never swept (collections carry scene structure; a
    # name-prefix filter like the ID-material one would have too large a blast radius here).
    _ID_COLLECTION_PROP = "btk_color_id"

    # Blender 5.1 factory theme collection-tag colors — fallback when no theme is reachable.
    _STOCK_TAG_COLORS = (
        (0.8863, 0.3765, 0.3569),
        (0.9451, 0.6392, 0.3333),
        (0.9451, 0.8627, 0.3333),
        (0.4824, 0.8000, 0.4824),
        (0.3647, 0.7137, 0.9176),
        (0.5529, 0.3490, 0.8549),
        (0.7765, 0.4510, 0.7216),
        (0.4784, 0.3294, 0.2549),
    )

    @classmethod
    def collection_tag_colors(cls) -> List[Color]:
        """The 8 collection-tag swatch colors from the user's theme (``COLOR_01``…``COLOR_08``),
        falling back to the 5.1 factory values. Read live so a retinted theme still maps right."""
        import bpy

        try:
            colors = [
                tuple(c.color[:3])
                for c in bpy.context.preferences.themes[0].collection_color
            ]
            if len(colors) == 8:
                return colors
        except Exception:
            pass
        return list(cls._STOCK_TAG_COLORS)

    @classmethod
    def nearest_collection_tag(cls, color: Color) -> str:
        """The ``COLOR_NN`` tag enum whose theme swatch is closest to ``color``."""
        tags = cls.collection_tag_colors()
        i = min(range(len(tags)), key=lambda n: cls.color_difference(tags[n], color))
        return f"COLOR_{i + 1:02d}"

    @classmethod
    def add_to_color_set(cls, objects: Sequence, color: Color):
        """Group ``objects`` under a color-tagged ``ID_<HEX>`` collection in the outliner.

        Links each object into the collection *additionally* (home memberships untouched),
        moves it out of any other stamped ID collection (a recolor is a move, not a pile-up),
        and garbage-collects stamped collections left empty. Returns the collection
        (None when ``objects`` is empty — nothing is created)."""
        import bpy

        objects = [o for o in objects if o is not None]
        if not objects:
            return None
        # Stamp-keyed lookup, never name-keyed: a user's own collection that happens to hold
        # the ID_<HEX> name must not be adopted (stamped, retagged, and later swept by reset) —
        # creating alongside it just gets Blender's .001 suffix. The name is display-only.
        col = next(
            (
                c
                for c in bpy.data.collections
                if cls._ID_COLLECTION_PROP in c
                and cls.color_difference(
                    tuple(c[cls._ID_COLLECTION_PROP])[:3], color[:3]
                )
                < 1e-6
            ),
            None,
        )
        if col is None:
            col = bpy.data.collections.new(cls._id_name(color))
        col[cls._ID_COLLECTION_PROP] = list(color[:3])
        col.color_tag = cls.nearest_collection_tag(color)
        root = bpy.context.scene.collection
        if col.name not in {c.name for c in root.children_recursive}:
            root.children.link(col)
        for obj in objects:
            for other in list(obj.users_collection):  # snapshot — we unlink mid-walk
                if other != col and cls._ID_COLLECTION_PROP in other:
                    other.objects.unlink(obj)
            if obj.name not in col.objects:
                col.objects.link(obj)
        cls._gc_id_collections()
        return col

    @classmethod
    def get_color_set_color(cls, obj) -> Optional[Color]:
        """The exact color stamped on the object's ID collection, or None when it has none."""
        for col in getattr(obj, "users_collection", ()) or ():
            if cls._ID_COLLECTION_PROP in col:
                return tuple(col[cls._ID_COLLECTION_PROP])[:3]
        return None

    @classmethod
    def remove_from_color_sets(cls, objects: Sequence) -> None:
        """Unlink ``objects`` from every stamped ID collection; remove any left empty.

        An object whose ID collection became its ONLY membership (the user unlinked its home)
        is re-homed to the scene root first — a zero-collection object is orphaned data, gone
        from the view layer and collected on the next save/load."""
        import bpy

        for obj in objects:
            if obj is None:
                continue
            memberships = list(obj.users_collection)
            if memberships and all(
                cls._ID_COLLECTION_PROP in c for c in memberships
            ):
                bpy.context.scene.collection.objects.link(obj)
            for col in memberships:
                if cls._ID_COLLECTION_PROP in col:
                    col.objects.unlink(obj)
        cls._gc_id_collections()

    @classmethod
    def _gc_id_collections(cls) -> None:
        """Remove stamped ID collections that no longer hold anything."""
        import bpy

        for col in list(bpy.data.collections):
            if (
                cls._ID_COLLECTION_PROP in col
                and not col.objects
                and not col.children
            ):
                bpy.data.collections.remove(col)

    @classmethod
    def apply_color(
        cls,
        objects: Sequence,
        color: Optional[Color] = None,
        apply_to_material: bool = False,
        apply_to_object: bool = False,
        apply_to_vertex: bool = False,
        apply_to_outliner: bool = False,
        set_per_color: bool = False,
    ) -> None:
        """Apply ``color`` (random when None) to each object across the enabled channels.

        ``set_per_color`` additionally groups the batch into an ``ID_<HEX>`` collection — a
        grouping aid orthogonal to the color channels (mirrors mayatk's objectSet twin)."""
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
        if apply_to_outliner:
            # Batched: the stamp is per-object but enabling the overlay is not, so a
            # per-object call would re-enter the handler check once per object.
            cls.set_outliner_color(objects, color)
        if set_per_color:
            # One collection pass for the whole batch (per-object would churn the GC);
            # the engine filters Nones and no-ops on an empty batch itself.
            cls.add_to_color_set(objects, color)

    # ── viewport display ───────────────────────────────────────────────────
    # Which Solid-shading color source shows each channel. Writing a channel is only half the
    # job: Blender's Solid shading defaults to ``color_type='MATERIAL'``, so an ``obj.color`` or
    # vertex-color write renders *nothing* until the viewport is pointed at that source. Maya's
    # outliner/wireframe tints need no such switch, so a straight port of the mayatk tool looks
    # broken. Precedence puts MATERIAL last — it is already Blender's default, so the channels
    # that need a switch win when several are enabled at once.
    CHANNEL_COLOR_TYPE = (
        ("object", "OBJECT"),
        ("vertex", "VERTEX"),
        ("material", "MATERIAL"),
    )

    @classmethod
    def show_channels(cls, channels: dict) -> int:
        """Point every 3D viewport at the enabled channels' color source; returns viewports updated.

        ``channels`` maps channel keys to bools; recognized here: ``"object"`` / ``"material"`` /
        ``"vertex"`` / ``"wireframe"``. Unknown keys are ignored — the panel's dict also carries
        ``"outliner"``, which needs no viewport switch (collection tags always show there), and
        omits ``"object"`` (that engine channel keeps no checkbox; pass it programmatically to
        show ``obj.color`` as the Solid fill). The **wireframe** channel drives
        ``wireframe_color_type`` (a separate knob that reads the same ``obj.color``), so it
        composes with — rather than competes for — the Solid color source.

        The shading *mode* is only forced when the channel genuinely cannot draw without it: the
        per-object sources (OBJECT / VERTEX) need Solid, and a wireframe-only pass needs Solid or
        Wireframe. A Material pass never forces anything — an ID material is what Material-preview
        and Rendered already show, so yanking a look-dev viewport back to Solid would cost the
        user their view and gain nothing.
        """
        color_type = next(
            (ct for key, ct in cls.CHANNEL_COLOR_TYPE if channels.get(key)), None
        )
        wireframe = bool(channels.get("wireframe"))
        if color_type is None and not wireframe:
            return 0

        if color_type in ("OBJECT", "VERTEX"):
            drawable_in = ("SOLID",)
        elif wireframe:  # wireframe-only pass — Wireframe mode draws object-colored wires too
            drawable_in = ("SOLID", "WIREFRAME")
        else:  # MATERIAL only — every shading mode already shows it
            drawable_in = None

        count = 0
        for area in CoreUtils.get_areas("VIEW_3D"):
            shading = getattr(area.spaces.active, "shading", None)
            if shading is None:
                continue
            if drawable_in and shading.type not in drawable_in:
                shading.type = "SOLID"
            if color_type is not None:
                shading.color_type = color_type
            if wireframe:
                shading.wireframe_color_type = "OBJECT"
            count += 1
        return count

    # ── read ───────────────────────────────────────────────────────────────
    # Blender ships no "use object color" flag (Maya's ``useOutlinerColor``), so the untouched
    # default — white, what :meth:`reset_colors` restores — is the only available "unset" signal.
    UNSET_OBJECT_COLOR: Color = (1.0, 1.0, 1.0)

    @classmethod
    def has_object_color(cls, obj, tolerance: float = 1e-4) -> bool:
        """True when ``obj`` carries an assigned object color (i.e. it isn't the default white).

        Lets the read paths tell "ID color = white" from "never assigned", the way Maya's
        ``useOutlinerColor`` flag does. The cost of having no real flag: an ID color of pure
        white is indistinguishable from unset on this channel.
        """
        c = cls.get_object_color(obj)
        if c is None:
            return False
        return cls.color_difference(c, cls.UNSET_OBJECT_COLOR) > tolerance

    @staticmethod
    def get_object_color(obj) -> Optional[Color]:
        """The object's viewport display color (``obj.color`` RGB), or None.

        A raw read — every object always has one. Pair with :meth:`has_object_color` to tell an
        assigned ID color from the untouched default.
        """
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
        check_outliner: bool = False,
        check_set: bool = False,
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
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_object and not matched and cls.has_object_color(obj):
                c = cls.get_object_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_vertex and not matched:
                c = cls.get_average_vertex_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_outliner and not matched:
                c = cls.get_outliner_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_set and not matched:
                # Compares the exact stamped color, not the quantized tag swatch — so what
                # apply just grouped is always found, whatever the 8-swatch rounding did.
                c = cls.get_color_set_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
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
        reset_outliner: bool = True,
        reset_sets: bool = True,
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
        if reset_outliner:
            cls.reset_outliner_colors(objects)
        if reset_sets:
            cls.remove_from_color_sets(objects)

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

    Channel checkboxes (same objectName → same channel as mayatk): ``chk012`` Wireframe ·
    ``chk013`` Outliner · ``chk014`` Material · ``chk015`` Vertex (see :meth:`_channels`).
    Self-contained (``ptk.LoggingMixin`` only)."""

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
            "swatches": [
                self._hex_from_rgb(rgb) for rgb in ColorId.DEFAULT_SWATCH_COLORS
            ]
        }
        try:
            presets.save(self._DEFAULT_PRESET)
        finally:
            presets.metadata_provider = original

    def header_init(self, widget):
        """Configure header help text and preset combobox."""
        # Gesture-scoped window: pin button + auto-hide on key_show release.
        widget.config_buttons("menu", "collapse", "pin")
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Color ID",
                body="Color-code scene objects across four channels: an ID "
                "<b>Material</b>, an <b>Outliner</b> color-tag group, the "
                "<b>Wireframe</b> tint, and <b>Vertex</b> colors.",
                steps=[
                    "Click a palette swatch to pick the active color (right-click a "
                    "swatch to change its color).",
                    "Enable the channels to apply via the <b>Outliner</b> / "
                    "<b>Wireframe</b> / <b>Material</b> / <b>Vertex</b> checkboxes.",
                    "Select objects and press <b>Set Color</b>.",
                    "Use <b>Select By Color</b> to find objects matching the active "
                    "color across the enabled channels.",
                ],
                sections=[
                    (
                        "Notes",
                        [
                            f"<b>Reset</b> clears assignments on the selection (or every "
                            f"object with {self.sb.tooltip.kbd('Ctrl')}-click).",
                            "<b>Outliner</b> groups the objects under a color-tagged "
                            "<i>ID collection</i> (their normal collections are kept). "
                            "Blender colors collection rows, not object text — and shows "
                            "the nearest of the 8 theme collection colors; Select By / "
                            "Get Color still use the exact applied color.",
                            "<b>Set Color</b> switches the viewport's Solid shading to the "
                            "channel it applied (Solid display ▸ Color) — Blender defaults "
                            "that to <i>Material</i>, where a wireframe or vertex color "
                            "would not show at all.",
                            "<b>Material</b> assigns a flat ID material (replaces the "
                            "object's material slots).",
                        ],
                    ),
                    (
                        "Presets",
                        [
                            "The header menu's preset combo saves / restores swatch "
                            "palettes. Use <b>Save</b> to capture the current colors; "
                            "pick a preset to restore them.",
                        ],
                    ),
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
        objects = CoreUtils.selected_objects()
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
        # Same objectName → same channel as mayatk: chk012 Wireframe (obj.color drawn on the
        # wires), chk013 Outliner (color-tagged ID collection), chk014 Material, chk015 Vertex.
        # The engine's "object" channel (obj.color as the Solid fill) keeps no checkbox of its
        # own — the Wireframe channel writes the same obj.color, and ColorId.set_object_color /
        # show_channels({"object": True}) stay available programmatically.
        return {
            "wireframe": self.ui.chk012.isChecked(),
            "outliner": self.ui.chk013.isChecked(),
            "material": self.ui.chk014.isChecked(),
            "vertex": self.ui.chk015.isChecked(),
            "set": self.ui.chk016.isChecked(),
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
        with CoreUtils.undo_chunk("Color ID: Reset"):
            ColorId.reset_colors(objects)
        CoreUtils.tag_redraw()  # all editors — the outliner repaints too

    def b001(self) -> None:
        """Set Color — apply the active color to the selected objects on the enabled channels."""
        objects = self.selected_objects
        color = self.target_color
        if not objects or color is None:
            return
        ch = self._channels()
        with CoreUtils.undo_chunk("Color ID: Set Color"):
            ColorId.apply_color(
                objects,
                color=color,
                apply_to_object=ch["wireframe"],  # the wireframe channel's datum is obj.color
                apply_to_material=ch["material"],
                apply_to_vertex=ch["vertex"],
                apply_to_outliner=ch["outliner"],
                set_per_color=ch["set"],
            )
        # Writing the channel is only half the job — point the viewports at that color source,
        # or the applied color renders nowhere and the tool looks like it did nothing. (The
        # outliner channel needs no switch: collection tags always show there.)
        ColorId.show_channels(ch)
        CoreUtils.tag_redraw()  # all editors — the outliner repaints too

    def b002(self) -> None:
        """Select By Color — select scene objects matching the active color (enabled channels)."""
        import bpy

        color = self.target_color
        if color is None:
            return
        ch = self._channels()
        found = ColorId.get_objects_by_color(
            color,
            check_object=ch["wireframe"],
            check_material=ch["material"],
            check_vertex=ch["vertex"],
            check_outliner=ch["outliner"],
            check_set=ch["set"],
        )
        # Direct select_set (not bpy.ops.object.select_all) so Select-By-Color works in any
        # mode — the object operator poll-fails in edit mode (Maya's selects anywhere).
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj in found)
        bpy.context.view_layer.objects.active = found[0] if found else None
        CoreUtils.tag_redraw()  # selection highlights in the outliner too

    def b003(self) -> None:
        """Get Color — read the active object's color into the selected swatch.

        Reads whichever enabled channel has a color (Outliner → Wireframe → Material → Vertex —
        the outliner stamp first since it round-trips the exact applied color).
        (Mayatk's b003 is a fixed wireframe-color eyedropper; here every enabled channel can
        answer, so they're read in order.)"""
        import bpy

        obj = bpy.context.view_layer.objects.active
        button = self.selected_button
        if obj is None or button is None:
            self.sb.message_box("Select an object and a swatch first.")
            return
        ch = self._channels()
        color = None
        if ch["outliner"]:
            color = ColorId.get_outliner_color(obj)
        if color is None and ch["set"]:
            color = ColorId.get_color_set_color(obj)
        # has_object_color, not a None check: obj.color always reads back (default white), so a
        # bare get_object_color would swallow every fallback and hand back white forever.
        if color is None and ch["wireframe"] and ColorId.has_object_color(obj):
            color = ColorId.get_object_color(obj)
        if color is None and ch["material"]:
            color = ColorId.get_material_color(obj)
        if color is None and ch["vertex"]:
            color = ColorId.get_average_vertex_color(obj)
        if color is None:
            self.sb.message_box(
                "No color found on the active object's enabled channels."
            )
            return
        button.color = self.sb.QtGui.QColor(
            int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("color_id", reload=True)
    ui.show(pos="screen", app_exec=True)
