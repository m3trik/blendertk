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

# Co-located tool panels that BlenderUiHandler must discover from the blendertk package.
PANELS = [
    "curtain",
    "mirror",
    "bevel",
    "bridge",
    "snap",
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
    "color_manager",
    "exploded_view",
    "calculator",
    "texture_path_editor",
    "image_to_plane",
    "shader_templates",
    "mat_updater",
    "rizom_bridge",
    "maya_bridge",
    "game_shader",
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

try:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from uitk import Switchboard
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    sb = Switchboard()
    handler = BlenderUiHandler(switchboard=sb)

    # 1. The handler's recursive scan of the blendertk package registers exactly the eight
    #    co-located tool panels (and nothing spurious) — the core architectural guarantee.
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
        ("maya_bridge", "MayaBridgeSlots"),  # engine/slots init is bpy-free
        ("game_shader", "GameShaderSlots"),  # cmb001_init bpy-free (static OpenGL/DirectX list)
        ("channels", "ChannelsSlots"),  # __init__ + table/header init are bpy-free (guarded refresh)
        ("exploded_view", "ExplodedViewSlots"),  # __init__ (logging only) is bpy-free
        ("snap", "SnapSlots"),  # __init__ + option-box b###_init are bpy-free
        ("reference_manager", "ReferenceManagerSlots"),  # *_init bpy-guarded → table degrades w/o bpy
        ("hdr_manager", "HdrManagerSlots"),  # __init__ + header/cmb _init are bpy-free (os dir scan)
        ("dynamic_pipe", "DynamicPipeSlots"),  # __init__ (logging only) is bpy-free
        ("image_tracer", "ImageTracerSlots"),  # __init__ + txt000/b005 _init are bpy-free
        ("curve_to_tube", "CurveToTubeSlots"),  # __init__ (Preview/connect_multi/combo) is bpy-free
        ("image_to_plane", "ImageToPlaneSlots"),  # __init__ (button connects) is bpy-free
        ("naming", "NamingSlots"),  # __init__ + option-box *_init are bpy-free
        ("telescope_rig", "TelescopeRigSlots"),  # __init__ (logging + btn connect) is bpy-free
        ("wheel_rig", "WheelRigSlots"),  # __init__ (logging + btn connect) is bpy-free
        ("shadow_rig", "ShadowRigSlots"),  # __init__ (Preview + btn connect) is bpy-free
        ("render_opacity", "RenderOpacitySlots"),  # __init__ (btn connect) is bpy-free
        ("tube_rig", "TubeRigSlots"),  # __init__ (mode combo + dynamic options) is bpy-free
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
