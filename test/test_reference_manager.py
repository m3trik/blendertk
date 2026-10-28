"""blendertk Reference Manager engine headless test — verifies the bpy-side library functions and
the ``ReferenceManager`` engine behind tentacle's shared Reference Manager panel (the panel's own
behavior -- its menus, the selection -> reference diff, the prompts, the progress footer, the
notes -- is tentacle's ``test/test_reference_manager_panel.py``).

Run: blender --background --factory-startup --python blendertk/test/test_reference_manager.py
"""

import json
import sys
import os
import tempfile
import shutil
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


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
    check(
        "find_blend_files finds both .blend",
        set(map(os.path.normcase, files))
        == {os.path.normcase(lib_path), os.path.normcase(other_path)},
        f"{files}",
    )

    # 2. filter — narrows by wildcard name.
    filtered = btk.find_blend_files(tmp, filter_text="kit*")
    check(
        "find_blend_files filter narrows",
        [os.path.basename(p) for p in filtered] == ["kit.blend"],
    )

    # 3. not linked yet.
    check("is_blend_linked False before link", not btk.is_blend_linked(lib_path))

    # 4. link_blend_file — links the collection + instances it; library now present.
    n = btk.link_blend_file(lib_path, link=True)
    check("link_blend_file links a collection", n >= 1, f"count={n}")
    check("is_blend_linked True after link", btk.is_blend_linked(lib_path))
    # Another spelling of the name is the same file only where the OS folds case.
    _recased = os.path.join(
        os.path.dirname(lib_path), os.path.basename(lib_path).upper()
    )
    check(
        "is_blend_linked folds case exactly where the filesystem does",
        btk.is_blend_linked(_recased) == (os.name == "nt"),
    )
    check(
        "collection instanced into the scene",
        any(
            o.instance_type == "COLLECTION"
            and o.instance_collection
            and o.instance_collection.name == "LibColl"
            for o in bpy.data.objects
        ),
    )

    # 5. list_libraries — the record carries the live datablock + existence.
    recs = btk.list_libraries()
    rec = next(
        (r for r in recs if r["abspath"].lower() == os.path.normpath(lib_path).lower()),
        None,
    )
    check("list_libraries reports the linked file", rec is not None and rec["exists"])

    # 6. reload then remove.
    check("reload_library succeeds", btk.reload_library(rec["library"]))
    check("remove_library succeeds", btk.remove_library(rec["library"]))
    check("is_blend_linked False after remove", not btk.is_blend_linked(lib_path))
    # ...and takes the collection-instance Empty link_blend_file made with it: the Empty
    # is LOCAL, so removing the library alone left it behind instancing nothing.
    check(
        "remove_library leaves no collection instance pointing at nothing",
        not any(
            o.instance_type == "COLLECTION" and o.instance_collection is None
            for o in bpy.data.objects
        ),
        str([o.name for o in bpy.data.objects]),
    )

    # 7. append (link=False) brings a LOCAL copy in (datablock.library is None — unlike link,
    #    a lingering 0-user library datablock may persist until orphan purge, so the meaningful
    #    check is locality, not is_blend_linked).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    n2 = btk.link_blend_file(lib_path, link=False)
    check("append brings the collection in", n2 >= 1)
    appended = bpy.data.collections.get("LibColl")
    check(
        "append makes a local copy (library is None)",
        appended is not None and appended.library is None,
    )

    # 8. make_library_local — link, then make the library's data local + drop the library.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    rec = next(
        (
            r
            for r in btk.list_libraries()
            if r["abspath"].lower() == os.path.normpath(lib_path).lower()
        ),
        None,
    )
    check("library present before make_local", rec is not None)
    made = btk.make_library_local(rec["library"])
    check("make_library_local localizes datablocks", made >= 1, f"count={made}")
    check(
        "make_library_local drops the now-unused library",
        not btk.is_blend_linked(lib_path),
    )
    local_coll = bpy.data.collections.get("LibColl")
    check(
        "make_library_local leaves the data local (library is None)",
        local_coll is not None and local_coll.library is None,
    )
    # The library's cube survives with its mesh local too. A single pass in dir() order
    # visits meshes before objects, and a mesh used only by a still-linked object cannot
    # be made local -- dropping the library then took the mesh AND the object now using it.
    kept = [o for o in bpy.data.objects if o.type == "MESH"]
    check(
        "make_library_local keeps the library's mesh object, its mesh local",
        len(kept) == 1 and kept[0].library is None and kept[0].data.library is None,
        str([(o.name_full, o.type) for o in bpy.data.objects]),
    )

    # 8b. the same through the object fallback (a library with no collection).
    flat_path = os.path.join(tmp, "libs", "flat.blend")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    flat = bpy.data.objects.new("flat_geo", bpy.data.meshes.new("flat_mesh"))
    bpy.context.scene.collection.objects.link(flat)
    bpy.ops.wm.save_as_mainfile(filepath=flat_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(flat_path, link=True)
    btk.make_library_local(next(iter(bpy.data.libraries)))
    flat = bpy.data.objects.get("flat_geo")
    check(
        "make_library_local keeps a flat library's object, its mesh local",
        flat is not None
        and flat.library is None
        and flat.data is not None
        and flat.data.library is None
        and not bpy.data.libraries,
        str([(o.name_full, o.type) for o in bpy.data.objects]),
    )

    # 9. make_library_local on a bogus name → 0 (no crash).
    check(
        "make_library_local(unknown) → 0", btk.make_library_local("does_not_exist") == 0
    )

    # 10. find_workspaces — the project folders under a root (subdirs holding .blend).
    ws = btk.find_workspaces(tmp)
    check(
        "find_workspaces finds the libs project folder",
        [os.path.normcase(w) for w in ws]
        == [os.path.normcase(os.path.join(tmp, "libs"))],
        f"{ws}",
    )
    ws_root = btk.find_workspaces(os.path.join(tmp, "libs"))
    check(
        "find_workspaces includes a root that directly holds .blend",
        os.path.normcase(os.path.join(tmp, "libs"))
        in [os.path.normcase(w) for w in ws_root],
    )
    check(
        "find_workspaces(bad dir) → []",
        btk.find_workspaces(os.path.join(tmp, "nope")) == [],
    )

    # 11. format_scene_name — case + suffix, suffix not duplicated; combo values must be set_case-valid.
    check(
        "format_scene_name applies case + suffix",
        btk.format_scene_name("cube", "upper", "_GEO") == "CUBE_GEO",
    )
    check(
        "format_scene_name doesn't double the suffix",
        btk.format_scene_name("CUBE_GEO", None, "_GEO") == "CUBE_GEO",
    )
    check(
        "format_scene_name camel lowercases first letter",
        btk.format_scene_name("MyScene", "camel") == "myScene",
    )
    check(
        "format_scene_name pascal capitalizes first letter",
        btk.format_scene_name("myScene", "pascal") == "MyScene",
    )
    # 12. save_scene_as — saves the current scene under a workspace with naming applied.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ws_dir = os.path.join(tmp, "proj")
    os.makedirs(ws_dir, exist_ok=True)
    saved = btk.save_scene_as(ws_dir, "shot", case="lower", suffix="_v01")
    check(
        "save_scene_as writes a named .blend",
        saved is not None
        and os.path.isfile(saved)
        and os.path.basename(saved) == "shot_v01.blend",
        str(saved),
    )

    # 13. save_scene_as with a subfolder pattern.
    sub = btk.save_scene_as(ws_dir, "hero", subfolder="scenes/{name}")
    check(
        "save_scene_as honors the subfolder pattern",
        sub is not None
        and os.path.normcase(os.path.dirname(sub)).endswith(
            os.path.normcase(os.path.join("scenes", "hero"))
        ),
        str(sub),
    )

    # 14. rename_scene_file — renames on disk; refuses a real name clash without side effects.
    renamed = btk.rename_scene_file(saved, "shot_final")
    check(
        "rename_scene_file renames the .blend",
        renamed is not None and os.path.isfile(renamed) and not os.path.exists(saved),
    )
    clash = os.path.join(os.path.dirname(renamed), "taken.blend")
    open(clash, "w").close()
    check(
        "rename_scene_file refuses an existing target (no side effect)",
        btk.rename_scene_file(renamed, "taken") is None and os.path.isfile(renamed),
    )
    # 14a. a case-only rename goes through -- on a case-insensitive file system its target
    #      "exists" (it IS the file), which used to read as a name clash.
    recased = btk.rename_scene_file(renamed, "Shot_Final")
    check(
        "rename_scene_file does a case-only rename",
        recased is not None
        and "Shot_Final.blend" in os.listdir(os.path.dirname(renamed)),
        str(recased),
    )
    renamed = recased or renamed

    # 14b. renaming the OPEN file is save-then-reopen: the edits authored since the last save go
    #      into the renamed file, and the session tracks the new path. Without it bpy.data.filepath
    #      keeps pointing at a name that no longer exists and the next save resurrects the old file.
    open_path = btk.save_scene_as(ws_dir, "live")  # this is now the open file
    bpy.ops.mesh.primitive_cube_add()  # an edit made after that save
    bpy.context.active_object.name = "RenameProbe"
    moved = btk.rename_scene_file(open_path, "live_renamed")
    check(
        "rename_scene_file renames the open .blend",
        moved is not None and os.path.isfile(moved) and not os.path.exists(open_path),
        str(moved),
    )
    check(
        "the session tracks the renamed file, not the old name",
        moved is not None
        and os.path.normcase(os.path.normpath(bpy.data.filepath))
        == os.path.normcase(moved),
        bpy.data.filepath,
    )
    check(
        "the unsaved edit was saved into the renamed file",
        "RenameProbe" in bpy.data.objects,
    )
    check(
        "the .blend1 backup followed the rename (no leftover under the old name)",
        moved is not None
        and os.path.isfile(moved + "1")
        and not os.path.exists(open_path + "1"),
    )
    bpy.ops.wm.save_mainfile()
    check(
        "saving after the rename doesn't resurrect the old name",
        not os.path.exists(open_path),
    )
    btk.delete_scene_file(moved, permanent=True)

    # 15. delete_scene_file — to the trash (2026-09-27: Delete was permanent, no Recycle Bin),
    # its .blend1 backup along; permanently only when asked (a drive with no trash, confirmed
    # as such); and a trash that will not take it leaves it where it was. The trash is a
    # scratch folder here -- never this machine's.
    from unittest import mock as _rm_mock

    import pythontk as ptk

    _rm_bin = os.path.join(tmp, "rm_bin")
    os.makedirs(_rm_bin, exist_ok=True)

    def _to_bin(path):
        target = os.path.join(_rm_bin, os.path.basename(path))
        os.replace(path, target)
        return target

    open(renamed + "1", "w").close()
    with _rm_mock.patch.object(ptk.FileUtils, "move_to_trash", side_effect=_to_bin):
        check(
            "delete_scene_file moves the .blend and its backup to the trash",
            btk.delete_scene_file(renamed)
            and not os.path.exists(renamed)
            and sorted(os.listdir(_rm_bin))
            == sorted([os.path.basename(renamed), os.path.basename(renamed) + "1"]),
            str(os.listdir(_rm_bin)),
        )
    check("delete_scene_file(missing) → False", not btk.delete_scene_file(renamed))
    _kept = os.path.join(tmp, "no_trash.blend")
    open(_kept, "w").close()
    with _rm_mock.patch.object(ptk.FileUtils, "move_to_trash", return_value=None):
        check(
            "a trash that will not take it leaves the file (False)",
            not btk.delete_scene_file(_kept) and os.path.isfile(_kept),
        )
    check(
        "permanent=True deletes outright",
        btk.delete_scene_file(_kept, permanent=True) and not os.path.exists(_kept),
    )

    # 16. set/get_reference_display_mode — tri-state on a linked library's instance objects.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    rec = next(
        (
            r
            for r in btk.list_libraries()
            if r["abspath"].lower() == os.path.normpath(lib_path).lower()
        ),
        None,
    )
    check(
        "display mode defaults to off",
        btk.get_reference_display_mode(rec["library"]) == "off",
    )
    btk.set_reference_display_mode(rec["library"], "template")
    check(
        "template → WIRE + locked + reported",
        btk.get_reference_display_mode(rec["library"]) == "template",
    )
    inst = next((o for o in bpy.data.objects if o.instance_type == "COLLECTION"), None)
    check(
        "template sets the instance to WIRE + hide_select",
        inst is not None and inst.display_type == "WIRE" and inst.hide_select,
    )
    btk.set_reference_display_mode(rec["library"], "reference")
    check(
        "reference → locked, normal shading",
        btk.get_reference_display_mode(rec["library"]) == "reference"
        and inst.display_type == "TEXTURED"
        and inst.hide_select,
    )
    btk.set_reference_display_mode(rec["library"], "off")
    check(
        "off → normal, unlocked",
        btk.get_reference_display_mode(rec["library"]) == "off"
        and not inst.hide_select,
    )
    raised = False
    try:
        btk.set_reference_display_mode(rec["library"], "bogus")
    except ValueError:
        raised = True
    check("invalid display mode raises", raised)

    # 17. open_scene — opens a .blend (replaces the file). Run LAST (resets bpy state).
    check(
        "open_scene opens the file",
        btk.open_scene(lib_path)
        and os.path.normcase(os.path.normpath(bpy.data.filepath))
        == os.path.normcase(os.path.normpath(lib_path)),
    )
    check(
        "open_scene(missing) → False",
        not btk.open_scene(os.path.join(tmp, "nope.blend")),
    )

    # 18. new_scene — the 'close' counterpart: discard the open file, empty untitled scene.
    check(
        "new_scene resets to an empty, unsaved scene",
        btk.new_scene() and bpy.data.filepath == "" and len(bpy.data.objects) == 0,
    )

    # 19. Unsaved-work guard. bpy.data.is_dirty follows the UNDO stack, so a single click in the
    # viewport flips it on a brand-new, never-saved, empty scene (verified live in 5.1 — one
    # ed.undo_push in a VIEW_3D context is enough), which made the panel's discard prompt fire
    # with nothing to lose. Not reproducible headless (undo pushes need a real UI context), so the
    # decision is pinned through the pure predicate plus the real content probe.
    from blendertk.env_utils._env_utils import EnvUtils as _EU

    check(
        "dirty + never saved + no content → nothing to lose",
        _EU._is_unsaved_work(True, False, False) is False,
    )
    check(
        "dirty + never saved + content → unsaved work",
        _EU._is_unsaved_work(True, False, True) is True,
    )
    check(
        "dirty + saved file → unsaved work (flag is meaningful once on disk)",
        _EU._is_unsaved_work(True, True, False) is True,
    )
    check(
        "not dirty → never unsaved work",
        _EU._is_unsaved_work(False, True, True) is False,
    )

    bpy.ops.wm.read_factory_settings(use_empty=True)
    check("empty scene has no content", not btk.scene_has_content())
    check(
        "pristine empty scene reports no unsaved changes",
        not btk.scene_has_unsaved_changes(),
    )
    bpy.ops.object.camera_add()
    bpy.ops.object.light_add()
    check(
        "camera + light only is still the default document", not btk.scene_has_content()
    )
    bpy.ops.mesh.primitive_cube_add()
    check("a mesh counts as content", btk.scene_has_content())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.data.texts.new("notes")
    check("a text datablock counts as content", btk.scene_has_content())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    check("a linked library counts as content", btk.scene_has_content())

    # --- The engine: ReferenceManager (tentacle's shared panel composes it) ------------------
    # The panel's own behavior is tentacle's (test/test_reference_manager_panel.py); these
    # checks pin the host half it calls. Reached by module path: the root export is the
    # package's DEFAULT_INCLUDE entry.
    from blendertk.env_utils import reference_manager as _rm_mod
    from blendertk.env_utils.reference_manager import ReferenceManager
    from blendertk.env_utils.maya_bridge import _scene_import as _si_mod
    from blendertk.env_utils.maya_bridge._scene_import import (
        MayaSceneImport,
        BAKE_SOURCE_EXTENSIONS,
        BAKE_SOURCE_SUFFIX,
    )
    import pythontk as _ptk_types

    def _norm(p):
        return os.path.normcase(os.path.normpath(p))

    rm = ReferenceManager()

    # References: every linked library, as a reference keyed by its file.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    other_lib = os.path.join(tmp, "libs", "other_ws.blend")
    shutil.copy2(lib_path, other_lib)
    check("engine: nothing linked -> no references", rm.current_references == [])
    check(
        "engine: add_reference links a .blend",
        rm.add_reference("kit", lib_path) is True and btk.is_blend_linked(lib_path),
    )
    btk.link_blend_file(other_lib, link=True)
    _refs = rm.current_references
    _by_row = {rm.row_key(r.path): r for r in _refs}
    check(
        "engine: current_references wraps each library (path / namespace / label)",
        set(_by_row) == {_norm(lib_path), _norm(other_lib)}
        and all(r.namespace == r.library.name == r.label for r in _refs),
        str([(r.namespace, r.path) for r in _refs]),
    )
    _n_libs = len(bpy.data.libraries)
    check(
        "engine: add_reference of a linked file links nothing more",
        rm.add_reference("kit", lib_path) is True
        and len(bpy.data.libraries) == _n_libs,
    )
    check(
        "engine: add_reference of a missing file -> False",
        rm.add_reference("gone", os.path.join(tmp, "gone.blend")) is False,
    )
    _kit = _by_row.get(_norm(lib_path))
    check(
        "engine: a display override round-trips through a reference",
        _kit is not None
        and rm.set_reference_display_mode(_kit, "template")
        and rm.get_reference_display_mode(_kit) == "template",
    )
    if _kit is not None:
        rm.set_reference_display_mode(_kit, "off")
    with _rm_mock.patch.object(
        _EU, "reload_library", wraps=_EU.reload_library
    ) as _reloads:
        rm.update_references()
    check(
        "engine: update_references reloads every library",
        _reloads.call_count == len(bpy.data.libraries) == 2,
        f"calls={_reloads.call_count}",
    )
    _failed = rm.remove_references([_kit.namespace]) if _kit is not None else None
    check(
        "engine: remove_references removes only the named library",
        _failed == []
        and not btk.is_blend_linked(lib_path)
        and btk.is_blend_linked(other_lib),
        str(btk.list_libraries()),
    )
    # A removal that left its collection-instance Empty behind piled one up per
    # click (the panel's row selection is the reference set); the other library's stays.
    _instancers = [o for o in bpy.data.objects if o.instance_type == "COLLECTION"]
    check(
        "engine: ...and only its collection instance with it",
        len(_instancers) == 1 and _instancers[0].instance_collection is not None,
        str([(o.name, o.instance_collection) for o in _instancers]),
    )
    _made = rm.import_references()
    check(
        "engine: import_references makes every library local and drops it",
        _made >= 1 and len(bpy.data.libraries) == 0,
        f"made={_made} libs={btk.list_libraries()}",
    )
    _raised = False
    try:
        rm.import_references(namespace_mode="keep")
    except ValueError:
        _raised = True
    check(
        "engine: Blender has one namespace mode; another raises",
        _raised and ReferenceManager.NAMESPACE_MODES == ("remove",),
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    btk.link_blend_file(lib_path, link=True)
    btk.link_blend_file(other_lib, link=True)
    check(
        "engine: remove_references() removes every library",
        rm.remove_references() == [] and len(btk.list_libraries()) == 0,
        str(btk.list_libraries()),
    )

    # The open file: unsaved-work test, in-place save, save as, close.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    check(
        "engine: a pristine empty scene has nothing to lose",
        rm.scene_modified() is False,
    )
    check(
        "engine: a never-saved file cannot be saved in place (None)",
        rm.save_open_scene() is None,
    )
    _on_disk = os.path.join(tmp, "on_disk.blend")
    check(
        "engine: save_scene writes the open file there and follows it",
        rm.save_scene(_on_disk) == os.path.normpath(_on_disk)
        and os.path.isfile(_on_disk)
        and _norm(rm.current_scene()) == _norm(_on_disk),
        rm.current_scene(),
    )
    with (
        _rm_mock.patch.object(_EU, "scene_has_unsaved_changes", return_value=False),
        _rm_mock.patch.object(_EU, "_save_open_file") as _flush,
    ):
        check(
            "engine: a clean on-disk file is up to date, nothing saved",
            rm.save_open_scene() is True and not _flush.called,
        )
    with (
        _rm_mock.patch.object(_EU, "scene_has_unsaved_changes", return_value=True),
        _rm_mock.patch.object(_EU, "_save_open_file", return_value=True) as _flush,
    ):
        check(
            "engine: a modified on-disk file is flushed in place",
            rm.scene_modified() is True
            and rm.save_open_scene() is True
            and _flush.called,
        )
    with (
        _rm_mock.patch.object(_EU, "scene_has_unsaved_changes", return_value=True),
        _rm_mock.patch.object(_EU, "_save_open_file", return_value=False),
    ):
        _raised = False
        try:
            rm.save_open_scene()
        except RuntimeError:
            _raised = True
        check("engine: a failed in-place save raises (the work is unsaved)", _raised)

    # Rename: same folder, the open file reopened on its new path; a clash refuses.
    _ren_dir = os.path.join(tmp, "ren")
    os.makedirs(_ren_dir, exist_ok=True)
    _ren_old = os.path.join(_ren_dir, "shot_a.blend")
    _ren_new = os.path.join(_ren_dir, "shot_b.blend")
    bpy.ops.wm.save_as_mainfile(filepath=_ren_old)
    _got = rm.rename_scene_file(_ren_old, _ren_new, folder="shot_b")
    check(
        "engine: rename_scene_file renames the open file and reopens it",
        bool(_got)
        and _norm(_got) == _norm(_ren_new)
        and os.path.isfile(_ren_new)
        and not os.path.exists(_ren_old)
        and _norm(bpy.data.filepath) == _norm(_ren_new),
        f"{_got} open={bpy.data.filepath}",
    )
    _taken = os.path.join(_ren_dir, "taken.blend")
    open(_taken, "w").close()
    check(
        "engine: a name clash refuses the rename (None), nothing moved",
        rm.rename_scene_file(_ren_new, _taken) is None and os.path.isfile(_ren_new),
    )
    check(
        "engine: new_scene starts an empty, unsaved file",
        rm.new_scene() is True and rm.current_scene() == "",
        rm.current_scene(),
    )

    # Delete: to the trash; for good when asked; untouched (None) when the trash refuses.
    # The trash is a scratch folder here -- never this machine's.
    _del_ws = os.path.join(tmp, "del_ws")
    os.makedirs(_del_ws, exist_ok=True)
    _victim = os.path.join(_del_ws, "victim_lod0.blend")
    open(_victim, "w").close()
    _bin2 = os.path.join(tmp, "rm_bin2")
    os.makedirs(_bin2, exist_ok=True)

    def _to_bin2(path):
        target = os.path.join(_bin2, os.path.basename(path))
        os.replace(path, target)
        return target

    with (
        _rm_mock.patch.object(ptk.FileUtils, "can_trash", return_value=True),
        _rm_mock.patch.object(ptk.FileUtils, "move_to_trash", side_effect=_to_bin2),
    ):
        _gone = rm.remove_scene_file(_victim)
    check(
        "engine: remove_scene_file sends the file to the trash",
        _gone is True
        and not os.path.exists(_victim)
        and os.path.isfile(os.path.join(_bin2, "victim_lod0.blend")),
        str(_gone),
    )
    open(_victim, "w").close()
    with _rm_mock.patch.object(ptk.FileUtils, "move_to_trash") as _never:
        _gone = rm.remove_scene_file(_victim, permanent=True)
    check(
        "engine: permanent deletes outright, the trash never asked",
        _gone is True and not os.path.exists(_victim) and not _never.called,
    )
    open(_victim, "w").close()
    with _rm_mock.patch.object(ptk.FileUtils, "move_to_trash", return_value=None):
        _gone = rm.remove_scene_file(_victim)
    check(
        "engine: a trash that refuses leaves the file untouched (None)",
        _gone is None and os.path.isfile(_victim),
        str(_gone),
    )
    check(
        "engine: a missing file -> False",
        rm.remove_scene_file(os.path.join(_del_ws, "gone.blend")) is False,
    )

    # Workspaces: marked ones and folders holding .blend, an empty marked one only
    # when asked for; each lists its scenes, a marked project's scenes/ included.
    _ws_root = os.path.join(tmp, "ws_root")
    _ws_full = os.path.join(_ws_root, "full")
    _ws_empty = os.path.join(_ws_root, "empty_marked")
    _ws_marked = os.path.join(_ws_root, "marked")
    os.makedirs(_ws_full, exist_ok=True)
    os.makedirs(_ws_empty, exist_ok=True)
    os.makedirs(os.path.join(_ws_marked, "scenes"), exist_ok=True)
    open(os.path.join(_ws_full, "full.blend"), "w").close()
    open(os.path.join(_ws_marked, "scenes", "m.blend"), "w").close()
    ptk.Workspace.create(_ws_empty, create_dirs=False)
    ptk.Workspace.create(_ws_marked, create_dirs=False)
    _ws_rm = ReferenceManager()
    _ws_rm.current_working_dir = _ws_root
    _found = {_norm(p) for _n, p in _ws_rm.find_available_workspaces(_ws_root)}
    check(
        "engine: workspaces = folders holding .blend + marked projects with scenes",
        _found == {_norm(_ws_full), _norm(_ws_marked)},
        str(_found),
    )
    check(
        "engine: each workspace lists its scenes (a marked project's scenes/ too)",
        [
            _norm(p)
            for k, v in _ws_rm.workspace_files.items()
            if _norm(k) == _norm(_ws_marked)
            for p in v
        ]
        == [_norm(os.path.join(_ws_marked, "scenes", "m.blend"))],
        str(_ws_rm.workspace_files),
    )
    _ws_rm.ignore_empty_workspaces = False
    _found = {_norm(p) for _n, p in _ws_rm.find_available_workspaces(_ws_root)}
    check(
        "engine: Ignore Empty Workspaces off lists the empty marked project too",
        _found == {_norm(_ws_full), _norm(_ws_marked), _norm(_ws_empty)},
        str(_found),
    )

    # Foreign rows: the classification the panel's Include Types and actions read.
    check(
        "engine: ma/mb/fbx + every USD spelling are foreign, blend native",
        all(
            ReferenceManager.is_foreign(f"x{e}")
            for e in (".ma", ".mb", ".fbx", *_ptk_types.USD_EXTENSIONS)
        )
        and not ReferenceManager.is_foreign("x.blend")
        and not ReferenceManager.is_foreign(""),
    )
    check(
        "engine: NATIVE + FOREIGN partition the Include Types' extensions",
        set(ReferenceManager.NATIVE_EXTENSIONS)
        | set(ReferenceManager.FOREIGN_EXTENSIONS)
        == {".ma", ".mb", ".fbx", ".blend", *_ptk_types.USD_EXTENSIONS}
        and not set(ReferenceManager.NATIVE_EXTENSIONS)
        & set(ReferenceManager.FOREIGN_EXTENSIONS),
    )
    check(
        "engine: the include defaults are the native type",
        set(ReferenceManager.INCLUDE_DEFAULTS)
        == set(ReferenceManager.NATIVE_EXTENSIONS)
        == {".blend"},
    )
    check(
        "engine: the foreign set matches the bridge's bakeable sources",
        set(ReferenceManager.FOREIGN_EXTENSIONS) == set(BAKE_SOURCE_EXTENSIONS),
    )
    check(
        "engine: every foreign row imports without a link",
        set(ReferenceManager.IMPORT_EXTENSIONS)
        == set(ReferenceManager.FOREIGN_EXTENSIONS),
    )
    check(
        "engine: only a Maya scene converts (an .fbx / USD row is the bake's own input)",
        ReferenceManager.needs_conversion("a.ma")
        and ReferenceManager.needs_conversion("a.MB")
        and not any(
            ReferenceManager.needs_conversion(f"a{e}")
            for e in (".fbx", ".blend", *_ptk_types.USD_EXTENSIONS)
        ),
    )
    check(
        "engine: three ways a rig can travel; Save writes .blend",
        ReferenceManager.RIG_MODES == ("rig", "bake", "raw")
        and ReferenceManager.SCENE_SAVE_EXTENSION == ".blend"
        and ReferenceManager.PRESET_DIR == "blendertk/reference_manager",
    )

    # find_scenes honors the extensions narrowing (drives the Include Types discovery).
    scan_ws = os.path.join(tmp, "scan_ws")
    os.makedirs(scan_ws, exist_ok=True)
    for fname in ("a.ma", "b.mb", "c.fbx", "d.blend"):
        open(os.path.join(scan_ws, fname), "w").close()
    default_scan = {os.path.basename(p) for p in MayaSceneImport.find_scenes(scan_ws)}
    check(
        "find_scenes default lists Maya scenes only",
        default_scan == {"a.ma", "b.mb"},
        str(default_scan),
    )
    fbx_scan = {
        os.path.basename(p)
        for p in MayaSceneImport.find_scenes(scan_ws, extensions=[".fbx"])
    }
    check(
        "find_scenes extensions= narrows to the checked types",
        fbx_scan == {"c.fbx"},
        str(fbx_scan),
    )
    check(
        "engine: find_foreign_scenes is the bridge's scan",
        {
            os.path.basename(p)
            for p in ReferenceManager.find_foreign_scenes(scan_ws, extensions=[".fbx"])
        }
        == fbx_scan,
    )
    for fname in ("e.usd", "f.usda", "g.usdc", "h.usdz"):
        open(os.path.join(scan_ws, fname), "w").close()
    usd_scan = {
        os.path.basename(p)
        for p in MayaSceneImport.find_scenes(
            scan_ws, extensions=list(_ptk_types.USD_EXTENSIONS)
        )
    }
    check(
        "find_scenes lists every USD spelling when the usd toggle is on",
        usd_scan == {"e.usd", "f.usda", "g.usdc", "h.usdz"},
        str(usd_scan),
    )

    # A bake made before 2026-10-13 maps back through its .source.json sidecar
    # (read for one release; new bakes record the source on their own scene).
    fake_bake = os.path.join(tmp, "fake_bake.blend")
    open(fake_bake, "w").close()
    with open(fake_bake + BAKE_SOURCE_SUFFIX, "w") as _fh:
        json.dump({"source": os.path.join(scan_ws, "a.ma")}, _fh)
    check(
        "bake_source reads back a legacy sidecar",
        os.path.normcase(MayaSceneImport.bake_source(fake_bake) or "")
        == os.path.normcase(os.path.join(scan_ws, "a.ma")),
    )
    check(
        "bake_source(non-bake) -> None", MayaSceneImport.bake_source(lib_path) is None
    )
    check(
        "engine: a bake's row is the scene it was baked from (row_key)",
        ReferenceManager.row_key(fake_bake) == _norm(os.path.join(scan_ws, "a.ma"))
        and ReferenceManager.row_key(lib_path) == _norm(lib_path)
        and ReferenceManager.bake_source("") is None,
    )

    # --- End-to-end: .fbx source -> cached .blend bake -> link -> row resolution --------------
    # An .fbx bakes with NO Maya involved (the bake stage launches a fresh headless Blender),
    # so this covers the real reference-toggle path for at least one foreign type.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    fbx_src = os.path.join(tmp, "kit_export.fbx")
    bpy.ops.export_scene.fbx(filepath=fbx_src, use_selection=False)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # The bake is a project file of the HOST's project (2026-10-13): saved into
    # this test's project, so the bake lands in its cache/bridge.
    bake_proj = os.path.join(tmp, "bake_proj")
    ptk.Workspace.create(bake_proj)
    host = os.path.join(bake_proj, "scenes", "host.blend")
    bpy.ops.wm.save_as_mainfile(filepath=host)
    baked = MayaSceneImport().bake_scene(fbx_src, use_cache=False)
    try:
        check(
            "bake_maya_scene(.fbx) produces a .blend",
            os.path.isfile(baked) and baked.lower().endswith(".blend"),
            baked,
        )
        check(
            "the bake lands in the host's <project>/cache/bridge, never temp",
            ptk.FileUtils.is_under(baked, os.path.join(bake_proj, "cache", "bridge"))
            and not ptk.FileUtils.is_under(
                baked, os.path.join(ptk.TempArtifacts.root(), "maya_bake_btk_cache")
            )
            and os.path.basename(baked).startswith("btk_bake_cache_kit_export_"),
            baked,
        )
        check(
            "the bake's own scene points back at the .fbx source (no sidecar)",
            os.path.normcase(MayaSceneImport.bake_source(baked) or "")
            == os.path.normcase(fbx_src)
            and not os.path.exists(baked + BAKE_SOURCE_SUFFIX),
            str(MayaSceneImport.bake_info(baked)),
        )
        check(
            "engine: bake_project_root is the open file's project",
            _norm(rm.bake_project_root() or "") == _norm(bake_proj),
            str(rm.bake_project_root()),
        )
        _stamp = os.stat(baked).st_mtime_ns
        check(
            "engine: an unchanged source reuses its project bake with no child",
            rm.bake_scene(fbx_src) == baked and os.stat(baked).st_mtime_ns == _stamp,
        )
        check(
            "engine: add_reference links the bake like a native library",
            rm.add_reference("kit_export", baked) is True,
        )
        _blib = next(
            (
                lib
                for lib in bpy.data.libraries
                if os.path.normcase(os.path.normpath(bpy.path.abspath(lib.filepath)))
                == os.path.normcase(baked)
            ),
            None,
        )
        check(
            "the bake is linked //-relative (the scene keeps it when the project moves)",
            _blib is not None and _blib.filepath.startswith("//"),
            getattr(_blib, "filepath", None),
        )
        bpy.ops.wm.save_mainfile()
        check(
            "purge keeps a bake a project .blend links",
            MayaSceneImport().purge_unused_bakes(bake_proj) == []
            and os.path.isfile(baked),
        )
        # A file saved before 2026-10-13 links a bake in TEMP: the engine's follow
        # relinks it onto the project's bake of its source.
        _old_store = ptk.TempArtifacts("btk_rm_rehome_test", policy="scoped")
        _old = _old_store.path(extension=".blend")
        shutil.copyfile(baked, _old)
        with open(_old + BAKE_SOURCE_SUFFIX, "w") as _fh:
            json.dump({"source": fbx_src}, _fh)
        btk.remove_library(_blib)
        btk.link_blend_file(_old, link=True)
        _moved = rm.follow_bake_sources()
        _rlib = next(iter(bpy.data.libraries), None)
        check(
            "engine: a library reading a temp bake is relinked onto the project's bake",
            len(_moved) == 1
            and _rlib is not None
            and os.path.normcase(os.path.normpath(bpy.path.abspath(_rlib.filepath)))
            == os.path.normcase(baked)
            and _rlib.filepath.startswith("//"),
            getattr(_rlib, "filepath", None),
        )
        _old_store.cleanup()
        _bref = [
            r for r in rm.current_references if rm.row_key(r.path) == _norm(fbx_src)
        ]
        check(
            "engine: the foreign row resolves to its library through the bake",
            len(_bref) == 1,
            str([(r.namespace, r.path) for r in rm.current_references]),
        )
        if _bref:  # second toggle click: removed by the source row's key
            rm.remove_references([_bref[0].namespace])
            check(
                "engine: the foreign row un-references through the same key",
                not any(
                    rm.row_key(r.path) == _norm(fbx_src) for r in rm.current_references
                ),
            )
        bpy.ops.wm.save_mainfile()
        _purged = MayaSceneImport().purge_unused_bakes(bake_proj)
        check(
            "purge removes the bake once no project file links it",
            [os.path.normcase(p) for p in _purged] == [os.path.normcase(baked)]
            and not os.path.exists(baked),
            str(_purged),
        )
    finally:
        bpy.ops.wm.read_factory_settings(
            use_empty=True
        )  # release the link before deleting
        shutil.rmtree(bake_proj, ignore_errors=True)

    # --- End-to-end: USD source -> cached .blend bake -> link -> row resolution -------------
    # A USD layer is the bake's own input exactly like an .fbx: the bake stage (a fresh
    # headless Blender) imports it natively, so a USD row references with NO Maya at all.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "usd_kit_cube"
    usd_src = os.path.join(tmp, "kit_export.usda")
    bpy.ops.wm.usd_export(filepath=usd_src)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    usd_proj = os.path.join(tmp, "usd_bake_proj")
    os.makedirs(usd_proj, exist_ok=True)
    usd_baked = MayaSceneImport().bake_scene(
        usd_src, use_cache=False, project=usd_proj
    )
    try:
        check(
            "bake_scene(.usda) produces a .blend with no Maya involved",
            os.path.isfile(usd_baked) and usd_baked.lower().endswith(".blend"),
            usd_baked,
        )
        check(
            "the bake's own scene points back at the USD source",
            os.path.normcase(MayaSceneImport.bake_source(usd_baked) or "")
            == os.path.normcase(usd_src),
        )
        check(
            "engine: the baked USD links like a native library",
            rm.add_reference("kit_export", usd_baked) is True,
        )
        _uref = [
            r for r in rm.current_references if rm.row_key(r.path) == _norm(usd_src)
        ]
        check("engine: the USD row resolves through its bake", len(_uref) == 1)
        if _uref:
            rm.remove_references([_uref[0].namespace])
            check(
                "engine: the USD row un-references through the same key",
                not any(
                    rm.row_key(r.path) == _norm(usd_src) for r in rm.current_references
                ),
            )
    finally:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(usd_proj, ignore_errors=True)

    # Rig logic the conversion prompt asks about: a .ma is line-scanned.
    complex_ma = os.path.join(tmp, "anim.ma")
    with open(complex_ma, "w") as f:
        f.write('//Maya ASCII 2025 scene\ncreateNode pointConstraint -n "pc1";\n')
    static_ma = os.path.join(tmp, "static.ma")
    with open(static_ma, "w") as f:
        f.write('//Maya ASCII 2025 scene\ncreateNode transform -n "cube";\n')
    check(
        "engine: a Maya scene with a constraint has rig logic to ask about",
        bool(ReferenceManager.scene_has_complex_animation(complex_ma)),
    )
    check(
        "engine: a static scene has none (nothing to ask)",
        not ReferenceManager.scene_has_complex_animation(static_ma),
    )

    # --- Unlink and Import on a not-linked USD row imports it directly: no Maya, no bake ---
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rm.import_scene(usd_src)
    check(
        "engine: a USD row imports directly as local data",
        any(o.type == "MESH" and o.library is None for o in bpy.data.objects),
        f"objects={[o.name for o in bpy.data.objects]}",
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # A USD row involves no Maya, so a missing file must not be reported as one.
    _missing = None
    try:
        rm.import_scene(os.path.join(tmp, "gone.usda"))
    except Exception as e:  # noqa: BLE001 - what the panel shows is its text
        _missing = e
    check(
        "engine: a missing USD row's import raises and blames no Maya",
        _missing is not None and "Maya" not in str(_missing),
        repr(_missing),
    )

    # --- an .fbx row imported in place goes through the consumer its bake runs ------------
    # Measured before (a Maya-exported joint chain): Blender's stock FBX import combed the
    # bones the bake orients along the chain, and kept 300 keys to the bake's 27 -- the
    # same row landed differently imported and linked.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _fbx_direct = MayaSceneImport(
        maya_path="X:/definitely/not/maya.exe", log_level="WARNING"
    ).import_scene(fbx_src)
    check(
        "import_scene(.fbx) imports in place, no Maya involved",
        any(getattr(o, "type", "") == "MESH" for o in _fbx_direct),
        f"{[getattr(o, 'name', o) for o in _fbx_direct]}",
    )
    _import_calls = []
    _real_import_scene = _si_mod.MayaSceneImport.import_scene

    def _recording_import(self, path, *args, **kwargs):  # wraps, never replaces
        _import_calls.append(os.path.basename(path))
        return _real_import_scene(self, path, *args, **kwargs)

    _si_mod.MayaSceneImport.import_scene = _recording_import
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        rm.import_scene(fbx_src)
    finally:
        _si_mod.MayaSceneImport.import_scene = _real_import_scene
    check(
        "engine: an .fbx row's import takes the bake's consumer",
        _import_calls == [os.path.basename(fbx_src)]
        and any(o.type == "MESH" for o in bpy.data.objects),
        f"calls={_import_calls}",
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # --- foreign scratch: <temp root>/btk_opened/btk_opened_<hash>/<stem>_<ext>.blend -------
    # A .ma opens as `<stem>_ma.blend` (provenance in the name, never shadowing a sibling
    # `<stem>.blend`); same-named scenes in different projects must not share a scratch.
    _scr_a = ReferenceManager.foreign_scratch_path(os.path.join(tmp, "projA", "shot.ma"))
    _scr_b = ReferenceManager.foreign_scratch_path(os.path.join(tmp, "projB", "shot.ma"))
    _scr_fbx = ReferenceManager.foreign_scratch_path(
        os.path.join(tmp, "projA", "shot.FBX")
    )
    check(
        "scratch name carries the source type: shot.ma -> shot_ma.blend",
        os.path.basename(_scr_a) == "shot_ma.blend"
        and os.path.basename(_scr_fbx) == "shot_fbx.blend",
        f"{_scr_a} / {_scr_fbx}",
    )
    check(
        "scratch dir is a TempArtifacts-swept `btk_opened_*` dir in its store's "
        "folder under the temp root",
        os.path.basename(os.path.dirname(_scr_a)).startswith("btk_opened_")
        and os.path.normcase(os.path.dirname(os.path.dirname(_scr_a)))
        == os.path.normcase(os.path.join(ptk.TempArtifacts.root(), "btk_opened")),
        _scr_a,
    )
    check(
        "same-named scenes in different projects get separate scratches",
        os.path.dirname(_scr_a) != os.path.dirname(_scr_b),
    )
    check(
        "scratch path is deterministic (a second Open click must resolve the same file)",
        ReferenceManager.foreign_scratch_path(os.path.join(tmp, "projA", "shot.ma"))
        == _scr_a,
    )

    # --- scratch discard on close / next open: the engine hands the current file to the
    # twins store (ptk.ScratchTwins owns untouched-vs-saved; pinned in pythontk's own tests).
    _twins = _rm_mod._scratch_twins()
    _bake = os.path.join(tmp, "bake.blend")
    with open(_bake, "wb") as fh:
        fh.write(b"bake")
    _scr = _twins.create(os.path.join(tmp, "projA", "shot.ma"), _bake)
    check(
        "the engine's twins store is one process-wide ptk.ScratchTwins",
        _twins is _rm_mod._scratch_twins()
        and isinstance(_twins, ptk.ScratchTwins)
        and os.path.isfile(_scr),
    )
    rm.discard_stale_scratches()  # the open file (an empty scene) is not the twin
    check(
        "engine: discard_stale_scratches removes an untouched twin once the scene moved on",
        not os.path.exists(_scr) and not _twins.is_twin(_scr),
    )

    # --- open_scene pins the scene's project: marked project > browsed folder > own folder ---
    _proj = os.path.join(tmp, "proj_marked")
    os.makedirs(os.path.join(_proj, "scenes"), exist_ok=True)
    ptk.Workspace.create(_proj, create_dirs=False)
    _loose_root = os.path.join(tmp, "loose")
    _loose = os.path.join(_loose_root, "sub")
    os.makedirs(_loose, exist_ok=True)
    _a_blend = os.path.join(_proj, "scenes", "a.blend")
    _b_blend = os.path.join(_loose, "b.blend")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.save_as_mainfile(filepath=_a_blend)
    bpy.ops.wm.save_as_mainfile(filepath=_b_blend)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _prev_pin = btk.current_workspace()
    _browse_rm = ReferenceManager()
    _browse_rm.current_working_dir = _loose_root

    def _pinned():
        _ws = btk.current_workspace()
        return _norm(_ws.root) if _ws is not None else ""

    check(
        "engine: open_scene pins the marked project containing the scene",
        _browse_rm.open_scene(_a_blend) is True and _pinned() == _norm(_proj),
        _pinned(),
    )
    check(
        "engine: ...else the browsed folder when the scene lies under it",
        _browse_rm.open_scene(_b_blend) is True and _pinned() == _norm(_loose_root),
        _pinned(),
    )
    _plain_rm = ReferenceManager()  # nothing browsed
    check(
        "engine: ...else the scene's own folder",
        _plain_rm.open_scene(_b_blend) is True and _pinned() == _norm(_loose),
        _pinned(),
    )
    btk.set_current_workspace(_proj)
    _plain_rm.open_scene(_b_blend, set_workspace=False)
    check(
        "engine: set_workspace=False leaves the session's workspace alone",
        _pinned() == _norm(_proj),
        _pinned(),
    )
    check(
        "engine: is_current knows the open file",
        _plain_rm.is_current(_b_blend) and not _plain_rm.is_current(_a_blend),
    )
    check(
        "engine: current_workspace is the session's pin",
        _norm(_plain_rm.current_workspace or "") == _norm(_proj),
        _plain_rm.current_workspace,
    )
    _raised = False
    try:
        _plain_rm.open_scene(os.path.join(tmp, "missing.blend"))
    except FileNotFoundError:
        _raised = True
    check("engine: opening a missing file raises FileNotFoundError", _raised)
    btk.set_current_workspace(None)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _bare_rm = ReferenceManager()
    check(
        "engine: no pin, no saved file -> no workspace to browse (no Documents fallback)",
        _bare_rm.current_workspace == "" and _bare_rm.current_working_dir == "",
        f"{_bare_rm.current_workspace!r} / {_bare_rm.current_working_dir!r}",
    )
    btk.set_current_workspace(_prev_pin.root if _prev_pin else None)

    # --- a foreign scene opened as new: the bake's project is its SOURCE's --------------------
    # BACKLOG 2026-10-04 (decided 2026-10-05): the scratch twin lives in temp, so the pin
    # on the source's project did not hold it, and a lightmap bake's project was the
    # scratch folder -- Beside Material Textures accepted no texture folder, and a re-bake
    # into the project set aside nothing it superseded. The scratch counts as its source.
    _src_proj = os.path.join(tmp, "proj_source")
    os.makedirs(os.path.join(_src_proj, "scenes"), exist_ok=True)
    ptk.Workspace.create(_src_proj, create_dirs=False)
    _src = os.path.join(_src_proj, "scenes", "opened.ma")
    with open(_src, "w") as f:
        f.write("//Maya ASCII 2025 scene\n")
    _opened_bake = os.path.join(tmp, "opened_bake.blend")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _si_mod.MayaSceneImport._write_bake_record({_si_mod.BAKE_INFO_SOURCE: _src})
    bpy.ops.wm.save_as_mainfile(filepath=_opened_bake)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _bake_projects = []

    def _bake_to_opened(path, project=None):  # the panel's bake, stubbed
        _bake_projects.append(project)
        return _opened_bake

    _open_rm = ReferenceManager(bake_for_reference=_bake_to_opened)
    _opened = _open_rm.open_scene(_src)
    _v3d_overlays = [
        sp.overlay
        for scr in bpy.data.screens
        for ar in scr.areas
        if ar.type == "VIEW_3D"
        for sp in ar.spaces
        if getattr(sp, "overlay", None) is not None
    ]
    check(
        "open as new: the opened scene's Relationship Lines are off",
        _v3d_overlays and not any(o.show_relationship_lines for o in _v3d_overlays),
        f"{len(_v3d_overlays)} viewports",
    )
    check(
        "open as new: the open file is the source's scratch twin in temp",
        _opened is True
        and os.path.normcase(bpy.data.filepath)
        == os.path.normcase(ReferenceManager.foreign_scratch_path(_src)),
        f"{_opened} {bpy.data.filepath}",
    )
    check(
        "open as new: the bake goes in the SOURCE's project",
        [_norm(p or "") for p in _bake_projects] == [_norm(_src_proj)],
        str(_bake_projects),
    )
    check(
        "open as new: the twin carries no bake record (it is not a bake)",
        _si_mod.BAKE_INFO_TEXT not in bpy.data.texts,
        str(list(bpy.data.texts.keys())),
    )
    check(
        "open as new: is_current reads the foreign row as the open scene",
        _open_rm.is_current(_src),
    )
    check(
        "open as new: the bake's project is the pinned source project, not the scratch's folder",
        os.path.normcase(btk.DataNodes.scene_project_root() or "")
        == os.path.normcase(_src_proj),
        str(btk.DataNodes.scene_project_root()),
    )
    _pin_elsewhere = os.path.join(tmp, "proj_elsewhere")
    os.makedirs(_pin_elsewhere, exist_ok=True)
    btk.set_current_workspace(_pin_elsewhere)
    check(
        "...and with the pin moved off it, the source's own project",
        os.path.normcase(btk.DataNodes.scene_project_root() or "")
        == os.path.normcase(_src_proj),
        str(btk.DataNodes.scene_project_root()),
    )
    btk.set_current_workspace(_prev_pin.root if _prev_pin else None)
    _scr_open = bpy.data.filepath
    check(
        "engine: new_scene closes it, its untouched twin discarded",
        _open_rm.new_scene() is True
        and bpy.data.filepath == ""
        and not os.path.exists(_scr_open),
        _scr_open,
    )
    _twins.discard_except("")
    # No twin can be written: refuse, never open the project's bake itself
    # (a save would then write into the bake cache).

    class _NoTwins:
        def create(self, *_a):
            raise OSError("disk full")

        def discard_except(self, *_a):
            pass

    _stock_twins = _rm_mod._scratch_twins
    _rm_mod._scratch_twins = lambda: _NoTwins()
    _no_twin = None
    try:
        _open_rm.open_scene(_src)
    except OSError as e:
        _no_twin = str(e)
    finally:
        _rm_mod._scratch_twins = _stock_twins
    check(
        "open as new: no twin -> OSError naming why, the bake never opened",
        _no_twin is not None
        and "disk full" in _no_twin
        and os.path.normcase(bpy.data.filepath) != os.path.normcase(_opened_bake),
        f"{bpy.data.filepath} error={_no_twin}",
    )
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # A library path re-spelled for the open file never raises across drives.
    _xd_open = os.path.join(tmp, "xdrive_host.blend")
    bpy.ops.wm.save_as_mainfile(filepath=_xd_open)
    _xd_other = ("Q:" if os.path.splitdrive(_xd_open)[0].upper() != "Q:" else "R:") + (
        "\\proj\\cache\\bake.blend"
    )
    check(
        "DataNodes._blend_relpath: another drive keeps the path absolute (no raise)",
        os.name != "nt" or btk.DataNodes._blend_relpath(_xd_other) == _xd_other,
    )
    # The re-home relinks across drives (the open file in temp, the project's
    # bake on another drive) without raising: the path stays absolute.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _xr_dir = os.path.join(HERE, "temp_tests", "btk_rm_xdrive")
    shutil.rmtree(_xr_dir, ignore_errors=True)
    os.makedirs(_xr_dir, exist_ok=True)
    _xr_store = ptk.TempArtifacts("btk_rm_xdrive_test", policy="scoped")
    _xr_stock = (
        _si_mod.MayaSceneImport.__dict__["bake_source"],
        _si_mod.MayaSceneImport.__dict__["rehome_bake"],
    )
    try:
        _xr_target = os.path.join(_xr_dir, "btk_bake_cache_xr_0123abcd.blend")
        _xr_coll = bpy.data.collections.new("XR")
        bpy.data.libraries.write(_xr_target, {_xr_coll}, fake_user=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _xr_old = _xr_store.path(extension=".blend")
        shutil.copyfile(_xr_target, _xr_old)
        bpy.ops.wm.save_as_mainfile(filepath=_xr_store.path(extension=".blend"))
        btk.link_blend_file(_xr_old, link=True)
        _si_mod.MayaSceneImport.bake_source = classmethod(lambda cls, p: _src)
        _si_mod.MayaSceneImport.rehome_bake = lambda self, p, project=None: _xr_target
        _xr_moved = ReferenceManager().follow_bake_sources()
        _xr_lib = next(iter(bpy.data.libraries), None)
        check(
            "engine: re-home of a bake on another drive than the open file (absolute)",
            len(_xr_moved) == 1
            and _xr_lib is not None
            and os.path.normcase(bpy.path.abspath(_xr_lib.filepath))
            == os.path.normcase(_xr_target),
            getattr(_xr_lib, "filepath", None),
        )
    finally:
        (
            _si_mod.MayaSceneImport.bake_source,
            _si_mod.MayaSceneImport.rehome_bake,
        ) = _xr_stock
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _xr_store.cleanup()
        shutil.rmtree(_xr_dir, ignore_errors=True)

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
