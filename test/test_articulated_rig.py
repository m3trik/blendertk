"""blendertk.rig_utils.articulated_rig headless test -- the engine, its record and its export.

Run: blender --background --factory-startup --python blendertk/test/test_articulated_rig.py

Mirror of mayatk's ``test_articulated_rig.py`` (the classes that apply before the grab
and the pivot adjusting land: Analyze, Build, Teardown, EndControl, the solve's
conformance (mayatk's SolverExpression), Limits, PostRig, Record), on the same fixture:
the production magnifier's shape built from primitives under a scaled asset (``ASSET``
x2 > ``ARM`` > six part groups), Maya's Y-up coordinates turned Z-up -- a base plate and
post, a pole on a knob swivel, an elbow knob, an upper tube with a collar knob, an outer
rod with an inner rod running inside it, and a head on a small ball. Every part is a
GROUP of shells, and the head's group is turned (its shells compensated), so a part's
own transform and parent inverse are exercised.

Pinned here: the proposal (Z up); a build that moves nothing at rest and a teardown that
puts every part back exactly; every control on its own joint, the telescope's arrow
clear of its tubes; Blender evaluating the deform bones exactly as
``ptk.ArticulationModel`` does (random FK states, and every ``ArticulationConformance``
case of the rig whose joints are the engine's types); a limit holding the joint (the
driver reads the control as its limit leaves it); one undo step per build; a Shift+D
copy refused; the post-rig edits carrying the keys across (rebuild, insert, fold,
retype, a refused edit, a lost part) with the F-curves bound to the same control bones;
the record (== the model, a part renamed after the build, the rest on the bones); the
end control (the head follows it, turns with it, plays its keys back, hands over to FK
without a jump, drops and comes back, takes a give at once, is repaired when the
session's solve is missing) and its solve held to every ``ArticulationConformance``
solve case through the real rig (the end control placed, the joints read); the bake
(muted while the handlers run, written playing into the file, a file reopened WITH
blendertk moving on its end control and one reopened in a fresh Blender WITHOUT it
playing its baked IK and its FK); and the export contract: the Scene Exporter's FBX ->
FBX2glTF GLB ships the joints and the parts under them, no control bone and no leaf
bone, ``articulation_web`` binds them (mayatk's ``articulated_rig_live_check.py`` check
E), and its animation is the model of every frame's pose -- the end control's solve
included.

Tolerances: Blender's matrices are float32, so a pose compares to 1e-4 in a fixture
~120 units across (mayatk's double-precision Maya holds 1e-6).
"""

import json
import logging
import math
import os
import random
import re
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy
    import numpy as np
    from mathutils import Euler, Matrix, Quaternion, Vector

    import pythontk as ptk
    from blendertk.core_utils._core_utils import CoreUtils
    from blendertk.node_utils.data_nodes import DataNodes
    from blendertk.rig_utils.articulated_rig import ArticulatedRig
    from blendertk.rig_utils.articulated_rig._solver_handler import SolverHandler

    TOL = 1e-4
    SCALE = 2.0
    R = (0.966, 0.259, 0.0)

    def _unit(v):
        n = math.sqrt(sum(c * c for c in v))
        return tuple(c / n for c in v)

    def _add(p, d, s=1.0):
        return tuple(a + b * s for a, b in zip(p, d))

    def B(p):
        """A Maya (Y-up) fixture point, Z-up."""
        return (p[0], -p[2], p[1])

    ELBOW = (0.0, 32.0, 0.0)
    COLLAR = _add(ELBOW, (0.8, 0.6, 0.0), 25)
    ROD_END = _add(COLLAR, _unit(R), 32)
    INNER_0 = _add(COLLAR, _unit(R), 20)
    INNER_1 = _add(COLLAR, _unit(R), 40)
    BALL = _add(INNER_1, _unit(R), 0.7)
    PARTS = ["BASE", "LEG_1", "LEG_2", "LEG_3", "LEG_4", "HEAD"]

    def _mesh(name, verts, faces):
        verts = np.asarray(verts, dtype=float)
        centre = verts.mean(axis=0)
        me = bpy.data.meshes.new(name)
        me.from_pydata((verts - centre).tolist(), [], faces)
        ob = bpy.data.objects.new(name, me)
        ob.location = centre.tolist()
        bpy.context.scene.collection.objects.link(ob)
        return ob

    def _cyl(name, p0, p1, radius, sides=12):
        p0, p1 = np.array(B(p0)), np.array(B(p1))
        axis = p1 - p0
        n = axis / np.linalg.norm(axis)
        u = np.cross(n, (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0))
        u /= np.linalg.norm(u)
        v = np.cross(n, u)
        verts = [
            p0 + t * axis + radius * (math.cos(a) * u + math.sin(a) * v)
            for t in (0.0, 0.5, 1.0)
            for a in (2 * math.pi * i / sides for i in range(sides))
        ]
        faces = [
            (
                r * sides + i,
                r * sides + (i + 1) % sides,
                (r + 1) * sides + (i + 1) % sides,
                (r + 1) * sides + i,
            )
            for r in range(2)
            for i in range(sides)
        ]
        faces += [tuple(range(sides))[::-1], tuple(range(2 * sides, 3 * sides))]
        return _mesh(name, verts, faces)

    def _cube(name, centre, size):
        c = np.array(B(centre))
        w, h, d = size  # Maya: x, y (up), z
        half = np.array((w, d, h)) / 2.0
        verts = [
            c + half * np.array(s)
            for s in (
                (-1, -1, -1),
                (1, -1, -1),
                (1, 1, -1),
                (-1, 1, -1),
                (-1, -1, 1),
                (1, -1, 1),
                (1, 1, 1),
                (-1, 1, 1),
            )
        ]
        faces = [
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ]
        return _mesh(name, verts, faces)

    def _sphere(name, centre, radius, sx=8, sy=6):
        c = np.array(B(centre))
        verts = [c + (0, 0, -radius)]
        for k in range(1, sy):
            phi = math.pi * k / sy - math.pi / 2
            for i in range(sx):
                a = 2 * math.pi * i / sx
                verts.append(
                    c
                    + radius
                    * np.array(
                        (
                            math.cos(phi) * math.cos(a),
                            math.cos(phi) * math.sin(a),
                            math.sin(phi),
                        )
                    )
                )
        verts.append(c + (0, 0, radius))
        faces = [(0, 1 + (i + 1) % sx, 1 + i) for i in range(sx)]
        for k in range(sy - 2):
            for i in range(sx):
                a0, a1 = 1 + k * sx, 1 + (k + 1) * sx
                faces.append((a0 + i, a0 + (i + 1) % sx, a1 + (i + 1) % sx, a1 + i))
        top = len(verts) - 1
        last = 1 + (sy - 2) * sx
        faces += [(last + i, last + (i + 1) % sx, top) for i in range(sx)]
        return _mesh(name, verts, faces)

    def _empty(name, parent=None, location=(0.0, 0.0, 0.0)):
        ob = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = location
        if parent is not None:
            ob.parent = parent
        return ob

    def build_arm():
        """The fixture; returns the ``ARM`` group's name (its six part groups under it)."""
        r = _unit(R)
        parts = {
            "BASE": [
                _cube("plate", (0, 0.45, 0), (14, 0.9, 14)),
                _cyl("post", (0, 0.9, 0), (0, 7, 0), 2.0),
            ],
            "LEG_1": [
                _cyl("housing", (0, 4, 0), (0, 10, 0), 2.5),
                _cyl("pole", (0, 10, 0), (0, 31, 0), 2.47),
                _cube("tilt_knob", (0, 8, 3.4), (6, 2, 1)),
            ],
            "LEG_2": [
                _cyl("tube", ELBOW, COLLAR, 2.47),
                _cube("elbow_knob", (ELBOW[0], ELBOW[1], 3.4), (6, 2, 1)),
                _cube("collar_knob", (COLLAR[0], COLLAR[1], -3.4), (6, 2, 1)),
            ],
            "LEG_3": [_cyl("rod", COLLAR, ROD_END, 2.0)],
            "LEG_4": [_cyl("inner", INNER_0, INNER_1, 1.1)],
            "HEAD": [
                _sphere("ball", BALL, 0.7),
                _cyl("ring", _add(BALL, r, 12), _add(BALL, r, 12.3), 11.0, sides=24),
                _cyl("stub", BALL, _add(BALL, r, 1.5), 0.35),
            ],
        }
        asset = _empty("ASSET")
        arm = _empty("ARM", asset)
        for name, shells in parts.items():
            centre = np.mean([list(s.location) for s in shells], axis=0)
            group = _empty(name, arm, centre.tolist())
            for shell in shells:
                shell.parent = group
                shell.location = (np.array(shell.location) - centre).tolist()
        # The head's group turned and held through a parent inverse, its shells
        # compensated: a part whose own transform AND parent inverse are not the
        # identity (setting a parent resets the inverse -- the teardown must not).
        head = bpy.data.objects["HEAD"]
        offset = Vector((1.5, -2.0, 0.75))
        head.matrix_parent_inverse = Matrix.Translation(offset)
        head.location = Vector(head.location) - offset
        head.rotation_euler = (0.3, -0.2, 0.5)
        turn = head.rotation_euler.to_matrix().to_4x4()
        for shell in head.children:
            shell.matrix_parent_inverse = turn.inverted()
        asset.scale = (SCALE, SCALE, SCALE)
        bpy.context.view_layer.update()
        return arm.name

    def reset():
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for coll in (
            bpy.data.meshes,
            bpy.data.curves,
            bpy.data.armatures,
            bpy.data.actions,
        ):
            for d in list(coll):
                coll.remove(d)
        scene = bpy.context.scene
        if ArticulatedRig.REGISTRY_ATTR in scene.keys():
            del scene[ArticulatedRig.REGISTRY_ATTR]
        scene.frame_set(1)
        scene.tool_settings.use_keyframe_insert_auto = False

    def ob(name):
        return bpy.data.objects[name]

    def world(name):
        bpy.context.view_layer.update()
        return ob(name).matrix_world.copy()

    def mdiff(a, b):
        return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))

    def _w(point):
        """A fixture point (Maya coords, the arm's own space) in world space."""
        return list(ob("ARM").matrix_world @ Vector(B(point)))

    def setup():
        reset()
        arm = build_arm()
        rest = {p: world(p) for p in PARTS + ["ball", "ring"]}
        return arm, rest

    def build(arm, links=None, **kwargs):
        if links is None:
            plan = ArticulatedRig.analyze(arm)
            return ArticulatedRig.create(plan["links"], plan["joints"], **kwargs)
        return ArticulatedRig.create(links, **kwargs)

    def at_rest(rest):
        return max(mdiff(rest[p], world(p)) for p in rest)

    def random_state(model, rng, spread=40.0, fraction=1.0):
        state = []
        for slot in range(len(model.channels)):
            lo, hi = model.limits(slot)
            state.append(
                rng.uniform(
                    -spread if lo is None else lo * fraction,
                    spread if hi is None else hi * fraction,
                )
            )
        return state

    def model_matrix(p, q):
        return Matrix.LocRotScale(Vector(p), Quaternion((q[3], q[0], q[1], q[2])), None)

    def model_gap(rig, state):
        """Worst difference between the deform bones (rig space) and the model."""
        model = rig.model()
        ids = rig.joint_ids()
        return max(
            mdiff(model_matrix(p, q), rig.joint(ids[i]).matrix)
            for i, (p, q) in enumerate(model.world(state))
        )

    def keys(bone, channel):
        """Frames keyed on *bone*'s *channel*."""
        prop, index = ArticulatedRig.CHANNEL_PATHS[channel]
        return keys_of(bone, f".{prop}", index)

    def keys_of(bone, suffix, index):
        """Frames keyed on *bone*'s property *suffix* (``.location``, ``["prop"]``)."""
        path = f'pose.bones["{bone.name}"]{suffix}'
        anim = bone.id_data.animation_data
        import blendertk as btk

        for fc in btk.AnimUtils.get_fcurves([bone.id_data]) if anim else []:
            if fc.data_path == path and fc.array_index == index:
                return [round(k.co.x, 3) for k in fc.keyframe_points]
        return []

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    def captured():
        handler = _Capture()
        ArticulatedRig.logger.addHandler(handler)
        return handler

    # ----------------------------------------------------- end control helpers
    def update():
        bpy.context.view_layer.update()

    def set_switch(rig, name, value):
        """An end-control switch set as the UI sets it: the property, then the armature
        tagged (a Python write alone wakes nothing)."""
        rig.end_control[name] = value
        rig.group.update_tag()
        update()

    def end_world(rig):
        """The end control's pivot (its bone's head), world space."""
        update()
        return (rig.group.matrix_world @ rig.end_control.matrix).translation.copy()

    def end_turn(rig):
        update()
        return (rig.group.matrix_world @ rig.end_control.matrix).to_quaternion()

    def move_end(rig, delta):
        """Move the end control by a world-space *delta* (the viewport's G)."""
        end = rig.end_control
        to_basis = (rig.group.matrix_world @ end.bone.matrix_local).to_3x3().inverted()
        end.location = Vector(end.location) + to_basis @ Vector(delta)
        update()

    def rotate_end(rig, degrees):
        """Turn the end control in its own space by an XYZ Euler (Maya's ``rotate
        -relative -objectSpace``)."""
        end = rig.end_control
        turn = Euler([math.radians(d) for d in degrees], "XYZ").to_matrix()
        end.rotation_euler = (end.rotation_euler.to_matrix() @ turn).to_euler(
            "XYZ", end.rotation_euler
        )
        update()

    def place_end(rig, matrix):
        """Put the end control at *matrix* (the rig's space) through its channels."""
        end = rig.end_control
        location, rotation, _ = (end.bone.matrix_local.inverted() @ matrix).decompose()
        end.location = location
        end.rotation_euler = rotation.to_euler("XYZ")
        update()

    def head_point(rest, pivot):
        """Where the point that sat at the end control's pivot at rest is now, carried
        by the head."""
        return world("HEAD") @ rest["HEAD"].inverted() @ Vector(pivot)

    def turn_gap(a, b):
        """The angle between two rotations (``Quaternion`` or ``(w, x, y, z)``; q and
        -q one rotation), in double and through the half-angle's sine: an ``acos``
        of a float32 dot cannot tell anything under ~1e-3 rad from no turn."""
        a = np.array(tuple(a), dtype=np.float64)
        b = np.array(tuple(b), dtype=np.float64)
        a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
        sine = a[0] * b[1:] - b[0] * a[1:] - np.cross(a[1:], b[1:])
        return 2.0 * math.atan2(float(np.linalg.norm(sine)), abs(float(a @ b)))

    def xyz_turn(degrees):
        """An XYZ Euler (degrees; X applied first) as ``(w, x, y, z)``, in double."""
        out = np.array((1.0, 0.0, 0.0, 0.0))
        for axis in range(3):  # q = qz (x) qy (x) qx: each later one on the left
            half = math.radians(degrees[axis]) / 2.0
            q = np.zeros(4)
            q[0], q[1 + axis] = math.cos(half), math.sin(half)
            w1, v1, w2, v2 = q[0], q[1:], out[0], out[1:]
            out = np.concatenate(
                ([w1 * w2 - v1 @ v2], w1 * v2 + w2 * v1 + np.cross(v1, v2))
            )
        return out

    def deform_state(rig):
        """The deform bones' channels as Blender evaluated them, in the model's state
        order (degrees, the rig's units) -- what the drivers wrote."""
        update()
        ev = rig.group.evaluated_get(bpy.context.evaluated_depsgraph_get())
        spec = rig.spec
        joints = {j["id"]: j for j in spec["joints"]}
        out = []
        for joint_id, channel in ptk.ArticulationPlan.slots(spec):
            bone = ev.pose.bones[ArticulatedRig._bone_names(spec, joints[joint_id])[0]]
            prop, index = ArticulatedRig.CHANNEL_PATHS[channel]
            value = float(getattr(bone, prop)[index])
            out.append(math.degrees(value) if channel.startswith("r") else value)
        return out

    def conformance_rig(source, name, end_control=False):
        """The conformance rig *source* built through the engine from a plan read off
        its record (cubes on its joints, a root cube under them): its joints are the
        engine's types, its limits and weights the record's."""
        reset()
        kinds = {
            (tuple(ch), order): kind
            for kind, (ch, order) in ArticulatedRig.JOINT_TYPES.items()
        }
        placed, links, joints = [], [], []
        for index, j in enumerate(source["joints"]):
            q = Quaternion((j["q"][3], *j["q"][:3]))
            t = Vector(j["t"])
            if j["parent"] is None:
                wq, wp = q, t
            else:
                pq, pp = placed[j["parent"]]
                wq, wp = pq @ q, pp + pq @ t
            placed.append((wq, wp))
            channels = tuple(c["channel"] for c in j["channels"])
            joints.append(
                {
                    "link": index + 1,
                    "parent": 0 if j["parent"] is None else j["parent"] + 1,
                    "type": kinds[(channels, j["rotate_order"])],
                    "position": list(wp),
                    "aim": list(wq @ Vector((1, 0, 0))),
                    "normal": list(wq @ Vector((0, 0, 1))),
                    "limits": {
                        c["channel"]: [c["min"], c["max"]] for c in j["channels"]
                    },
                    "weights": {c["channel"]: c["weight"] for c in j["channels"]},
                    "size": 1.0,
                }
            )
        for part, at in [("root", (0, 0, -1))] + [
            (j["name"], placed[i][1]) for i, j in enumerate(source["joints"])
        ]:
            bpy.ops.mesh.primitive_cube_add(size=0.5, location=at)
            bpy.context.active_object.name = part
            links.append([part])
        return ArticulatedRig.create(links, joints, name=name, end_control=end_control)

    def end_on(rig, joint, local):
        """Rebuild *rig* with its end control on *joint*'s link, its pivot at *local*
        in that joint's frame and its axes the joint's (no turn)."""
        spec = rig.spec
        entry = spec["joints"][joint]
        frame = ptk.ArticulationPlan.frame(
            entry["position"], entry["aim"], entry["normal"]
        )
        placed = list(frame)
        for axis in range(3):
            placed[12 + axis] = frame[12 + axis] + sum(
                local[k] * frame[4 * k + axis] for k in range(3)
            )
        spec["end"] = {"part": spec["links"][entry["link"]][0]["uuid"], "frame": placed}
        return rig.rebuild(spec)

    # ============================================================ Analyze
    arm, rest = setup()
    plan = ArticulatedRig.analyze(arm)
    check(
        "analyze: the magnifier proposes its parts as links, root first",
        [p for link in plan["links"] for p in link] == PARTS,
        plan["links"],
    )
    check(
        "analyze: five joints of the magnifier's types (Z up)",
        [j["type"] for j in plan["joints"]]
        == ["universal", "hinge", "hinge", "slide", "ball"],
        [j["type"] for j in plan["joints"]],
    )
    want = [(0, 8, 0), ELBOW, COLLAR, ROD_END, BALL]
    gaps = [math.dist(j["position"], _w(p)) for j, p in zip(plan["joints"], want)]
    check("analyze: each joint where the parts meet", max(gaps) < 0.3 * SCALE, gaps)
    lo, hi = plan["joints"][3]["limits"]["tx"]
    check(
        "analyze: the telescope's travel",
        lo < 0 and abs(hi / SCALE - 12 * 0.8) < 0.5,
        (lo, hi),
    )
    plan = ArticulatedRig.analyze(PARTS, ordered=True)
    check(
        "analyze: a selection order is taken as given",
        [j["parent"] for j in plan["joints"]] == [0, 1, 2, 3, 4],
        [j["parent"] for j in plan["joints"]],
    )

    # ============================================================== Build
    arm, rest = setup()
    rig = build(arm)
    check("build: moves nothing", at_rest(rest) < TOL, at_rest(rest))
    check("build: five joints", len(rig.joint_ids()) == 5, rig.joint_ids())
    armature = rig.group
    carried = []
    for joint_id in rig.joint_ids():
        bone = rig.joint(joint_id).name
        riders = [
            o
            for o in bpy.data.objects
            if o.parent == armature
            and o.parent_type == "BONE"
            and o.parent_bone == bone
        ]
        carried.append(
            bool(riders) and all(ArticulatedRig._has_mesh(o) for o in riders)
        )
    check("build: every joint carries a part", all(carried), carried)
    check(
        "build: the armature rides the parts' parent, at its origin",
        armature.parent == ob("ARM")
        and mdiff(armature.matrix_world, ob("ARM").matrix_world) < 1e-6,
        armature.parent,
    )
    deform = [b.name for b in armature.data.bones if b.use_deform]
    check(
        "build: the deform bones are the joints, in a hidden collection",
        sorted(deform) == sorted(rig.joint(j).name for j in rig.joint_ids())
        and all(
            not c.is_visible for b in deform for c in armature.data.bones[b].collections
        ),
        deform,
    )
    check(
        "build: no deform bone hangs under a control bone",
        all(
            b.parent is None or b.parent.use_deform
            for b in armature.data.bones
            if b.use_deform
        ),
    )
    check(
        "build: the control shapes are in no collection (nothing exports them)",
        all(
            rig.control(j).custom_shape is not None
            and not rig.control(j).custom_shape.users_collection
            for j in rig.joint_ids()
        ),
    )
    worst = max(
        mdiff(rig.control(j).matrix, rig.joint(j).matrix) for j in rig.joint_ids()
    )
    check("build: every control sits on its joint", worst < 1e-5, worst)
    rig.set_state(random_state(rig.model(), random.Random(5)), key=False)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)
    worst = max(
        mdiff(rig.control(j).matrix, rig.joint(j).matrix) for j in rig.joint_ids()
    )
    check("build: ... and stays on it, posed", worst < TOL, worst)
    rig.set_state([0.0] * len(rig.state()), key=False)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 1.0)

    # The telescope's arrow floats clear of its tubes, in the arm's plane.
    widget = rig.control("LEG_4").custom_shape
    cvs = [tuple(p.co[:3]) for s in widget.data.splines for p in s.points]
    span = max(abs(p[0]) for p in cvs)
    to_joint = (
        armature.matrix_world
        @ armature.data.bones[rig.joint("LEG_4").name].matrix_local
    ).inverted()
    reach = max(
        math.hypot(q.y, q.z)
        for part in ("LEG_3", "LEG_4")
        for s in ArticulatedRig._shells(part)
        for q in (to_joint @ Vector(p) for p in s)
        if abs(q.x) <= span
    )
    check(
        "build: the telescope's arrow floats clear of its tubes",
        min(p[1] for p in cvs) > reach and max(abs(p[2]) for p in cvs) < reach,
        (min(p[1] for p in cvs), reach),
    )
    check("build: the controls draw in front", armature.show_in_front)

    arm, rest = setup()
    rig = build(arm, end_control=False)  # FK alone: mayatk's model-pose check
    check(
        "build: without an end control, no MCH bone and no ik variable",
        rig.end_control is None
        and rig.solver is None
        and not ArticulatedRig._mch_bones(rig.group)
        and all(
            [v.name for v in fc.driver.variables] == ["fk"]
            for fc in rig.group.animation_data.drivers
        ),
    )
    worst = 0.0
    rng = random.Random(3)
    for _ in range(8):
        state = random_state(rig.model(), rng)
        rig.set_state(state, key=False)
        worst = max(worst, model_gap(rig, state))
    check(
        "build: Blender poses the joints exactly as the model does", worst < TOL, worst
    )

    slide = rig.control("LEG_4")
    con = slide.constraints.get("Articulated Rig Location Limit")
    check(
        "build: a slide's control carries its travel alone, within its limits",
        tuple(slide.lock_location) == (False, True, True)
        and tuple(slide.lock_rotation) == (True, True, True)
        and con is not None
        and con.use_min_x
        and con.use_max_x
        and con.min_x < 0.0 < con.max_x
        and con.owner_space == "LOCAL"
        and con.use_transform_limit,
        (tuple(slide.lock_location), con and (con.min_x, con.max_x)),
    )
    base = rig.control("LEG_1")
    check(
        "build: the universal's control turns rx and rz, swivel outermost",
        tuple(base.lock_rotation) == (False, True, False)
        and base.rotation_mode == "ZYX",
        (tuple(base.lock_rotation), base.rotation_mode),
    )

    # A limit holds the joint: the driver reads the control as its limit leaves it.
    rig.set_state([0.0] * len(rig.state()), key=False)
    rig.set_limits("LEG_2", "rz", -10.0, 10.0)
    rig.control("LEG_2").rotation_euler[2] = math.radians(40.0)
    bpy.context.view_layer.update()
    state = [0.0] * len(rig.state())
    state[ptk.ArticulationPlan.slots(rig.spec).index(("LEG_2", "rz"))] = 10.0
    check(
        "build: a control past its limit leaves the joint at the limit",
        model_gap(rig, state) < TOL,
        model_gap(rig, state),
    )
    check(
        "build: ... and the pose reads the limit, as the joint shows it",
        abs(rig.channel_value("LEG_2", "rz") - 10.0) < 1e-4,
        rig.channel_value("LEG_2", "rz"),
    )
    rig.set_limits("LEG_2", "rz", None, None)

    arm, rest = setup()
    ob("LEG_2").keyframe_insert("location", index=0)
    bpy.ops.ed.undo_push(message="baseline")
    ob("ARM")["mark"] = 1
    bpy.ops.ed.undo_push(message="mark")
    plan = ArticulatedRig.analyze(arm)
    try:
        ArticulatedRig.create(plan["links"], plan["joints"])
        refused = False
    except ValueError as e:
        refused = "LEG_2" in str(e)
    check("build: a driven part is refused", refused)
    check(
        "build: ... and nothing was built",
        not [o for o in bpy.data.objects if o.name.endswith("_RIG")],
    )
    bpy.ops.ed.undo()
    check(
        "build: ... nor an undo step taken (one undo reverts the edit before it)",
        "mark" not in ob("ARM").keys(),
    )

    # One undo takes the whole build back.
    arm, rest = setup()
    bpy.ops.ed.undo_push(message="baseline")
    build(arm)
    bpy.ops.ed.undo()
    check(
        "build: one undo takes the whole build back",
        not [
            o
            for o in bpy.data.objects
            if o.name.endswith("_RIG") or o.name.startswith("WGT_")
        ]
        and not bpy.data.armatures
        and at_rest(rest) < 1e-6
        and ob("LEG_3").parent == ob("ARM"),
        [o.name for o in bpy.data.objects if o.name.endswith("_RIG")],
    )
    # ... and a build after an undo still drives its joints (the step pushed last).
    rig = build(arm, end_control=False)
    state = random_state(rig.model(), random.Random(11))
    rig.set_state(state, key=False)
    check(
        "build: a fresh build's drivers evaluate",
        model_gap(rig, state) < TOL,
        model_gap(rig, state),
    )

    # A handle outlives a rebuild made through another; a torn-down rig stays gone.
    other = ArticulatedRig.for_node(rig.control("LEG_2"))
    other.rebuild()
    check(
        "lookup: a handle outlives a rebuild made through another",
        other is not None
        and rig.group == other.group
        and rig.joint_ids() == other.joint_ids(),
    )
    check(
        "lookup: for_node finds the rig by a part",
        ArticulatedRig.for_node("HEAD").name == rig.name,
    )
    check(
        "lookup: ... and by a part of the fixed root link",
        ArticulatedRig.for_node("BASE").name == rig.name,
    )
    check(
        "lookup: ... and by a shell under a part",
        ArticulatedRig.for_node("ring").name == rig.name,
    )
    check(
        "lookup: ... and a stranger by nothing",
        ArticulatedRig.for_node("ASSET") is None,
    )
    check(
        "lookup: joint_id_of a part, a control bone and a shell",
        rig.joint_id_of("HEAD") == "HEAD"
        and rig.joint_id_of(rig.control("LEG_2")) == "LEG_2"
        and rig.joint_id_of("ring") == "HEAD"
        and rig.joint_id_of("BASE") is None,
    )
    armature = rig.group
    armature.name = "renamed_RIG"
    check(
        "lookup: the armature renamed is still the rig's",
        rig.group.name == "renamed_RIG" and rig.name == other.name,
    )
    rig.teardown()
    try:
        _ = other.group
        gone = False
    except RuntimeError:
        gone = True
    check("lookup: a torn-down rig stays gone", gone)

    # A Shift+D copy of the armature carries the record -- refused, alone or with its parts.
    arm, rest = setup()
    rig = build(arm)
    armature = rig.group
    for with_parts in (False, True):
        with CoreUtils.window_context_override():
            bpy.ops.object.select_all(action="DESELECT")
            armature.select_set(True)
            if with_parts:
                for child in armature.children_recursive:
                    child.select_set(True)
            bpy.context.view_layer.objects.active = armature
            before = set(bpy.data.objects)
            bpy.ops.object.duplicate()
        copy = next(
            o for o in bpy.data.objects if o not in before and o.type == "ARMATURE"
        )
        try:
            ArticulatedRig(copy)
            refused = False
        except ValueError:
            refused = True
        check(
            f"lookup: a Shift+D copy {'with its parts' if with_parts else 'alone'} is refused",
            refused and len(ArticulatedRig.scene_rigs()) == 1,
            [r.group.name for r in ArticulatedRig.scene_rigs()],
        )
    check(
        "lookup: the rig itself still stands", ArticulatedRig(armature).name == rig.name
    )

    arm, rest = setup()
    first = build(arm, name="arm")
    other_arm = build_arm()
    plan = ArticulatedRig.analyze(other_arm)
    second = ArticulatedRig.create(plan["links"], plan["joints"], name="arm")
    check(
        "build: a second rig of the same name is numbered",
        first.name != second.name and len(ArticulatedRig.scene_rigs()) == 2,
        (first.name, second.name),
    )

    # =========================================================== Teardown
    arm, rest = setup()
    attrs = ArticulatedRig.PART_ATTRS
    before = {}
    for p in PARTS:
        o = ob(p)
        before[p] = (
            o.parent,
            [list(r) for r in o.matrix_parent_inverse],
            {
                a: (
                    getattr(o, a)
                    if isinstance(getattr(o, a), (str, bool))
                    else list(getattr(o, a))
                )
                for a in attrs
            },
        )
    rig = build(arm)
    rig.set_state([10.0] * len(rig.state()), key=False)
    ob("LEG_3").location.x += 1.0  # a part's own channel moved on the rig
    ob("LEG_3").lock_scale[1] = True
    rig.teardown()
    check("teardown: every part back where it was", at_rest(rest) < 1e-6, at_rest(rest))
    exact = []
    for p in PARTS:
        o = ob(p)
        now = (
            o.parent,
            [list(r) for r in o.matrix_parent_inverse],
            {
                a: (
                    getattr(o, a)
                    if isinstance(getattr(o, a), (str, bool))
                    else list(getattr(o, a))
                )
                for a in attrs
            },
        )
        if now != before[p]:
            exact.append(p)
    check(
        "teardown: parent, parent inverse and every channel exactly as captured",
        not exact,
        exact,
    )
    leftovers = [
        o.name
        for o in bpy.data.objects
        if o.name.startswith(("arm_", "WGT_")) or o.name.endswith("_RIG")
    ]
    check(
        "teardown: nothing of the rig is left",
        not leftovers
        and not bpy.data.armatures
        and not bpy.data.curves
        and not bpy.data.actions,
        leftovers,
    )
    check(
        "teardown: the record is cleared",
        ptk.SceneRecords.ARTICULATION.load(DataNodes) is None,
    )

    # ============================================================= Limits
    arm, rest = setup()
    rig = build(arm, end_control=False)
    ctrl = rig.control("LEG_2")
    ctrl.rotation_euler[2] = math.radians(72.0)
    rig.set_limit_from_pose("LEG_2", "rz", "max")
    ctrl.rotation_euler[2] = math.radians(-35.0)
    rig.set_limit_from_pose("LEG_2", "rz", "min")
    con = ctrl.constraints.get("Articulated Rig Rotation Limit")
    check(
        "limits: from the pose, they bound the control",
        con.use_limit_z
        and abs(math.degrees(con.min_z) + 35.0) < 1e-4
        and abs(math.degrees(con.max_z) - 72.0) < 1e-4,
        (math.degrees(con.min_z), math.degrees(con.max_z)),
    )
    elbow = next(j for j in rig.record()["joints"] if j["name"].endswith("LEG_2_jnt"))
    check(
        "limits: ... and the record",
        abs(elbow["channels"][0]["min"] + 35.0) < 1e-4
        and abs(elbow["channels"][0]["max"] - 72.0) < 1e-4,
        elbow["channels"],
    )
    rig.set_limits("LEG_2", "rz", None, None)
    check("limits: freed, the control is unbounded", not con.use_limit_z)
    rig.set_weight("LEG_2", 2.0)
    elbow = next(j for j in rig.record()["joints"] if j["name"].endswith("LEG_2_jnt"))
    check(
        "limits: a weight reaches the record",
        elbow["channels"][0]["weight"] == 2.0,
        elbow["channels"],
    )

    # ============================================================ PostRig
    def key(rig, frames, seed=9):
        rng = random.Random(seed)
        model = rig.model()
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            rig.set_state(random_state(model, rng, 30, 0.8), key=True)
        return sample(frames)

    def sample(frames):
        out = {}
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            out[frame] = {p: world(p) for p in PARTS}
        return out

    def same(a, b, tol=TOL):
        return max(mdiff(a[f][p], b[f][p]) for f in a for p in a[f])

    arm, rest = setup()
    rig = build(arm)
    keyed = key(rig, [1, 12, 24])
    armature = rig.group
    curves_before = sorted(
        (fc.data_path, fc.array_index)
        for fc in __import__("blendertk").AnimUtils.get_fcurves([armature])
    )
    rig.rebuild()
    curves_after = sorted(
        (fc.data_path, fc.array_index)
        for fc in __import__("blendertk").AnimUtils.get_fcurves([armature])
    )
    check(
        "postrig: a rebuild carries the animation across",
        same(keyed, sample([1, 12, 24])) < TOL,
        same(keyed, sample([1, 12, 24])),
    )
    check(
        "postrig: ... on the same armature, its curves bound to the same control bones",
        rig.group == armature
        and curves_before == curves_after
        and all(armature.path_resolve(p, False) is not None for p, _i in curves_after),
        len(curves_after),
    )

    arm, rest = setup()
    rig = build(arm)
    rig.group.location.z += 15.0
    moved = {p: world(p) for p in PARTS[1:]}
    rig.rebuild()
    worst = max(mdiff(moved[p], world(p)) for p in PARTS[1:])
    check("postrig: a moved armature stays moved through a rebuild", worst < TOL, worst)

    arm, rest = setup()
    links = [["BASE"], ["LEG_1"], ["LEG_2"], ["LEG_3", "LEG_4"], ["HEAD"]]
    rig = build(arm, links=links)
    check("postrig: the rods as one link make four joints", len(rig.joint_ids()) == 4)
    key(rig, [1, 12, 24])
    bpy.context.scene.frame_set(12)
    move_end(rig, B((0.0, 4.0, 0.0)))
    rig.end_control.keyframe_insert("location", frame=12)
    keyed = sample([1, 12, 24])
    rig.insert_joint(["LEG_4"])
    spec = rig.spec
    added = next((j for j in spec["joints"] if j["id"] == "LEG_4"), None)
    head = next(j for j in spec["joints"] if j["id"] == "HEAD")
    check(
        "postrig: a telescope added after the fact is a slide the head hangs off",
        added is not None
        and added["type"] == "slide"
        and head["parent"] == added["link"],
        added and added["type"],
    )
    check(
        "postrig: ... joining the end control's solve at give 0",
        added is not None and added["weights"] == {"tx": 0.0},
        added and added["weights"],
    )
    check(
        "postrig: ... and every key holds, the end control's too",
        same(keyed, sample([1, 12, 24])) < TOL,
        same(keyed, sample([1, 12, 24])),
    )
    bpy.context.scene.frame_set(12)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)
    head_before = world("HEAD")
    rig.control("LEG_4").location[0] = 1.5
    check("postrig: ... and it slides", mdiff(head_before, world("HEAD")) > 1e-3)

    # A split-off part named like a standing joint's id takes a fresh id.
    arm, rest = setup()
    links = [["BASE"], ["LEG_1"], ["LEG_2"], ["LEG_3", "LEG_4"], ["HEAD"]]
    rig = build(arm, links=links)
    ctrl = rig.control("LEG_2")
    rig.set_state([0.0] * len(rig.state()), key=False)
    bpy.context.scene.frame_set(1)
    ctrl.rotation_euler[2] = math.radians(10)
    ctrl.keyframe_insert("rotation_euler", index=2, frame=1)
    ctrl.rotation_euler[2] = math.radians(40)
    ctrl.keyframe_insert("rotation_euler", index=2, frame=24)
    ob("LEG_2").name = "ELBOW"
    ob("LEG_4").name = "LEG_2"
    rig.insert_joint(["LEG_2"], joint_type="slide")
    ids = rig.joint_ids()
    check(
        "postrig: a split-off part named like a joint leaves that joint its keys",
        len(set(ids)) == 5
        and "LEG_2" in ids
        and keys(rig.control("LEG_2"), "rz") == [1.0, 24.0],
        (ids, keys(rig.control("LEG_2"), "rz")),
    )

    arm, rest = setup()
    rig = build(arm)
    key(rig, [1, 24])
    bpy.ops.ed.undo_push(message="keys")  # an animator's keys are a step of their own
    rig.remove_joint("LEG_4")
    check(
        "postrig: folding a joint back keeps the others' animation",
        len(rig.joint_ids()) == 4
        and keys(rig.control("LEG_2"), "rz") == [1.0, 24.0]
        and rig.end_control is not None,
        (rig.joint_ids(), keys(rig.control("LEG_2"), "rz")),
    )
    bpy.ops.ed.undo()
    check(
        "postrig: one undo takes the fold back, keys and all",
        len(rig.joint_ids()) == 5
        and keys(rig.control("LEG_4"), "tx") == [1.0, 24.0]
        and keys(rig.control("LEG_2"), "rz") == [1.0, 24.0],
        (
            rig.joint_ids(),
            keys(rig.control("LEG_4"), "tx"),
            keys(rig.control("LEG_2"), "rz"),
        ),
    )
    bpy.context.scene.frame_set(1)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)  # the pose is FK alone
    slots = rig._slots()[2:4]
    rig.set_state([12.0, -7.0], key=False, slots=slots)
    check(
        "postrig: after an undo the joints' drivers (limits clamped) still pose them",
        model_gap(rig, rig.state()) < TOL,
        model_gap(rig, rig.state()),
    )
    check(
        "state: set_state over the slots given touches those alone",
        [round(v, 4) for v in rig.state(slots)] == [12.0, -7.0]
        and abs(rig.channel_value(*ptk.ArticulationPlan.slots(rig.spec)[2]) - 12.0)
        < 1e-4,
        rig.state(slots),
    )

    arm, rest = setup()
    rig = build(arm)
    key(rig, [1, 24])
    armature = rig.group
    joint = next(j for j in rig.spec["joints"] if j["id"] == "LEG_2")
    # In double, as edit_joint reads it (float32 would leave a hair off the aim).
    aim = ptk.ArticulationPlan._vector(
        joint["aim"], ArticulatedRig._plan(armature.matrix_world)
    )
    try:
        rig.edit_joint("LEG_2", normal=aim)  # a hinge axis along the link
        refused = False
    except ValueError:
        refused = True
    check(
        "postrig: a refused edit leaves the rig and its keys standing",
        refused
        and armature.name in bpy.data.objects
        and keys(rig.control("LEG_2"), "rz") == [1.0, 24.0]
        and keys(rig.control("LEG_4"), "tx") == [1.0, 24.0],
    )

    arm, rest = setup()
    rig = build(arm)
    key(rig, [1, 24])
    with CoreUtils.window_context_override():
        bpy.ops.object.select_all(action="DESELECT")
        for o in [ob("HEAD")] + list(ob("HEAD").children):
            o.select_set(True)
        bpy.ops.object.delete()
    try:
        rig.rebuild()
        message = ""
    except ValueError as e:
        message = str(e)
    check(
        "postrig: a rebuild missing a part names it and leaves the rig standing",
        re.search("no longer in the scene.*HEAD", message) is not None
        and rig.group is not None
        and keys(rig.control("LEG_2"), "rz") == [1.0, 24.0],
        message,
    )

    arm, rest = setup()
    rig = build(arm)
    key(rig, [1, 24])
    rig.edit_joint("LEG_2", type="ball")
    ctrl = rig.control("LEG_2")
    check(
        "postrig: a retyped joint keeps the channels both types share",
        tuple(ctrl.lock_rotation) == (False, False, False)
        and keys(ctrl, "rz") == [1.0, 24.0],
        (tuple(ctrl.lock_rotation), keys(ctrl, "rz")),
    )
    log = captured()
    rig.edit_joint("LEG_3", type="slide")
    ArticulatedRig.logger.removeHandler(log)
    check(
        "postrig: a channel the new type lacks loses its curve, with a warning",
        keys(rig.control("LEG_3"), "rz") == []
        and any("LEG_3.rz" in m for m in log.messages),
        log.messages,
    )

    # ============================================================= Record
    arm, rest = setup()
    rig = build(arm)
    record = ArticulatedRig.export_record(None)
    payload = record.payload if record is not None else {}
    entry = (payload.get("rigs") or [{}])[0]
    check(
        "record: one rig, its joints parent first",
        payload.get("version") == ptk.SceneRecords.ARTICULATION.version
        and [j["parent"] for j in entry.get("joints", [])] == [None, 0, 1, 2, 3],
        entry.get("joints") and [j["parent"] for j in entry["joints"]],
    )
    check(
        "record: the grab names every moving part",
        sorted(g["node"] for g in entry.get("grab", [])) == sorted(PARTS[1:]),
    )
    model = ptk.ArticulationModel(entry)
    check("record: the model's channels", len(model.channels) == 2 + 1 + 1 + 1 + 3)
    stored = ptk.SceneRecords.ARTICULATION.load(DataNodes)
    check(
        "record: published at build for any export to carry",
        stored is not None and stored["rigs"][0]["name"] == rig.name,
    )
    data = rig.spec
    check(
        "record: the stamped plan has mayatk's shape",
        set(data)
        == {"version", "name", "links", "joints", "group_uuid", "parts", "end"}
        and set(data["end"]) == {"part", "frame"}
        and len(data["end"]["frame"]) == 16
        and all(set(m) == {"uuid", "name"} for link in data["links"] for m in link)
        and all(set(p) == {"uuid", "name", "parent", "attrs"} for p in data["parts"]),
        sorted(data),
    )
    ob("HEAD").name = "LENS"
    grabbed = [g["node"] for g in rig.record()["grab"]]
    check(
        "record: the grab names a part renamed after the build",
        "LENS" in grabbed and "HEAD" not in grabbed,
        grabbed,
    )
    rig.set_state(
        [15.0] * len(rig.state()), key=False
    )  # posed: the record must not care
    armature = rig.group
    gaps = []
    for joint, rec in zip(rig.spec["joints"], rig.record()["joints"]):
        bone = armature.data.bones[rig.joint(joint["id"]).name]
        local = (
            bone.parent.matrix_local.inverted() @ bone.matrix_local
            if bone.parent
            else bone.matrix_local
        )
        q = Quaternion((rec["q"][3], *rec["q"][:3]))
        turn = local.to_quaternion().rotation_difference(q).angle
        gaps.append(max(turn, (local.translation - Vector(rec["t"])).length / 100.0))
    check(
        "record: the rest in the record is the rest on the bones",
        max(gaps) < 1e-5,
        max(gaps),
    )

    # ======================================================= Conformance
    # The conformance rig whose joints are the engine's types, built through the
    # engine from a plan read off its record; every case's state posed by Blender.
    doc = ptk.ArticulationConformance.cases(seed=0, per_rig=4)
    source = doc["rigs"]["arm"]
    rig = conformance_rig(source, "arm")
    rest_gap = max(
        max(abs(a - b) for a, b in zip(r["t"], j["t"]))
        + min(
            sum(abs(a - b) for a, b in zip(r["q"], j["q"])),
            sum(abs(a + b) for a, b in zip(r["q"], j["q"])),
        )
        for r, j in zip(rig.record()["joints"], source["joints"])
    )
    check(
        "conformance: the plan read off the record ships that record's rest",
        rest_gap < 1e-5,
        rest_gap,
    )
    worst = 0.0
    count = 0
    for case in doc["cases"]:
        if case["rig"] != "arm":
            continue
        count += 1
        rig.set_state(case["state"], key=False)
        for i, (p, q) in enumerate(case["world"]):
            worst = max(
                worst, mdiff(model_matrix(p, q), rig.joint(rig.joint_ids()[i]).matrix)
            )
    check(
        "conformance: Blender poses every case of the rig as the model does",
        count == 4 and worst < TOL,
        (count, worst),
    )

    # ======================================================== End control
    # mayatk's TestEndControl: an IK target the stand follows, solved by blendertk's
    # handlers running the model the Grab Tool and the runtimes run.
    arm, rest = setup()
    rig = build(arm)
    end = rig.end_control
    armature = rig.group
    check(
        "end: built by default, its solve the installed handler",
        end is not None
        and rig.solver is not None
        and SolverHandler.installed()
        and rig.SOLVER_TYPE == "handler",
        (end, rig.solver),
    )
    check("end: moves nothing at rest", at_rest(rest) < TOL, at_rest(rest))
    head_frame = armature.matrix_world @ rig.joint("HEAD").matrix
    check(
        "end: aligned with the head's joint",
        turn_gap(end_turn(rig), head_frame.to_quaternion()) < 1e-5,
        turn_gap(end_turn(rig), head_frame.to_quaternion()),
    )
    local = [
        head_frame.inverted() @ Vector(p)
        for s in ArticulatedRig._shells("HEAD")
        for p in s
    ]
    centre = head_frame @ Vector(
        [0.5 * (min(p[i] for p in local) + max(p[i] for p in local)) for i in range(3)]
    )
    check(
        "end: its pivot at the centre of the head's geometry",
        (end_world(rig) - centre).length < 1e-4,
        (end_world(rig) - centre).length,
    )
    to_world = armature.matrix_world @ end.matrix
    box = [
        to_world @ Vector(p.co[:3])
        for spline in end.custom_shape.data.splines
        for p in spline.points
    ]
    head_points = [p for s in ArticulatedRig._shells("HEAD") for p in s]
    check(
        "end: its box holds the whole head",
        all(
            min(b[i] for b in box) <= min(p[i] for p in head_points) + 1e-5
            and max(b[i] for b in box) >= max(p[i] for p in head_points) - 1e-5
            for i in range(3)
        ),
    )
    check(
        "end: keyable translate and rotate (a ball end), the IK blend and the follow",
        tuple(end.lock_location) == (False,) * 3
        and tuple(end.lock_rotation) == (False,) * 3
        and tuple(end.lock_scale) == (True,) * 3
        and end.get(ArticulatedRig.BLEND_ATTR) == 1.0
        and end.get(ArticulatedRig.FOLLOW_ATTR) is True
        and not end.bone.use_deform,
        dict(end.items()),
    )
    check(
        "end: one of the rig's controls, drawn by its shape, in no collection",
        end in rig.controls()
        and end.custom_shape is not None
        and not end.custom_shape.users_collection,
    )
    mch = ArticulatedRig._mch_bones(armature)
    check(
        "end: each FK control hangs under its MCH bone, each deform driver sums both",
        len(mch) == len(rig.joint_ids())
        and all(
            rig.control(j).parent.name == rig.control(j).name + rig.IK_GROUP_SUFFIX
            for j in rig.joint_ids()
        )
        and all(
            [v.name for v in fc.driver.variables] == ["fk", "ik"]
            for fc in armature.animation_data.drivers
        )
        and not any(armature.data.bones[n].use_deform for n in mch),
        mch,
    )

    arm, rest = setup()
    rig = build(arm)
    pivot = end_world(rig)
    move_end(rig, B((-7.0, 9.0, 6.0)))
    target = end_world(rig)
    check(
        "end: moving the end control brings the head along",
        (head_point(rest, pivot) - target).length < 0.05,
        (head_point(rest, pivot) - target).length,
    )
    check(
        "end: ... the head keeping its turn",
        turn_gap(world("HEAD").to_quaternion(), rest["HEAD"].to_quaternion()) < 1e-4,
        turn_gap(world("HEAD").to_quaternion(), rest["HEAD"].to_quaternion()),
    )
    check(
        "end: ... FK untouched, the pose FK plus the solve",
        rig.fk_state() == [0.0] * len(rig.fk_state()) and rig.state() != rig.fk_state(),
    )
    worst = max(
        max(
            (
                rig.control(j).matrix.translation - rig.joint(j).matrix.translation
            ).length,
            turn_gap(
                rig.control(j).matrix.to_quaternion(),
                rig.joint(j).matrix.to_quaternion(),
            ),
        )
        for j in rig.joint_ids()
    )
    check("end: ... and the FK controls ride their joints", worst < 1e-4, worst)
    check(
        "end: Blender shows the model of the pose the end control made",
        model_gap(rig, rig.state()) < TOL,
        model_gap(rig, rig.state()),
    )

    arm, rest = setup()
    rig = build(arm)
    before = world("HEAD").to_quaternion()
    control_before = end_turn(rig)
    rotate_end(rig, (0.0, 25.0, 10.0))
    wanted = end_turn(rig) @ control_before.inverted() @ before
    check(
        "end: rotating the end control turns the ball head",
        turn_gap(world("HEAD").to_quaternion(), wanted) < 1e-3,
        turn_gap(world("HEAD").to_quaternion(), wanted),
    )

    arm, rest = setup()
    rig = build(arm)
    set_switch(rig, ArticulatedRig.FOLLOW_ATTR, False)
    rotate_end(rig, (0.0, 40.0, 0.0))
    move_end(rig, B((-5.0, 6.0, 0.0)))
    slots = [
        i
        for i, (j, _c) in enumerate(ptk.ArticulationPlan.slots(rig.spec))
        if j == "HEAD"
    ]
    state = rig.state()
    check(
        "end: follow rotation off keeps the head's own turn",
        [state[i] for i in slots] == [0.0, 0.0, 0.0]
        and model_gap(rig, state) < TOL
        and at_rest({"LEG_2": rest["LEG_2"]}) > 1e-3,
        [state[i] for i in slots],
    )

    arm, rest = setup()
    rig = build(arm)
    move_end(rig, B((-7.0, 9.0, 6.0)))
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)
    check(
        "end: IK blend 0 is the FK pose",
        at_rest(rest) < TOL and rig.state() == rig.fk_state(),
        at_rest(rest),
    )

    arm, rest = setup()
    rig = build(arm)
    scene = bpy.context.scene
    pivot = end_world(rig)
    targets = {}
    for frame, offset in ((1, (0.0, 0.0, 0.0)), (10, B((-6.0, 8.0, 4.0)))):
        scene.frame_set(frame)
        rig.end_control.location = (0.0, 0.0, 0.0)
        move_end(rig, offset)
        rig.end_control.keyframe_insert("location", frame=frame)
        targets[frame] = end_world(rig)
    misses = []
    for frame in (10, 1, 10, 4):
        scene.frame_set(frame)
        if frame in targets:
            misses.append((head_point(rest, pivot) - targets[frame]).length)
    check(
        "end: the end control's keys play back (frame_set, the live solve)",
        max(misses) < 0.05 and len(misses) == 3,
        misses,
    )
    scene.frame_set(4)
    check(
        "end: ... between its keys too (Blender shows the model of the frame's pose)",
        model_gap(rig, rig.state()) < TOL and rig.state() != rig.fk_state(),
        model_gap(rig, rig.state()),
    )

    arm, rest = setup()
    rig = build(arm)
    pivot = end_world(rig)
    move_end(rig, B((-4.0, 5.0, 0.0)))
    target = end_world(rig)
    elbow = world("LEG_2")
    rig.control("LEG_2").rotation_euler[2] = math.radians(25.0)  # the seed moves...
    update()
    check(
        "end: FK shapes the arm under a pinned end",
        mdiff(elbow, world("LEG_2")) > 1e-3  # ...the arm too
        and (head_point(rest, pivot) - target).length < 0.05,
        (mdiff(elbow, world("LEG_2")), (head_point(rest, pivot) - target).length),
    )

    arm, rest = setup()
    rig = build(arm)
    move_end(rig, B((-6.0, 7.0, 5.0)))
    posed = {p: world(p) for p in PARTS}
    off = rig.switch_ik(key=False)
    jump = max(mdiff(posed[p], world(p)) for p in PARTS)
    check(
        "end: switching to FK never jumps",
        off is False and rig.ik_blend() == 0.0 and jump < 1e-4,
        (off, rig.ik_blend(), jump),
    )
    rig.set_state(random_state(rig.model(), random.Random(2), spread=20), key=False)
    fk = {p: world(p) for p in PARTS}
    on = rig.switch_ik(key=False)
    jump = max(mdiff(fk[p], world(p)) for p in PARTS)
    check(
        "end: ... nor back to IK (the end control matched first)",
        on is True and rig.ik_blend() == 1.0 and jump < 1e-4,
        (on, rig.ik_blend(), jump),
    )
    rig.set_state(random_state(rig.model(), random.Random(7), spread=15), key=False)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)
    fk = {p: world(p) for p in PARTS}
    rig.match_end_control(key=False)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 1.0)
    jump = max(mdiff(fk[p], world(p)) for p in PARTS)
    check("end: match, then the blend up, moves nothing", jump < 1e-4, jump)

    arm, rest = setup()
    rig = build(arm)
    rig.end_control.rotation_mode = "QUATERNION"  # an animator's choice
    rig.set_state(random_state(rig.model(), random.Random(7), spread=15), key=False)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)
    fk = {p: world(p) for p in PARTS}
    rig.match_end_control(key=True)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 1.0)
    jump = max(mdiff(fk[p], world(p)) for p in PARTS)
    check(
        "end: a quaternion end control matches and keys its own rotation",
        jump < 1e-4
        and keys_of(rig.end_control, ".rotation_quaternion", 0) == [1.0]
        and keys(rig.end_control, "tx") == [1.0],
        jump,
    )

    SolverHandler.uninstall()
    try:
        flips = [rig.switch_ik(key=False) for _ in range(3)]
        inert = rig.solver is None
    finally:
        ArticulatedRig.install()
    check(
        "end: an inert end control still switches both ways",
        inert and flips == [False, True, False],
        flips,
    )

    arm, rest = setup()
    rig = build(arm)
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    try:
        rig.switch_ik(False)
        blend_keys = keys_of(rig.end_control, f'["{ArticulatedRig.BLEND_ATTR}"]', 0)
        fk_keys = keys(rig.control("LEG_2"), "rz")
    finally:
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    check(
        "end: switching keys with auto key",
        blend_keys == [1.0] and fk_keys == [1.0],
        (blend_keys, fk_keys),
    )

    arm, rest = setup()
    rig = build(arm)
    move_end(rig, B((-6.0, 7.0, 5.0)))
    rig.set_state([5.0] * len(rig.fk_state()), key=False)
    rig.reset_pose(key=False)
    check("end: rest pose puts every control back", at_rest(rest) < TOL, at_rest(rest))

    arm, rest = setup()
    rig = build(arm)
    scene.frame_set(1)
    rig.end_control.keyframe_insert("location", frame=1)
    scene.frame_set(10)
    move_end(rig, B((0.0, 6.0, 0.0)))
    rig.end_control.keyframe_insert("location", frame=10)
    rig.end_control[ArticulatedRig.BLEND_ATTR] = 0.5
    rig.end_control.keyframe_insert(f'["{ArticulatedRig.BLEND_ATTR}"]', frame=10)
    scene.frame_set(7)
    before = {p: world(p) for p in PARTS}
    rig.rebuild()
    worst = max(mdiff(before[p], world(p)) for p in PARTS)
    check(
        "end: a rebuild carries the end control's animation",
        worst < TOL
        and keys(rig.end_control, "tz") == [1.0, 10.0]
        and keys_of(rig.end_control, f'["{ArticulatedRig.BLEND_ATTR}"]', 0) == [10.0],
        (worst, keys(rig.end_control, "tz")),
    )

    arm, rest = setup()
    rig = build(arm)
    scene.frame_set(1)
    move_end(rig, B((0.0, 3.0, 0.0)))
    rig.end_control.keyframe_insert("location", frame=1)
    log = captured()
    rig.set_end_control(False)
    ArticulatedRig.logger.removeHandler(log)
    armature = rig.group
    check(
        "end: the end control drops, its solve with it",
        rig.end_control is None
        and rig.solver is None
        and not ArticulatedRig._mch_bones(armature)
        and armature.data.collections.get(ArticulatedRig.MCH_COLLECTION) is None
        and all(
            [v.name for v in fc.driver.variables] == ["fk"]
            for fc in armature.animation_data.drivers
        )
        and at_rest(rest) < TOL,
        at_rest(rest),
    )
    check(
        "end: ... its animation dropped with a warning",
        not [
            fc
            for fc in __import__("blendertk").AnimUtils.get_fcurves([armature])
            if "_end" in fc.data_path
        ]
        and any(ArticulatedRig.END_KEY in m for m in log.messages),
        log.messages,
    )
    rig.set_end_control(True, part="LEG_3")
    check(
        "end: ... and comes back on another link, moving nothing",
        rig.end_control is not None
        and ptk.ArticulationPlan.end_joint(rig.spec) == rig.joint_ids().index("LEG_3")
        and at_rest(rest) < TOL,
        at_rest(rest),
    )
    move_end(rig, B((0.0, 5.0, 0.0)))
    check(
        "end: ... where it moves its new link, one end control",
        mdiff(rest["LEG_3"], world("LEG_3")) > 1e-3
        and sum(b.name.endswith("_end_CTRL") for b in rig.group.data.bones) == 1,
        mdiff(rest["LEG_3"], world("LEG_3")),
    )
    try:
        rig.set_end_control(True, part="BASE")
        refused = False
    except ValueError:
        refused = True
    check("end: ... and refuses the fixed root link", refused)

    arm, rest = setup()
    rig = build(arm)
    rig.set_weight("LEG_1", 0.25)
    base = next(j for j in rig.record()["joints"] if j["name"].endswith("LEG_1_jnt"))
    check(
        "end: a give reaches the record",
        [c["weight"] for c in base["channels"]] == [0.25, 0.25],
        base["channels"],
    )
    rig.set_weight("LEG_1", 0.0)
    move_end(rig, B((-6.0, 7.0, 5.0)))
    slots = [
        i
        for i, (j, _c) in enumerate(ptk.ArticulationPlan.slots(rig.spec))
        if j == "LEG_1"
    ]
    held = mdiff(rest["LEG_1"], world("LEG_1"))
    check(
        "end: ... and the solve: give 0 holds the joint",
        [rig.state()[i] for i in slots] == [0.0, 0.0] and held < TOL,
        ([rig.state()[i] for i in slots], held),
    )
    rig.set_weight("LEG_1", 1.0)  # no rebuild, no viewport edit: the solve takes it now
    check(
        "end: ... and give 1 moves it at once",
        [rig.state()[i] for i in slots] != [0.0, 0.0]
        and mdiff(rest["LEG_1"], world("LEG_1")) > 1e-3
        and model_gap(rig, rig.state()) < TOL,
        mdiff(rest["LEG_1"], world("LEG_1")),
    )
    try:
        rig.set_weight("LEG_1", -1.0)
        refused = False
    except ValueError:
        refused = True
    check("end: ... and a negative give is refused", refused)

    arm, rest = setup()
    rig = build(arm, end_control=False)
    rig.set_end_control(True)
    move_end(rig, B((-6.0, 7.0, 5.0)))
    actual = rig.channel_value("LEG_2", "rz")
    taken = rig.set_limit_from_pose("LEG_2", "rz", "max" if actual > 0 else "min")
    check(
        "end: limits from the pose read the pose the end control made",
        abs(actual) > 1e-2 and abs(taken - actual) < 1e-6,
        (actual, taken),
    )
    rig.set_limits("LEG_2", "rz", -10.0, 10.0)
    move_end(rig, B((-12.0, 14.0, 0.0)))
    check(
        "end: the end control never takes a joint past its limit",
        abs(rig.channel_value("LEG_2", "rz")) <= 10.0 + 1e-6
        and abs(
            deform_state(rig)[
                ptk.ArticulationPlan.slots(rig.spec).index(("LEG_2", "rz"))
            ]
        )
        <= 10.0 + 1e-4,
        rig.channel_value("LEG_2", "rz"),
    )

    # A rig whose solve is missing -- a session that never installed the handlers --
    # is repaired in place: no rebuild, so its animation stays where it is.
    arm, rest = setup()
    rig = build(arm)
    armature = rig.group
    ArticulatedRig.uninstall()
    try:
        inert = rig.solver is None
        move_end(rig, B((-6.0, 7.0, 5.0)))
        still = at_rest(rest)
        repaired = ArticulatedRig.repair_scene()
        again = ArticulatedRig.repair_scene()
    finally:
        ArticulatedRig.install()
    check(
        "end: a rig whose solve is missing is repaired in place",
        inert
        and still < TOL
        and repaired == 1
        and again == 0
        and rig.group == armature
        and rig.solver is not None
        and mdiff(rest["HEAD"], world("HEAD")) > 1e-3,
        (inert, still, repaired, again),
    )
    check(
        "end: ensure_solver says the end control works",
        rig.ensure_solver() is True,
    )
    ArticulatedRig.uninstall()
    ArticulatedRig.export_record(None)  # an export's producer: the first touch
    check(
        "end: an export's producer arms the solve (a headless export)",
        SolverHandler.installed(),
    )

    # ============================================================== Solve
    # mayatk's TestSolverExpression, through the real rig: every conformance solve
    # case of the rigs whose joints are the engine's types -- the end control placed on
    # the case's target (and turn), the FK controls on its seed, the joints read back
    # off the deform bones as Blender evaluated them.
    tolerance = doc["tolerance"]["solve"]
    checked = skipped = 0
    worst = 0.0
    failures = []
    for rig_name in ("arm", "ball_xyz"):
        source = doc["rigs"][rig_name]
        model = ptk.ArticulationModel(source)
        rig = conformance_rig(source, rig_name, end_control=True)
        current = None
        for index, case in enumerate(doc["cases"]):
            if case["rig"] != rig_name:
                continue
            for solve in case["solve"]:
                joint = solve["joint"]
                turns = {c for j, c in model.channels if j == joint}
                if {"rx", "ry", "rz"} <= turns and solve["rotation"] is None:
                    skipped += 1  # a ball keeping its own turn is carried (below)
                    continue
                if current != (joint, tuple(solve["local"])):
                    rig = end_on(rig, joint, solve["local"])
                    current = (joint, tuple(solve["local"]))
                rig.set_state(solve["from"], key=False)
                x, y, z, w = solve["rotation"] or (0.0, 0.0, 0.0, 1.0)
                place_end(
                    rig,
                    Matrix.LocRotScale(
                        Vector(solve["target"]), Quaternion((w, x, y, z)), None
                    ),
                )
                chain = model.chain(joint)
                want = [
                    expect if model.channels[slot][0] in chain else start
                    for slot, (expect, start) in enumerate(
                        zip(solve["expect"], solve["from"])
                    )
                ]
                got = deform_state(rig)
                gap = max(abs(g - w_) for g, w_ in zip(got, want))
                worst = max(worst, gap)
                if gap >= tolerance:
                    failures.append((rig_name, index, joint, round(gap, 6)))
                checked += 1
    total = sum(
        len(c["solve"]) for c in doc["cases"] if c["rig"] in ("arm", "ball_xyz")
    )
    check(
        "solve: the rig's joints match every conformance solve case (the model's)",
        not failures and checked + skipped == total and checked > skipped,
        (checked, skipped, worst, failures[:4]),
    )

    # The joints read their controls' channels as they are: a ball wound past the
    # Euler triple a transform read decomposes to (the one nearest zero) keeps its
    # own -- the sum with the solve's offset is channel by channel.
    rig = conformance_rig(doc["rigs"]["ball_xyz"], "ball_xyz")
    wound = [10.0, -170.0, 70.0, 165.0]  # the post's rz; the ball's rx, ry, rz
    rig.set_state(wound, key=False)
    gap = max(abs(a - b) for a, b in zip(deform_state(rig), wound))
    check(
        "solve: a ball wound past the nearest-zero triple keeps its own channels",
        gap < 1e-4 and model_gap(rig, wound) < TOL,
        (gap, model_gap(rig, wound)),
    )

    # followRotation off: the ball keeps its FK turn and the chain above carries it
    # -- the held point solved on the parent's link.
    rng = random.Random(5)
    gaps, compared = [], 0
    for rig_name in ("arm", "ball_xyz"):
        source = doc["rigs"][rig_name]
        model = ptk.ArticulationModel(source)
        joint = len(model.joints) - 1
        parent = model.joints[joint]["parent"]
        pivot = [rng.uniform(-1.0, 1.0) for _ in range(3)]
        rig = end_on(conformance_rig(source, rig_name, end_control=True), joint, pivot)
        set_switch(rig, ArticulatedRig.FOLLOW_ATTR, False)
        for _ in range(6):
            seed = ptk.ArticulationConformance._random_state(model, rng)
            goal = model.point(
                ptk.ArticulationConformance._random_state(model, rng), joint, pivot
            )
            held = model.to_local(seed, parent, model.point(seed, joint, pivot))
            want = model.solve(seed, parent, held, goal)
            rig.set_state(seed, key=False)
            place_end(rig, Matrix.Translation(Vector(goal)))
            gaps.append(max(abs(g - w_) for g, w_ in zip(deform_state(rig), want)))
            compared += 1
    check(
        "solve: a ball keeping its own turn is carried by its parent's link",
        compared == 12 and max(gaps) < 5e-4,
        (compared, max(gaps)),
    )

    # Past the reach a step is halved until it brings the held point nearer, and the
    # solve stops when none does: the arm settles where it comes nearest -- along a
    # drag, each solve seeded with where the last one settled.
    rig_source = doc["rigs"]["arm"]
    model = ptk.ArticulationModel(rig_source)
    compared, gaps = 0, []
    for joint, turned, stream in ((3, False, 21), (4, True, 8)):
        rng = random.Random(stream)
        local = [rng.uniform(-1.5, 1.5) for _ in range(3)]
        rig = end_on(conformance_rig(rig_source, "arm", end_control=True), joint, local)
        chain = model.chain(joint)
        reach = math.dist(local, (0.0, 0.0, 0.0)) + sum(
            math.dist(model.joints[j]["t"], (0.0, 0.0, 0.0)) for j in chain[1:]
        )
        root = model.world(model.rest_state())[chain[0]][0]
        way = [rng.gauss(0.0, 1.0) for _ in range(3)]
        way = [c / math.dist(way, (0.0, 0.0, 0.0)) for c in way]
        seed = ptk.ArticulationConformance._random_state(model, rng)
        hand = ptk.ArticulationConformance._random_state(model, rng)
        rotation = list(model.world(hand)[joint][1]) if turned else None
        for k in (1.2, 1.2, 1.6, 2.0, 2.0):
            target = [r + c * k * reach for r, c in zip(root, way)]
            want = model.solve(seed, joint, local, target, rotation)
            rig.set_state(seed, key=False)
            x, y, z, w = rotation or (0.0, 0.0, 0.0, 1.0)
            place_end(
                rig, Matrix.LocRotScale(Vector(target), Quaternion((w, x, y, z)), None)
            )
            expected = [
                w_ if model.channels[slot][0] in chain else s
                for slot, (w_, s) in enumerate(zip(want, seed))
            ]
            gaps.append(max(abs(g - e) for g, e in zip(deform_state(rig), expected)))
            compared += 1
            seed = want
    check(
        "solve: an out-of-reach target settles as the model does",
        compared == 10 and max(gaps) < tolerance,
        (compared, max(gaps)),
    )

    # ========================================================== Bake
    # The file plays its IK without blendertk: every save bakes the solve onto the MCH
    # channels when anything it reads is animated; the handlers own those channels
    # while installed (the bake muted), and a reinstall / an uninstall flip it.
    store = ptk.TempArtifacts("btk_articulated_rig_test", policy="session")
    out_dir = store.dir_path()
    arm, rest = setup()
    rig = build(arm)
    scratch = os.path.join(out_dir, "articulated_static.blend")
    bpy.ops.wm.save_as_mainfile(filepath=scratch, copy=True)
    check(
        "bake: a rig with nothing animated saves no bake",
        not ArticulatedRig._bake_curves(rig.group),
    )
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 10
    pivot = end_world(rig)
    for frame, offset in ((1, (0.0, 0.0, 0.0)), (10, B((-6.0, 8.0, 4.0)))):
        scene.frame_set(frame)
        rig.end_control.location = (0.0, 0.0, 0.0)
        move_end(rig, offset)
        rig.end_control.keyframe_insert("location", frame=frame)
    bpy.ops.wm.save_as_mainfile(filepath=scratch, copy=True)
    baked = ArticulatedRig._bake_curves(rig.group)
    check(
        "bake: a save bakes the solve on every MCH channel, a key a frame",
        len(baked) == len(rig.fk_state())
        and all(len(fc.keyframe_points) == 10 for fc in baked),
        [(fc.data_path, fc.array_index, len(fc.keyframe_points)) for fc in baked],
    )
    check(
        "bake: ... muted while the handlers run",
        baked and all(fc.mute for fc in baked),
    )
    # The live solve wins over a stale bake: re-key the end control, step the frame
    # twice (the second evaluation would restore a playing bake under the handler).
    scene.frame_set(10)
    rig.end_control.location = (0.0, 0.0, 0.0)
    move_end(rig, B((4.0, 6.0, -2.0)))
    rig.end_control.keyframe_insert("location", frame=10)
    target = end_world(rig)
    scene.frame_set(9)
    scene.frame_set(10)
    scene.frame_set(10)
    check(
        "bake: ... so a re-keyed end control plays live over the stale bake",
        (head_point(rest, pivot) - target).length < 0.05,
        (head_point(rest, pivot) - target).length,
    )
    ArticulatedRig.uninstall()
    unmuted = all(not fc.mute for fc in ArticulatedRig._bake_curves(rig.group))
    ArticulatedRig.install()
    check(
        "bake: ... unmuted without the handlers, muted again with them",
        unmuted and all(fc.mute for fc in ArticulatedRig._bake_curves(rig.group)),
    )

    # A save whose bake still holds keeps it -- solving every frame costs seconds on
    # a long timeline -- and any change to what it was made from bakes again: a setting
    # of an input curve's modifier, a key's interpolation, the bake edited by hand.
    arm, rest = setup()
    rig = build(arm)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 20
    for frame, offset in ((1, (0.0, 0.0, 0.0)), (10, B((-6.0, 8.0, 4.0)))):
        scene.frame_set(frame)
        rig.end_control.location = (0.0, 0.0, 0.0)
        move_end(rig, offset)
        rig.end_control.keyframe_insert("location", frame=frame)
    written = []
    keyed_curve = ArticulatedRig._key_curve

    def spy(bone, prop, index, frames, values):
        written.append((bone.name, prop, index))
        return keyed_curve(bone, prop, index, frames, values)

    def save():
        written.clear()
        bpy.ops.wm.save_as_mainfile(filepath=scratch, copy=True)
        return len(written)

    def input_curve(rig, prop, index):
        path = f'pose.bones["{rig.end_control.name}"].{prop}'
        return next(
            fc
            for fc in __import__("blendertk").AnimUtils.get_fcurves([rig.group])
            if fc.data_path == path and fc.array_index == index
        )

    slots = len(rig.fk_state())
    noise = input_curve(rig, "location", 0).modifiers.new("NOISE")
    noise.scale, noise.strength = 4.0, 2.0
    ArticulatedRig._key_curve = staticmethod(spy)
    try:
        first = save()
        scene.frame_set(4)  # a save on another frame is the same bake
        again = save()
        noise.strength = 6.0  # the end control shakes harder: keys and handles stay
        shaken = save()
        input_curve(rig, "location", 1).keyframe_points[0].interpolation = "CONSTANT"
        retimed = save()
        baked = ArticulatedRig._bake_curves(rig.group)
        baked[0].keyframe_points[3].co.y += 0.5
        edited = save()
        settled = save()
    finally:
        ArticulatedRig._key_curve = staticmethod(keyed_curve)
    check(
        "bake: a save whose bake still holds keeps it, on any frame; a curve "
        "modifier's setting, an interpolation or a hand edit bakes again",
        (first, again, shaken, retimed, edited, settled)
        == (slots, 0, slots, slots, slots, 0),
        (first, again, shaken, retimed, edited, settled),
    )
    scene.frame_set(15)  # past the end control's keys: the noise plays on
    index = ptk.ArticulationPlan.slots(rig.spec).index(("LEG_2", "rz"))
    live = rig.state()[index] - rig.fk_state()[index]
    curve = next(
        fc
        for fc in ArticulatedRig._bake_curves(rig.group)
        if "LEG_2" in fc.data_path and fc.array_index == 2
    )
    check(
        "bake: ... and the shaken end control is baked past its last key",
        abs(math.degrees(curve.evaluate(15)) - live) < 1e-3 and abs(live) > 1e-2,
        (math.degrees(curve.evaluate(15)), live),
    )

    # ============================================================= Render
    # An animation render (Ctrl F12) poses the rig with the solve, on the render's own
    # depsgraph, frame by frame: the end link's silhouette stands where the viewport
    # puts it (measured 0.1 px; with no solve it trails by up to 25 px).
    from bpy_extras.object_utils import world_to_camera_view

    reset()
    scene = bpy.context.scene
    names = []
    for i, x in enumerate((0.0, 4.0, 8.0, 12.0)):
        bpy.ops.mesh.primitive_cube_add(size=1.5, location=(x, 0.0, 0.0))
        bpy.context.active_object.name = f"P{i}"
        names.append(f"P{i}")
    joints = [
        {
            "link": i,
            "parent": i - 1,
            "type": "hinge",
            "position": [4.0 * i - 2.0, 0.0, 0.0],
            "aim": [1.0, 0.0, 0.0],
            "normal": [0.0, 0.0, 1.0],
            "limits": {},
            "size": 1.0,
        }
        for i in range(1, 4)
    ]
    rig = ArticulatedRig.create([[n] for n in names], joints, name="chain")
    scene.frame_start, scene.frame_end = 1, 5
    for frame, delta in ((1, (0.0, 0.0, 0.0)), (5, (-4.0, 5.0, 0.0))):
        scene.frame_set(frame)
        rig.end_control.location = (0.0, 0.0, 0.0)
        move_end(rig, delta)
        rig.end_control.keyframe_insert("location", frame=frame)
    for name in names[:3]:
        ob(name).hide_render = True
    lens = bpy.data.cameras.new("render_check")
    lens.type, lens.ortho_scale = "ORTHO", 24.0
    camera = bpy.data.objects.new("render_check", lens)
    scene.collection.objects.link(camera)
    camera.location = (6.0, 2.0, 30.0)
    render = scene.render
    saved = (scene.camera, render.engine, render.resolution_x, render.resolution_y)
    saved += (render.film_transparent, render.filepath)
    size = 64
    scene.camera = camera
    render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples = "CPU", 1
    render.resolution_x = render.resolution_y = size
    render.film_transparent = True
    render.image_settings.file_format = "PNG"
    render.image_settings.color_mode = "RGBA"
    expected = {}
    for frame in range(1, 6):
        scene.frame_set(frame)
        p = world_to_camera_view(scene, camera, world("P3").translation)
        expected[frame] = (p.x * size, p.y * size)
    scene.frame_set(1)
    folder = store.dir_path()
    render.filepath = os.path.join(folder, "render_")
    try:
        bpy.ops.render.render(animation=True)
    finally:
        scene.camera, render.engine, render.resolution_x = saved[:3]
        render.resolution_y, render.film_transparent, render.filepath = saved[3:]
    gaps = []
    for frame in range(1, 6):
        path = os.path.join(folder, f"render_{frame:04d}.png")
        if not os.path.isfile(path):
            continue
        image = bpy.data.images.load(path)
        alpha = np.array(image.pixels[:]).reshape(size, size, 4)[:, :, 3] > 0.5
        bpy.data.images.remove(image)
        rows, cols = np.nonzero(alpha)
        if not len(rows):
            continue
        cx, cy = cols.mean() + 0.5, rows.mean() + 0.5
        gaps.append(math.dist((cx, cy), expected[frame]))
    travel = math.dist(expected[1], expected[5])
    check(
        "render: an animation render poses the rig with the solve, frame by frame",
        len(gaps) == 5 and max(gaps) < 1.0 and travel > 10.0,
        (gaps, travel),
    )

    # ============================================================= Adjust
    # mayatk's TestAdjust: a handle on every joint and on the end control, the rig
    # rebuilt on them when the adjusting ends.
    def handle_world(handle):
        update()
        return handle.matrix_world.copy()

    def joint_world(rig, joint_id):
        update()
        return rig.group.matrix_world @ rig.joint(joint_id).matrix

    arm, rest = setup()
    rig = build(arm)
    armature = rig.group
    handles = rig.begin_adjust()
    check(
        "adjust: a handle on every joint and on the end control",
        rig.adjusting
        and set(handles) == set(rig.joint_ids()) | {ArticulatedRig.END_KEY},
        sorted(handles),
    )
    gap = max(
        mdiff(handle_world(handles[j]), joint_world(rig, j)) for j in rig.joint_ids()
    )
    end_gap = mdiff(
        handle_world(handles[ArticulatedRig.END_KEY]),
        armature.matrix_world @ rig.end_control.matrix,
    )
    check(
        "adjust: the handles sit on the pivots and change nothing",
        gap < TOL and end_gap < TOL and at_rest(rest) < TOL,
        (gap, end_gap, at_rest(rest)),
    )
    check("adjust: a second call adds nothing", rig.begin_adjust() == handles)
    applied = rig.end_adjust()
    check(
        "adjust: nothing moved, no rebuild, no handle left",
        applied is False
        and rig.group == armature
        and not rig.adjusting
        and not [o for o in bpy.data.objects if ArticulatedRig.PIVOT_ATTR in o.keys()],
        applied,
    )

    arm, rest = setup()
    rig = build(arm, end_control=False)
    handles = rig.begin_adjust()
    before = handle_world(handles["LEG_3"]).translation
    rig.control("LEG_2").rotation_euler[2] = math.radians(30.0)
    gap = (
        handle_world(handles["LEG_3"]).translation
        - joint_world(rig, "LEG_3").translation
    ).length
    check(
        "adjust: a handle rides the part its joint hangs off",
        gap < TOL and (handle_world(handles["LEG_3"]).translation - before).length > 1,
        gap,
    )

    arm, rest = setup()
    rig = build(arm, end_control=False)
    ctrl = rig.control("LEG_2")
    scene = bpy.context.scene
    for frame, value in ((1, 0.0), (10, 35.0)):
        ctrl.rotation_euler[2] = math.radians(value)
        ctrl.keyframe_insert("rotation_euler", index=2, frame=frame)
    scene.frame_set(1)
    handles = rig.begin_adjust()
    handle = handles["LEG_2"]
    handle.location = handle.location + handle.matrix_basis.to_3x3() @ Vector(
        (2.0, 1.0, 0.0)
    )
    pivot = handle_world(handle).translation
    child = joint_world(rig, "LEG_3").translation
    applied = rig.end_adjust()
    check(
        "adjust: a moved pivot rebuilds the rig, moving nothing",
        applied is True and not rig.adjusting and at_rest(rest) < TOL,
        (applied, at_rest(rest)),
    )
    check(
        "adjust: ... the joint turning there, its child's pivot where it was",
        (joint_world(rig, "LEG_2").translation - pivot).length < TOL
        and (joint_world(rig, "LEG_3").translation - child).length < TOL,
        (
            (joint_world(rig, "LEG_2").translation - pivot).length,
            (joint_world(rig, "LEG_3").translation - child).length,
        ),
    )
    on_pivot = world("LEG_2").inverted() @ pivot
    scene.frame_set(10)
    check(
        "adjust: ... its keys kept, now turning the part about the new pivot",
        keys(rig.control("LEG_2"), "rz") == [1.0, 10.0]
        and (world("LEG_2") @ on_pivot - pivot).length < 1e-3
        and mdiff(rest["LEG_2"], world("LEG_2")) > 1e-2,
        (world("LEG_2") @ on_pivot - pivot).length,
    )

    arm, rest = setup()
    rig = build(arm)
    handles = rig.begin_adjust()
    handle = handles[ArticulatedRig.END_KEY]
    handle.rotation_euler = (
        handle.rotation_euler.to_matrix() @ Euler((0.0, 0.0, math.pi / 2)).to_matrix()
    ).to_euler("XYZ")
    wanted = handle_world(handle)
    pivot = wanted.translation.copy()
    applied = rig.end_adjust()
    end = rig.end_control
    to_world = rig.group.matrix_world @ end.matrix
    box = [
        to_world @ Vector(p.co[:3])
        for spline in end.custom_shape.data.splines
        for p in spline.points
    ]
    head_points = [p for s in ArticulatedRig._shells("HEAD") for p in s]
    check(
        "adjust: a turned end handle reorients the end control, refitted, at rest",
        applied is True
        and at_rest(rest) < TOL
        and mdiff(to_world, wanted) < TOL
        and all(
            min(b[i] for b in box) <= min(p[i] for p in head_points) + 1e-4
            and max(b[i] for b in box) >= max(p[i] for p in head_points) - 1e-4
            for i in range(3)
        ),
        (applied, at_rest(rest), mdiff(to_world, wanted)),
    )
    move_end(rig, B((-6.0, 7.0, 5.0)))
    target = end_world(rig)
    check(
        "adjust: ... which still brings the head along",
        (head_point(rest, pivot) - target).length < 0.05,
        (head_point(rest, pivot) - target).length,
    )

    arm, rest = setup()
    rig = build(arm)
    plan = rig.spec
    handles = rig.begin_adjust()
    handle = handles["LEG_3"]
    handle.location = handle.location + handle.matrix_basis.to_3x3() @ Vector(
        (3.0, 0.0, 0.0)
    )
    dropped = rig.end_adjust(apply=False)
    check(
        "adjust: an adjust dropped changes nothing",
        dropped is False and rig.spec == plan and not rig.adjusting,
        dropped,
    )
    handles = rig.begin_adjust()
    handle = handles["LEG_3"]
    handle.location = handle.location + handle.matrix_basis.to_3x3() @ Vector(
        (3.0, 0.0, 0.0)
    )
    applied = rig.end_adjust()
    changed = rig.spec["joints"] != plan["joints"]
    bpy.ops.ed.undo()
    check(
        "adjust: ... and one undo takes an applied one back",
        applied and changed and rig.spec["joints"] == plan["joints"],
        (applied, changed),
    )

    # =============================================================== Grab
    # mayatk's TestGrab: the engine's grab on plain values, then the tool's.
    ring_point = Vector(_w(_add(BALL, _unit(R), 12.15)))

    def ring_now(rest, point):
        """Where *point* on the ring is now, carried by the ring."""
        return world("ring") @ rest["ring"].inverted() @ Vector(point)

    arm, rest = setup()
    rig = build(arm, end_control=False)
    hold = rig.grab_begin("ring", ring_point)
    target = ring_point + Vector(B((-6.0, 8.0, 4.0)))
    state = rig.grab_to(hold, target, key=False)
    check(
        "grab: brings the held point to its target, inside the limits",
        (Vector(rig.held_point(hold)) - target).length < 0.05
        and (ring_now(rest, ring_point) - target).length < 0.05
        and state == hold["model"].clamp(state),
        (Vector(rig.held_point(hold)) - target).length,
    )

    arm, rest = setup()
    rig = build(arm)
    hold = rig.grab_begin("ring", ring_point)
    target = ring_point + Vector(B((-6.0, 8.0, 4.0)))
    rig.grab_to(hold, target, key=False)
    check(
        "grab: a grab on the head drags the end control",
        "end" in hold
        and (Vector(rig.held_point(hold)) - target).length < 0.05
        and (ring_now(rest, ring_point) - target).length < 0.05
        and rig.fk_state() == [0.0] * len(rig.fk_state()),
        (Vector(rig.held_point(hold)) - target).length,
    )

    for end_on in (False, True):
        arm, rest = setup()
        rig = build(arm, end_control=end_on)
        hold = rig.grab_begin("ring", ring_point)
        before = world("HEAD").to_quaternion()
        turn = Quaternion(Vector(B((0.0, 1.0, 0.0))), math.radians(20.0))  # world up
        rig.grab_to(hold, ring_point, turn=(turn.x, turn.y, turn.z, turn.w), key=False)
        gap = turn_gap(world("HEAD").to_quaternion(), turn @ before)
        held = (Vector(rig.held_point(hold)) - ring_point).length
        check(
            "grab: a turned hand turns the ball head by that turn"
            + (" (through the end control)" if end_on else ""),
            gap < 1e-3 and held < 0.05 and ("end" in hold) == end_on,
            (gap, held),
        )

    # The hand's turn is the world's: on a prop turned in the world, the rig space is
    # not, and the turn is carried into it.
    arm, rest = setup()
    ob("ASSET").rotation_euler = (0.4, 0.0, 0.6)
    update()
    rig = build(arm, end_control=False)
    point = Vector(_w(_add(BALL, _unit(R), 12.15)))
    hold = rig.grab_begin("ring", point)
    before = world("HEAD").to_quaternion()
    turn = Quaternion((0.0, 0.0, 1.0), math.radians(20.0))  # world Z
    rig.grab_to(hold, point, turn=(turn.x, turn.y, turn.z, turn.w), key=False)
    gap = turn_gap(world("HEAD").to_quaternion(), turn @ before)
    check(
        "grab: on a prop turned in the world, the hand's turn is still the world's",
        gap < 1e-3 and (Vector(rig.held_point(hold)) - point).length < 0.05,
        gap,
    )

    arm, rest = setup()
    rig = build(arm)
    rig.edit_joint("HEAD", type="hinge")
    hold = rig.grab_begin("ring", ring_point)  # on the ring, off the pivot
    target = ring_point + Vector(B((-6.0, 8.0, 0.0)))
    turn = Quaternion(Vector(B((0.0, 1.0, 0.0))), math.radians(20.0))
    rig.grab_to(hold, target, turn=(turn.x, turn.y, turn.z, turn.w), key=False)
    check(
        "grab: a grab off a hinged end's pivot lands on the cursor, unturned",
        "end" in hold
        and (Vector(rig.held_point(hold)) - target).length < 0.05
        and tuple(rig.end_control.rotation_euler) == (0.0, 0.0, 0.0),
        (Vector(rig.held_point(hold)) - target).length,
    )

    for end_on in (False, True):
        arm, rest = setup()
        rig = build(arm, end_control=end_on)
        target = _w(_add(BALL, (-5, 6, 3)))
        left = rig.pose_to("HEAD", target, point=_w(BALL), key=False)
        check(
            "grab: pose_to lands a part on a target"
            + (" through the end control" if end_on else ""),
            left < 0.05,
            left,
        )

    from blendertk.rig_utils.articulated_rig import ArticulatedRigGrab

    def drag(rig, point):
        """Press on *point* along the fixture's -Z (Maya's camera), drag, let go --
        Auto Key on."""
        origin = Vector(point) + Vector(B((0.0, 0.0, 200.0)))
        direction = B((0.0, 0.0, -1.0))
        tool = ArticulatedRigGrab()
        pressed = tool.press(origin, direction)
        name = tool.rig.name if tool.rig else None
        tool.drag(origin + Vector(B((-5.0, 5.0, 0.0))), direction)
        tool.release()
        return pressed, name

    arm, rest = setup()
    rig = build(arm)
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    try:
        pressed, name = drag(rig, ring_point)
        end_keys = keys(rig.end_control, "tx")
        fk_keys = keys(rig.control("LEG_1"), "rx")
        moved = at_rest(rest)
        bpy.ops.ed.undo()  # the whole drag, keys included
        after = keys(rig.end_control, "tx")
        back = at_rest(rest)
    finally:
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    check(
        "grab tool: drags the end control and keys it on release; one undo",
        pressed
        and name == rig.name
        and end_keys == [1.0]
        and fk_keys == []
        and moved > 1e-2
        and after == []
        and back < TOL,
        (pressed, name, end_keys, fk_keys, moved, after, back),
    )

    arm, rest = setup()
    rig = build(arm)
    set_switch(rig, ArticulatedRig.BLEND_ATTR, 0.0)
    bpy.ops.ed.undo_push(message="IK off")  # the switch, a step of its own
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    try:
        pressed, name = drag(rig, ring_point)
        fk_keys = keys(rig.control("LEG_1"), "rx")
        bpy.ops.ed.undo()
        after = keys(rig.control("LEG_1"), "rx")
    finally:
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    check(
        "grab tool: poses and keys FK with IK off; one undo",
        pressed and fk_keys == [1.0] and after == [],
        (pressed, fk_keys, after),
    )

    arm, rest = setup()
    build(arm)
    plate = Vector(_w((0.0, 0.45, 0.0)))
    tool = ArticulatedRigGrab()
    check(
        "grab tool: ignores the fixed root",
        tool.press(plate + Vector(B((0.0, 0.0, 100.0))), B((0.0, 0.0, -1.0))) is False
        and tool.rig is None,
    )

    # The operator's event routing, on stand-in events (a modal needs a user's mouse):
    # mid-drag a key press is held back -- Ctrl+Z would free the bones the drag
    # holds, X / G would act on the rig under it -- while view navigation still
    # passes, and Esc / RMB still end the tool.
    from types import SimpleNamespace

    from blendertk.rig_utils.articulated_rig.grab_tool import (
        _ArticulatedRigGrabInternal as GrabOp,
    )

    arm, rest = setup()
    build(arm)
    origin = ring_point + Vector(B((0.0, 0.0, 200.0)))
    down = B((0.0, 0.0, -1.0))
    context = SimpleNamespace(window=None, workspace=None, area=None, region=None)

    def route(tool, kind, value="PRESS", **mods):
        op = SimpleNamespace(_tool=tool, report=lambda *_args: None)
        event = SimpleNamespace(
            type=kind,
            value=value,
            alt=mods.get("alt", False),
            ctrl=mods.get("ctrl", False),
            shift=mods.get("shift", False),
            mouse_region_x=0,
            mouse_region_y=0,
        )
        return GrabOp._op_modal(op, context, event)

    tool = ArticulatedRigGrab()
    tool.press(origin, down)
    ended = route(tool, "RIGHTMOUSE")
    check(
        "grab tool: RMB ends the tool, letting go",
        ended == {"FINISHED"} and tool.hold is None,
        ended,
    )
    tool = ArticulatedRigGrab()
    tool.press(origin, down)
    tool.drag(origin + Vector(B((-5.0, 5.0, 0.0))), down)
    posed = {p: world(p) for p in PARTS}
    held = [route(tool, "Z", ctrl=True), route(tool, "X"), route(tool, "G")]
    check(
        "grab tool: mid-drag a key press is held back (Ctrl+Z, X, G), the drag kept",
        held == [{"RUNNING_MODAL"}] * 3
        and tool.hold is not None
        and at_rest(rest) > 1e-2
        and at_rest(posed) < TOL,
        held,
    )
    passed = [
        route(tool, "MIDDLEMOUSE"),
        route(tool, "WHEELUPMOUSE"),
        route(tool, "NUMPAD_5"),
    ]
    check(
        "grab tool: ... while view navigation still passes through",
        passed == [{"PASS_THROUGH"}] * 3 and tool.hold is not None,
        passed,
    )
    ended = route(tool, "ESC")
    between = route(tool, "Z", ctrl=True)
    check(
        "grab tool: Esc ends a drag, keeping it; between drags a key passes through",
        ended == {"FINISHED"}
        and tool.hold is None
        and at_rest(posed) < TOL
        and between == {"PASS_THROUGH"},
        (ended, between),
    )
    try:
        ArticulatedRigGrab.activate()
        refused = False
    except RuntimeError:
        refused = True
    check("grab tool: refuses to start in a background Blender", refused)

    # ============================================================== Panel
    # The panel's scene glue on a real rig (its Qt half is test_articulated_rig_panel,
    # under the .venv): the slots import with no Qt binding; a row's control is a
    # bone, selected the way Blender selects one -- its armature active in Pose Mode,
    # that bone alone selected -- and read back as its joint; a plan row's parts by
    # name, back in Object Mode; the handles Adjust Pivots puts out; Split Off reading
    # the parts, never a control; Selection Order reading the picks in their order.
    import blendertk as btk
    from blendertk.rig_utils.articulated_rig import ArticulatedRigSlots as Slots

    check(
        "panel: the slots import in a background Blender, Qt deferred",
        Slots.__module__.endswith("articulated_rig_slots")
        and "PySide6.QtWidgets" not in sys.modules,
    )
    arm, rest = setup()
    rig = build(arm)
    group = rig.group
    layer = bpy.context.view_layer
    Slots._select([rig.control("LEG_2")])
    picked = Slots._selection()
    bones = [n for n in picked if isinstance(n, bpy.types.PoseBone)]
    check(
        "panel: a row's control is selected as Blender selects a bone",
        group.mode == "POSE"
        and layer.objects.active == group
        and [b.name for b in bones] == [rig.control("LEG_2").name]
        and group in picked,
        (group.mode, [n.name for n in picked]),
    )
    check(
        "panel: ... and the selection reads back as that joint, of that rig",
        bones
        and rig.joint_id_of(bones[0]) == "LEG_2"
        and ArticulatedRig.for_node(bones[0]).name == rig.name,
    )
    ob("LEG_4").select_set(True)  # a part picked alongside the control
    check(
        "panel: Split Off reads the selected parts, never a control or the rig",
        [n.name for n in Slots._parts_of(rig, Slots._selection())] == ["LEG_4"],
        [n.name for n in Slots._selection()],
    )
    Slots._select(["LEG_4", "NO_SUCH_PART"])
    check(
        "panel: a plan row selects its parts by name, back in Object Mode",
        group.mode == "OBJECT"
        and [o.name for o in CoreUtils.selected_objects()] == ["LEG_4"]
        and layer.objects.active == ob("LEG_4"),
        (group.mode, [o.name for o in CoreUtils.selected_objects()]),
    )
    Slots._select(["NO_SUCH_PART"])
    check(
        "panel: a pick of nothing that exists leaves the selection alone",
        [o.name for o in CoreUtils.selected_objects()] == ["LEG_4"],
    )
    handles = rig.begin_adjust()
    Slots._select(list(handles.values()))
    check(
        "panel: Adjust Pivots' handles come out selected",
        set(CoreUtils.selected_objects()) == set(handles.values())
        and len(handles) == len(rig.joint_ids()) + 1,
        len(handles),
    )
    rig.end_adjust(apply=False)
    picks = [ob("LEG_3"), ob("BASE"), ob("LEG_1")]
    Slots._select(picks)
    btk.SelectionOrder.set_order(picks)  # the picks as the tracker records them
    try:
        ordered = [n.name for n in Slots._selection()]
    finally:
        btk.SelectionOrder.disable()
    check(
        "panel: Selection Order reads the picks in the order they were made",
        ordered == ["LEG_3", "BASE", "LEG_1"],
        ordered,
    )

    # ============================================================= Export
    from blendertk.env_utils.scene_exporter._scene_exporter import SceneExporter

    arm, rest = setup()
    rig = build(arm)
    key(rig, [1, 12, 24])
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 24
    # The end control keyed too: the solve moves under the keyed FK every frame.
    for frame, offset in ((1, (0.0, 0.0, 0.0)), (24, B((-5.0, 6.0, 4.0)))):
        scene.frame_set(frame)
        rig.end_control.location = (0.0, 0.0, 0.0)
        move_end(rig, offset)
        rig.end_control.keyframe_insert("location", frame=frame)
    expected, fk_expected = {}, {}
    for frame in range(1, 25):
        scene.frame_set(frame)
        expected[frame] = rig.state()  # the model's: FK plus the solve
        fk_expected[frame] = rig.fk_state()
    store = ptk.TempArtifacts("btk_articulated_rig_test", policy="session")
    out_dir = store.dir_path()
    shipped = [ob("ASSET")] + list(ob("ASSET").children_recursive)
    exported_state = rig.state()  # the pose the file's nodes stand in
    exp = SceneExporter()
    ok = exp.perform_export(
        export_dir=out_dir,
        objects=shipped,
        output_name="articulated",
        export_visible=True,
        tasks={
            "export_data_node": True,
            "drop_rig_apparatus": True,
            "output_format": "fbx_glb",
        },
    )
    glb = os.path.join(out_dir, "articulated.glb")
    check(
        "export: the Scene Exporter writes the FBX and the GLB",
        ok and os.path.isfile(glb),
        (ok, os.listdir(out_dir)),
    )
    names, nodes, manifest = [], [], None
    if os.path.isfile(glb):
        with ptk.MeshConvert.open_glb(glb) as edit:
            nodes = edit.gltf.get("nodes") or []
            names = [n.get("name", "") for n in nodes]
            manifest = (edit.gltf.get("extras") or {}).get(
                ptk.MeshConvert.ARTICULATION_WEB_KEY
            )
    record = rig.record()
    joint_names = [j["name"] for j in record["joints"]]
    grabbed = [g["node"] for g in record["grab"]]
    affix = ptk.NamingConvention.affix("control")
    controls = [n for n in names if n.endswith(affix)]
    leaves = [n for n in names if n.endswith("_end")]
    check(
        "export: the GLB ships the skeleton and the parts, not the controls",
        all(j in names for j in joint_names)
        and all(g in names for g in grabbed)
        and not controls
        and not leaves,
        (
            sum(j in names for j in joint_names),
            sum(g in names for g in grabbed),
            controls,
            leaves,
        ),
    )
    parents = {c: i for i, n in enumerate(nodes) for c in n.get("children") or []}
    under = [
        names[parents.get(names.index(g["node"]), -1)]
        == record["joints"][g["joint"]]["name"]
        if g["node"] in names
        else False
        for g in record["grab"]
    ]
    check("export: every grabbed part hangs under its joint", all(under), under)
    roots = {
        parents.get(names.index(j["name"]))
        for j in record["joints"]
        if j["parent"] is None and j["name"] in names
    }
    check(
        "export: the root joints hang under the armature's node (the rig space)",
        len(roots) == 1 and names[next(iter(roots))] == rig.group.name,
        [names[r] if r is not None else None for r in roots],
    )
    bound = manifest["rigs"][0] if manifest and manifest.get("rigs") else {}
    check(
        "export: extras.articulation_web binds them",
        len(bound.get("joints", [])) == len(joint_names)
        and len(bound.get("grab", [])) == len(grabbed),
        (len(bound.get("joints", [])), len(bound.get("grab", []))),
    )
    # The GLB's joints are the record's: each node's local transform is the model's
    # local of the exported pose (rotations exactly, translations to the file's one
    # unit scale) -- what a runtime's clip reading (ArticulationModel.read) assumes.
    locals_ = []
    for j in record["joints"]:
        n = nodes[names.index(j["name"])] if j["name"] in names else {}
        locals_.append(
            (n.get("translation", [0, 0, 0]), n.get("rotation", [0, 0, 0, 1]))
        )
    model = ptk.ArticulationModel(record)
    want = model.pose(exported_state)
    angles = [
        Quaternion((q[3], *q[:3])).rotation_difference(Quaternion((w[3], *w[:3]))).angle
        for (t, q), (_wt, w) in zip(locals_, want)
    ]
    turn = max(min(a, abs(2 * math.pi - a)) for a in angles)  # q and -q: one turn
    k = model.scale_of(locals_) if locals_ else 0.0
    stretch = max(
        (Vector(t) - Vector(wt) * k).length / max(Vector(wt).length * k, 1e-9)
        for (t, q), (wt, _w) in zip(locals_, want)
    )
    check(
        "export: the GLB's joints are the model's locals of the exported pose",
        turn < 1e-4 and stretch < 1e-4,
        (turn, stretch, k),
    )

    # The FBX bake -- what the GLB and every engine play -- is the model of every
    # frame's pose, the end control's solve included: the exporter steps the frames,
    # and the handler's write lands in the frame's second evaluation pass. Read off
    # the file (Blender's importer would shift it a frame): each joint's ``Lcl
    # Rotation`` keys, XYZ degrees, against the model's local turn.
    fbx_path = os.path.join(out_dir, "articulated.fbx")
    curves = {}
    if os.path.isfile(fbx_path):
        fps = scene.render.fps / scene.render.fps_base
        per_frame = ptk.FbxFile.TICKS_PER_SECOND / fps
        taken = ptk.FbxFile.load(fbx_path, decode_arrays=True).take_keys(
            properties=("Lcl Rotation",)
        )
        for (target, _prop, component), keys_ in next(iter(taken.values()), {}).items():
            curves.setdefault(target, {})["XYZ".index(component[-1])] = sorted(
                (ticks / per_frame, value) for ticks, value, _interp in keys_
            )

    def at(curve, frame):
        """A linear curve's value at *frame* (the exporter drops a key a straight
        run does not need)."""
        if frame <= curve[0][0]:
            return curve[0][1]
        for (f0, v0), (f1, v1) in zip(curve, curve[1:]):
            if f0 <= frame <= f1:
                return v0 + (v1 - v0) * (frame - f0) / max(f1 - f0, 1e-9)
        return curve[-1][1]

    turns, missing = [], 0
    for frame, state in expected.items():
        for (_wt, (x, y, z, w)), joint in zip(model.pose(state), record["joints"]):
            axes = curves.get(joint["name"], {})
            if len(axes) != 3:
                missing += 1
                continue
            turn = xyz_turn([at(axes[i], frame) for i in range(3)])
            turns.append(turn_gap(turn, (w, x, y, z)))
    ik = max(
        abs(a - b)
        for frame, state in expected.items()
        for a, b in zip(state, fk_expected[frame])
    )
    check(
        "export: the FBX bake is the model of every frame, the solve included",
        not missing
        and len(turns) == len(expected) * len(record["joints"])
        and ik > 1.0
        and max(turns) < 5e-4,  # a frame late: ~1e-2
        (len(turns), missing, ik, turns and max(turns)),
    )
    # The FBX write's own repair: a rig ships its armature and no leaf bones,
    # whatever options a bridge passed.
    opts = {"object_types": {"MESH", "EMPTY"}}
    repaired = btk.FbxUtils._force_rig_readability([rig.group], opts)
    check(
        "export: an FBX write admits the rig's armature and drops leaf bones",
        "ARMATURE" in opts["object_types"] and opts.get("add_leaf_bones") is False,
        repaired,
    )
    check(
        "export: the producer is registered",
        ptk.SceneRecords.ARTICULATION in btk.FbxUtils.PRODUCERS
        and ptk.SceneRecords.ARTICULATION in btk.FbxUtils.producers(),
    )
    check(
        "surface: btk.ArticulatedRig is the engine",
        btk.ArticulatedRig is ArticulatedRig,
    )

    # ============================================================== Saved
    # A saved rig plays with Auto Run off: its drivers run no Python.
    arm, rest = setup()
    rig = build(arm, end_control=False)
    saved = os.path.join(out_dir, "articulated_saved.blend")
    bpy.ops.wm.save_as_mainfile(filepath=saved, copy=True)
    bpy.ops.wm.open_mainfile(filepath=saved)
    auto_run = bpy.context.preferences.filepaths.use_scripts_auto_execute
    rig = ArticulatedRig.for_node("HEAD")
    state = random_state(rig.model(), random.Random(21))
    rig.set_state(state, key=False)
    check(
        "saved: reopened with Auto Run off, the rig poses as the model does",
        not auto_run and model_gap(rig, state) < TOL,
        (auto_run, model_gap(rig, state)),
    )
    check(
        "saved: ... and it is still the one rig, not a copy",
        len(ArticulatedRig.scene_rigs()) == 1,
    )

    # A saved rig with an end control holds its solve baked, playing: reopened in a
    # FRESH Blender without blendertk on its path (no handler, Auto Run off) the file
    # plays its keyed IK and its FK still drives the joints; reopened here -- the
    # handlers survive a load -- the end control moves the arm live.
    arm, rest = setup()
    rig = build(arm)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 10
    pivot = end_world(rig)
    targets = {}
    for frame, offset in ((1, (0.0, 0.0, 0.0)), (10, B((-6.0, 8.0, 4.0)))):
        scene.frame_set(frame)
        rig.end_control.location = (0.0, 0.0, 0.0)
        move_end(rig, offset)
        rig.end_control.keyframe_insert("location", frame=frame)
        targets[frame] = end_world(rig)
    model = rig.model()
    spec = rig.spec
    frames = (1, 4, 7, 10)
    states = {}
    for frame in frames:
        scene.frame_set(frame)
        states[frame] = rig.state()
    slot = ptk.ArticulationPlan.slots(spec).index(("LEG_2", "rz"))
    _lo, hi = (
        next(j for j in spec["joints"] if j["id"] == "LEG_2").get("limits") or {}
    ).get("rz") or (None, None)
    nudge = 10.0 if hi is None or hi >= 20.0 else 0.5 * hi
    ik_saved = os.path.join(out_dir, "articulated_ik.blend")
    bpy.ops.wm.save_as_mainfile(filepath=ik_saved, copy=True)
    params = {
        "armature": rig.group.name,
        "joints": [rig.joint(j).name for j in rig.joint_ids()],
        "control": rig.control("LEG_2").name,
        "frames": list(frames),
        "nudge_frame": 7,
        "nudge": nudge,
    }
    child_code = r"""
import importlib.util
import json
import math
import sys

import bpy

params = json.loads(sys.argv[sys.argv.index("--") + 1])
arm = bpy.data.objects[params["armature"]]
scene = bpy.context.scene


def pose():
    bpy.context.view_layer.update()
    return [[list(row) for row in arm.pose.bones[n].matrix] for n in params["joints"]]


def curves(action):
    if getattr(action, "layers", None):
        return [
            fc
            for layer in action.layers
            for strip in layer.strips
            for bag in strip.channelbags
            for fc in bag.fcurves
        ]
    return list(getattr(action, "fcurves", []))


handlers = bpy.app.handlers
out = {
    "blendertk": importlib.util.find_spec("blendertk") is not None,
    "auto_run": bpy.context.preferences.filepaths.use_scripts_auto_execute,
    "handlers": [
        getattr(fn, "__name__", "")
        for fn in list(handlers.frame_change_post) + list(handlers.depsgraph_update_post)
    ],
    "baked": [
        fc.mute
        for fc in curves(arm.animation_data.action)
        if "_IK" in fc.data_path
    ],
    "frames": {},
}
for frame in params["frames"]:
    scene.frame_set(frame)
    out["frames"][str(frame)] = pose()
scene.frame_set(params["nudge_frame"])
arm.pose.bones[params["control"]].rotation_euler[2] += math.radians(params["nudge"])
out["nudged"] = pose()
print("AR_RESULT=" + json.dumps(out))
"""
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    completed = btk.BlenderConnection(bpy.app.binary_path).run_code(
        child_code,
        blend_file=ik_saved,
        script_args=[json.dumps(params)],
        env=env,
        timeout=300,
    )
    found = next(
        (
            line
            for line in (completed.stdout or "").splitlines()
            if line.startswith("AR_RESULT=")
        ),
        None,
    )
    child = json.loads(found[len("AR_RESULT=") :]) if found else {}
    check(
        "saved: a fresh Blender opens it with no blendertk, no handler, Auto Run off",
        bool(child)
        and child["blendertk"] is False
        and child["auto_run"] is False
        and not [n for n in child["handlers"] if n.startswith("_on_")],
        child and (child["blendertk"], child["auto_run"], child["handlers"]),
    )
    check(
        "saved: ... the file holds the solve baked, playing",
        bool(child)
        and len(child["baked"]) == len(states[1])
        and not any(child["baked"]),
        child and child["baked"],
    )

    def child_gap(rows, state):
        return max(
            mdiff(model_matrix(p, q), Matrix(row))
            for (p, q), row in zip(model.world(state), rows)
        )

    gaps = (
        [child_gap(child["frames"][str(f)], states[f]) for f in frames] if child else []
    )
    ik = max(abs(a - b) for a, b in zip(states[10], [0.0] * len(states[10])))
    check(
        "saved: ... and plays its keyed IK, frame by frame, as the model poses it",
        len(gaps) == len(frames) and max(gaps) < TOL and ik > 1.0,
        (gaps, ik),
    )
    nudged = list(states[7])
    nudged[slot] += nudge
    gap = child_gap(child["nudged"], nudged) if child else float("inf")
    check(
        "saved: ... its FK still drives the joints there",
        gap < TOL,
        (gap, completed.returncode, (completed.stderr or "")[-400:]),
    )

    bpy.ops.wm.open_mainfile(filepath=ik_saved)
    rig = ArticulatedRig.for_node("HEAD")
    scene = bpy.context.scene
    check(
        "saved: reopened here, the handlers own its rigs again (the bake muted)",
        SolverHandler.installed()
        and ArticulatedRig._bake_curves(rig.group)
        and all(fc.mute for fc in ArticulatedRig._bake_curves(rig.group)),
    )
    scene.frame_set(10)
    miss = (head_point(rest, pivot) - targets[10]).length
    move_end(rig, B((2.0, 3.0, 1.0)))
    target = end_world(rig)
    moved = (head_point(rest, pivot) - target).length
    check(
        "saved: ... plays its keys and moves on its end control live",
        miss < 0.05 and moved < 0.05,
        (miss, moved),
    )

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
