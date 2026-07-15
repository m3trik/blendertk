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
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
