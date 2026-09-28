"""blendertk RizomUV round-trip plumbing test (no RizomUV executable required).

Run: blender --background --factory-startup --python blendertk/test/test_rizom_roundtrip.py

Exercises ``RizomUVBridge.process_with_rizomuv`` end-to-end with the headless RizomUV run
(``_execute_uv_script``) stubbed out, so it validates the Blender-specific half of the round-trip
(the part that could actually break) without needing RizomUV installed:

  export __RZTMP copies -> FBX -> [stubbed RizomUV] -> re-import -> map imports back to originals ->
  transfer UVs onto the originals -> clean up every temp object.

Two stubs stand in for RizomUV:
  * a no-op (leaves the exported FBX untouched) -> the originals' UVs must come back UNCHANGED,
    which is the strict test of loop-order fidelity through the FBX round-trip + the per-loop
    UV copy (each loop is pre-seeded with a UNIQUE uv, so any reordering is detectable);
  * a simulator that re-imports the FBX, shifts every UV by a known delta, and re-exports ->
    the delta must propagate back onto the originals (a real UV change flows through).

The actual RizomUV invocation + every Lua preset is covered by ``mayatk/test/rizom_headless_probe.py``
(it needs the external executable); the script-construction / version-gating half is DCC-agnostic
and verified separately under the workspace venv.
"""
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    import bpy
    from blendertk.env_utils.fbx_utils import FbxUtils
    from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    def seed_unique_uvs(obj):
        """Give obj's active UV layer a unique-per-loop coordinate so any loop reorder is visible."""
        mesh = obj.data
        uv = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
        n = len(uv.data)
        for i in range(n):
            uv.data[i].uv = ((i % 97) / 97.0, (i * 13 % 89) / 89.0)
        mesh.update()

    def uv_snapshot(obj):
        return [tuple(d.uv) for d in obj.data.uv_layers.active.data]

    def max_uv_diff(a, b, offset=(0.0, 0.0)):
        if len(a) != len(b):
            return float("inf")
        m = 0.0
        for (au, av), (bu, bv) in zip(a, b):
            m = max(m, abs(au - (bu + offset[0])), abs(av - (bv + offset[1])))
        return m

    def temp_leftovers():
        return [o.name for o in bpy.data.objects if "__RZTMP" in o.name]

    # -----------------------------------------------------------------------------
    # 1) Identity round-trip (no-op RizomUV): UVs must survive unchanged.
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "RZ_Cube"
    seed_unique_uvs(cube)
    before = uv_snapshot(cube)
    vcount_before = len(cube.data.vertices)
    baseline_objs = set(bpy.data.objects.keys())

    bridge = RizomUVBridge(rizom_path="not-used.exe")
    bridge._execute_uv_script = lambda: None  # stub RizomUV: leave the exported FBX as-is
    bridge.process_with_rizomuv([cube], preset="pack")

    after = uv_snapshot(cube)
    diff = max_uv_diff(before, after)
    check("identity: UVs unchanged through the round-trip (loop order preserved)",
          diff < 1e-4, f"max_uv_diff={diff:.2e}")
    check("identity: original mesh intact (vert count unchanged)",
          len(cube.data.vertices) == vcount_before)
    check("identity: no __RZTMP temp objects leaked", not temp_leftovers(), str(temp_leftovers()))
    check("identity: no orphan objects left (imports cleaned up)",
          set(bpy.data.objects.keys()) == baseline_objs,
          str(set(bpy.data.objects.keys()) ^ baseline_objs))

    # -----------------------------------------------------------------------------
    # 2) Change propagation (simulated RizomUV shifts every UV by a known delta).
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "RZ_Cube2"
    seed_unique_uvs(cube)
    before = uv_snapshot(cube)
    DELTA = (0.3, 0.15)

    bridge = RizomUVBridge(rizom_path="not-used.exe")

    def fake_rizom():
        """Stand in for RizomUV: re-import the exported FBX, shift UVs, re-export over it."""
        objs = FbxUtils.import_fbx(bridge.export_path)
        meshes = [o for o in objs if getattr(o, "type", None) == "MESH"]
        for o in meshes:
            uv = o.data.uv_layers.active
            for d in uv.data:
                d.uv = (d.uv[0] + DELTA[0], d.uv[1] + DELTA[1])
            o.data.update()
        FbxUtils.export(filepath=bridge.export_path, objects=meshes, selection_only=True)
        for o in meshes:
            m = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if getattr(m, "users", 1) == 0:
                bpy.data.meshes.remove(m)

    bridge._execute_uv_script = fake_rizom
    bridge.process_with_rizomuv([cube], preset="unwrap_hard")

    after = uv_snapshot(cube)
    diff = max_uv_diff(after, before, offset=DELTA)  # after ≈ before + DELTA
    check("propagation: the UV delta transferred back onto the original",
          diff < 1e-4, f"max_uv_diff_vs_shifted={diff:.2e}")
    check("propagation: no __RZTMP temp objects leaked", not temp_leftovers(), str(temp_leftovers()))

    # -----------------------------------------------------------------------------
    # 3) Multi-object mapping: two meshes with DISTINCT UVs must not cross-wire.
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    a = bpy.context.active_object
    a.name = "RZ_A"
    bpy.ops.mesh.primitive_ico_sphere_add(location=(4, 0, 0))  # different topology than the cube
    b = bpy.context.active_object
    b.name = "RZ_B"
    seed_unique_uvs(a)
    seed_unique_uvs(b)
    a_before, b_before = uv_snapshot(a), uv_snapshot(b)

    bridge = RizomUVBridge(rizom_path="not-used.exe")
    bridge._execute_uv_script = lambda: None
    bridge.process_with_rizomuv([a, b], preset="optimize")

    a_diff = max_uv_diff(a_before, uv_snapshot(a))
    b_diff = max_uv_diff(b_before, uv_snapshot(b))
    check("multi: object A kept its own UVs (no cross-wiring)", a_diff < 1e-4, f"A diff={a_diff:.2e}")
    check("multi: object B kept its own UVs (no cross-wiring)", b_diff < 1e-4, f"B diff={b_diff:.2e}")
    check("multi: no __RZTMP temp objects leaked", not temp_leftovers(), str(temp_leftovers()))

    # -----------------------------------------------------------------------------
    # 4) Datablock hygiene: the import must not leak orphan materials/images.
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "RZ_Mat_Cube"
    seed_unique_uvs(cube)
    cube.data.materials.append(bpy.data.materials.new("RZ_Mat"))
    mats_before = len(bpy.data.materials)
    imgs_before = len(bpy.data.images)

    bridge = RizomUVBridge(rizom_path="not-used.exe")
    bridge._execute_uv_script = lambda: None  # FBX still carries the material -> re-import dups it
    bridge.process_with_rizomuv([cube], preset="pack")

    check("hygiene: no orphan material datablocks leaked by the round-trip",
          len(bpy.data.materials) == mats_before,
          f"{len(bpy.data.materials)} vs {mats_before}")
    check("hygiene: no orphan image datablocks leaked by the round-trip",
          len(bpy.data.images) == imgs_before)
    check("hygiene: no __RZTMP temp objects leaked", not temp_leftovers(), str(temp_leftovers()))

    # -----------------------------------------------------------------------------
    # 5) Modified mesh: the BASE topology round-trips (not the evaluated Subsurf mesh),
    #    so the per-loop transfer stays exact instead of falling back to spatial mapping.
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "RZ_Subsurf"
    seed_unique_uvs(cube)
    before = uv_snapshot(cube)
    loops_before = len(cube.data.loops)
    cube.modifiers.new("Subsurf", type="SUBSURF")  # evaluated mesh is far denser than the base

    bridge = RizomUVBridge(rizom_path="not-used.exe")
    bridge._execute_uv_script = lambda: None
    bridge.process_with_rizomuv([cube], preset="unwrap_hard")

    check("modifier: base loop count unchanged (evaluated mesh not baked in)",
          len(cube.data.loops) == loops_before, f"{len(cube.data.loops)} vs {loops_before}")
    check("modifier: base UVs transferred exactly (fast path, not spatial fallback)",
          max_uv_diff(before, uv_snapshot(cube)) < 1e-4)

    # -----------------------------------------------------------------------------
    # 5b) pack_into_existing: select_objects' faces carry the subset tag, the rest none.
    #     (Its old island-GROUP-name selection was a silent no-op on 2020.1; the tag is
    #     what the preset's Materials selection turns into the islands that move.)
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    existing = bpy.context.active_object
    existing.name = "RZ_Existing"
    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
    new = bpy.context.active_object
    new.name = "RZ_New"
    layout_mat = bpy.data.materials.new("RZ_Layout")
    for o in (existing, new):
        seed_unique_uvs(o)
        o.data.materials.append(layout_mat)
    slots_before = {o.name: [s.material for s in o.material_slots] for o in (existing, new)}
    seen = {}

    bridge = RizomUVBridge(rizom_path="not-used.exe")

    def inspect_payload():
        """Stand in for RizomUV: read which faces of each exported copy carry the tag."""
        seen["token"] = bridge._params.get("PACK_SELECT_NAMES")
        mats_before = set(bpy.data.materials)
        objs = FbxUtils.import_fbx(bridge.export_path)
        for o in objs:
            if getattr(o, "type", None) != "MESH":
                continue
            names = [s.material.name if s.material else None for s in o.material_slots]
            seen[o.name] = sorted({names[p.material_index] for p in o.data.polygons})
        for o in objs:
            m = o.data if getattr(o, "type", None) == "MESH" else None
            bpy.data.objects.remove(o, do_unlink=True)
            if m is not None and m.users == 0:
                bpy.data.meshes.remove(m)
        for mat in set(bpy.data.materials) - mats_before:  # this stub's own import
            if mat.users == 0:
                bpy.data.materials.remove(mat)

    bridge._execute_uv_script = inspect_payload
    bridge.process_with_rizomuv(
        [existing, new], preset="pack_into_existing", select_objects=[new]
    )
    tag = (seen.get("token") or "").strip('{}"')
    by_src = {  # imported names carry Blender's .NNN clash suffix
        k.split("_")[1]: [re.sub(r"\.\d{3}$", "", n) for n in v]
        for k, v in seen.items()
        if k != "token"
    }
    check("pack_into_existing: the preset receives one subset tag",
          tag.startswith("rizomSubset"), str(seen.get("token")))
    check("pack_into_existing: every face of the selected object carries it",
          by_src.get("New") == [tag], str(by_src))
    check("pack_into_existing: the rest of the layout carries none of it",
          by_src.get("Existing") == ["RZ_Layout"], str(by_src))
    check("pack_into_existing: the tag never reaches the originals or outlives the run",
          {o.name: [s.material for s in o.material_slots] for o in (existing, new)} == slots_before
          and not [m.name for m in bpy.data.materials if m.name.startswith("rizomSubset")],
          str([m.name for m in bpy.data.materials]))
    check("pack_into_existing: no __RZTMP temp objects leaked",
          not temp_leftovers(), str(temp_leftovers()))

    for select, expect in (([existing, new], "no existing layout"), ([], "select_objects")):
        bridge = RizomUVBridge(rizom_path="not-used.exe")
        bridge._execute_uv_script = lambda: None
        stray = None
        if not select:
            bpy.ops.mesh.primitive_cube_add(location=(8, 0, 0))
            stray = bpy.context.active_object
            select = [stray]
        try:
            bridge.process_with_rizomuv(
                [existing, new] if stray else select,
                preset="pack_into_existing",
                select_objects=select,
            )
            check(f"pack_into_existing refuses: {expect}", False)
        except ValueError as error:
            check(f"pack_into_existing refuses: {expect}", expect in str(error), str(error))
        bridge._release_temp_payloads()

    # -----------------------------------------------------------------------------
    # 5c) A GROUP handed straight to the engine API (not through the panel's scope
    #     closure) names its meshes, as mayatk's does (Components.get_mesh_transforms /
    #     export-selection): the round-trip found "No valid mesh objects" and the send
    #     shipped the Empty alone (measured 2026-09-27, Blender 5.1).
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.object.empty_add(location=(0, 0, 0))
    grp = bpy.context.active_object
    grp.name = "RZ_Grp"
    bpy.ops.object.empty_add(location=(0, 0, 0))
    sub = bpy.context.active_object
    sub.name = "RZ_SubGrp"
    sub.parent = grp
    kids = []
    for i, parent in enumerate((grp, sub)):
        bpy.ops.mesh.primitive_cube_add(location=(3 * i, 0, 0))
        kid = bpy.context.active_object
        kid.name = f"RZ_Kid{i}"
        kid.parent = parent
        seed_unique_uvs(kid)
        kids.append(kid)
    kids_before = [uv_snapshot(k) for k in kids]

    bridge = RizomUVBridge(rizom_path="not-used.exe")
    bridge._execute_uv_script = lambda: None
    try:
        bridge.process_with_rizomuv([grp], preset="pack")
        sent = sorted(o.name for o in bridge._export_name_map.values())
        check("group: the round-trip processes the group's meshes",
              sent == ["RZ_Kid0", "RZ_Kid1"], str(sent))
        check("group: their UVs come back onto them",
              all(max_uv_diff(b, uv_snapshot(k)) < 1e-4 for b, k in zip(kids_before, kids)))
    except ValueError as error:
        check("group: the round-trip processes the group's meshes", False, str(error))
    check("group: no __RZTMP temp objects leaked", not temp_leftovers(), str(temp_leftovers()))

    import pythontk as ptk

    class _Launched:
        pid = 0

    launched = {}

    def fake_launch(exe, args=None, detached=False, **kwargs):
        launched["script"] = args[1]
        return _Launched()

    real_launch = ptk.AppLauncher.launch
    ptk.AppLauncher.launch = staticmethod(fake_launch)
    try:
        script = RizomUVBridge(rizom_path="not-used.exe").send([grp], load_textures=False)
    finally:
        ptk.AppLauncher.launch = real_launch
    fbx = re.search(r'Path="([^"]+)"', open(script, encoding="utf-8").read()).group(1)
    before_import = set(bpy.data.objects)
    shipped = FbxUtils.import_fbx(fbx)
    shipped_meshes = sorted(
        re.sub(r"\.\d{3}$", "", o.name) for o in shipped if o.type == "MESH"
    )
    for o in shipped:
        m = o.data if o.type == "MESH" else None
        bpy.data.objects.remove(o, do_unlink=True)
        if m is not None and m.users == 0:
            bpy.data.meshes.remove(m)
    for path in (fbx, script):
        try:
            os.remove(path)
        except OSError:
            pass
    check("group: a send ships the group's meshes, not the Empty alone",
          shipped_meshes == ["RZ_Kid0", "RZ_Kid1"], str(shipped_meshes))
    check("group: the send left the scene as it found it",
          set(bpy.data.objects) == before_import)

    # -----------------------------------------------------------------------------
    # 5d) Payloads under a folder whose name is not plain ASCII (a user profile, so
    #     %TEMP%, named "José" or "Жук"). RizomUV 2020.1 reads the -cfi argument in the
    #     ANSI code page and the UTF-8 bytes of the paths INSIDE its Lua as ANSI: a -cfi
    #     script under a Cyrillic folder hangs to the timeout, ZomLoad/ZomSave under a
    #     cp1252 folder too; the 8.3 forms pass (serial runs, 2026-09-27). Mirror of
    #     mayatk's TestRizomBridgeNonAsciiPaths.
    # -----------------------------------------------------------------------------
    import base64
    import shutil

    import pythontk as ptk

    paths_root = os.path.join(HERE, "temp_tests", f"rizom_paths_{os.getpid()}")
    folders = {}
    for name in ("Jos\u00e9", "\u0416\u0443\u043a"):
        folders[name] = os.path.join(paths_root, f"{name} payloads")
        os.makedirs(folders[name], exist_ok=True)
    cyr, latin = folders["\u0416\u0443\u043a"], folders["Jos\u00e9"]

    def in_folder(path, folder):
        return path.isascii() and os.path.samefile(os.path.dirname(path), folder)

    def bridge_in(folder, **kwargs):
        br = RizomUVBridge(**kwargs)
        br._temp = ptk.TempArtifacts("rizom_roundtrip", policy="scoped", dir=folder)
        return br

    try:
        # A volume with 8.3 names off answers the LONG name, not None.
        if not (ptk.AppLauncher._short_name(cyr) or "").isascii():
            check("non-ASCII payloads: SKIP (no 8.3 short names on this volume)", True)
        else:
            for name, folder in folders.items():
                br = bridge_in(folder, rizom_path="not-used.exe")
                lua_paths = re.findall(r'Path="([^"]+)"', br._construct_full_script("-- probe"))
                check(f"non-ASCII payloads: the Lua names the FBX in ASCII ({ascii(name)})",
                      len(lua_paths) == 2 and all(in_folder(q, folder) for q in lua_paths),
                      ascii(lua_paths))
                br._release_temp_payloads()

            br = bridge_in(cyr, rizom_path="not-used.exe")
            br.script_path = "-- probe"
            seen = {}

            def capture(exe, args=None, timeout=None, **kwargs):
                seen["args"] = args
                raise RuntimeError("stop before RizomUV")

            real_run = ptk.AppLauncher.run
            ptk.AppLauncher.run = staticmethod(capture)
            try:
                br._execute_uv_script()
            except RuntimeError:
                pass
            finally:
                ptk.AppLauncher.run = real_run
            arg = (seen.get("args") or [None, ""])[1]
            try:
                arg.encode(ptk.AppLauncher._ansi_codec())
                held = True
            except UnicodeEncodeError:
                held = False
            check("non-ASCII payloads: -cfi names the script in the ANSI code page",
                  held and os.path.samefile(arg, br.script_path), ascii(arg))
            br._release_temp_payloads()

            png = os.path.join(latin, "albedo.png")
            with open(png, "wb") as fh:
                fh.write(base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
            reset()
            bpy.ops.mesh.primitive_cube_add()
            textured = bpy.context.active_object
            tex_mat = bpy.data.materials.new("RZ_Tex")
            tex_mat.use_nodes = True
            node = tex_mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.load(png)
            textured.data.materials.append(tex_mat)
            send_paths = re.findall(
                r'Path="([^"]+)"',
                RizomUVBridge(rizom_path="not-used.exe").build_send_script(
                    os.path.join(cyr, "send.fbx"), objects=[textured]
                ),
            )
            check("non-ASCII payloads: a send names its FBX and texture in ASCII",
                  len(send_paths) == 2 and in_folder(send_paths[0], cyr)
                  and in_folder(send_paths[1], latin), ascii(send_paths))
            bpy.data.images.remove(node.image)

            if RizomUVBridge.APP.path:
                for name, folder in folders.items():
                    reset()
                    bpy.ops.mesh.primitive_cube_add()
                    cube = bpy.context.active_object
                    seed_unique_uvs(cube)
                    for d in cube.data.uv_layers.active.data:
                        d.uv = (d.uv[0] + 1.5, d.uv[1] + 0.25)  # out of the tile
                    try:
                        bridge_in(folder, timeout=120).process_with_rizomuv([cube], preset="pack")
                        us = [d.uv[0] for d in cube.data.uv_layers.active.data]
                        check(f"non-ASCII payloads: a real round-trip lands ({ascii(name)})",
                              max(us) <= 1.0, f"u max {max(us):.3f}")
                    except RuntimeError as error:
                        check(f"non-ASCII payloads: a real round-trip lands ({ascii(name)})",
                              False, str(error).splitlines()[0])
    finally:
        shutil.rmtree(paths_root, ignore_errors=True)

    # -----------------------------------------------------------------------------
    # 5e) An Edit Mode face selection names UV SHELLS (mirror of mayatk's component
    #     selection): `pack` tags only the shells the selected faces touch, an object in
    #     Object Mode packs whole, and the round-trip itself -- which raised from Edit Mode
    #     (the export's select_all poll-failed) and could not write UVs there -- runs in
    #     Object Mode and hands the mesh back in Edit Mode with its selection.
    # -----------------------------------------------------------------------------
    import bmesh

    def edit_select(obj, faces):
        """*obj* alone into Edit Mode with exactly *faces* selected."""
        with CoreUtils.window_context_override():
            if bpy.context.object and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for o in bpy.context.view_layer.objects:
                o.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            f.select_set(f.index in faces)
        bmesh.update_edit_mesh(obj.data)

    def leave_edit_mode():
        with CoreUtils.window_context_override():
            if bpy.context.object and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

    def tag_reader(bridge, seen, token="PACK_SUBSET"):
        """A RizomUV stand-in recording, per exported copy, the faces carrying the tag."""

        def inspect():
            seen["token"] = bridge._params.get(token)
            tag = re.sub(r"\.\d{3}$", "", (seen["token"] or "").strip('{}"'))
            mats_before = set(bpy.data.materials)
            objs = FbxUtils.import_fbx(bridge.export_path)
            for o in objs:
                if o.type != "MESH":
                    continue
                names = [s.material.name if s.material else "" for s in o.material_slots]
                seen[o.name.split("_")[1]] = sorted(
                    p.index for p in o.data.polygons
                    if tag and p.material_index < len(names)
                    and re.sub(r"\.\d{3}$", "", names[p.material_index]) == tag
                )
            for o in objs:
                m = o.data if o.type == "MESH" else None
                bpy.data.objects.remove(o, do_unlink=True)
                if m is not None and m.users == 0:
                    bpy.data.meshes.remove(m)
            for mat in set(bpy.data.materials) - mats_before:
                if mat.users == 0:
                    bpy.data.materials.remove(mat)

        return inspect

    try:
        from blendertk.core_utils._core_utils import CoreUtils

        reset()
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        shells = bpy.context.active_object
        shells.name = "RZ_Shells"
        bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
        whole = bpy.context.active_object
        whole.name = "RZ_Whole"
        for o in (shells, whole):
            seed_unique_uvs(o)  # every face its own UV shell
        edit_select(shells, {1, 2})
        seen = {}
        bridge = RizomUVBridge(rizom_path="not-used.exe")
        bridge._execute_uv_script = tag_reader(bridge, seen)
        bridge.process_with_rizomuv([shells, whole], preset="pack")
        check("shells: the preset receives the subset tag",
              (seen.get("token") or "").startswith('{"rizomSubset'), str(seen.get("token")))
        check("shells: only the selected shells of the Edit Mode mesh are tagged",
              seen.get("Shells") == [1, 2], str(seen))
        check("shells: the mesh in Object Mode packs whole",
              seen.get("Whole") == list(range(6)), str(seen))
        check("shells: the Edit Mode mesh is handed back in Edit Mode", shells.mode == "EDIT",
              shells.mode)
        bm = bmesh.from_edit_mesh(shells.data)
        check("shells: its face selection survives the run",
              sorted(f.index for f in bm.faces if f.select) == [1, 2])
        check("shells: no subset tag outlives the run",
              not [m.name for m in bpy.data.materials
                   if m.name.startswith(("rizomSubset", "rizomFixed"))],
              str([m.name for m in bpy.data.materials]))

        # The round-trip lands from Edit Mode (it raised before) and the edit mesh shows it.
        before = [tuple(loop[bm.loops.layers.uv.active].uv) for f in bm.faces for loop in f.loops]
        bridge = RizomUVBridge(rizom_path="not-used.exe")

        def shift_all():
            mats_before = set(bpy.data.materials)
            objs = FbxUtils.import_fbx(bridge.export_path)
            meshes = [o for o in objs if o.type == "MESH"]
            for o in meshes:
                for d in o.data.uv_layers.active.data:
                    d.uv = (d.uv[0] + 0.3, d.uv[1] + 0.15)
            FbxUtils.export(filepath=bridge.export_path, objects=meshes, selection_only=True)
            for o in meshes:
                m = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                if m.users == 0:
                    bpy.data.meshes.remove(m)
            for mat in set(bpy.data.materials) - mats_before:
                if mat.users == 0:
                    bpy.data.materials.remove(mat)

        bridge._execute_uv_script = shift_all
        try:
            bridge.process_with_rizomuv([shells], preset="pack")
            raised = None
        except Exception as error:  # noqa: BLE001
            raised = error
        check("shells: a round-trip from Edit Mode runs", raised is None, repr(raised))
        bm = bmesh.from_edit_mesh(shells.data)
        after = [tuple(loop[bm.loops.layers.uv.active].uv) for f in bm.faces for loop in f.loops]
        shift = max(abs(a[0] - b[0] - 0.3) for a, b in zip(after, before))
        check("shells: its UVs reach the edit mesh", shift < 1e-4, f"off by {shift:.4f}")

        # A preset that cannot take a subset says so and processes the whole object.
        bridge = RizomUVBridge(rizom_path="not-used.exe")
        bridge._execute_uv_script = lambda: None
        warned = []
        real_warning = bridge.logger.warning
        bridge.logger.warning = lambda msg, *a, **k: (warned.append(str(msg)), real_warning(msg, *a, **k))
        bridge.process_with_rizomuv([shells], preset="optimize")
        check("shells: a whole-object preset says it processed whole objects",
              any("works on whole objects" in w for w in warned), str(warned))

        # pack_into_existing: select_objects in Edit Mode names its selected shells.
        seen = {}
        bridge = RizomUVBridge(rizom_path="not-used.exe")
        bridge._execute_uv_script = tag_reader(bridge, seen, token="PACK_SELECT_NAMES")
        bridge.process_with_rizomuv(
            [shells, whole], preset="pack_into_existing", select_objects=[shells]
        )
        check("shells: pack_into_existing moves only the selected shells",
              seen.get("Shells") == [1, 2] and seen.get("Whole") == [], str(seen))
    except Exception as error:  # noqa: BLE001
        check("shells: setup", False, repr(error))
        lines.append(traceback.format_exc())
    finally:
        leave_edit_mode()

    # -----------------------------------------------------------------------------
    # 6) Guard: empty / non-mesh input raises rather than silently doing nothing.
    # -----------------------------------------------------------------------------
    reset()
    bridge = RizomUVBridge(rizom_path="not-used.exe")
    try:
        bridge.process_with_rizomuv([], preset="pack")
        check("guard: empty selection -> ValueError", False)
    except ValueError:
        check("guard: empty selection -> ValueError", True)

    # -----------------------------------------------------------------------------
    # 7) Preset resolution: unknown preset name -> FileNotFoundError (not a silent no-op).
    # -----------------------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    c = bpy.context.active_object
    bridge = RizomUVBridge(rizom_path="not-used.exe")
    try:
        bridge.process_with_rizomuv([c], preset="does_not_exist")
        check("guard: unknown preset -> FileNotFoundError", False)
    except FileNotFoundError:
        check("guard: unknown preset -> FileNotFoundError", True)

except Exception as e:
    # Environmental skip: process_with_rizomuv pulls the shared uitk.bridge
    # parameter framework, which needs a Qt binding to import. Headless
    # `--factory-startup` Blender ships none, so a missing Qt binding is an
    # environment gap, not a logic failure -- skip->PASS, mirroring the other
    # Qt-dependent suites. (The roundtrip logic itself is covered by mayatk's
    # test_uv_rizom_bridge under a Qt-enabled interpreter.)
    _missing = (getattr(e, "name", "") or "").split(".")[0]
    if isinstance(e, ModuleNotFoundError) and _missing in (
        "qtpy",
        "PySide2",
        "PySide6",
        "shiboken2",
        "shiboken6",
    ):
        lines.append(
            f"OK  SKIP: Qt binding '{_missing}' unavailable in headless Blender "
            "(uitk.bridge param stack needs Qt)"
        )
    else:
        lines.append(f"FAIL setup: {e!r}")
        lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
