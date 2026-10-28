"""blendertk.mat_utils._mat_utils headless test: the name-based MatUtils operations a
material list drives (tentacle's Materials panel, through ``self.tk``) -- names in, names
out, each mutation one undo step, a vanished name a ``ValueError``.

Mirror of mayatk's ``test_mat_utils.py`` TestNamedMaterialOperations.

Run: blender --background --factory-startup --python blendertk/test/test_mat_utils.py
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # blendertk/
MONO = os.path.dirname(REPO)  # _scripts/
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")


def raises(error, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except error:
        return True
    return False


try:
    import bpy
    import pythontk as ptk
    from blendertk.core_utils._core_utils import CoreUtils
    from blendertk.mat_utils._mat_utils import MatUtils

    def reset():
        if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    def add_cube(name, location=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(location=location)
        o = bpy.context.active_object
        o.name = name
        return o

    # ---- undo: every mutation the panel calls directly is one step ----------------------
    for _op in (
        "merge_duplicate_materials", "delete_mats", "rename_mat", "select_by_mat",
        "select_unassigned", "show_mat_attributes", "assign_random_mat", "assign_new_mat",
        "delete_unused_materials",
    ):
        check(f"MatUtils.{_op} is one undo step (CoreUtils.undoable)",
              hasattr(getattr(MatUtils, _op), "__wrapped__"))
    check("MatUtils.assign_mat stays raw (bulk callers; the panel wraps it in undo_chunk)",
          not hasattr(MatUtils.assign_mat, "__wrapped__"))

    # ---- a scene: crate A + its duplicate B, wood missing its texture, old unused --------
    reset()
    with ptk.TempArtifacts("btk_named_mats", policy="scoped") as store:
        crate = bpy.data.images.new("Crate_BaseColor", 4, 4)
        crate.filepath_raw = os.path.join(store.dir_path(), "Crate_BaseColor.png")
        crate.file_format = "PNG"
        crate.save()
        wood = bpy.data.images.new("Wood_BaseColor", 4, 4)
        wood.source = "FILE"
        wood.filepath = os.path.join(store.dir_path(), "gone", "Wood_BaseColor.png")
        geos = {}
        for i, (name, img) in enumerate((("CrateA", crate), ("CrateB", crate), ("Wood", wood))):
            mat = MatUtils.create_mat("standard", name=name)
            mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
            geos[name] = add_cube(f"{name}_geo", (3 * i, 0, 0))
            MatUtils.assign_mat(geos[name], mat)
        MatUtils.create_mat("standard", name="Old")
        MatUtils.create_mat("standard", name="Kept").use_fake_user = True
        CoreUtils.select([])

        names = MatUtils.get_scene_mat_names()
        check("get_scene_mat_names: the file's material names, sorted",
              names == sorted(names) and {"CrateA", "CrateB", "Wood", "Old"} <= set(names),
              names)
        check("get_scene_mat_names takes mayatk's filters (no-ops here)",
              MatUtils.get_scene_mat_names(exclude_defaults=True, exc_renderer="arnold") == names)

        check("get_mat_names: [] with nothing selected", MatUtils.get_mat_names() == [])
        CoreUtils.select([geos["CrateA"], geos["Wood"]])
        check("get_mat_names: the selection's materials, as names",
              sorted(MatUtils.get_mat_names()) == ["CrateA", "Wood"])
        check("get_mat_names: given objects", MatUtils.get_mat_names([geos["Wood"]]) == ["Wood"])

        facts = MatUtils.get_mat_facts(["CrateA", "CrateB", "Wood", "Old", "Kept", "Nope"])
        check("get_mat_facts: a name gone is left out", "Nope" not in facts, list(facts))
        check("get_mat_facts: a verified duplicate names its original",
              facts["CrateB"]["duplicate_of"] == "CrateA" and facts["CrateA"]["duplicate_of"] is None,
              facts.get("CrateB"))
        check("get_mat_facts: a missing texture by its file name",
              facts["Wood"]["missing"] == ["Wood_BaseColor.png"] and facts["CrateA"]["missing"] == [],
              facts.get("Wood"))
        check("get_mat_facts: unused, unless a fake user keeps it",
              facts["Old"]["unused"] and not facts["Kept"]["unused"] and not facts["CrateA"]["unused"])
        check("get_mat_facts: type, users and textures",
              (facts["CrateA"]["type"], facts["CrateA"]["users"], facts["CrateA"]["textures"])
              == ("Principled BSDF", 1, 1), facts.get("CrateA"))

        check("find_duplicate_materials: {duplicate: original} names",
              MatUtils.find_duplicate_materials() == {"CrateB": "CrateA"})
        check("find_duplicate_materials: an empty scope finds none",
              MatUtils.find_duplicate_materials([]) == {})
        check("merge_duplicate_materials: a pair whose duplicate is gone merges nothing",
              MatUtils.merge_duplicate_materials({"Nope": "CrateA"}) == 0)
        check("merge_duplicate_materials: a pair whose original is gone is skipped",
              MatUtils.merge_duplicate_materials({"CrateB": "Nope"}) == 0
              and "CrateB" in bpy.data.materials)
        merged = MatUtils.merge_duplicate_materials({"CrateB": "CrateA"})
        check("merge_duplicate_materials: merged, counted, the users reassigned",
              merged == 1 and "CrateB" not in bpy.data.materials
              and MatUtils.get_mat_names([geos["CrateB"]]) == ["CrateA"], merged)

        check("get_mat_swatch_icon: a name gone gives None",
              MatUtils.get_mat_swatch_icon("Nope") is None)

        # ---- rename, delete, assign ------------------------------------------------------
        check("rename_mat: returns the name Blender gave (a clash suffixes)",
              MatUtils.rename_mat("Old", "Wood") == "Wood.001")
        check("rename_mat: a name gone is a ValueError",
              raises(ValueError, MatUtils.rename_mat, "Nope", "X"))
        check("delete_mats: the names deleted, a name gone skipped",
              MatUtils.delete_mats(["Wood.001", "Nope"]) == ["Wood.001"]
              and "Wood.001" not in bpy.data.materials)
        check("assign_mat: a name assigns that material",
              (MatUtils.assign_mat([geos["Wood"]], "CrateA"),
               MatUtils.get_mat_names([geos["Wood"]]))[1] == ["CrateA"])
        check("assign_mat(create=False): a name gone is a ValueError, nothing made",
              raises(ValueError, MatUtils.assign_mat, [geos["Wood"]], "Nope", create=False)
              and "Nope" not in bpy.data.materials)
        MatUtils.assign_mat([geos["Wood"]], "Made")
        check("assign_mat: a name gone makes it (mayatk's default)",
              MatUtils.get_mat_names([geos["Wood"]]) == ["Made"])

        # ---- random / new -----------------------------------------------------------------
        CoreUtils.select([])
        check("assign_random_mat / assign_new_mat: nothing selected is a ValueError",
              raises(ValueError, MatUtils.assign_random_mat)
              and raises(ValueError, MatUtils.assign_new_mat))
        rnd = add_cube("Rnd_geo", (12, 0, 0))
        CoreUtils.select([rnd])
        first = MatUtils.assign_random_mat()
        second = MatUtils.assign_random_mat(None, previous=first)
        check("assign_random_mat: a re-roll replaces what nothing uses any more",
              first not in bpy.data.materials and MatUtils.get_mat_names([rnd]) == [second],
              f"{first} {second}")
        third = MatUtils.assign_random_mat([geos["Wood"]], previous=second)
        check("assign_random_mat: the previous one stays while something uses it",
              second in bpy.data.materials and third != second)
        new = MatUtils.assign_new_mat([rnd])
        check("assign_new_mat: a Material, assigned, its name returned",
              new.startswith("Material") and MatUtils.get_mat_names([rnd]) == [new], new)

        # ---- select -----------------------------------------------------------------------
        got = MatUtils.select_by_mat("CrateA", shell=True)
        check("select_by_mat(shell): its objects selected, the rest not",
              sorted(o.name for o in got) == ["CrateA_geo", "CrateB_geo"]
              and sorted(o.name for o in CoreUtils.selected_objects()) == ["CrateA_geo", "CrateB_geo"],
              [o.name for o in got])
        MatUtils.select_by_mat(new, shell=True, add=True)
        check("select_by_mat(add): added to the selection",
              {"CrateA_geo", "CrateB_geo", "Rnd_geo"} <= {o.name for o in CoreUtils.selected_objects()})
        check("select_by_mat: within objects", [o.name for o in MatUtils.select_by_mat(
            "CrateA", [geos["CrateA"]], shell=True)] == ["CrateA_geo"])
        check("select_by_mat: a name gone is a ValueError",
              raises(ValueError, MatUtils.select_by_mat, "Nope"))
        bare = add_cube("Bare_geo", (15, 0, 0))
        got = MatUtils.select_unassigned()
        check("select_unassigned: the object with no material",
              [o.name for o in got] == [bare.name], [o.name for o in got])
        # every user outside the view layer: refused, the selection untouched
        hidden = bpy.data.collections.new("TblHidden")
        bpy.context.scene.collection.children.link(hidden)
        out = bpy.data.objects.new("Out_geo", bpy.data.meshes.new("Out_mesh"))
        hidden.objects.link(out)
        out.data.materials.append(MatUtils.create_mat("standard", name="OutOnly"))
        bpy.context.view_layer.layer_collection.children["TblHidden"].exclude = True
        bpy.context.view_layer.update()  # the layer's object list follows the exclude
        before_sel = [o.name for o in CoreUtils.selected_objects()]
        check("select_by_mat: every user outside the view layer is a RuntimeError",
              raises(RuntimeError, MatUtils.select_by_mat, "OutOnly", shell=True)
              and [o.name for o in CoreUtils.selected_objects()] == before_sel)

        # ---- show attributes ----------------------------------------------------------------
        shown = MatUtils.show_mat_attributes(new)
        check("show_mat_attributes: its users selected, the active one on its slot",
              [o.name for o in shown] == ["Rnd_geo"]
              and CoreUtils.active_object().active_material.name == new,
              [o.name for o in shown])
        check("show_mat_attributes: nothing uses it -> []",
              MatUtils.show_mat_attributes("Kept") == [])

        # ---- textures -----------------------------------------------------------------------
        check("get_texture_paths: material names resolve",
              [os.path.basename(p) for p in MatUtils.get_texture_paths(materials=["CrateA"])]
              == ["Crate_BaseColor.png"])
        check("get_texture_paths(exclude_bundled): a project texture stays",
              len(MatUtils.get_texture_paths(materials=["CrateA"], exclude_bundled=True)) == 1)
        check("get_texture_paths(exclude_bundled): Blender's own installation is dropped",
              MatUtils._is_bundled_path(os.path.join(os.path.dirname(bpy.app.binary_path), "x.png"))
              and not MatUtils._is_bundled_path(crate.filepath_raw))
        unassigned = [i["name"] for i in MatUtils.get_texture_info(exclude_unassigned=True)]
        check("get_texture_info(exclude_unassigned): only the used materials' textures",
              "Crate_BaseColor.png" in unassigned and "Wood_BaseColor.png" not in unassigned,
              unassigned)
        scopes = MatUtils.get_texture_scopes()
        CoreUtils.select([])
        check("get_texture_scopes: Selected Objects, [] with nothing selected",
              list(scopes) == ["Selected Objects"] and scopes["Selected Objects"]() == [])
        CoreUtils.select([geos["CrateA"]])
        check("get_texture_scopes: the selected objects' textures",
              [os.path.basename(p) for p in scopes["Selected Objects"]()] == ["Crate_BaseColor.png"])
        MatUtils.reload_textures(reset_viewport=True)
        check("reload_textures(reset_viewport) runs headless", True)

        # ---- delete unused ------------------------------------------------------------------
        MatUtils.create_mat("standard", name="Orphan")
        removed = MatUtils.delete_unused_materials()
        check("delete_unused_materials: the orphan's name, the kept and used ones stay",
              "Orphan" in removed and "Kept" not in removed and "CrateA" in bpy.data.materials,
              removed)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
print("\n===MAT-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
