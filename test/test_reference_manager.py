"""blendertk Reference Manager engine headless test — verifies the bpy-side library functions that
back the co-located ``reference_manager`` panel (the Qt slot can't run headless: no Qt binding in
Blender; panel structure/wiring is covered by ``test_blender_ui_handler.py`` under the .venv).

Run: blender --background --factory-startup --python blendertk/test/test_reference_manager.py
"""
import sys, os, tempfile, shutil, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

tmp = tempfile.mkdtemp(prefix="refmgr_test_")
try:
    import bpy
    import blendertk as btk

    # --- author a library .blend (a collection with a cube), save it to disk -----------------
    lib_path = os.path.join(tmp, "libs", "kit.blend")
    os.makedirs(os.path.dirname(lib_path), exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.data.collections.new("LibColl")
    bpy.context.scene.collection.children.link(coll)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    coll.objects.link(cube)
    bpy.ops.wm.save_as_mainfile(filepath=lib_path)

    # a second .blend, for filter testing
    other_path = os.path.join(tmp, "libs", "prop_chair.blend")
    bpy.ops.wm.save_as_mainfile(filepath=other_path)

    # --- fresh empty scene: the consumer ------------------------------------------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # 1. find_blend_files — discovers both .blend files under the root.
    files = btk.find_blend_files(tmp, recursive=True)
    check("find_blend_files finds both .blend", set(map(os.path.normcase, files)) ==
          {os.path.normcase(lib_path), os.path.normcase(other_path)}, f"{files}")

    # 2. filter — narrows by wildcard name.
    filtered = btk.find_blend_files(tmp, filter_text="kit*")
    check("find_blend_files filter narrows", [os.path.basename(p) for p in filtered] == ["kit.blend"])

    # 3. not linked yet.
    check("is_blend_linked False before link", not btk.is_blend_linked(lib_path))

    # 4. link_blend_file — links the collection + instances it; library now present.
    n = btk.link_blend_file(lib_path, link=True)
    check("link_blend_file links a collection", n >= 1, f"count={n}")
    check("is_blend_linked True after link", btk.is_blend_linked(lib_path))
    check("collection instanced into the scene", any(
        o.instance_type == "COLLECTION" and o.instance_collection and o.instance_collection.name == "LibColl"
        for o in bpy.data.objects))

    # 5. list_libraries — the record carries the live datablock + existence.
    recs = btk.list_libraries()
    rec = next((r for r in recs if r["abspath"].lower() == os.path.normpath(lib_path).lower()), None)
    check("list_libraries reports the linked file", rec is not None and rec["exists"])

    # 6. reload then remove.
    check("reload_library succeeds", btk.reload_library(rec["library"]))
    check("remove_library succeeds", btk.remove_library(rec["library"]))
    check("is_blend_linked False after remove", not btk.is_blend_linked(lib_path))

    # 7. append (link=False) brings a LOCAL copy in (datablock.library is None — unlike link,
    #    a lingering 0-user library datablock may persist until orphan purge, so the meaningful
    #    check is locality, not is_blend_linked).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    n2 = btk.link_blend_file(lib_path, link=False)
    check("append brings the collection in", n2 >= 1)
    appended = bpy.data.collections.get("LibColl")
    check("append makes a local copy (library is None)",
          appended is not None and appended.library is None)

    # 8. make_library_local — link, then make the library's data local + drop the library.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    rec = next((r for r in btk.list_libraries()
                if r["abspath"].lower() == os.path.normpath(lib_path).lower()), None)
    check("library present before make_local", rec is not None)
    made = btk.make_library_local(rec["library"])
    check("make_library_local localizes datablocks", made >= 1, f"count={made}")
    check("make_library_local drops the now-unused library", not btk.is_blend_linked(lib_path))
    local_coll = bpy.data.collections.get("LibColl")
    check("make_library_local leaves the data local (library is None)",
          local_coll is not None and local_coll.library is None)

    # 9. make_library_local on a bogus name → 0 (no crash).
    check("make_library_local(unknown) → 0", btk.make_library_local("does_not_exist") == 0)

    # 10. find_workspaces — the project folders under a root (subdirs holding .blend).
    ws = btk.find_workspaces(tmp)
    check("find_workspaces finds the libs project folder",
          [os.path.normcase(w) for w in ws] == [os.path.normcase(os.path.join(tmp, "libs"))], f"{ws}")
    ws_root = btk.find_workspaces(os.path.join(tmp, "libs"))
    check("find_workspaces includes a root that directly holds .blend",
          os.path.normcase(os.path.join(tmp, "libs")) in [os.path.normcase(w) for w in ws_root])
    check("find_workspaces(bad dir) → []", btk.find_workspaces(os.path.join(tmp, "nope")) == [])

    # 11. format_scene_name — case + suffix, suffix not duplicated; combo values must be set_case-valid.
    check("format_scene_name applies case + suffix", btk.format_scene_name("cube", "upper", "_GEO") == "CUBE_GEO")
    check("format_scene_name doesn't double the suffix", btk.format_scene_name("CUBE_GEO", None, "_GEO") == "CUBE_GEO")
    check("format_scene_name camel lowercases first letter", btk.format_scene_name("MyScene", "camel") == "myScene")
    check("format_scene_name pascal capitalizes first letter", btk.format_scene_name("myScene", "pascal") == "MyScene")
    from blendertk.env_utils.reference_manager import _CASE_STYLES
    _valid_cases = {"upper", "lower", "capitalize", "swapcase", "title", "pascal", "camel", "None"}
    check("RM case-style combo values are all set_case-valid (no silent no-ops)",
          all(c in _valid_cases for c in _CASE_STYLES), str(_CASE_STYLES))

    # 12. save_scene_as — saves the current scene under a workspace with naming applied.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ws_dir = os.path.join(tmp, "proj")
    os.makedirs(ws_dir, exist_ok=True)
    saved = btk.save_scene_as(ws_dir, "shot", case="lower", suffix="_v01")
    check("save_scene_as writes a named .blend",
          saved is not None and os.path.isfile(saved) and os.path.basename(saved) == "shot_v01.blend", str(saved))

    # 13. save_scene_as with a subfolder pattern.
    sub = btk.save_scene_as(ws_dir, "hero", subfolder="scenes/{name}")
    check("save_scene_as honors the subfolder pattern",
          sub is not None and os.path.normcase(os.path.dirname(sub)).endswith(os.path.normcase(os.path.join("scenes", "hero"))),
          str(sub))

    # 14. rename_scene_file — renames on disk; refuses a real name clash without side effects.
    renamed = btk.rename_scene_file(saved, "shot_final")
    check("rename_scene_file renames the .blend",
          renamed is not None and os.path.isfile(renamed) and not os.path.exists(saved))
    clash = os.path.join(os.path.dirname(renamed), "taken.blend")
    open(clash, "w").close()
    check("rename_scene_file refuses an existing target (no side effect)",
          btk.rename_scene_file(renamed, "taken") is None and os.path.isfile(renamed))

    # 14b. renaming the OPEN file is save-then-reopen: the edits authored since the last save go
    #      into the renamed file, and the session tracks the new path. Without it bpy.data.filepath
    #      keeps pointing at a name that no longer exists and the next save resurrects the old file.
    open_path = btk.save_scene_as(ws_dir, "live")  # this is now the open file
    bpy.ops.mesh.primitive_cube_add()  # an edit made after that save
    bpy.context.active_object.name = "RenameProbe"
    moved = btk.rename_scene_file(open_path, "live_renamed")
    check("rename_scene_file renames the open .blend",
          moved is not None and os.path.isfile(moved) and not os.path.exists(open_path), str(moved))
    check("the session tracks the renamed file, not the old name",
          moved is not None and os.path.normcase(os.path.normpath(bpy.data.filepath)) == os.path.normcase(moved),
          bpy.data.filepath)
    check("the unsaved edit was saved into the renamed file", "RenameProbe" in bpy.data.objects)
    check("the .blend1 backup followed the rename (no leftover under the old name)",
          moved is not None and os.path.isfile(moved + "1") and not os.path.exists(open_path + "1"))
    bpy.ops.wm.save_mainfile()
    check("saving after the rename doesn't resurrect the old name", not os.path.exists(open_path))
    btk.delete_scene_file(moved)

    # 15. delete_scene_file — removes it.
    check("delete_scene_file removes the .blend", btk.delete_scene_file(renamed) and not os.path.exists(renamed))
    check("delete_scene_file(missing) → False", not btk.delete_scene_file(renamed))

    # 16. set/get_reference_display_mode — tri-state on a linked library's instance objects.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    rec = next((r for r in btk.list_libraries()
                if r["abspath"].lower() == os.path.normpath(lib_path).lower()), None)
    check("display mode defaults to off", btk.get_reference_display_mode(rec["library"]) == "off")
    btk.set_reference_display_mode(rec["library"], "template")
    check("template → WIRE + locked + reported", btk.get_reference_display_mode(rec["library"]) == "template")
    inst = next((o for o in bpy.data.objects if o.instance_type == "COLLECTION"), None)
    check("template sets the instance to WIRE + hide_select",
          inst is not None and inst.display_type == "WIRE" and inst.hide_select)
    btk.set_reference_display_mode(rec["library"], "reference")
    check("reference → locked, normal shading", btk.get_reference_display_mode(rec["library"]) == "reference"
          and inst.display_type == "TEXTURED" and inst.hide_select)
    btk.set_reference_display_mode(rec["library"], "off")
    check("off → normal, unlocked", btk.get_reference_display_mode(rec["library"]) == "off" and not inst.hide_select)
    raised = False
    try:
        btk.set_reference_display_mode(rec["library"], "bogus")
    except ValueError:
        raised = True
    check("invalid display mode raises", raised)

    # 17. open_scene — opens a .blend (replaces the file). Run LAST (resets bpy state).
    check("open_scene opens the file", btk.open_scene(lib_path) and
          os.path.normcase(os.path.normpath(bpy.data.filepath)) == os.path.normcase(os.path.normpath(lib_path)))
    check("open_scene(missing) → False", not btk.open_scene(os.path.join(tmp, "nope.blend")))

    # 18. new_scene — the 'close' counterpart: discard the open file, empty untitled scene.
    check("new_scene resets to an empty, unsaved scene",
          btk.new_scene() and bpy.data.filepath == "" and len(bpy.data.objects) == 0)

    # 19. Unsaved-work guard. bpy.data.is_dirty follows the UNDO stack, so a single click in the
    # viewport flips it on a brand-new, never-saved, empty scene (verified live in 5.1 — one
    # ed.undo_push in a VIEW_3D context is enough), which made the panel's discard prompt fire
    # with nothing to lose. Not reproducible headless (undo pushes need a real UI context), so the
    # decision is pinned through the pure predicate plus the real content probe.
    from blendertk.env_utils._env_utils import EnvUtils as _EU
    check("dirty + never saved + no content → nothing to lose",
          _EU._is_unsaved_work(True, False, False) is False)
    check("dirty + never saved + content → unsaved work",
          _EU._is_unsaved_work(True, False, True) is True)
    check("dirty + saved file → unsaved work (flag is meaningful once on disk)",
          _EU._is_unsaved_work(True, True, False) is True)
    check("not dirty → never unsaved work",
          _EU._is_unsaved_work(False, True, True) is False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    check("empty scene has no content", not btk.scene_has_content())
    check("pristine empty scene reports no unsaved changes", not btk.scene_has_unsaved_changes())
    bpy.ops.object.camera_add()
    bpy.ops.object.light_add()
    check("camera + light only is still the default document", not btk.scene_has_content())
    bpy.ops.mesh.primitive_cube_add()
    check("a mesh counts as content", btk.scene_has_content())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.data.texts.new("notes")
    check("a text datablock counts as content", btk.scene_has_content())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    check("a linked library counts as content", btk.scene_has_content())

    # --- Slot bulk-operation routing (stub ui/sb; bypass the Qt-heavy __init__) ---------------
    from blendertk.env_utils.reference_manager import ReferenceManagerSlots

    class _SB:
        def __init__(self, ui, confirm="Yes"):
            self.loaded_ui = type("L", (), {"reference_manager": ui})()
            self.messages = []
            self._confirm = confirm
        def message_box(self, msg, *buttons):
            self.messages.append(msg)
            return self._confirm if buttons else None

    def make_slots(confirm="Yes"):
        ui = type("U", (), {})()  # no tbl000 → _refresh is a harmless no-op
        sb = _SB(ui, confirm)
        s = ReferenceManagerSlots.__new__(ReferenceManagerSlots)
        s.sb = sb
        s.ui = ui
        s._recursive = True
        return s, sb

    # reload_all over a live linked library — success is logged, not popped up (the toast was
    # dropped as a pure success confirmation), so capture the ring buffer to verify the reload ran.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    s, sb = make_slots()
    ReferenceManagerSlots.set_log_level("DEBUG")
    ReferenceManagerSlots.enable_log_buffer()
    ReferenceManagerSlots.clear_log_buffer()
    s.reload_all()
    check("slot reload_all reports a reload", "Reloaded" in ReferenceManagerSlots.dump_log(),
          ReferenceManagerSlots.dump_log() or str(sb.messages))

    # make_local_all (confirm Yes) → library gone, data local
    s, sb = make_slots("Yes")
    s.make_local_all()
    check("slot make_local_all localizes + drops the library", not btk.is_blend_linked(lib_path))

    # remove_all (confirm Yes) on a fresh link → no libraries left
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    s, sb = make_slots("Yes")
    s.remove_all()
    check("slot remove_all removes every library", len(btk.list_libraries()) == 0, str(btk.list_libraries()))

    # remove_all declined (confirm No) leaves the library intact
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    s, sb = make_slots("No")
    s.remove_all()
    check("slot remove_all (declined) keeps the library", len(btk.list_libraries()) == 1)

    # reload_all with nothing linked → friendly message, no crash
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s, sb = make_slots()
    s.reload_all()
    check("slot reload_all with no libraries reports it",
          any("No linked" in m for m in sb.messages), str(sb.messages))

    # The discard guard routes through the engine's corrected test (see check 19) — no prompt when
    # there is nothing to lose, and it still asks (and honors "No") when there is.
    _orig_unsaved = btk.scene_has_unsaved_changes
    try:
        btk.scene_has_unsaved_changes = lambda: False
        s, sb = make_slots("No")
        check("panel: silent when the scene holds nothing to lose",
              s._confirm_discard_unsaved("open") and not sb.messages, str(sb.messages))
        btk.scene_has_unsaved_changes = lambda: True
        s, sb = make_slots("No")
        declined = s._confirm_discard_unsaved("open")
        check("panel: prompts and honors 'No' when there IS unsaved work",
              (not declined) and any("unsaved changes" in m for m in sb.messages), str(sb.messages))
    finally:
        btk.scene_has_unsaved_changes = _orig_unsaved

    # Folder-structure filter must resolve {scenes} (regression: the filter passed no scenes= to
    # replace_placeholders, so a "{scenes}/…" pattern — now the header default — matched nothing
    # and silently hid every file, exactly the "panel shows nothing" symptom).
    s, _fsb = make_slots()
    fs_ws = os.path.join(tmp, "fs_ws")
    os.makedirs(os.path.join(fs_ws, "scenes", "hero"), exist_ok=True)
    in_scenes = os.path.join(fs_ws, "scenes", "hero", "hero.blend")
    loose = os.path.join(fs_ws, "loose.blend")
    open(in_scenes, "w").close()
    open(loose, "w").close()
    kept = s._filter_by_folder_structure([in_scenes, loose], fs_ws, "{scenes}/{name}", "")
    check("folder-structure filter resolves {scenes} (keeps scenes/<name>, drops loose)",
          [os.path.normcase(p) for p in kept] == [os.path.normcase(in_scenes)], str(kept))

    # --- Include Types / foreign classification (mirror of mayatk's, inverted) ----------------
    from blendertk.env_utils.maya_bridge._scene_import import (
        MayaSceneImport, BAKE_SOURCE_EXTENSIONS, BAKE_SOURCE_SUFFIX,
    )

    check("_is_foreign classifies ma/mb/fbx foreign, blend native",
          all(ReferenceManagerSlots._is_foreign(f"x{e}") for e in (".ma", ".mb", ".fbx"))
          and not ReferenceManagerSlots._is_foreign("x.blend"))
    check("NATIVE + FOREIGN partition covers exactly the Include Types row",
          set(ReferenceManagerSlots.NATIVE_EXTENSIONS) | set(ReferenceManagerSlots.FOREIGN_EXTENSIONS)
          == {f".{t}" for t in ReferenceManagerSlots._INCLUDE_TYPES}
          and not set(ReferenceManagerSlots.NATIVE_EXTENSIONS) & set(ReferenceManagerSlots.FOREIGN_EXTENSIONS))
    check("include defaults are this panel's native type",
          set(ReferenceManagerSlots._INCLUDE_DEFAULTS) == set(ReferenceManagerSlots.NATIVE_EXTENSIONS))
    check("foreign set matches the bridge's bakeable sources",
          set(ReferenceManagerSlots.FOREIGN_EXTENSIONS) == set(BAKE_SOURCE_EXTENSIONS))
    s, _ = make_slots()  # stub ui has no header menu -> defaults
    check("_included_extensions falls back to defaults without a menu",
          s._included_extensions() == set(ReferenceManagerSlots._INCLUDE_DEFAULTS))

    # Conversion route: FBX is the default, USD is opt-in. A missing menu must fall
    # back to the SAME route the engine defaults to, not the opposite one.
    check("_foreign_route falls back to fbx without a menu", s._foreign_route() == "fbx")
    for _text, _expected in (("Convert via FBX", "fbx"), ("Convert via USD", "usd"), ("", "fbx")):
        _combo = type("C", (), {"currentText": lambda self, t=_text: t})()
        _menu = type("M", (), {"cmb_conversion_route": _combo})()
        s2, _ = make_slots()
        s2.ui = type("U", (), {"header": type("H", (), {"menu": _menu})()})()
        check(f"_foreign_route({_text!r}) -> {_expected}", s2._foreign_route() == _expected)
    # uitk persists a combo by INDEX, so the item list is append-only and the default
    # moves via setCurrentIndex. Tie the two together: reordering the items without
    # moving the index must fail here, not silently re-default every profile to USD.
    # Anchored on the objectName — this panel builds several combos, and an
    # unanchored match would pin whichever one came first in the file. The kwarg
    # ORDER matters too: set_attributes applies kwargs in order, so setCurrentIndex
    # must follow addItems or it selects into an empty model.
    import ast as _ast, re as _re
    from blendertk.env_utils import reference_manager as _rm_mod
    with open(_rm_mod.__file__, encoding="utf-8") as _fh:
        _rm_src = _fh.read()
    _blk = _re.search(
        # [^\n]* tolerates a trailing comment after setCurrentIndex.
        r'addItems=(\[[^\]]*\]),\s*setCurrentIndex=(\d+),[^\n]*\s*'
        r'setObjectName="cmb_conversion_route"',
        _rm_src,
    )
    check("route combo declares addItems -> setCurrentIndex -> objectName", _blk is not None)
    if _blk:
        _route_items = _ast.literal_eval(_blk.group(1))
        check("route combo default index is the FBX item",
              "FBX" in _route_items[int(_blk.group(2))],
              f"{_route_items} idx={_blk.group(2)}")

    # find_scenes honors the extensions narrowing (drives the Include Types discovery).
    scan_ws = os.path.join(tmp, "scan_ws")
    os.makedirs(scan_ws, exist_ok=True)
    for fname in ("a.ma", "b.mb", "c.fbx", "d.blend"):
        open(os.path.join(scan_ws, fname), "w").close()
    default_scan = {os.path.basename(p) for p in MayaSceneImport.find_scenes(scan_ws)}
    check("find_scenes default lists Maya scenes only", default_scan == {"a.ma", "b.mb"}, str(default_scan))
    fbx_scan = {os.path.basename(p) for p in MayaSceneImport.find_scenes(scan_ws, extensions=[".fbx"])}
    check("find_scenes extensions= narrows to the checked types", fbx_scan == {"c.fbx"}, str(fbx_scan))

    # Bake-source sidecar round trip (maps a linked bake back to the row the user sees).
    fake_bake = os.path.join(tmp, "fake_bake.blend")
    open(fake_bake, "w").close()
    MayaSceneImport()._write_bake_source(fake_bake, os.path.join(scan_ws, "a.ma"))
    check("bake_source reads back the sidecar",
          os.path.normcase(MayaSceneImport.bake_source(fake_bake) or "")
          == os.path.normcase(os.path.join(scan_ws, "a.ma")))
    check("bake_source(non-bake) -> None", MayaSceneImport.bake_source(lib_path) is None)

    # --- End-to-end: .fbx source -> cached .blend bake -> link -> row resolution --------------
    # An .fbx bakes with NO Maya involved (the bake stage launches a fresh headless Blender),
    # so this covers the real reference-toggle path for at least one foreign type.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    fbx_src = os.path.join(tmp, "kit_export.fbx")
    bpy.ops.export_scene.fbx(filepath=fbx_src, use_selection=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    baked = MayaSceneImport().bake_scene(fbx_src, use_cache=False)
    try:
        check("bake_maya_scene(.fbx) produces a .blend", os.path.isfile(baked) and baked.lower().endswith(".blend"), baked)
        check("bake sidecar points back at the .fbx source",
              os.path.normcase(MayaSceneImport.bake_source(baked) or "") == os.path.normcase(fbx_src))
        n_baked = btk.link_blend_file(baked, link=True)
        check("baked .blend links like a native library", n_baked >= 1, f"count={n_baked}")
        s, _ = make_slots()
        lib = s._library_for_path(fbx_src)  # resolve the SOURCE row through the sidecar
        check("_library_for_path resolves the foreign row via its bake", lib is not None)
        if lib is not None:  # second toggle click: remove by the source path, row reads unreferenced
            btk.remove_library(lib)
            check("foreign row un-references through the same path", s._library_for_path(fbx_src) is None)
    finally:
        # The bake lands in the user-level cache store, not tmp — don't leave test artifacts there.
        bpy.ops.wm.read_factory_settings(use_empty=True)  # release the link before deleting
        for _p in (baked, baked + BAKE_SOURCE_SUFFIX):
            try:
                os.remove(_p)
            except OSError:
                pass

    # --- _resolve_smart_bake: prompt bake-vs-raw when driven animation is present ---
    complex_ma = os.path.join(tmp, "anim.ma")
    with open(complex_ma, "w") as f:
        f.write("//Maya ASCII 2025 scene\ncreateNode pointConstraint -n \"pc1\";\n")
    static_ma = os.path.join(tmp, "static.ma")
    with open(static_ma, "w") as f:
        f.write("//Maya ASCII 2025 scene\ncreateNode transform -n \"cube\";\n")
    # Driven animation present -> prompt; the mocked choice maps to smart_bake.
    check("_resolve_smart_bake: Yes -> bake (True)",
          make_slots("Yes")[0]._resolve_smart_bake(complex_ma) is True)
    check("_resolve_smart_bake: No -> import raw (False)",
          make_slots("No")[0]._resolve_smart_bake(complex_ma) is False)
    check("_resolve_smart_bake: Cancel -> None (abort)",
          make_slots("Cancel")[0]._resolve_smart_bake(complex_ma) is None)
    # No driven animation -> no prompt, falls through to the bridge's "auto" default.
    check("_resolve_smart_bake: static scene -> 'auto' (no prompt)",
          make_slots("Yes")[0]._resolve_smart_bake(static_ma) == "auto")

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
