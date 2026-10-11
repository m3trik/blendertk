"""blendertk Texture Path Editor engine headless test — verifies the bpy-side functions that back
the co-located ``texture_path_editor`` panel, and the slot's handlers with Qt stood in for (the Qt
slot itself can't run headless: Blender ships no Qt binding; its widgets and wiring are covered by
``test_texture_path_editor_panel.py`` under the .venv).

Run: blender --background --factory-startup --python blendertk/test/test_texture_path_editor.py
"""
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}")

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

    # 2b. A texture inside a node group maps to the material using the group: the
    # map walked the top-level nodes only, so the panel showed it "(unused)" and
    # Keep Names In Sync found no material for it (review, 2026-10-05).
    grp_img = bpy.data.images.load(write_png(os.path.join(src_dir, "wood_ROUGH.png")))
    grp = bpy.data.node_groups.new("WoodDetail", "ShaderNodeTree")
    grp.nodes.new("ShaderNodeTexImage").image = grp_img
    grp_node = mat.node_tree.nodes.new("ShaderNodeGroup")
    grp_node.node_tree = grp
    mp = btk.get_image_material_map()
    check(
        "get_image_material_map maps a group-nested image to its material",
        mp.get(grp_img.name) == ["WoodMat"],
        f"{mp}",
    )
    mat.node_tree.nodes.remove(grp_node)
    bpy.data.node_groups.remove(grp)
    bpy.data.images.remove(grp_img)

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
    # 6a. plan_find_and_copy_textures — the same decision, written nowhere. The panel's
    # Dry Run reports from this, so it must name the exact source and destination the
    # commit then uses, and must not create the destination or touch the datablock.
    planned = btk.plan_find_and_copy_textures(
        [img2], os.path.join(tmp, "search"), find_dest
    )
    before = img2.filepath
    check("plan_find_and_copy_textures finds the one texture", len(planned) == 1,
          repr(planned))
    check("...naming the source the walk found",
          planned and planned[0]["source"] == find_tex, repr(planned))
    check("...and the destination it would land at",
          planned and planned[0]["destination"] == os.path.join(find_dest, "metal_NRM.png"))
    check("...listing the image that would repath",
          planned and planned[0]["images"] == [img2])
    check("...flagging that it is not already in place",
          planned and planned[0]["in_place"] is False)
    check("plan writes nothing: no destination folder", not os.path.exists(find_dest))
    check("plan writes nothing: the image is untouched", img2.filepath == before)

    n = btk.find_and_copy_textures([img2], os.path.join(tmp, "search"), find_dest, mode="copy")
    check("find_and_copy_textures relocates + repaths",
          n == 1 and os.path.exists(os.path.join(find_dest, "metal_NRM.png")))
    check("the commit landed the file the plan named",
          os.path.exists(planned[0]["destination"]) if planned else False)

    # 6b. A source already at the destination is flagged in_place: only the stored path
    # would change, and Move must not treat it as a self-copy.
    in_place_plan = btk.plan_find_and_copy_textures([img2], None, find_dest)
    check("plan flags a source already at the destination",
          len(in_place_plan) == 1 and in_place_plan[0]["in_place"] is True,
          repr(in_place_plan))

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
    # used to leave the path absolute on Windows. On a case-sensitive filesystem the recased dir
    # is a different folder, so the path correctly stays absolute there.
    _abs_tex = os.path.join(tmp, "proj", "sourceimages", "t.png")
    cased = btk.to_project_relative(
        _abs_tex,
        blenddir=os.path.join(tmp, "proj").upper(),
        project_root=os.path.join(tmp, "proj").upper(),
    )
    check(
        "to_project_relative folds path case exactly where the OS does",
        cased == ("//sourceimages/t.png" if os.name == "nt" else _abs_tex.replace("\\", "/")),
        cased,
    )
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

    # 7f2. Neither walk enters a folder of stale copies (ptk.FileDependencies.walk; 2026-09-27):
    # sync caches, the Recycle Bin, a _superseded folder, version control. Both walked everything,
    # so a texture whose only same-named file sat in one bound to that stale copy -- mayatk's walk
    # pruned them already.
    stale_root = os.path.join(tmp, "stale")
    for folder in (".dropbox.cache", "$Recycle.Bin", "_superseded", ".git"):
        reset()
        copy = write_png(os.path.join(stale_root, folder, "stale_DIFF.png"))
        img_s = bpy.data.images.load(write_png(os.path.join(tmp, "stalesrc", "stale_DIFF.png")))
        img_s.filepath = os.path.join(tmp, "gone", "stale_DIFF.png")  # missing
        n_stale = btk.resolve_missing_textures(stale_root, stem=True)
        planned_s = btk.plan_find_and_copy_textures(
            [img_s], stale_root, os.path.join(tmp, "stale_dest")
        )
        check(
            f"a copy in {folder} is never bound by Resolve Missing",
            n_stale == 0 and "gone" in _abspath(img_s),
            f"n={n_stale} path={_abspath(img_s)}",
        )
        check(
            "...nor found by Find & Copy",
            not planned_s,
            f"{planned_s}",
        )
        os.remove(copy)

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

    # 13. image_texture_nodes / select_image_nodes -- the Blender answer to Maya's
    # "Select File Node". Maya's file node is ONE object; Blender splits it into the image
    # datablock (owns the path) and the ShaderNodeTexImage nodes referencing it, so a row maps
    # to 0, 1, or N nodes. Covers the multi-material and nested-in-node-group cases.
    reset()
    sel_dir = os.path.join(tmp, "selnodes")
    sel_img = bpy.data.images.load(write_png(os.path.join(sel_dir, "sel_DIFF.png")))
    other_img = bpy.data.images.load(write_png(os.path.join(sel_dir, "other_DIFF.png")))

    made_nodes = []
    for mname in ("SelMatA", "SelMatB"):
        m = btk.create_mat("standard", name=mname)
        n = m.node_tree.nodes.new("ShaderNodeTexImage")
        n.image = sel_img
        made_nodes.append(n)
    # a decoy bound to a DIFFERENT image -- must not be selected
    decoy_mat = btk.create_mat("standard", name="SelMatDecoy")
    decoy = decoy_mat.node_tree.nodes.new("ShaderNodeTexImage")
    decoy.image = other_img
    # and one nested inside a node group
    grp = bpy.data.node_groups.new("SelGrp", "ShaderNodeTree")
    nested = grp.nodes.new("ShaderNodeTexImage")
    nested.image = sel_img
    grp_host_mat = btk.create_mat("standard", name="SelMatGroup")
    grp_host_mat.node_tree.nodes.new("ShaderNodeGroup").node_tree = grp

    # a SECOND material hosting the SAME node group -- the nested node is reachable through
    # both, but it is one node: counting it twice would overstate the selection and the
    # remove-confirm's "nodes left with no texture".
    grp_host_mat2 = btk.create_mat("standard", name="SelMatGroup2")
    grp_host_mat2.node_tree.nodes.new("ShaderNodeGroup").node_tree = grp

    hits = btk.image_texture_nodes(sel_img)
    check("image_texture_nodes finds every node bound to the image (groups included)",
          len(hits) == 3, f"{[(m.name, n.name) for m, n in hits]}")
    # bpy hands back a FRESH wrapper per access, so `is` never holds -- compare by pointer.
    check("a node group shared by two materials counts its node ONCE",
          sum(1 for _m, n in hits if n.as_pointer() == nested.as_pointer()) == 1,
          f"{[(m.name, n.name) for m, n in hits]}")
    check("image_texture_nodes ignores nodes bound to another image",
          all(n.image is sel_img for _m, n in hits))
    check("image_texture_nodes on an unused image returns []",
          btk.image_texture_nodes(bpy.data.images.new("Unused", 4, 4)) == [])

    n_sel = btk.select_image_nodes(sel_img)
    check("select_image_nodes selects them all", n_sel == 3, f"n={n_sel}")
    check("every bound node is selected", all(n.select for _m, n in hits),
          f"{[(n.name, n.select) for _m, n in hits]}")
    check("two hits in one tree both survive the replace-deselect",
          all(n.select for n in made_nodes))
    check("the decoy node is NOT selected", not decoy.select)
    check("the active node is one of the hits",
          made_nodes[0].id_data.nodes.active in [n for _m, n in hits])

    # The pure query must not touch selection state -- the remove-confirm counts with it.
    for _m, n in hits:
        n.select = False
    btk.image_texture_nodes(sel_img)
    check("image_texture_nodes is side-effect free (selects nothing)",
          not any(n.select for _m, n in hits))

    # Removing the datablock strands the nodes but keeps them (and the file on disk).
    node_names = [(m.name, n.name) for m, n in hits]
    sel_path = _abspath(sel_img)
    bpy.data.images.remove(sel_img)
    survivors = []
    for mname, nname in node_names:
        tree = bpy.data.materials[mname].node_tree if mname in bpy.data.materials else grp
        node = tree.nodes.get(nname) or grp.nodes.get(nname)
        survivors.append(node)
    check("removing the texture leaves its Image Texture nodes in place",
          all(n is not None for n in survivors), f"{node_names}")
    check("...with an empty texture slot",
          all(getattr(n, "image", None) is None for n in survivors if n))
    check("...and the file on disk untouched", os.path.exists(sel_path), sel_path)

    # 12. Lightmap rows in the panel (mirror of mayatk's TestFindAndCopyLightmaps'
    # scope tests). The Qt table is stubbed: the slot's row helpers are bpy-only.
    from types import SimpleNamespace
    from blendertk.mat_utils.texture_path_editor import TexturePathEditorSlots
    from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

    bpy.ops.mesh.primitive_cube_add()
    lm_cube = bpy.context.active_object
    lm_cube.name = "lm_cube"
    lm_mat = btk.create_mat("standard", name="ROOM_ENV")
    btk.assign_mat(lm_cube, lm_mat)
    lm_dir = os.path.join(tmp, "lightmaps")
    os.makedirs(lm_dir, exist_ok=True)
    lm_map = os.path.join(lm_dir, "ROOM_ENV_LightMap.exr")
    open(lm_map, "wb").close()
    LightmapBaker().commit_lightmap({lm_cube.name: lm_map})

    slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
    slot._lightmap_rows = {}
    slot._find_copy_lightmaps = []
    slot._find_copy_images = []
    deps = slot._lightmap_records().lightmap_dependencies()
    dep = deps[0] if deps else None
    check("the panel lists the committed lightmap as a dependency", dep is not None, f"{deps}")
    row_path = slot._lightmap_row_path(dep) if dep else ""
    check("a lightmap row shows the marker's recorded folder + map",
          row_path.endswith("/ROOM_ENV_LightMap.exr"), row_path)
    mat_label, node_label = slot._lightmap_row_labels(dep) if dep else ("", "")
    check("a lightmap row is labelled by the material and the map stem, not the objects",
          mat_label == "ROOM_ENV" and node_label == "ROOM_ENV_LightMap",
          f"{mat_label!r} / {node_label!r}")

    slot._lightmap_rows = {row_path: dep}
    entry = SimpleNamespace(values={"material": mat_label, "path": row_path, "image": ""})
    slot.ui = SimpleNamespace(tbl000=SimpleNamespace(get_selection=lambda **kw: [entry]))
    check("a selected lightmap row is a scope of its own", slot._get_scope_lightmaps() == [dep])
    images, label = slot._get_scope_images()
    check("...that the image commands see as nothing to do", images == [] and "lightmap" in label, label)
    check("...and that Browse/Select resolve to the record",
          slot._lightmaps_from_selection([entry]) == [dep] and slot._images_from_selection([entry]) == [])
    slot.ui = SimpleNamespace(tbl000=SimpleNamespace(get_selection=lambda **kw: []))
    check("no selection scopes to every lightmap row shown", slot._get_scope_lightmaps() == [dep])

    fields = {f["name"]: f for f in slot._find_and_copy_fields([], [], tmp, lightmaps=[dep])}
    check("Find & Copy has no lightmap opt-out row -- the scope decides",
          "include_lightmaps" not in fields, f"{sorted(fields)}")
    os.remove(lm_map)
    missing_dep = slot._lightmap_records().lightmap_dependencies(search_dirs=[], walk=False)[0]
    fields = {f["name"]: f for f in slot._find_and_copy_fields([], [], tmp, lightmaps=[missing_dep])}
    check("a missing lightmap switches the search folder on and is named in its hint",
          fields["source_dir"]["enabled"] and "ROOM_ENV_LightMap.exr" in fields["source_dir"]["hint"],
          f"{fields['source_dir']}")
    slot._find_copy_lightmaps = [missing_dep]
    check("the accept button counts the lightmaps",
          slot._find_and_copy_ok_text({"mode": "Copy"}) == "Copy 0 texture(s) + 1 lightmap(s)"
          or slot._find_and_copy_ok_text({"mode": "Copy"}) == "Copy 1 lightmap(s)",
          slot._find_and_copy_ok_text({"mode": "Copy"}))
    LightmapBaker().revert()

    # 14. Renaming a texture file, and Keep Names In Sync (mirror of mayatk's
    # TestRenameTextures / TestRenameFromTheEditor, 2026-10-04). A texture is
    # referenced by NAME, so a file renamed in Explorer left every image reading
    # it pointing at nothing: MatUtils.rename_texture_file renames and repoints
    # together, and sync_material_names names a material, its texture set's
    # files, their images and the set's lightmap for one base. Each case runs
    # in a fresh file with its own folder pinned as the project (sync renames
    # only the project's own files). Added: 2026-10-05
    from unittest import mock as _mock

    from blendertk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
    from blendertk.mat_utils._mat_utils import MatUtils

    def _section(name, body):
        """Run one case; an error is one failed check, not the end of the run."""
        try:
            body()
        except Exception as error:  # noqa: BLE001
            traceback.print_exc()
            check(f"{name} raised", False, repr(error))

    def _listing(folder):
        return sorted(
            f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))
        )

    def _write(folder, name, data=b"DATA"):
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _image(name, folder, file_name):
        img = bpy.data.images.new(name, 4, 4)
        img.source = "FILE"
        img.filepath_raw = os.path.join(folder, file_name)
        return img

    def _case(label, project=True):
        """A fresh file: rock_MAT reading rock_Base_Color.<UDIM>.png (two tiles,
        image named after its stem) and rock_Normal.png (image named after its
        file, as Blender names a loaded image), beside an unrelated taken.png."""
        bpy.ops.wm.read_factory_settings(use_empty=True)
        folder = os.path.join(tmp, "rename", label)
        os.makedirs(folder, exist_ok=True)
        for name in (
            "rock_Base_Color.1001.png",
            "rock_Base_Color.1002.png",
            "rock_Normal.png",
            "taken.png",
        ):
            _write(folder, name)
        btk.EnvUtils.set_current_workspace(folder if project else None)
        color = _image("rock_Base_Color", folder, "rock_Base_Color.<UDIM>.png")
        normal = _image("rock_Normal.png", folder, "rock_Normal.png")
        mat = btk.create_mat("standard", name="rock_MAT")
        for img in (color, normal):
            mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
        return folder, mat

    def _path(name):
        return _abspath(bpy.data.images[name])

    def _same(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
            os.path.normpath(b)
        )

    def _rename_repoints_every_reader():
        folder, _mat = _case("readers")
        twin = _image("twin", folder, "rock_Normal.png")  # another reader
        result = MatUtils.rename_texture_file(
            os.path.join(folder, "rock_Normal.png"), "stone_Normal.png"
        )
        check(
            "rename_texture_file renames the file on disk",
            "stone_Normal.png" in _listing(folder)
            and "rock_Normal.png" not in _listing(folder),
            f"{_listing(folder)}",
        )
        check(
            "...and repoints every image reading it",
            sorted(result["images"]) == sorted(["rock_Normal.png", twin.name])
            and all(
                _same(_path(n), os.path.join(folder, "stone_Normal.png"))
                for n in ("rock_Normal.png", "twin")
            ),
            f"{result}",
        )
        check(
            "...returning the files it renamed",
            [os.path.basename(new) for _old, new in result["renamed"]]
            == ["stone_Normal.png"],
            f"{result['renamed']}",
        )
        color_path = _path("rock_Base_Color")
        result = MatUtils.rename_texture_file(
            os.path.join(folder, "stone_Normal.png"),
            "slate_Normal.png",
            images=["rock_Normal.png", "rock_Base_Color"],
        )
        check(
            "given images, only those reading the file are repointed",
            result["images"] == ["rock_Normal.png"]
            and _path("rock_Base_Color") == color_path
            and _same(_path("twin"), os.path.join(folder, "stone_Normal.png")),
            f"{result['images']} {_path('rock_Base_Color')}",
        )

    def _rename_keeps_the_spelling():
        folder, _mat = _case("spelling")
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "scene.blend"))
        relative = bpy.data.images["rock_Normal.png"]
        relative.filepath_raw = "//rock_Normal.png"
        windows = _image("windows", folder, "rock_Normal.png")
        windows.filepath_raw = os.path.join(folder, "rock_Normal.png").replace("/", "\\")
        MatUtils.rename_texture_file(relative.filepath, "stone_Normal.png")
        check(
            "a // path stays //-relative, with the new name",
            relative.filepath == "//stone_Normal.png",
            relative.filepath,
        )
        check(
            "a backslash path keeps its folder and its separators",
            windows.filepath
            == os.path.join(folder, "stone_Normal.png").replace("/", "\\"),
            windows.filepath,
        )

    def _rename_a_tile_set():
        folder, _mat = _case("tiles")
        MatUtils.rename_texture_file(
            bpy.data.images["rock_Base_Color"].filepath, "stone_Base_Color.<UDIM>.png"
        )
        check(
            "a tile set renames every tile",
            {"stone_Base_Color.1001.png", "stone_Base_Color.1002.png"}
            <= set(_listing(folder)),
            f"{_listing(folder)}",
        )
        check(
            "...and its image keeps the token",
            bpy.data.images["rock_Base_Color"].filepath.endswith(
                "stone_Base_Color.<UDIM>.png"
            ),
            bpy.data.images["rock_Base_Color"].filepath,
        )

    def _rename_refuses_a_collision():
        folder, _mat = _case("collision")
        try:
            MatUtils.rename_texture_file(
                os.path.join(folder, "rock_Normal.png"), "taken.png"
            )
            raised = False
        except FileExistsError:
            raised = True
        check(
            "a rename onto another file raises and changes nothing",
            raised
            and "rock_Normal.png" in _listing(folder)
            and _same(_path("rock_Normal.png"), os.path.join(folder, "rock_Normal.png")),
            f"raised={raised} {_listing(folder)}",
        )

    def _rename_follows_undo():
        """Blender's undo restores the datablocks, never the disk: undoing a
        rename left the image naming a file that was gone."""
        folder, _mat = _case("undo")
        bpy.ops.ed.undo_push(message="before the rename")
        MatUtils.rename_texture_file(
            os.path.join(folder, "rock_Normal.png"), "stone_Normal.png"
        )
        bpy.ops.ed.undo()
        check(
            "undo puts the file back with the path that names it",
            "rock_Normal.png" in _listing(folder)
            and "stone_Normal.png" not in _listing(folder)
            and _same(_path("rock_Normal.png"), os.path.join(folder, "rock_Normal.png")),
            f"{_listing(folder)} {_path('rock_Normal.png')}",
        )
        bpy.ops.ed.redo()
        check(
            "...and redo renames it again",
            "stone_Normal.png" in _listing(folder)
            and "rock_Normal.png" not in _listing(folder)
            and _same(_path("rock_Normal.png"), os.path.join(folder, "stone_Normal.png")),
            f"{_listing(folder)} {_path('rock_Normal.png')}",
        )

    def _undone_rename_stays_undone():
        """Every undo / redo re-applied EVERY recorded rename to the side the
        serial named, whether or not that step was a rename: a rename undone
        and its redo branch discarded by a new edit stayed live, so a later
        Ctrl+Z renamed a fresh file that only shares the name (review,
        2026-10-05). A rename's files move only when its side changes."""
        folder, _mat = _case("undo_stale")
        bpy.ops.ed.undo_push(message="before the rename")
        MatUtils.rename_texture_file(
            os.path.join(folder, "rock_Normal.png"), "stone_Normal.png"
        )
        bpy.ops.ed.undo()  # the rename undone: rock_Normal.png is back
        bpy.data.objects.new("edit_a", None)
        bpy.ops.ed.undo_push(message="a new edit")  # the redo branch is gone
        os.remove(os.path.join(folder, "rock_Normal.png"))  # out of band...
        _write(folder, "stone_Normal.png", b"FRESH")  # ...and a fresh export
        bpy.data.objects.new("edit_b", None)
        bpy.ops.ed.undo_push(message="another edit")
        bpy.ops.ed.undo()
        fresh_path = os.path.join(folder, "stone_Normal.png")
        fresh = False
        if os.path.isfile(fresh_path):
            with open(fresh_path, "rb") as fh:
                fresh = fh.read() == b"FRESH"
        check(
            "a later undo leaves a fresh file that shares an undone rename's name",
            fresh and "rock_Normal.png" not in _listing(folder),
            f"{_listing(folder)}",
        )

    def _sync_names_one_base():
        folder, mat = _case("sync")
        result = MatUtils.sync_material_names(
            mat, "stone", material_affix=("", "_MAT")
        )
        check(
            "sync names the material for the base",
            result["material"] == ("rock_MAT", "stone_MAT")
            and "stone_MAT" in bpy.data.materials,
            f"{result['material']}",
        )
        check(
            "...renames its texture set on disk, every tile",
            _listing(folder)
            == [
                "stone_Base_Color.1001.png",
                "stone_Base_Color.1002.png",
                "stone_Normal.png",
                "taken.png",
            ],
            f"{_listing(folder)}",
        )
        check(
            "...and names each image after its texture, in the image's own style",
            "stone_Normal.png" in bpy.data.images
            and "stone_Base_Color" in bpy.data.images
            and bpy.data.images["stone_Normal.png"].filepath.endswith(
                "stone_Normal.png"
            ),
            f"{[i.name for i in bpy.data.images]}",
        )

    def _sync_image_affix():
        _folder, mat = _case("sync_affix")
        MatUtils.sync_material_names(mat, "stone", image_affix=("", "_img"))
        check(
            "an image suffix names every image after its texture's stem",
            {"stone_Normal_img", "stone_Base_Color_img"}
            <= {i.name for i in bpy.data.images},
            f"{[i.name for i in bpy.data.images]}",
        )

    def _sync_rolls_back_a_failure_part_way():
        """A rename failing part-way through a sync (a file held open by
        another app: WinError 32) left the files renamed before it on their
        new names while the images, material and lightmap kept the old (review,
        2026-10-05). The done renames are put back before the error is raised,
        and the message says so."""
        from blendertk.mat_utils._mat_utils import _FileRenameUndo

        folder, mat = _case("sync_part_way")
        before = _listing(folder)
        paths = {img.name: _path(img.name) for img in bpy.data.images}
        scene = bpy.context.scene
        serial = scene.get(_FileRenameUndo.SERIAL_PROP)
        recorded = list(_FileRenameUndo._renames)
        real_rename = ptk.TiledPath.rename
        calls = []

        def locked(path, new_name, dry_run=False):
            if not dry_run:
                calls.append(new_name)
                if len(calls) == 2:  # the second file is held open
                    raise PermissionError(13, "in use by another process", path)
            return real_rename(path, new_name, dry_run=dry_run)

        message = ""
        with _mock.patch.object(ptk.TiledPath, "rename", side_effect=locked):
            try:
                MatUtils.sync_material_names(mat, "stone", material_affix=("", "_MAT"))
            except (ValueError, OSError) as error:
                message = str(error)
        check(
            "a sync failing part-way puts back the files it renamed",
            _listing(folder) == before
            and all(_same(_path(name), path) for name, path in paths.items())
            and "rock_MAT" in bpy.data.materials,
            f"{_listing(folder)}",
        )
        check("...and says so", "put back" in message, message)
        # Its renames and put-backs stayed on record, the scene's serial past
        # them, so a later undo replayed both on disk (and the redo branch the
        # first one dropped was gone while Blender kept it). Added: 2026-10-10
        check(
            "...and leaves no rename on record for an undo to replay",
            scene.get(_FileRenameUndo.SERIAL_PROP) == serial
            and _FileRenameUndo._renames == recorded,
            f"serial {scene.get(_FileRenameUndo.SERIAL_PROP)} (was {serial}); "
            f"{len(_FileRenameUndo._renames)} record(s) (were {len(recorded)})",
        )

    def _sync_refuses_a_collision():
        folder, mat = _case("sync_collision")
        _write(folder, "stone_Normal.png", b"OTHER")
        try:
            MatUtils.sync_material_names(mat, "stone", material_affix=("", "_MAT"))
            raised = False
        except ValueError:
            raised = True
        check(
            "sync refuses the lot when one rename would collide",
            raised
            and "rock_MAT" in bpy.data.materials
            and "rock_Base_Color.1001.png" in _listing(folder),
            f"raised={raised} {_listing(folder)}",
        )

    def _sync_dry_run():
        folder, mat = _case("sync_dry")
        plan = MatUtils.sync_material_names(mat, "stone", dry_run=True)
        check(
            "a dry run plans and changes nothing",
            plan["material"] == ("rock_MAT", "stone")
            and len(plan["textures"]) == 2
            and "rock_MAT" in bpy.data.materials
            and "rock_Normal.png" in _listing(folder),
            f"{plan}",
        )

    def _sync_own_set_only():
        """An environment cube beside the set is another set's map."""
        folder, mat = _case("sync_foreign")
        cube = _image("env_cube.dds", folder, "env_cube.dds")
        _write(folder, "env_cube.dds", b"CUBE")
        mat.node_tree.nodes.new("ShaderNodeTexImage").image = cube
        result = MatUtils.sync_material_names(
            mat, "stone", material_affix=("", "_MAT")
        )
        check(
            "sync follows the material's own texture set only",
            "env_cube.dds" in _listing(folder)
            and "stone_Normal.png" in _listing(folder)
            and "env_cube.dds" in bpy.data.images,
            f"{_listing(folder)}",
        )
        check(
            "...and reports the other set's map once",
            len([r for r in result["skipped"] if "env_cube.dds" in r]) == 1,
            f"{result['skipped']}",
        )

    def _sync_keeps_files_outside_the_project():
        folder, mat = _case("sync_outside")
        outside = os.path.join(tmp, "rename", "sync_outside_library")
        os.makedirs(outside, exist_ok=True)
        _write(outside, "rock_Roughness.png")
        rough = _image("rough_img", outside, "rock_Roughness.png")
        mat.node_tree.nodes.new("ShaderNodeTexImage").image = rough
        result = MatUtils.sync_material_names(
            mat, "stone", material_affix=("", "_MAT")
        )
        check(
            "sync leaves a file outside the project its name",
            _listing(outside) == ["rock_Roughness.png"],
            f"{_listing(outside)}",
        )
        check(
            "...names its image after the file it still reads",
            dict(result["images"]).get("rough_img") == "rock_Roughness"
            and "rock_Roughness" in bpy.data.images,
            f"{result['images']}",
        )
        check(
            "...reports it, and renames the project's own",
            any("rock_Roughness" in r for r in result["skipped"])
            and "stone_Normal.png" in _listing(folder),
            f"{result['skipped']}",
        )

    def _baked(folder):
        """Two sets' lightmaps bound by their markers: this set's
        ``rock_Lightmap_1.exr`` and another set's ``rockery_Lightmap.exr``."""
        maps = {"rock_GEO": "rock_Lightmap_1.exr", "rockery_GEO": "rockery_Lightmap.exr"}
        for name, map_name in maps.items():
            _write(folder, map_name, b"EXR")
            obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
            bpy.context.scene.collection.objects.link(obj)
            LightmapRecords._write_marker(
                obj,
                {"map": map_name, "uv_set": "UVMap", "intensity": 1.0, "mode": "separated"},
            )
        LightmapRecords._save_folder_hints({n.lower(): folder for n in maps.values()})

    def _sync_takes_the_lightmap():
        folder, mat = _case("sync_lightmap")
        _baked(folder)
        before = _listing(folder)
        bpy.ops.ed.undo_push(message="before the sync")
        result = MatUtils.sync_material_names(
            mat, "stone", material_affix=("", "_MAT"), lightmaps=LightmapRecords
        )
        check(
            "sync takes the set's lightmap along",
            [(old, new) for _p, old, new in result["lightmaps"]]
            == [("rock_Lightmap_1.exr", "stone_Lightmap_1.exr")]
            and "stone_Lightmap_1.exr" in _listing(folder),
            f"{result['lightmaps']} {_listing(folder)}",
        )
        check(
            "...re-stamping the markers that bind it, and no other set's",
            LightmapRecords.lightmap_info("rock_GEO").get("map") == "stone_Lightmap_1.exr"
            and "rockery_Lightmap.exr" in _listing(folder),
            f"{LightmapRecords.lightmap_info('rock_GEO')}",
        )
        bpy.ops.ed.undo()
        check(
            "one undo puts the whole sync back: files, marker and material",
            _listing(folder) == before
            and LightmapRecords._marker_info("rock_GEO").get("map")
            == "rock_Lightmap_1.exr"
            and "rock_MAT" in bpy.data.materials,
            f"{_listing(folder)} {LightmapRecords._marker_info('rock_GEO')}",
        )

    def _sync_without_records():
        folder, mat = _case("sync_no_records")
        _baked(folder)
        result = MatUtils.sync_material_names(mat, "stone")
        check(
            "without the lightmap records the lightmap stays",
            result["lightmaps"] == []
            and "rock_Lightmap_1.exr" in _listing(folder)
            and LightmapRecords.lightmap_info("rock_GEO").get("map")
            == "rock_Lightmap_1.exr",
            f"{result['lightmaps']}",
        )

    def _rename_lightmap_restamps():
        folder, _mat = _case("rename_lightmap")
        _baked(folder)
        os.rename(
            os.path.join(folder, "rock_Lightmap_1.exr"),
            os.path.join(folder, "pebble_Lightmap_1.exr"),
        )
        count = LightmapRecords.rename_lightmap(
            "rock_Lightmap_1.exr", "pebble_Lightmap_1.exr"
        )
        deps = {d["map"]: d for d in LightmapRecords.lightmap_dependencies(walk=False)}
        check(
            "rename_lightmap re-stamps every marker naming the map",
            count == 1 and "rock_Lightmap_1.exr" not in deps,
            f"{count} {sorted(deps)}",
        )
        check(
            "...and its folder record: the renamed file resolves",
            bool(deps.get("pebble_Lightmap_1.exr", {}).get("path")),
            f"{deps.get('pebble_Lightmap_1.exr')}",
        )
        record = LightmapRecords._record()
        check(
            "...and the manifest the deliverable ships names it",
            record is not None
            and "pebble_Lightmap_1.exr" in record.text
            and "rock_Lightmap_1.exr" not in record.text,
            record.text if record is not None else "no record",
        )
        check(
            "an unknown map re-stamps nothing",
            LightmapRecords.rename_lightmap("nothing.exr", "x.exr") == 0,
        )

    # -- the panel, driven through its own handlers (Qt stubbed) ---------------
    class _Cell:
        """A table cell: its text, and the name it held (UserRole)."""

        def __init__(self, text, role=None):
            self.value, self.role = text, role

        def text(self):
            return self.value

        def setText(self, value):
            self.value = value

        def data(self, _role):
            return self.role

        def setData(self, _role, value):
            self.role = value

    def _panel(sync=None, material_affix=("", "_MAT"), image_suffix=""):
        """A TexturePathEditorSlots with no window: the header menu holds the
        Keep Names In Sync toggle (``sync`` True / False) and its option box."""
        slot = TexturePathEditorSlots.__new__(TexturePathEditorSlots)
        said = []
        slot.sb = SimpleNamespace(
            QtCore=SimpleNamespace(
                Qt=SimpleNamespace(UserRole=256),
                QTimer=SimpleNamespace(singleShot=lambda *_a: None),
            ),
            message_box=lambda text, *buttons, **kw: (said.append(text) or "Yes"),
        )
        menu = SimpleNamespace()
        if sync is not None:
            flyout = SimpleNamespace(
                txt_shader_affix=SimpleNamespace(
                    option_box=SimpleNamespace(
                        resolve_affix=lambda default: material_affix
                    )
                ),
                txt_file_node_suffix=SimpleNamespace(text=lambda: image_suffix),
            )
            menu.chk_sync_names = SimpleNamespace(
                isChecked=lambda: sync, option_box=SimpleNamespace(menu=flyout)
            )
        slot.ui = SimpleNamespace(
            header=SimpleNamespace(menu=menu),
            tbl000=SimpleNamespace(init_slot=lambda: None),
        )
        slot._lightmap_rows = {}
        slot._previous_paths = {}
        slot._footer_controller = None
        slot._image_to_mats = btk.get_image_material_map()
        return slot, said

    def _edit_cell(slot, image_name, col, text, material="rock_MAT"):
        """Type *text* into an image row's cell *col* and commit it, as the
        table does (``handle_cell_edit``): name cells keep the old name in
        UserRole, the path cell none."""
        img = bpy.data.images[image_name]
        cells = {
            0: _Cell(material, material),
            1: _Cell(img.filepath),
            2: _Cell(img.name, img.name),
        }
        cells[col].setText(text)
        slot.ui.tbl000 = SimpleNamespace(
            item=lambda _row, column: cells.get(column),
            blockSignals=lambda _on: False,
            apply_formatting=lambda: None,
            init_slot=lambda: None,
        )
        slot.handle_cell_edit(0, col)
        return cells

    def _context(image_name):
        return {"image": image_name, "material": "rock_MAT", "materials": ["rock_MAT"]}

    def _panel_is_file_rename():
        folder, _mat = _case("panel_is_rename")
        slot, _said = _panel()
        stored = os.path.join(folder, "rock_Normal.png")
        check(
            "a path edit to a free name in the same folder is a rename",
            slot._is_file_rename(stored, os.path.join(folder, "new.png")),
        )
        check(
            "...a path to an existing file is a repoint",
            not slot._is_file_rename(stored, os.path.join(folder, "taken.png")),
        )
        check(
            "...and so is a path into another folder",
            not slot._is_file_rename(stored, os.path.join(folder, "sub", "new.png")),
        )

    def _panel_path_edit_outside_the_project():
        """Outside the project a name-only path edit repoints: another project
        may read that file, so it keeps its name."""
        folder, _mat = _case("panel_outside", project=False)
        elsewhere = os.path.join(tmp, "rename", "panel_outside_project")
        os.makedirs(elsewhere, exist_ok=True)
        btk.EnvUtils.set_current_workspace(elsewhere)
        slot, _said = _panel()
        typed = os.path.join(folder, "stone_Normal.png")
        _edit_cell(slot, "rock_Normal.png", 1, typed)
        check(
            "a path edit outside the project renames nothing on disk",
            "rock_Normal.png" in _listing(folder)
            and "stone_Normal.png" not in _listing(folder),
            f"{_listing(folder)}",
        )
        check(
            "...and repoints the image",
            _same(_path("rock_Normal.png"), typed),
            _path("rock_Normal.png"),
        )

    def _panel_path_edit_renames():
        folder, _mat = _case("panel_path_rename")
        slot, _said = _panel()
        _edit_cell(slot, "rock_Normal.png", 1, os.path.join(folder, "stone_Normal.png"))
        check(
            "a name-only path edit inside the project renames the file",
            "stone_Normal.png" in _listing(folder)
            and "rock_Normal.png" not in _listing(folder)
            and _same(_path("rock_Normal.png"), os.path.join(folder, "stone_Normal.png")),
            f"{_listing(folder)}",
        )

    def _panel_rename_file():
        folder, _mat = _case("panel_rename")
        slot, _said = _panel(sync=False)
        ok = slot._rename_texture(_context("rock_Normal.png"), "stone_Normal.png")
        check(
            "Rename File renames on disk and repoints",
            ok
            and "stone_Normal.png" in _listing(folder)
            and _same(_path("rock_Normal.png"), os.path.join(folder, "stone_Normal.png")),
            f"{ok} {_listing(folder)}",
        )
        check("...with sync off the material stays", "rock_MAT" in bpy.data.materials)
        ok = slot._rename_texture(_context("rock_Normal.png"), "taken.png")
        check(
            "a refused name changes nothing and says so",
            not ok
            and "stone_Normal.png" in _listing(folder)
            and any("taken.png" in text for text in _said),
            f"{ok} {_said[-1:] if _said else ''}",
        )

    def _panel_rename_file_opens_the_cell():
        """Rename File edits the path cell on just the file name."""
        _folder, _mat = _case("panel_edit_as")
        slot, _said = _panel()
        opened = []
        slot.ui.tbl000 = SimpleNamespace(
            edit_cell_as=lambda row, col, text, commit: opened.append((row, col, text))
        )
        entry = SimpleNamespace(
            row=3, values={"material": "rock_MAT", "path": "", "image": "rock_Normal.png"}
        )
        slot.row_rename_file([entry])
        check(
            "Rename File opens the path cell on the file name",
            opened == [(3, 1, "rock_Normal.png")],
            f"{opened}",
        )

    def _panel_browse_for_folder():
        """Browse warned and did nothing with several rows."""
        folder, _mat = _case("panel_folder")
        moved = os.path.join(folder, "moved")
        os.makedirs(moved)
        _write(moved, "rock_Normal.png")
        slot, _said = _panel()
        slot.sb.dir_dialog = lambda **kwargs: moved
        slot._browse_for_folder(
            [bpy.data.images["rock_Normal.png"], bpy.data.images["rock_Base_Color"]], []
        )
        check(
            "Browse on several rows repoints each to its same-named file there",
            _same(_path("rock_Normal.png"), os.path.join(moved, "rock_Normal.png")),
            _path("rock_Normal.png"),
        )
        check(
            "...and a texture the folder does not hold keeps its path",
            _same(
                _path("rock_Base_Color"),
                os.path.join(folder, "rock_Base_Color.<UDIM>.png"),
            ),
            _path("rock_Base_Color"),
        )

    def _panel_delete_lightmap_row():
        """Remove Texture returned silently on a lightmap row; it reverts the
        objects the map binds, confirmed, and the map stays on disk."""
        folder, _mat = _case("panel_delete")
        _baked(folder)
        dep = next(
            d
            for d in LightmapRecords.lightmap_dependencies(walk=False)
            if d["map"] == "rock_Lightmap_1.exr"
        )
        slot, said = _panel()
        slot._lightmap_rows = {"x/rock_Lightmap_1.exr": dep}
        slot.delete_file_node([{"path": "x/rock_Lightmap_1.exr"}])
        check(
            "delete on a lightmap row asks once, naming the map",
            len([t for t in said if "rock_Lightmap_1.exr" in t]) >= 1
            and "Revert to Source" in said[0],
            f"{said}",
        )
        check(
            "...and clears the marker, leaving the map on disk",
            not LightmapRecords.lightmap_info("rock_GEO")
            and "rock_Lightmap_1.exr" in _listing(folder),
            f"{LightmapRecords.lightmap_info('rock_GEO')}",
        )

    def _panel_sync_whole_material():
        folder, _mat = _case("panel_sync")
        slot, _said = _panel(sync=True)
        slot._rename_texture(_context("rock_Normal.png"), "stone_Normal.png")
        check(
            "with sync on a new base renames the whole material",
            "stone_MAT" in bpy.data.materials
            and _listing(folder)
            == [
                "stone_Base_Color.1001.png",
                "stone_Base_Color.1002.png",
                "stone_Normal.png",
                "taken.png",
            ],
            f"{_listing(folder)}",
        )

    def _panel_sync_new_map_type():
        """The base is unchanged by a new map type: the file is renamed alone."""
        folder, _mat = _case("panel_sync_map_type")
        slot, said = _panel(sync=True)
        ok = slot._rename_texture(_context("rock_Normal.png"), "rock_Bump.png")
        check(
            "with sync on a new map type renames that file alone",
            ok
            and "rock_Bump.png" in _listing(folder)
            and "rock_MAT" in bpy.data.materials
            and "rock_Base_Color.1001.png" in _listing(folder),
            f"{ok} {_listing(folder)}",
        )
        check("...and says so", any("renamed alone" in t for t in said), f"{said}")

    def _panel_sync_other_set():
        folder, mat = _case("panel_sync_other")
        cube = _image("env_cube.dds", folder, "env_cube.dds")
        _write(folder, "env_cube.dds", b"CUBE")
        mat.node_tree.nodes.new("ShaderNodeTexImage").image = cube
        slot, _said = _panel(sync=True)
        slot._rename_texture(_context("env_cube.dds"), "studio_env.dds")
        check(
            "with sync on another set's map is renamed alone",
            "studio_env.dds" in _listing(folder)
            and "rock_MAT" in bpy.data.materials
            and "rock_Normal.png" in _listing(folder),
            f"{_listing(folder)}",
        )

    def _panel_sync_image_cells():
        folder, mat = _case("panel_sync_cells")
        cube = _image("env_cube.dds", folder, "env_cube.dds")
        _write(folder, "env_cube.dds", b"CUBE")
        mat.node_tree.nodes.new("ShaderNodeTexImage").image = cube
        slot, _said = _panel(sync=True)
        slot._image_to_mats = btk.get_image_material_map()
        _edit_cell(slot, "env_cube.dds", 2, "studio_env")
        check(
            "another set's image renamed in its cell is renamed alone",
            "studio_env" in bpy.data.images
            and "rock_MAT" in bpy.data.materials
            and "env_cube.dds" in _listing(folder),
            f"{[i.name for i in bpy.data.images]}",
        )
        _edit_cell(slot, "rock_Normal.png", 2, "stone_Normal.png")
        check(
            "an image of the set given a new base renames the whole material",
            "stone_MAT" in bpy.data.materials
            and "stone_Normal.png" in _listing(folder)
            and "stone_Base_Color.1001.png" in _listing(folder),
            f"{_listing(folder)}",
        )

    def _panel_material_cell():
        folder, _mat = _case("panel_material")
        slot, _said = _panel(sync=False)
        _edit_cell(slot, "rock_Normal.png", 0, "boulder_MAT")
        check(
            "with sync off the material cell renames the material alone",
            "boulder_MAT" in bpy.data.materials
            and "rock_Normal.png" in _listing(folder),
            f"{[m.name for m in bpy.data.materials]}",
        )
        slot, _said = _panel(sync=True)
        _edit_cell(slot, "rock_Normal.png", 0, "stone_MAT", material="boulder_MAT")
        check(
            "with sync on it renames the material's set to the new base",
            "stone_MAT" in bpy.data.materials
            and "stone_Normal.png" in _listing(folder),
            f"{_listing(folder)}",
        )

    def _panel_sync_readers():
        slot, _said = _panel()
        check("an unbuilt menu reads Keep Names In Sync off", not slot._sync_names_enabled())
        shader, image = slot._sync_affixes()
        check(
            "...with the convention's material affix and no image suffix",
            shader == ptk.NamingConvention.affix_parts("material") and image == ("", ""),
            f"{shader} {image}",
        )
        slot, _said = _panel(sync=True, material_affix=("M_", ""), image_suffix="img")
        check(
            "the option box gives the affixes",
            slot._sync_names_enabled()
            and slot._sync_affixes() == (("M_", ""), ("", "_img")),
            f"{slot._sync_affixes()}",
        )

    def _panel_truncate_length():
        slot, _said = _panel()
        calls = []
        table = SimpleNamespace(set_column_truncation=lambda col, **kw: calls.append(kw))
        slot.ui.header.menu.chk_truncate_paths = SimpleNamespace(
            isChecked=lambda: True,
            option_box=SimpleNamespace(
                menu=SimpleNamespace(spn_truncate_length=SimpleNamespace(value=lambda: 140))
            ),
        )
        slot._apply_path_truncation(table)
        check(
            "the Truncate Texture Paths option box sets the length",
            calls and calls[0]["length"] == 140,
            f"{calls}",
        )
        check(
            "...96 by default, the File Node column hidden",
            TexturePathEditorSlots._PATH_TRUNCATE_LENGTH == 96,
        )

    def _panel_undo_refresh():
        """An undone rename left the rows naming what was gone: the table
        followed file loads, never Ctrl+Z. It refreshes on Undo / Redo now --
        only while shown, since every undo in the file fires them."""
        slot, _said = _panel()
        queued, refreshed = [], []
        slot.sb.QtCore.QTimer = SimpleNamespace(
            singleShot=lambda _ms, fn: queued.append(fn)
        )
        slot._refresh_pending = False
        slot._refresh_table_content = refreshed.append
        hidden = SimpleNamespace(isVisible=lambda: False)
        shown = SimpleNamespace(isVisible=lambda: True)
        for widget, visible_only in ((hidden, True), (shown, True), (hidden, False)):
            slot._on_scene_change(widget, visible_only=visible_only)
            while queued:
                queued.pop(0)()
        check(
            "an undo refreshes a shown table only; a load refreshes regardless",
            refreshed == [shown, hidden],
            f"{refreshed}",
        )
        refreshed.clear()
        slot._on_scene_change(hidden, visible_only=True)
        slot._on_scene_change(hidden)
        check("one refresh per idle", len(queued) == 1, f"{queued}")
        while queued:
            queued.pop(0)()
        check(
            "...and a waiting undo does not swallow a load",
            refreshed == [hidden],
            f"{refreshed}",
        )
        events = []
        manager = SimpleNamespace(
            subscribe=lambda event, _cb, owner=None: events.append(event),
            connect_cleanup=lambda *_a, **_kw: None,
        )
        with _mock.patch.object(btk.ScriptJobManager, "instance", return_value=manager):
            slot._setup_scene_change_callback(shown)
        check(
            "Undo and Redo are subscribed",
            "Undo" in events and "Redo" in events,
            f"{events}",
        )

    def _panel_fact_columns():
        folder, _mat = _case("panel_facts")
        png = write_png(os.path.join(folder, "facts_DIFF.png"))
        tip = TexturePathEditorSlots._format_fact
        size = ptk.ImgUtils.texture_facts(png)
        check(
            "File Size reads the bytes, sorted by the number",
            tip("bytes", size)[2] == os.path.getsize(png),
            f"{tip('bytes', size)}",
        )
        check(
            "Dimensions reads the header",
            tip("dimensions", size)[0] == "4 x 4",
            f"{tip('dimensions', size)}",
        )
        check("no file reads as empty", tip("mode", {})[0] == "", f"{tip('mode', {})}")
        slot, _said = _panel()
        filled = {}
        hidden = {2, 4, 5}
        row_cells = {1: _Cell(png), 2: _Cell("facts_DIFF.png", "facts_DIFF.png")}
        widget = SimpleNamespace(
            horizontalHeader=lambda: SimpleNamespace(
                isSectionHidden=lambda column: column in hidden
            ),
            columnCount=lambda: 6,
            rowCount=lambda: 1,
            item=lambda _row, column: row_cells.get(column),
            isSortingEnabled=lambda: False,
            setSortingEnabled=lambda _on: None,
            blockSignals=lambda _on: False,
            set_sorted_cell=lambda row, column, text, key: (
                filled.__setitem__(column, text)
                or SimpleNamespace(setToolTip=lambda _tip: None)
            ),
        )
        slot._fill_info_columns(widget)
        check(
            "only the fact columns that show are read",
            sorted(filled) == [3] and filled[3].endswith("bytes"),
            f"{filled}",
        )

    def _panel_fits_the_row_menu():
        slot, _said = _panel()
        buttons = {
            name: SimpleNamespace(
                text="", enabled=True, setText=None, setEnabled=None, name=name
            )
            for name in ("delete_file_node", "row_browse_for_file", "row_rename_file")
        }
        for button in buttons.values():
            button.setText = lambda text, b=button: setattr(b, "text", text)
            button.setEnabled = lambda on, b=button: setattr(b, "enabled", on)
        slot.sb.QtWidgets = SimpleNamespace(QPushButton=object)
        lightmap = SimpleNamespace(values={"material": "", "path": "x/a.exr", "image": ""})
        image = SimpleNamespace(values={"material": "m", "path": "y/b.png", "image": "b"})
        slot._lightmap_rows = {"x/a.exr": {"map": "a.exr"}}
        for label, rows, browse, rename, delete in (
            ("an image row", [image], "Browse for File...", True, "Remove Texture"),
            (
                "an image and a lightmap row",
                [image, lightmap],
                "Browse for Folder...",
                False,
                "Remove Texture",
            ),
            ("a lightmap row", [lightmap], "Browse for File...", True, "Revert to Source..."),
        ):
            widget = SimpleNamespace(
                get_selection=lambda columns=None, include_current=True, r=rows: r,
                menu=SimpleNamespace(findChild=lambda _type, name: buttons.get(name)),
            )
            slot._fit_row_menu(widget)
            check(
                f"the row menu fits {label}",
                buttons["row_browse_for_file"].text == browse
                and buttons["row_rename_file"].enabled is rename
                and buttons["delete_file_node"].text == delete,
                f"{[(b.name, b.text, b.enabled) for b in buttons.values()]}",
            )

    def _linked_refuses():
        """What a linked library brings is its library's: a rename here would leave
        it reading nothing (its path and marker are the library's to change)."""
        bpy.ops.wm.read_factory_settings(use_empty=True)
        lib_dir = os.path.join(tmp, "rename", "linked_library")
        os.makedirs(lib_dir, exist_ok=True)
        _write(lib_dir, "crate_Normal.png")
        _write(lib_dir, "crate_Lightmap.exr", b"EXR")
        img = _image("crate_Normal.png", lib_dir, "crate_Normal.png")
        mat = btk.create_mat("standard", name="crate_MAT")
        mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
        mat.use_fake_user = True
        obj = bpy.data.objects.new("crate_GEO", bpy.data.meshes.new("crate_GEO"))
        bpy.context.scene.collection.objects.link(obj)
        LightmapRecords._write_marker(obj, {"map": "crate_Lightmap.exr"})
        lib_file = os.path.join(lib_dir, "library.blend")
        bpy.ops.wm.save_as_mainfile(filepath=lib_file)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        btk.EnvUtils.set_current_workspace(lib_dir)  # its files are this project's
        with bpy.data.libraries.load(lib_file, link=True) as (_src, dst):
            dst.materials = ["crate_MAT"]
            dst.objects = ["crate_GEO"]
        linked = bpy.data.materials.get("crate_MAT")
        before = _listing(lib_dir)
        refused = []
        for name, call in (
            ("sync", lambda: MatUtils.sync_material_names(linked, "box")),
            (
                "rename",
                lambda: MatUtils.rename_texture_file(
                    os.path.join(lib_dir, "crate_Normal.png"), "box_Normal.png"
                ),
            ),
            (
                "lightmap",
                lambda: LightmapRecords.rename_lightmap(
                    "crate_Lightmap.exr", "box_Lightmap.exr"
                ),
            ),
        ):
            try:
                call()
            except ValueError:
                refused.append(name)
        check(
            "a linked material, a texture a linked image reads and a map a linked "
            "marker names each refuse",
            linked is not None
            and linked.library is not None
            and refused == ["sync", "rename", "lightmap"]
            and _listing(lib_dir) == before,
            f"{refused} {_listing(lib_dir)}",
        )
        dep = {
            "map": "crate_Lightmap.exr",
            "objects": ["crate_GEO"],
            "path": os.path.join(lib_dir, "crate_Lightmap.exr"),
        }
        slot, said = _panel()
        renamed = slot._rename_texture({"lightmap": dep}, "box_Lightmap.exr")
        reverted = slot._remove_lightmaps([dep])
        check(
            "the panel refuses to rename or revert a linked library's lightmap",
            renamed is False
            and reverted == 0
            and sum("linked" in text for text in said) == 2
            and _listing(lib_dir) == before
            and LightmapRecords._marker_info("crate_GEO").get("map")
            == "crate_Lightmap.exr",
            f"{said}",
        )

    # 15. UDIM tile sets get rows (2026-10-05). get_image_records listed FILE
    # images only, so a TILED image -- one row per set in mayatk's editor, its
    # existence judged over its tiles -- was invisible to the panel and to every
    # path command, and the commands that did meet one (an explicit selection)
    # read its <UDIM> path as missing and repathed it without moving a tile.
    def _udim_case(label):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        folder = os.path.join(tmp, "udim", label)
        os.makedirs(folder, exist_ok=True)
        btk.EnvUtils.set_current_workspace(folder)
        return folder

    def _tiled(folder, base="rock_Base_Color", name=None):
        """A TILED image over real PNG tiles 1001-1002 in *folder*: Blender
        spells its path as the <UDIM> pattern."""
        paths = [write_png(os.path.join(folder, f"{base}.{n}.png")) for n in (1001, 1002)]
        img = bpy.data.images.load(paths[0], check_existing=False)
        img.source = "TILED"
        if name:
            img.name = name
        return img

    def _udim_rows():
        folder = _udim_case("rows")
        img = _tiled(folder)
        gone_dir = os.path.join(folder, "gone_src")
        gone = _tiled(gone_dir, base="gone_Normal", name="gone_Normal")
        shutil.rmtree(gone_dir)  # every tile of this set deleted
        records = {r["name"]: r for r in btk.get_image_records()}
        check(
            "a TILED image gets one row, its path the set's pattern",
            img.name in records
            and records[img.name]["filepath"].endswith("rock_Base_Color.<UDIM>.png"),
            f"{sorted(records)}",
        )
        check(
            "...present when its tiles are on disk, missing when none is",
            records.get(img.name, {}).get("exists") is True
            and records.get(gone.name, {}).get("exists") is False,
            f"{[(n, r['exists']) for n, r in records.items()]}",
        )

    def _udim_resolve_missing_never_flattens():
        """Resolve Missing rebinds to ONE file: a set must never be flattened
        to a lone tile that matched its stem (mayatk skips token paths)."""
        folder = _udim_case("resolve")
        lost_dir = os.path.join(folder, "lost")
        img = _tiled(lost_dir, name="lost_set")
        stored = img.filepath
        shutil.rmtree(lost_dir)
        found = os.path.join(folder, "found")
        write_png(os.path.join(found, "rock_Base_Color.1001.png"))  # a lone tile
        n = btk.resolve_missing_textures(found, stem=True, texture=True, fuzzy=True)
        check(
            "Resolve Missing never rebinds a tile set to one tile",
            n == 0 and img.filepath == stored,
            f"n={n} {img.filepath}",
        )

    def _udim_set_directory():
        folder = _udim_case("set_dir")
        img = _tiled(folder)
        dest = os.path.join(folder, "dest")
        n = btk.set_texture_directory([img], dest, mode="copy")
        tiles = ["rock_Base_Color.1001.png", "rock_Base_Color.1002.png"]
        check(
            "Set Directory (copy) copies every tile of a set",
            n == 1 and os.path.isdir(dest) and _listing(dest) == tiles,
            f"n={n} {_listing(dest) if os.path.isdir(dest) else 'no folder'}",
        )
        check(
            "...and repoints the set's pattern there",
            _same(_abspath(img), os.path.join(dest, "rock_Base_Color.<UDIM>.png")),
            _abspath(img),
        )
        moved = os.path.join(folder, "moved")
        n = btk.set_texture_directory([img], moved, mode="move")
        check(
            "...and a move takes every tile along, whole",
            n == 1
            and os.path.isdir(moved)
            and _listing(moved) == tiles
            and _listing(dest) == [],
            f"n={n} {_listing(dest)}",
        )

    def _udim_set_lands_whole_or_not():
        """Tiles were copied one by one and a refused tile returned "skip",
        leaving the tiles copied before it in the destination -- foreign to the
        same-named set already there (review, 2026-10-05)."""
        folder = _udim_case("whole_or_not")
        img = _tiled(os.path.join(folder, "src"))
        pattern = os.path.join(folder, "src", "rock_Base_Color.<UDIM>.png")
        dest = os.path.join(folder, "dest")
        os.makedirs(dest)
        _write(dest, "rock_Base_Color.1002.png", b"ANOTHER SET")
        n = btk.set_texture_directory([img], dest, mode="copy")
        check(
            "a set one tile of which is refused copies none of its tiles",
            n == 0
            and _listing(dest) == ["rock_Base_Color.1002.png"]
            and _same(_abspath(img), pattern),
            f"n={n} {_listing(dest)}",
        )
        dest2 = os.path.join(folder, "dest2")
        real_copy = shutil.copy2
        calls = []

        def flaky_copy(src, dst, *args, **kwargs):
            calls.append(src)
            if len(calls) == 2:  # the second tile fails
                raise OSError("disk full")
            return real_copy(src, dst, *args, **kwargs)

        with _mock.patch("shutil.copy2", flaky_copy):
            n = btk.set_texture_directory([img], dest2, mode="copy")
        check(
            "...and a copy failing part-way takes back the tiles it wrote",
            n == 0
            and (not os.path.isdir(dest2) or _listing(dest2) == [])
            and _same(_abspath(img), pattern),
            f"n={n} {_listing(dest2) if os.path.isdir(dest2) else 'no folder'}",
        )

    def _udim_normalize():
        folder = _udim_case("normalize")
        img = _tiled(os.path.join(tmp, "udim", "normalize_external"))
        project = os.path.join(folder, "sourceimages")
        moved = btk.normalize_texture_paths("copy", project_dir=project, images=[img])
        check(
            "Normalize Paths (copy) brings every tile of an external set in",
            moved == 1
            and os.path.isdir(project)
            and len(_listing(project)) == 2
            and _same(_abspath(img), os.path.join(project, "rock_Base_Color.<UDIM>.png")),
            f"moved={moved} {_abspath(img)}",
        )

    def _udim_find_and_copy():
        folder = _udim_case("find_copy")
        img = _tiled(os.path.join(folder, "src"))
        dest = os.path.join(folder, "dest")
        n = btk.find_and_copy_textures([img], None, dest)
        check(
            "Find & Copy relocates a set that resolves, every tile",
            n == 1
            and os.path.isdir(dest)
            and len(_listing(dest)) == 2
            and _same(_abspath(img), os.path.join(dest, "rock_Base_Color.<UDIM>.png")),
            f"n={n} {_abspath(img)}",
        )
        lost_dir = os.path.join(folder, "lost")
        lost = _tiled(lost_dir, base="lost_Normal", name="lost_Normal")
        archive = os.path.join(folder, "archive", "deep")
        os.makedirs(archive)
        for name in os.listdir(lost_dir):
            shutil.move(os.path.join(lost_dir, name), archive)
        dest2 = os.path.join(folder, "dest2")
        n = btk.find_and_copy_textures([lost], os.path.join(folder, "archive"), dest2)
        check(
            "...and finds a missing set by its pattern, every tile",
            n == 1
            and os.path.isdir(dest2)
            and _listing(dest2) == ["lost_Normal.1001.png", "lost_Normal.1002.png"]
            and _same(_abspath(lost), os.path.join(dest2, "lost_Normal.<UDIM>.png")),
            f"n={n} {_abspath(lost)}",
        )

    def _udim_panel():
        folder = _udim_case("panel")
        img = _tiled(folder)
        slot, said = _panel()
        check(
            "the panel reads a set whose tiles are there as resolving",
            slot._path_resolves(img),
        )
        slot.sb.file_dialog = lambda **kwargs: os.path.join(
            folder, "rock_Base_Color.1002.png"
        )
        slot.ui.tbl000 = SimpleNamespace(init_slot=lambda: None)
        entry = {"image": img.name, "path": img.filepath}
        slot._do_browse_for_file([entry])
        check(
            "Browse for File on a set, picking one tile, keeps the set",
            img.filepath.endswith("rock_Base_Color.<UDIM>.png"),
            img.filepath,
        )

    # 16. Set Directory's Allow Missing Targets (mayatk's, 2026-08-25): the path
    # pass repathed every image whether or not the folder held its file, which
    # only spelled a breakage differently. Without the option such an image
    # keeps its path, and the panel says why. Added: 2026-10-05
    def _set_directory_allow_missing():
        folder = _udim_case("allow_missing")
        img = bpy.data.images.load(write_png(os.path.join(folder, "wood_DIFF.png")))
        empty = os.path.join(folder, "empty")
        os.makedirs(empty)
        n = btk.set_texture_directory([img], empty)
        check(
            "Set Directory leaves an image the folder does not hold on its path",
            n == 0 and _same(_abspath(img), os.path.join(folder, "wood_DIFF.png")),
            f"n={n} {_abspath(img)}",
        )
        n = btk.set_texture_directory([img], empty, allow_missing=True)
        check(
            "...unless Allow Missing Targets asks for it",
            n == 1 and _same(_abspath(img), os.path.join(empty, "wood_DIFF.png")),
            f"n={n} {_abspath(img)}",
        )

    def _panel_allow_missing_option():
        folder = _udim_case("allow_missing_panel")
        img = bpy.data.images.load(write_png(os.path.join(folder, "wood_DIFF.png")))
        empty = os.path.join(folder, "empty")
        os.makedirs(empty)

        def button(allow):
            return SimpleNamespace(
                option_box=SimpleNamespace(
                    menu=SimpleNamespace(
                        cmb_relocate_mode=SimpleNamespace(currentIndex=lambda: 0),
                        chk_allow_missing=SimpleNamespace(isChecked=lambda: allow),
                    )
                )
            )

        slot, said = _panel()
        slot.ui.tbl000 = SimpleNamespace(
            init_slot=lambda: None, get_selection=lambda **kwargs: []
        )
        slot.sb.dir_dialog = lambda **kwargs: empty
        slot.tb_set_texture_directory(button(False))
        check(
            "the panel's Set Directory leaves it, and says why",
            _same(_abspath(img), os.path.join(folder, "wood_DIFF.png"))
            and any("Allow Missing Targets" in text for text in said),
            f"{said}",
        )
        slot.tb_set_texture_directory(button(True))
        check(
            "...and repoints it with Allow Missing Targets on",
            _same(_abspath(img), os.path.join(empty, "wood_DIFF.png")),
            _abspath(img),
        )

    for _name, _body in (
        ("rename repoints every reader", _rename_repoints_every_reader),
        ("rename keeps the spelling", _rename_keeps_the_spelling),
        ("rename a tile set", _rename_a_tile_set),
        ("rename refuses a collision", _rename_refuses_a_collision),
        ("rename follows undo", _rename_follows_undo),
        ("an undone rename stays undone", _undone_rename_stays_undone),
        ("sync rolls back a failure part-way", _sync_rolls_back_a_failure_part_way),
        ("sync names one base", _sync_names_one_base),
        ("sync image affix", _sync_image_affix),
        ("sync refuses a collision", _sync_refuses_a_collision),
        ("sync dry run", _sync_dry_run),
        ("sync own set only", _sync_own_set_only),
        ("sync outside the project", _sync_keeps_files_outside_the_project),
        ("sync takes the lightmap", _sync_takes_the_lightmap),
        ("sync without records", _sync_without_records),
        ("rename_lightmap", _rename_lightmap_restamps),
        ("panel is file rename", _panel_is_file_rename),
        ("panel path edit outside the project", _panel_path_edit_outside_the_project),
        ("panel path edit renames", _panel_path_edit_renames),
        ("panel rename file", _panel_rename_file),
        ("panel rename file opens the cell", _panel_rename_file_opens_the_cell),
        ("panel browse for folder", _panel_browse_for_folder),
        ("panel delete lightmap row", _panel_delete_lightmap_row),
        ("panel sync whole material", _panel_sync_whole_material),
        ("panel sync new map type", _panel_sync_new_map_type),
        ("panel sync other set", _panel_sync_other_set),
        ("panel sync image cells", _panel_sync_image_cells),
        ("panel material cell", _panel_material_cell),
        ("panel sync readers", _panel_sync_readers),
        ("panel truncate length", _panel_truncate_length),
        ("panel undo refresh", _panel_undo_refresh),
        ("panel fact columns", _panel_fact_columns),
        ("panel fits the row menu", _panel_fits_the_row_menu),
        ("linked refuses", _linked_refuses),
        ("udim rows", _udim_rows),
        ("udim resolve missing", _udim_resolve_missing_never_flattens),
        ("udim set directory", _udim_set_directory),
        ("udim set lands whole or not", _udim_set_lands_whole_or_not),
        ("udim normalize", _udim_normalize),
        ("udim find and copy", _udim_find_and_copy),
        ("udim panel", _udim_panel),
        ("set directory allow missing", _set_directory_allow_missing),
        ("panel allow missing option", _panel_allow_missing_option),
    ):
        _section(_name, _body)
    btk.EnvUtils.set_current_workspace(None)

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
