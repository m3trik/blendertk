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

    # reload_all over a live linked library
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    s, sb = make_slots()
    s.reload_all()
    check("slot reload_all reports a reload", any("Reloaded" in m for m in sb.messages), str(sb.messages))

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
