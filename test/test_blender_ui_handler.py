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
    "macro_manager",
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
    "hierarchy_manager",
    "scene_exporter",
    "smart_bake",
    "channels",
    "telescope_rig",
    "wheel_rig",
    "shadow_rig",
    "render_opacity",
    "tube_rig",
]

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    from qtpy import QtWidgets  # noqa: F401
except Exception:
    # Blender headless ships no Qt binding — this suite is a .venv target. Skip cleanly.
    print("SKIP test_blender_ui_handler (no Qt binding — run under the workspace .venv)")
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
        ("bevel", "BevelSlots"),  # Preview/connect_multi init is bpy-free -> loadable under .venv
        ("bridge", "BridgeSlots"),  # Preview/connect_multi + setVisible init is bpy-free
        ("calculator", "CalculatorSlots"),
        ("shader_templates", "ShaderTemplatesSlots"),  # bpy-free init -> loadable under .venv
        ("mat_updater", "MatUpdaterSlots"),  # engine defers bpy; cmb001_init is bpy-free
        ("rizom_bridge", "RizomBridgeSlots"),  # engine/slots init is bpy-free
        ("shell_xform", "ShellXformSlots"),  # __init__ (logging + deferred icons/uitk) is bpy-free
        ("maya_bridge", "MayaBridgeSlots"),  # engine/slots init is bpy-free
        ("unity_bridge", "UnityBridgeSlots"),  # engine/slots init is bpy-free (unitytk lookup guarded)
        ("marmoset_bridge", "MarmosetBridgeSlots"),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        ("substance_bridge", "SubstanceBridgeSlots"),  # BridgeSlotsBase init is bpy-free (engine defers bpy)
        ("game_shader", "GameShaderSlots"),  # cmb001_init bpy-free (static OpenGL/DirectX list)
        ("channels", "ChannelsSlots"),  # __init__ + table/header init are bpy-free (guarded refresh)
        ("exploded_view", "ExplodedViewSlots"),  # __init__ (logging only) is bpy-free
        ("color_id", "ColorIdSlots"),  # __init__ (button groups + keep_square swatches) is bpy-free
        ("snap", "SnapSlots"),  # __init__ + option-box b###_init are bpy-free
        ("macro_manager", "MacroManagerSlots"),  # __init__ + table/header/cmb _init are bpy-free
        ("audio_clips", "AudioClipsSlots"),  # __init__ + cmb000/tb001/b004 _init are bpy-free (list refresh guarded)
        ("blendshape_animator", "BlendshapeAnimatorSlots"),  # __init__ + header/b000/cmb000/le001/b001/b004/b006/b008 _init are bpy-free (tree stays empty without bpy)
        ("reference_manager", "ReferenceManagerSlots"),  # *_init bpy-guarded → table degrades w/o bpy
        ("hdr_manager", "HdrManagerSlots"),  # __init__ + header/cmb _init are bpy-free (os dir scan)
        ("dynamic_pipe", "DynamicPipeSlots"),  # __init__ (logging only) is bpy-free
        ("image_tracer", "ImageTracerSlots"),  # __init__ + header/txt000 _init are bpy-free
        ("curve_to_tube", "CurveToTubeSlots"),  # __init__ (Preview/connect_multi/combo) is bpy-free
        ("image_to_plane", "ImageToPlaneSlots"),  # __init__ (button connects) is bpy-free
        ("naming", "NamingSlots"),  # __init__ + option-box *_init are bpy-free
        ("telescope_rig", "TelescopeRigSlots"),  # __init__ (logging + btn connect) is bpy-free
        ("wheel_rig", "WheelRigSlots"),  # __init__ (logging + btn connect) is bpy-free
        ("shadow_rig", "ShadowRigSlots"),  # __init__ (Preview + btn connect) is bpy-free
        ("render_opacity", "RenderOpacitySlots"),  # __init__ (btn connect) is bpy-free
        ("tube_rig", "TubeRigSlots"),  # __init__ (mode combo + dynamic options) is bpy-free
        ("lightmap_baker", "LightmapBakerSlots"),  # __init__ + cmb _init are bpy-free (preset store)
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
        "reference_manager", "color_id", "exploded_view", "bridge",
        "cut_on_axis", "mirror", "shell_xform", "naming",
    ]
    for panel in GESTURE_SCOPED:
        gs_ui = sb.get_ui(panel)
        gs_slots = getattr(gs_ui, "slots", None)
        if gs_slots is None:
            check(f"{panel} exposes slots for the gesture-scoped check", False, "no slots")
            continue
        gs_slots.header_init(gs_ui.header)
        gs_buttons = set(getattr(gs_ui.header, "buttons", {}))
        check(
            f"{panel} is gesture-scoped: pin button, no hide button",
            "pin" in gs_buttons and "hide" not in gs_buttons,
            f"{sorted(gs_buttons)}",
        )

    # maya_bridge: render_template substitutes the FBX path + params (Qt path; needs no bpy, so it
    # belongs here rather than in the headless test_maya_bridge harness which lacks Qt).
    from blendertk.env_utils.maya_bridge._maya_bridge import MayaBridge
    from blendertk.env_utils.maya_bridge import parameters as _mb_params
    rendered = MayaBridge(maya_path="C:/fake/maya.exe").render_template(
        "import", r"C:\t\x.fbx", _mb_params.defaults()
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
    from blendertk.env_utils.unity_bridge.unity_bridge_slots import UnityBridgeSlots as _UBS
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

    # macro_manager: table/filter/header wiring is entirely Qt-driven off the bpy-free
    # MacroManager management API (list_available_macros/macro_category/get_current_bindings
    # are pure introspection — see blendertk.edit_utils.macros) — so the panel should populate
    # for real under the offscreen .venv, not just resolve to an empty stub.
    mm_ui = sb.get_ui("macro_manager")
    mm = getattr(mm_ui, "slots", None)
    if mm is not None:
        # tbl000_init (and the row_names/table it fills) is lazily triggered on first touch
        # of ``ui.tbl000`` — touch it before reading anything table-derived, cmb000 included
        # (tbl000_init also calls cmb000_init itself, so this alone settles both).
        row_count = mm_ui.tbl000.rowCount()
        macro_col_labels = {
            mm_ui.tbl000.item(r, mm.COL_MACRO).text() for r in range(row_count)
        }
        row_names = set(mm._row_names)
        check(
            "macro_manager table populated from list_available_macros (bpy-free)",
            "m_back_face_culling" in row_names and "m_frame" in row_names and row_count == len(row_names),
            f"row_count={row_count} {sorted(row_names)}",
        )
        check(
            "macro_manager humanizes macro names in the Macro column",
            "Back Face Culling" in macro_col_labels,
            f"{sorted(macro_col_labels)}",
        )
        cat_items = [mm_ui.cmb000.itemText(i) for i in range(mm_ui.cmb000.count())]
        check(
            "macro_manager category combo lists All + mixin-derived categories",
            cat_items[:1] == ["All"] and {"Display", "Edit", "Selection"} <= set(cat_items),
            f"{cat_items}",
        )
        check(
            "macro_manager header menu wires Clear All / Reset to Default",
            hasattr(mm_ui.header.menu, "hdr_clear_all")
            and hasattr(mm_ui.header.menu, "hdr_reset_default"),
        )
        check(
            "macro_manager installed the in-cell Category choice delegate",
            mm._category_delegate is not None,
        )
        preset_combo = getattr(mm_ui.header.menu, "cmb_presets", None)
        preset_items = (
            [preset_combo.itemText(i) for i in range(preset_combo.count())]
            if preset_combo else []
        )
        check(
            "macro_manager preset combo lists the shipped 'default' preset",
            "default" in preset_items,
            f"{preset_items}",
        )
    else:
        check("macro_manager exposes slots for the table check", False, "no slots")

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
            all(hasattr(cmb_menu, n) for n in (
                "btn_rename_track", "btn_replace_track", "btn_remove_selected", "btn_remove_audio",
            )),
        )
        from uitk.widgets.optionBox.options.action import ActionOption

        check(
            "audio_clips tb001 option box wires reveal (select) + sync (refresh) actions",
            ac_ui.tb001.option_box.find_option(ActionOption) is not None,
        )
        check(
            "audio_clips b004 option box wires Extend Only (default checked)",
            hasattr(ac_ui.b004.option_box.menu, "chk_extend_only")
            and ac_ui.b004.option_box.menu.chk_extend_only.isChecked(),
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
            any("Spline" in m for m in modes) and any("Anchor" in m for m in modes)
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
        anchor_idx = next(i for i in range(tcmb.count()) if "Anchor" in tcmb.itemText(i))
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
        res_items = [lb_ui.cmb_resolution.itemText(i) for i in range(lb_ui.cmb_resolution.count())]
        check(
            "lightmap_baker Resolution combo lists the fixed sizes (default 1024)",
            res_items == [f"Resolution:\t{r}" for r in (256, 512, 1024, 2048, 4096)]
            and lb._resolution() == 1024,
            f"{res_items} _resolution()={lb._resolution()}",
        )
        scope_items = [lb_ui.cmb_scope.itemText(i) for i in range(lb_ui.cmb_scope.count())]
        check(
            "lightmap_baker Scope combo lists Selected/Visible/Scene (default selected)",
            scope_items == ["Selected", "Visible", "Scene"] and lb._scope() == "selected",
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

    # channels: Compact View + the wheel-scrub step ladder are Qt-only (no bpy), so exercise them
    # on the real loaded panel here. Compact collapses row height + hides the table column header;
    # the ladder scales ×10 (Ctrl) / ×100 (Ctrl+Shift) / ÷10 (Alt) per modifier (mirror of Maya).
    channels_ui = sb.get_ui("channels")
    cslots = getattr(channels_ui, "slots", None)
    ctbl = getattr(channels_ui, "tbl000", None)
    if cslots is not None and ctbl is not None:
        vh = ctbl.verticalHeader()
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

        fake = _FakeCtl()
        cslots.controller = fake
        desc = {"name": "location_x", "data_path": "location", "index": 0,
                "kind": "transform", "type": "float", "is_angle": False}
        cslots._row_descriptors = [desc]
        COL_V = cslots.COL_VALUE
        # MMB scrub: dx=10 px * 0.01 step = +0.1 applied to each object's own start.
        cslots._on_scrub_started(0, COL_V)
        cslots._on_scrub_moved(0, COL_V, 10, 0)
        scrub_ok = (round(fake.vals["a"], 4) == 1.1 and round(fake.vals["b"], 4) == 2.1
                    and len(fake.sets) == 2)
        cslots._on_scrub_finished(0, COL_V)
        check("channels MMB-scrub applies per-object delta in display space", scrub_ok,
              f"vals={fake.vals} sets={fake.sets}")

        # Wheel (display mode, no editor open, no modifier): +1 notch * 0.1 default step.
        fake.vals = {"a": 1.0, "b": 2.0}
        fake.sets = []
        Qt = cslots.sb.QtCore.Qt
        cslots._on_wheel_scrolled(0, COL_V, 1, Qt.NoModifier)
        wheel_ok = round(fake.vals["a"], 4) == 1.1 and round(fake.vals["b"], 4) == 2.1
        check("channels wheel-scrub steps each object by the modifier's step", wheel_ok,
              f"vals={fake.vals}")
    else:
        check("channels exposes slots + table for compact-view check", False, "missing slots/tbl000")

except Exception as e:
    traceback.print_exc()
    check("handler discovery raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
