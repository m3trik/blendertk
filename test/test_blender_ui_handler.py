"""BlenderUiHandler discovery test — proves the co-located tool panels live in blendertk and
are served by the handler (the mayatk/MayaUiHandler split, mirrored for Blender).

Unlike the other suites this one needs **Qt, not bpy** (it loads ``.ui`` files + wires Slots,
none of which touch Blender). So it is meant to run under the workspace ``.venv`` (PySide6)::

    .venv\\Scripts\\python.exe blendertk/test/test_blender_ui_handler.py

When launched by the Blender harness (``--background --factory-startup``, which ships no Qt) it
detects the missing binding and SKIPS with a PASS sentinel, so ``Run-Tests.ps1`` stays green.
"""

import sys
import os
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")


def _sandbox_qsettings():
    """Keep this run off the real QSettings store (uitk/test/conftest.py owns the shim).

    Loading real panels through Switchboard otherwise reads AND writes the
    developer's live ``uitk\\shared`` state — a prior run's toggled Compact
    View, for example, comes back as this run's load-time default.  Import
    the sandbox from uitk's conftest (monorepo checkout only; a pip-installed
    uitk ships no test dir, in which case this harness shouldn't run anyway).
    """
    import importlib.util

    conftest = os.path.join(MONO, "uitk", "test", "conftest.py")
    if not os.path.isfile(conftest):
        raise SystemExit(
            "SKIP test_blender_ui_handler (no uitk/test/conftest.py — refusing "
            "to run against the live QSettings store)"
        )
    spec = importlib.util.spec_from_file_location("_uitk_conftest", conftest)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # activates the QSettings sandbox at import time


# Co-located tool panels that BlenderUiHandler must discover from the blendertk package.
PANELS = [
    "rizom_bridge",
    "maya_bridge",
    "marmoset_bridge",
    "substance_bridge",
    "shot_sequencer",
]

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


# This suite is a .venv target: it needs Qt, a real mayapy for the reference-manager
# interop check, and the optional unitytk. Gate on "am I inside Blender?", NOT on
# "is Qt missing?" — Blender headless normally ships no binding, but tentacle/blendertk
# PROVISION PySide6 into Blender's addons/modules on demand, and once that has happened
# the old qtpy probe stops skipping and the suite half-runs under the Blender harness
# (measured 2026-08-24: 198/200, the 2 failures being the mayapy and unitytk checks).
try:
    import bpy  # noqa: F401

    _IN_BLENDER = True
except Exception:
    _IN_BLENDER = False

if _IN_BLENDER:
    print(
        "SKIP test_blender_ui_handler (Blender harness — run under the workspace .venv)"
    )
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

try:
    from qtpy import QtWidgets  # noqa: F401
except Exception:
    print(
        "SKIP test_blender_ui_handler (no Qt binding — run under the workspace .venv)"
    )
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

# After the Qt guard (the conftest itself imports qtpy), before the first
# Switchboard/QSettings construction.
_sandbox_qsettings()

try:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from uitk import Switchboard
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    sb = Switchboard()
    handler = BlenderUiHandler(switchboard=sb)

    # sb.tk: the host a Slots class written for either host calls (mirror of
    # MayaUiHandler), so a panel this handler stands up alone finds Blender;
    # one a caller named already stays.
    import blendertk

    check("handler names its toolkit on the switchboard", sb.tk is blendertk)
    named = Switchboard()
    named.tk = _marker = object()
    BlenderUiHandler(switchboard=named)
    check("a toolkit the caller named stays", named.tk is _marker)

    # NodeIcons.audio_icon: an audio row's icon (Blender's speaker) read from the
    # type alone -- a VSE strip has no object to read it off. The shared Shot
    # Manifest panel asks either host for it by this one name.
    from blendertk.ui_utils.node_icons import NodeIcons

    _audio = NodeIcons.audio_icon()
    check(
        "NodeIcons.audio_icon is the speaker icon",
        _audio is not None and not _audio.isNull(),
        repr(_audio),
    )
    from uitk import PresetEditor

    check(
        "handler labels blendertk's preset folder (uitk names no host)",
        PresetEditor.APP_LABELS.get("blendertk") == "Blender",
    )

    # 0. Constructing the handler must register blendertk's dispatch_log_link into
    #    uitk's dependency-inverted log-link registry (so uitk never imports
    #    blendertk). A POSITIVE check: the registration is wrapped in try/except
    #    in __init__, so a wrong import path would silently skip it and this is
    #    the only thing that would catch that drift.
    from uitk.bridge.slots import _BridgeSlotsInternal
    from blendertk.ui_utils._ui_utils import UiUtils

    check(
        "BlenderUiHandler registers dispatch_log_link with uitk",
        UiUtils.dispatch_log_link in _BridgeSlotsInternal._LOG_LINK_HANDLERS,
    )

    # 0b. ...and point ptk.Deprecation.sink at the console, once: a
    #     DeprecationWarning is hidden outside __main__, so without it no artist
    #     sees a deprecated call before it goes (mirror of MayaUiHandler). A sink
    #     a host set first is left alone.
    import contextlib
    import io

    import pythontk as ptk

    check(
        "BlenderUiHandler installs the console deprecation sink",
        ptk.Deprecation.sink is BlenderUiHandler._print_deprecation,
        f"sink={ptk.Deprecation.sink!r}",
    )
    _printed = io.StringIO()
    with contextlib.redirect_stdout(_printed):
        ptk.Deprecation.sink("X is deprecated and will be removed in blendertk 9.9.9")
    check(
        "the console sink prints the notice as a Warning line",
        _printed.getvalue().strip()
        == "Warning: X is deprecated and will be removed in blendertk 9.9.9",
        repr(_printed.getvalue()),
    )

    def _host_sink(message):
        pass

    ptk.Deprecation.sink = _host_sink
    check(
        "a sink a host set first is left alone",
        BlenderUiHandler._install_deprecation_sink() is False
        and ptk.Deprecation.sink is _host_sink,
    )
    ptk.Deprecation.sink = None

    # 0c. A handler that builds its own switchboard (the startup-script path) is
    #     that switchboard's "ui" handler -- a bare Switchboard() registers a
    #     plain UiHandler there, and the UI Browser's rows (and their copied
    #     launch code) ran through the generic base. Construction is stubbed:
    #     only the wiring is under test, not a second package scan.
    from unittest import mock

    import uitk
    from uitk.handlers.ui_handler import UiHandler

    _built = {}

    class _RecordingSwitchboard:
        def __init__(self, **kwargs):
            _built.update(kwargs)

    def _bare_init(self, switchboard=None, **_kwargs):
        self.sb = switchboard

    # The handler above holds the (cls, None) singleton slot; a fresh slot makes
    # this a first construction instead of returning that initialized handler.
    _saved_instances = dict(BlenderUiHandler._instances)
    BlenderUiHandler._instances.clear()
    try:
        # The post-init wiring touches the switchboard and global sinks: stubbed.
        _inert = staticmethod(lambda *a, **k: False)
        with (
            mock.patch.object(uitk, "Switchboard", _RecordingSwitchboard),
            mock.patch.object(UiHandler, "__init__", _bare_init),
            mock.patch.multiple(
                BlenderUiHandler,
                _install_deprecation_sink=_inert,
                _install_record_path_rebase=_inert,
                _install_articulated_rig_solve=_inert,
                _register_native_menu_proxies=lambda self: None,
            ),
        ):
            _booted = BlenderUiHandler()
        check(
            "a self-built switchboard gets this handler as its 'ui' handler",
            (_built.get("handlers") or {}).get("ui") is _booted,
            f"handlers={_built.get('handlers')!r}",
        )
    finally:
        BlenderUiHandler._instances.clear()
        BlenderUiHandler._instances.update(_saved_instances)

    # 1. The handler's recursive scan of the blendertk package registers exactly the
    #    co-located tool panels listed in PANELS (and nothing spurious) — the core
    #    architectural guarantee.
    registry = sb.registry.ui_registry
    registered = set(registry.get("filename") or [])
    for panel in PANELS:
        check(f"panel discovered: {panel}", panel in registered, "in ui_registry")
    check(
        "no spurious panels registered",
        registered == set(PANELS),
        f"{sorted(registered)}",
    )

    # 2. Loading a panel whose Slots.__init__ is bpy-free wires the co-located <Tool>Slots
    #    FROM blendertk (not tentacle) and stamps the blendertk source tag (hide-button style).
    for panel, slot_cls in (
        ("rizom_bridge", "RizomBridgeSlots"),  # engine/slots init is bpy-free
        ("maya_bridge", "MayaBridgeSlots"),  # engine/slots init is bpy-free
        (
            "marmoset_bridge",
            "MarmosetBridgeSlots",
        ),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        (
            "substance_bridge",
            "SubstanceBridgeSlots",
        ),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
    ):
        ui = sb.get_ui(panel)
        check(f"{panel} ui loads", ui is not None)
        slot = getattr(ui, "slots", None)
        check(
            f"{panel} wires {slot_cls} from blendertk",
            slot is not None
            and type(slot).__name__ == slot_cls
            and type(slot).__module__.startswith("blendertk."),
            type(slot).__module__ if slot else "no slot",
        )
        check(
            f"{panel} carries the blendertk source tag",
            hasattr(ui, "has_tags") and ui.has_tags(["blendertk"]),
        )

    # 2b. The "blendertk" -> hide rule is a DEFAULT declared through
    #     UiHandler.default_persistence, not a hardcoded header_buttons swap in
    #     apply_styles. That distinction is the whole point: a hardcoded set is a
    #     value the UI Browser's window-persistence override has no way to outrank
    #     (which is why the setting had no effect on marking-menu panels).
    from uitk.handlers.ui_handler import UiHandler as _UiHandler

    check(
        "blendertk-tagged panels default to sticky (hide button)",
        handler.default_persistence(sb.get_ui("rizom_bridge"))
        == _UiHandler.PERSISTENCE_STICKY,
    )
    check(
        "BlenderUiHandler does not override apply_styles",
        BlenderUiHandler.apply_styles is _UiHandler.apply_styles,
        "a hardcoded header_buttons swap would outrank the user's stored choice",
    )
    check(
        "a UI Browser override outranks the blendertk default",
        handler.resolve_persistence(sb.get_ui("rizom_bridge"), context_default=None)
        == _UiHandler.PERSISTENCE_STICKY
        and (
            handler.set_persistence_override("rizom_bridge", _UiHandler.PERSISTENCE_TRANSIENT)
            or handler.resolve_persistence(sb.get_ui("rizom_bridge"))
        )
        == _UiHandler.PERSISTENCE_TRANSIENT,
    )
    handler.set_persistence_override("rizom_bridge", None)  # leave the store clean

    # maya_bridge: render_template substitutes the FBX path + params (Qt path; needs no bpy, so it
    # belongs here rather than in the headless test_maya_bridge harness which lacks Qt).
    from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge
    from blendertk.env_utils.maya_bridge import parameters as _mb_params

    rendered = MayaBridge(maya_path="C:/fake/maya.exe").render_template(
        "import", r"C:\t\x.fbx", _mb_params.Parameters.defaults()
    )
    check(
        "maya_bridge render_template substitutes path + params",
        "FBXImport" in rendered
        and 'FBX_PATH = r"C:/t/x.fbx"' in rendered
        and "__" not in rendered,
    )
    # The Rebuild Shader choice is Maya's shader vocabulary (mayatk.GameShader's own
    # values), owned here by the bridge that targets Maya -- uitk carries no host's.
    shader = _mb_params.PARAMS["SHADER_TYPE"]
    check(
        "maya_bridge Rebuild Shader offers GameShader's own values, Stingray first",
        [value for _label, value in shader.choices]
        == ["stingray", "standard_surface", "open_pbr"]
        and shader.default == "stingray",
        f"{shader.choices} default={shader.default}",
    )

    # unity_bridge: params_defaults() (Qt path via uitk.bridge.AttributeSpec; needs no bpy, so it
    # belongs here rather than in the headless test_unity_bridge harness which lacks Qt). The
    # panel's mode combo is tentacle's shared tool's (test_unity_bridge_panel).
    from blendertk.env_utils.unity_bridge._unity_bridge import UnityBridge

    _ub_defaults = UnityBridge().params_defaults()
    check(
        "unity_bridge params_defaults (Assets subdir / no-launch / scope / version)",
        _ub_defaults.get("ASSETS_SUBDIR") == "Imported"
        and _ub_defaults.get("LAUNCH_MODE") == ""
        and _ub_defaults.get("INCLUDE_MATERIALS") is True
        and _ub_defaults.get("SCOPE") == "selected"
        and _ub_defaults.get("UNITY_VERSION") == "",
        f"{_ub_defaults}",
    )

    # Macro Manager: the bespoke panel was retired — the UI is now the unified uitk
    # ShortcutEditor over the bpy-free Macros controller (btk.Macros.show_editor, the
    # mirror of mtk.Macros.show_editor). Building it is Qt-only: list_available_macros /
    # macro_category are pure introspection and the live keymap bookkeeping is empty
    # without bpy — so the editor populates for real under the offscreen .venv.
    from blendertk.edit_utils.macros import Macros

    med = Macros.show_editor(parent=None)
    try:
        med._set_show_hidden(False)
        med._set_show_all(True)
        med_rows = [
            med.table.item(r, 0).text()
            for r in range(med.table.rowCount())
            if med.table.item(r, 0) and med.table.columnSpan(r, 0) == 1
        ]
        check(
            "macro editor lists every discoverable macro (bpy-free)",
            len(med_rows) == len(Macros.list_available_macros())
            and "Back Face Culling" in med_rows,
            f"{sorted(med_rows)}",
        )
        check(
            "macro editor branded + grouped by category (scope column dropped)",
            med.windowTitle() == "Macro Manager"
            and med.table.horizontalHeaderItem(med.COL_UI).text() == "Category"
            and med.table.isColumnHidden(med.COL_SCOPE)
            and [med.cmb_ui.itemText(i) for i in range(med.cmb_ui.count())]
            == Macros.editor_categories(),
        )
        check(
            "macro editor preset row fronts the blendertk macro store ('default' listed)",
            med._preset_mgr is not None and "default" in med._list_presets(),
        )
    finally:
        med.close()
        Macros._editor = None

except Exception as e:
    traceback.print_exc()
    check("handler discovery raised", False, repr(e))

# ---------------------------------------------------------------------------
# Shared Scope parameter (needs Qt: the slot + parameter modules import
# uitk.bridge, so this cannot live in the headless-Blender bridge suite).
# ---------------------------------------------------------------------------
try:
    from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase
    from blendertk.env_utils.maya_bridge.maya_bridge_slots import MayaBridgeSlots
    from blendertk.mat_utils.marmoset_bridge.marmoset_bridge_slots import (
        MarmosetBridgeSlots,
    )
    from blendertk.mat_utils.substance_bridge.substance_bridge_slots import (
        SubstanceBridgeSlots,
    )
    from blendertk.uv_utils.rizom_bridge.rizom_bridge_slots import RizomBridgeSlots

    # Every bridge slot must inherit the ONE resolver. MayaBridgeSlots and
    # RizomBridgeSlots subclassed uitk's BridgeSlotsBase directly, which resolves
    # every scope to an EMPTY set -- the send then reports "nothing selected"
    # forever. Pin the inheritance, not merely the presence of the parameter.
    for _cls in (
        MayaBridgeSlots,
        MarmosetBridgeSlots,
        SubstanceBridgeSlots,
        RizomBridgeSlots,
    ):
        check(
            f"{_cls.__name__} inherits the shared scope resolver",
            _cls.resolve_scope_objects is BlenderBridgeSlotsBase.resolve_scope_objects,
        )

    import importlib as _il

    _scope_specs = []
    for _mod_name in (
        "blendertk.env_utils.maya_bridge.parameters",
        "blendertk.env_utils.unity_bridge.parameters",
        "blendertk.mat_utils.marmoset_bridge.parameters",
        "blendertk.mat_utils.substance_bridge.parameters",
        "blendertk.uv_utils.rizom_bridge.parameters",
    ):
        _spec = _il.import_module(_mod_name).PARAMS.get("SCOPE")
        _scope_specs.append(_spec)
        check(
            f"{_mod_name.split('.')[-2]} registers SCOPE (selected default)",
            _spec is not None
            and _spec.default == "selected"
            and [v for _l, v in _spec.choices] == ["selected", "all", "visible"],
        )
    # A shared mutable spec would let one bridge's tweak leak into all of them.
    check(
        "each bridge holds its OWN SCOPE spec instance",
        len({id(sp) for sp in _scope_specs if sp is not None})
        == len([sp for sp in _scope_specs if sp is not None]),
    )
except Exception as e:
    traceback.print_exc()
    check("scope parameter wiring raised", False, repr(e))

# ---------------------------------------------------------------------------
# Scope precedence, against a stand-in bpy: WHICH read answers each Scope word,
# and what happens when a read cannot answer (pythontk.HandoffScope's rule; the
# reads themselves are covered headless by test_unity_bridge / the bridge suite).
# ---------------------------------------------------------------------------
try:
    import types as _types
    from unittest import mock as _mock

    import blendertk as _btk
    from blendertk.env_utils.handoff_export import BlenderExportMixin
    from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase

    _mesh = _types.SimpleNamespace(type="MESH", name="mesh")
    _empty = _types.SimpleNamespace(type="EMPTY", name="empty")
    _fake_bpy = _types.SimpleNamespace(
        context=_types.SimpleNamespace(
            scene=_types.SimpleNamespace(objects=[_mesh, _empty])
        )
    )

    def _scope(scope, owner):
        return BlenderBridgeSlotsBase.resolve_scope_objects(owner, scope)

    def _owner(scene=_mock.sentinel.no_bridge):
        if scene is _mock.sentinel.no_bridge:
            return _types.SimpleNamespace()  # a panel whose bridge is unbuilt
        return _types.SimpleNamespace(
            bridge=_types.SimpleNamespace(_scene_objects=lambda: scene)
        )

    _visible = staticmethod(lambda: ["vis"])
    with _mock.patch.dict(sys.modules, {"bpy": _fake_bpy}):
        with _mock.patch.object(_btk, "selected_objects", lambda: ["sel"]):
            with _mock.patch.object(BlenderExportMixin, "_visible_objects", _visible):
                _sel = _scope("selected", _owner())
                _bogus = _scope("bogus", _owner())
                _hooked = _scope("all", _owner(["root"]))
                _unanswered = _scope("all", _owner(None)), _scope("all", _owner())
                _empty = _scope("all", _owner([]))
                _vis = _scope("visible", _owner())
    check("scope 'selected' reads the selection", _sel == ["sel"])
    check("an unknown scope never widens", _bogus == ["sel"])
    check("scope 'all' prefers the bridge's whole-scene hook", _hooked == ["root"])
    check(
        "scope 'all' falls back to the scene's meshes when the hook can't answer",
        _unanswered == ([_mesh], [_mesh]),
    )
    check("an empty scene stays empty", _empty == [])
    check("scope 'visible' reads the export mixin, bridge or not", _vis == ["vis"])
except Exception as e:
    traceback.print_exc()
    check("scope precedence raised", False, repr(e))

# ---------------------------------------------------------------------------
# The Bake Source row says what THIS bridge does with the set. The shared base
# told every bridge's artist "Sends now ship it as the companion bake source",
# while the Marmoset send never read the set at all. Now each bridge carries
# its own sentence (mayatk's wording per bridge), both read the ONE
# bake_sets.BakeSourceSet, and the Marmoset panel greys the suffix fallback
# while a set exists (mirror of mayatk's _refresh_param_enablement).
# Added: 2026-09-27
# ---------------------------------------------------------------------------
try:
    import types as _types

    from blendertk.mat_utils.bake_sets import BakeSourceSet
    from blendertk.mat_utils.marmoset_bridge import parameters as _mar_params
    from blendertk.mat_utils.marmoset_bridge.marmoset_bridge_slots import (
        MarmosetBridgeSlots,
    )
    from blendertk.mat_utils.substance_bridge.substance_bridge_slots import (
        SubstanceBridgeSlots,
    )
    from blendertk.ui_utils.blender_bridge_slots_base import BlenderBridgeSlotsBase

    check(
        "the Bake Source row acts on bake_sets.BakeSourceSet",
        BlenderBridgeSlotsBase._bake_source_set() is BakeSourceSet
        and MarmosetBridgeSlots._bake_source_set() is BakeSourceSet
        and SubstanceBridgeSlots._bake_source_set() is BakeSourceSet,
    )

    class _Log:
        def __init__(self):
            self.records = []

        def info(self, msg):
            self.records.append(("info", msg))

        def warning(self, msg):
            self.records.append(("warning", msg))

    def _bare_slots(cls, has_set):
        """A panel-less slots instance whose set reports *has_set*."""
        inst = object.__new__(cls)
        inst._bridge = _types.SimpleNamespace(logger=_Log())
        inst._param_widgets = {}  # no supersession triggers wired
        inst.enabled = {}
        inst.set_param_enabled = lambda key, on, reason="": inst.enabled.__setitem__(
            key, (on, reason)
        )
        fake_set = _types.SimpleNamespace(
            SET_NAME=BakeSourceSet.SET_NAME,
            define=lambda: ["obj"],
            exists=lambda: has_set,
        )
        inst._bake_source_set = lambda: fake_set
        return inst

    _mar = _bare_slots(MarmosetBridgeSlots, has_set=True)
    _mar.set_bake_source_from_selection()
    _sub = _bare_slots(SubstanceBridgeSlots, has_set=True)
    _sub.set_bake_source_from_selection()
    _mar_msg = " ".join(m for _k, m in _mar._bridge.logger.records)
    _sub_msg = " ".join(m for _k, m in _sub._bridge.logger.records)
    check(
        "Marmoset's Set From Selection names what ITS bake send does",
        "Bake sends now export it as the bake source" in _mar_msg
        and "Suffix fallback is inactive" in _mar_msg,
        _mar_msg,
    )
    check(
        "Substance's keeps its own sentence",
        "Sends now ship it as the companion bake source" in _sub_msg
        and "Suffix" not in _sub_msg,
        _sub_msg,
    )
    check(
        "defining the set greys Marmoset's suffix-fallback rows at once",
        set(MarmosetBridgeSlots.SUFFIX_FALLBACK_KEYS)
        <= set(_mar_params.Parameters.PARAMS)
        and all(
            _mar.enabled.get(k, (True, ""))[0] is False and _mar.enabled[k][1]
            for k in MarmosetBridgeSlots.SUFFIX_FALLBACK_KEYS
        ),
        str(_mar.enabled),
    )
    _clear = _bare_slots(MarmosetBridgeSlots, has_set=False)
    _clear._refresh_param_enablement()
    check(
        "...and without a set they are live again",
        all(
            _clear.enabled.get(k) == (True, "")
            for k in MarmosetBridgeSlots.SUFFIX_FALLBACK_KEYS
        ),
        str(_clear.enabled),
    )
except Exception as e:
    traceback.print_exc()
    check("bake-source row wiring raised", False, repr(e))

# ---------------------------------------------------------------------------
# A panel's scope ships the subtree it names. Every Blender bridge panel
# resolves its Scope through BlenderBridgeSlotsBase.scoped_objects, so a group
# selected there reached the RizomUV send (whose engine does not close the
# scope itself) as the Empty alone. Against a stand-in bpy: the closure reads
# only parent / children_recursive. Added: 2026-09-27
# ---------------------------------------------------------------------------
try:
    import types as _types
    from unittest import mock as _mock

    from blendertk.uv_utils.rizom_bridge.rizom_bridge_slots import RizomBridgeSlots

    class _Node:
        def __init__(self, name, parent=None):
            self.name, self.parent, self._kids = name, parent, []
            if parent is not None:
                parent._kids.append(self)

        @property
        def children_recursive(self):
            out = []
            for kid in self._kids:
                out += [kid] + kid.children_recursive
            return out

    _g = _Node("g")
    _s = _Node("s", _g)
    _Node("a", _g)
    _Node("b", _s)
    _scoped = object.__new__(RizomBridgeSlots)
    _scoped._bridge = _types.SimpleNamespace(
        logger=_types.SimpleNamespace(warning=lambda *_a, **_k: None)
    )
    # Selected answers the group; Visible Only answers the objects on screen.
    _scoped.resolve_scope_objects = lambda scope: (
        [_g] if scope == "selected" else [_g, _s]
    )
    _stand_in = _types.SimpleNamespace(data=_types.SimpleNamespace(objects={}))
    with _mock.patch.dict(sys.modules, {"bpy": _stand_in}):
        _sel = {o.name for o in _scoped.scoped_objects({"SCOPE": "selected"})}
        _vis = {o.name for o in _scoped.scoped_objects({"SCOPE": "visible"})}
    check(
        "a panel's Selected scope ships the group's whole subtree",
        _sel == {"g", "s", "a", "b"},
        str(sorted(_sel)),
    )
    check(
        "...while Visible Only re-adds no child it left out",
        _vis == {"g", "s"},
        str(sorted(_vis)),
    )
except Exception as e:
    traceback.print_exc()
    check("scope closure raised", False, repr(e))


passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
