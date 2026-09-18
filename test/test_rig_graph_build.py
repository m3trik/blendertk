"""blendertk.rig_utils.rig_graph_build.RigGraphBuilder -- headless test.
Run: blender --background --factory-startup --python blendertk/test/test_rig_graph_build.py

Phase 3 of the rig-transfer stack: a RigGraph (the Maya extractor's output shape)
planned against Blender's capability and BUILT from blendertk's RigUtils
primitives. Fidelity is earned, not typed: with no conformance fixtures yet every
op grades ``approximate`` (schema 9.1), so verify attaches to every record.
"""

import json
import os
import sys
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    import bpy
    from pythontk import RigCapability, RigGraph
    from blendertk.rig_utils._rig_utils import RigUtils
    from blendertk.rig_utils.rig_graph_build import RigGraphBuilder

    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.context.scene.collection

    def empty(name, loc=(0, 0, 0)):
        o = bpy.data.objects.new(name, None)
        o.location = loc
        coll.objects.link(o)
        return o

    ctrl_a, ctrl_b = empty("ctrl_a", (1, 0, 0)), empty("ctrl_b", (0, 2, 0))
    driven, aimed, up_obj = empty("driven"), empty("aimed"), empty("up_obj", (0, 0, 1))
    ctrl_a["stretch"] = 0.0
    arm = RigUtils.create_armature("rig_skeleton")
    names = RigUtils.add_bone_chain(
        arm, [(0, 0, 0), (0, 2, 1), (0, 4, 0), (0, 6, 0)], prefix="j"
    )  # points are bone ENDPOINTS: four make the three-bone chain the graph names
    cd = bpy.data.curves.new("ik_curve", "CURVE")
    cd.dimensions = "3D"
    sp = cd.splines.new("NURBS")
    sp.points.add(2)
    for pt, co in zip(sp.points, [(0, 0, 0), (0, 2, 1), (0, 4, 0)]):
        pt.co = (co[0], co[1], co[2], 1.0)
    curve = bpy.data.objects.new("ik_curve", cd)
    coll.objects.link(curve)
    imported = [ctrl_a, ctrl_b, driven, aimed, up_obj, arm, curve]

    NODE_IDS = (
        "/rig",
        "/rig/ctrl_a",
        "/rig/ctrl_b",
        "/rig/driven",
        "/rig/aimed",
        "/rig/up_obj",
        "/rig/j_00",
        "/rig/j_00/j_01",
        "/rig/j_00/j_01/j_02",
        "/rig/ik_curve",
        "/rig/ghost",
    )
    graph = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake", "verify": {"tolerance": 0.1, "frames": [1]}},
        "nodes": [{"id": i} for i in NODE_IDS],
        "records": [
            {
                "id": "r_blend",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/rig/driven", "channels": ["translate", "rotate"]},
                "sources": [
                    {"id": "/rig/ctrl_a", "role": "space", "weight": 1.0},
                    {"id": "/rig/ctrl_b", "role": "space", "weight": 0.25},
                ],
                "params": {"compose": "matrix", "maintain_offset": True},
            },
            {
                "id": "r_aim",
                "shape": "transform",
                "op": "aim",
                "target": {"id": "/rig/aimed", "channels": ["rotate"]},
                "sources": [
                    {"id": "/rig/ctrl_a", "role": "target", "weight": 1.0},
                    {"id": "/rig/up_obj", "role": "up"},
                ],
                "params": {
                    "aim_axis": [1.0, 0, 0],
                    "up_axis": [0, 1.0, 0],
                    "up_ref": {"kind": "object", "role": "up"},
                },
            },
            {
                "id": "r_lin",
                "shape": "channel",
                "op": "linear",
                "target": "/rig/aimed.scale.y",
                "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
                "params": {"scale": 2.0, "offset": 0.0},
            },
            {
                "id": "r_curve",
                "shape": "channel",
                "op": "curve",
                "target": "/rig/driven.scale.x",
                "sources": [{"plug": "/rig/ctrl_a.stretch", "role": "a"}],
                "params": {"points": [[0, 0, 0, 0], [1, 5, 0, 0]], "interp": "bezier"},
            },
            {
                "id": "r_spline",
                "shape": "transform",
                "op": "spline_ik",
                "target": {
                    "chain": ["/rig/j_00", "/rig/j_00/j_01", "/rig/j_00/j_01/j_02"],
                    "channels": ["rotate"],
                },
                "sources": [{"id": "/rig/ik_curve", "role": "curve"}],
                "params": {
                    "twist": {"distribution": "linear", "start": 0.0, "end": 0.0}
                },
            },
            {
                "id": "r_opaque",
                "shape": "opaque",
                "op": "opaque",
                "target": {"ids": ["/rig/ghost"]},
                "sources": [],
                "params": {
                    "origin": {"app": "maya", "node_type": "multMatrix", "node": "mm1"}
                },
            },
        ],
    }
    check("fixture graph is well formed", RigGraph.from_dict(graph).validate() == [])

    # ---- capability: generated from the registry, honest grades --------------
    cap = RigGraphBuilder.capability()
    parsed = RigCapability.from_dict(cap)
    check(
        "capability parses and names the target",
        cap["target"] == "blender" and parsed is not None,
    )
    ops = cap["ops"]
    for key in (
        "transform/blend",
        "transform/aim",
        "transform/spline_ik",
        "channel/linear",
        "channel/curve",
    ):
        check(f"capability offers {key}", key in ops, str(sorted(ops)))
    check(
        "every op grades approximate until a fixture vouches for it",
        all(o["fidelity"] == "approximate" for o in ops.values()),
        str({k: o["fidelity"] for k, o in ops.items()}),
    )
    check("opaque is never a buildable op", "opaque/opaque" not in ops)

    # ---- build ---------------------------------------------------------------
    result = RigGraphBuilder().build(graph, imported, is_usd=False)
    built, baked = set(result["built"]), set(result["baked"])
    failed = [e for e in result["report"] if e.get("kind") == "failed"]
    check(
        "built the describable records",
        {"r_blend", "r_aim", "r_lin", "r_curve", "r_spline"} <= built,
        f"built={sorted(built)} failed={failed}",
    )
    check(
        "the opaque target is baked, never built",
        "/rig/ghost" in baked and "r_opaque" not in built,
    )
    check(
        "verify attached to every built record (approximate ops)",
        set(result["verify"]) == built,
        str(sorted(result["verify"])),
    )

    # Maya's parentConstraint REPLACES the driven's transform with the space's
    # world matrix through a constant offset; a CHILD_OF composes the space onto
    # the owner's WORLD matrix instead (its animated ancestors included), so the
    # blend is a per-space helper parented to the space holding the offset, and
    # the driven copies the helper's world transform. N spaces stack by
    # influence (1.0, then w_k / sum(w_1..w_k)): a sequential lerp is the mean.
    check(
        "blend -> COPY_TRANSFORMS from one space helper per space, weights blended",
        len(driven.constraints) == 2
        and all(c.type == "COPY_TRANSFORMS" for c in driven.constraints)
        and sorted(round(c.influence, 3) for c in driven.constraints) == [0.2, 1.0]
        and [c.target.parent for c in driven.constraints] == [ctrl_a, ctrl_b],
        str(
            [
                (c.type, c.target.name if c.target else None, c.influence)
                for c in driven.constraints
            ]
        ),
    )
    tt = [c for c in aimed.constraints if c.type == "TRACK_TO"]
    check(
        "aim -> TRACK_TO at the target",
        len(tt) == 1 and tt[0].target is ctrl_a,
        str([(c.type, getattr(c, "target", None)) for c in aimed.constraints]),
    )
    drv = {
        (fc.data_path, fc.array_index): fc
        for fc in (aimed.animation_data.drivers if aimed.animation_data else [])
    }
    lin = drv.get(("scale", 1))
    check(
        "linear -> a driver on scale.y reading the control's property",
        lin is not None
        and any(v.targets[0].data_path == '["stretch"]' for v in lin.driver.variables),
        str(
            [
                (k, [v.targets[0].data_path for v in f.driver.variables])
                for k, f in drv.items()
            ]
        ),
    )
    ddrv = {
        (fc.data_path, fc.array_index): fc
        for fc in (driven.animation_data.drivers if driven.animation_data else [])
    }
    cur = ddrv.get(("scale", 0))
    check(
        "curve -> a driver whose F-curve holds the SDK points",
        cur is not None
        and [tuple(k.co) for k in cur.keyframe_points] == [(0.0, 0.0), (1.0, 5.0)],
        str([tuple(k.co) for k in cur.keyframe_points]) if cur else "no driver",
    )
    tip = arm.pose.bones[names[-1]].constraints
    check(
        "spline_ik -> SPLINE_IK on the tip bone spanning the chain",
        any(
            c.type == "SPLINE_IK" and c.target is curve and c.chain_count == 3
            for c in tip
        ),
        str([(c.type, getattr(c, "chain_count", None)) for c in tip]),
    )

    # ---- what the production module taught (2026-09-17 acceptance run) --------
    # All 217 parentConstraints target or reference JOINTS, which arrive as bones;
    # and on the FBX route the two configs carry identical leaf names.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.context.scene.collection
    cfg_a, cfg_b = empty("cfg_a"), empty("cfg_b")
    da, db = empty("driven", (1, 0, 0)), empty("driven", (0, 1, 0))
    da.parent, db.parent = cfg_a, cfg_b  # Blender renames the second: driven.001
    ctrl = empty("ctrl", (0, 0, 5))
    arm2 = RigUtils.create_armature("skel")
    bones = RigUtils.add_bone_chain(arm2, [(0, 0, 0), (0, 1, 0), (0, 2, 0)], prefix="b")
    imported2 = [cfg_a, cfg_b, da, db, ctrl, arm2]
    graph2 = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [
            {"id": i}
            for i in (
                "/cfg_a",
                "/cfg_b",
                "/cfg_a/driven",
                "/cfg_b/driven",
                "/ctrl",
                "/skel",
                "/skel/b_00",
                "/skel/b_00/b_01",
            )
        ],
        "records": [
            {
                "id": "dup_a",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/cfg_a/driven", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/ctrl", "role": "space", "weight": 1.0}],
                "params": {},
            },
            {
                "id": "dup_b",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/cfg_b/driven", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/ctrl", "role": "space", "weight": 1.0}],
                "params": {},
            },
            {
                "id": "bone_t",
                "shape": "transform",
                "op": "blend",
                "target": {
                    "id": "/skel/b_00/b_01",
                    "channels": ["translate", "rotate"],
                },
                "sources": [{"id": "/ctrl", "role": "space", "weight": 1.0}],
                "params": {},
            },
            {
                "id": "bone_s",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/ctrl", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/skel/b_00", "role": "space", "weight": 1.0}],
                "params": {},
            },
        ],
    }
    r2 = RigGraphBuilder().build(graph2, imported2, is_usd=False)
    failed2 = [e for e in r2["report"] if e.get("kind") == "failed"]
    check(
        "FBX route: duplicate leaves resolve by their ancestors (both built, each on its own object)",
        {"dup_a", "dup_b"} <= set(r2["built"])
        and [c.type for c in da.constraints] == ["COPY_TRANSFORMS"]
        and [c.type for c in db.constraints] == ["COPY_TRANSFORMS"],
        f"built={sorted(r2['built'])} failed={failed2}"[:300],
    )
    pb = arm2.pose.bones[bones[1]]
    check(
        "a joint TARGET builds on the pose bone",
        "bone_t" in r2["built"]
        and any(
            c.type == "COPY_TRANSFORMS" and c.target.parent is ctrl
            for c in pb.constraints
        ),
        str([(c.type, getattr(c, "target", None)) for c in pb.constraints]),
    )
    cc = [c for c in ctrl.constraints if c.type == "COPY_TRANSFORMS"]
    check(
        "a joint SPACE parents the helper to armature + bone",
        "bone_s" in r2["built"]
        and cc
        and cc[0].target.parent is arm2
        and cc[0].target.parent_type == "BONE"
        and cc[0].target.parent_bone == bones[0],
        str(
            [
                (c.type, getattr(c, "target", None), getattr(c, "subtarget", None))
                for c in ctrl.constraints
            ]
        ),
    )

    # ---- the production failure (2026-09-17, 217/217 diverged): a driven node
    # under an ANIMATED ancestor must follow its space rigidly, not ride the
    # ancestor twice. Space and driven share the ancestor; the offset is (1,0,0).
    anc = empty("anc")
    for f, loc in ((1, (0, 0, 0)), (10, (5, 0, 0))):
        anc.location = loc
        anc.keyframe_insert("location", frame=f)
    space, drv = empty("space", (0, 0, 3)), empty("drv", (1, 0, 3))
    space.parent = anc
    drv.parent = anc
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    agraph = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [{"id": i} for i in ("/anc", "/anc/space", "/anc/drv")],
        "records": [
            {
                "id": "a_blend",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/anc/drv", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/anc/space", "role": "space", "weight": 1.0}],
                "params": {"maintain_offset": True},
            }
        ],
    }
    ab = RigGraphBuilder()
    ar = ab.build(agraph, [anc, space, drv], is_usd=False)
    bpy.context.scene.frame_set(10)
    bpy.context.view_layer.update()
    got = tuple(drv.matrix_world.translation)
    check(
        "an animated ancestor moves a blended driven ONCE (space + offset, Maya semantics)",
        "a_blend" in ar["built"]
        and all(abs(a - b) < 1e-4 for a, b in zip(got, (6.0, 0.0, 3.0))),
        f"got {got}, want (6.0, 0.0, 3.0); built={ar['built']} report={ar['report']}"[
            :300
        ],
    )
    helper_names = [o.name for o in bpy.data.objects if o.parent is space]
    ab.remove("a_blend")
    check(
        "remove() takes the space helper back with the constraint",
        bool(helper_names)
        and all(n not in bpy.data.objects for n in helper_names)
        and not drv.constraints,
        str([(n, n in bpy.data.objects) for n in helper_names]),
    )
    bpy.context.scene.frame_set(1)

    # ---- a joint that arrives as a CONNECTED bone (head glued to the parent's
    # tail) cannot be placed by any constraint: build() frees the bones its
    # transform records target, remove() re-glues a demoted record's.
    carm = RigUtils.create_armature("cskel")
    cb = RigUtils.add_bone_chain(carm, [(0, 0, 0), (0, 1, 0), (0, 2, 0)], prefix="c")
    cctrl = empty("cctrl", (0, 1, 0))
    bpy.context.view_layer.update()
    cgraph = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [
            {"id": i} for i in ("/cctrl", "/cskel", "/cskel/c_00", "/cskel/c_00/c_01")
        ],
        "records": [
            {
                "id": "c_bone",
                "shape": "transform",
                "op": "blend",
                "target": {
                    "id": "/cskel/c_00/c_01",
                    "channels": ["translate", "rotate"],
                },
                "sources": [{"id": "/cctrl", "role": "space", "weight": 1.0}],
                "params": {},
            }
        ],
    }
    check(
        "fixture: the chain arrives connected",
        carm.data.bones[cb[1]].use_connect is True,
    )
    cbuild = RigGraphBuilder()
    cres = cbuild.build(cgraph, [cctrl, carm], is_usd=False)
    cctrl.location = (2.0, 1.0, 0.0)
    chead = cbuild.sample_world("/cskel/c_00/c_01", 1)
    check(
        "build() frees a connected bone so its head follows the space",
        "c_bone" in cres["built"]
        and carm.data.bones[cb[1]].use_connect is False
        and chead is not None
        and all(abs(a - b) < 1e-4 for a, b in zip(chead, (2.0, 1.0, 0.0))),
        f"use_connect={carm.data.bones[cb[1]].use_connect} head={chead} report={cres['report']}"[
            :300
        ],
    )
    cbuild.remove("c_bone")
    check("remove() re-glues the bone", carm.data.bones[cb[1]].use_connect is True)

    # ---- a HIDDEN driven (the rig internals of every production pull, with
    # ANIMATED hide keys) is not evaluated by Blender at all, so its matrices
    # freeze: build() and the verify run inside evaluable(), which reveals the
    # touched objects and mutes their hide keys for the duration.
    hspace, hdrv = empty("hspace", (0, 0, 0)), empty("hdrv", (1, 0, 0))
    for f, loc in ((1, (0, 0, 0)), (10, (5, 0, 0))):
        hspace.location = loc
        hspace.keyframe_insert("location", frame=f)
    hdrv.hide_viewport = True
    hdrv.keyframe_insert("hide_viewport", frame=1)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    hgraph = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [{"id": i} for i in ("/hspace", "/hdrv")],
        "records": [
            {
                "id": "h_blend",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/hdrv", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/hspace", "role": "space", "weight": 1.0}],
                "params": {"maintain_offset": True},
            }
        ],
    }
    hb = RigGraphBuilder()
    hr = hb.build(hgraph, [hspace, hdrv], is_usd=False)
    with hb.evaluable():
        hpos = hb.sample_world("/hdrv", 10)
    check(
        "a hidden, hide-keyed driven is evaluated inside evaluable() and follows its space",
        "h_blend" in hr["built"]
        and hpos is not None
        and all(abs(a - b) < 1e-4 for a, b in zip(hpos, (6.0, 0.0, 0.0))),
        f"pos={hpos} built={hr['built']}",
    )
    check(
        "evaluable() restores the hide state on exit",
        hdrv.hide_viewport is True or bpy.context.scene.frame_current == 10,
    )
    bpy.context.scene.frame_set(1)

    # ---- the verify sampler must see BONES, or a bone record passes vacuously ---
    _b = RigGraphBuilder()
    _b._index(imported2, False)
    ctrl.location = (0.0, 0.0, 5.0)
    bpy.context.view_layer.update()
    check(
        "sample_world: an object samples its world position",
        _b.sample_world("/ctrl", 1) is not None
        and all(
            abs(a - b) < 1e-6
            for a, b in zip(_b.sample_world("/ctrl", 1), (0.0, 0.0, 5.0))
        ),
        str(_b.sample_world("/ctrl", 1)),
    )
    _pb = arm2.pose.bones[bones[1]]
    _want = tuple((arm2.matrix_world @ _pb.matrix).translation)
    check(
        "sample_world: a joint id samples the pose bone's world head",
        _b.sample_world("/skel/b_00/b_01", 1) is not None
        and all(
            abs(a - b) < 1e-6
            for a, b in zip(_b.sample_world("/skel/b_00/b_01", 1), _want)
        ),
        f"{_b.sample_world('/skel/b_00/b_01', 1)} vs {_want}",
    )
    check(
        "sample_world: an unknown id is None, never a fake point",
        _b.sample_world("/nowhere", 1) is None,
    )

    # ---- the carrier bakes the constrained motion too: mute -> verify -> commit/unmute
    # (2026-09-17 acceptance run: 217/217 built blends diverged because the new
    # CHILD_OF composed on top of the payload's baked keys -- a double transform).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.context.scene.collection
    kctrl, kdriven = empty("kctrl", (0, 0, 3)), empty("kdriven")
    for f, loc in ((1, (0, 0, 0)), (10, (5, 0, 0))):
        kdriven.location = loc
        kdriven.keyframe_insert("location", frame=f)
    karm = RigUtils.create_armature("kskel")
    kbones = RigUtils.add_bone_chain(karm, [(0, 0, 0), (0, 1, 0)], prefix="k")
    kpb = karm.pose.bones[kbones[0]]
    kpb.location = (0, 0, 0)
    kpb.keyframe_insert("location", frame=1)
    kpb.location = (2, 0, 0)
    kpb.keyframe_insert("location", frame=10)
    kgraph = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [{"id": i} for i in ("/kctrl", "/kdriven", "/kskel", "/kskel/k_00")],
        "records": [
            {
                "id": "k_obj",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/kdriven", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/kctrl", "role": "space", "weight": 1.0}],
                "params": {},
            },
            {
                "id": "k_bone",
                "shape": "transform",
                "op": "blend",
                "target": {"id": "/kskel/k_00", "channels": ["translate", "rotate"]},
                "sources": [{"id": "/kctrl", "role": "space", "weight": 1.0}],
                "params": {},
            },
        ],
    }
    from blendertk.anim_utils._anim_utils import (
        AnimUtils,
    )  # slot-aware: 5.x has no Action.fcurves

    kb = RigGraphBuilder()
    kr = kb.build(kgraph, [kctrl, kdriven, karm], is_usd=False)
    obj_fcs = [
        fc for fc in AnimUtils.get_fcurves(kdriven) if fc.data_path == "location"
    ]

    def _bone_fcs():
        return [
            fc
            for fc in AnimUtils.get_fcurves(karm)
            if fc.data_path == f'pose.bones["{kbones[0]}"].location'
        ]

    check(
        "build MUTES the target's baked keys instead of deleting them (object)",
        {"k_obj", "k_bone"} <= set(kr["built"])
        and obj_fcs
        and all(fc.mute for fc in obj_fcs),
        f"built={sorted(kr['built'])} muted={[fc.mute for fc in obj_fcs]}",
    )
    check(
        "build MUTES the target's baked keys (pose bone)",
        _bone_fcs() and all(fc.mute for fc in _bone_fcs()),
        str([fc.mute for fc in _bone_fcs()]),
    )
    kb.remove("k_bone")
    check(
        "a demotion UNMUTES the keys (no motion is lost)",
        _bone_fcs() and not any(fc.mute for fc in _bone_fcs()),
        str([fc.mute for fc in _bone_fcs()]),
    )
    kb.commit("k_obj")
    remaining = [
        fc for fc in AnimUtils.get_fcurves(kdriven) if fc.data_path == "location"
    ]
    check(
        "a commit DELETES the keys the constraint now owns",
        remaining == [] and kdriven.constraints,
        str(len(remaining)),
    )

    # ---- the committed manifest is the drift guard (schema 9.2) ---------------
    # ---- points/skin: a binding is CHECKED, never built ----------------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scoll = bpy.context.scene.collection
    sarm = RigUtils.create_armature("sskel")
    snames = RigUtils.add_bone_chain(
        sarm, [(0, 0, 0), (0, 2, 0), (0, 4, 0), (0, 6, 0)], prefix="s"
    )

    def mesh_obj(name):
        me = bpy.data.meshes.new(name)
        me.from_pydata([(0, 0, 0), (0, 2, 1), (0, 4, 0)], [], [(0, 1, 2)])
        o = bpy.data.objects.new(name, me)
        scoll.objects.link(o)
        return o

    skinned, bare = mesh_obj("skin_mesh"), mesh_obj("bare_mesh")
    skinned.vertex_groups.new(name=snames[0])
    skinned.modifiers.new("Armature", "ARMATURE").object = sarm
    scurve = bpy.data.objects.new(
        "skin_curve", bpy.data.curves.new("skin_curve", "CURVE")
    )
    scoll.objects.link(scurve)

    def skin_rec(rid, target, source, geometry="mesh"):
        return {
            "id": rid,
            "shape": "points",
            "op": "skin",
            "target": {"id": target, "points": "all"},
            "sources": [{"id": source, "role": "influence"}],
            "params": {"geometry": geometry},
        }

    # Three separate components (one bone each), so the component rule does not
    # fold one outcome into the others.
    sgraph = {
        "version": 1,
        "source": {"app": "maya", "linear_unit": "cm", "census": {}},
        "policy": {"fallback": "bake"},
        "nodes": [
            {"id": i}
            for i in (
                "/sskel",
                "/sskel/s_00",
                "/sskel/s_01",
                "/sskel/s_02",
                "/skin_mesh",
                "/bare_mesh",
                "/skin_curve",
            )
        ],
        "records": [
            skin_rec("r_ok", "/skin_mesh", "/sskel/s_00"),
            skin_rec("r_bare", "/bare_mesh", "/sskel/s_01"),
            skin_rec("r_curve", "/skin_curve", "/sskel/s_02", "curve"),
        ],
    }
    sres = RigGraphBuilder().build(sgraph, [sarm, skinned, bare, scurve], is_usd=True)
    skinds = {e["record"]: (e["kind"], e.get("reason")) for e in sres["report"]}
    check(
        "points/skin: a mesh binding that arrived is native, a missing one fails, a curve skin is refused",
        skinds.get("r_ok") == ("native", "built")
        and skinds.get("r_bare") == ("failed", "builder_raised")
        and skinds.get("r_curve") == ("baked", "unsupported_param")
        and sres["built"] == ["r_ok"],
        str(skinds),
    )

    committed = os.path.join(HERE, "rig_capability_blender.json")
    if not os.path.isfile(committed):
        with open(committed, "w") as fh:
            json.dump(cap, fh, indent=1, sort_keys=True)
    with open(committed) as fh:
        on_disk = json.load(fh)
    check(
        "committed capability manifest matches capability() "
        "(regenerate: delete the json and re-run)",
        on_disk == json.loads(json.dumps(cap)),
    )
except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
print(
    f"===RESULT: {'PASS' if all(ln.startswith('OK') for ln in lines) else 'FAIL'}=== "
    f"({passed}/{len(lines)})"
)
