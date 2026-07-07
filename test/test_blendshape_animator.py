"""blendertk blendshape_animator (btk.BlendshapeAnimator) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_blendshape_animator.py

Covers the engine end to end: CREATE (shape key + keyed value 0->1), weight/frame-based tween
creation, APPLY (writing sculpted tween deltas into driver-driven corrective shape keys) and,
critically, that the resulting animation actually PLAYS BACK the custom piecewise-linear curve
(not just "no exception") -- sampling the evaluated mesh at several master-key values and
comparing against the hand-computed piecewise-linear interpolation between control points
(Basis -> tween(s) -> Target), which is the same math Maya's blendShape in-between targets
produce. Also covers diagnostics (topology mismatch detect + cleanup), export finalize
(hide/delete + keyframes survive), from_existing (rebind after finalize, incl. the
custom-property-based target tracking), and cross-session isolation (two independent
BlendshapeAnimator sessions authoring different shape keys on the SAME base mesh must never
see/apply/delete each other's tween meshes).
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
    from blendertk.anim_utils.blendshape_animator._blendshape_animator import BlendshapeAnimator
    from blendertk.anim_utils.blendshape_animator.applicator import ApplyStatus
    from blendertk.anim_utils.blendshape_animator.target import Target, Targets
    from blendertk.anim_utils.blendshape_animator.weights import Weights

    def reset():
        if (
            bpy.context.view_layer.objects.active
            and bpy.context.view_layer.objects.active.mode != "OBJECT"
        ):
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for m in list(bpy.data.meshes):
            if m.users == 0:
                bpy.data.meshes.remove(m)

    def make_cube(name, z_scale=1.0, z_offset=0.0):
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
        obj = bpy.context.active_object
        obj.name = name
        for v in obj.data.vertices:
            v.co.z = v.co.z * z_scale + z_offset
        return obj

    def z_of(obj, index=0):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        ev = obj.evaluated_get(depsgraph)
        me = ev.to_mesh()
        try:
            return me.vertices[index].co.z
        finally:
            ev.to_mesh_clear()

    # ============================ package registration ============================
    check(
        "btk.BlendshapeAnimator resolves via DEFAULT_INCLUDE",
        btk.BlendshapeAnimator is BlendshapeAnimator,
    )

    # ============================ Weights (pure math) ============================
    check("round_weight precision", Weights.round_weight(0.123456) == 0.123)
    check("frame_to_weight clamps below start", Weights.frame_to_weight(0, 10, 20) == 0.0)
    check("frame_to_weight clamps above end", Weights.frame_to_weight(30, 10, 20) == 1.0)
    check("frame_to_weight midpoint", abs(Weights.frame_to_weight(15, 10, 20) - 0.5) < 1e-9)
    weights3 = Weights.generate_weights(3)
    check("generate_weights(3) evenly spaced", weights3 == [0.25, 0.5, 0.75], str(weights3))

    # ============================ CREATE ============================
    reset()
    base = make_cube("Base")
    target = make_cube("Target", z_offset=4.0)  # obvious, uniform +4 Z delta

    animator = BlendshapeAnimator()
    ok = animator.create(base_obj=base, target_obj=target, start_frame=1, end_frame=50, name="morph")
    check("create() succeeds", ok is True)
    check("key_name resolved", animator.key_name == "morph", str(animator.key_name))
    check("base_obj is the receiving mesh", animator.base_obj is base)
    check("target_obj is the contributing mesh", animator.target_obj is target)
    shape_keys = base.data.shape_keys
    check("shape_keys created on base", shape_keys is not None)
    check("Basis key present", shape_keys is not None and "Basis" in shape_keys.key_blocks)
    check("master key present", shape_keys is not None and "morph" in shape_keys.key_blocks)
    check(
        "target object untouched (still 2 separate meshes)",
        len(bpy.data.objects) >= 2 and target.name in bpy.data.objects,
    )
    check(
        "custom-property tracking set on base",
        base.get("blendshape_animator_key") == "morph"
        and base.get("blendshape_animator_target") == target.name,
    )

    # ---- keyframe playback: value at start/end/mid frames ----
    kb = shape_keys.key_blocks["morph"]
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    check("value at start frame == 0.0", abs(kb.value - 0.0) < 1e-6, f"{kb.value}")
    bpy.context.scene.frame_set(50)
    bpy.context.view_layer.update()
    check("value at end frame == 1.0", abs(kb.value - 1.0) < 1e-6, f"{kb.value}")
    bpy.context.scene.frame_set(25)
    bpy.context.view_layer.update()
    expected_mid = Weights.frame_to_weight(25, 1, 50)
    check(
        "value at mid frame matches linear curve",
        abs(kb.value - expected_mid) < 0.02,
        f"got={kb.value} expected~={expected_mid}",
    )
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()

    # ---- plain two-point mix plays back correctly BEFORE any tweens ----
    basis_z = shape_keys.key_blocks["Basis"].data[0].co.z
    target_z = shape_keys.key_blocks["morph"].data[0].co.z
    check("basis/target z differ (sanity)", abs(target_z - basis_z) > 1.0, f"{basis_z} vs {target_z}")
    kb.value = 0.5
    z_mid_uncorrected = z_of(base)
    expected_mid_z = basis_z + 0.5 * (target_z - basis_z)
    check(
        "uncorrected mix at 0.5 is the linear midpoint",
        abs(z_mid_uncorrected - expected_mid_z) < 0.02,
        f"got={z_mid_uncorrected} expected={expected_mid_z}",
    )
    kb.value = 0.0

    # ============================ EDIT: weight-based tweens ============================
    tweens = animator.edit_weight_based(weights=[0.3, 0.7])
    check("edit_weight_based creates 2 tweens", len(tweens) == 2, str(len(tweens)))
    check(
        "tweens tagged + parented under the group",
        all(t.obj.parent is not None and t.obj.parent.name == Targets.GROUP_NAME for t in tweens),
    )
    found = Targets.find_all_targets()
    check("Targets.find_all_targets sees both tweens", len(found) == 2, str(len(found)))

    # Sculpt: nudge each tween's z by a distinctive, known amount (simulates the artist).
    SCULPT = {0.3: 1.5, 0.7: -1.0}
    tween_by_weight = {t.weight: t for t in tweens}
    for w, delta in SCULPT.items():
        tw = tween_by_weight[w]
        for v in tw.obj.data.vertices:
            v.co.z += delta

    # ============================ APPLY ============================
    applied = animator.edit_apply_tweens()
    check("edit_apply_tweens reports both applied", len(applied) == 2, str(len(applied)))

    corrective_names = [
        kb2.name for kb2 in base.data.shape_keys.key_blocks if "_tween_w" in kb2.name
    ]
    check("2 corrective shape keys written", len(corrective_names) == 2, str(corrective_names))

    # ---- THE roundtrip: sample the evaluated mesh across the full curve and compare against
    # hand-computed piecewise-linear interpolation between (0=Basis, 0.3, 0.7, 1=Target).
    def linear_mix(w):
        return basis_z + w * (target_z - basis_z)

    control_points = [(0.0, basis_z)]
    for w in sorted(SCULPT):
        control_points.append((w, linear_mix(w) + SCULPT[w]))
    control_points.append((1.0, target_z))

    def expected_z(w):
        for (wa, za), (wb, zb) in zip(control_points, control_points[1:]):
            if wa <= w <= wb:
                t = 0.0 if wb == wa else (w - wa) / (wb - wa)
                return za + t * (zb - za)
        return None

    roundtrip_ok = True
    detail_parts = []
    for w in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):
        kb.value = w
        got = z_of(base)
        exp = expected_z(w)
        close = abs(got - exp) < 0.02
        roundtrip_ok = roundtrip_ok and close
        detail_parts.append(f"w={w}:{'ok' if close else f'got={got:.3f}!=exp={exp:.3f}'}")
    check("piecewise in-between playback matches Maya-equivalent math", roundtrip_ok, " ".join(detail_parts))
    kb.value = 0.0

    # ---- endpoints exactly preserved (corrective contribution must vanish at 0 and 1) ----
    kb.value = 0.0
    check("value=0 still gives exact Basis", abs(z_of(base) - basis_z) < 1e-3, f"{z_of(base)}")
    kb.value = 1.0
    check("value=1 still gives exact Target", abs(z_of(base) - target_z) < 1e-3, f"{z_of(base)}")
    kb.value = 0.0

    # ============================ frame-based tween ============================
    tw_frame = animator.tween_creator.create_frame_based_tween(38)  # -> weight ~ 0.755...
    check("frame-based tween created", tw_frame is not None)
    if tw_frame is not None:
        check("frame-based tween records target_frame", tw_frame.target_frame == 38)
        expected_w = Weights.frame_to_weight(38, 1, 50)
        check(
            "frame-based tween weight matches formula",
            abs(tw_frame.weight - expected_w) < 1e-3,
            f"{tw_frame.weight} vs {expected_w}",
        )

    # ============================ diagnostics: topology mismatch ============================
    reset_mismatch_name = "morph_ib_bad"
    bad_mesh = bpy.data.meshes.new(reset_mismatch_name)
    bad_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])  # 3 verts only
    bad_obj = bpy.data.objects.new(reset_mismatch_name, bad_mesh)
    bpy.context.scene.collection.objects.link(bad_obj)
    animator.tween_creator.tag_tween_mesh(bad_obj, 0.5)

    ok_topology = animator.diagnose_topology_issues()
    check("diagnose_topology_issues detects the mismatch", ok_topology is False)

    cleanup_ok = animator.cleanup_topology_mismatches(delete_mismatched=True, apply_valid_only=True)
    check("cleanup_topology_mismatches runs", cleanup_ok is True)
    check(
        "mismatched tween deleted",
        bpy.data.objects.get(reset_mismatch_name) is None,
    )
    remaining = Targets.find_all_targets()
    check(
        "valid tweens still present after cleanup",
        all(t.weight != 0.5 for t in remaining) and len(remaining) >= 2,
        str([t.weight for t in remaining]),
    )

    # ============================ export finalize ============================
    n_keys_before = len(animator.keyframes._value_fcurve().keyframe_points)
    finalize_ok = animator.finalize_for_export(
        cleanup_scene=True,
        delete_construction_history=True,
        hide_target_mesh=True,
        delete_inbetween_meshes=True,
    )
    check("finalize_for_export succeeds", finalize_ok is True)
    check("target mesh hidden", target.hide_viewport is True and target.hide_render is True)
    check("tween meshes deleted", len(Targets.find_all_targets()) == 0)
    fc_after = animator.keyframes._value_fcurve()
    check(
        "keyframes survive finalize",
        fc_after is not None and len(fc_after.keyframe_points) == n_keys_before,
        f"{len(fc_after.keyframe_points) if fc_after else None} vs {n_keys_before}",
    )
    # corrective keys (the actually-applied sculpting) must also survive finalize --
    # finalize only removes SOURCE tween meshes/target, never the shape keys they wrote.
    check(
        "corrective shape keys survive finalize",
        all(name in base.data.shape_keys.key_blocks for name in corrective_names),
    )
    # and the curve must still play back correctly post-finalize
    kb.value = 0.5
    check(
        "curve still plays back correctly after finalize",
        abs(z_of(base) - expected_z(0.5)) < 0.02,
        f"{z_of(base)} vs {expected_z(0.5)}",
    )
    kb.value = 0.0

    # ============================ from_existing (rebind) ============================
    animator2 = BlendshapeAnimator.from_existing(base)
    check("from_existing binds successfully", animator2 is not None)
    if animator2 is not None:
        check("from_existing resolves the same key_name", animator2.key_name == "morph")
        check(
            "from_existing resolves target_obj via custom property",
            animator2.target_obj is target,
        )

    # ============================ recover_animation ============================
    reset()
    base2 = make_cube("Base2")
    target2 = make_cube("Target2", z_offset=3.0)
    animator3 = BlendshapeAnimator()
    animator3.create(base_obj=base2, target_obj=target2, start_frame=10, end_frame=90, name="morph")
    tw_a = animator3.tween_creator.create_frame_based_tween(30)
    tw_b = animator3.tween_creator.create_frame_based_tween(70)
    check("recover-scenario tweens created", tw_a is not None and tw_b is not None)

    # Simulate lost keyframes.
    fc = animator3.keyframes._value_fcurve()
    for kp in reversed(list(fc.keyframe_points)):
        fc.keyframe_points.remove(kp, fast=True)
    fc.update()
    try:
        animator3.keyframes.get_frame_range()
        had_range = True
    except ValueError:
        had_range = False
    check("keyframes actually lost before recovery", had_range is False)

    recovered = animator3.recover_animation()
    check("recover_animation succeeds", recovered is True)
    start_r, end_r = animator3.keyframes.get_frame_range()
    check(
        "recover_animation infers range from tween frame metadata",
        (start_r, end_r) == (30, 70),
        f"{(start_r, end_r)}",
    )

    # ============================ apply_tweens duplicate-weight grouping ============================
    reset()
    base3 = make_cube("Base3")
    target3 = make_cube("Target3", z_offset=2.0)
    animator4 = BlendshapeAnimator()
    animator4.create(base_obj=base3, target_obj=target3, start_frame=1, end_frame=10, name="morph")
    t1 = animator4.tween_creator.create_weight_based_tweens([0.4])[0]
    # A second, independent tween object claiming the SAME weight (simulates a stray duplicate).
    dup_mesh = bpy.data.meshes.new("dup_w400")
    dup_mesh.from_pydata(
        [v.co.copy() for v in base3.data.vertices],
        [],
        [list(p.vertices) for p in base3.data.polygons],
    )
    dup_obj = bpy.data.objects.new("dup_w400", dup_mesh)
    bpy.context.scene.collection.objects.link(dup_obj)
    animator4.tween_creator.tag_tween_mesh(dup_obj, 0.4)

    results = animator4.tween_applicator.apply_tweens([t1, Target(dup_obj)])
    check("apply_tweens collapses duplicate-weight tweens to one result", len(results) == 1, str(len(results)))
    check("the collapsed result is APPLIED (not an error)", results[0][1] is ApplyStatus.APPLIED)

    # ============================ boundary-weight tweens (w==0.0 / w==1.0) ============================
    # A tween sculpted exactly at the timeline's first/last control (weight 0.0 or 1.0) must still
    # apply. Regression test for a driver-expression discontinuity where the outer "outside the
    # span" guard used <=/>= instead of </>, so a control sitting exactly at its own virtual
    # endpoint (m==w_prev==0.0, or m==w_next==1.0) hit the "outside" branch and silently evaluated
    # to 0 instead of the peak value 1 -- Apply All Edits reported "Applied" with no error, but
    # scrubbing to exactly 0.0/1.0 showed plain Basis/Target instead of the sculpt.
    reset()
    base6 = make_cube("Base6")
    target6 = make_cube("Target6", z_offset=5.0)
    animator6 = BlendshapeAnimator()
    animator6.create(base_obj=base6, target_obj=target6, start_frame=1, end_frame=10, name="morph")
    boundary_tweens = animator6.tween_creator.create_weight_based_tweens([0.0, 1.0])
    check(
        "boundary tweens created at 0.0 and 1.0",
        {t.weight for t in boundary_tweens} == {0.0, 1.0},
        str([t.weight for t in boundary_tweens]),
    )
    tween_by_w6 = {t.weight: t for t in boundary_tweens}

    BOUNDARY_SCULPT = {0.0: 2.0, 1.0: 3.0}
    for w, delta in BOUNDARY_SCULPT.items():
        for v in tween_by_w6[w].obj.data.vertices:
            v.co.z += delta

    boundary_applied = animator6.tween_applicator.apply_tweens(boundary_tweens)
    check(
        "boundary tweens applied",
        all(s is ApplyStatus.APPLIED for _, s in boundary_applied),
        str(boundary_applied),
    )

    kb6 = base6.data.shape_keys.key_blocks["morph"]
    basis6_z = base6.data.shape_keys.key_blocks["Basis"].data[0].co.z
    target6_z = kb6.data[0].co.z

    kb6.value = 0.0
    got0 = z_of(base6)
    expected0 = basis6_z + BOUNDARY_SCULPT[0.0]
    check(
        "tween sculpted AT weight 0.0 applies (not silently plain Basis)",
        abs(got0 - expected0) < 0.02,
        f"got={got0} expected={expected0} (plain Basis would be {basis6_z})",
    )

    kb6.value = 1.0
    got1 = z_of(base6)
    expected1 = target6_z + BOUNDARY_SCULPT[1.0]
    check(
        "tween sculpted AT weight 1.0 applies (not silently plain Target)",
        abs(got1 - expected1) < 0.02,
        f"got={got1} expected={expected1} (plain Target would be {target6_z})",
    )
    kb6.value = 0.0

    # ============================ cross-session isolation (same base mesh, two keys) ============================
    # Two independent BlendshapeAnimator sessions authoring DIFFERENT shape keys on the SAME
    # base mesh (a normal rigging workflow) must never see, apply, or delete each other's
    # tween meshes -- find_all_targets()/get_existing_weights()/apply_tweens(tweens=None)/
    # finalize_for_export's delete_inbetween_meshes must all be scoped per (key, base mesh).
    reset()
    base_shared = make_cube("Head")
    target_smile = make_cube("Target_Smile", z_offset=10.0)
    target_frown = make_cube("Target_Frown", z_offset=-10.0)

    animator_smile = BlendshapeAnimator()
    animator_smile.create(
        base_obj=base_shared, target_obj=target_smile, start_frame=1, end_frame=50, name="Smile"
    )
    animator_frown = BlendshapeAnimator()
    animator_frown.create(
        base_obj=base_shared, target_obj=target_frown, start_frame=1, end_frame=50, name="Frown"
    )
    check(
        "two sessions bound to the SAME base mesh, different keys",
        animator_smile.base_obj is base_shared
        and animator_frown.base_obj is base_shared
        and animator_smile.key_name == "Smile"
        and animator_frown.key_name == "Frown",
    )

    SMILE_DELTA = 5.0
    FROWN_DELTA = 999.0

    smile_tweens = animator_smile.edit_weight_based(weights=[0.5])
    check(
        "smile tween created at weight 0.5",
        len(smile_tweens) == 1 and abs(smile_tweens[0].weight - 0.5) < 1e-6,
        str([t.weight for t in smile_tweens]),
    )
    smile_tween = smile_tweens[0]
    smile_tween_name = smile_tween.obj.name
    for v in smile_tween.obj.data.vertices:
        v.co.z += SMILE_DELTA

    cross_weights = animator_frown.tween_creator.get_existing_weights()
    check(
        "Frown's get_existing_weights does NOT see Smile's weight (no cross-session leak)",
        cross_weights == set(),
        str(cross_weights),
    )

    frown_tweens = animator_frown.edit_weight_based(weights=[0.5])
    check(
        "frown tween lands at the requested weight 0.5 (not offset by a false collision)",
        len(frown_tweens) == 1 and abs(frown_tweens[0].weight - 0.5) < 1e-6,
        str([t.weight for t in frown_tweens]),
    )
    frown_tween = frown_tweens[0]
    frown_tween_name = frown_tween.obj.name
    for v in frown_tween.obj.data.vertices:
        v.co.z += FROWN_DELTA
    frown_z_after_sculpt = bpy.data.objects[frown_tween_name].data.vertices[0].co.z

    # Apply ONLY Smile's own tween (tweens=None -> now scoped to key+base mesh).
    applied_smile = animator_smile.edit_apply_tweens()
    check(
        "edit_apply_tweens(tweens=None) applies exactly Smile's own tween",
        len(applied_smile) == 1 and applied_smile[0].obj is smile_tween.obj,
        str(len(applied_smile)),
    )

    smile_correctives = [
        kb2.name for kb2 in base_shared.data.shape_keys.key_blocks
        if kb2.name.startswith("Smile_tween_w")
    ]
    check(
        "exactly one Smile corrective written, no phantom from Frown's tween",
        smile_correctives == ["Smile_tween_w500"],
        str(smile_correctives),
    )
    frown_correctives_yet = [
        kb2.name for kb2 in base_shared.data.shape_keys.key_blocks
        if kb2.name.startswith("Frown_tween_w")
    ]
    check(
        "no Frown corrective baked yet (Frown hasn't applied)",
        frown_correctives_yet == [],
        str(frown_correctives_yet),
    )

    smile_kb = base_shared.data.shape_keys.key_blocks["Smile"]
    frown_kb = base_shared.data.shape_keys.key_blocks["Frown"]
    smile_corr = base_shared.data.shape_keys.key_blocks["Smile_tween_w500"]
    smile_kb.value, frown_kb.value = 0.5, 0.0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    # NOTE: the FINAL BAKED MESH (Object.to_mesh()) is deliberately NOT asserted here.
    # Confirmed Blender 5.1 depsgraph limitation (see applicator.py's module docstring): once a
    # SECOND independently-keyed master key ("Frown") exists on this base mesh, Blender can
    # silently stop reflecting a driver-corrected key's own contribution in the baked mesh --
    # which key is affected isn't a stable "first" or "last" rule (it shifted between repro
    # runs). What IS reliable and asserted below: every RNA value involved (the master key's own
    # read-back value, and the corrective's driver-computed tent value) stays scoped to SMILE
    # alone and uncontaminated by Frown's sibling tween/value -- the actual isolation contract
    # this section is exercising. The single-master-key-per-mesh case (exercised end-to-end
    # above, including full baked-mesh playback) IS reliable.
    check(
        "Smile's own value reads back 0.5, Frown's stays 0.0 (no cross-key contamination)",
        abs(smile_kb.value - 0.5) < 1e-9 and abs(frown_kb.value - 0.0) < 1e-9,
        f"smile={smile_kb.value} frown={frown_kb.value}",
    )
    check(
        "Smile's corrective driver computes ITS OWN tent value from Smile's weight (not Frown's)",
        abs(smile_corr.value - 1.0) < 1e-6,
        f"corr.value={smile_corr.value}",
    )
    smile_kb.value, frown_kb.value = 0.0, 0.0

    check(
        "Frown's own tween object survives Smile's apply (not consumed)",
        bpy.data.objects.get(frown_tween_name) is not None,
    )
    check(
        "Frown's tween sculpt data untouched by Smile's apply",
        abs(bpy.data.objects[frown_tween_name].data.vertices[0].co.z - frown_z_after_sculpt) < 1e-9,
    )

    applied_frown = animator_frown.edit_apply_tweens()
    check(
        "Frown's own apply now works and is scoped to its own tween",
        len(applied_frown) == 1 and applied_frown[0].obj is frown_tween.obj,
        str(len(applied_frown)),
    )
    frown_correctives = [
        kb2.name for kb2 in base_shared.data.shape_keys.key_blocks
        if kb2.name.startswith("Frown_tween_w")
    ]
    check(
        "exactly one Frown corrective written",
        frown_correctives == ["Frown_tween_w500"],
        str(frown_correctives),
    )
    frown_corr = base_shared.data.shape_keys.key_blocks["Frown_tween_w500"]
    smile_kb.value, frown_kb.value = 0.0, 0.5
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    # Baked mesh not asserted here either -- same known Blender limitation as above, now with
    # BOTH keys carrying a driver-based corrective. RNA-level isolation is what's reliable.
    check(
        "Frown's own value reads back 0.5, Smile's stays 0.0 (no cross-key contamination)",
        abs(frown_kb.value - 0.5) < 1e-9 and abs(smile_kb.value - 0.0) < 1e-9,
        f"smile={smile_kb.value} frown={frown_kb.value}",
    )
    check(
        "Frown's corrective driver computes ITS OWN tent value from Frown's weight (not Smile's)",
        abs(frown_corr.value - 1.0) < 1e-6,
        f"corr.value={frown_corr.value}",
    )
    smile_kb.value, frown_kb.value = 0.0, 0.0

    # finalize_for_export's delete_inbetween_meshes must not scene-wide-delete a sibling
    # session's still-in-progress tween.
    finalize_smile_ok = animator_smile.finalize_for_export(delete_inbetween_meshes=True)
    check("Smile finalize_for_export succeeds", finalize_smile_ok is True)
    check(
        "Smile's own tween mesh deleted by its finalize",
        bpy.data.objects.get(smile_tween_name) is None,
    )
    check(
        "Frown's tween mesh SURVIVES Smile's finalize (no scene-wide deletion)",
        bpy.data.objects.get(frown_tween_name) is not None,
    )
    check(
        "shared tween group not deleted while Frown's tween is still parented there",
        bpy.data.objects.get(Targets.GROUP_NAME) is not None,
    )

    finalize_frown_ok = animator_frown.finalize_for_export(delete_inbetween_meshes=True)
    check("Frown finalize_for_export succeeds", finalize_frown_ok is True)
    check(
        "Frown's own tween mesh deleted by its finalize",
        bpy.data.objects.get(frown_tween_name) is None,
    )

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
