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
import inspect
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

try:
    import bpy
except ImportError:  # surface must resolve headless (btk.Macros) — bpy used only at runtime
    bpy = None
import pythontk as ptk

from blendertk.edit_utils._edit_utils import set_subdivision, _group_under_empty

# Read selection/active through the view-layer (window-independent): the macros run from the Qt
# event-pump timer context where bpy.context.selected_objects / active_object are empty (their
# screen-context requires bpy.context.window, which is None there). See _core_utils.selected_objects.
from blendertk.core_utils._core_utils import active_object, selected_objects


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
        sel = selected_objects()
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
        objs = [o for o in selected_objects() if o.type == "MESH"]
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
        active = active_object()
        framing_selection = bool(selected_objects()) or (
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
        obj = active_object()
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
        obj = active_object()
        if obj and obj.type == "MESH" and obj.mode == "EDIT":
            bpy.ops.mesh.remove_doubles(threshold=tolerance)
            return
        import bmesh

        for o in (m for m in selected_objects() if m.type == "MESH"):
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
        sel = selected_objects()
        if not sel:
            return
        _group_under_empty(sel, "group", center=True)


class SelectionMacros:
    """ """

    @staticmethod
    def m_object_selection():
        """Object selection mask — leave edit mode (object mode)."""
        obj = active_object()
        if obj and obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

    @staticmethod
    def _enter_edit_mesh_mode(select_mode):
        obj = active_object()
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
        obj = active_object()
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
        for obj in selected_objects():
            for path in cls._CHANNELS:
                obj.keyframe_insert(data_path=path)

    @classmethod
    def m_unset_selected_keys(cls):
        """Remove keys on the selected objects' transform channels at the current frame."""
        for obj in selected_objects():
            for path in cls._CHANNELS:
                try:
                    obj.keyframe_delete(data_path=path)
                except RuntimeError:
                    pass  # no key on this channel at the current frame


# ====================================================================================
# Macro manager — parses the Maya-format spec and registers Blender keymap items
# ====================================================================================
class MacroManager:
    """Register ``m_*`` macros to Blender hotkeys from the same string spec Maya uses.

    Also the management API behind the ``MacroManagerSlots`` panel
    (``blendertk.edit_utils.macro_manager``) — mirror of mayatk's
    ``MacroManager`` management surface (:meth:`list_available_macros`,
    :meth:`macro_category`, :meth:`get_current_bindings`, preset persistence,
    …), fully usable without the panel, same as upstream.

    Divergence from mayatk (by design): Maya's ``cmds.runTimeCommand`` /
    ``cmds.hotkey`` is a DCC-side registry that Maya itself persists across
    sessions, so :meth:`get_current_bindings` reads it live. Blender's addon
    keyconfig is **process-lifetime only** — it is wiped on restart and rebuilt
    by ``tentacle_startup.py`` calling :meth:`set_macros` again (see the module
    docstring), so here "live" bindings are read back from :attr:`_KEYMAPS`,
    the bookkeeping list every :meth:`set_macro` call populates, rather than by
    re-querying a DCC-side registry. Likewise a Blender keymap item has no
    first-class ``category`` slot the way ``runTimeCommand`` does, so
    :class:`BTK_OT_macro` carries a second ``category`` ``StringProperty`` as a
    pure data carrier (never read by :meth:`BTK_OT_macro.execute`) — the direct
    analogue of Maya's runtime-command category attribute.
    """

    _KEYMAPS = []  # (keymap, keymap_item) pairs we added, for clean removal

    MACRO_PREFIX = "m_"
    PRESET_NAME = "macro_manager"
    PRESET_PACKAGE = "blendertk"
    DEFAULT_PRESET = "default"

    # Source tokens that should keep a fixed casing in the humanized label
    # (e.g. ``m_toggle_UV_select_type`` -> "Toggle UV Select Type"). Tokens
    # already upper-case in the method name (``UV``) are preserved as-is.
    _LABEL_ACRONYMS = {"uv": "UV", "uvs": "UVs", "id": "ID", "ui": "UI", "3d": "3D"}

    # Modifier-order + Qt-modifier-name mapping are the ecosystem's shared, DCC-agnostic
    # hotkey-token convention (mirrored by mayatk's own macros.py) -- sourced from
    # ``ptk.HotkeyUtils`` (the SSoT) rather than duplicated here, so a fix to the
    # conversion logic (e.g. the meta/cmd modifier mapping) can't drift between copies.
    _MOD_ORDER = ptk.HotkeyUtils.MOD_ORDER
    _QT_MOD_MAP = ptk.HotkeyUtils.QT_MOD_MAP

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
    # Reverse of the above — Blender keymap ``type`` -> Maya-style key token —
    # used to read a live keymap item back into a "ctl+sht+i"-style string.
    _REV_DIGITS = {v: k for k, v in _DIGITS.items()}
    _REV_SPECIAL = {v: k for k, v in _SPECIAL.items()}
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
        overrides the mode-specific defaults that would otherwise shadow it. ``cat`` is stashed on
        the keymap item's own ``category`` property (see the class docstring) so it round-trips
        through :meth:`get_current_bindings` without a separate side-store.

        Idempotent per-macro: any keymap item(s) already registered for ``name`` are cleared
        first (mirrors mayatk's ``set_macro`` ``delete_existing=True`` guard), so re-binding the
        same macro — to the same chord or a different one — can never duplicate or leave a ghost
        keymap item behind."""
        if not key or not hasattr(Macros, name):
            return
        cls.clear_hotkey(name)
        ctl, alt, sht, key_token = cls._parse_key(key)
        mods = {"ctrl": ctl, "alt": alt, "shift": sht}
        key_type = cls._blender_key(key_token)

        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is None:  # headless / no addon keyconfig
            return
        for km_name, space in cls._KEYMAP_TARGETS:
            km = kc.keymaps.new(name=km_name, space_type=space)
            kmi = km.keymap_items.new("btk.macro", type=key_type, value="PRESS", **mods)
            kmi.properties.macro = name
            kmi.properties.category = cat or ""
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

    # ------------------------------------------------------------------
    # Management API (UI-agnostic — fully functional without the panel)
    # ------------------------------------------------------------------
    # Mirror of mayatk's ``MacroManager`` management surface: the single source of
    # truth for discovering, querying, applying, clearing, and persisting macro
    # bindings. Both the ``MacroManagerSlots`` panel and ``tentacle_startup.py``
    # are thin consumers; nothing here imports Qt. A *binding* is a
    # ``{"key": <maya-style token>, "cat": <category>}`` dict; a *binding set* maps
    # macro name -> binding and is exactly what a preset file stores.

    @classmethod
    def list_available_macros(cls) -> Dict[str, str]:
        """Discover every ``m_*`` macro callable, mapped to its annotation.

        Returns:
            ``{macro_name: first_docstring_line}`` sorted by name. Pure class
            introspection (no ``bpy``), so the panel can populate its table
            without a live Blender.
        """
        macros = {}
        for name in dir(cls):
            if not name.startswith(cls.MACRO_PREFIX):
                continue
            try:
                attr = inspect.getattr_static(cls, name)
            except AttributeError:
                continue
            func = attr.__func__ if isinstance(attr, (staticmethod, classmethod)) else attr
            if not callable(func):
                continue
            doc = (getattr(func, "__doc__", "") or "").strip()
            macros[name] = doc.split("\n")[0].strip() if doc else ""
        return dict(sorted(macros.items()))

    @classmethod
    def macro_label(cls, name: str) -> str:
        """Humanize a macro name for display, e.g. ``m_back_face_culling`` ->
        "Back Face Culling" (acronyms like ``UV`` / ``ID`` are preserved)."""
        return ptk.HotkeyUtils.humanize_label(name, prefix=cls.MACRO_PREFIX, acronyms=cls._LABEL_ACRONYMS)

    @classmethod
    def macro_category(cls, name: str) -> str:
        """Default category for a macro, derived from the ``*Macros`` mixin that
        defines it (e.g. a method on :class:`DisplayMacros` -> ``"Display"``).

        The defining mixin is the single source of truth for a macro's category,
        so every discoverable macro has a sensible default with no per-macro
        annotation to maintain. Returns ``""`` when *name* isn't defined on a
        ``*Macros`` mixin.
        """
        for klass in cls.__mro__:
            kname = klass.__name__
            if kname.endswith("Macros") and kname != "Macros" and name in vars(klass):
                stem = kname[: -len("Macros")]
                return cls._LABEL_ACRONYMS.get(stem.lower(), stem)
        return ""

    @classmethod
    def list_categories(cls) -> List[str]:
        """Sorted distinct default categories across all discoverable macros.

        Derived from the ``*Macros`` mixins (via :meth:`macro_category`) so the
        category set never drifts from the code organization — adding a new
        ``<Domain>Macros`` mixin contributes its category automatically.
        """
        cats = {cls.macro_category(name) for name in cls.list_available_macros()}
        cats.discard("")
        return sorted(cats)

    @classmethod
    def macro_help(cls, name: str) -> str:
        """Return a macro's full (dedented) docstring — the single source of
        truth for its UI tooltip. Empty string when the macro has no docstring."""
        try:
            attr = inspect.getattr_static(cls, name)
        except AttributeError:
            return ""
        func = attr.__func__ if isinstance(attr, (staticmethod, classmethod)) else attr
        return (inspect.getdoc(func) or "").strip()

    @classmethod
    def _live_keymap_bindings(cls) -> Dict[str, dict]:
        """``{macro_name: {"key", "cat"}}`` read back from :attr:`_KEYMAPS` — the
        keymap items THIS session has actually registered (see the class
        docstring for why that, not a DCC-side registry, is "live" here)."""
        bindings = {}
        for km, kmi in cls._KEYMAPS:
            try:
                name = kmi.properties.macro
            except ReferenceError:
                continue
            if not name or name in bindings:
                continue  # a macro is bound identically into every target keymap
            mods = []
            if kmi.ctrl:
                mods.append("ctl")
            if kmi.alt:
                mods.append("alt")
            if kmi.shift:
                mods.append("sht")
            token = cls._REV_DIGITS.get(kmi.type) or cls._REV_SPECIAL.get(kmi.type)
            if token is None:
                token = kmi.type.lower() if len(kmi.type) == 1 else kmi.type
            cat = getattr(kmi.properties, "category", "") or ""
            bindings[name] = {"key": "+".join(mods + [token]), "cat": cat}
        return bindings

    @classmethod
    def get_current_bindings(cls) -> Dict[str, dict]:
        """Return the *live* key + category for every available macro.

        Unbound macros report an empty ``key``; ``cat`` falls back to the
        mixin-derived default (:meth:`macro_category`) so it is never empty.
        """
        live = cls._live_keymap_bindings()
        bindings = {}
        for name in cls.list_available_macros():
            spec = live.get(name, {})
            bindings[name] = {
                "key": spec.get("key", ""),
                "cat": spec.get("cat") or cls.macro_category(name),
            }
        return bindings

    @classmethod
    def apply_bindings(cls, bindings: Dict[str, dict]) -> None:
        """Apply a binding set ``{name: {"key", "cat"}}``.

        An entry with a falsy ``key`` clears that macro's hotkey instead of
        setting one. :meth:`set_macro` remains the single low-level applier.
        """
        for name, spec in (bindings or {}).items():
            if not isinstance(spec, dict):
                continue
            key = spec.get("key")
            cat = spec.get("cat")
            if not key:
                cls.clear_hotkey(name)
                continue
            cls.set_macro(name, key=key, cat=cat)

    @classmethod
    def clear_hotkey(cls, name: str, key: Optional[str] = None) -> None:
        """Unbind ``name``'s hotkey across every keymap it was registered into
        (``key`` is accepted for signature parity with mayatk but unused —
        Blender's keymap items are found and removed by macro name, not key)."""
        keep = []
        for km, kmi in cls._KEYMAPS:
            try:
                match = kmi.properties.macro == name
            except ReferenceError:
                continue
            if match:
                try:
                    km.keymap_items.remove(kmi)
                except (RuntimeError, ReferenceError):
                    pass
            else:
                keep.append((km, kmi))
        cls._KEYMAPS = keep

    @classmethod
    def find_conflicts(cls, bindings: Dict[str, dict]) -> Dict[str, List[str]]:
        """Return ``{normalized_key: [macro, ...]}`` for keys bound more than once."""
        by_key = defaultdict(list)
        for name, spec in (bindings or {}).items():
            key = spec.get("key") if isinstance(spec, dict) else None
            if key:
                by_key[cls._normalize_key(key)].append(name)
        return {k: v for k, v in by_key.items() if len(v) > 1}

    # --- key-format helpers (pure string, bpy-free -- delegate to the shared,
    # DCC-agnostic ptk.HotkeyUtils SSoT rather than reimplementing) -------

    @staticmethod
    def _parse_key(key: str) -> Tuple[bool, bool, bool, str]:
        """Split a Maya-style key token into ``(ctl, alt, sht, key)``.

        Modifiers (``ctl``/``alt``/``sht``) are matched case-insensitively; the
        remaining token is the key itself (e.g. ``"i"``, ``"F3"``).
        """
        return ptk.HotkeyUtils.parse_key(key)

    @classmethod
    def _normalize_key(cls, key: str) -> str:
        """Canonical form of a key token (sorted modifiers, key folded through the
        same case-normalization :meth:`_blender_key` applies at registration).

        Folding through :meth:`_blender_key` (rather than only lowercasing
        single-character tokens) keeps this comparison in lockstep with what
        actually collides in the Blender keymap: two tokens that differ only in
        case — including multi-character ones like ``"f3"``/``"F3"`` or
        ``"up"``/``"UP"`` — resolve to the identical Blender ``type`` enum at
        registration, so they must also normalize identically here or
        :meth:`find_conflicts` misses a real hotkey collision."""
        ctl, alt, sht, k = cls._parse_key(key)
        present = {"ctl": ctl, "alt": alt, "sht": sht}
        mods = [m for m in cls._MOD_ORDER if present[m]]
        k = cls._blender_key(k).lower() if k else k
        return "+".join(mods + [k])

    @classmethod
    def qt_sequence_to_maya_key(cls, sequence: str) -> str:
        """Convert a Qt key-sequence string (``"Ctrl+Shift+I"``) to the
        Maya-style token :meth:`set_macro` accepts.

        Returns ``""`` when *sequence* carries no non-modifier key.
        """
        return ptk.HotkeyUtils.qt_sequence_to_key(sequence)

    @classmethod
    def maya_key_to_qt_sequence(cls, key: str) -> str:
        """Convert a Maya-style key token (``"ctl+sht+i"``) to a Qt key-sequence
        string for display (``"Ctrl+Shift+I"``)."""
        return ptk.HotkeyUtils.key_to_qt_sequence(key)

    # --- preset persistence (PresetStore-backed, bpy-free) -------------

    @classmethod
    def _preset_store(cls) -> ptk.PresetStore:
        """Two-tier store: shipped ``macro_manager/presets`` + a writable user tier.

        Resolves to the same files the panel's ``uitk.PresetManager`` uses
        (relative ``preset_dir="blendertk/macro_manager"``), so headless and
        panel-driven usage share one source of truth.
        """
        builtin_dir = os.path.join(os.path.dirname(__file__), "macro_manager", "presets")
        return ptk.PresetStore(cls.PRESET_NAME, package=cls.PRESET_PACKAGE, builtin_dir=builtin_dir)

    @classmethod
    def list_presets(cls) -> List[str]:
        """Return all preset names (built-in + user, user shadows built-in)."""
        return cls._preset_store().list()

    @classmethod
    def load_preset(cls, name: str) -> Dict[str, dict]:
        """Return the binding set stored under *name* (``_meta`` stripped)."""
        data = cls._preset_store().load(name)
        return {k: v for k, v in data.items() if k != "_meta"}

    @classmethod
    def save_preset(cls, name: str, bindings: Optional[Dict[str, dict]] = None) -> str:
        """Save *bindings* (default: the current bindings) as user preset *name*.

        Sets *name* as the active preset and returns the written path (as str).
        """
        if bindings is None:
            bindings = cls.get_current_bindings()
        data = {"_meta": {"version": 1}}
        data.update(bindings)
        store = cls._preset_store()
        path = store.save(name, data)
        store.active = name
        return str(path)

    @classmethod
    def delete_preset(cls, name: str) -> bool:
        """Delete a *user* preset (built-ins are read-only). Returns success."""
        return cls._preset_store().delete(name)

    @classmethod
    def get_active_preset(cls) -> Optional[str]:
        """The last-selected/applied preset name, or ``None``."""
        return cls._preset_store().active

    @classmethod
    def set_active_preset(cls, name: Optional[str]) -> None:
        """Set (or clear, with ``None``) the active-preset pointer."""
        cls._preset_store().active = name

    @classmethod
    def apply_saved_macros(cls, name: Optional[str] = None) -> None:
        """Apply a saved preset/template's bindings on demand.

        Resolution order: explicit *name* -> the persisted active preset ->
        the shipped ``default`` template. A stale active pointer falls back to
        ``default``. Loading an explicit *name* makes it active. This is what
        ``tentacle_startup.py`` calls at Blender launch, exactly as mayatk's
        ``userSetup.py`` calls the Maya macros — Blender's keymap edits don't
        outlive the process on their own (see the class docstring), so this
        call is what makes bindings "stick" across sessions rather than the
        keyconfig itself.
        """
        store = cls._preset_store()
        target = name or store.active or cls.DEFAULT_PRESET
        try:
            bindings = cls.load_preset(target)
        except (KeyError, ValueError, OSError):
            if target == cls.DEFAULT_PRESET:
                return
            try:
                bindings = cls.load_preset(cls.DEFAULT_PRESET)
            except (KeyError, ValueError, OSError):
                return
        cls.apply_bindings(bindings)
        if name:
            store.active = name

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
            # Data carrier only — never read by execute(); see the MacroManager
            # class docstring (the Blender analogue of runTimeCommand's category).
            category: bpy.props.StringProperty(name="Category", default="")

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
