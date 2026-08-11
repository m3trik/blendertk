"""blendertk.mat_utils + anim_utils + core recent-files headless test.
Run: blender --background --factory-startup --python blendertk/test/test_mat_anim_utils.py
"""
import sys, os, traceback

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
    import pythontk as ptk

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.materials):
            bpy.data.materials.remove(m)

    # ---- mat_utils ----------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object

    m1 = btk.create_mat("standard", name="M1")
    check("create_mat standard", m1.name == "M1" and m1.use_nodes)
    rnd = btk.create_mat("random")
    check("create_mat random has color name", rnd.name.startswith("mat_"))

    btk.assign_mat([a, b], m1)
    check("assign_mat -> both objects use M1", btk.get_mats([a, b]) == [m1])

    btk.assign_mat(b, rnd)
    users = btk.find_by_mat_id(m1)
    check("find_by_mat_id scoped to users", users == [a], f"{[o.name for o in users]}")

    sel = btk.select_by_material(rnd)
    check("select_by_material selects + activates", sel == [b] and b.select_set is not None
          and bpy.context.view_layer.objects.active is b)

    # find_unassigned: the complement of find_by_mat_id (mirror of mtk.find_unassigned)
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0)); bare = bpy.context.active_object
    bare.name = "Bare"
    unassigned = btk.find_unassigned()
    check("find_unassigned finds the material-less object", unassigned == [bare],
          f"{[o.name for o in unassigned]}")
    check("find_unassigned excludes assigned objects",
          not ({a, b} & set(btk.find_unassigned())))
    bare.data.materials.append(None)  # an EMPTY slot is still "no material"
    check("find_unassigned counts an empty slot as unassigned",
          btk.find_unassigned() == [bare], f"{[o.name for o in btk.find_unassigned()]}")
    check("find_unassigned scopes to the given objects",
          btk.find_unassigned(objects=[a]) == [], f"{btk.find_unassigned(objects=[a])}")
    bpy.ops.object.empty_add(location=(9, 0, 0))
    check("find_unassigned ignores objects that can't hold materials",
          btk.find_unassigned() == [bare], f"{[o.name for o in btk.find_unassigned()]}")
    check("find_unassigned is the complement of find_by_mat_id",
          not (set(btk.find_by_mat_id(m1)) & set(btk.find_unassigned())))

    # ---- mat_utils: Maya-mirror surface (scene mats / info / cleanup) -------
    # get_scene_mats: list / dict / sort / name filter
    names = {m.name for m in btk.get_scene_mats()}
    check("get_scene_mats lists scene mats", {"M1"} <= names, f"{sorted(names)}")
    d = btk.get_scene_mats(as_dict=True, sort=True)
    check("get_scene_mats as_dict sorted", list(d) == sorted(d) and d.get("M1") is m1)
    only_m1 = btk.get_scene_mats(inc="M1")
    check("get_scene_mats inc name filter", only_m1 == [m1], f"{[m.name for m in only_m1]}")

    # is_mat_assigned: M1 on 'a', a fresh unassigned mat is not
    orphan = btk.create_mat("standard", name="Orphan")
    check("is_mat_assigned true for assigned", btk.is_mat_assigned(m1))
    check("is_mat_assigned false for orphan", not btk.is_mat_assigned(orphan))

    # add a TEX_IMAGE node with a generated image to M1 -> texture path/info/get_mat_info
    img = bpy.data.images.new("Tex", 64, 32)
    img.filepath = "//tex/diffuse.png"  # gives the node a resolvable name/path
    tex_node = m1.node_tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = img

    paths = btk.get_texture_paths(materials=[m1])
    check("get_texture_paths finds the node image", any("diffuse.png" in p for p in paths),
          f"{paths}")
    tinfo = btk.get_texture_info(materials=[m1])
    check("get_texture_info reads native size", tinfo and tinfo[0]["width"] == 64
          and tinfo[0]["height"] == 32 and tinfo[0]["mode"] == "RGBA", f"{tinfo}")

    recs = btk.get_mat_info(materials=[m1])
    rec = next((r for r in recs if r["material"] == "M1"), None)
    check("get_mat_info record schema", rec is not None and rec["type"]
          and len(rec["textures"]) == 1 and rec["textures"][0]["width"] == 64, f"{rec}")
    check("get_mat_info type = surface shader label", rec and "BSDF" in rec["type"], rec and rec["type"])

    # exclude_unassigned drops the orphan; include_textures=False -> empty textures
    recs_assigned = btk.get_mat_info(exclude_unassigned=True)
    check("get_mat_info exclude_unassigned drops orphan",
          "Orphan" not in {r["material"] for r in recs_assigned})
    recs_notex = btk.get_mat_info(materials=[m1], include_textures=False)
    check("get_mat_info include_textures=False", recs_notex[0]["textures"] == [])

    # format delegates to pythontk.MatReport
    html = btk.format_mat_info_html(recs)
    check("format_mat_info_html delegates", "Material Info" in html and "M1" in html)

    # duplicate detection: two materials sharing the same texture file
    dup_mat = btk.create_mat("standard", name="M1_dup")
    dup_node = dup_mat.node_tree.nodes.new("ShaderNodeTexImage")
    dup_node.image = img  # same image datablock -> same path signature
    bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0)); c = bpy.context.active_object
    btk.assign_mat(c, dup_mat)
    groups = btk.find_materials_with_duplicate_textures()
    check("find duplicate-texture materials", any({m1, dup_mat} <= set(g) for g in groups),
          f"{[[m.name for m in g] for g in groups]}")
    reassigned = btk.reassign_duplicate_materials(groups, delete=True)
    check("reassign duplicates -> c now uses the canonical mat",
          reassigned >= 1 and c.material_slots[0].material is m1
          and "M1_dup" not in {m.name for m in bpy.data.materials},
          f"reassigned={reassigned}")

    # delete_unused_materials removes the orphan (no users, no fake user)
    removed = btk.delete_unused_materials()
    check("delete_unused_materials removes orphan",
          "Orphan" in removed and "Orphan" not in {m.name for m in bpy.data.materials},
          f"removed={removed}")

    # fake-user material is protected
    keep = btk.create_mat("standard", name="KeepMe"); keep.use_fake_user = True
    btk.delete_unused_materials()
    check("delete_unused_materials respects fake user", "KeepMe" in {m.name for m in bpy.data.materials})

    # ---- texture path management (Texture Path Editor engine) ---------------
    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix="btk_tex_")

    def make_png(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        gen = bpy.data.images.new("_gen", 4, 4)
        gen.filepath_raw = path
        gen.file_format = "PNG"
        gen.save()
        bpy.data.images.remove(gen)

    def absp(img):
        return os.path.normpath(bpy.path.abspath(img.filepath))

    for im in [i for i in bpy.data.images if i.source == "FILE"]:
        bpy.data.images.remove(im)
    p_ok = os.path.join(tmp, "diffuse.png"); make_png(p_ok)
    img_ok = bpy.data.images.load(p_ok)
    img_missing = bpy.data.images.new("missing_tex", 4, 4)
    img_missing.source = "FILE"
    img_missing.filepath = os.path.join(tmp, "nope", "normal.png")  # does not exist

    recs = {r["name"]: r for r in btk.get_image_records()}
    check("get_image_records: present texture exists",
          recs[img_ok.name]["exists"] is True and recs[img_ok.name]["abspath"] == os.path.normpath(p_ok))
    check("get_image_records: missing texture flagged",
          recs["missing_tex"]["exists"] is False)

    # resolve_missing_textures: a same-named file under the search dir repaths the missing one
    make_png(os.path.join(tmp, "search", "normal.png"))
    n = btk.resolve_missing_textures(tmp)
    check("resolve_missing_textures repaths by basename",
          n >= 1 and absp(img_missing) == os.path.normpath(os.path.join(tmp, "search", "normal.png")),
          f"n={n} -> {img_missing.filepath}")

    # normalize_texture_paths copy: external textures pulled into the project folder
    proj = os.path.join(tmp, "proj_tex")
    n = btk.normalize_texture_paths("copy", project_dir=proj)
    check("normalize_texture_paths copy brings externals into project",
          n >= 1 and os.path.exists(os.path.join(proj, "diffuse.png"))
          and os.path.dirname(absp(img_ok)) == os.path.normpath(proj), f"n={n}")

    # repath_image: point an image at a new file
    other = os.path.join(tmp, "other.png"); make_png(other)
    check("repath_image sets the path",
          btk.repath_image(img_ok, other) and absp(img_ok) == os.path.normpath(other))

    html = btk.format_texture_paths_html()
    check("format_texture_paths_html builds a table",
          "<table" in html and "Texture Paths" in html)

    # resolve_missing 'texture' tier: restrict to the SAME map type (an _AO never grabs a _Normal),
    # even when a different-type file has the closer (exact) base name.
    for im in [i for i in bpy.data.images if i.source == "FILE"]:
        bpy.data.images.remove(im)
    rtdir = os.path.join(tmp, "rt_search")
    make_png(os.path.join(rtdir, "rock2_AO.png"))     # same map type (AO), fuzzy base "rock2"
    make_png(os.path.join(rtdir, "rock_Normal.png"))  # different map type, exact base "rock"
    miss_ao = bpy.data.images.new("miss_ao", 4, 4)
    miss_ao.source = "FILE"
    miss_ao.filepath = os.path.join(tmp, "nope", "rock_AO.png")  # missing
    n = btk.resolve_missing_textures(rtdir, stem=False, texture=True, fuzzy=False)
    check("resolve_missing 'texture' tier matches same map type, not the closer wrong-type name",
          n == 1 and os.path.basename(absp(miss_ao)) == "rock2_AO.png",
          f"n={n} -> {miss_ao.filepath}")
    bpy.data.images.remove(miss_ao)

    # collision guard: copy onto a DIFFERENT-size same-name file is skipped (no clobber, no rebind);
    # an IDENTICAL file already at the destination is a safe rebind.
    for im in [i for i in bpy.data.images if i.source == "FILE"]:
        bpy.data.images.remove(im)
    csrc = os.path.join(tmp, "csrc", "tile.png"); make_png(csrc)
    cdst = os.path.join(tmp, "cdst"); os.makedirs(cdst, exist_ok=True)
    with open(os.path.join(cdst, "tile.png"), "wb") as fh:
        fh.write(b"x" * 99999)  # different size -> a different file with the same name
    size_before = os.path.getsize(os.path.join(cdst, "tile.png"))
    cimg = bpy.data.images.load(csrc)
    n = btk.set_texture_directory([cimg], cdst, mode="copy")
    check("collision guard: different-size dst not overwritten; image keeps its path",
          n == 0 and os.path.getsize(os.path.join(cdst, "tile.png")) == size_before
          and absp(cimg) == os.path.normpath(csrc),
          f"n={n} dst={os.path.getsize(os.path.join(cdst, 'tile.png'))}/{size_before}")
    cdst2 = os.path.join(tmp, "cdst2"); os.makedirs(cdst2, exist_ok=True)
    shutil.copy2(csrc, os.path.join(cdst2, "tile.png"))  # identical file already present
    n2 = btk.set_texture_directory([cimg], cdst2, mode="copy")
    check("collision guard: identical dst is a safe rebind (repath, no error)",
          n2 == 1 and os.path.dirname(absp(cimg)) == os.path.normpath(cdst2), f"n2={n2}")
    bpy.data.images.remove(cimg)

    shutil.rmtree(tmp, ignore_errors=True)

    # ---- duplicate-material two-phase verification (mirror of mayatk's near-misses) ----
    # The old fingerprint was the bare SET of texture abspaths: no socket identity, no
    # shader settings, and node-group textures were invisible. Two materials merely
    # SHARING one detail/normal map merged — and reassign_duplicate_materials(delete=True)
    # (default-on exporter task) then DELETED one of them. verify=True (the default)
    # must reject every near-miss below; only true duplicates merge.
    reset()
    dv_tmp = tempfile.mkdtemp(prefix="btk_dupver_")
    dv_imgs = []

    def dv_load(path):
        im = bpy.data.images.load(path, check_existing=False)
        dv_imgs.append(im)
        return im

    def pbr_mat(name, tex_path, socket="Base Color", mapping=None, colorspace=None):
        """Fresh Principled material with tex_path wired into *socket* (optionally
        through a Mapping node with the given Scale)."""
        mat = btk.create_mat("standard", name=name)
        nt = mat.node_tree
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = dv_load(tex_path)
        if colorspace:
            tex.image.colorspace_settings.name = colorspace
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        nt.links.new(tex.outputs["Color"], bsdf.inputs[socket])
        if mapping is not None:
            mp = nt.nodes.new("ShaderNodeMapping")
            mp.inputs["Scale"].default_value = mapping
            nt.links.new(mp.outputs["Vector"], tex.inputs["Vector"])
        return mat

    def find_pair(groups, x, y):
        return any(x in g and y in g for g in groups)

    _find = btk.find_materials_with_duplicate_textures
    shared = os.path.join(dv_tmp, "shared_Normal.png"); make_png(shared)

    # (a) socket identity: same texture feeding DIFFERENT surface inputs != duplicates
    m_sock_a = pbr_mat("DupSockA", shared, socket="Base Color")
    m_sock_b = pbr_mat("DupSockB", shared, socket="Roughness")
    check("same texture on different surface sockets -> NOT duplicates",
          not find_pair(_find(materials=[m_sock_a, m_sock_b]), m_sock_a, m_sock_b))

    # (b) same texture, different tiling/mapping: fingerprints still group them
    # (the old false positive) but verification must reject the pair.
    m_flat = pbr_mat("DupMapA", shared)
    m_tiled = pbr_mat("DupMapB", shared, mapping=(4.0, 4.0, 1.0))
    check("verify=False still nets the tiling near-miss (the pre-fix candidate)",
          find_pair(_find(materials=[m_flat, m_tiled], verify=False), m_flat, m_tiled))
    check("same texture, different tiling/mapping -> NOT duplicates (verified)",
          not find_pair(_find(materials=[m_flat, m_tiled]), m_flat, m_tiled))

    # (c) same texture, different unlinked shader settings != duplicates
    m_set_a = pbr_mat("DupSetA", shared)
    m_set_b = pbr_mat("DupSetB", shared)
    next(n for n in m_set_b.node_tree.nodes
         if n.type == "BSDF_PRINCIPLED").inputs["Metallic"].default_value = 1.0
    check("same texture, different unlinked shader settings -> NOT duplicates",
          not find_pair(_find(materials=[m_set_a, m_set_b]), m_set_a, m_set_b))

    # (d) same texture, different color space != duplicates
    m_cs_a = pbr_mat("DupCsA", shared)
    m_cs_b = pbr_mat("DupCsB", shared, colorspace="Non-Color")
    check("same texture, different color space -> NOT duplicates",
          not find_pair(_find(materials=[m_cs_a, m_cs_b]), m_cs_a, m_cs_b))

    # (e) same basename, different file CONTENT: the loose stem fingerprint nets
    # them (the consolidation heuristic) but the content id must split them.
    pA = os.path.join(dv_tmp, "setA", "albedo.png"); make_png(pA)
    pB = os.path.join(dv_tmp, "setB", "albedo.png")
    os.makedirs(os.path.dirname(pB), exist_ok=True)
    genB = bpy.data.images.new("_gen16", 16, 16)  # different pixels -> different bytes
    genB.filepath_raw = pB; genB.file_format = "PNG"; genB.save()
    bpy.data.images.remove(genB)
    m_cnt_a = pbr_mat("DupCntA", pA)
    m_cnt_b = pbr_mat("DupCntB", pB)
    check("same-basename fingerprint groups them unverified (consolidation net)",
          find_pair(_find(materials=[m_cnt_a, m_cnt_b], verify=False), m_cnt_a, m_cnt_b))
    check("same basename, different file content -> NOT duplicates (verified)",
          not find_pair(_find(materials=[m_cnt_a, m_cnt_b]), m_cnt_a, m_cnt_b))

    # (f) same basename, IDENTICAL content in another folder -> verified duplicates
    # (the consolidation case the loose fingerprint exists for).
    pC = os.path.join(dv_tmp, "setC", "albedo.png")
    os.makedirs(os.path.dirname(pC), exist_ok=True)
    shutil.copy2(pA, pC)
    m_cnt_c = pbr_mat("DupCntC", pC)
    check("same basename, identical content elsewhere -> verified duplicates",
          find_pair(_find(materials=[m_cnt_a, m_cnt_c]), m_cnt_a, m_cnt_c))

    # (g) a texture nested inside a node group is SEEN (and its socket resolved
    # through the group boundary) — previously invisible to the fingerprint.
    def group_mat(name, tex_path):
        mat = btk.create_mat("standard", name=name)
        nt = mat.node_tree
        g = bpy.data.node_groups.new(f"{name}_grp", "ShaderNodeTree")
        g.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        gout = g.nodes.new("NodeGroupOutput")
        tex = g.nodes.new("ShaderNodeTexImage")
        tex.image = dv_load(tex_path)
        g.links.new(tex.outputs["Color"], gout.inputs["Color"])
        gn = nt.nodes.new("ShaderNodeGroup")
        gn.node_tree = g
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        nt.links.new(gn.outputs["Color"], bsdf.inputs["Base Color"])
        return mat

    m_grp = group_mat("DupGrpA", pA)
    m_dir = pbr_mat("DupGrpB", pA)
    check("texture inside a node group is seen (duplicates its direct twin)",
          find_pair(_find(materials=[m_grp, m_dir]), m_grp, m_dir))

    # (h) true duplicates (same image, same settings) -> found AND merged.
    bpy.ops.mesh.primitive_cube_add(location=(20, 0, 0)); duo = bpy.context.active_object
    m_true_a = pbr_mat("DupTrueA", pA)
    m_true_b = pbr_mat("DupTrueB", pA)
    btk.assign_mat(duo, m_true_b)
    true_groups = _find(materials=[m_true_a, m_true_b])
    check("true duplicates (same image, same settings) are found",
          find_pair(true_groups, m_true_a, m_true_b),
          f"{[[m.name for m in g] for g in true_groups]}")
    n_re = btk.reassign_duplicate_materials(true_groups, delete=True)
    check("true duplicates merge: object reassigned to keeper, dup deleted",
          n_re >= 1 and duo.material_slots[0].material is m_true_a
          and "DupTrueB" not in {m.name for m in bpy.data.materials},
          f"reassigned={n_re}")

    # (i) reassign's own verify gate: a stale/unverified group must not merge
    # a tiling near-miss even when handed to it directly.
    stale = [[m_flat, m_tiled]]
    check("reassign_duplicate_materials(verify=True) skips an unverified group",
          btk.reassign_duplicate_materials(stale, delete=True) == 0
          and m_tiled.name in {m.name for m in bpy.data.materials})

    for _m in (m_sock_a, m_sock_b, m_flat, m_tiled, m_set_a, m_set_b, m_cs_a,
               m_cs_b, m_cnt_a, m_cnt_b, m_cnt_c, m_grp, m_dir, m_true_a):
        bpy.data.materials.remove(_m)
    for _im in dv_imgs:
        try:
            bpy.data.images.remove(_im)
        except Exception:
            pass
    shutil.rmtree(dv_tmp, ignore_errors=True)

    # ---- shader templates ---------------------------------------------------
    templates = btk.get_shader_templates()
    check("get_shader_templates lists presets", "Metal" in templates and "Glass" in templates)
    mat_metal = btk.create_shader_template("Metal", name="TmplMetal")
    bsdf = next((n for n in mat_metal.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("create_shader_template builds a configured material",
          mat_metal.name == "TmplMetal" and bsdf is not None
          and abs(bsdf.inputs["Metallic"].default_value - 1.0) < 1e-6)
    # apply onto an existing material (Glass -> transmission)
    plain = btk.create_mat("standard", name="TmplApply")
    applied = btk.apply_shader_template(plain, "Glass")
    gb = next((n for n in plain.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("apply_shader_template writes Principled inputs",
          "Roughness" in applied and abs(gb.inputs["Roughness"].default_value) < 1e-6, f"{applied}")
    check("apply_shader_template unknown template -> []",
          btk.apply_shader_template(plain, "NopeTemplate") == [])

    # ---- shader graph capture/restore (serialize_material / restore_material) -----------------
    st_tmp = tempfile.mkdtemp(prefix="btk_st_")

    def st_png(mapname):
        p = os.path.join(st_tmp, f"STAsset_{mapname}.png"); make_png(p); return p

    # Build a real multi-node graph (base color + roughness + normal) to capture.
    src_set = [st_png(m) for m in ("BaseColor", "Roughness", "Normal")]
    src_mat = btk.create_pbr_material(src_set, name="STSource")
    snap = btk.serialize_material(src_mat)
    check("serialize_material captures nodes + links",
          isinstance(snap, dict) and len(snap["nodes"]) >= 4 and len(snap["links"]) >= 3,
          f"nodes={len(snap['nodes'])} links={len(snap['links'])}")
    check("serialize_material records image map types (not paths)",
          any(nd.get("map_type") == "Base_Color" for nd in snap["nodes"])
          and not any("STAsset" in str(nd.get("inputs")) for nd in snap["nodes"]))
    # JSON round-trip (the on-disk preset form)
    import json as _json
    snap = _json.loads(_json.dumps(snap))

    # Restore with FRESH textures (different set) -> rebound by map type, structure preserved.
    fresh = [os.path.join(st_tmp, "fresh", f"Brick_{m}.png") for m in ("BaseColor", "Roughness", "Normal")]
    for f in fresh:
        make_png(f)
    restored = btk.restore_material(snap, name="STRestored", textures=fresh)
    rnt = restored.node_tree
    rb = next((n for n in rnt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("restore_material rebuilds the BSDF + link topology",
          rb is not None and len(rnt.links) >= 3
          and rb.inputs["Base Color"].is_linked and rb.inputs["Roughness"].is_linked
          and rb.inputs["Normal"].is_linked)
    base_img = next((n for n in rnt.nodes if n.type == "TEX_IMAGE"
                     and "Brick_BaseColor" in (n.image.name if n.image else "")), None)
    check("restore_material rebinds FRESH textures by map type",
          base_img is not None
          and any(n.type == "TEX_IMAGE" and n.image and "Brick_Normal" in n.image.name
                  and n.image.colorspace_settings.name == "Non-Color" for n in rnt.nodes))

    # PresetStore round-trip (the panel's save→load path): save snap, reload from a FRESH store.
    pdir = os.path.join(st_tmp, "presets")
    ptk.PresetStore("st_test", package="blendertk", user_dir=pdir).save("MyShader", snap)
    store2 = ptk.PresetStore("st_test", package="blendertk", user_dir=pdir)
    check("PresetStore save/list/rename round-trips a template",
          "MyShader" in store2.list(tier="user")
          and store2.rename("MyShader", "MyShader2") and "MyShader2" in store2.list(tier="user"))
    from_store = btk.restore_material(store2.load("MyShader2"), name="STFromStore", textures=fresh)
    check("restore_material from a PresetStore-loaded preset rebuilds the graph",
          next((n for n in from_store.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) is not None
          and len(from_store.node_tree.links) >= 3)

    # param-preset shorthand shares the restore path
    pm = btk.restore_material({"params": {"Metallic": 1.0, "Roughness": 0.2}}, name="STParam")
    pb = next((n for n in pm.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("restore_material param shorthand builds a Principled",
          pb is not None and abs(pb.inputs["Metallic"].default_value - 1.0) < 1e-6)

    # unbound image node when no matching texture is supplied (mirrors Maya's missing-texture case)
    nob = btk.restore_material(snap, name="STNoTex", textures=[])
    check("restore_material leaves image nodes unbound when no textures given",
          all(n.image is None for n in nob.node_tree.nodes if n.type == "TEX_IMAGE"))

    shutil.rmtree(st_tmp, ignore_errors=True)

    # ---- material updater (MatUpdater engine) -------------------------------
    import tempfile as _tf

    # ensure_image_deps: empty request is a clean no-op (never triggers a pip install).
    check("ensure_image_deps empty request -> []", btk.ensure_image_deps(packages={}) == [])

    mu_tmp = _tf.mkdtemp(prefix="btk_matupd_")

    def mat_with_texture(name, tex_path):
        """A node material whose Base Color is an on-disk image at ``tex_path``."""
        make_png(tex_path)
        mat = btk.create_mat("standard", name=name)
        nt = mat.node_tree
        img = bpy.data.images.load(tex_path)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        return mat, img

    # A material with NO file texture -> nothing to do, returns {} (no PIL needed).
    bare = btk.create_mat("standard", name="MU_Bare")
    check("update_materials: no textures -> {}", btk.update_materials(materials=[bare]) == {})

    # materials_for_textures: the "Browse…" scope — find materials referencing picked files.
    mat_b, img_b = mat_with_texture("MU_Browse", os.path.join(mu_tmp, "MUBrowse_Diffuse.png"))
    hit = btk.materials_for_textures([os.path.join(mu_tmp, "MUBrowse_Diffuse.png")])
    check("materials_for_textures finds the material referencing a picked texture",
          [m.name for m in hit] == ["MU_Browse"], f"{[m.name for m in hit]}")
    check("materials_for_textures: unreferenced path -> []",
          btk.materials_for_textures([os.path.join(mu_tmp, "ghost_tex.png")]) == []
          and btk.materials_for_textures([]) == [])

    # Dry run reports a plan and does NOT touch the image path (no PIL / no install needed).
    mat_dry, img_dry = mat_with_texture("MU_Dry", os.path.join(mu_tmp, "MUDry_Diffuse.png"))
    before = absp(img_dry)
    res_dry = btk.update_materials(materials=[mat_dry], config={"dry_run": True})
    check("update_materials dry_run: plan only, path unchanged",
          res_dry.get("MU_Dry", {}).get("updated") == 0 and absp(img_dry) == before,
          f"{res_dry}")

    # Full reprocess once the image lib is available. ``ensure_image_deps`` provisions Pillow into
    # Blender's Python (idempotent — already-installed = fast "already satisfied" + path add) and
    # returns what's importable; when it can't (offline / no pip) the assertions skip, not fail.
    if "PIL" in btk.ensure_image_deps():
        out_dir = os.path.join(mu_tmp, "out")
        mat_run, img_run = mat_with_texture("MU_Run", os.path.join(mu_tmp, "MURun_Diffuse.png"))
        orig = absp(img_run)
        res = btk.update_materials(
            materials=[mat_run],
            config={"output_extension": "tga", "move_to_folder": out_dir},
        )
        new = absp(img_run)
        check("update_materials reprocess: image repathed to a real new file",
              res.get("MU_Run", {}).get("updated", 0) >= 1 and new != orig and os.path.isfile(new),
              f"{res} -> {new}")
    else:
        lines.append("OK   update_materials full reprocess SKIPPED (no PIL in this Blender)")

    # The run report goes out through log_box/log_group, which write via log_raw
    # and BYPASS level filtering — so a non-verbose caller (the Scene Exporter
    # runs this as its convert_textures task with the default verbose=False)
    # gets the whole banner + settings dump unless the engine gates it itself.
    import logging as _logging

    from blendertk.mat_utils._mat_utils import MatUpdater as _MatUpdater

    class _Collector(_logging.Handler):  # no .stream -> log_raw uses emit()
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    mat_q, _img_q = mat_with_texture("MU_Quiet", os.path.join(mu_tmp, "MUQuiet_Diffuse.png"))
    for verbose, want_banner in ((False, False), (True, True)):
        collector = _Collector()
        _MatUpdater.logger.addHandler(collector)
        try:
            _MatUpdater.update_materials(
                materials=[mat_q], config={"dry_run": True}, verbose=verbose
            )
        finally:
            _MatUpdater.logger.removeHandler(collector)
        emitted = "\n".join(str(r.msg) for r in collector.records)
        check(
            f"update_materials verbose={verbose}: run banner "
            f"{'present' if want_banner else 'suppressed'}",
            ("MATERIAL UPDATE" in emitted) == want_banner,
            # ASCII-folded: the report is full of box-drawing glyphs, and a
            # detail string is printed straight to the (cp1252) console.
            emitted[:200].encode("ascii", "replace").decode("ascii"),
        )

    shutil.rmtree(mu_tmp, ignore_errors=True)

    # ---- game shader (create_pbr_material) ----------------------------------
    gs_tmp = _tf.mkdtemp(prefix="btk_gs_")

    def gs_png(mapname):
        p = os.path.join(gs_tmp, f"GSAsset_{mapname}.png")
        make_png(p)
        return p

    tex_set = [gs_png(m) for m in
               ("BaseColor", "Roughness", "Metallic", "Normal", "AmbientOcclusion", "Emissive", "Opacity")]
    pbr = btk.create_pbr_material(tex_set, name="GSMat")
    nt = pbr.node_tree if pbr else None
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None) if nt else None
    check("create_pbr_material builds a material with a Principled BSDF",
          pbr is not None and pbr.name == "GSMat" and bsdf is not None)

    def src_node(inp):
        s = bsdf.inputs.get(inp) if bsdf else None
        return s.links[0].from_node if (s and s.is_linked) else None

    def img_cs(maptype):  # colorspace of the loaded image whose filename carries maptype
        node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and maptype in n.image.name), None)
        return node.image.colorspace_settings.name if node else None

    check("metallic + roughness wired from images",
          getattr(src_node("Metallic"), "type", "") == "TEX_IMAGE"
          and getattr(src_node("Roughness"), "type", "") == "TEX_IMAGE")
    check("normal wired through a Normal Map node",
          getattr(src_node("Normal"), "type", "") == "NORMAL_MAP")
    check("emission + alpha wired", src_node("Emission Color") is not None and src_node("Alpha") is not None)
    check("AO multiplied into Base Color (MixRGB)",
          getattr(src_node("Base Color"), "type", "") == "MIX_RGB")
    check("color space: base sRGB, data Non-Color",
          img_cs("BaseColor") == "sRGB" and img_cs("Metallic") == "Non-Color"
          and img_cs("Normal") == "Non-Color")

    # glossiness -> invert -> roughness (no Roughness map present)
    gmat = btk.create_pbr_material([gs_png("Glossiness")], name="GSGloss")
    gb = next((n for n in gmat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    rough_src = gb.inputs["Roughness"].links[0].from_node if gb.inputs["Roughness"].is_linked else None
    check("glossiness inverted into roughness", getattr(rough_src, "type", "") == "INVERT")

    # DirectX normal -> green-flip graph (SeparateColor present)
    dmat = btk.create_pbr_material([gs_png("Normal_DirectX")], name="GSDX")
    check("DirectX normal builds a green-flip (SeparateColor)",
          any(n.type == "SEPARATE_COLOR" for n in dmat.node_tree.nodes))

    # packed ORM -> SeparateColor feeds Roughness(G) + Metallic(B)
    omat = btk.create_pbr_material([gs_png("ORM")], name="GSORM")
    ob = next((n for n in omat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("ORM split feeds Metallic + Roughness",
          ob.inputs["Metallic"].is_linked and ob.inputs["Roughness"].is_linked
          and ob.inputs["Metallic"].links[0].from_node.type == "SEPARATE_COLOR")

    # Albedo+Transparency: one image node feeds Base Color (Color) AND Alpha (Alpha out)
    at_path = gs_png("AlbedoTransparency")
    check("AlbedoTransparency filename classifies",
          ptk.MapFactory.resolve_map_type(at_path, key=True) == "Albedo_Transparency")
    atmat = btk.create_pbr_material([at_path], name="GSAT")
    ab = next((n for n in atmat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    base_src = ab.inputs["Base Color"].links[0].from_node if ab.inputs["Base Color"].is_linked else None
    alpha_src = ab.inputs["Alpha"].links[0].from_node if ab.inputs["Alpha"].is_linked else None
    # One image node feeds both — compare by .name (bpy recreates RNA wrappers, so `is` is unreliable)
    # and confirm there's only one TEX_IMAGE in the tree.
    n_at_imgs = sum(1 for n in atmat.node_tree.nodes if n.type == "TEX_IMAGE")
    check("AlbedoTransparency: one TEX_IMAGE drives both Base Color and Alpha",
          getattr(base_src, "type", "") == "TEX_IMAGE" and getattr(alpha_src, "type", "") == "TEX_IMAGE"
          and base_src.name == alpha_src.name and n_at_imgs == 1
          and atmat.blend_method == "HASHED",
          f"base={getattr(base_src,'name',None)} alpha={getattr(alpha_src,'name',None)} imgs={n_at_imgs}")

    # Metallic_Smoothness: RGB->Metallic, A->invert->Roughness (Unity packing)
    ms_path = gs_png("MetallicSmoothness")
    check("MetallicSmoothness filename classifies",
          ptk.MapFactory.resolve_map_type(ms_path, key=True) == "Metallic_Smoothness")
    msmat = btk.create_pbr_material([ms_path], name="GSMS")
    mb = next((n for n in msmat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("MetallicSmoothness feeds Metallic (image) + Roughness (invert)",
          mb.inputs["Metallic"].links[0].from_node.type == "TEX_IMAGE"
          and mb.inputs["Roughness"].links[0].from_node.type == "INVERT")

    # MSAO / Unity HDRP mask: R->Metallic, G->AO(multiply Base Color), A->invert->Roughness
    msao_path = gs_png("MaskMap")
    check("MaskMap filename classifies as MSAO",
          ptk.MapFactory.resolve_map_type(msao_path, key=True) == "MSAO")
    msaomat = btk.create_pbr_material([msao_path], name="GSMSAO")
    qb = next((n for n in msaomat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    check("MSAO feeds Metallic (SeparateColor) + Roughness (invert) + AO (MixRGB into Base)",
          qb.inputs["Metallic"].links[0].from_node.type == "SEPARATE_COLOR"
          and qb.inputs["Roughness"].links[0].from_node.type == "INVERT"
          and qb.inputs["Base Color"].links[0].from_node.type == "MIX_RGB")

    # Packed-map redundancy is decided by the SHARED ptk.MapFactory.filter_redundant_maps (the
    # registry's precedence rules), NOT a local priority chain — so Blender and Maya can't drift.
    # RIVAL packings collapse to ONE: ORM, MSAO and Metallic_Smoothness all drive metallic /
    # roughness / AO, so a set carrying several used to wire those inputs several times. With no
    # config there is no stated target, and ptk.MapRegistry.packed_precedence ranks by declared
    # breadth — ORM ships to UE/glTF/Godot, Metallic_Smoothness to URP/SpecGloss, MSAO only to
    # HDRP — so the ORM wins and both rivals retire (their channels are all derivable from it,
    # so nothing has to be extracted first).
    pfiles = [gs_png("ORM"), gs_png("MaskMap"), gs_png("MetallicSmoothness")]
    pplan = btk.MatUtils.resolve_pbr_plan(pfiles)
    check("rival packings collapse to one: ORM supersedes MSAO + MetallicSmoothness",
          set(pplan["dropped"]) == {"MSAO", "Metallic_Smoothness"}
          and set(pplan["by_type"]) == {"ORM"}
          and pplan["extracted"] == {},
          f"kept={sorted(pplan['by_type'])} dropped={sorted(pplan['dropped'])}")
    pmat = btk.create_pbr_material(pfiles, name="GSPrio")
    n_imgs = sum(1 for n in pmat.node_tree.nodes if n.type == "TEX_IMAGE")
    check("rival packings: only the surviving ORM image is loaded",
          n_imgs == 1, f"images={n_imgs}")
    # One surviving packed map -> the AO multiply appears exactly once (two chained MULTIPLY
    # nodes would darken the albedo twice).
    n_mix = sum(1 for n in pmat.node_tree.nodes if n.type == "MIX_RGB")
    check("the surviving packed map applies AO exactly once (no stacked MULTIPLY)",
          n_mix == 1, f"mix_nodes={n_mix}")

    # Batch: a set of files spanning two texture sets -> two materials
    def gs_set_png(setname, mapname):
        p = os.path.join(gs_tmp, f"{setname}_{mapname}.png"); make_png(p); return p
    batch_files = [gs_set_png("brick", "BaseColor"), gs_set_png("brick", "Normal"),
                   gs_set_png("wood", "BaseColor"), gs_set_png("wood", "Roughness")]
    batch = btk.create_pbr_materials(batch_files)
    check("create_pbr_materials groups by set -> 2 materials",
          set(batch) == {"brick", "wood"} and all(m is not None for m in batch.values()),
          f"{list(batch)}")
    merged = btk.create_pbr_materials(batch_files, name="Merged")
    check("create_pbr_materials explicit name -> single material",
          list(merged) == ["Merged"] and merged["Merged"] is not None)
    # a prefix alone still batches (name=None) and is prepended to each set's material name
    pref = btk.create_pbr_materials(batch_files, prefix="Mat_")
    check("create_pbr_materials prefix batches + prepends per set",
          set(pref) == {"Mat_brick", "Mat_wood"} and all(m is not None for m in pref.values()),
          f"{list(pref)}")
    check("create_pbr_materials nothing on disk -> {}",
          btk.create_pbr_materials([os.path.join(gs_tmp, "ghost.png")]) == {})

    check("create_pbr_material no classifiable textures -> None",
          btk.create_pbr_material([os.path.join(gs_tmp, "random_noise.png")]) is None)

    # ---- game shader: resolve_pbr_plan (shared conflict resolution) ----------
    # The plan is the SSoT both create_pbr_material and GameShader's report read; the redundancy
    # call is ptk.MapFactory.filter_redundant_maps, not a hardcoded priority chain.
    noise = os.path.join(gs_tmp, "random_noise.png"); make_png(noise)
    plan = btk.MatUtils.resolve_pbr_plan([gs_png("BaseColor"), gs_png("Normal"), noise])
    check("resolve_pbr_plan returns the documented shape",
          set(plan) == {"by_type", "dropped", "extracted", "unknown", "unhandled", "wired"},
          f"{sorted(plan)}")
    check("resolve_pbr_plan classifies known maps + collects unclassified ones",
          set(plan["by_type"]) == {"Base_Color", "Normal"} and plan["unknown"] == [noise],
          f"{sorted(plan['by_type'])} / {plan['unknown']}")

    # A classified map with no Principled input is REPORTED (mayatk's "no matching slot" row),
    # not silently dropped from the plan.
    plan_un = btk.MatUtils.resolve_pbr_plan([gs_png("BaseColor"), gs_png("Thickness")])
    check("resolve_pbr_plan flags a classified-but-unwirable map as unhandled",
          set(plan_un["unhandled"]) == {"Thickness"}, f"{plan_un['unhandled']}")

    # An MRAO supersedes loose Metallic/Roughness in the shared registry's precedence rules, so
    # the wiring MUST consume it — otherwise the loose maps are dropped and nothing replaces them.
    mr_files = [gs_png("BaseColor"), gs_png("MRAO"), gs_png("Metallic"), gs_png("Roughness")]
    mr_plan = btk.MatUtils.resolve_pbr_plan(mr_files)
    check("MRAO supersedes loose Metallic/Roughness (shared precedence rules)",
          set(mr_plan["dropped"]) == {"Metallic", "Roughness"} and "MRAO" in mr_plan["by_type"],
          f"kept={sorted(mr_plan['by_type'])} dropped={sorted(mr_plan['dropped'])}")
    mrmat = btk.create_pbr_material(mr_files, name="GSMRAO")
    mrb = next(n for n in mrmat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    check("MRAO is WIRED, so superseding the loose maps loses nothing",
          mrb.inputs["Metallic"].is_linked and mrb.inputs["Roughness"].is_linked
          and mrb.inputs["Metallic"].links[0].from_node.type == "SEPARATE_COLOR"
          and mrb.inputs["Base Color"].links[0].from_node.type == "MIX_RGB",
          f"metallic={mrb.inputs['Metallic'].is_linked} rough={mrb.inputs['Roughness'].is_linked}")

    # A kept-but-shadowed map must NOT be reported as connected (the silent-drop guard):
    # Height loses Normal to the real normal map, Glossiness loses Roughness to the real one.
    sh_plan = btk.MatUtils.resolve_pbr_plan(
        [gs_png("BaseColor"), gs_png("Normal"), gs_png("Height"),
         gs_png("Roughness"), gs_png("Glossiness")])
    btk.create_pbr_material([], name="GSShadow", plan=sh_plan)
    check("create_pbr_material reports the map types it actually wired",
          "wired" in sh_plan and {"Base_Color", "Normal", "Roughness"} <= sh_plan["wired"],
          f"wired={sorted(sh_plan.get('wired', ()))}")
    check("shadowed maps (Height under Normal, Glossiness under Roughness) are NOT 'wired'",
          not ({"Height", "Glossiness"} & sh_plan["wired"])
          and {"Height", "Glossiness"} <= set(sh_plan["by_type"]),
          f"wired={sorted(sh_plan['wired'])}")

    # An explicit direction tag in the FILENAME outranks the normal_direction argument: a
    # Normal_OpenGL map must never be green-flipped just because the caller asked for DirectX.
    def _normal_chain(mat):
        b = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        nm = b.inputs["Normal"].links[0].from_node
        return nm.inputs["Color"].links[0].from_node.type

    ogl = btk.create_pbr_material([gs_png("Normal_OpenGL")], name="GSNrmOGL",
                                  normal_direction="DirectX")
    check("explicit Normal_OpenGL is NOT flipped even when DirectX is requested",
          _normal_chain(ogl) == "TEX_IMAGE", f"{_normal_chain(ogl)}")
    dx = btk.create_pbr_material([gs_png("Normal_DirectX")], name="GSNrmDX",
                                 normal_direction="OpenGL")
    check("explicit Normal_DirectX IS flipped even when OpenGL is requested",
          _normal_chain(dx) == "COMBINE_COLOR", f"{_normal_chain(dx)}")
    amb = btk.create_pbr_material([gs_png("Normal")], name="GSNrmAmb",
                                  normal_direction="DirectX")
    check("ambiguous Normal follows the requested direction",
          _normal_chain(amb) == "COMBINE_COLOR", f"{_normal_chain(amb)}")

    # Precedence when a set carries more than one normal type: the explicit tag wins.
    # Selection used to try "Normal" first, so an ambiguous map shadowed a labeled one
    # and then followed the combo — the opposite of the rule the flip test above locks
    # in. Order comes from the shared ptk.MapRegistry.NORMAL_TYPES, so both DCCs agree.
    both = btk.create_pbr_material(
        [gs_png("Normal"), gs_png("Normal_OpenGL")], name="GSNrmBoth",
        normal_direction="DirectX")
    check("explicit Normal_OpenGL outranks an ambiguous Normal in the same set",
          _normal_chain(both) == "TEX_IMAGE", f"{_normal_chain(both)}")
    both_dx = btk.create_pbr_material(
        [gs_png("Normal"), gs_png("Normal_DirectX")], name="GSNrmBothDX",
        normal_direction="OpenGL")
    check("explicit Normal_DirectX outranks an ambiguous Normal in the same set",
          _normal_chain(both_dx) == "COMBINE_COLOR", f"{_normal_chain(both_dx)}")

    # config=None keeps the legacy 'packed wins' direction the old hardcoded block had.
    plan_pk = btk.MatUtils.resolve_pbr_plan(
        [gs_png("ORM"), gs_png("Metallic"), gs_png("Roughness")])
    check("resolve_pbr_plan config=None -> packed ORM supersedes its loose components",
          set(plan_pk["by_type"]) == {"ORM"} and set(plan_pk["dropped"]) == {"Metallic", "Roughness"},
          f"kept={sorted(plan_pk['by_type'])} dropped={sorted(plan_pk['dropped'])}")

    # ---- game shader: GameShader.create_network (the mayatk-mirroring engine) -
    gs = btk.GameShader()
    prog = []
    gs_files = [gs_png(m) for m in ("BaseColor", "Normal", "Roughness", "Metallic")]
    gmat = gs.create_network(gs_files, name="GSNet",
                            progress_callback=lambda p, m: prog.append(p))
    check("GameShader.create_network builds one named material",
          getattr(gmat, "name", None) == "GSNet")
    check("GameShader.create_network drives progress_callback to 100",
          prog and prog[-1] == 100, f"{prog}")
    check("GameShader.create_network mirrors mtk.GameShader's entry point",
          callable(getattr(btk.GameShader, "create_network", None)))

    # No name -> group by set -> one material per set (mayatk: group_by_set = not bool(name))
    gs_batch = gs.create_network([gs_set_png("stone", "BaseColor"), gs_set_png("stone", "Normal"),
                                  gs_set_png("metal", "BaseColor")])
    check("GameShader.create_network batches unnamed input into one material per set",
          isinstance(gs_batch, list) and len(gs_batch) == 2
          and {m.name for m in gs_batch if m} == {"stone", "metal"},
          f"{[getattr(m, 'name', m) for m in (gs_batch or [])]}")

    check("GameShader.create_network no textures -> None",
          gs.create_network([]) is None)

    # Affix is applied idempotently to the resolved set name (mayatk parity).
    gs_pre = gs.create_network([gs_set_png("slate", "BaseColor")], prefix="MAT_")
    check("GameShader.create_network applies a prefix to the resolved name",
          getattr(gs_pre, "name", "") == "MAT_slate", f"{getattr(gs_pre, 'name', gs_pre)}")

    # The map-writing pipeline (the capability the disabled Output Template / Ext combos gated):
    # a workflow profile PACKS new maps and wires the packed result. Needs an image library.
    if "PIL" in btk.ensure_image_deps():
        packed = gs.create_network(
            [gs_set_png("ore", m) for m in
             ("BaseColor", "Normal", "Roughness", "Metallic", "AmbientOcclusion")],
            name="GSPacked", config="Unreal Engine")
        loaded = {n.image.name for n in packed.node_tree.nodes
                  if n.type == "TEX_IMAGE"} if packed else set()
        check("GameShader.create_network workflow profile generates + wires a packed ORM",
              any("ORM" in n for n in loaded), f"{sorted(loaded)}")
        check("GameShader.create_network packed profile drops the loose components it packed",
              not any(("Roughness" in n or "Metallic" in n or "Occlusion" in n) for n in loaded),
              f"{sorted(loaded)}")
    else:
        lines.append("OK   GameShader workflow-profile packing SKIPPED (no PIL in this Blender)")

    shutil.rmtree(gs_tmp, ignore_errors=True)

    # ---- anim_utils ---------------------------------------------------------
    def key_obj(o, frames=(1, 11)):
        for f in frames:
            o.location.x = float(f)
            o.keyframe_insert(data_path="location", index=0, frame=f)

    def key_times(o):
        return sorted(k.co.x for fc in btk.get_fcurves(o) for k in fc.keyframe_points)

    reset()
    bpy.ops.mesh.primitive_cube_add(); a = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0)); b = bpy.context.active_object
    key_obj(a, (1, 11)); key_obj(b, (1, 21))

    btk.shift_keys(a, 5)
    check("shift_keys +5", key_times(a) == [6.0, 16.0], f"{key_times(a)}")

    btk.invert_keys(a)  # mirror about center (11) -> same frames swapped = same set
    check("invert_keys keeps range", key_times(a) == [6.0, 16.0])

    btk.snap_keys(a)
    check("snap_keys whole frames", all(t == int(t) for t in key_times(a)))

    btk.scale_keys(a, 2.0)  # about first key (6): 6,16 -> 6,26
    check("scale_keys x2 about first", key_times(a) == [6.0, 26.0], f"{key_times(a)}")

    btk.stagger_keys([a, b], spacing=5)  # a: 6-26; b starts at 31
    check("stagger_keys sequential", key_times(b)[0] == 31.0, f"{key_times(b)}")

    btk.set_stepped(a)
    interps = {k.interpolation for fc in btk.get_fcurves(a) for k in fc.keyframe_points}
    check("set_stepped CONSTANT", interps == {"CONSTANT"})

    btk.set_interpolation(a, "LINEAR")
    interps = {k.interpolation for fc in btk.get_fcurves(a) for k in fc.keyframe_points}
    check("set_interpolation LINEAR", interps == {"LINEAR"})
    btk.set_interpolation(a, "BEZIER", handle="VECTOR")
    htypes = {k.handle_left_type for fc in btk.get_fcurves(a) for k in fc.keyframe_points}
    check("set_interpolation BEZIER + handle VECTOR", htypes == {"VECTOR"}, f"{htypes}")

    # scale_keys about an explicit pivot (current-frame option): pivot=0 -> times double, then
    # restore (this fixture is shared sequentially by the tests below, so leave a=[6,26] intact).
    t0 = key_times(a)
    btk.scale_keys(a, 2.0, pivot=0.0)
    check("scale_keys about pivot=0 doubles times",
          key_times(a) == [t * 2 for t in t0], f"{t0}->{key_times(a)}")
    btk.scale_keys(a, 0.5, pivot=0.0)  # restore
    check("scale_keys restore", key_times(a) == t0, f"{key_times(a)}")

    rng = btk.fit_playback_range([a, b])
    sc = bpy.context.scene
    check("fit_playback_range", rng == (sc.frame_start, sc.frame_end) and sc.frame_start == 6,
          f"{rng}")

    # move_keys_to_frame: a=6-26, b=31-51 after stagger. Global (retain_spacing): earliest
    # key (6) lands on the target, b keeps its +25 offset; per-action: both start at target.
    moved = btk.move_keys_to_frame([a, b], frame=100, retain_spacing=True)
    check("move_keys_to_frame retain_spacing keeps offsets",
          moved == 2 and key_times(a)[0] == 100.0 and key_times(b)[0] == 125.0,
          f"a={key_times(a)} b={key_times(b)}")
    btk.move_keys_to_frame([a, b], frame=50, retain_spacing=False)
    check("move_keys_to_frame per-action aligns first keys",
          key_times(a)[0] == 50.0 and key_times(b)[0] == 50.0,
          f"a={key_times(a)} b={key_times(b)}")
    check("move_keys_to_frame keyless -> 0", btk.move_keys_to_frame([], frame=1) == 0)
    sc0 = sc.frame_current
    sc.frame_set(60)
    btk.move_keys_to_frame(a)  # frame defaults to the current frame
    check("move_keys_to_frame defaults to current frame", key_times(a)[0] == 60.0,
          f"{key_times(a)}")
    sc.frame_set(sc0)

    # adjust_key_spacing: a = 60,80 -> shift keys >= 70 by +5 (only the 80 moves)
    moved = btk.adjust_key_spacing(a, spacing=5, frame=70)
    check("adjust_key_spacing shifts only keys at/after the frame",
          moved == 1 and key_times(a) == [60.0, 85.0], f"{key_times(a)}")
    check("adjust_key_spacing no keys after frame -> 0",
          btk.adjust_key_spacing(a, spacing=5, frame=1000) == 0)

    # align_selected_keyframes: only SELECTED keys move
    for k in [k for fc in btk.get_fcurves(a) for k in fc.keyframe_points]:
        k.select_control_point = False
    last = max(
        (k for fc in btk.get_fcurves(a) for k in fc.keyframe_points),
        key=lambda k: k.co.x,
    )
    last.select_control_point = True
    moved = btk.align_selected_keyframes(a, target_frame=70)
    check("align_selected_keyframes moves only the selected key",
          moved == 1 and key_times(a) == [60.0, 70.0], f"{key_times(a)}")
    for k in [k for fc in btk.get_fcurves(a) for k in fc.keyframe_points]:
        k.select_control_point = False
    check("align_selected_keyframes none selected -> 0",
          btk.align_selected_keyframes(a) == 0)

    # intermediate keys: a = 60,70 -> sampled key on every frame between (61..69)
    added = btk.add_intermediate_keys(a)
    check("add_intermediate_keys fills the span", added == 9 and len(key_times(a)) == 11,
          f"added={added} n={len(key_times(a))}")
    removed = btk.remove_intermediate_keys(a)
    check("remove_intermediate_keys keeps endpoints",
          removed == 9 and key_times(a) == [60.0, 70.0], f"{key_times(a)}")

    # select_keys: range selects in-range, deselects the rest; None = all
    n = btk.select_keys(a, time=(65, 75))
    sel = [k.co.x for fc in btk.get_fcurves(a) for k in fc.keyframe_points
           if k.select_control_point]
    check("select_keys range", n == 1 and sel == [70.0], f"n={n} sel={sel}")
    check("select_keys all", btk.select_keys(a) == 2)

    # select_keys: current-frame-relative string scopes (mirrors Maya's cmb041 time-scope list)
    frame_before = sc.frame_current

    def selected_times():
        return sorted(k.co.x for fc in btk.get_fcurves(a) for k in fc.keyframe_points
                      if k.select_control_point)

    sc.frame_set(65)
    n = btk.select_keys(a, time="before")
    check("select_keys before", n == 1 and selected_times() == [60.0],
          f"n={n} sel={selected_times()}")
    n = btk.select_keys(a, time="after")
    check("select_keys after", n == 1 and selected_times() == [70.0],
          f"n={n} sel={selected_times()}")

    sc.frame_set(60)
    n = btk.select_keys(a, time="current")
    check("select_keys current", n == 1 and selected_times() == [60.0],
          f"n={n} sel={selected_times()}")
    n = btk.select_keys(a, time="before|current")
    check("select_keys before|current", n == 1 and selected_times() == [60.0],
          f"n={n} sel={selected_times()}")
    n = btk.select_keys(a, time="after|current")
    check("select_keys after|current", n == 2 and selected_times() == [60.0, 70.0],
          f"n={n} sel={selected_times()}")
    try:
        btk.select_keys(a, time="bogus")
        check("select_keys unknown scope raises", False)
    except ValueError:
        check("select_keys unknown scope raises", True)

    sc.frame_set(frame_before)

    # set_visibility_keys: keys hide_viewport/hide_render at the frame
    keyed = btk.set_visibility_keys(b, visible=False, frame=42)
    vis_curves = [
        fc for fc in btk.get_fcurves(b)
        if fc.data_path in ("hide_viewport", "hide_render")
    ]
    check("set_visibility_keys keys both hide props",
          keyed == [b] and len(vis_curves) == 2
          and all(any(k.co.x == 42.0 for k in fc.keyframe_points) for fc in vis_curves)
          and b.hide_render,
          f"curves={[fc.data_path for fc in vis_curves]}")

    action = btk.copy_keys(a)
    btk.paste_keys(b, action)
    check("copy/paste keys independent copy",
          key_times(b) == key_times(a)
          and b.animation_data.action is not a.animation_data.action)

    cleared = btk.delete_keys([a, b])
    check("delete_keys clears both", len(cleared) == 2 and a.animation_data is None)

    # ---- optimize_keys ------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(); c = bpy.context.active_object
    for f in (1, 10, 20):                       # static x = 5 (constant curve)
        c.location.x = 5.0; c.keyframe_insert("location", index=0, frame=f)
    for f, v in ((1, 0.0), (10, 0.0), (20, 0.0), (30, 5.0)):   # flat hold then ramp on z
        c.location.z = v; c.keyframe_insert("location", index=2, frame=f)
    stats = {}
    btk.optimize_keys(c, stats=stats)
    x_curves = [fc for fc in btk.get_fcurves(c)
                if fc.data_path == "location" and fc.array_index == 0]
    check("optimize_keys removes static curve (value preserved)",
          x_curves == [] and abs(c.location.x - 5.0) < 1e-6)
    z = [fc for fc in btk.get_fcurves(c)
         if fc.data_path == "location" and fc.array_index == 2][0]
    ztimes = sorted(k.co.x for k in z.keyframe_points)
    check("optimize_keys removes interior flat key (keeps boundaries)",
          ztimes == [1.0, 20.0, 30.0], f"{ztimes}")
    check("optimize_keys stats reduced",
          stats["curves_after"] < stats["curves_before"]
          and stats["keys_after"] < stats["keys_before"], f"{stats}")

    # ---- tie_keyframes ------------------------------------------------------
    reset()
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 30
    bpy.ops.mesh.primitive_cube_add(); d = bpy.context.active_object
    for f in (5, 15):
        d.location.x = float(f); d.keyframe_insert("location", index=0, frame=f)
    changed = btk.tie_keyframes(d)
    times = sorted(k.co.x for fc in btk.get_fcurves(d) for k in fc.keyframe_points)
    check("tie_keyframes adds range bookends",
          changed == 2 and times == [1.0, 5.0, 15.0, 30.0], f"{times}")
    changed2 = btk.tie_keyframes(d, untie=True)
    times2 = sorted(k.co.x for fc in btk.get_fcurves(d) for k in fc.keyframe_points)
    check("tie_keyframes untie removes only the bookends",
          changed2 == 2 and times2 == [5.0, 15.0], f"{times2}")

    # ---- get_animation_info / formatter ------------------------------------
    recs = btk.get_animation_info([d])
    check("get_animation_info record shape",
          len(recs) == 1 and recs[0]["name"] == d.name and recs[0]["keys"] == 2
          and recs[0]["start"] == 5.0 and recs[0]["end"] == 15.0, f"{recs}")
    html = btk.format_animation_info_html(recs)
    check("format_animation_info_html builds a table",
          "<table" in html and d.name in html and "Keys" in html)
    check("get_animation_info empty scope -> []", btk.get_animation_info([]) == [])
    check("format_animation_info_html empty -> ''",
          btk.format_animation_info_html([]) == "")

    # ---- bake_keys (native nla.bake; resolves a constraint) -----------------
    reset()
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 20
    bpy.ops.mesh.primitive_cube_add(); target = bpy.context.active_object
    target.name = "Target"
    for f in (1, 20):
        target.location.x = float(f) * 2; target.keyframe_insert("location", index=0, frame=f)
    bpy.ops.mesh.primitive_cube_add(location=(0, 5, 0)); follower = bpy.context.active_object
    follower.name = "Follower"
    con = follower.constraints.new("COPY_LOCATION"); con.target = target
    try:
        baked = btk.bake_keys([follower], clear_constraints=True)
        followed = bool(follower.animation_data and follower.animation_data.action)
        check("bake_keys bakes the constrained follower + clears the constraint",
              baked == [follower] and followed and len(follower.constraints) == 0)
    except RuntimeError as e:   # nla.bake context wall under --background (live-only)
        check("bake_keys (context-limited headless)", "context" in str(e).lower(), repr(e))

    # ---- bake_blend_shapes (sample a DRIVEN shape-key value -> keyframes) ----
    reset()
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 10
    bpy.ops.mesh.primitive_cube_add(); shp = bpy.context.active_object
    shp.name = "ShapeMesh"
    shp.shape_key_add(name="Basis")
    kb = shp.shape_key_add(name="Key1")
    # drive Key1.value from the scene's current frame so its value changes per frame (and is NOT
    # a plain fcurve -> nla.bake/keyframes wouldn't capture it without bake_blend_shapes)
    drv = kb.driver_add("value").driver
    drv.type = "SCRIPTED"
    drv.expression = "frame / 10.0"
    var = drv.variables.new(); var.name = "frame"; var.type = "SINGLE_PROP"
    var.targets[0].id_type = "SCENE"; var.targets[0].id = sc
    var.targets[0].data_path = "frame_current"
    baked = btk.bake_blend_shapes([shp], frame_range=(1, 10), step=1)
    sk = shp.data.shape_keys
    has_drivers = bool(sk.animation_data and sk.animation_data.drivers)
    has_action = bool(sk.animation_data and sk.animation_data.action)
    check("bake_blend_shapes baked the driven mesh", baked == [shp], f"{[o.name for o in baked]}")
    check("bake_blend_shapes removed the driver", not has_drivers)
    check("bake_blend_shapes created a baked action", has_action)
    # the baked keys must follow the driver (frame/10): frame 5 -> ~0.5, frame 10 -> ~1.0.
    # Read the EVALUATED value (slot-aware, version-agnostic — no reliance on action.fcurves).
    def _eval_key1(frame):
        sc.frame_set(frame)
        dg = bpy.context.evaluated_depsgraph_get()
        return shp.evaluated_get(dg).data.shape_keys.key_blocks["Key1"].value
    v5, v10 = _eval_key1(5), _eval_key1(10)
    check("bake_blend_shapes captured driver values (f5~0.5, f10~1.0)",
          abs(v5 - 0.5) < 0.05 and abs(v10 - 1.0) < 0.05, f"v5={v5:.3f} v10={v10:.3f}")

    # no driven/animated shape keys -> no-op (empty)
    reset()
    bpy.ops.mesh.primitive_cube_add(); plain = bpy.context.active_object
    check("bake_blend_shapes no shape keys -> []", btk.bake_blend_shapes([plain]) == [])

    # ---- core recent files --------------------------------------------------
    files = btk.get_recent_files()
    check("get_recent_files returns list", isinstance(files, list))
    autosaves = btk.get_recent_autosave(filter_time=24)
    check("get_recent_autosave (path, stamp) pairs",
          isinstance(autosaves, list)
          and all(len(t) == 2 for t in autosaves))

    # ---- core scene info / cleanup -----------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(); cube = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    bpy.ops.object.light_add(type="POINT", location=(0, 0, 5))
    bpy.ops.object.camera_add(location=(0, -5, 0))
    info = btk.get_scene_info()
    check("get_scene_info counts objects",
          info["objects"] == 4 and info["meshes"] == 2, f"{info}")
    check("get_scene_info mesh stats (2 cubes)",
          info["vertices"] == 16 and info["faces"] == 12 and info["triangles"] == 24,
          f"{info}")
    check("get_scene_info lights + cameras", info["lights"] == 1 and info["cameras"] == 1)
    check("get_scene_info no-material meshes", info["meshesWithoutMaterial"] == 2)
    one = btk.get_scene_info([cube])
    check("get_scene_info scoped to one object", one["objects"] == 1 and one["meshes"] == 1)
    html = btk.format_scene_info_html(info)
    check("format_scene_info_html builds report",
          "<h3>Scene Info" in html and "Triangles" in html and "Objects by type" in html)
    check("format_scene_info_html empty -> ''", btk.format_scene_info_html({}) == "")

    # ---- analyze_scene (budgeted, sectioned audit) -------------------------
    reset()
    # one small cube (under budget) + one dense ico sphere (over the Generic 100k? no -> use a
    # tiny generic budget via the adaptive path on a big object). Build a clearly-over-budget mesh.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=6)  # ~80k tris
    big = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
    rep = btk.analyze_scene(adaptive=False, sections=("summary", "pareto", "offenders", "assumptions"))
    check("analyze_scene returns only requested sections",
          set(rep) == {"summary", "pareto", "offenders", "assumptions"}, f"{list(rep)}")
    check("analyze_scene summary names the Generic profile", "Generic" in rep["summary"])
    check("analyze_scene pareto lists the dense mesh", big.name in rep["pareto"])
    rep_a = btk.analyze_scene(adaptive=True, sections=("summary",))
    check("analyze_scene adaptive profile labelled", "Adaptive" in rep_a["summary"])
    # textures section: a file image bucketed by dimension
    img = bpy.data.images.new("Tex4K", width=4096, height=4096); img.source = "FILE"
    img.filepath = "//missing_tex_4k.png"
    rep_t = btk.analyze_scene(sections=("textures", "pipeline"))
    check("analyze_scene textures section present", "Textures" in rep_t["textures"])
    check("analyze_scene pipeline flags missing texture",
          "missing_tex_4k" in rep_t["pipeline"] or "resolve" in rep_t["pipeline"])

    orphan_me = bpy.data.meshes.new("OrphanMesh")          # 0 users -> purged
    keep_me = bpy.data.meshes.new("KeepMesh"); keep_me.use_fake_user = True  # survives
    removed = btk.cleanup_scene(quiet=True)
    mesh_names = [m.name for m in bpy.data.meshes]
    check("cleanup_scene purges orphans, keeps fake-user + in-use",
          "OrphanMesh" not in mesh_names and "KeepMesh" in mesh_names
          and big.data.name in mesh_names and removed.get("meshes", 0) >= 1, f"{removed}")

    # ---- fix_color_spaces ---------------------------------------------------
    def _mk_img(name, filepath, space):
        im = bpy.data.images.new(name, 4, 4)
        im.source = "FILE"
        im.filepath = filepath
        im.colorspace_settings.name = space
        return im

    # Data maps default-wrong as sRGB; a correct color map; an unrecognized name.
    nrm = _mk_img("nrm", "//tex/rock_Normal.png", "sRGB")
    rgh = _mk_img("rgh", "//tex/rock_Roughness.png", "sRGB")
    alb = _mk_img("alb", "//tex/rock_BaseColor.png", "sRGB")
    unknown = _mk_img("unknown", "//tex/randomthing.png", "sRGB")

    changed = btk.fix_color_spaces(images=[nrm, rgh, alb, unknown])
    check("fix_color_spaces sets data maps -> Non-Color",
          nrm.colorspace_settings.name == "Non-Color"
          and rgh.colorspace_settings.name == "Non-Color", f"{changed}")
    check("fix_color_spaces leaves correct color map", alb.colorspace_settings.name == "sRGB")
    check("fix_color_spaces reports only genuine changes",
          set(changed) == {nrm.name, rgh.name}, f"{set(changed)}")
    check("fix_color_spaces ignores unrecognized map",
          unknown.colorspace_settings.name == "sRGB" and unknown.name not in changed)

    # dry_run reports but doesn't write
    nrm2 = _mk_img("nrm2", "//tex/wall_Normal.png", "sRGB")
    dr = btk.fix_color_spaces(images=[nrm2], dry_run=True)
    check("fix_color_spaces dry_run reports without writing",
          nrm2.name in dr and nrm2.colorspace_settings.name == "sRGB")

    # a color map wrongly tagged Non-Color is corrected back to sRGB
    alb2 = _mk_img("alb2", "//tex/wall_Albedo.png", "Non-Color")
    btk.fix_color_spaces(images=[alb2])
    check("fix_color_spaces corrects color map -> sRGB", alb2.colorspace_settings.name == "sRGB")

    # scan-all path (images=None) covers every FILE image
    nrm3 = _mk_img("nrm3", "//tex/floor_Normal.png", "sRGB")
    allchanged = btk.fix_color_spaces()
    check("fix_color_spaces scan-all path fixes new data map",
          nrm3.colorspace_settings.name == "Non-Color" and nrm3.name in allchanged, f"{allchanged}")

    # ---- configure_render_output (rendering playblast format engine) --------
    sc = bpy.context.scene
    btk.configure_render_output(sc, file_format="PNG")
    check("configure_render_output PNG", sc.render.image_settings.file_format == "PNG")
    btk.configure_render_output(sc, file_format="FFMPEG", container="MPEG4", codec="H264", quality=90)
    check("configure_render_output FFMPEG MP4/H264 + quality->CRF",
          sc.render.image_settings.file_format == "FFMPEG"
          and sc.render.ffmpeg.format == "MPEG4" and sc.render.ffmpeg.codec == "H264"
          and sc.render.image_settings.quality == 90
          and sc.render.ffmpeg.constant_rate_factor == "HIGH",
          f"crf={sc.render.ffmpeg.constant_rate_factor}")
    btk.configure_render_output(sc, file_format="JPEG", quality=50)
    check("configure_render_output JPEG + quality",
          sc.render.image_settings.file_format == "JPEG" and sc.render.image_settings.quality == 50)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===MAT-ANIM-UTILS===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
