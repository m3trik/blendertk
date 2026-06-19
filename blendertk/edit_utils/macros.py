# !/usr/bin/python
# coding=utf-8
"""Hotkey macros — the Blender counterpart of ``mayatk.edit_utils.macros``.

Mirrors mayatk's macro system **name + behavior** so the same muscle memory transfers between
DCCs: a ``Macros`` class of ``m_*`` viewport/edit/selection/animation toggles, plus a
``MacroManager`` whose ``set_macros("m_name, key=1, cat=Display", ...)`` accepts the **identical
string spec** the Maya ``userSetup.py`` uses — only here it registers Blender **keymap items**
(bound to a single dispatcher operator) instead of Maya runtime commands.

Runtime-only macros: the ``m_*`` methods touch ``bpy.ops`` / keyconfigs, so they only do
anything inside a running Blender. ``Macros`` IS in ``DEFAULT_INCLUDE`` (so ``btk.Macros``
mirrors ``mtk.Macros``), so the ``bpy`` import is **guarded** (``bpy = None`` when absent) —
exactly like mayatk's ``macros`` guards ``maya.cmds`` — so resolving the package surface never
requires Blender (no module-level ``bpy`` use; the operator class is built inside
``_ensure_operator``). ``MacroManager.set_macros`` is called explicitly by the Blender startup
script (``tentacle_startup.py``), exactly as Maya's ``userSetup.py`` calls the mayatk macros.

    from blendertk.edit_utils import macros
    macros.Macros.set_macros("m_frame, key=f, cat=Display")
"""
try:
    import bpy
except ImportError:  # surface must resolve headless (btk.Macros) — bpy used only at runtime
    bpy = None

from blendertk.edit_utils._edit_utils import set_subdivision, _group_under_empty


# ====================================================================================
# Macro functions (Blender-idiomatic equivalents of the mayatk macros)
# ====================================================================================
class _ViewportMixin:
    """Shared viewport helpers."""

    @staticmethod
    def _find_view3d():
        """(window, area, space) of the **active** 3D viewport — the one the macro fired in
        (``context.area``, like Maya's focused panel) — else the first 3D viewport found, else
        (None, None, None). Headless-safe."""
        ctx = bpy.context
        area = getattr(ctx, "area", None)
        if area and area.type == "VIEW_3D":
            return ctx.window, area, area.spaces.active
        for win in ctx.window_manager.windows:
            for a in win.screen.areas:
                if a.type == "VIEW_3D":
                    return win, a, a.spaces.active
        return None, None, None

    @classmethod
    def _view3d(cls):
        """(area, space) of the active 3D viewport, or (None, None)."""
        _win, area, space = cls._find_view3d()
        return area, space

    @classmethod
    def _view3d_override(cls):
        """A ``temp_override`` targeting the active 3D viewport (window/area/region), or None."""
        win, area, _space = cls._find_view3d()
        if not area:
            return None
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if not region:
            return None
        return bpy.context.temp_override(window=win, area=area, region=region)


class DisplayMacros(_ViewportMixin):
    """ """

    @classmethod
    def m_back_face_culling(cls):
        """Toggle Back-Face Culling in the viewport."""
        _area, space = cls._view3d()
        if space:
            space.shading.show_backface_culling = not space.shading.show_backface_culling

    @classmethod
    def m_isolate_selected(cls):
        """Isolate the current selection (toggle Local View)."""
        ov = cls._view3d_override()
        if ov is None:
            return
        with ov:
            bpy.ops.view3d.localview()

    @classmethod
    def m_wireframe(cls):
        """Cycle the wireframe-on-shaded overlay: Off -> Full -> Reduced (mirrors Maya's
        ``wireframeOnShadedActive`` none/full/reduced). Switching the actual shading mode is '4'
        (m_shading)."""
        _area, space = cls._view3d()
        if not space:
            return
        ov = space.overlay
        if not ov.show_wireframes:  # Off -> Full
            ov.show_wireframes = True
            ov.wireframe_threshold = 1.0
        elif ov.wireframe_threshold >= 1.0:  # Full -> Reduced
            ov.wireframe_threshold = 0.5
        else:  # Reduced -> Off
            ov.show_wireframes = False

    @classmethod
    def m_shading(cls):
        """Cycle viewport shading: Wireframe -> Solid -> Material Preview."""
        _area, space = cls._view3d()
        if not space:
            return
        order = ["WIREFRAME", "SOLID", "MATERIAL"]
        current = space.shading.type
        space.shading.type = order[(order.index(current) + 1) % len(order)] if current in order else "SOLID"

    @classmethod
    def m_lighting(cls):
        """Cycle Solid-mode viewport lighting Studio -> MatCap -> Flat (Maya's displayLights
        cycle). Lighting only applies in Solid, so switch to Solid first if in another mode
        (without resetting the light) rather than yanking the light state every press."""
        _area, space = cls._view3d()
        if not space:
            return
        if space.shading.type != "SOLID":
            space.shading.type = "SOLID"
            return
        order = ["STUDIO", "MATCAP", "FLAT"]
        current = space.shading.light
        space.shading.light = order[(order.index(current) + 1) % len(order)] if current in order else "STUDIO"

    @classmethod
    def m_grid_and_image_planes(cls):
        """Toggle the floor grid and reference image-empties together."""
        _area, space = cls._view3d()
        if not space:
            return
        new_state = not space.overlay.show_floor
        space.overlay.show_floor = new_state
        space.overlay.show_axis_x = new_state
        space.overlay.show_axis_y = new_state
        for obj in bpy.data.objects:
            if obj.type == "EMPTY" and obj.empty_display_type == "IMAGE":
                obj.hide_viewport = not new_state

    _DISPLAY_CYCLE = ["TEXTURED", "WIRE", "BOUNDS"]

    @classmethod
    def m_cycle_display_state(cls):
        """Cycle the selected objects' draw type: Textured -> Wireframe -> Bounds (driven by the
        first object). A reversible draw cycle rather than Maya's Visible/XRay/Templated/Hidden:
        actually hiding an object would drop it from the selection and break the cycle (use H/Alt-H
        to hide). All selected objects follow the first's next state."""
        sel = [o for o in bpy.context.selected_objects if o]
        if not sel:
            return
        cycle = cls._DISPLAY_CYCLE
        current = sel[0].display_type
        nxt = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else cycle[1]
        for o in sel:
            o.display_type = nxt

    @classmethod
    def m_smooth_preview(cls):
        """Toggle a live Subdivision-Surface preview on the selected meshes."""
        objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not objs:
            return
        has_subsurf = any(
            m.type == "SUBSURF" and m.show_viewport for m in objs[0].modifiers
        )
        if has_subsurf:
            for o in objs:
                for m in [m for m in o.modifiers if m.type == "SUBSURF"]:
                    o.modifiers.remove(m)
        else:
            set_subdivision(objs, viewport_levels=1)

    @classmethod
    def m_frame(cls):
        """Frame the selection (or the whole scene when nothing is selected)."""
        ov = cls._view3d_override()
        if ov is None:
            return
        active = bpy.context.active_object
        framing_selection = bool(bpy.context.selected_objects) or (
            active is not None and active.mode == "EDIT"
        )
        with ov:
            if framing_selection:
                bpy.ops.view3d.view_selected()
            else:
                bpy.ops.view3d.view_all()


class EditMacros(_ViewportMixin):
    """ """

    @staticmethod
    def m_multi_component():
        """Multi-component selection — enable vertex+edge+face select together (edit mode)."""
        obj = bpy.context.active_object
        if obj and obj.type == "MESH" and obj.mode == "EDIT":
            bpy.context.tool_settings.mesh_select_mode = (True, True, True)

    @classmethod
    def m_paste_and_rename(cls):
        """Paste objects (Blender's paste adds no 'pasted__' prefix, so no rename needed)."""
        ov = cls._view3d_override()
        if ov is None:
            return
        with ov:
            bpy.ops.view3d.pastebuffer()

    @staticmethod
    def m_merge_vertices(tolerance=0.0001):
        """Merge vertices by distance — on the active mesh in Edit Mode, or across every selected
        mesh in Object Mode (mirrors Maya's component- and object-mode merge)."""
        obj = bpy.context.active_object
        if obj and obj.type == "MESH" and obj.mode == "EDIT":
            bpy.ops.mesh.remove_doubles(threshold=tolerance)
            return
        import bmesh

        for o in (m for m in bpy.context.selected_objects if m.type == "MESH"):
            bm = bmesh.new()
            bm.from_mesh(o.data)
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=tolerance)
            bm.to_mesh(o.data)
            bm.free()
            o.data.update()

    @staticmethod
    def m_group():
        """Group the selected objects under an Empty at the selection's center, keeping their
        world transforms (Maya's group + center-pivot)."""
        sel = [o for o in bpy.context.selected_objects if o]
        if not sel:
            return
        _group_under_empty(sel, "group", center=True)


class SelectionMacros:
    """ """

    @staticmethod
    def m_object_selection():
        """Object selection mask — leave edit mode (object mode)."""
        if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

    @staticmethod
    def _enter_edit_mesh_mode(select_mode):
        obj = bpy.context.active_object
        if not (obj and obj.type == "MESH"):
            return False
        if obj.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.tool_settings.mesh_select_mode = select_mode
        return True

    @classmethod
    def m_vertex_selection(cls):
        """Vertex selection mask (edit mode)."""
        cls._enter_edit_mesh_mode((True, False, False))

    @classmethod
    def m_edge_selection(cls):
        """Edge selection mask (edit mode)."""
        cls._enter_edit_mesh_mode((False, True, False))

    @classmethod
    def m_face_selection(cls):
        """Face selection mask (edit mode)."""
        cls._enter_edit_mesh_mode((False, False, True))

    @staticmethod
    def m_invert_selection():
        """Invert the current selection (component-aware)."""
        obj = bpy.context.active_object
        if obj and obj.type == "MESH" and obj.mode == "EDIT":
            bpy.ops.mesh.select_all(action="INVERT")
        else:
            bpy.ops.object.select_all(action="INVERT")

    @staticmethod
    def m_toggle_UV_select_type():
        """Toggle UV select mode between Vertex and Face (Blender's ``uv_select_mode`` enum is
        VERTEX/EDGE/FACE — there is no Maya-style UV-shell mode; Face is the closest 'whole
        element' analogue)."""
        ts = bpy.context.scene.tool_settings
        ts.uv_select_mode = "FACE" if ts.uv_select_mode != "FACE" else "VERTEX"


class UiMacros(_ViewportMixin):
    """ """

    @classmethod
    def m_toggle_panels(cls):
        """Toggle the 3D viewport's header, tool, and side (N) regions together."""
        _area, space = cls._view3d()
        if not space:
            return
        new_state = not space.show_region_header
        space.show_region_header = new_state
        space.show_region_toolbar = new_state
        space.show_region_ui = new_state


class AnimationMacros:
    """ """

    _CHANNELS = ("location", "rotation_euler", "scale")

    @classmethod
    def m_set_selected_keys(cls):
        """Set keys on the selected objects' transform channels at the current frame."""
        for obj in (o for o in bpy.context.selected_objects if o):
            for path in cls._CHANNELS:
                obj.keyframe_insert(data_path=path)

    @classmethod
    def m_unset_selected_keys(cls):
        """Remove keys on the selected objects' transform channels at the current frame."""
        for obj in (o for o in bpy.context.selected_objects if o):
            for path in cls._CHANNELS:
                try:
                    obj.keyframe_delete(data_path=path)
                except RuntimeError:
                    pass  # no key on this channel at the current frame


# ====================================================================================
# Macro manager — parses the Maya-format spec and registers Blender keymap items
# ====================================================================================
class MacroManager:
    """Register ``m_*`` macros to Blender hotkeys from the same string spec Maya uses."""

    _KEYMAPS = []  # (keymap, keymap_item) pairs we added, for clean removal

    # Maya key token -> Blender keymap ``type`` enum.
    _DIGITS = {
        "0": "ZERO", "1": "ONE", "2": "TWO", "3": "THREE", "4": "FOUR",
        "5": "FIVE", "6": "SIX", "7": "SEVEN", "8": "EIGHT", "9": "NINE",
    }
    _SPECIAL = {
        "up": "UP_ARROW", "down": "DOWN_ARROW", "left": "LEFT_ARROW", "right": "RIGHT_ARROW",
        "home": "HOME", "end": "END", "page_up": "PAGE_UP", "page_down": "PAGE_DOWN",
        "insert": "INSERT", "return": "RET", "enter": "RET", "space": "SPACE",
        "tab": "TAB", "delete": "DEL", "backspace": "BACK_SPACE",
    }
    # Maya modifier token -> keymap_items.new kwarg.
    _MODIFIERS = {"ctl": "ctrl", "alt": "alt", "sht": "shift"}

    # Keymaps each macro is bound into. The generic ``3D View`` (space) keymap is evaluated
    # *after* the mode keymaps, so a default bound in ``Object Mode`` / ``Mesh`` (e.g. ``1``,
    # ``f``, ``ctl+m``, ``g``) shadows a ``3D View`` item — the macro would never fire. Adding the
    # item to those mode keymaps in the **addon** keyconfig overrides the default for that key
    # (addon items win over the default config for the same keymap). ``3D View`` is kept so the
    # macro also covers other modes and beats the global ``Screen`` keymap.
    _KEYMAP_TARGETS = (
        ("3D View", "VIEW_3D"),
        ("Object Mode", "EMPTY"),
        ("Mesh", "EMPTY"),
    )

    @classmethod
    def set_macros(cls, *args):
        """Register a macro per spec string (``"m_name, key=1, cat=Display"``). Idempotent —
        existing macro keymaps are cleared first so a Reload Scripts doesn't duplicate them."""
        cls.remove_macros()
        cls._ensure_operator()
        for spec in args:
            cls.call_with_input(cls.set_macro, spec)

    @staticmethod
    def call_with_input(func, input_string):
        """Parse ``"arg, key=val, ..."`` into positional/keyword args and call ``func``."""
        args, kwargs = [], {}
        for token in input_string.split(","):
            try:
                key, value = token.split("=")
                kwargs[key.strip()] = value.strip()
            except ValueError:
                args.append(token.strip())
        return func(*args, **kwargs)

    @classmethod
    def _blender_key(cls, token):
        """Translate a single Maya key token to a Blender keymap ``type``."""
        token = token.strip()
        if token in cls._DIGITS:
            return cls._DIGITS[token]
        low = token.lower()
        if low in cls._SPECIAL:
            return cls._SPECIAL[low]
        return token.upper()  # 'f' -> 'F', 'q' -> 'Q', 'F1' -> 'F1'

    @classmethod
    def set_macro(cls, name, key=None, cat=None, ann=None):
        """Bind macro ``name`` to ``key`` (e.g. ``"ctl+sht+i"``) across the target keymaps so it
        overrides the mode-specific defaults that would otherwise shadow it."""
        if not key or not hasattr(Macros, name):
            return
        mods = {"ctrl": False, "alt": False, "shift": False}
        key_token = key
        for part in key.split("+"):
            mod = cls._MODIFIERS.get(part.strip().lower())
            if mod:
                mods[mod] = True
            else:
                key_token = part.strip()
        key_type = cls._blender_key(key_token)

        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is None:  # headless / no addon keyconfig
            return
        for km_name, space in cls._KEYMAP_TARGETS:
            km = kc.keymaps.new(name=km_name, space_type=space)
            kmi = km.keymap_items.new("btk.macro", type=key_type, value="PRESS", **mods)
            kmi.properties.macro = name
            cls._KEYMAPS.append((km, kmi))

    @classmethod
    def remove_macros(cls):
        """Remove every keymap item this manager added (clean teardown / reload)."""
        for km, kmi in cls._KEYMAPS:
            try:
                km.keymap_items.remove(kmi)
            except (RuntimeError, ReferenceError):
                pass
        cls._KEYMAPS.clear()

    @staticmethod
    def _ensure_operator():
        """Register the dispatcher operator once per process (idempotent)."""
        if hasattr(bpy.types, "BTK_OT_macro"):
            return

        class BTK_OT_macro(bpy.types.Operator):
            """Run a blendertk macro by name (the keymap target for every macro hotkey)."""

            bl_idname = "btk.macro"
            bl_label = "blendertk Macro"
            bl_options = {"REGISTER", "UNDO"}
            macro: bpy.props.StringProperty(name="Macro", default="")

            def execute(self, context):
                func = getattr(Macros, self.macro, None)
                if func is None:
                    self.report({"WARNING"}, f"Unknown macro: {self.macro}")
                    return {"CANCELLED"}
                try:
                    func()
                except Exception as error:  # a bad macro must not break the keymap
                    self.report({"ERROR"}, f"{self.macro}: {error}")
                    return {"CANCELLED"}
                return {"FINISHED"}

        bpy.utils.register_class(BTK_OT_macro)


# ====================================================================================
class Macros(
    MacroManager,
    DisplayMacros,
    EditMacros,
    SelectionMacros,
    AnimationMacros,
    UiMacros,
):
    """Concrete macro holder — combines every macro mixin with the manager (mirror of mayatk)."""


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
