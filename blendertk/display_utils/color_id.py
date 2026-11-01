# !/usr/bin/python
# coding=utf-8
"""Color ID engine: color-code scene objects across channels.

Blender port of mayatk's ``ColorId``. Maya's four channels (material / outliner / wireframe /
vertex) map onto Blender as:

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
half the job — :meth:`ColorId.apply_color` ends by pointing the viewports at the channels it
wrote (:meth:`ColorId.show_channels`), without which the tool writes correct data that renders
nowhere. Apply to / select by / read / reset across any combination of the channels.

The panel is tentacle's ``slots/tools/color_id.py``: one Slots class for both hosts over this
class as ``btk.ColorId`` (mirror of mayatk's ``display_utils.color_id``, keywords included:
``apply_to_wireframe`` / ``check_wireframe_color`` / ``reset_wireframe``). ``import bpy`` is
deferred into the call bodies.
"""

import random
from typing import List, Optional, Sequence, Tuple

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

    #: The swatch palettes' preset store (uitk ``PresetManager.preset_dir``), which the
    #: tentacle panel reads: each host keeps its own palettes (mayatk's: "mayatk/color_manager").
    SWATCH_PRESET_DIR = "blendertk/color_id"

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
        from blendertk.mat_utils._mat_utils import MatUtils

        mat.diffuse_color = rgba  # viewport (Solid / Material-preview flat)
        bsdf = MatUtils.get_node_tree(mat, create=True).nodes.get("Principled BSDF")
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
            if memberships and all(cls._ID_COLLECTION_PROP in c for c in memberships):
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
            if cls._ID_COLLECTION_PROP in col and not col.objects and not col.children:
                bpy.data.collections.remove(col)

    @classmethod
    def apply_color(
        cls,
        objects: Sequence,
        color: Optional[Color] = None,
        apply_to_material: bool = False,
        apply_to_vertex: bool = False,
        apply_to_wireframe: bool = False,
        apply_to_outliner: bool = False,
        set_per_color: bool = False,
        apply_to_object: bool = False,
    ) -> None:
        """Apply ``color`` (random when None) to each object across the enabled channels, as
        ONE undo step (mayatk's keywords), then point the viewports at what was applied.

        ``apply_to_wireframe`` writes ``obj.color`` and draws it on the wires (Maya's wireframe
        override); ``apply_to_object`` writes the same ``obj.color`` but shows it as the Solid
        fill (an engine-only channel). ``set_per_color`` additionally groups the batch into an
        ``ID_<HEX>`` collection — a grouping aid orthogonal to the color channels (mirrors
        mayatk's objectSet twin). Writing a channel is only half the job here: Solid shading
        defaults to the material color, so :meth:`show_channels` switches the viewports to the
        applied channels' source, without which the color renders nowhere."""
        objects = list(objects)  # walked once per channel below
        if color is None:
            color = (random.random(), random.random(), random.random())
        with CoreUtils.undo_chunk("Color ID: Set Color"):
            for obj in objects:
                if obj is None:
                    continue
                if apply_to_object or apply_to_wireframe:
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
        cls.show_channels(
            {
                "object": apply_to_object,
                "wireframe": apply_to_wireframe,
                "material": apply_to_material,
                "vertex": apply_to_vertex,
            }
        )
        CoreUtils.tag_redraw()  # all editors — the outliner repaints too

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
        ``"vertex"`` / ``"wireframe"``; unknown keys are ignored (the outliner needs no viewport
        switch). :meth:`apply_color` calls it with the channels it wrote; ``"object"`` shows
        ``obj.color`` as the Solid fill (an engine channel the panel keeps no checkbox for).
        The **wireframe** channel drives
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
        elif (
            wireframe
        ):  # wireframe-only pass — Wireframe mode draws object-colored wires too
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
        from blendertk.mat_utils._mat_utils import MatUtils

        tree = MatUtils.get_node_tree(mat)
        if tree is not None:
            bsdf = tree.nodes.get("Principled BSDF")
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
        check_material_color: bool = False,
        check_vertex_color: bool = False,
        check_wireframe_color: bool = False,
        check_outliner_color: bool = False,
        check_set: bool = False,
    ) -> List:
        """View-layer mesh objects whose color (on any enabled channel) is within ``threshold``.

        mayatk's keywords: ``check_wireframe_color`` reads ``obj.color`` (when assigned), the
        datum both the wireframe and the object channel write.

        Iterates ``view_layer.objects`` (not ``scene.objects``) so every match is selectable —
        an object in a view-layer-excluded collection can't be selected, so the caller's
        ``select_set`` would otherwise raise on it."""

        out = []
        # the window's layer: the one the caller's select_set acts in
        for obj in CoreUtils._active_view_layer().objects:
            if obj.type != "MESH":
                continue
            matched = False
            if check_material_color and not matched:
                c = cls.get_material_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_wireframe_color and not matched and cls.has_object_color(obj):
                c = cls.get_object_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_vertex_color and not matched:
                c = cls.get_average_vertex_color(obj)
                matched = (
                    c is not None and cls.color_difference(c, target_color) <= threshold
                )
            if check_outliner_color and not matched:
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

    @classmethod
    def select_by_color(
        cls, target_color: Color, threshold: float = 0.1, **checks
    ) -> List:
        """Select the objects :meth:`get_objects_by_color` finds, replacing the selection
        (none found clears it), the first one active; returns them (mirror of mayatk's).
        ``checks`` are its ``check_*`` channel flags.

        In any mode, as Maya's selects (:func:`CoreUtils.select`)."""
        found = cls.get_objects_by_color(target_color, threshold=threshold, **checks)
        CoreUtils.select(found)
        CoreUtils.tag_redraw()  # selection highlights in the outliner too
        return found

    @classmethod
    def get_color(
        cls,
        obj,
        check_material_color: bool = False,
        check_vertex_color: bool = False,
        check_wireframe_color: bool = False,
        check_outliner_color: bool = False,
        check_set: bool = False,
    ) -> Optional[Color]:
        """The first color ``obj`` carries on the enabled channels (0-1 RGB), or None.

        The panel's Get Color (mirror of mayatk's). Read in the order Outliner, Set,
        Wireframe, Material, Vertex: the exact stamps first, since they round-trip the
        applied color. The wireframe channel answers only for an assigned ``obj.color``
        (:meth:`has_object_color`): it always reads back, the default white included."""
        readers = (
            (check_outliner_color, cls.get_outliner_color),
            (check_set, cls.get_color_set_color),
            (
                check_wireframe_color,
                lambda o: cls.get_object_color(o) if cls.has_object_color(o) else None,
            ),
            (check_material_color, cls.get_material_color),
            (check_vertex_color, cls.get_average_vertex_color),
        )
        for enabled, read in readers:
            color = read(obj) if enabled else None
            if color is not None:
                return tuple(color[:3])
        return None

    @staticmethod
    def lead_object():
        """The object Get Color reads: the active object, or None (mirror of mayatk's,
        where it is the last selected)."""
        return CoreUtils.active_object()

    @staticmethod
    def scene_objects() -> List:
        """Every object a scene-wide Color ID action covers (the panel's Ctrl+click
        Reset): the scene's objects (mirror of mayatk's, its geometry transforms)."""
        import bpy

        return list(bpy.context.scene.objects)

    # ── reset ──────────────────────────────────────────────────────────────
    @classmethod
    def reset_colors(
        cls,
        objects: Sequence,
        reset_outliner: bool = True,
        reset_wireframe: bool = True,
        reset_vertex: bool = True,
        reset_material: bool = True,
        reset_sets: bool = True,
    ) -> None:
        """Clear color assignments on ``objects`` for the chosen channels, as ONE undo step
        (mayatk's keywords; ``reset_wireframe`` restores ``obj.color``, the datum both the
        wireframe and the object channel write)."""
        objects = [o for o in objects if o is not None]
        with CoreUtils.undo_chunk("Color ID: Reset"):
            for obj in objects:
                if reset_wireframe:
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
        CoreUtils.tag_redraw()  # all editors — the outliner repaints too

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
