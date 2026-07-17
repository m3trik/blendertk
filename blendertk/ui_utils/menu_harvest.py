# !/usr/bin/python
# coding=utf-8
"""Harvest a native Blender menu into a live ``QMenu`` — the Blender half of Maya's wrap.

Maya's menus are Qt: ``MayaNativeMenus`` lifts the real ``QAction`` rows off the menu bar
and re-hosts them. Blender draws its menus in OpenGL — there are no widgets to lift — but
every Blender menu IS a Python class whose ``draw(self, context)`` declares its items
against a ``UILayout``. So the faithful equivalent is to execute that ``draw`` against a
duck-typed *recorder* layout and rebuild the recording as ``QAction``s: always the live
menu definition (add-ons and mode included), zero hand-authored content.

Item execution mirrors how Blender's own menu rows behave: operators run via ``bpy.ops``
(``INVOKE_DEFAULT``) under a VIEW_3D context override, deferred one timer tick so the Qt
click fully unwinds first; boolean props become checkable actions. Rows whose operator
``poll()`` fails are greyed — exactly Blender's presentation.

Known, accepted fidelity gaps: asset-catalog entries (``layout.template_*``) and other
non-item layout calls are skipped via the recorder's swallow-all fallback, and icons /
shortcut hints are not reproduced.

Import-clean: no ``bpy`` / Qt at module top — ``bpy`` and ``qtpy`` are deferred into the
functions, and the Qt surface is just the ``QMenu`` instance handed in — so offscreen Qt
tests and headless Blender can both import it.
"""
import functools
import inspect
import types

# Menus legitimately recur across branches (e.g. VIEW3D_MT_snap appears under several
# parents) — the cycle guard is per recursion *path*, not global. Depth is a backstop.
_MAX_DEPTH = 8


class _OpProps:
    """Records attribute assignments on the object ``layout.operator`` returns.

    Menu draws configure operators as ``layout.operator("wm.url_open").url = ...`` —
    the assignments become the kwargs of the eventual ``bpy.ops`` call. **Macro**
    operators are configured through nested sub-operator groups
    (``props.MESH_OT_rip.use_fill = False`` in the edit-mesh Vertices menu), so an
    unset attribute read returns-and-records a nested recorder rather than raising —
    ``_plain_props`` later serializes the tree into the nested-dict form ``bpy.ops``
    macros take.
    """

    __slots__ = ("props",)

    def __init__(self):
        object.__setattr__(self, "props", {})

    def __setattr__(self, name, value):
        self.props[name] = value

    def __getattr__(self, name):
        value = self.props.get(name)
        if value is None and name not in self.props:
            value = _OpProps()
            self.props[name] = value
        return value


def _plain_props(rec):
    """An ``_OpProps`` tree as plain (possibly nested) dicts for the ``bpy.ops`` call.

    Empty nested groups — created by reads that never assigned — are dropped.
    """
    out = {}
    for key, value in rec.props.items():
        if isinstance(value, _OpProps):
            nested = _plain_props(value)
            if nested:
                out[key] = nested
        else:
            out[key] = value
    return out


class _Sink:
    """Swallows any call/attribute chain — the recorder's fallback for layout members
    that never yield menu rows (``template_*``, ``popover``, ``context_pointer_set`` …)."""

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        return self

    def __setattr__(self, name, value):
        pass


class _LayoutRecorder:
    """Duck-typed ``UILayout`` that records menu rows instead of drawing them.

    Sub-layouts (``row``/``column``/…) share the parent's item list; a sub-layout's
    ``enabled = False`` greys the rows recorded through it (Blender menus use this for
    contextually dead entries).
    """

    def __init__(self, items=None, enabled=True):
        self.__dict__["items"] = [] if items is None else items
        self.__dict__["_enabled"] = enabled

    # Absorb layout state sets (operator_context, active, scale_y, alignment, …);
    # only 'enabled' affects the recording.
    def __setattr__(self, name, value):
        if name == "enabled":
            self.__dict__["_enabled"] = bool(value)

    def __getattr__(self, name):
        return _Sink()

    def _child(self, *args, **kwargs):
        return _LayoutRecorder(self.items, self.__dict__["_enabled"])

    row = column = box = split = column_flow = grid_flow = menu_pie = _child

    def operator(self, operator, text=None, *args, **kwargs):
        rec = _OpProps()
        self.items.append(("operator", operator, text, self.__dict__["_enabled"], rec))
        return rec

    def menu(self, menu, text=None, *args, **kwargs):
        self.items.append(("menu", menu, text))

    def menu_contents(self, menu):
        self.items.append(("menu_contents", menu))

    def operator_menu_enum(self, operator, property, text=None, *args, **kwargs):
        self.items.append(("op_menu_enum", operator, property, text))

    def operator_enum(self, operator, property, *args, **kwargs):
        self.items.append(("op_enum", operator, property))

    def separator(self, *args, **kwargs):
        self.items.append(("separator",))

    def separator_spacer(self):
        self.items.append(("separator",))

    def label(self, text="", *args, **kwargs):
        if text:
            self.items.append(("label", text))

    def prop(self, data, property, text=None, *args, **kwargs):
        self.items.append(("prop", data, property, text, self.__dict__["_enabled"]))


class _MenuShim:
    """Stand-in ``self`` for an unbound ``Menu.draw`` call.

    Provides ``.layout`` and forwards everything else to the menu class — plain class
    attributes (``bl_label``) come back as-is, functions come back bound to this shim so
    ``self.draw_xyz(context)`` helper patterns keep working.
    """

    def __init__(self, menu_cls, layout):
        self.__dict__["_cls"] = menu_cls
        self.__dict__["layout"] = layout

    def __getattr__(self, name):
        cls = self.__dict__["_cls"]
        attr = getattr(cls, name)
        if isinstance(attr, types.FunctionType):
            # A staticmethod surfaces as a plain function too — it must NOT be
            # bound to the shim (a real instance wouldn't pass self either).
            if isinstance(inspect.getattr_static(cls, name, None), staticmethod):
                return attr
            return functools.partial(attr, self)
        return attr


def harvest_menu(idname):
    """Execute ``idname``'s ``draw`` against a recorder; return the recorded item list.

    Raises whatever the draw raises — mode-dependent draws (``VIEW3D_MT_edit_armature``)
    deref ``context.edit_object`` and fail outside their mode; the caller treats that as
    "this menu does not exist right now" and falls back.
    """
    import bpy

    menu_cls = getattr(bpy.types, idname)
    recorder = _LayoutRecorder()
    menu_cls.draw(_MenuShim(menu_cls, recorder), bpy.context)
    return recorder.items


def _op_callable(op_idname):
    """The ``bpy.ops`` callable for ``"module.operator"``, or None for a bad id."""
    import bpy

    mod, _, op = op_idname.partition(".")
    try:
        return getattr(getattr(bpy.ops, mod), op)
    except AttributeError:
        return None


def _op_label(fn, op_idname):
    try:
        return fn.get_rna_type().name or op_idname
    except Exception:
        return op_idname


def _op_poll(fn):
    """Whether the operator would run right now; unknowable polls stay clickable."""
    try:
        return bool(fn.poll())
    except Exception:
        return True


def invoke_operator(op_idname, props=None):
    """Run an operator one timer tick later, ``INVOKE_DEFAULT``, under a VIEW_3D override.

    Deferred so the Qt click that triggered it fully unwinds first (the same reason the
    old ``wm.call_menu`` path deferred). Errors surface as a native popup rather than a
    silent console traceback.
    """
    import bpy
    import blendertk as btk

    fn = _op_callable(op_idname)
    if fn is None:
        return

    def _run():
        try:
            ctx = btk.get_view3d_context()
            if ctx:
                with bpy.context.temp_override(**ctx):
                    fn("INVOKE_DEFAULT", **(props or {}))
            else:
                fn("INVOKE_DEFAULT", **(props or {}))
        except Exception as e:
            btk.popup_message(str(e), title=op_idname, icon="ERROR")
        return None  # one-shot

    bpy.app.timers.register(_run, first_interval=0.05)


def _set_prop_deferred(data, prop_name, value):
    """Deferred boolean-prop toggle (menu rows like *Auto Merge*); dead refs are ignored."""
    import bpy

    def _run():
        try:
            setattr(data, prop_name, value)
        except Exception:
            pass
        return None

    bpy.app.timers.register(_run, first_interval=0.05)


def refill_qmenu(qmenu, idname):
    """Rebuild ``qmenu``'s actions from a fresh harvest of ``idname``; return the row count.

    Runs the whole harvest (draws + polls) under one VIEW_3D override when a viewport
    exists, so mode/region-sensitive draws and ``poll()`` see the same context a real
    menu open would. When nothing is being edited, the active object is injected as
    ``edit_object`` — a few mode-scoped draws hard-deref it (``VIEW3D_MT_edit_armature``,
    ``VIEW3D_MT_edit_curve_ctrlpoints``) and any object satisfies the deref, so their
    menus open out of mode with ``poll()``-greyed rows (Maya's menus are likewise always
    openable) instead of failing. Called on every show — content is mode- and add-on-live,
    and data refs recorded into prop toggles are always fresh.
    """
    import bpy
    import blendertk as btk

    ctx = btk.get_view3d_context()
    if ctx:
        try:
            editing = bpy.context.edit_object
        except AttributeError:  # windowless (Qt-timer) context
            editing = None
        if editing is None:
            active = btk.active_object()
            if active is not None:
                ctx = dict(ctx)
                ctx["edit_object"] = active
        with bpy.context.temp_override(**ctx):
            return _refill(qmenu, idname)
    return _refill(qmenu, idname)


def _refill(qmenu, idname):
    from qtpy import QtCore, QtWidgets

    # Harvest BEFORE clearing so a failing draw leaves the previous content intact.
    items = harvest_menu(idname)
    # clear() removes actions but never deletes child QMenus created by addMenu —
    # without this, every refill leaks the previous show's submenu tree.
    old_submenus = qmenu.findChildren(
        QtWidgets.QMenu, options=QtCore.Qt.FindDirectChildrenOnly
    )
    qmenu.clear()
    for submenu in old_submenus:
        submenu.deleteLater()
    return _fill(qmenu, items, depth=0, path={idname})


def _fill(qmenu, items, depth, path):
    count = 0
    for item in items:
        kind = item[0]
        if kind == "separator":
            qmenu.addSeparator()
        elif kind == "label":
            action = qmenu.addAction(item[1])
            action.setEnabled(False)
        elif kind == "operator":
            count += _add_operator(qmenu, item)
        elif kind == "menu":
            count += _add_submenu(qmenu, item[1], item[2], depth, path)
        elif kind == "menu_contents":
            count += _add_menu_contents(qmenu, item[1], depth, path)
        elif kind == "op_menu_enum":
            count += _add_enum_submenu(qmenu, item[1], item[2], item[3])
        elif kind == "op_enum":
            count += _add_enum_inline(qmenu, item[1], item[2])
        elif kind == "prop":
            count += _add_prop(qmenu, item)
    return count


def _add_operator(qmenu, item):
    _, op_idname, text, enabled, rec = item
    fn = _op_callable(op_idname)
    if fn is None:
        return 0
    action = qmenu.addAction(text or _op_label(fn, op_idname))
    action.setEnabled(enabled and _op_poll(fn))
    action.triggered.connect(
        functools.partial(_trigger_op, op_idname, _plain_props(rec))
    )
    return 1


def _trigger_op(op_idname, props, *_args):
    invoke_operator(op_idname, props)


def _add_submenu(qmenu, sub_idname, text, depth, path):
    """A nested native menu — harvested eagerly; a failing child draw becomes one
    disabled row instead of sinking the parent."""
    import bpy

    menu_cls = getattr(bpy.types, sub_idname, None)
    if menu_cls is None:
        return 0
    label = text or getattr(menu_cls, "bl_label", sub_idname)
    if sub_idname in path or depth >= _MAX_DEPTH:
        return 0
    try:
        items = harvest_menu(sub_idname)
    except Exception:
        action = qmenu.addAction(label)
        action.setEnabled(False)
        return 1
    submenu = qmenu.addMenu(label)
    count = _fill(submenu, items, depth + 1, path | {sub_idname})
    if not count:
        submenu.menuAction().setEnabled(False)
    return 1


def _add_menu_contents(qmenu, sub_idname, depth, path):
    """``layout.menu_contents`` inlines another menu's rows at this level."""
    if sub_idname in path or depth >= _MAX_DEPTH:
        return 0
    try:
        items = harvest_menu(sub_idname)
    except Exception:
        return 0
    return _fill(qmenu, items, depth + 1, path | {sub_idname})


def _enum_items(fn, prop_name):
    try:
        prop = fn.get_rna_type().properties[prop_name]
        return [(e.identifier, e.name) for e in prop.enum_items]
    except Exception:
        return []


def _add_enum_submenu(qmenu, op_idname, prop_name, text):
    fn = _op_callable(op_idname)
    if fn is None:
        return 0
    items = _enum_items(fn, prop_name)
    if not items:
        # Dynamic (callback-driven) enum — fall back to a plain invoke; Blender
        # presents its own chooser for ops that need one.
        action = qmenu.addAction(text or _op_label(fn, op_idname))
        action.setEnabled(_op_poll(fn))
        action.triggered.connect(functools.partial(_trigger_op, op_idname, {}))
        return 1
    submenu = qmenu.addMenu(text or _op_label(fn, op_idname))
    enabled = _op_poll(fn)
    for identifier, name in items:
        action = submenu.addAction(name)
        action.setEnabled(enabled)
        action.triggered.connect(
            functools.partial(_trigger_op, op_idname, {prop_name: identifier})
        )
    if not enabled:
        submenu.menuAction().setEnabled(False)
    return 1


def _add_enum_inline(qmenu, op_idname, prop_name):
    fn = _op_callable(op_idname)
    if fn is None:
        return 0
    enabled = _op_poll(fn)
    count = 0
    for identifier, name in _enum_items(fn, prop_name):
        action = qmenu.addAction(name)
        action.setEnabled(enabled)
        action.triggered.connect(
            functools.partial(_trigger_op, op_idname, {prop_name: identifier})
        )
        count += 1
    return count


def _add_prop(qmenu, item):
    """Boolean props become checkable rows; other prop types have no menu-row analogue."""
    _, data, prop_name, text, enabled = item
    try:
        rna_prop = data.bl_rna.properties[prop_name]
        if rna_prop.type != "BOOLEAN" or getattr(rna_prop, "is_array", False):
            return 0
        value = bool(getattr(data, prop_name))
        label = text or rna_prop.name
    except Exception:
        return 0
    action = qmenu.addAction(label)
    action.setCheckable(True)
    action.setChecked(value)
    action.setEnabled(enabled)
    action.triggered.connect(functools.partial(_toggle_prop, data, prop_name))
    return 1


def _toggle_prop(data, prop_name, checked=False, *_args):
    _set_prop_deferred(data, prop_name, bool(checked))
