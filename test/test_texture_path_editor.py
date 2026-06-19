"""blendertk Texture Path Editor engine headless test — verifies the bpy-side functions that back
the co-located ``texture_path_editor`` panel (the Qt slot itself can't run headless: Blender ships
no Qt binding; panel structure/wiring is covered by ``test_blender_ui_handler.py`` under the .venv).

Run: blender --background --factory-startup --python blendertk/test/test_texture_path_editor.py
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

tmp = tempfile.mkdtemp(prefix="tpe_test_")
try:
    import bpy
    import blendertk as btk
    from blendertk.mat_utils._mat_utils import _abspath

    def reset():
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)
        for i in list(bpy.data.images):
            if i.users == 0:
                bpy.data.images.remove(i)

    def write_png(path, name="gen"):
        """Write a real 4x4 PNG to disk and return the path."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        gen = bpy.data.images.new(name, 4, 4)
        gen.filepath_raw = path
        gen.file_format = "PNG"
        gen.save()
        bpy.data.images.remove(gen)
        return path

    reset()
    src_dir = os.path.join(tmp, "src")
    tex_path = write_png(os.path.join(src_dir, "wood_DIFF.png"))
    img = bpy.data.images.load(tex_path)
    mat = btk.create_mat("standard", name="WoodMat")
    texnode = mat.node_tree.nodes.new("ShaderNodeTexImage")
    texnode.image = img

    # 1. get_image_records — the FILE image is listed and exists on disk.
    records = btk.get_image_records()
    rec = next((r for r in records if r["image"] is img), None)
    check("get_image_records lists the image", rec is not None)
    check("record marks the file as existing", bool(rec and rec["exists"]))

    # 2. get_image_material_map — image -> the material referencing it.
    mp = btk.get_image_material_map()
    check("get_image_material_map links image -> material", mp.get(img.name) == ["WoodMat"], f"{mp}")

    # 3. set_texture_directory(copy) — relocate the file + repath.
    dest_dir = os.path.join(tmp, "dest")
    n = btk.set_texture_directory([img], dest_dir, mode="copy")
    moved = os.path.join(dest_dir, "wood_DIFF.png")
    check("set_texture_directory copies + repaths", n == 1 and os.path.exists(moved))
    check("image now points under dest dir", os.path.normpath(_abspath(img)) == os.path.normpath(moved))

    # 4. resolve_missing_textures (exact stem) — break the path, then resolve from a folder.
    resolve_dir = os.path.join(tmp, "resolve")
    write_png(os.path.join(resolve_dir, "wood_DIFF.png"))
    img.filepath = os.path.join(tmp, "gone", "wood_DIFF.png")  # missing
    check("path is now missing", not os.path.exists(_abspath(img)))
    n = btk.resolve_missing_textures(resolve_dir)
    check("resolve_missing_textures (stem) repaths", n == 1 and os.path.exists(_abspath(img)))

    # 5. resolve_missing_textures (fuzzy) — different stem, only matched with fuzzy=True.
    fuzzy_dir = os.path.join(tmp, "fuzzy")
    write_png(os.path.join(fuzzy_dir, "wood_DIFFUSE_4k.png"))
    img.filepath = os.path.join(tmp, "gone", "wood_DIFFUSE.png")  # missing, no exact stem
    n_exact = btk.resolve_missing_textures(fuzzy_dir, fuzzy=False)
    check("fuzzy off does not over-match", n_exact == 0)
    n_fuzzy = btk.resolve_missing_textures(fuzzy_dir, fuzzy=True)
    check("fuzzy on resolves the loose name", n_fuzzy == 1 and "wood_DIFFUSE_4k" in _abspath(img))

    # 5b. stem tier — same name, different extension, only matched with stem=True.
    stem_dir = os.path.join(tmp, "stemdir")
    write_png(os.path.join(stem_dir, "rock_DIFF.png"))
    img.filepath = os.path.join(tmp, "gone", "rock_DIFF.tga")  # missing, different extension
    n_off = btk.resolve_missing_textures(stem_dir, stem=False, fuzzy=False)
    check("stem off: different-extension not matched", n_off == 0)
    n_on = btk.resolve_missing_textures(stem_dir, stem=True)
    check("stem on: same-stem different-extension resolves",
          n_on == 1 and "rock_DIFF.png" in _abspath(img))

    # 6. find_and_copy_textures — search a tree, relocate to a destination, repath.
    reset()
    search_root = os.path.join(tmp, "search", "deep", "nested")
    find_tex = write_png(os.path.join(search_root, "metal_NRM.png"))
    img2 = bpy.data.images.load(find_tex)
    mat2 = btk.create_mat("standard", name="MetalMat")
    mat2.node_tree.nodes.new("ShaderNodeTexImage").image = img2
    img2.filepath = os.path.join(tmp, "gone", "metal_NRM.png")  # break so we must find it
    find_dest = os.path.join(tmp, "find_dest")
    n = btk.find_and_copy_textures([img2], os.path.join(tmp, "search"), find_dest, mode="copy")
    check("find_and_copy_textures relocates + repaths",
          n == 1 and os.path.exists(os.path.join(find_dest, "metal_NRM.png")))

    # 7. normalize_texture_paths(absolute) — make the path absolute.
    n = btk.normalize_texture_paths("absolute")
    check("normalize_texture_paths(absolute) runs", isinstance(n, int))

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
