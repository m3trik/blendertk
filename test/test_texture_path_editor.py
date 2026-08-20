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
    import pythontk as ptk
    import blendertk as btk
    from blendertk.env_utils._env_utils import EnvUtils
    from blendertk.mat_utils._mat_utils import _MatUtilsInternal

    _abspath = _MatUtilsInternal._abspath  # helper moved onto the internal base

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

    # 7b. to_project_relative is PURE given both roots — the containment test is against the
    # workspace ROOT, not the .blend's folder. In the standard layout (<root>/scenes/x.blend beside
    # <root>/sourceimages/) the old blenddir-only test refused every texture the panel manages, so
    # Normalize Paths silently did nothing and its copy/move modes left the path absolute.
    rel = btk.to_project_relative(
        os.path.join(tmp, "proj", "sourceimages", "t.png"),
        blenddir=os.path.join(tmp, "proj", "scenes"),
        project_root=os.path.join(tmp, "proj"),
    )
    check("to_project_relative relativizes across the project root", rel == "//../sourceimages/t.png", rel)
    flat = btk.to_project_relative(
        os.path.join(tmp, "proj", "sourceimages", "t.png"),
        blenddir=os.path.join(tmp, "proj"),
        project_root=os.path.join(tmp, "proj"),
    )
    check("to_project_relative still handles the flat layout", flat == "//sourceimages/t.png", flat)
    # Case-folding: os.path.commonpath compares case-sensitively, so a differently-cased blend dir
    # used to leave the path absolute on Windows.
    cased = btk.to_project_relative(
        os.path.join(tmp, "proj", "sourceimages", "t.png"),
        blenddir=os.path.join(tmp, "proj").upper(),
        project_root=os.path.join(tmp, "proj").upper(),
    )
    check("to_project_relative folds path case", cased == "//sourceimages/t.png", cased)
    outside = btk.to_project_relative(
        os.path.join(tmp, "elsewhere", "t.png"),
        blenddir=os.path.join(tmp, "proj", "scenes"),
        project_root=os.path.join(tmp, "proj"),
    )
    check("to_project_relative leaves out-of-project paths absolute", not outside.startswith("//"), outside)

    # 7c. Normalize Paths end-to-end in the layout that broke: .blend saved in <root>/scenes,
    # texture in <root>/sourceimages -> the path must come back '//'-relative.
    reset()
    proj = os.path.join(tmp, "wsproj")
    # Mark <proj> as a workspace (workspace.mel) so it — not the .blend's own scenes/ folder —
    # is the resolved project root; that marker is what makes the layout a project rather than
    # a loose folder, and it is the same marker mayatk reads.
    ptk.Workspace.create(proj)
    si_dir = os.path.join(proj, "sourceimages")
    si_tex = write_png(os.path.join(si_dir, "brick_DIFF.png"))
    os.makedirs(os.path.join(proj, "scenes"), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(proj, "scenes", "shot.blend"))
    check("workspace root resolves to the project, not the .blend folder",
          EnvUtils.workspace_root() == os.path.normpath(proj),
          f"{EnvUtils.workspace_root()!r} vs {proj!r}")
    img3 = bpy.data.images.load(si_tex)
    img3.filepath = si_tex  # absolute, outside the .blend's own folder
    n = btk.normalize_texture_paths("relative", images=[img3])
    check("normalize(relative) rewrites a sourceimages path in a project layout",
          n == 1 and img3.filepath.startswith("//"), f"n={n} filepath={img3.filepath!r}")
    check("normalized path still resolves on disk", os.path.exists(_abspath(img3)), _abspath(img3))

    # 7c2. normalize(absolute) — the inverse (backs the panel's Make Paths Absolute action):
    # the //-relative path becomes absolute again, still resolves, and round-trips back.
    n = btk.normalize_texture_paths("absolute", images=[img3])
    check("normalize(absolute) rewrites // back to absolute",
          n == 1 and os.path.isabs(img3.filepath), f"n={n} filepath={img3.filepath!r}")
    check("absolutized path still resolves on disk", os.path.exists(_abspath(img3)), _abspath(img3))
    n = btk.normalize_texture_paths("relative", images=[img3])
    check("absolute -> relative round-trips",
          n == 1 and img3.filepath.startswith("//"), f"n={n} filepath={img3.filepath!r}")

    # 7d. Normalize Paths / copy — an EXTERNAL texture is brought into the project AND ends up
    # relative (it used to be copied in but left with an absolute path).
    ext_tex = write_png(os.path.join(tmp, "external", "steel_DIFF.png"))
    img4 = bpy.data.images.load(ext_tex)
    moved = btk.normalize_texture_paths("copy", project_dir=si_dir, images=[img4])
    check("normalize(copy) relocates the external texture",
          moved == 1 and os.path.exists(os.path.join(si_dir, "steel_DIFF.png")))
    check("normalize(copy) leaves the path relative, not absolute",
          img4.filepath.startswith("//"), f"{img4.filepath!r}")

    # 7d2. Normalize Paths / move with duplicated datablocks — two images (a `.001` twin)
    # storing the SAME external file: the first move stages it and removes the original, so
    # the second finds no source on disk. It must rebind to the same staged twin — before
    # the 2026-08-20 fix it read "shared" as "missing" and stayed absolute on a deleted
    # file (mirror of mayatk's moved_this_run rule).
    shared_tex = write_png(os.path.join(tmp, "external", "shared_DIFF.png"))
    img4a = bpy.data.images.load(shared_tex)
    img4b = bpy.data.images.load(shared_tex, check_existing=False)  # the .001 twin
    n_mv = btk.normalize_texture_paths("move", project_dir=si_dir, images=[img4a, img4b])
    check("normalize(move) rebinds BOTH twins sharing the external path",
          n_mv == 2
          and img4a.filepath.startswith("//")
          and img4b.filepath == img4a.filepath,
          f"n={n_mv} a={img4a.filepath!r} b={img4b.filepath!r}")
    check("and the external original was moved, not copied",
          not os.path.exists(shared_tex)
          and os.path.exists(os.path.join(si_dir, "shared_DIFF.png")))

    # 7e. Ambiguity guard — the same basename in two folders must NOT auto-resolve (mayatk skips
    # with a warning rather than binding to whichever copy the walk reached first).
    amb_dir = os.path.join(tmp, "ambiguous")
    write_png(os.path.join(amb_dir, "a", "amb_DIFF.png"))
    write_png(os.path.join(amb_dir, "b", "amb_DIFF.png"))
    img5 = bpy.data.images.load(write_png(os.path.join(tmp, "ambsrc", "amb_DIFF.png")))
    img5.filepath = os.path.join(tmp, "gone", "amb_DIFF.png")  # missing
    n_amb = btk.resolve_missing_textures(amb_dir, stem=True, texture=True, fuzzy=True)
    check("resolve_missing_textures refuses an ambiguous multi-hit", n_amb == 0, f"n={n_amb}")

    # 7f. Find & Copy picks the NEWEST duplicate, not the shallowest (mayatk's dedup rule).
    reset()
    dup_root = os.path.join(tmp, "dups")
    shallow = write_png(os.path.join(dup_root, "dup_DIFF.png"))
    deep = write_png(os.path.join(dup_root, "nested", "dup_DIFF.png"))
    os.utime(shallow, (1_000_000, 1_000_000))  # shallow = OLD
    os.utime(deep, (2_000_000, 2_000_000))  # nested = NEW
    img6 = bpy.data.images.load(shallow)
    img6.filepath = os.path.join(tmp, "gone", "dup_DIFF.png")
    dup_dest = os.path.join(tmp, "dup_dest")
    n = btk.find_and_copy_textures([img6], dup_root, dup_dest, mode="copy")
    picked = os.path.join(dup_dest, "dup_DIFF.png")
    check("find_and_copy_textures copies a match", n == 1 and os.path.exists(picked))
    check("find_and_copy_textures picks the newest duplicate",
          abs(os.path.getmtime(picked) - os.path.getmtime(deep)) < 1.0,
          f"picked={os.path.getmtime(picked)} deep={os.path.getmtime(deep)} shallow={os.path.getmtime(shallow)}")

    # 7g. Find & Copy sources a VALID path directly: an image whose filepath resolves is its own
    # source, so no search dir is needed at all (the panel skips that dialog on the same rule).
    reset()
    valid_src = write_png(os.path.join(tmp, "valid_src", "keep_DIFF.png"))
    img7 = bpy.data.images.load(valid_src)
    vp_dest = os.path.join(tmp, "vp_dest")
    n = btk.find_and_copy_textures([img7], None, vp_dest, mode="copy")
    check("find_and_copy_textures relocates from a valid path with no search dir",
          n == 1 and os.path.exists(os.path.join(vp_dest, "keep_DIFF.png")), f"n={n}")
    check("find_and_copy_textures repaths the image to the destination",
          os.path.normcase(_abspath(img7))
          == os.path.normcase(os.path.normpath(os.path.join(vp_dest, "keep_DIFF.png"))),
          _abspath(img7))

    # use_valid_paths=False is the old contract — nothing but the walk is a source.
    reset()
    img8 = bpy.data.images.load(write_png(os.path.join(tmp, "off_src", "off_DIFF.png")))
    n = btk.find_and_copy_textures(
        [img8], None, os.path.join(tmp, "off_dest"), mode="copy", use_valid_paths=False
    )
    check("use_valid_paths=False ignores the valid path and needs a search dir", n == 0, f"n={n}")

    # 7h. A valid path outranks a NEWER same-name hit under the search tree: it is the file the
    # scene is actually rendering with, so the newest-wins walk rule must not displace it.
    reset()
    real = write_png(os.path.join(tmp, "real_src", "dup2_DIFF.png"))
    stale = write_png(os.path.join(tmp, "stale_src", "dup2_DIFF.png"))
    os.utime(stale, (3_000_000, 3_000_000))
    os.utime(real, (1_000_000, 1_000_000))
    img9 = bpy.data.images.load(real)
    rank_dest = os.path.join(tmp, "rank_dest")
    btk.find_and_copy_textures([img9], os.path.join(tmp, "stale_src"), rank_dest, mode="copy")
    picked9 = os.path.join(rank_dest, "dup2_DIFF.png")
    check("a valid path outranks a newer search hit of the same name",
          os.path.exists(picked9) and abs(os.path.getmtime(picked9) - 1_000_000) < 1.0,
          f"picked={os.path.getmtime(picked9) if os.path.exists(picked9) else None}")

    # 7i. Move with the source already AT the destination is a self-relocation — the file must
    # survive it (the guard that makes 'Always Relocate To The Textures Folder' safe to repeat).
    reset()
    inplace_dir = os.path.join(tmp, "inplace_dest")
    in_place = write_png(os.path.join(inplace_dir, "here_DIFF.png"))
    img10 = bpy.data.images.load(in_place)
    n = btk.find_and_copy_textures([img10], None, inplace_dir, mode="move")
    check("move leaves a texture already at the destination intact",
          n == 1 and os.path.exists(in_place), f"n={n}")

    # 8. _abspath is LIBRARY-aware — an image linked from a library .blend stores its
    # ``//`` path relative to the LIBRARY file, not the current .blend, so resolving it
    # without ``library=img.library`` yields a wrong path (false "missing" in
    # check_valid_paths / get_image_records, wrong duplicate fingerprints).
    # Build a library .blend whose image uses a library-relative path, then link it
    # into a fresh UNSAVED main file (the worst case: '//' has nothing local to
    # resolve against). NOTE: this check re-reads factory settings, so it must stay LAST.
    lib_dir = os.path.join(tmp, "lib")
    lib_tex = write_png(os.path.join(lib_dir, "textures", "lib_tex.png"), name="libgen")
    lib_img = bpy.data.images.load(lib_tex)
    lib_img.name = "LibLinkedTex"
    lib_img.use_fake_user = True  # zero users — must survive the library save
    lib_blend = os.path.join(lib_dir, "lib.blend")
    bpy.ops.wm.save_as_mainfile(filepath=lib_blend)
    lib_img.filepath = "//textures/lib_tex.png"  # canonical library-relative form
    bpy.ops.wm.save_mainfile()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    with bpy.data.libraries.load(lib_blend, link=True) as (_from, _to):
        _to.images = ["LibLinkedTex"]
    linked = next((i for i in bpy.data.images if i.library), None)
    check("image links from the library with a //-relative path",
          linked is not None and (linked.filepath or "").startswith("//"),
          f"{(linked and linked.filepath)!r}")
    resolved = _abspath(linked) if linked else ""
    check("_abspath resolves a LINKED image against its LIBRARY, not the open .blend",
          bool(resolved) and os.path.normpath(resolved) == os.path.normpath(lib_tex)
          and os.path.exists(resolved),
          f"resolved={resolved!r} expected={lib_tex!r}")
    rec = next((r for r in btk.get_image_records() if r["image"] is linked), None)
    check("get_image_records marks the linked texture as existing on disk",
          bool(rec and rec["exists"]), f"{rec}")

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
