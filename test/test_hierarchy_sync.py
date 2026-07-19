"""blendertk Hierarchy Sync engine headless test — mirror of mayatk's ``test_hierarchy_sync``.

Covers the bpy-backed engine: path building, the pure diff-detection passes, stub creation,
quarantine (with the animation-ancestor / -descendant / -own skip guards), reparent repair (with
the keyed-transform shift guard + constraint-safe move + empty-parent reference-preservation), and
the full Pull feature (``ObjectSwapper`` — Add to Scene / Merge Hierarchies / Pull Children / dry
run). The Qt-only tree_utils extraction is covered by ``test_hierarchy_tree_utils.py`` (.venv).

Run: blender --background --factory-startup --python blendertk/test/test_hierarchy_sync.py
"""
import sys, os, tempfile, shutil, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


tmp = tempfile.mkdtemp(prefix="hier_mgr_test_")
try:
    import bpy
    import blendertk as btk
    from blendertk.env_utils.hierarchy_sync._hierarchy_sync import (
        HierarchySync,
        HierarchyMapBuilder,
        ObjectSwapper,
        build_path,
        should_keep_node_by_type,
        stage_reference_blend,
    )

    SCOLL = lambda: bpy.context.scene.collection

    def reset():
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def empty(name, parent=None, loc=(0.0, 0.0, 0.0)):
        o = bpy.data.objects.new(name, None)
        o.location = loc
        SCOLL().objects.link(o)
        if parent is not None:
            o.parent = parent
        return o

    def cube(name, parent=None):
        me = bpy.data.meshes.new(name + "Mesh")
        import bmesh

        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        SCOLL().objects.link(o)
        if parent is not None:
            o.parent = parent
        return o

    def key_loc(o):
        o.location = (0.0, 0.0, 0.0)
        o.keyframe_insert("location", frame=1)
        o.location = (0.0, 0.0, 5.0)
        o.keyframe_insert("location", frame=10)

    def pmap(objs):
        return {build_path(o): o for o in objs}

    # ------------------------------------------------------------------ build_path / filters
    reset()
    g = empty("Grp")
    c = empty("Child", parent=g)
    check("build_path joins the ancestor chain", build_path(c) == "Grp|Child")
    check("build_path of a root is the bare name", build_path(g) == "Grp")
    m = cube("Geo")
    check("should_keep_node_by_type excludes matched type", should_keep_node_by_type(m, ["MESH"], exclude=True) is False)
    check("should_keep_node_by_type keeps unmatched type", should_keep_node_by_type(g, ["MESH"], exclude=True) is True)

    # ------------------------------------------------------------------ camera/light type filters
    # Locks the engine params behind the panel's 'Filter Cameras' / 'Filter Lights'
    # diff options (mirror of mayatk's test_filter_cameras_and_lights).
    reset()
    keep = empty("KeepGrp")
    cam_o = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    SCOLL().objects.link(cam_o)
    light_o = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    SCOLL().objects.link(light_o)
    ref_o = empty("RefOnly")
    check("should_keep_node_by_type excludes CAMERA", should_keep_node_by_type(cam_o, ["CAMERA"], exclude=True) is False)
    check("should_keep_node_by_type excludes LIGHT", should_keep_node_by_type(light_o, ["LIGHT"], exclude=True) is False)

    hm = HierarchySync(fuzzy_matching=False, dry_run=True)
    d_on = hm.analyze_hierarchies(
        [keep, cam_o, light_o], [ref_o], filter_meshes=False, filter_cameras=True, filter_lights=True
    )
    check("filter_cameras/lights drop cameras+lights from extras",
          "Cam" not in d_on["extra"] and "Lamp" not in d_on["extra"] and "KeepGrp" in d_on["extra"],
          str(d_on.get("extra")))
    d_off = hm.analyze_hierarchies(
        [keep, cam_o, light_o], [ref_o], filter_meshes=False, filter_cameras=False, filter_lights=False
    )
    check("filters off keep cameras+lights in extras",
          "Cam" in d_off["extra"] and "Lamp" in d_off["extra"],
          str(d_off.get("extra")))

    # ------------------------------------------------------------------ pure detection passes
    hm = HierarchySync(dry_run=True)
    rep, rem_m, rem_e = hm._detect_reparented(["Grp|Leaf"], ["Other|Leaf"])
    check("_detect_reparented pairs same-leaf across parents",
          len(rep) == 1 and rep[0]["reference_path"] == "Grp|Leaf" and rep[0]["current_path"] == "Other|Leaf"
          and not rem_m and not rem_e)

    fz, rem_m, rem_e = hm._detect_fuzzy_renames(["Grp|Switchh"], ["Grp|Switch"])
    check("_detect_fuzzy_renames pairs near-identical leaves",
          len(fz) == 1 and fz[0]["target_name"] == "Grp|Switchh" and fz[0]["current_name"] == "Grp|Switch")

    sfx, rem_m, rem_e = hm._detect_suffix_flattening(["Grp|Boost"], ["Grp|Console_Boost"])
    check("_detect_suffix_flattening pairs FBX name-flattening",
          len(sfx) == 1 and sfx[0]["target_name"] == "Grp|Boost" and sfx[0]["current_name"] == "Grp|Console_Boost")

    # ------------------------------------------------------------------ create_stubs
    reset()
    hm = HierarchySync(dry_run=False)
    created = hm.create_stubs(["A|B|Leaf"])
    a = bpy.data.objects.get("A")
    leaf = bpy.data.objects.get("Leaf")
    check("create_stubs builds the full parent chain",
          a is not None and leaf is not None and build_path(leaf) == "A|B|Leaf", str(created))
    check("create_stubs tags stubs", leaf.get(HierarchySync.STUB_ATTR) is True)
    # dry-run doesn't touch the scene
    hm2 = HierarchySync(dry_run=True)
    before = len(bpy.data.objects)
    hm2.create_stubs(["X|Y"])
    check("create_stubs dry-run makes no objects", len(bpy.data.objects) == before)

    # ------------------------------------------------------------------ quarantine animation guards
    def quarantine_case(builder, path, skip):
        reset()
        hm = HierarchySync(dry_run=False)
        node, objs = builder()
        hm.current_scene_path_map = pmap(objs)
        moved = hm.quarantine_extras(paths=[path], skip_animated=skip)
        return node, moved

    # (a) node under an ANIMATED ANCESTOR is skipped (the audit's new ancestor check).
    def _b_anc():
        ap = empty("AnimParent"); key_loc(ap)
        prop = empty("Prop", parent=ap)
        return prop, [ap, prop]

    node, moved = quarantine_case(_b_anc, "AnimParent|Prop", skip=True)
    check("quarantine skips a node under an animated ancestor", node.parent.name == "AnimParent" and not moved)
    node, moved = quarantine_case(_b_anc, "AnimParent|Prop", skip=False)
    check("quarantine moves it when skip_animated off", node.parent.name == "_QUARANTINE")

    # (b) node with an ANIMATED DESCENDANT is skipped.
    def _b_desc():
        grp = empty("Grp")
        kid = empty("Kid", parent=grp); key_loc(kid)
        return grp, [grp, kid]

    node, moved = quarantine_case(_b_desc, "Grp", skip=True)
    check("quarantine skips a node with an animated descendant", node.parent is None and not moved)

    # (c) node with its OWN animation is skipped; a plain extra moves.
    def _b_own():
        grp = empty("Grp"); key_loc(grp)
        return grp, [grp]

    node, moved = quarantine_case(_b_own, "Grp", skip=True)
    check("quarantine skips a self-animated node", node.parent is None and not moved)

    def _b_plain():
        grp = empty("Grp")
        return grp, [grp]

    node, moved = quarantine_case(_b_plain, "Grp", skip=True)
    check("quarantine moves a plain extra", node.parent is not None and node.parent.name == "_QUARANTINE")

    # ------------------------------------------------------------------ fix_reparented guards
    def reparent_case(builder, skip):
        reset()
        hm = HierarchySync(dry_run=False)
        node, objs, item = builder()
        hm.current_scene_path_map = pmap(objs)
        fixed = hm.fix_reparented([item], skip_animated=skip)
        return node, fixed

    # (a) keyed local transform → skipped (would shift world motion).
    def _r_keyed():
        wp = empty("WrongParent")
        mv = empty("Moved", parent=wp); key_loc(mv)
        return mv, [wp, mv], {"current_path": "WrongParent|Moved", "reference_path": "RightParent|Moved"}

    node, fixed = reparent_case(_r_keyed, skip=True)
    check("fix_reparented skips a keyed-transform node", node.parent.name == "WrongParent" and not fixed)
    node, fixed = reparent_case(_r_keyed, skip=False)
    check("fix_reparented moves the keyed node when skip off", node.parent.name == "RightParent")

    # (b) constraint-only node still moves (constraints are parent-agnostic, like Maya).
    def _r_constraint():
        wp = empty("WrongParent")
        tgt = empty("Tgt")
        mv = empty("Moved", parent=wp)
        con = mv.constraints.new("COPY_LOCATION")
        con.target = tgt
        return mv, [wp, tgt, mv], {"current_path": "WrongParent|Moved", "reference_path": "RightParent|Moved"}

    node, fixed = reparent_case(_r_constraint, skip=True)
    check("fix_reparented moves a constraint-only node", node.parent.name == "RightParent" and fixed)

    # (c) empty source parent that exists in the reference is PRESERVED (audit fix).
    reset()
    hm = HierarchySync(dry_run=False)
    sp = empty("SrcParent")
    mv = empty("Moved", parent=sp)
    hm.current_scene_path_map = pmap([sp, mv])
    hm.reference_scene_path_map = {"SrcParent": sp}  # SrcParent is a real reference node
    hm.fix_reparented([{"current_path": "SrcParent|Moved", "reference_path": "Dest|Moved"}], skip_animated=True)
    check("fix_reparented preserves an empty parent that exists in the reference",
          bpy.data.objects.get("SrcParent") is not None)

    # a source parent NOT in the reference is cleaned up.
    reset()
    hm = HierarchySync(dry_run=False)
    sp = empty("StubParent")
    HierarchySync._finalize_stub_node(sp)
    mv = empty("Moved", parent=sp)
    hm.current_scene_path_map = pmap([sp, mv])
    hm.reference_scene_path_map = {}
    hm.fix_reparented([{"current_path": "StubParent|Moved", "reference_path": "Dest|Moved"}], skip_animated=True)
    check("fix_reparented cleans up an orphaned empty stub parent", bpy.data.objects.get("StubParent") is None)

    # ------------------------------------------------------------------ fix_fuzzy_renames
    reset()
    hm = HierarchySync(dry_run=False)
    grp = empty("Grp")
    bad = empty("Switchh", parent=grp)
    hm.current_scene_path_map = pmap([grp, bad])
    hm.fix_fuzzy_renames([{"current_name": "Grp|Switchh", "target_name": "Grp|Switch"}])
    check("fix_fuzzy_renames renames to the reference leaf", bad.name == "Switch")

    # ------------------------------------------------------------------ Pull (ObjectSwapper)
    # Author a reference .blend: Root(empty at 5,0,0) > Body(mesh, material + keyed location).
    src = os.path.join(tmp, "ref.blend")
    reset()
    root = empty("Root", loc=(5.0, 0.0, 0.0))
    body = cube("Body", parent=root)
    body.location = (0.0, 2.0, 0.0)
    mat = bpy.data.materials.new("BodyMat")
    body.data.materials.append(mat)
    key_loc(body)
    body.location = (0.0, 2.0, 0.0)
    bpy.ops.wm.save_as_mainfile(filepath=src)

    def link_ref():
        btk.link_blend_file(src, link=True, instance=False)
        return {
            build_path(o): o
            for o in bpy.data.objects
            if o.library is not None
        }

    # (1) Add to Scene: single object appended local, grafted under a rebuilt Root stub.
    reset()
    ref_map = link_ref()
    sw = ObjectSwapper(dry_run=False, pull_mode="Add to Scene", pull_children=False)
    ok = sw.pull_objects_from_reference(["Root|Body"], src, ref_map)
    pulled = next((o for o in bpy.context.scene.objects if o.library is None and o.name.split(".")[0] == "Body"), None)
    check("pull Add-to-Scene returns success", ok)
    check("pulled object is a LOCAL copy", pulled is not None and pulled.library is None and pulled.data.library is None)
    check("pulled object carries its material", pulled is not None and any(ms.material and ms.material.library is None for ms in pulled.material_slots))
    check("pulled object carries its animation", pulled is not None and pulled.animation_data and pulled.animation_data.action)
    check("pulled object grafted under a local Root", pulled is not None and pulled.parent is not None and pulled.parent.name.split(".")[0] == "Root" and pulled.parent.library is None)

    # (2) dry-run pulls nothing.
    reset()
    ref_map = link_ref()
    sw = ObjectSwapper(dry_run=True, pull_mode="Add to Scene")
    ok = sw.pull_objects_from_reference(["Root|Body"], src, ref_map)
    check("pull dry-run reports success but changes nothing",
          ok and not any(o.library is None and o.name.split(".")[0] == "Body" for o in bpy.context.scene.objects))

    # (3) Merge Hierarchies: replaces an existing stub at the target path (exact name, no dupe).
    reset()
    hm = HierarchySync(dry_run=False)
    hm.create_stubs(["Root|Body"])  # a stub Body under a stub Root
    ref_map = link_ref()
    sw = ObjectSwapper(dry_run=False, pull_mode="Merge Hierarchies", pull_children=False)
    sw.pull_objects_from_reference(["Root|Body"], src, ref_map)
    local_bodies = [o for o in bpy.context.scene.objects if o.library is None and o.name == "Body"]
    check("pull Merge takes the exact clean name (no .001)", len(local_bodies) == 1)
    check("pull Merge replaced the stub with real geometry", local_bodies and local_bodies[0].type == "MESH")

    # (3b) Merge PRESERVES an existing that carries animation (no silent data loss).
    reset()
    hm = HierarchySync(dry_run=False)
    hm.create_stubs(["Root"])
    existing_body = cube("Body", parent=bpy.data.objects["Root"])
    key_loc(existing_body)  # the local Body has animation the reference lacks
    ref_map = link_ref()
    sw = ObjectSwapper(dry_run=False, pull_mode="Merge Hierarchies", pull_children=False)
    ok = sw.pull_objects_from_reference(["Root|Body"], src, ref_map)
    check("pull Merge preserves an animated existing (skips, no data loss)",
          not ok and existing_body.name == "Body" and existing_body.animation_data
          and existing_body.animation_data.action is not None)

    # (4) Pull Children: the whole subtree comes in under one graft.
    reset()
    ref_map = link_ref()
    sw = ObjectSwapper(dry_run=False, pull_mode="Add to Scene", pull_children=True)
    sw.pull_objects_from_reference(["Root"], src, ref_map)
    local_root = next((o for o in bpy.context.scene.objects if o.library is None and o.name.split(".")[0] == "Root"), None)
    check("pull children brings the subtree",
          local_root is not None and any(ch.name.split(".")[0] == "Body" for ch in local_root.children))

    # ------------------------------------------------------------------ FBX reference staging
    # A .fbx reference must work exactly like a .blend one (mayatk's first-class reference format).
    # Author an FBX, then stage → temp .blend → link → pull, and verify the user's file stays clean.
    reset()
    empty("FbxGrp")
    fbody = cube("FbxBody", parent=bpy.data.objects["FbxGrp"])
    fbody.data.materials.append(bpy.data.materials.new("FbxMat"))
    key_loc(fbody)  # animation -> the FBX importer creates a fake-user "Take 001" action
    fbx_path = os.path.join(tmp, "asm.fbx")
    bpy.ops.export_scene.fbx(filepath=fbx_path, use_selection=False, bake_anim=True)

    check(".blend reference passes straight through", stage_reference_blend(os.path.join(tmp, "x.blend")) == (os.path.join(tmp, "x.blend"), None))
    check("unsupported format is rejected", stage_reference_blend(os.path.join(tmp, "x.obj")) == (None, None))

    reset()
    empty("UserObj")  # pre-existing user content that staging must not disturb
    before_names = {o.name for o in bpy.data.objects}
    before_actions = {a.name for a in bpy.data.actions}
    before_materials = {m.name for m in bpy.data.materials}
    blend_path, temp_blend = stage_reference_blend(fbx_path)
    check("stage_reference_blend converts FBX to a temp .blend",
          bool(blend_path) and blend_path == temp_blend and os.path.isfile(temp_blend))
    # No leftover datablocks of ANY type — objects, materials, OR the fake-user actions the FBX
    # importer creates (which retain a user and leak unless the fake user is cleared first).
    check("staging leaves the user's file clean (no leftover import datablocks)",
          {o.name for o in bpy.data.objects} == before_names
          and {m.name for m in bpy.data.materials} == before_materials
          and {a.name for a in bpy.data.actions} == before_actions)

    reset()
    n = btk.link_blend_file(temp_blend, link=True, instance=False)
    fbx_ref_map = {build_path(o): o for o in bpy.data.objects if o.library is not None}
    check("FBX-staged reference preserves the hierarchy", "FbxGrp|FbxBody" in fbx_ref_map)
    sw = ObjectSwapper(dry_run=False, pull_mode="Add to Scene", pull_children=False)
    sw.pull_objects_from_reference(["FbxGrp|FbxBody"], temp_blend, fbx_ref_map)
    fpulled = next((o for o in bpy.context.scene.objects if o.library is None and o.name.split(".")[0] == "FbxBody"), None)
    check("pull from an FBX-staged reference yields a local object with its material",
          fpulled is not None and fpulled.library is None and any(ms.material for ms in fpulled.material_slots))
    if os.path.isfile(temp_blend):
        os.remove(temp_blend)

    # ------------------------------------------------------------------ FBX staging: name collision
    # Regression for the .001-suffix bug: a Hierarchy Sync reference by design shares object names
    # with the current scene. The old in-process staging imported the FBX into the LIVE scene, so
    # every colliding object was suffixed .001 and that suffix was baked into the staged .blend —
    # shifting every reference path and detonating the diff into a fuzzy-match explosion. Staging in
    # a fresh headless Blender must keep the names CLEAN even under a full collision.
    reset()
    cg = empty("ColGrp")
    cube("ColBody", parent=cg)
    collide_fbx = os.path.join(tmp, "collide.fbx")
    bpy.ops.export_scene.fbx(filepath=collide_fbx, use_selection=False)

    reset()
    cg2 = empty("ColGrp")  # SAME names as the reference -> forces a .001 collision on any live import
    cube("ColBody", parent=cg2)
    _, collide_temp = stage_reference_blend(collide_fbx)
    check("collision staging produced a temp .blend", bool(collide_temp) and os.path.isfile(collide_temp))
    if collide_temp:
        n_c = btk.link_blend_file(collide_temp, link=True, instance=False)
        collide_ref = [o for o in bpy.data.objects if o.library is not None]
        ref_paths = sorted(build_path(o) for o in collide_ref)
        check("staged reference names are CLEAN under collision (no .001)",
              ref_paths == ["ColGrp", "ColGrp|ColBody"], detail=str(ref_paths))
        # The whole point: an identical hierarchy now diffs to ZERO fuzzy matches (was N pre-fix).
        current_local = [o for o in bpy.context.scene.objects if o.library is None]
        cdiff = HierarchySync(fuzzy_matching=True, dry_run=True).analyze_hierarchies(
            current_local, collide_ref, filter_meshes=False)
        check("collision diff finds 0 fuzzy matches (was a fuzzy explosion pre-fix)",
              len(cdiff.get("fuzzy_matches", [])) == 0, detail=str(len(cdiff.get("fuzzy_matches", []))))
        check("collision diff finds 0 missing / 0 extra",
              not cdiff.get("missing") and not cdiff.get("extra"))
        os.remove(collide_temp)

    # ------------------------------------------------------------------ delete_objects: cascade
    # Maya parity (slot b018): cmds.delete removes the whole subtree, but a bare
    # bpy.data.objects.remove() re-roots the children — leaving them in the scene (and shifting
    # their world transform once the parent's transform vanishes).
    from blendertk.env_utils.hierarchy_sync._hierarchy_sync import delete_objects

    reset()
    droot = empty("DelRoot")
    dkid = empty("DelKid", parent=droot)
    empty("DelGrand", parent=dkid)
    empty("DelSolo")
    deleted = delete_objects([droot])
    check("delete_objects removes the whole subtree",
          set(deleted) == {"DelRoot", "DelKid", "DelGrand"}, detail=str(sorted(deleted)))
    check("subtree objects are gone from the blend data",
          all(bpy.data.objects.get(n) is None for n in ("DelRoot", "DelKid", "DelGrand")))
    check("unrelated object survives", bpy.data.objects.get("DelSolo") is not None)

    # An overlapping selection (parent AND child both selected) must not double-delete.
    r2 = empty("DelRoot2")
    empty("DelKid2", parent=r2)
    deleted = delete_objects([r2, bpy.data.objects["DelKid2"]])
    check("delete_objects dedups an overlapping selection",
          sorted(deleted) == ["DelKid2", "DelRoot2"], detail=str(sorted(deleted)))

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
