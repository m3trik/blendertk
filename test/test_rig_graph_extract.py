"""blendertk.rig_utils.rig_graph_extract.RigGraphExtractor -- headless test.
Run: blender --background --factory-startup --python blendertk/test/test_rig_graph_extract.py

The Blender reader for the RigGraph schema, mirror of mayatk's: object constraints,
pose-bone constraints and drivers become records, everything else that drives the
scene becomes ``opaque``, and ``coverage()`` -- not the plan -- is the bar.
"""

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
    from pythontk import RigGraph
    from blendertk.rig_utils._rig_utils import RigUtils
    from blendertk.rig_utils.rig_graph_extract import RigGraphExtractor

    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.context.scene.collection

    def empty(name, loc=(0, 0, 0)):
        o = bpy.data.objects.new(name, None)
        o.location = loc
        coll.objects.link(o)
        return o

    rig = empty("rig")
    ctrl_a, ctrl_b = empty("ctrl_a", (1, 0, 0)), empty("ctrl_b", (0, 2, 0))
    driven, aimed, up_obj = empty("driven"), empty("aimed"), empty("up_obj", (0, 0, 1))
    for o in (ctrl_a, ctrl_b, driven, aimed, up_obj):
        o.parent = rig
    ctrl_a["stretch"] = 0.5
    c1 = RigUtils.child_of(driven, ctrl_a)
    c2 = RigUtils.child_of(driven, ctrl_b)
    c2.influence = 0.25
    RigUtils.track_to(aimed, ctrl_a, track_axis="TRACK_X", up_axis="UP_Y")
    fc = RigUtils.add_transform_driver(
        aimed, "scale", 1, ctrl_a, "LOC_X", expression="a * 2.0 + 0.0", var_name="a"
    )
    RigUtils.refresh_drivers([aimed])
    arm = RigUtils.create_armature("rig_skeleton")
    arm.parent = rig
    names = RigUtils.add_bone_chain(
        arm, [(0, 0, 0), (0, 2, 1), (0, 4, 0), (0, 6, 0)], prefix="j"
    )
    cd = bpy.data.curves.new("ik_curve", "CURVE")
    cd.dimensions = "3D"
    sp = cd.splines.new("NURBS")
    sp.points.add(2)
    for pt, co in zip(sp.points, [(0, 0, 0), (0, 2, 1), (0, 4, 0)]):
        pt.co = (co[0], co[1], co[2], 1.0)
    curve = bpy.data.objects.new("ik_curve", cd)
    coll.objects.link(curve)
    curve.parent = rig
    RigUtils.add_spline_ik(arm, names[-1], curve, chain_count=3)
    # A deformer binding: a mesh whose vertex groups name two of the chain's bones.
    me = bpy.data.meshes.new("skin_mesh")
    me.from_pydata([(0, 0, 0), (0, 2, 1), (0, 4, 0)], [], [(0, 1, 2)])
    skinned = bpy.data.objects.new("skin_mesh", me)
    coll.objects.link(skinned)
    skinned.parent = rig
    for n in names[:2]:
        skinned.vertex_groups.new(name=n)
    skinned.modifiers.new("Armature", "ARMATURE").object = arm
    # An unrecognised driver shape: a scripted driver with TWO variables -> opaque.
    fc2 = RigUtils.add_transform_driver(
        driven, "scale", 2, ctrl_a, "LOC_Y", expression="a + b", var_name="a"
    )
    RigUtils.add_transform_var(fc2, "b", ctrl_b, "LOC_Z")

    data = RigGraphExtractor().extract()
    graph = RigGraph.from_dict(data)

    def recs(shape, op):
        return [r for r in graph.records if r.shape == shape and r.op == op]

    check("graph is well formed", graph.validate() == [], str(graph.validate()[:3]))
    check("source is blender", data["source"]["app"] == "blender")
    cov = graph.coverage()
    check(
        "every driver is accounted for",
        cov["seen"] > 0 and cov["unaccounted"] == 0 and cov["uncensused"] == {},
        str(cov),
    )
    ids = {n.id for n in graph.nodes}
    check(
        "ids are the exporter's prim paths",
        "/rig/driven" in ids and "/rig/rig_skeleton" in ids,
        str(sorted(ids))[:200],
    )
    blends = recs("transform", "blend")
    check(
        "CHILD_OF pair -> one blend with weighted spaces",
        len(blends) == 1
        and {s["id"]: s["weight"] for s in blends[0].sources}
        == {"/rig/ctrl_a": 1.0, "/rig/ctrl_b": 0.25},
        str([(r.target, r.sources) for r in blends])[:300],
    )
    aims = recs("transform", "aim")
    check(
        "TRACK_TO -> aim at the target with local axes",
        len(aims) == 1
        and aims[0].target["id"] == "/rig/aimed"
        and aims[0].params["aim_axis"] == [1.0, 0.0, 0.0],
        str([(r.target, r.params) for r in aims])[:300],
    )
    lin = recs("channel", "linear")
    check(
        "a*k+b driver -> linear",
        len(lin) == 1
        and lin[0].target == "/rig/aimed.scale.y"
        and lin[0].params == {"scale": 2.0, "offset": 0.0}
        and lin[0].sources[0]["plug"] == "/rig/ctrl_a.translate.x",
        str([(r.target, r.params, r.sources) for r in lin])[:300],
    )
    spl = recs("transform", "spline_ik")
    check(
        "SPLINE_IK -> spline_ik over the chain with its curve",
        len(spl) == 1
        and len(spl[0].target["chain"]) == 3
        and spl[0].sources[0]["id"] == "/rig/ik_curve",
        str([(r.target, r.sources) for r in spl])[:300],
    )
    opq = recs("opaque", "opaque")
    skins = [
        (r.target, [src["id"] for src in r.sources], r.params, r.provenance)
        for r in recs("points", "skin")
    ]
    check(
        "ARMATURE modifier -> points/skin naming the bones its vertex groups bind (the binding, not the weights)",
        skins
        == [
            (
                {"id": "/rig/skin_mesh", "points": "all"},
                ["/rig/rig_skeleton/j_00", "/rig/rig_skeleton/j_01"],
                {"geometry": "mesh"},
                ["armature_modifier:skin_mesh.Armature"],
            )
        ],
        str(skins),
    )
    check(
        "a two-variable driver is opaque, never silent",
        any("/rig/driven" in r.target["ids"] for r in opq),
        str([r.params for r in opq])[:200],
    )
    check(
        "provenance names the owner and constraint/driver",
        all(r.provenance for r in graph.records),
    )

    # ---- a REAL rig nobody here authored: Blender's own bundled Rigify -------
    # The fixture above is ours, so it can only test what we already thought of.
    # Rigify is the de-facto standard Blender rig and is generated headless in
    # seconds -- real-world complexity (measured when written: 706 bones, 533
    # constraints, 114 drivers) with no asset to download, no licence to carry
    # and no vendored binary to drift. `coverage()` is the bar, not the plan: a
    # rig we cannot BUILD must still be read completely enough to bake honestly.
    from collections import Counter as _Counter

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _rigify_ok = True
    try:
        bpy.ops.preferences.addon_enable(module="rigify")
        bpy.ops.object.armature_human_metarig_add()
        _meta = bpy.context.object
        bpy.ops.pose.rigify_generate()
        _gen = next(
            o for o in bpy.data.objects if o.type == "ARMATURE" and o is not _meta
        )
    except Exception as _e:
        _rigify_ok = False
        check(
            "rigify: the bundled addon generates a rig headless", False, repr(_e)[:200]
        )

    if _rigify_ok:
        _cons = sum(len(pb.constraints) for pb in _gen.pose.bones)
        _drv = len(_gen.animation_data.drivers) if _gen.animation_data else 0
        check(
            "rigify: generated a rig of production complexity (the fixture is worth testing against)",
            len(_gen.data.bones) > 300 and _cons > 200 and _drv > 50,
            f"bones={len(_gen.data.bones)} constraints={_cons} drivers={_drv}",
        )

        _rdata = RigGraphExtractor().extract()
        _rgraph = RigGraph.from_dict(_rdata)
        check(
            "rigify: the extracted graph is well formed",
            _rgraph.validate() == [],
            str(_rgraph.validate()[:3]),
        )
        _rcov = _rgraph.coverage()
        check(
            "rigify: every driver is accounted for (0 unaccounted is the acceptance bar)",
            _rcov["seen"] > 0
            and _rcov["unaccounted"] == 0
            and _rcov["uncensused"] == {},
            str(_rcov),
        )
        check(
            "rigify: a rig this size cannot read as a handful of records",
            len(_rgraph.records) > 200,
            f"records={len(_rgraph.records)}",
        )
        _rops = _Counter((r.shape, r.op) for r in _rgraph.records)
        check(
            "rigify: its IK chains and tracked bones are read as ops, not all dumped to opaque",
            _rops[("transform", "blend")] > 100
            and _rops[("transform", "ik")] > 0
            and _rops[("transform", "aim")] > 0,
            str(_rops.most_common()),
        )
        check(
            "rigify: every record carries provenance (what it was read FROM)",
            all(r.provenance for r in _rgraph.records),
        )
        check(
            "rigify: the graph round-trips through plain values unchanged",
            RigGraph.from_dict(_rgraph.to_dict()).to_dict() == _rgraph.to_dict(),
        )

        # The planner must ACCOUNT for all of it -- built, baked or dropped --
        # and the component rule must hold: a rig this connected cannot come out
        # as hundreds of independent one-record rigs.
        from pythontk import RigCapability, RigPlanner
        import json as _json

        _cap = RigCapability.from_dict(
            _json.load(open(os.path.join(HERE, "rig_capability_blender.json")))
        )
        _plan = RigPlanner.plan(_rgraph, _cap)
        _decided = {e.record for e in _plan.report}
        check(
            "rigify: the plan decides EVERY record (nothing silently disappears)",
            _decided == {r.id for r in _rgraph.records},
            f"records={len(_rgraph.records)} decided={len(_decided)}",
        )
        check(
            "rigify: a built record is never left driving a baked node (the component rule holds)",
            not (
                set(_plan.build)
                & {
                    r.id
                    for r in _rgraph.records
                    if set(r.target_ids()) & set(_plan.bake)
                }
            ),
        )
        _sizes = sorted((len(c) for c in _rgraph.components()), reverse=True)
        check(
            "rigify: its records group into real rig components, not singletons",
            _sizes and _sizes[0] > 20,
            f"largest components={_sizes[:5]}",
        )
except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
print(
    f"===RESULT: {'PASS' if all(ln.startswith('OK') for ln in lines) else 'FAIL'}=== ({passed}/{len(lines)})"
)
