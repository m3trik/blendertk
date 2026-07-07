"""blendertk style_setter headless test — the native interface_theme preset install/apply surface.
Run: blender --background --factory-startup --python blendertk/test/test_style_setter.py

Session safety: this suite installs theme presets, which write into Blender's per-user
``presets/interface_theme`` dir. To NEVER touch the user's real Themes dropdown / config, it
redirects ``BLENDER_USER_SCRIPTS`` to a throwaway temp dir BEFORE any preset op runs (verified live
that ``bpy.utils.user_resource`` and ``interface_theme_preset_add`` honor the env var set at runtime),
and removes the sandbox in a finally. Theme application only mutates in-memory prefs of this throwaway
``--background`` process and never calls ``wm.save_userpref`` (persist defaults False), so the real
preferences file is untouched too.

``StyleSetter`` has no backup/restore of its own (removed 2026-07-05 — see the package CHANGELOG):
reverting to the user's own look is just picking their own (built-in or saved) theme from the same
native list ``list_templates()`` already scans, so this suite proves THAT instead of a bespoke
"Default" entry — see the "reverts to Blender's factory look" check below.
"""
import sys, os, tempfile, shutil, traceback

# --- redirect ALL Blender preset reads/writes to a sandbox up front (before importing the module
# or running any preset op) so nothing lands in the user's real config.
_SANDBOX = tempfile.mkdtemp(prefix="btk_style_setter_test_")
os.environ["BLENDER_USER_SCRIPTS"] = _SANDBOX

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    import blendertk as btk
    from blendertk.ui_utils.style_setter import _style_setter as ss
    from blendertk.ui_utils.style_setter._style_setter import StyleSetter

    # sanity — we really are sandboxed (guards the whole suite against polluting real config)
    check("preset dir is sandboxed, not the user's real config", _SANDBOX in ss.user_preset_dir(create=True))

    theme = bpy.context.preferences.themes[0]
    ui = theme.user_interface

    # ---- shipped template presence (native XML only — no JSON supplement any more)
    check("list_styles() ships 'Maya'", "Maya" in ss.list_styles(), str(ss.list_styles()))
    check("no JSON left in styles/ (pure native)", not any(f.endswith(".json") for f in os.listdir(ss.STYLES_DIR)), str(os.listdir(ss.STYLES_DIR)))

    # ---- install() puts Maya.xml where Blender's native Themes dropdown reads from
    ss.install()
    check("install() copies Maya.xml into the user preset dir", ss.is_installed("Maya"))
    scan = []
    for d in bpy.utils.preset_paths("interface_theme"):
        if os.path.isdir(d):
            scan += [f for f in os.listdir(d) if f.endswith(".xml")]
    check("Maya.xml is visible to the native Themes dropdown scan", "Maya.xml" in scan, str(sorted(set(scan))))

    # ---- list_templates() is the native Themes list: built-ins + user + our injected Maya
    templates = ss.list_templates()
    check("list_templates() returns a {display_name: filepath} mapping", isinstance(templates, dict) and all(os.path.isfile(v) for v in templates.values()), str(list(templates)))
    check("list_templates() includes our injected 'Maya'", "Maya" in templates, str(list(templates)))
    check("list_templates() includes Blender's built-in themes (e.g. Blender Dark)", any("Blender" in k for k in templates), str(list(templates)))
    check("StyleSetter has no bespoke backup/restore API (removed — Blender's own built-ins cover reverting)", not any(hasattr(ss, n) for n in ("BACKUP_NAME", "ensure_backup", "backup_current", "restore_default_style", "_backup_stem")))

    # ---- factory baseline, then apply the 'Maya' template BY TOKEN (as the combo does)
    bpy.ops.preferences.reset_default_theme()
    factory_inner = tuple(round(v, 3) for v in ui.wcol_regular.inner)

    ss.apply_template(templates["Maya"])
    inner = tuple(round(v, 3) for v in ui.wcol_regular.inner)
    check("apply_template(Maya) applies the Maya button fill (~0.365 / #5d5d5d)", abs(inner[0] - 0.365) < 0.01, str(inner))
    check("apply_template(Maya) flattens widget roundness to 0", ui.wcol_regular.roundness == 0.0)
    check(
        "apply_template(Maya) applies the Maya viewport LINEAR gradient",
        theme.view_3d.space.gradients.background_type == "LINEAR",
        theme.view_3d.space.gradients.background_type,
    )

    # ---- refinements pulled from Maya's live QPalette: slider/progress fills use Maya's
    # Highlight blue (#5285a6), and the Node Editor reads like Maya's (gray node bodies on a
    # DARK canvas) rather than the inverted dark-nodes-on-light default.
    slider = tuple(round(v, 3) for v in ui.wcol_numslider.item)[:3]
    check(
        "apply_template(Maya) sets the slider fill to Maya Highlight (#5285a6)",
        all(abs(a - b) < 0.012 for a, b in zip(slider, (0.322, 0.522, 0.651))),
        str(slider),
    )
    ne = theme.node_editor
    canvas_lum = sum(ne.space.back[:3]) / 3.0
    body_lum = sum(ne.node_backdrop[:3]) / 3.0
    check(
        "Node Editor nodes stay readable (gray body lighter than the dark Maya canvas)",
        body_lum > canvas_lum + 0.05,
        f"canvas={canvas_lum:.3f} body={body_lum:.3f}",
    )

    # ---- Blender's OWN built-in theme is already in the list — reverting is just picking it,
    # exactly like the native dropdown; no bespoke backup/restore of our own is needed.
    blender_dark = next((k for k in templates if "Dark" in k), None)
    if blender_dark is None:
        check("a Blender built-in 'Dark' theme is present to revert to", False, str(list(templates)))
    else:
        ss.apply_template(templates[blender_dark])
        restored = tuple(round(v, 3) for v in ui.wcol_regular.inner)
        check(
            f"apply_template({blender_dark!r}) reverts to Blender's own factory look",
            all(abs(a - b) < 0.006 for a, b in zip(restored, factory_inner)),
            f"{factory_inner} -> {restored}",
        )

    # ---- the name-based helper still works + guard unknown names
    ss.set_style("Maya")
    check("set_style('Maya') still applies via the name-based path", abs(round(ui.wcol_regular.inner[0], 3) - 0.365) < 0.01)
    try:
        ss.apply_theme_preset("ZZNoSuchTheme")
        check("apply_theme_preset raises FileNotFoundError for an unknown name", False)
    except FileNotFoundError:
        check("apply_theme_preset raises FileNotFoundError for an unknown name", True)

    # ---- public surface: StyleSetter is the registered class (like Bevel/Selection); its helpers
    # live on the class + as module fns, and are deliberately NOT sprayed into the flat btk.* namespace.
    check("btk.StyleSetter resolves (the registered public surface)", getattr(btk, "StyleSetter", None) is StyleSetter)
    for fn in (
        "list_styles",
        "list_templates",
        "apply_template",
        "user_preset_dir",
        "user_preset_path",
        "is_installed",
        "install",
        "apply_theme_preset",
        "set_style",
    ):
        check(f"StyleSetter.{fn} is callable", callable(getattr(StyleSetter, fn, None)))
        check(f"module-level {fn} is callable", callable(getattr(ss, fn, None)))
    # the old font-supplement API must be gone (native-only now)
    check("supplement API removed (apply_supplement gone)", not hasattr(ss, "apply_supplement") and not hasattr(StyleSetter, "apply_supplement"))
    # the generic helper names must NOT leak into the flat btk.* namespace (collision hygiene)
    check("generic helpers are NOT dumped into btk.* (e.g. btk.install absent)", not hasattr(btk, "install") and not hasattr(btk, "is_installed"))

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")
finally:
    shutil.rmtree(_SANDBOX, ignore_errors=True)

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
