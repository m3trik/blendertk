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
    "curtain",
    "mirror",
    "bevel",
    "bridge",
    "snap",
    "audio_clips",
    "blendshape_animator",
    "dynamic_pipe",
    "image_tracer",
    "curve_to_tube",
    "naming",
    "cut_on_axis",
    "duplicate_linear",
    "duplicate_radial",
    "duplicate_grid",
    "hdr_manager",
    "lightmap_baker",
    "reference_manager",
    "workspace_editor",
    "color_id",
    "exploded_view",
    "calculator",
    "texture_path_editor",
    "image_to_plane",
    "shader_templates",
    "mat_updater",
    "rizom_bridge",
    "shell_xform",
    "maya_bridge",
    "unity_bridge",
    "marmoset_bridge",
    "substance_bridge",
    "arnold_bridge",
    "game_shader",
    "hierarchy_sync",
    "scene_exporter",
    "smart_bake",
    "channels",
    "telescope_rig",
    "wheel_rig",
    "shadow_rig",
    "render_opacity",
    "tube_rig",
    "shots",
    "shot_manifest",
    "shot_sequencer",
]

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    from qtpy import QtWidgets  # noqa: F401
except Exception:
    # Blender headless ships no Qt binding — this suite is a .venv target. Skip cleanly.
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
        ("mirror", "MirrorSlots"),
        ("curtain", "CurtainSlots"),
        (
            "bevel",
            "BevelSlots",
        ),  # Preview/connect_multi init is bpy-free -> loadable under .venv
        (
            "bridge",
            "BridgeSlots",
        ),  # Preview/connect_multi + setVisible init is bpy-free
        ("calculator", "CalculatorSlots"),
        (
            "shader_templates",
            "ShaderTemplatesSlots",
        ),  # bpy-free init -> loadable under .venv
        (
            "mat_updater",
            "MatUpdaterSlots",
        ),  # engine defers bpy; cmb001_init is bpy-free
        ("rizom_bridge", "RizomBridgeSlots"),  # engine/slots init is bpy-free
        (
            "shell_xform",
            "ShellXformSlots",
        ),  # __init__ (logging + deferred icons/uitk) is bpy-free
        (
            "workspace_editor",
            "WorkspaceEditorSlots",
        ),  # pythontk.Workspace engine — bpy-free
        ("maya_bridge", "MayaBridgeSlots"),  # engine/slots init is bpy-free
        (
            "unity_bridge",
            "UnityBridgeSlots",
        ),  # engine/slots init is bpy-free (unitytk lookup guarded)
        (
            "marmoset_bridge",
            "MarmosetBridgeSlots",
        ),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        (
            "substance_bridge",
            "SubstanceBridgeSlots",
        ),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        (
            "game_shader",
            "GameShaderSlots",
        ),  # cmb001_init bpy-free (static OpenGL/DirectX list)
        (
            "channels",
            "ChannelsSlots",
        ),  # __init__ + table/header init are bpy-free (guarded refresh)
        ("exploded_view", "ExplodedViewSlots"),  # __init__ (logging only) is bpy-free
        (
            "color_id",
            "ColorIdSlots",
        ),  # __init__ (button groups + keep_square swatches) is bpy-free
        ("snap", "SnapSlots"),  # __init__ + option-box b###_init are bpy-free
        (
            "audio_clips",
            "AudioClipsSlots",
        ),  # __init__ + cmb000/tb001/b004 _init are bpy-free (list refresh guarded)
        (
            "blendshape_animator",
            "BlendshapeAnimatorSlots",
        ),  # __init__ + header/b000/cmb000/le001/b001/b004/b006/b008 _init are bpy-free (tree stays empty without bpy)
        (
            "reference_manager",
            "ReferenceManagerSlots",
        ),  # *_init bpy-guarded → table degrades w/o bpy
        (
            "hdr_manager",
            "HdrManagerSlots",
        ),  # __init__ + header/cmb _init are bpy-free (os dir scan)
        ("dynamic_pipe", "DynamicPipeSlots"),  # __init__ (logging only) is bpy-free
        (
            "image_tracer",
            "ImageTracerSlots",
        ),  # __init__ + header/txt000 _init are bpy-free
        (
            "curve_to_tube",
            "CurveToTubeSlots",
        ),  # __init__ (Preview/connect_multi/combo) is bpy-free
        (
            "image_to_plane",
            "ImageToPlaneSlots",
        ),  # __init__ (button connects) is bpy-free
        ("naming", "NamingSlots"),  # __init__ + option-box *_init are bpy-free
        (
            "telescope_rig",
            "TelescopeRigSlots",
        ),  # __init__ (logging + btn connect) is bpy-free
        ("wheel_rig", "WheelRigSlots"),  # __init__ (logging + btn connect) is bpy-free
        (
            "shadow_rig",
            "ShadowRigSlots",
        ),  # __init__ (Preview + btn connect) is bpy-free
        ("render_opacity", "RenderOpacitySlots"),  # __init__ (btn connect) is bpy-free
        (
            "tube_rig",
            "TubeRigSlots",
        ),  # __init__ (mode combo + dynamic options) is bpy-free
        (
            "lightmap_baker",
            "LightmapBakerSlots",
        ),  # __init__ + cmb _init are bpy-free (preset store)
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
        # Preview panels ship the commit button enabled=false in the .ui; Preview must enable
        # it on construction or Create is a dead button (preview can never be committed). Verify
        # end-to-end on the real loaded widget — the unit test only covers the stub.
        if panel in ("mirror", "curtain", "bevel", "bridge"):
            b000 = getattr(ui, "b000", None)
            check(
                f"{panel} Create button enabled after load (Preview manages it)",
                b000 is not None and b000.isEnabled(),
                "b000.isEnabled()",
            )

    # Gesture-scoped panels opt into the pin + auto-hide-on-key_show-release behavior by declaring a
    # "pin" header button in header_init (overriding BlenderUiHandler's blanket "blendertk"->hide
    # default). The offscreen load skips header_init (see the channels note below), so drive it
    # explicitly — the documented init entry point — then assert pin replaced the default hide.
    GESTURE_SCOPED = [
        "reference_manager",
        "color_id",
        "exploded_view",
        "bridge",
        "cut_on_axis",
        "mirror",
        "shell_xform",
        "naming",
    ]
    for panel in GESTURE_SCOPED:
        gs_ui = sb.get_ui(panel)
        gs_slots = getattr(gs_ui, "slots", None)
        if gs_slots is None:
            check(
                f"{panel} exposes slots for the gesture-scoped check", False, "no slots"
            )
            continue
        gs_slots.header_init(gs_ui.header)
        gs_buttons = set(getattr(gs_ui.header, "buttons", {}))
        check(
            f"{panel} is gesture-scoped: pin button, no hide button",
            "pin" in gs_buttons and "hide" not in gs_buttons,
            f"{sorted(gs_buttons)}",
        )

    # reference_manager: the clickable icon columns (link / open / display) must stay VISIBLE
    # square columns and the footer must host the bulk 'Un-Reference All' button — the mayatk parity
    # the panel was missing. Regression guard for two bugs: (a) overriding the action columns to
    # ResizeToContents collapsed them to ~0 px (invisible with icon-only cells / an empty table);
    # (b) the footer had no Un-Reference-All analogue. Qt-only (find_blend_files is a bpy-free os
    # scan), so it exercises the real populate path here under the .venv.
    import tempfile as _rm_tempfile
    import shutil as _rm_shutil
    from qtpy.QtWidgets import QHeaderView as _RM_QHeaderView

    rm_ui = sb.get_ui("reference_manager")
    rm = getattr(rm_ui, "slots", None)
    if rm is not None:
        rm_btns = [
            b.text() for b in rm_ui.footer.findChildren(QtWidgets.QPushButton)
        ]
        check(
            "reference_manager footer hosts the bulk 'Un-Reference All' button",
            "Un-Reference All" in rm_btns,
            f"{rm_btns}",
        )
        rm_tmp = _rm_tempfile.mkdtemp(prefix="btk_rm_ui_test_")
        try:
            rm_proj = os.path.join(rm_tmp, "proj")
            os.makedirs(rm_proj)
            for _n in ("alpha.blend", "beta.blend", "gamma.blend"):
                open(os.path.join(rm_proj, _n), "wb").close()
            # Offscreen load skips the *_init entry points — drive them explicitly.
            rm.header_init(rm_ui.header)
            rm.txt000_init(rm_ui.txt000)
            rm.cmb000_init(rm_ui.cmb000)
            rm.tbl000_init(rm_ui.tbl000)
            rm_ui.txt000.setText(rm_tmp)  # root -> 'proj' becomes a workspace
            rm._populate_workspaces()
            rm.tbl000_init(rm_ui.tbl000)  # re-run -> _refresh_table_content populates

            # Regression (deliberate, localized — not dependent on other test sections having
            # already re-driven header_init): a widget-outlives-slots reload calls header_init
            # again on the SAME persisted header (the GESTURE_SCOPED loop above already does this
            # incidentally). The one-time menu build must not re-append every Naming / Filter /
            # Include-Types control, AND refresh_requested must stay a SINGLE connection (not
            # doubled) so a refresh doesn't fire the handler twice.
            _hdr_incl_before = len(
                [
                    c
                    for c in rm_ui.header.menu.findChildren(QtWidgets.QCheckBox)
                    if c.objectName().startswith("chk_include_")
                ]
            )
            _refresh_calls = []
            rm._refresh = lambda: _refresh_calls.append(1)
            rm.header_init(rm_ui.header)  # deliberate 2nd call on the same widget
            rm.header_init(rm_ui.header)  # and a 3rd, for good measure
            _hdr_incl_after = len(
                [
                    c
                    for c in rm_ui.header.menu.findChildren(QtWidgets.QCheckBox)
                    if c.objectName().startswith("chk_include_")
                ]
            )
            rm_ui.header.refresh_requested.emit()
            check(
                "reference_manager header_init is idempotent across repeat calls "
                "(no re-appended controls, no doubled refresh_requested connection)",
                _hdr_incl_after == _hdr_incl_before and len(_refresh_calls) == 1,
                f"checkboxes before={_hdr_incl_before} after={_hdr_incl_after} "
                f"refresh_calls={len(_refresh_calls)}",
            )
            del rm._refresh

            # Header mirrors mayatk's exact items: Naming uses txt_subfolder_structure +
            # 'Save To Workspace'; Operations is 'Unlink and Import All' + 'Un-Reference All'
            # (Maya's exact labels); the Include Types row adds one chk_include_<type> per shared
            # file type. Recursive + workspace management moved OFF the header onto the Root
            # Directory option box (where Maya keeps chk000). Guards against the header drifting
            # back to the old Blender-only set.
            def _obj_names(_menu):
                return {
                    w.objectName()
                    for w in _menu.findChildren(QtWidgets.QWidget)
                    if w.objectName()
                }

            _hdr_names = _obj_names(rm_ui.header.menu)
            _opt_names = _obj_names(rm_ui.txt000.option_box.menu)
            check(
                "reference_manager header mirrors Maya's items (no Blender-only extras in the header)",
                {
                    "txt_subfolder_structure",
                    # Operations buttons carry Maya's exact labels/names (renamed from the
                    # old Make Local All / Remove All).
                    "btn_unlink_import_all",
                    "btn_unreference_all",
                    # Include Types row — one toggle per shared file type, both panels.
                    "chk_include_ma",
                    "chk_include_mb",
                    "chk_include_fbx",
                    "chk_include_blend",
                }
                <= _hdr_names
                and not (
                    {"chk_recursive", "btn_new_workspace", "btn_mark_workspace", "btn_reload_all"}
                    & _hdr_names
                ),
                f"header={sorted(_hdr_names)}",
            )
            check(
                "reference_manager Recursive + workspace mgmt live on the Root Directory option box",
                {"chk_recursive", "btn_new_workspace", "btn_mark_workspace"} <= _opt_names,
                f"optbox={sorted(_opt_names)}",
            )

            rm_tbl = rm_ui.tbl000
            rm_hdr = rm_tbl.horizontalHeader()
            action_cols = (rm.COL_REF, rm.COL_OPEN, rm.COL_DISPLAY)
            check(
                "reference_manager populated 3 file rows from the workspace",
                rm_tbl.rowCount() == 3,
                f"rows={rm_tbl.rowCount()}",
            )
            check(
                "reference_manager icon columns are Fixed, visible squares (not collapsed)",
                all(
                    rm_hdr.sectionResizeMode(c) == _RM_QHeaderView.Fixed
                    and rm_hdr.sectionSize(c) > 0
                    for c in action_cols
                ),
                f"modes={[str(rm_hdr.sectionResizeMode(c)) for c in action_cols]} "
                f"widths={[rm_hdr.sectionSize(c) for c in action_cols]}",
            )
            check(
                "reference_manager every row carries a link-column state + icon",
                all(
                    rm_tbl.actions.get(r, rm.COL_REF)
                    in ("referenced", "unreferenced")
                    and rm_tbl.item(r, rm.COL_REF) is not None
                    and not rm_tbl.item(r, rm.COL_REF).icon().isNull()
                    for r in range(rm_tbl.rowCount())
                ),
                f"states={[rm_tbl.actions.get(r, rm.COL_REF) for r in range(rm_tbl.rowCount())]}",
            )

            # Cross-DCC: with the Include Types row's 'ma'/'mb' toggles on, the workspace's
            # .ma/.mb also list — by their plain file name (no redundant '(Maya)' tag; the user
            # reveals the extension to tell them apart, mirror of the Maya panel dropping its
            # '(Blender)' tag). A foreign row toggles like any other (link bakes to a cached .blend
            # and links that; Open bakes + opens as a new file), so it carries the same clickable
            # referenced/unreferenced + Open states — only Display stays unavailable until linked.
            for _n in ("robot.ma", "prop.mb"):
                open(os.path.join(rm_proj, _n), "wb").close()
            rm_menu = rm_ui.header.menu
            check(
                "reference_manager header wires the Include Types row",
                all(
                    hasattr(rm_menu, f"chk_include_{t}")
                    for t in ("ma", "mb", "fbx", "blend")
                ),
            )
            rm_menu.chk_include_ma.setChecked(True)
            rm_menu.chk_include_mb.setChecked(True)
            rm.tbl000_init(rm_ui.tbl000)
            rm_rows = {rm_tbl.item(r, 0).text(): r for r in range(rm_tbl.rowCount())}
            foreign_rows = [
                r
                for name, r in rm_rows.items()
                if os.path.splitext(name)[1].lower() in (".ma", ".mb", ".fbx")
            ]
            check(
                "reference_manager lists foreign scenes by plain name with native toggle states",
                len(foreign_rows) == 2
                and all(
                    rm_tbl.actions.get(r, rm.COL_REF) == "unreferenced"
                    and rm_tbl.actions.get(r, rm.COL_OPEN) == "default"
                    and rm_tbl.actions.get(r, rm.COL_DISPLAY) == "unavailable"
                    for r in foreign_rows
                ),
                f"{sorted(rm_rows)}",
            )

            # Regression (row context menu duplicated its entries): tbl000_init runs more than
            # once before the framework stamps is_initialized (a refresh's init_slot + the
            # post-init connect_slot re-enter while it is still False), and the old
            # `if not is_initialized` guard rebuilt the whole menu each pass -> every row action
            # listed 2x. tbl000_init was run several times above; the menu must still be unique.
            _rm_menu_names = [
                b.objectName()
                for b in rm_tbl.menu.findChildren(QtWidgets.QPushButton)
            ]
            check(
                "reference_manager row context menu has NO duplicate entries (built once)",
                len(_rm_menu_names) > 0
                and len(_rm_menu_names) == len(set(_rm_menu_names)),
                f"{len(_rm_menu_names)} items, {len(set(_rm_menu_names))} unique",
            )
            _rm_hdr_incl = [
                c.objectName()
                for c in rm_ui.header.menu.findChildren(QtWidgets.QCheckBox)
                if c.objectName().startswith("chk_include_")
            ]
            check(
                "reference_manager header Include Types row is not duplicated (4 unique)",
                sorted(_rm_hdr_incl)
                == ["chk_include_blend", "chk_include_fbx", "chk_include_ma", "chk_include_mb"],
                f"{sorted(_rm_hdr_incl)}",
            )

            # Regression (dead row-icon-clicks / context-menu actions after a UI reload): tbl000
            # is a persisted QWidget that can outlive its slots instance (a script/module reload
            # builds a NEW ReferenceManagerSlots on the SAME widget, which already carries
            # is_initialized=True from the earlier calls above). Before this fix, the action-column
            # states and register_menu_action handlers were only wired inside the one-time
            # `if not is_initialized` block, so a reload silently left every row icon click and
            # context-menu action bound to the OLD (dead) slots instance. tbl000_init now re-wires
            # them via _wire_table_signals on every call — verify a fresh instance actually takes
            # over the live bindings (mirror of the channels reload-rewire regression test).
            from blendertk.env_utils.reference_manager import ReferenceManagerSlots

            _s2 = ReferenceManagerSlots.__new__(ReferenceManagerSlots)
            _s2.sb = rm.sb
            _s2.ui = rm.ui
            _s2._recursive = rm._recursive
            _s2._notes = {}
            _s2._suppress_note_save = False
            _open_calls = []
            # Patch BEFORE tbl000_init runs: register_menu_action's handler closure captures
            # `self.open_selected` (the bound method) at wiring time, not via a later lazy
            # attribute lookup, so patching after wiring would silently miss the swap.
            _s2.open_selected = lambda: _open_calls.append(1)
            _s2.tbl000_init(rm_tbl)
            _action_owner = rm_tbl.actions._columns[rm.COL_REF]["states"]["unreferenced"][
                "action"
            ].__self__
            check(
                "reference_manager tbl000_init re-wires the action column to a NEW slots "
                "instance on reload (not left bound to the dead old one)",
                _action_owner is _s2 and _action_owner is not rm,
                f"owner={_action_owner!r}",
            )
            rm_tbl._menu_action_registry["row_open"]["handler"](rm_tbl.selectedItems() or [])
            check(
                "reference_manager tbl000_init re-wires context-menu actions to a NEW slots "
                "instance on reload (row_open dispatches to the live instance)",
                _open_calls == [1],
                f"open_calls={_open_calls}",
            )

            # Crash regression: _rewire_signal must drop ONLY its own keyed connection, never a
            # widget's internal one. The mayatk panel re-wires customContextMenuRequested (the
            # table wires -> _show_context_menu in __init__) and the delegate's closeEditor (Qt
            # wires it for the edit lifecycle); a blanket signal.disconnect() stripped those and
            # crashed live Maya. Use an ISOLATED throwaway widget (not the live panel's wired
            # signals, whose real slots pop modal dialogs): an 'internal' connection plus two
            # keyed re-wires — the internal one must survive, and only the latest keyed slot fires.
            _probe_w = QtWidgets.QCheckBox()
            _internal, _first, _second = [], [], []
            _probe_w.toggled.connect(lambda *a: _internal.append(1))  # stands in for internal wiring
            ReferenceManagerSlots._rewire_signal(
                _probe_w, _probe_w.toggled, lambda *a: _first.append(1), "probe"
            )
            ReferenceManagerSlots._rewire_signal(
                _probe_w, _probe_w.toggled, lambda *a: _second.append(1), "probe"
            )
            _probe_w.toggle()
            check(
                "_rewire_signal preserves a foreign/internal connection and replaces only its "
                "own key (the live-Maya crash fix)",
                _internal == [1] and _first == [] and _second == [1],
                f"internal={_internal} first={_first} second={_second}",
            )
            _probe_w.deleteLater()

            # _open_path degrades gracefully on a foreign (.ma) row under the .venv (no bpy) —
            # regression guard for the bpy-import-ordering bug where `import bpy` ran at the top of
            # _open_path (unconditional) and raised ModuleNotFoundError before the foreign branch.
            # It must route to _open_foreign_as_new, whose _has_bpy() guard surfaces a clean message.
            _rm_msgs = []
            _rm_orig_mb = rm.sb.message_box
            rm.sb.message_box = lambda *a, **k: _rm_msgs.append(a[0] if a else "")
            try:
                rm._open_path(os.path.join(rm_proj, "robot.ma"))  # must NOT raise under .venv
                check(
                    "reference_manager _open_path(foreign) degrades cleanly without bpy (no import crash)",
                    any("needs a running Blender" in m for m in _rm_msgs),
                    f"{_rm_msgs}",
                )
            finally:
                rm.sb.message_box = _rm_orig_mb

            # Open icon is a TOGGLE (mirror of the reference icon) and Open/Reference are mutually
            # exclusive. Drive the handlers with stubbed engine ops (no bpy under the .venv):
            #   - Open on the CURRENT scene -> close (btk.new_scene), not re-open.
            #   - Open on a REFERENCED file -> drop the reference, then open.
            #   - Reference on the CURRENT scene -> close it first (can't self-reference), then link.
            import blendertk as _rm_btk

            _native = os.path.join(rm_proj, "alpha.blend")
            _log = {"new": 0, "open": [], "removed": [], "linked": []}
            _saved = {
                "cur": rm._current_scene_path,
                "lib": rm._library_for_path,
                "new": _rm_btk.new_scene,
                "open": _rm_btk.open_scene,
                "remove": _rm_btk.remove_library,
                "link": _rm_btk.link_blend_file,
                "conf": rm._confirm_discard_unsaved,
                "ref": rm._refresh,
                "mb": rm.sb.message_box,
            }
            rm._confirm_discard_unsaved = lambda *a, **k: True
            rm._refresh = lambda: None
            rm.sb.message_box = lambda *a, **k: None
            _rm_btk.new_scene = lambda: (_log.__setitem__("new", _log["new"] + 1) or True)
            _rm_btk.open_scene = lambda p: (_log["open"].append(p) or True)
            _rm_btk.remove_library = lambda lib: (_log["removed"].append(lib) or True)
            _rm_btk.link_blend_file = lambda p, **k: (_log["linked"].append(p) or 1)
            try:
                _row = next(
                    r
                    for r in range(rm_tbl.rowCount())
                    if rm._row_path(r)
                    and os.path.normcase(rm._row_path(r)) == os.path.normcase(_native)
                )
                # (a) Open on the CURRENT scene -> close.
                rm._current_scene_path = lambda: os.path.normpath(_native).lower()
                rm._library_for_path = lambda p: None
                rm._open_scene_at_row(_row, rm.COL_OPEN)
                check(
                    "Open icon on the current scene CLOSES it (new empty scene), not re-open",
                    _log["new"] == 1 and _log["open"] == [],
                    f"{_log}",
                )
                # (b) Open on a REFERENCED, non-current file -> just open it (opening replaces the
                # session, so the reference is discarded for free — NO explicit remove_library,
                # which would wrongly persist if the unsaved-changes prompt were then declined).
                _log["open"].clear(); _log["removed"].clear()
                rm._current_scene_path = lambda: ""
                _fake_lib = object()
                rm._library_for_path = lambda p: _fake_lib
                rm._open_scene_at_row(_row, rm.COL_OPEN)
                check(
                    "Open icon on a referenced file just opens it (open discards the reference, "
                    "no pre-remove)",
                    _log["removed"] == []
                    and [os.path.normcase(p) for p in _log["open"]]
                    == [os.path.normcase(_native)],
                    f"{_log}",
                )
                # (c) Reference on the CURRENT scene -> close it first, then link.
                _log["new"] = 0; _log["linked"].clear()
                rm._current_scene_path = lambda: os.path.normpath(_native).lower()
                rm._library_for_path = lambda p: None
                rm._toggle_reference_at_row(_row, rm.COL_REF)
                check(
                    "Reference icon on the current scene CLOSES it first, then links it",
                    _log["new"] == 1
                    and [os.path.normcase(p) for p in _log["linked"]]
                    == [os.path.normcase(_native)],
                    f"{_log}",
                )
            finally:
                rm._current_scene_path = _saved["cur"]
                rm._library_for_path = _saved["lib"]
                _rm_btk.new_scene = _saved["new"]
                _rm_btk.open_scene = _saved["open"]
                _rm_btk.remove_library = _saved["remove"]
                _rm_btk.link_blend_file = _saved["link"]
                rm._confirm_discard_unsaved = _saved["conf"]
                rm._refresh = _saved["ref"]
                rm.sb.message_box = _saved["mb"]

            import blendertk.env_utils.maya_bridge._scene_import as _rm_si

            _rm_calls = []
            _rm_orig_import = _rm_si.import_maya_scene
            _rm_si.import_maya_scene = lambda p, **k: (_rm_calls.append(p) or [object()])
            # Faking a live Blender is needed only to pass the import guard; stub _refresh +
            # message_box too so nothing else reaches into bpy under the .venv.
            rm._has_bpy = lambda: True
            rm._refresh = lambda: None
            rm.sb.message_box = lambda *a, **k: None
            try:
                _fr = foreign_rows[0]
                _fpath = rm._row_path(_fr)
                rm._import_foreign_paths([_fpath])
                check(
                    "reference_manager 'Import (convert)' routes the Maya scene to btk.import_maya_scene",
                    _rm_calls == [_fpath],
                    f"{_rm_calls}",
                )

                # The link icon takes the OTHER path: bake -> link the cached .blend.
                import blendertk as _rm_btk

                _rm_baked, _rm_linked = [], []
                _rm_orig_bake = _rm_si.bake_maya_scene
                _rm_si.bake_maya_scene = lambda p, **k: (
                    _rm_baked.append(p) or (p + ".baked.blend")
                )
                _rm_orig_link = _rm_btk.link_blend_file
                _rm_btk.link_blend_file = lambda p, **k: (_rm_linked.append(p) or 1)
                # list_libraries() needs bpy; the row is unlinked in this fixture anyway.
                rm._library_for_path = lambda p: None
                # The mutual-exclusion check calls _is_current -> _current_scene_path, which imports
                # bpy once _has_bpy() is stubbed True above; the foreign row is not the open scene
                # in this fixture, so stub it out (no bpy under the .venv).
                rm._current_scene_path = lambda: ""
                try:
                    rm._toggle_reference_at_row(_fr, rm.COL_REF)
                    check(
                        "reference_manager link icon bakes a foreign row and links the bake",
                        _rm_baked == [_fpath]
                        and _rm_linked == [_fpath + ".baked.blend"],
                        f"baked={_rm_baked} linked={_rm_linked}",
                    )
                finally:
                    _rm_si.bake_maya_scene = _rm_orig_bake
                    _rm_btk.link_blend_file = _rm_orig_link
                    del rm._library_for_path, rm._current_scene_path
            finally:
                _rm_si.import_maya_scene = _rm_orig_import
                del rm._has_bpy, rm._refresh, rm.sb.message_box
        finally:
            rm_ui.txt000.clear()
            _rm_shutil.rmtree(rm_tmp, ignore_errors=True)
    else:
        check("reference_manager exposes slots for the icon-column check", False, "no slots")

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

    # unity_bridge: params_defaults() (Qt path via uitk.bridge.AttributeSpec; needs no bpy, so it
    # belongs here rather than in the headless test_unity_bridge harness which lacks Qt) + the
    # single-mode combo surface (parity with mayatk -- no leftover "Unity Studio" mode).
    from blendertk.env_utils.unity_bridge._unity_bridge import UnityBridge
    from blendertk.env_utils.unity_bridge.unity_bridge_slots import (
        UnityBridgeSlots as _UBS,
    )

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
    check(
        "unity_bridge single delivery mode ('Copy to Project', no Unity Studio)",
        _UBS.MODE_COPY == "copy_to_assets"
        and _UBS.MODE_LABELS == {_UBS.MODE_COPY: "Copy to Project"}
        and not hasattr(_UBS, "MODE_STUDIO")
        and not hasattr(_UBS, "MODE_EXISTING"),
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

    # audio_clips: no bpy under the offscreen .venv, so the clips combo/spinboxes degrade to
    # empty/zero (guarded via _has_bpy()) rather than raising — verify the degrade AND that the
    # Qt-only option-box wiring (management menu buttons, Move's select/refresh actions, Sync
    # Scene Range's checkbox) still fully materializes.
    ac_ui = sb.get_ui("audio_clips")
    ac = getattr(ac_ui, "slots", None)
    if ac is not None:
        check(
            "audio_clips cmb000 degrades to empty (no bpy) without raising",
            ac_ui.cmb000.count() == 0,
        )
        check(
            "audio_clips trim spinboxes degrade to 0 (no bpy)",
            ac_ui.s000.value() == 0 and ac_ui.s001.value() == 0,
        )
        cmb_menu = ac_ui.cmb000.option_box.menu
        check(
            "audio_clips cmb000 option box wires the clip-management menu",
            all(
                hasattr(cmb_menu, n)
                for n in (
                    "btn_rename_track",
                    "btn_replace_track",
                    "btn_remove_selected",
                    "btn_remove_audio",
                )
            ),
        )
        # tb001 folds its two actions into the option-box MENU (QPushButtons),
        # mirroring Maya — not as registered ActionOptions. Check the menu buttons.
        tb001_menu = ac_ui.tb001.option_box.menu
        check(
            "audio_clips tb001 option box wires reveal + sync menu actions",
            all(
                hasattr(tb001_menu, n)
                for n in ("btn_reveal_sequencer", "btn_sync_range")
            ),
        )
        # The fit mode is a two-valued combo since 2026-07-12 (was a
        # chk_extend_only checkbox); "Extend Only" preserves the old default.
        _fit = getattr(ac_ui.b004.option_box.menu, "cmb_fit", None)
        check(
            "audio_clips b004 option box wires the fit-mode combo (default Extend Only)",
            _fit is not None and _fit.currentText() == "Extend Only",
        )
    else:
        check("audio_clips exposes slots for the option-box check", False, "no slots")

    # curtain: the cmb000 preset selector is wired via uitk.PresetManager and populated from the
    # shipped built-in presets (proves the combo + builtin_dir work, not just "didn't error").
    curtain_ui = sb.get_ui("curtain")
    cmb = getattr(curtain_ui, "cmb000", None)
    items = [cmb.itemText(i) for i in range(cmb.count())] if cmb else []
    check(
        "curtain preset combo lists the built-in presets",
        "Stage Swag" in items and "Shower Curtain" in items,
        f"{items}",
    )

    # tube_rig (HYBRID): the mode combo lists the registered strategies and the options body is
    # rebuilt from the SELECTED strategy's option dicts (AttributeSpec -> make_widget) — the core
    # dynamic-spec behavior. Switching modes swaps the option widget set.
    tube_ui = sb.get_ui("tube_rig")
    tslots = getattr(tube_ui, "slots", None)
    tcmb = getattr(tube_ui, "cmb_preset", None)
    if tslots is not None and tcmb is not None:
        modes = [tcmb.itemText(i) for i in range(tcmb.count())]
        check(
            "tube_rig mode combo lists the 3 strategies",
            any("Spline" in m for m in modes)
            and any("Anchor" in m for m in modes)
            and any("FK" in m for m in modes),
            f"{modes}",
        )
        # the initial (Spline) options were built from its dicts
        spline_keys = set(tslots._option_widgets)
        check(
            "tube_rig built the Spline option widgets from its dicts",
            {"num_joints", "num_controls", "radius", "enable_stretch"} <= spline_keys,
            f"{sorted(spline_keys)}",
        )
        # switch to Anchor -> the options body rebuilds to Anchor's smaller dict set
        anchor_idx = next(
            i for i in range(tcmb.count()) if "Anchor" in tcmb.itemText(i)
        )
        tcmb.setCurrentIndex(anchor_idx)
        anchor_keys = set(tslots._option_widgets)
        check(
            "switching mode rebuilds the options body (Anchor set, no num_controls)",
            "num_controls" not in anchor_keys and "enable_stretch" in anchor_keys,
            f"{sorted(anchor_keys)}",
        )

    # lightmap_baker: the Resolution combo is a fixed power-of-two list (replacing the old
    # spinbox) and the Scope combo gates which objects bake; both are Qt-only (no bpy). Verify
    # the lists, the defaults, and that a Quality preset snaps the Resolution combo.
    lb_ui = sb.get_ui("lightmap_baker")
    lb = getattr(lb_ui, "slots", None)
    if lb is not None:
        res_items = [
            lb_ui.cmb_resolution.itemText(i)
            for i in range(lb_ui.cmb_resolution.count())
        ]
        check(
            "lightmap_baker Resolution combo lists the fixed sizes (default 1024)",
            res_items == [f"Resolution:\t{r}" for r in (256, 512, 1024, 2048, 4096)]
            and lb._resolution() == 1024,
            f"{res_items} _resolution()={lb._resolution()}",
        )
        scope_items = [
            lb_ui.cmb_scope.itemText(i) for i in range(lb_ui.cmb_scope.count())
        ]
        check(
            "lightmap_baker Scope combo lists Selected/Visible/Scene (default selected)",
            scope_items == ["Selected", "Visible", "Scene"]
            and lb._scope() == "selected",
            f"{scope_items} _scope()={lb._scope()}",
        )
        lb._apply_preset("preview")
        lb_preview = lb._resolution()
        lb._apply_preset("desktop")
        check(
            "lightmap_baker Quality preset snaps the Resolution combo",
            lb_preview == 256 and lb._resolution() == 2048,
            f"preview={lb_preview} desktop={lb._resolution()}",
        )
    else:
        check("lightmap_baker exposes slots for the combo check", False, "no slots")

    # hdr_manager: the 2026-07-03 drift port added an option-box Add-HDR flow on cmb000
    # (add_hdr_btn + cmb_add_mode) and an inline exact-angle ValueOption on slider000 — all
    # Qt-only (uitk option boxes, no bpy). Prove they materialized (not just that _init ran).
    hdr_ui = sb.get_ui("hdr_manager")
    # The option-box wiring runs in a deferred singleShot(0) (_initialize_ui) —
    # pump once so it fires; nothing else pumps under this harness.
    app.processEvents()
    hslots = getattr(hdr_ui, "slots", None)
    if hslots is not None:
        menu = hdr_ui.cmb000.option_box.menu
        mode_items = [
            menu.cmb_add_mode.itemText(i) for i in range(menu.cmb_add_mode.count())
        ]
        check(
            "hdr_manager Add-HDR option box built add_hdr_btn + cmb_add_mode",
            hasattr(menu, "add_hdr_btn")
            and mode_items == [label for label, _t in hslots._ADD_MODES]
            and hslots._add_mode() == "copy",
            f"{mode_items} _add_mode()={hslots._add_mode()}",
        )
        from uitk.widgets.optionBox.options.value import ValueOption

        check(
            "hdr_manager rotation slider carries the exact-angle ValueOption",
            hdr_ui.slider000.option_box.find_option(ValueOption) is not None,
        )
        # _norm_path (the combo path-match fix): an imported os.path.normpath() path still
        # matches get_dir_contents' filepaths despite a different slash style — the miss that
        # made the old plain findData() fail to highlight a just-added map.
        norm_p = os.path.join("HDRs", "Env A.hdr")
        check(
            "hdr_manager _norm_path collapses slash style for combo matching",
            hslots._norm_path(norm_p) == hslots._norm_path(norm_p.replace(os.sep, "/")),
        )
    else:
        check("hdr_manager exposes slots for the option-box check", False, "no slots")

    # workspace_editor: the minimal Project Window — one root field, RULE/LOCATION table with
    # per-row reset/remove action columns, and rule edits that write through to workspace.mel in
    # real time (no Accept). Qt-only (pythontk.Workspace engine), so exercise the whole loop here.
    # The uitk conftest sandboxes UITK_PRESETS_ROOT, so the template store never touches live data.
    import tempfile as _tempfile
    import shutil as _shutil
    import pythontk as _ptk
    import blendertk as _btk

    we_ui = sb.get_ui("workspace_editor")
    we = getattr(we_ui, "slots", None)
    if we is not None:
        # Offscreen load skips the *_init entry points — drive them explicitly.
        we.header_init(we_ui.header)
        we.txt000_init(we_ui.txt000)
        we.tbl000_init(we_ui.tbl000)
        check(
            "workspace_editor header menu: Add Rule in, retired verbs (incl. Set As "
            "Current) out",
            hasattr(we_ui.header.menu, "btn_add_rule")
            and not hasattr(we_ui.header.menu, "btn_set_current")
            and not hasattr(we_ui.header.menu, "btn_remove_rule")
            and not hasattr(we_ui.header.menu, "btn_save_template")
            and not hasattr(we_ui.header.menu, "btn_delete_template"),
        )
        check(
            "workspace_editor opens persistent (hide button, not gesture-scoped pin)",
            "hide" in set(getattr(we_ui.header, "buttons", {}))
            and "pin" not in set(getattr(we_ui.header, "buttons", {})),
            f"{sorted(getattr(we_ui.header, 'buttons', {}))}",
        )
        check(
            "workspace_editor template combo is PresetManager-wired",
            we._preset_mgr is not None
            and getattr(we_ui.header.menu, "cmb_template", None) is not None,
        )
        we_tmp = _tempfile.mkdtemp(prefix="btk_we_ui_test_")
        try:
            we_proj = os.path.join(we_tmp, "rt_proj")
            we_marker = os.path.join(we_proj, "workspace.mel")
            we_ui.txt000.setText(we_proj)
            check(
                "workspace_editor fresh path seeds the template, writes nothing",
                not os.path.exists(we_marker)
                and we_ui.tbl000.rowCount() == len(_ptk.DEFAULT_FILE_RULES),
                f"rows={we_ui.tbl000.rowCount()}",
            )
            check(
                "workspace_editor rows carry the reset + remove action icons",
                we_ui.tbl000.actions.get(0, 2) == "reset"
                and we_ui.tbl000.actions.get(0, 3) == "remove",
            )
            from qtpy import QtWidgets as _QtW

            _hdr = we_ui.tbl000.horizontalHeader()
            check(
                "workspace_editor LOCATION stretches; action icons pinned right (fixed)",
                _hdr.sectionResizeMode(1) == _QtW.QHeaderView.Stretch
                and _hdr.sectionResizeMode(2) == _QtW.QHeaderView.Fixed
                and _hdr.sectionResizeMode(3) == _QtW.QHeaderView.Fixed,
            )
            r_scene = next(
                r for r in range(we_ui.tbl000.rowCount()) if we._key_at(r) == "scene"
            )
            we_ui.tbl000.item(r_scene, 1).setText("shots")  # itemChanged → write
            we_rules = (
                _ptk.Workspace.parse_workspace_mel(we_marker)
                if os.path.isfile(we_marker)
                else {}
            )
            check(
                "workspace_editor first rule edit creates the project (real-time Accept)",
                we_rules.get("scene") == "shots"
                and os.path.isdir(os.path.join(we_proj, "shots")),
                f"{we_rules}",
            )
            # Selecting/creating a project root auto-pins it as the current workspace
            # (there's no Set As Current button — the root selection does it).
            _cur = _btk.current_workspace()
            check(
                "workspace_editor auto-sets the built project as current workspace",
                _cur is not None
                and os.path.normcase(os.path.normpath(_cur.root))
                == os.path.normcase(os.path.normpath(we_proj)),
                f"{_cur}",
            )
            we.reset_row(r_scene)
            check(
                "workspace_editor row reset restores the template default and saves",
                _ptk.Workspace.parse_workspace_mel(we_marker).get("scene")
                == _ptk.DEFAULT_FILE_RULES["scene"],
            )
            r_images = next(
                r for r in range(we_ui.tbl000.rowCount()) if we._key_at(r) == "images"
            )
            we.remove_row(r_images)
            check(
                "workspace_editor row remove deletes the rule from workspace.mel",
                "images" not in _ptk.Workspace.parse_workspace_mel(we_marker),
            )
            we.clear_rules()
            check(
                "workspace_editor Clear Settings removes every rule (marker survives)",
                os.path.isfile(we_marker)
                and _ptk.Workspace.parse_workspace_mel(we_marker) == {},
            )
            we.reset_rules()
            check(
                "workspace_editor Reset Settings restores the defaults and saves",
                _ptk.Workspace.parse_workspace_mel(we_marker)
                == _ptk.DEFAULT_FILE_RULES,
            )
            # A combo-saved template (PresetManager wraps the rules with "_meta") must
            # round-trip through the headless btk API with the block stripped.
            we._preset_mgr.save("suite_tpl")
            check(
                "workspace_editor combo-saved template round-trips via the headless API",
                _btk.workspace_template_rules("suite_tpl") == _ptk.DEFAULT_FILE_RULES,
            )
            _btk.delete_workspace_template("suite_tpl")
        finally:
            we_ui.txt000.clear()
            _btk.set_current_workspace(None)  # clear the auto-set session pin
            _shutil.rmtree(we_tmp, ignore_errors=True)
    else:
        check(
            "workspace_editor exposes slots for the real-time check", False, "no slots"
        )

    # channels: Compact View + the wheel-scrub step ladder are Qt-only (no bpy), so exercise them
    # on the real loaded panel here. Compact collapses row height + hides the table column header;
    # the ladder scales ×10 (Ctrl) / ×100 (Ctrl+Shift) / ÷10 (Alt) per modifier (mirror of Maya).
    channels_ui = sb.get_ui("channels")
    cslots = getattr(channels_ui, "slots", None)
    ctbl = getattr(channels_ui, "tbl000", None)
    if cslots is not None and ctbl is not None:
        vh = ctbl.verticalHeader()
        # Compact view defaults ON since 2026-07-12 — establish the known
        # non-compact baseline first so base_h is the expanded height.
        cslots._on_toggle_compact_view(False)
        base_h = vh.defaultSectionSize()
        cslots._on_toggle_compact_view(True)
        compact_h = vh.defaultSectionSize()
        hdr_hidden = ctbl.horizontalHeader().isHidden()
        cslots._on_toggle_compact_view(False)
        restored_h = vh.defaultSectionSize()
        hdr_shown = not ctbl.horizontalHeader().isHidden()
        check(
            "channels compact view collapses rows + hides the column header",
            compact_h < base_h and hdr_hidden and restored_h == base_h and hdr_shown,
            f"base={base_h} compact={compact_h} restored={restored_h} hdr_hidden={hdr_hidden}",
        )
        Qt = cslots.sb.QtCore.Qt
        ladder_ok = (
            cslots._wheel_step(Qt.NoModifier, False) == 0.1
            and cslots._wheel_step(Qt.ControlModifier, False) == 1.0
            and cslots._wheel_step(Qt.ControlModifier | Qt.ShiftModifier, False) == 10.0
            and cslots._wheel_step(Qt.AltModifier, False) == 0.01
            and cslots._wheel_step(Qt.ControlModifier | Qt.AltModifier, False) == 0.0001
            and cslots._wheel_step(Qt.ControlModifier, True) == 10
            and cslots._wheel_step(Qt.AltModifier, True) == 0
        )
        check("channels wheel-step ladder scales ×10/÷10 per modifier", ladder_ok)
        # The header menu builds lazily (offscreen load skips it), so drive header_init explicitly
        # — the documented init entry point — to prove it wires the Compact View checkbox. The
        # footer single-object button is built in __init__ so it is already present.
        cslots.header_init(channels_ui.header)
        chk = getattr(cslots, "_chk_compact", None)
        check(
            "channels header wires Compact View + footer single-object button",
            chk is not None
            and chk.objectName() == "chk_compact_view"
            and getattr(cslots, "_footer_compact_btn", None) is not None,
            f"chk_compact={chk!r} footer_btn={getattr(cslots, '_footer_compact_btn', None) is not None}",
        )

        # Scrub/wheel value editing is engine-agnostic (it only routes deltas through the
        # controller's value getters/setters). Drive it with a stub controller — no bpy needed —
        # to prove the per-object delta + display-space round-trip is wired correctly.
        class _FakeCtl:
            def __init__(self):
                self.vals = {"a": 1.0, "b": 2.0}
                self.sets = []  # (obj, text)

            def get_selected_nodes(self):
                return ["a", "b"]

            def is_locked(self, obj, desc):
                return False

            def get_channel_value(self, obj, desc):
                return self.vals[obj]

            def set_channel_value(self, objs, desc, text):
                for o in objs:
                    self.vals[o] = float(text)
                    self.sets.append((o, text))

            def format_value(self, v):
                return str(v)

            # Enough of the engine surface for _refresh_table to run without bpy (used by the
            # rebuild regression below, which drives the real tbl000_init -> _refresh_table path).
            def build_table_data(self, *a, **k):
                return [["location_x", "", "", "1.0", "float"]], [(False, "none")]

            def collect_channels(self, *a, **k):
                return [dict(_CHAN_DESC)]

        _CHAN_DESC = {
            "name": "location_x",
            "data_path": "location",
            "index": 0,
            "kind": "transform",
            "type": "float",
            "is_angle": False,
        }
        fake = _FakeCtl()
        cslots.controller = fake
        desc = dict(_CHAN_DESC)
        cslots._row_descriptors = [desc]
        COL_V = cslots.COL_VALUE
        # MMB scrub: dx=10 px * 0.01 step = +0.1 applied to each object's own start.
        cslots._on_scrub_started(0, COL_V)
        cslots._on_scrub_moved(0, COL_V, 10, 0)
        scrub_ok = (
            round(fake.vals["a"], 4) == 1.1
            and round(fake.vals["b"], 4) == 2.1
            and len(fake.sets) == 2
        )
        cslots._on_scrub_finished(0, COL_V)
        check(
            "channels MMB-scrub applies per-object delta in display space",
            scrub_ok,
            f"vals={fake.vals} sets={fake.sets}",
        )

        # Wheel (display mode, no editor open, no modifier): +1 notch * 0.1 default step.
        fake.vals = {"a": 1.0, "b": 2.0}
        fake.sets = []
        Qt = cslots.sb.QtCore.Qt
        cslots._on_wheel_scrolled(0, COL_V, 1, Qt.NoModifier)
        wheel_ok = round(fake.vals["a"], 4) == 1.1 and round(fake.vals["b"], 4) == 2.1
        check(
            "channels wheel-scrub steps each object by the modifier's step",
            wheel_ok,
            f"vals={fake.vals}",
        )

        # Regression (wheel/edits silently dead after a UI reload): the tbl000 QWidget can
        # outlive the slots instance. tbl000_init must RE-WIRE the table signals on EVERY call
        # (not only when the widget is first initialized), or the persisted widget's
        # cellWheelScrolled / cellChanged / scrub signals stay bound to the dead instance and
        # no-op — the user-reported "scroll wheel over a value does nothing". Simulate the
        # rebuild (new ChannelsSlots on the same persisted, already-initialized widget) and
        # prove the wheel signal reaches the NEW instance. mirror of the mayatk panel.
        from blendertk.node_utils.attributes.channels.channels_slots import (
            ChannelsSlots as _ChannelsSlots,
        )

        cslots.tbl000_init(ctbl)
        ctbl.is_initialized = True  # what _perform_slot_init stamps after the first init
        s2 = _ChannelsSlots(sb)  # a fresh slots instance bound to the SAME persisted widget
        s2.controller = _FakeCtl()
        s2._row_descriptors = [dict(_CHAN_DESC)]
        s2.controller.vals = {"a": 9.0, "b": 2.0}
        # Isolate the tbl000_init re-wire specifically: drop every binding first (as a reloaded
        # .ui handing tbl000_init a different-but-now-stale widget would), so ONLY the
        # unconditional re-wire in tbl000_init — not the best-effort pass in __init__ — can make
        # the wheel live again. With the old code (wiring trapped in `if not is_initialized`),
        # tbl000_init leaves it disconnected and the assert fails.
        try:
            ctbl.cellWheelScrolled.disconnect()
        except (RuntimeError, TypeError):
            pass
        s2.tbl000_init(ctbl)  # is_initialized already True -> must still re-wire the signals
        ctbl.cellWheelScrolled.emit(0, COL_V, 1, Qt.NoModifier)
        app.processEvents()
        check(
            "channels tbl000_init re-wires table signals after a rebuild (wheel not dead)",
            round(s2.controller.vals["a"], 4) == 9.1,
            f"s2.vals={s2.controller.vals} (9.0 = dead wiring bug, 9.1 = fixed)",
        )
    else:
        check(
            "channels exposes slots + table for compact-view check",
            False,
            "missing slots/tbl000",
        )

except Exception as e:
    traceback.print_exc()
    check("handler discovery raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
