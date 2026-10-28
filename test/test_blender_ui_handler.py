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
    "reference_manager",
    "workspace_editor",
    "texture_path_editor",
    "rizom_bridge",
    "maya_bridge",
    "marmoset_bridge",
    "substance_bridge",
    "arnold_bridge",
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
        (
            "workspace_editor",
            "WorkspaceEditorSlots",
        ),  # pythontk.Workspace engine — bpy-free
        ("maya_bridge", "MayaBridgeSlots"),  # engine/slots init is bpy-free
        (
            "marmoset_bridge",
            "MarmosetBridgeSlots",
        ),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        (
            "substance_bridge",
            "SubstanceBridgeSlots",
        ),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        (
            "reference_manager",
            "ReferenceManagerSlots",
        ),  # *_init bpy-guarded → table degrades w/o bpy
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
        handler.default_persistence(sb.get_ui("reference_manager"))
        == _UiHandler.PERSISTENCE_STICKY,
    )
    check(
        "BlenderUiHandler does not override apply_styles",
        BlenderUiHandler.apply_styles is _UiHandler.apply_styles,
        "a hardcoded header_buttons swap would outrank the user's stored choice",
    )
    check(
        "a UI Browser override outranks the blendertk default",
        handler.resolve_persistence(sb.get_ui("reference_manager"), context_default=None)
        == _UiHandler.PERSISTENCE_STICKY
        and (
            handler.set_persistence_override("reference_manager", _UiHandler.PERSISTENCE_TRANSIENT)
            or handler.resolve_persistence(sb.get_ui("reference_manager"))
        )
        == _UiHandler.PERSISTENCE_TRANSIENT,
    )
    handler.set_persistence_override("reference_manager", None)  # leave the store clean

    # Gesture-scoped panels opt into the pin + auto-hide-on-key_show-release behavior by declaring a
    # "pin" header button in header_init (overriding BlenderUiHandler's blanket "blendertk"->hide
    # default). The load runs every *_init, header_init included -- re-running it here built a
    # second header menu -- so assert what the load itself wired: pin replaced the default hide.
    GESTURE_SCOPED = [
        "reference_manager",
    ]
    for panel in GESTURE_SCOPED:
        gs_ui = sb.get_ui(panel)
        gs_slots = getattr(gs_ui, "slots", None)
        if gs_slots is None:
            check(
                f"{panel} exposes slots for the gesture-scoped check", False, "no slots"
            )
            continue
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
        # Tap-to-pin: reference_manager opts in explicitly in header_init (an assignment,
        # not a dependency on UiHandler.pin_on_tap's process-wide default) — letting the
        # marking-menu key go right after the panel opens pins it instead of dismissing it.
        # The GESTURE_SCOPED loop above already drove header_init on this same header.
        check(
            "reference_manager header opts into tap-to-pin",
            getattr(rm_ui.header, "pin_on_tap", None) is True,
        )
        rm_btns = [b.text() for b in rm_ui.footer.findChildren(QtWidgets.QPushButton)]
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
            # 'Save To Workspace'; Operations is 'Unlink and Import All' (Maya's exact label;
            # Un-Reference All is footer-only); the Include Types row adds one chk_include_<type> per shared
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
                    # Operations buttons carry Maya's exact labels/names (renamed from the
                    # old Make Local All / Remove All).
                    "btn_unlink_import_all",
                    # Include Types row — one toggle per shared file type, both panels.
                    "chk_include_ma",
                    "chk_include_mb",
                    "chk_include_fbx",
                    "chk_include_usd",
                    "chk_include_blend",
                }
                <= _hdr_names
                and not (
                    {
                        "chk_recursive",
                        "btn_new_workspace",
                        "btn_mark_workspace",
                        "btn_reload_all",
                        # Redundant with the footer's Un-Reference All.
                        "btn_unreference_all",
                        # Save To Workspace is footer-only.
                        "btn_save_scene",
                    }
                    & _hdr_names
                ),
                f"header={sorted(_hdr_names)}",
            )
            check(
                "reference_manager Recursive + workspace mgmt live on the Root Directory option box",
                {"chk_recursive", "btn_new_workspace", "btn_mark_workspace"}
                <= _opt_names,
                f"optbox={sorted(_opt_names)}",
            )
            # The naming conventions are panel-wide (Save and Rename apply them,
            # the filters match against them), so they live in the header menu
            # (both panels), read by the one _naming_options path, captured by
            # the header presets; Save's footer button carries no option box.
            _naming = ("cmb_case_style", "txt_suffix", "txt_subfolder_structure")
            _preset_widgets = {
                w.objectName()
                for w in rm_ui.header.menu.presets._get_widgets()
                if w.objectName()
            }
            _save_btn = getattr(rm_ui.footer, "_rm_save_btn", None)
            check(
                "reference_manager naming fields live in the header menu (presets capture them)",
                set(_naming) <= _hdr_names
                and set(_naming) <= _preset_widgets
                and rm._naming_menu() is rm_ui.header.menu
                # Seated directly in the row: no option-box container wraps it.
                and rm_ui.footer.main_layout.indexOf(_save_btn) >= 0,
                f"header={sorted(set(_naming) & _hdr_names)} "
                f"presets={sorted(set(_naming) & _preset_widgets)}",
            )
            _preview = rm._save_scene_preview() if _save_btn is not None else ""
            check(
                "reference_manager footer Save button exists and previews the save path",
                _save_btn is not None
                and "Save To Workspace" in _preview
                and (".blend" in _preview or "workspace" in _preview.lower()),
                # ascii-safe: the preview carries "→" glyphs the cp1252
                # console this harness prints to can't encode.
                "preview="
                + _preview[:200].encode("ascii", "backslashreplace").decode("ascii"),
            )
            # The prepopulated Save name keeps the authored casing and any dots:
            # it must read the RAW filepath (_current_scene_file), never the
            # lowercased comparison key (_current_scene_path), and only the
            # extension comes off.
            rm._current_scene_file = lambda: "C:/proj/scenes/Hero.rig.blend"
            _default = rm._default_save_name("None", "")
            del rm._current_scene_file
            check(
                "reference_manager Save prepopulates the authored name (case + dots kept)",
                _default == "Hero.rig",
                f"default={_default!r}",
            )

            # A slots reload rebuilds the instance on the SAME persisted footer:
            # construction must not run again (duplicate buttons).
            def _footer_action_count():
                return sum(
                    1
                    for b in rm_ui.footer.findChildren(QtWidgets.QPushButton)
                    if b.text() in ("Save To Workspace", "Un-Reference All")
                )

            _before = _footer_action_count()
            rm2 = type(rm)(rm.sb)
            check(
                "reference_manager footer actions survive a slots reload without duplicating",
                _footer_action_count() == _before == 2
                and rm_ui.footer._rm_save_btn is _save_btn,
                f"before={_before} after={_footer_action_count()}",
            )
            # ...and the header (which also outlives the reload) re-targets its
            # naming-field edits at the NEW instance, not the dead one.
            rm2.header_init(rm_ui.header)
            _refreshed = []
            rm._refresh = lambda: _refreshed.append("old")
            rm2._refresh = lambda: _refreshed.append("new")
            _hide = rm_ui.header.menu.chk_hide_suffix
            _hide.blockSignals(True)
            _hide.setChecked(True)
            _suffix_w = rm_ui.header.menu.txt_suffix
            _suffix_was = _suffix_w.text()
            _suffix_w.setText(_suffix_was + "_x")
            _suffix_w.setText(_suffix_was)
            _hide.setChecked(False)
            _hide.blockSignals(False)
            del rm._refresh, rm2._refresh
            check(
                "reference_manager a naming-field edit after a reload re-filters the new instance",
                _refreshed == ["new", "new"],
                f"refreshed={_refreshed}",
            )

            # Footer row, left to right: Un-Reference All, then Save To Workspace.
            _unref_i = rm_ui.footer.main_layout.indexOf(rm_ui.footer._rm_unref_btn)
            _save_i = rm_ui.footer.main_layout.indexOf(rm_ui.footer._rm_save_btn)
            check(
                "reference_manager footer: Un-Reference All sits left of Save To Workspace",
                0 <= _unref_i < _save_i,
                f"unref={_unref_i} save={_save_i}",
            )

            rm_tbl = rm_ui.tbl000
            rm_hdr = rm_tbl.horizontalHeader()
            action_cols = (rm.COL_REF, rm.COL_OPEN, rm.COL_DISPLAY)
            check(
                "reference_manager populated 3 file rows from the workspace",
                rm_tbl.rowCount() == 3,
                f"rows={rm_tbl.rowCount()}",
            )
            # The FILES cell tooltip names the file in full: the row label can hide the
            # suffix/extension (header toggles) and elides in a narrow column, so hovering
            # must still identify the file (mirror of mayatk's table).
            _tips = {
                rm_tbl.item(_r, rm.COL_NAME).toolTip()
                for _r in range(rm_tbl.rowCount())
                if rm_tbl.item(_r, rm.COL_NAME)
            }
            check(
                "reference_manager FILES tooltips carry the full file name",
                _tips == {"alpha.blend", "beta.blend", "gamma.blend"},
                f"tooltips={sorted(_tips)}",
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
                    rm_tbl.actions.get(r, rm.COL_REF) in ("referenced", "unreferenced")
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
                    for t in ("ma", "mb", "fbx", "usd", "blend")
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
                b.objectName() for b in rm_tbl.menu.findChildren(QtWidgets.QPushButton)
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
                "reference_manager header Include Types row is not duplicated (5 unique)",
                sorted(_rm_hdr_incl)
                == [
                    "chk_include_blend",
                    "chk_include_fbx",
                    "chk_include_ma",
                    "chk_include_mb",
                    "chk_include_usd",
                ],
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
            # over the live bindings (mirror of the shared Channels panel's reload-rewire test).
            from blendertk.env_utils.reference_manager import ReferenceManagerSlots

            _s2 = ReferenceManagerSlots.__new__(ReferenceManagerSlots)
            _s2.sb = rm.sb
            _s2.ui = rm.ui
            _s2._recursive = rm._recursive
            _s2._suppress_note_save = False
            _open_calls = []
            # Patch BEFORE tbl000_init runs: register_menu_action's handler closure captures
            # `self.open_selected` (the bound method) at wiring time, not via a later lazy
            # attribute lookup, so patching after wiring would silently miss the swap.
            _s2.open_selected = lambda: _open_calls.append(1)
            _s2.tbl000_init(rm_tbl)
            _action_owner = rm_tbl.actions._columns[rm.COL_REF]["states"][
                "unreferenced"
            ]["action"].__self__
            check(
                "reference_manager tbl000_init re-wires the action column to a NEW slots "
                "instance on reload (not left bound to the dead old one)",
                _action_owner is _s2 and _action_owner is not rm,
                f"owner={_action_owner!r}",
            )
            rm_tbl._menu_action_registry["row_open"]["handler"](
                rm_tbl.selectedItems() or []
            )
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
            _probe_w.toggled.connect(
                lambda *a: _internal.append(1)
            )  # stands in for internal wiring
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
                rm._open_path(
                    os.path.join(rm_proj, "robot.ma")
                )  # must NOT raise under .venv
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
            _rm_btk.new_scene = lambda: _log.__setitem__("new", _log["new"] + 1) or True
            _rm_btk.open_scene = lambda p: _log["open"].append(p) or True
            _rm_btk.remove_library = lambda lib: _log["removed"].append(lib) or True
            _rm_btk.link_blend_file = lambda p, **k: _log["linked"].append(p) or 1
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
                _log["open"].clear()
                _log["removed"].clear()
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
                _log["new"] = 0
                _log["linked"].clear()
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
            _rm_orig_import = _rm_si.MayaSceneImport.import_scene
            _rm_si.MayaSceneImport.import_scene = lambda self, p, **k: (
                _rm_calls.append(p) or [object()]
            )
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
                    "reference_manager 'Import (convert)' routes the Maya scene to MayaSceneImport.import_scene",
                    _rm_calls == [_fpath],
                    f"{_rm_calls}",
                )

                # The link icon takes the OTHER path: bake -> link the cached .blend.
                import blendertk as _rm_btk

                _rm_baked, _rm_linked = [], []
                _rm_orig_bake = _rm_si.MayaSceneImport.bake_scene
                _rm_si.MayaSceneImport.bake_scene = lambda self, p, **k: (
                    _rm_baked.append(p) or (p + ".baked.blend")
                )
                _rm_orig_link = _rm_btk.link_blend_file
                _rm_btk.link_blend_file = lambda p, **k: _rm_linked.append(p) or 1
                # list_libraries() needs bpy; the row is unlinked in this fixture anyway.
                rm._library_for_path = lambda p: None
                # The mutual-exclusion check calls _is_current -> _current_scene_path, which imports
                # bpy once _has_bpy() is stubbed True above; the foreign row is not the open scene
                # in this fixture, so stub it out (no bpy under the .venv).
                rm._current_scene_path = lambda: ""
                # A linked Maya bake hides the viewports' Relationship Lines, which
                # walks bpy.data.screens -- recorded here instead (no bpy under the .venv).
                _rm_hid = []
                _rm_orig_hide = _rm_si.MayaSceneImport.hide_relationship_lines
                _rm_si.MayaSceneImport.hide_relationship_lines = staticmethod(
                    lambda: _rm_hid.append(True) or 1
                )
                try:
                    rm._toggle_reference_at_row(_fr, rm.COL_REF)
                    check(
                        "reference_manager link icon bakes a foreign row and links the bake",
                        _rm_baked == [_fpath]
                        and _rm_linked == [_fpath + ".baked.blend"],
                        f"baked={_rm_baked} linked={_rm_linked}",
                    )
                    check(
                        "reference_manager hides Relationship Lines once a bake is linked",
                        _rm_hid == [True],
                        f"{_rm_hid}",
                    )
                finally:
                    _rm_si.MayaSceneImport.bake_scene = _rm_orig_bake
                    _rm_si.MayaSceneImport.hide_relationship_lines = _rm_orig_hide
                    _rm_btk.link_blend_file = _rm_orig_link
                    del rm._library_for_path, rm._current_scene_path
            finally:
                _rm_si.MayaSceneImport.import_scene = _rm_orig_import
                del rm._has_bpy, rm._refresh, rm.sb.message_box

            # Notes are keyed by the scene's project-relative path (the project
            # store both DCCs share, ``ptk.SceneNotes``), so a note must follow
            # its file: a rename re-keys it, a delete drops it. Real on-disk
            # rename/delete (no bpy: nothing is "open").
            _nd = os.path.join(rm_tmp, "notes_probe")  # outside the 'proj' workspace
            os.makedirs(_nd)
            _old = os.path.join(_nd, "hero.blend")
            open(_old, "wb").close()
            _new = os.path.join(_nd, "villain.blend")
            rm.scene_notes.set(_old, "keep me")
            rm.sb.input_dialog = lambda *a, **k: "villain"
            rm.sb.message_box = lambda *a, **k: "Yes"
            rm._refresh = lambda: None
            # The row the menu was opened on (it was _selected_paths before the
            # row menu acted on the right-clicked row alone).
            rm._context_paths = lambda: [_new]
            try:
                rm._rename_path(_old)
                _moved = rm.scene_notes.get(_new)
                _left = bool(rm.scene_notes.get(_old))
                rm.delete_selected()
                _after = rm.scene_notes.get(_new)
                check(
                    "reference_manager a note follows its file through rename and delete",
                    _moved == "keep me"
                    and not _left
                    and not _after
                    and not os.path.exists(_new),
                    f"moved={_moved!r} left_on_old={_left} after_delete={_after!r}",
                )
            finally:
                rm.scene_notes.remove(_old)
                rm.scene_notes.remove(_new)
                del rm.sb.input_dialog, rm.sb.message_box, rm._refresh
                del rm._context_paths

            # Save appends the suffix, so one typed into its dialog must come off
            # first (mirror of mayatk). The file name alone never doubled here
            # (format_scene_name skips a suffix the name ends with), but the
            # {name} folder resolves from the typed name: "villain_v01" landed in
            # a villain_v01/ folder instead of villain/.
            _saves = []
            _orig_save_as = _rm_btk.save_scene_as
            _rm_btk.save_scene_as = lambda ws, name, **kw: _saves.append((ws, name, kw))
            _suffix_w = rm._naming_menu().txt_suffix
            _struct_w = rm._naming_menu().txt_subfolder_structure
            _orig_suffix, _orig_struct = _suffix_w.text(), _struct_w.text()
            _suffix_w.setText("_v01")
            _struct_w.setText("{scenes}/{name}")
            rm.sb.input_dialog = lambda *a, **k: "villain_v01"
            rm.sb.message_box = lambda *a, **k: None
            rm._refresh = lambda: None
            try:
                rm.save_scene()
                _ws, _name, _kw = _saves[0] if _saves else (None, None, {})
                _saved_as = (
                    os.path.relpath(
                        _rm_btk.EnvUtils.scene_save_path(
                            _ws,
                            _name,
                            case=_kw.get("case"),
                            suffix=_kw.get("suffix", ""),
                            subfolder=_kw.get("subfolder", ""),
                        ),
                        _ws,
                    )
                    if _saves
                    else None
                )
                check(
                    "reference_manager a suffix typed into Save is written once "
                    "(file and {name} folder)",
                    _saved_as == os.path.join("scenes", "villain", "villain_v01.blend"),
                    f"saved_as={_saved_as!r} name={_name!r}",
                )
            finally:
                _rm_btk.save_scene_as = _orig_save_as
                _suffix_w.setText(_orig_suffix)
                _struct_w.setText(_orig_struct)
                del rm.sb.input_dialog, rm.sb.message_box, rm._refresh
        finally:
            rm_ui.txt000.clear()
            _rm_shutil.rmtree(rm_tmp, ignore_errors=True)
    else:
        check(
            "reference_manager exposes slots for the icon-column check",
            False,
            "no slots",
        )

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
