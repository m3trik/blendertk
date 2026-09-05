"""blendertk.rig_utils.shadow_rig headless test — projected-shadow rig (driver-based).
Run: blender --background --factory-startup --python blendertk/test/test_shadow_rig.py

Verifies the rig BUILDS (source/contact/plane/material/silhouette + keyable props incl. the
``opacity`` fade, the measured constants, the canvas and bearing stamps and the target/source
/contact links), the driver chain is wired on the right channels (plane <- contact <- group,
every expression branchless and under the 255-char cap), and it EVALUATES the shared
projection model: the plane's placement, scale, heading and fade equal
``pythontk.ShadowProjection`` at several source positions, a sun (parallel rays, position-
independent) included; the plane covers exactly the targets' projected bounding box; an
overhead source draws the footprint; the anchor slides and the opacity fades as the target
rises; groundHeight moves the plane; an area light's size draws a penumbra. Then the
re-attach paths (from_plane / for_node / planes_for_nodes), Recalculate (live refit vs a baked
plane's kept canvas), set_source, unbake, rebuild, BAKE (keys on the transform AND the fade,
the chain stripped, the Z rotation unrolled, the material following the keyed fade), delete,
export metadata (the v2 record's keys, rounding and unit_scale — mayatk's schema), multi-source,
the lifecycle guards (retired mode alias, create rollback, uniquified re-create, evaluated
footprint, linked-library skip). Then the HORIZON rig (the map is baked and recorded — bins,
tile, layout, mapping, range, the Blender frame ``b = -Z``, the PNG's size and its r_min-on-top
orientation; Recalculate re-bakes only on a geometry change; rebuild keeps the type) and PER
OBJECT + ATLAS (one rig per target; packing remaps the quad's UVs into its inset rect, rebinds
the material to the atlas, keeps each plane's own PNG, rewrites one tile in place without
touching the other's bytes; horizon maps pack into their own atlas; unpack restores unit UVs).

Reference geometry: a 2x2x2 cube at the origin -> contact (0, 0, -1), objectHeight 2,
footprintRadius = hypot(2, 2) / 2 = 1.4142, ground 0. Default source (5, 5, 10).
"""

import sys
import os
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


def approx(a, b, tol=2e-2):
    return abs(a - b) <= tol


try:
    import math
    import numpy as np
    import bpy
    from blendertk.rig_utils.shadow_rig import ShadowRig, ShadowRigSlots
    from pythontk import ShadowAtlas, ShadowHorizon, ShadowProjection

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for blk in (
            bpy.data.materials,
            bpy.data.images,
            bpy.data.lights,
            bpy.data.actions,
        ):
            for d in list(blk):
                blk.remove(d)

    def cube(name="Cube", loc=(0, 0, 0)):
        # 2x2x2 cube (verts -1..1) via a mesh primitive (no bpy.ops to stay context-clean).
        import bmesh

        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        o.location = loc
        bpy.context.collection.objects.link(o)
        return o

    def sun(name="Sun", rot=(math.radians(45), 0.0, 0.0)):
        """A SUN light; rotation X = 45 deg shines toward +Y and down at 45 deg."""
        data = bpy.data.lights.new(name, "SUN")
        o = bpy.data.objects.new(name, data)
        o.rotation_euler = rot
        bpy.context.collection.objects.link(o)
        return o

    def drv(obj, data_path, index):
        ad = getattr(obj, "animation_data", None)
        if not ad:
            return None
        return next(
            (
                d
                for d in ad.drivers
                if d.data_path == data_path
                and (index is None or d.array_index == index)
            ),
            None,
        )

    def all_drivers(rig):
        out = []
        for idb in (rig.shadow_plane, rig.group, rig.contact, rig.material.node_tree):
            ad = getattr(idb, "animation_data", None)
            if ad:
                out.extend(ad.drivers)
        return out

    def action_fcurves(action):
        try:
            fcs = list(action.fcurves)
            if fcs:
                return fcs
        except AttributeError:
            pass
        out = []
        for layer in getattr(action, "layers", []):
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", []):
                    out.extend(bag.fcurves)
        return out

    def ev(o):
        return o.evaluated_get(bpy.context.evaluated_depsgraph_get())

    def post(parent, name="Post"):
        """A tall thin box off to one side of *parent* (a cube reads the same from every
        bearing; this makes the opposite bearing a different silhouette)."""
        import bmesh

        me = bpy.data.meshes.new(f"{name}_mesh")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.6, 0.6, 4.0), verts=bm.verts)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        o.location = (0.7, -0.7, 1.0)
        bpy.context.collection.objects.link(o)
        o.parent = parent
        return o

    def opacity_value(rig):
        return rig.material.node_tree.nodes["opacity"].outputs[0].default_value

    def angle_close(a, b, tol=1e-3):
        d = (a - b + math.pi) % (2 * math.pi) - math.pi
        return abs(d) <= tol

    def plane_matches_model(plane, label):
        """The drivers' placement equals the pythontk model's for the rig re-attached from
        the plane (the driver chain transcribes the Python)."""
        bpy.context.view_layer.update()
        rig = ShadowRig.from_plane(plane)
        m = rig.current_model()
        (cx, cy), du, dw = m.placement(rig.canvas)
        e = ev(plane)
        loc = e.matrix_world.translation
        ux, uy = m.bearing
        ok_loc = (
            approx(loc.x, cx, 2e-3)
            and approx(loc.y, cy, 2e-3)
            and approx(loc.z, rig.ground_height + ShadowRig.GROUND_OFFSET, 2e-3)
        )
        ok_scl = approx(e.scale[1], du, 2e-3) and approx(e.scale[0], dw, 2e-3)
        ok_rot = angle_close(e.rotation_euler[2], math.atan2(-ux, uy))
        # The fade: elongation falloff x source-height fade x rise fade.
        contact = rig._contact_point()
        light_z = (
            contact[2] + 1.0
            if ShadowRig.source_is_directional(rig.light)
            else rig.light.matrix_world.translation.z
        )
        p = plane
        stretch = max(1.0, m.length / max(1e-4, 2.0 * rig.footprint_radius))
        expected = float(p["shadowIntensity"]) / max(
            0.001, stretch ** float(p["falloffPower"])
        )
        expected *= min(max(light_z - contact[2], 0.0), 1.0)
        expected *= min(
            max(
                1.0
                - max(0.0, contact[2] - rig.ground_height)
                / max(0.001, float(p["fadeHeight"])),
                0.0,
            ),
            1.0,
        )
        expected = min(max(expected, 0.0), 1.0)
        op = float(rig.material.node_tree.nodes["opacity"].outputs[0].default_value)
        ok_op = approx(op, expected, 3e-3)
        check(
            f"{label}: plane placement matches the projection model",
            ok_loc and ok_scl and ok_rot,
            f"loc ({loc.x:.3f},{loc.y:.3f},{loc.z:.3f}) vs ({cx:.3f},{cy:.3f}); "
            f"scale ({e.scale[0]:.3f},{e.scale[1]:.3f}) vs ({dw:.3f},{du:.3f}); "
            f"rz {e.rotation_euler[2]:.3f} vs {math.atan2(-ux, uy):.3f}",
        )
        check(f"{label}: fade matches the model", ok_op, f"{op:.4f} vs {expected:.4f}")
        return m

    def alpha_of(image):
        px = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(px)
        return px.reshape(image.size[1], image.size[0], 4)[:, :, 3]

    # ============================ BUILD ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    check("default mode is orbit", rig.mode == "orbit", rig.mode)
    check(
        "source empty created",
        rig.light is not None and rig.light.name == "shadow_source",
    )
    check(
        "contact empty parented to target",
        rig.contact is not None and rig.contact.parent is c,
    )
    check(
        "contact sits at footprint min-Z",
        approx(rig.contact.matrix_world.translation[2], -1.0),
        f"z={rig.contact.matrix_world.translation[2]:.3f}",
    )
    check("shadow plane created", rig.shadow_plane is not None)
    check(
        "plane grouped under *_shadow_grp",
        rig.shadow_plane.parent is not None
        and rig.shadow_plane.parent.name.endswith("_shadow_grp"),
    )

    p = rig.shadow_plane
    for prop, val in (
        ("shadowIntensity", 1.0),
        ("falloffPower", 1.2),
        ("maxStretch", ShadowProjection.DEFAULT_MAX_STRETCH),
        ("groundHeight", 0.0),
        ("basePlaneSize", 1.0),
        ("objectHeight", 2.0),
        ("footprintRadius", math.hypot(2, 2) / 2),
        ("fadeHeight", 4.0),
    ):
        check(
            f"plane has {prop}={val:.4g}",
            approx(float(p.get(prop, -99)), val, 1e-3),
            f"{p.get(prop)}",
        )
    check(
        "scaleInfluence is gone (perspective growth is real now)",
        p.get("scaleInfluence") is None,
    )
    check(
        "plane has the opacity fade prop",
        p.get("opacity") is not None,
        f"{p.get('opacity')}",
    )
    expected_bearing = np.array([0, 0, -1]) - np.array([5, 5, 10])
    expected_bearing = expected_bearing / np.linalg.norm(expected_bearing)
    check(
        "silhouette bearing stamped (source -> contact, unit 3D)",
        all(
            approx(float(p.get(k, 9)), v, 1e-3)
            for k, v in zip(ShadowRig._BEARING_PROPS, expected_bearing)
        ),
        f"{[round(float(p.get(k, 9)), 3) for k in ShadowRig._BEARING_PROPS]}",
    )
    canvas = tuple(float(p.get(k, 9)) for k in ShadowRig._CANVAS_PROPS)
    check(
        "canvas fractions stamped",
        canvas[0] < canvas[1] and canvas[2] < canvas[3] and rig.canvas == canvas,
        f"{[round(v, 3) for v in canvas]}",
    )
    targets, source = ShadowRig._rig_links(p)
    check(
        "plane links its targets + source",
        targets == [c] and source is rig.light,
        f"{[t.name for t in targets]} / {getattr(source, 'name', None)}",
    )
    check("plane links its contact", ShadowRig._plane_contact(p) is rig.contact)

    check(
        "silhouette PNG written",
        bool(rig.texture_path) and os.path.exists(rig.texture_path),
        f"{rig.texture_path}",
    )
    check(
        "image datablock loaded",
        rig.image is not None and tuple(rig.image.size) == (64, 64),
    )
    nt = rig.material.node_tree
    nodes = {n.bl_idname for n in nt.nodes}
    check(
        "material is unlit emission + transparent + mix",
        {
            "ShaderNodeEmission",
            "ShaderNodeBsdfTransparent",
            "ShaderNodeMixShader",
            "ShaderNodeTexImage",
        }
        <= nodes,
        f"{sorted(nodes)}",
    )
    check(
        "plane uses the shadow material",
        len(p.data.materials) == 1 and p.data.materials[0] is rig.material,
    )

    # ---- the driver chain ----
    check(
        "plane location[0]/[1]/[2] driven",
        all(drv(p, "location", i) is not None for i in range(3)),
    )
    check("plane rotation_euler[2] driven", drv(p, "rotation_euler", 2) is not None)
    check(
        "plane scale[0] and scale[1] driven; scale[2] static",
        drv(p, "scale", 0) is not None
        and drv(p, "scale", 1) is not None
        and drv(p, "scale", 2) is None,
    )
    check(
        "group carries the level-1 intermediates",
        all(
            drv(rig.group, f'["{n}"]', None) is not None for n in ShadowRig._GROUP_PROPS
        ),
    )
    check(
        "contact carries the level-2 intermediates",
        all(
            drv(rig.contact, f'["{n}"]', None) is not None
            for n in ShadowRig._CONTACT_PROPS
        ),
    )
    check(
        "opacity math driver on the material node tree",
        drv(nt, ShadowRig._MATERIAL_OPACITY_PATH, None) is not None,
    )
    check(
        "plane opacity prop mirrors the material (driver)",
        drv(p, '["opacity"]', None) is not None,
    )
    exprs = [d.driver.expression for d in all_drivers(rig)]
    check(
        "driver expressions are branchless (no ' if '/comparison)",
        all(
            (" if " not in e and "<" not in e and ">" not in e and "==" not in e)
            for e in exprs
        ),
    )
    check(
        "driver expressions fit the 255-char cap",
        all(len(e) <= 255 for e in exprs),
        f"max={max(len(e) for e in exprs)}",
    )
    check(
        "every driver is valid",
        all(d.driver.is_valid for d in all_drivers(rig)),
        f"{[d.data_path for d in all_drivers(rig) if not d.driver.is_valid]}",
    )

    # ============================ EVALUATE vs THE MODEL ============================
    m0 = plane_matches_model(p, "build")
    check(
        "orbit heads away from the light (rz ≈ 2.356, not the mirrored -0.785)",
        angle_close(ev(p).rotation_euler[2], 3 * math.pi / 4),
        f"{ev(p).rotation_euler[2]:.3f}",
    )
    for pos in ((-4, 2, 6), (3, -6, 3), (0.5, 0, 12), (6, 6, 2.5)):
        rig.light.location = pos
        plane_matches_model(p, f"source {pos}")
    rig.light.location = (5, 5, 3)
    low = plane_matches_model(p, "low source")
    check(
        "lowering the source grows the reach",
        low.reach > m0.reach + 1e-3,
        f"{low.reach:.3f} > {m0.reach:.3f}",
    )
    rig.light.location = (5, 5, 10)

    # ---- the plane covers exactly the projected bounding box (at the raster position) ----
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(-6, 2, 5), texture_res=64)
    p = rig.shadow_plane
    bpy.context.view_layer.update()
    corners = np.array(
        [[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=float
    )
    ground, _ = ShadowProjection.project(corners, light=(-6, 2, 5), ground=0.0, up=2)
    model = ShadowRig.from_plane(p).current_model()
    uw = ShadowProjection.to_frame(ground, model)
    mw = ev(p).matrix_world
    world = [
        mw @ __import__("mathutils").Vector((x, y, 0))
        for x in (-0.5, 0.5)
        for y in (-0.5, 0.5)
    ]
    plane_uw = ShadowProjection.to_frame([(v.x, v.y) for v in world], model)
    covers = True
    for axis in (0, 1):
        lo, hi = plane_uw[:, axis].min(), plane_uw[:, axis].max()
        extent = uw[:, axis].max() - uw[:, axis].min()
        covers &= lo <= uw[:, axis].min() + 1e-3 and hi >= uw[:, axis].max() - 1e-3
        covers &= (hi - lo) - extent < 0.12 * max(extent, 1.0) + 1e-3
    check(
        "plane covers the projected bounding box (no wider, no shorter)",
        covers,
        f"plane u {plane_uw[:, 0].min():.3f}..{plane_uw[:, 0].max():.3f} w {plane_uw[:, 1].min():.3f}.."
        f"{plane_uw[:, 1].max():.3f}; shadow u {uw[:, 0].min():.3f}..{uw[:, 0].max():.3f} "
        f"w {uw[:, 1].min():.3f}..{uw[:, 1].max():.3f}",
    )

    # ---- rise: the anchor slides away from the light and the fade drops ----
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    p = rig.shadow_plane
    bpy.context.view_layer.update()
    op_grounded = float(opacity_value(rig))
    c.location = (0, 0, 3)  # contact z = 2
    m = plane_matches_model(p, "risen target")
    check(
        "anchor slides away from the light (k_base = 10/8)",
        approx(m.k_base, 1.25, 1e-3) and approx(m.anchor[0], 5 - 5 * 1.25, 1e-3),
        f"k={m.k_base:.3f} anchor={m.anchor}",
    )
    check(
        "opacity drops with the rise",
        float(opacity_value(rig)) < op_grounded * 0.55,
        f"{float(opacity_value(rig)):.3f} < {op_grounded:.3f} x 0.55",
    )
    c.location = (0, 0, 0)

    # ---- groundHeight is a live prop ----
    p["groundHeight"] = 2.0
    p.update_tag()
    m = plane_matches_model(p, "raised ground")
    check(
        "raised ground: plane sits on it (z = 2.01)",
        approx(ev(p).matrix_world.translation.z, 2.01, 1e-3),
    )
    check(
        "raised ground: k_base = 8/11",
        approx(m.k_base, 8 / 11, 1e-3),
        f"{m.k_base:.4f}",
    )
    p["groundHeight"] = 0.0
    p.update_tag()

    # ---- the drivers read the source's WORLD position ----
    holder = bpy.data.objects.new("light_grp", None)
    bpy.context.collection.objects.link(holder)
    rig.light.parent = holder
    holder.location = (5, 0, 0)  # source world x: 5 -> 10
    bpy.context.view_layer.update()
    before_rz = ev(p).rotation_euler[2]
    plane_matches_model(p, "parented source")
    check(
        "moving the source's parent re-heads the shadow",
        not angle_close(ev(p).rotation_euler[2], 3 * math.pi / 4, 0.05),
    )

    # ---- the shadow stays attached to the feet as the source lowers (the reported gap) ----
    reset()
    c = cube("Box", loc=(0, 0, 1))  # resting on the ground
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(6, 0, 20), texture_res=64)
    p = rig.shadow_plane

    def edge_x(local_y):
        # Local -Y is the light-side (near) edge: V = 0 there.
        bpy.context.view_layer.update()
        return (ev(p).matrix_world @ __import__("mathutils").Vector((0, local_y, 0))).x

    near_high = edge_x(-0.5)
    rig.light.location = (6, 0, 4)
    near_low = edge_x(-0.5)
    check(
        "near edge stays at the feet as the source lowers (stamped in footprint radii)",
        approx(near_low, near_high, 1e-3) and abs(near_high - 1.0) < 0.2,
        f"high {near_high:.3f} low {near_low:.3f} (the near face is x = 1)",
    )
    check(
        "far edge lands where the top's far corner projects (x = -8)",
        abs(edge_x(0.5) + 8.0) < 0.3,
        f"{edge_x(0.5):.3f}",
    )
    plane_matches_model(p, "lowered source")

    # ============================ OVERHEAD ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(0, 0, 10), texture_res=64)
    p = rig.shadow_plane
    m = plane_matches_model(p, "overhead")
    e = ev(p)
    check(
        "overhead source: the footprint, centred, square, heading +Y",
        m.overhead
        and approx(e.matrix_world.translation.x, 0, 1e-3)
        and approx(e.matrix_world.translation.y, 0, 1e-3)
        and approx(e.scale[0], e.scale[1], 1e-3)
        and angle_close(e.rotation_euler[2], 0.0),
        f"loc {tuple(round(v, 3) for v in e.matrix_world.translation)} scale {tuple(round(v, 3) for v in e.scale)}",
    )
    check(
        "overhead canvas is the top face's projection (k = 10/9) plus padding",
        2.3 < e.scale[0] < 2.6,
        f"{e.scale[0]:.3f}",
    )
    check(
        "overhead stamp points straight down",
        all(
            approx(float(p[k]), v, 1e-3)
            for k, v in zip(ShadowRig._BEARING_PROPS, (0, 0, -1))
        ),
    )
    check("overhead silhouette is not stale", not ShadowRig.silhouette_is_stale(p))

    # ============================ SUN ============================
    reset()
    c = cube("Box")
    s = sun()
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], source_name=s.name, texture_res=64)
    p = rig.shadow_plane
    check("a SUN light is a directional source", ShadowRig.source_is_directional(s))
    lz = drv(rig.group, '["sr_Lz"]', None)
    check(
        "sun drivers read the light's matrix_world column",
        lz is not None
        and any(
            v.targets[0].data_path.startswith("matrix_world[")
            for v in lz.driver.variables
        ),
        f"{[v.targets[0].data_path for v in lz.driver.variables] if lz else None}",
    )
    m = plane_matches_model(p, "sun")
    check(
        "sun reach = height x cot(45) = 2", approx(m.reach, 2.0, 1e-2), f"{m.reach:.4f}"
    )
    check(
        "sun bearing = its horizontal direction (+Y)",
        approx(m.bearing[1], 1.0, 1e-3) and approx(m.bearing[0], 0.0, 1e-3),
    )
    before = (
        tuple(ev(p).matrix_world.translation),
        tuple(ev(p).scale),
        ev(p).rotation_euler[2],
    )
    s.location = (8, 3, -2)
    bpy.context.view_layer.update()
    after = (
        tuple(ev(p).matrix_world.translation),
        tuple(ev(p).scale),
        ev(p).rotation_euler[2],
    )
    check(
        "moving the sun changes nothing (parallel rays)",
        all(approx(a, b, 1e-4) for a, b in zip(before[0], after[0]))
        and all(approx(a, b, 1e-4) for a, b in zip(before[1], after[1]))
        and angle_close(before[2], after[2], 1e-4),
    )
    s.rotation_euler = (math.radians(60), 0.0, 0.0)  # 30 deg elevation
    m2 = plane_matches_model(p, "sun at 30 deg elevation")
    check(
        "rotating the sun lower grows the reach (2 x cot 30 = 3.46)",
        approx(m2.reach, 2 / math.tan(math.radians(30)), 1e-2),
        f"{m2.reach:.3f}",
    )

    # ============================ AREA LIGHT PENUMBRA ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    sharp = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    sharp_alpha = alpha_of(sharp.image)
    area_data = bpy.data.lights.new("Area", "AREA")
    area_data.size = 2.0
    area = bpy.data.objects.new("Area", area_data)
    area.location = (5, 5, 10)
    area.scale = (2, 2, 2)
    bpy.context.collection.objects.link(area)
    bpy.context.view_layer.update()
    soft = ShadowRig.create([c], source_name="Area", texture_res=64)
    check(
        "area light size stamped (2 x world scale 2 = 4)",
        approx(float(soft.shadow_plane["sourceSize"]), 4.0, 1e-3),
        f"{soft.shadow_plane.get('sourceSize')}",
    )

    def partial(a):
        return int(((a > 0.05) & (a < 0.95)).sum())

    check(
        "a sized source draws a penumbra (more partial alpha)",
        partial(alpha_of(soft.image)) > partial(sharp_alpha) * 1.3,
        f"{partial(alpha_of(soft.image))} vs {partial(sharp_alpha)}",
    )
    for r in (sharp, soft):
        if r.texture_path and os.path.exists(r.texture_path):
            os.remove(r.texture_path)

    # ============================ RE-ATTACH ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    p = rig.shadow_plane
    again = ShadowRig.from_plane(p)
    check(
        "from_plane resolves targets/source/contact/group/material/image/canvas",
        again is not None
        and again.targets == [c]
        and again.light is rig.light
        and again.contact is rig.contact
        and again.group is rig.group
        and again.material is rig.material
        and again.image is rig.image
        and again.canvas == rig.canvas
        and approx(again.footprint_radius, rig.footprint_radius, 1e-6)
        and again._base == "Box",
    )
    check(
        "_from_plane is kept as an alias",
        ShadowRig._from_plane.__func__ is ShadowRig.from_plane.__func__,
    )
    for label, node in (
        ("plane", p),
        ("group", rig.group),
        ("target", c),
        ("source", rig.light),
        ("contact", rig.contact),
    ):
        found = ShadowRig.for_node(node)
        check(
            f"for_node resolves the rig from its {label}",
            found is not None and found.shadow_plane is p,
        )
    other = cube("Other", loc=(9, 9, 0))
    check("for_node ignores an unrelated object", ShadowRig.for_node(other) is None)
    check(
        "planes_for_nodes dedups target + source",
        len(ShadowRig.planes_for_nodes([c, rig.light])) == 1,
    )
    check("for_nodes lists distinct rigs", len(ShadowRig.for_nodes([c, other])) == 1)
    check(
        "find_shadow_planes on a target alone finds nothing (planes_for_nodes follows the links)",
        ShadowRig.find_shadow_planes([c]) == []
        and ShadowRig.planes_for_nodes([c]) == [p],
    )

    # ============================ RECALCULATE ============================
    reset()
    c = cube("Box")
    post(c)
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    p = rig.shadow_plane
    path = rig.texture_path
    canvas0 = rig.canvas
    before_png = open(path, "rb").read()
    check("fresh silhouette is not stale", not ShadowRig.silhouette_is_stale(p))
    rig.light.location = (-5, -5, 10)
    bpy.context.view_layer.update()
    check("moved source marks the silhouette stale", ShadowRig.silhouette_is_stale(p))
    check(
        "refresh re-rasterizes the live rig", ShadowRig.refresh_silhouette([p]) == [p]
    )
    check("refresh clears the stale flag", not ShadowRig.silhouette_is_stale(p))
    refit = tuple(float(p[k]) for k in ShadowRig._CANVAS_PROPS)
    check(
        "live refresh refits the canvas",
        refit != canvas0,
        f"{[round(v, 3) for v in refit]}",
    )
    plane_matches_model(p, "after refit")
    check(
        "refresh rewrites the PNG in place",
        open(path, "rb").read() != before_png
        and ShadowRig._plane_texture_path(p) == path,
    )
    rig.light.location = (5, 5, 10)
    bpy.context.view_layer.update()
    rig.bake(1, 2)
    check(
        "baked rig reports the moved-back source stale",
        ShadowRig.silhouette_is_stale(p),
    )
    check("refresh works on a baked rig", ShadowRig.refresh_silhouette([p]) == [p])
    check(
        "baked refresh keeps the canvas stamps (the keys own the placement)",
        tuple(float(p[k]) for k in ShadowRig._CANVAS_PROPS) == refit,
    )
    check(
        "baked refresh restamps the bearing",
        approx(float(p["silhouetteBearingX"]), -5 / math.sqrt(25 + 25 + 121), 1e-3)
        and not ShadowRig.silhouette_is_stale(p),
    )
    if os.path.exists(path):
        os.remove(path)

    # ---- unstamped rig ----
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    del rig.shadow_plane[ShadowRig._TARGETS_PROP]
    check(
        "refresh skips an unstamped rig",
        ShadowRig.refresh_silhouette([rig.shadow_plane]) == [],
    )
    check(
        "unstamped rig is never stale",
        not ShadowRig.silhouette_is_stale(rig.shadow_plane),
    )
    check(
        "for_node finds nothing on an unstamped plane",
        ShadowRig.for_node(rig.shadow_plane) is None,
    )

    # ============================ SET SOURCE / UNBAKE / REBUILD ============================
    reset()
    c = cube("Box")
    s = sun(rot=(math.radians(60), 0.0, math.radians(20)))
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    p = rig.shadow_plane
    rig.set_source(s.name, size=64)
    check("set_source relinks the source", ShadowRig._rig_links(p)[1] is s)
    lz = drv(rig.group, '["sr_Lz"]', None)
    check(
        "set_source rebuilt the chain for a sun",
        lz is not None
        and any(
            v.targets[0].data_path.startswith("matrix_world[")
            for v in lz.driver.variables
        ),
    )
    plane_matches_model(p, "after set_source")

    rig.bake(1, 3)
    check(
        "baked plane is baked, not live",
        ShadowRig.plane_is_baked(p) and not ShadowRig.plane_is_live(p),
    )
    check(
        "bake strips the chain on group + contact + material",
        drv(rig.group, '["sr_kb"]', None) is None
        and drv(rig.contact, '["sr_len"]', None) is None
        and drv(rig.material.node_tree, ShadowRig._MATERIAL_OPACITY_PATH, None)
        is not None
        and drv(
            rig.material.node_tree, ShadowRig._MATERIAL_OPACITY_PATH, None
        ).driver.expression
        == "op",
    )
    check("unbake restores the drivers", ShadowRig.unbake_planes([p]) == [p])
    check(
        "unbaked plane is live, not baked",
        ShadowRig.plane_is_live(p) and not ShadowRig.plane_is_baked(p),
    )
    bpy.context.view_layer.update()
    rz0 = ev(p).rotation_euler[2]
    s.rotation_euler = (math.radians(60), 0.0, math.radians(120))
    plane_matches_model(p, "after unbake")
    check(
        "unbaked plane follows the source again",
        not angle_close(ev(p).rotation_euler[2], rz0, 0.05),
    )
    check("a second unbake finds nothing baked", ShadowRig.unbake_planes([p]) == [])
    rig.bake(1, 3)
    rig.set_source("shadow_source", size=64)
    check(
        "set_source on a baked plane restores the drivers", ShadowRig.plane_is_live(p)
    )
    plane_matches_model(p, "re-sourced after bake")

    c.scale = (1, 1, 2)  # 4 tall
    bpy.context.view_layer.update()
    tex_before = rig.texture_path
    new = ShadowRig.rebuild(p, texture_res=32)
    check(
        "rebuild keeps the plane name and reads the new geometry",
        new is not None
        and new.shadow_plane.name == "Box_shadow"
        and bpy.data.objects.get("Box1_shadow") is None
        and approx(float(new.shadow_plane["objectHeight"]), 4.0, 1e-3)
        and tuple(new.image.size) == (32, 32)
        and ShadowRig._rig_links(new.shadow_plane)[1]
        is bpy.data.objects.get("shadow_source")
        and len(ShadowRig.find_shadow_planes()) == 1,
        f"{getattr(getattr(new, 'shadow_plane', None), 'name', None)} h={new.shadow_plane.get('objectHeight') if new else None}",
    )
    for t in (tex_before, new.texture_path if new else None):
        if t and os.path.exists(t):
            os.remove(t)

    # ============================ BAKE ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    p = rig.shadow_plane
    bpy.context.view_layer.update()
    driven = (tuple(ev(p).matrix_world.translation), float(opacity_value(rig)))
    baked = rig.bake(1, 3)
    check("bake returns the plane", baked == [p])
    check(
        "bake strips the plane's drivers",
        not (p.animation_data and p.animation_data.drivers),
    )
    fcs = action_fcurves(p.animation_data.action)
    paths = {(f.data_path, f.array_index) for f in fcs}
    check(
        "bake keys location/rotation/scale + the fade",
        {("location", 0), ("rotation_euler", 2), ("scale", 1), ('["opacity"]', 0)}
        <= paths,
        f"{sorted(paths)}",
    )
    loc_fc = next(f for f in fcs if f.data_path == "location" and f.array_index == 0)
    check("baked keys span the range (3 keys)", len(loc_fc.keyframe_points) == 3)
    bpy.context.scene.frame_set(2)
    bpy.context.view_layer.update()
    check(
        "baked values match the driven ones",
        approx(ev(p).matrix_world.translation.x, driven[0][0], 1e-3)
        and approx(float(p["opacity"]), driven[1], 1e-3),
        f"{ev(p).matrix_world.translation.x:.4f} vs {driven[0][0]:.4f}; {float(p['opacity']):.4f} vs {driven[1]:.4f}",
    )
    check(
        "material follows the keyed fade after the bake",
        (
            drv(rig.material.node_tree, ShadowRig._MATERIAL_OPACITY_PATH, None)
            or drv(p, "x", 0)
        )
        is not None
        and drv(
            rig.material.node_tree, ShadowRig._MATERIAL_OPACITY_PATH, None
        ).driver.expression
        == "op",
    )
    check("second bake is a no-op", ShadowRig.bake_planes([p]) == [])
    bpy.context.scene.frame_set(1)

    # ---- rotation unroll: source crossing behind the target ----
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(2, 5, 10), texture_res=32)
    L = rig.light
    L.location = (2, 5, 10)
    L.keyframe_insert("location", frame=1)
    L.location = (-2, 5, 10)
    L.keyframe_insert("location", frame=3)
    rig.bake(1, 3)
    fcs = action_fcurves(rig.shadow_plane.animation_data.action)
    rot_fc = next(
        (f for f in fcs if f.data_path == "rotation_euler" and f.array_index == 2), None
    )
    vals = [kp.co[1] for kp in rot_fc.keyframe_points] if rot_fc else []
    jumps = [abs(b - a) for a, b in zip(vals, vals[1:])]
    check(
        "baked Z rotation is unrolled (no ±pi jump between frames)",
        bool(jumps) and max(jumps) < math.pi / 2,
        f"{[round(v, 3) for v in vals]}",
    )

    # ---- batch bake ----
    reset()
    c = cube("Box")
    c2 = cube("Box2", loc=(6, 0, 0))
    bpy.context.view_layer.update()
    r1 = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    r2 = ShadowRig.create([c2], light_pos=(5, 5, 10), texture_res=32)
    baked = ShadowRig.bake_planes()
    check(
        "bake_planes(None) bakes every live rig",
        sorted(o.name for o in baked)
        == sorted([r1.shadow_plane.name, r2.shadow_plane.name]),
    )

    # ============================ EXPORT METADATA ============================
    import json as _json
    from blendertk.node_utils.data_nodes import DataNodes

    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    p = rig.shadow_plane
    raw = DataNodes.get_export_string(ShadowRig.SHADOW_METADATA)
    payload = _json.loads(raw) if raw else {}
    recs = {r["name"]: r for r in payload.get("planes", [])}
    check(
        "shadow_metadata published on the data_export carrier",
        payload.get("version") == 2 and p.name in recs,
        f"{raw}",
    )
    check(
        "record carries the silhouette filename",
        recs.get(p.name, {}).get("texture") == "Box_shadow.png",
        f"{recs.get(p.name)}",
    )
    check(
        "record carries the authored intensity",
        approx(recs.get(p.name, {}).get("intensity", -1), 1.0),
    )
    # v2: the engine's runtime inputs ride the record (the contract in
    # mayatk/docs/shadow_rig_morphing.md) — the same keys and rounding mayatk writes.
    check(
        "payload carries unit_scale (metres per Blender unit)",
        approx(payload.get("unit_scale", -1), 1.0, 1e-9),
        f"{payload.get('unit_scale')}",
    )
    rec = recs.get(p.name, {})
    check(
        "record: type is projected",
        rec.get("type") == "projected",
        f"{rec.get('type')}",
    )
    check(
        "record: source + source_type",
        rec.get("source") == ShadowRig.DEFAULT_SOURCE_NAME
        and rec.get("source_type") == "point",
        f"{rec.get('source')} / {rec.get('source_type')}",
    )
    check(
        "record: follow_source on",
        rec.get("follow_source") is True,
        f"{rec.get('follow_source')}",
    )
    check(
        "record: contact leaf name",
        rec.get("contact") == "Box_contact",
        f"{rec.get('contact')}",
    )
    check(
        "record: model inputs",
        approx(rec.get("radius", -1), math.hypot(2, 2) / 2, 1e-3)
        and approx(rec.get("height", -1), 2.0, 1e-3)
        and approx(rec.get("max_stretch", -1), 6.0, 1e-3)
        and approx(rec.get("ground", -1), 0.0, 1e-9)
        and len(rec.get("canvas", [])) == 4,
        f"{ {k: rec.get(k) for k in ('radius', 'height', 'max_stretch', 'ground')} }",
    )
    check(
        "record: no atlas / horizon block on a plain projected rig",
        "atlas" not in rec and "horizon" not in rec,
        f"{sorted(rec)}",
    )
    check(
        "record key set matches mayatk's v2 schema",
        sorted(rec)
        == sorted(
            [
                "name",
                "type",
                "texture",
                "intensity",
                "source",
                "source_type",
                "source_size",
                "source_angle",
                "follow_source",
                "contact",
                "ground",
                "radius",
                "height",
                "max_stretch",
                "canvas",
            ]
        ),
        f"{sorted(rec)}",
    )
    # A SUN source records its angular diameter instead of a world size.
    reset()
    c = cube("Box")
    sun("Sun")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], source_name="Sun", texture_res=32)
    rec = _json.loads(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))["planes"][
        0
    ]
    check(
        "record: a sun is directional and carries source_angle",
        rec["source_type"] == "directional"
        and rec["source_size"] == 0.0
        and rec["source_angle"] > 0.0,
        f"{rec['source_type']} {rec['source_angle']}",
    )

    # ============================ MULTI-SOURCE ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rigs = ShadowRig.create_for_sources(
        [c], ["shadow_source", "fillLight"], texture_res=32
    )
    check(
        "create_for_sources builds one plane per source, default keeps the plain name",
        [r.shadow_plane.name for r in rigs] == ["Box_shadow", "Box_fillLight_shadow"],
        f"{[r.shadow_plane.name for r in rigs]}",
    )
    check("per-source PNGs are distinct", rigs[0].texture_path != rigs[1].texture_path)
    check(
        "an existing object is a valid source (no extra empty)",
        bpy.data.objects.get("fillLight") is rigs[1].light,
    )
    for r in rigs:
        if r.texture_path and os.path.exists(r.texture_path):
            os.remove(r.texture_path)

    # ============================ RE-ENTRANCY + GUARDS ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    p = rig.shadow_plane
    rig2 = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=64)
    n_first = len(
        [
            d
            for d in p.animation_data.drivers
            if d.data_path == "location" and d.array_index == 0
        ]
    )
    check(
        "re-create does not stack drivers on the first rig", n_first == 1, f"{n_first}"
    )
    check(
        "re-create uniquifies the second rig (Maya parity)",
        rig2.shadow_plane.name == "Box1_shadow"
        and rig2.material is not rig.material
        and os.path.basename(rig2.texture_path) == "Box1_shadow.png",
        f"{rig2.shadow_plane.name} / {rig2.material.name} / {rig2.texture_path}",
    )
    check(
        "first rig keeps its own opacity driver",
        drv(rig.material.node_tree, ShadowRig._MATERIAL_OPACITY_PATH, None) is not None,
    )
    for extra_tex in (rig.texture_path, rig2.texture_path):
        if extra_tex and os.path.exists(extra_tex):
            os.remove(extra_tex)

    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32, mode="stretch")
    check("retired 'stretch' mode builds as orbit", rig.mode == "orbit")
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32, axis="y")
    check(
        "a retired explicit axis still builds the projected silhouette",
        rig.image is not None and tuple(rig.image.size) == (32, 32),
    )

    # ---- create rollback ----
    reset()
    holder = bpy.data.objects.new("NoMesh", None)  # empty, no mesh descendants
    bpy.context.collection.objects.link(holder)
    bpy.context.view_layer.update()
    before = {
        "objects": {o.name for o in bpy.data.objects},
        "meshes": {m.name for m in bpy.data.meshes},
        "materials": {m.name for m in bpy.data.materials},
        "images": {i.name for i in bpy.data.images},
    }
    try:
        ShadowRig.create([holder], texture_res=32)
        check("mesh-less target raises", False)
    except ValueError:
        check("mesh-less target raises", True)
    after = {
        "objects": {o.name for o in bpy.data.objects},
        "meshes": {m.name for m in bpy.data.meshes},
        "materials": {m.name for m in bpy.data.materials},
        "images": {i.name for i in bpy.data.images},
    }
    check(
        "failed create rolls back all datablocks",
        after == before,
        f"leaked: { {k: sorted(after[k] - before[k]) for k in after if after[k] != before[k]} }",
    )

    # ---- evaluated (modifier) footprint ----
    reset()
    c = cube("Box")
    mod = c.modifiers.new("arr", "ARRAY")
    mod.count = 2
    mod.relative_offset_displace = (1.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], texture_res=32)
    check(
        "footprint measures evaluated (modifier) geometry",
        approx(rig.footprint_radius, math.hypot(4, 2) / 2, 0.05),
        f"r={rig.footprint_radius:.3f}",
    )

    # ---- linked-library guard ----
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], texture_res=32)
    import tempfile

    lib_path = os.path.join(tempfile.gettempdir(), "btk_shadow_lib.blend")
    bpy.ops.wm.save_as_mainfile(filepath=lib_path, copy=True)
    ShadowRig.delete_rigs(delete_textures=True)  # drop the local rig
    with bpy.data.libraries.load(lib_path, link=True) as (src, dst):
        dst.objects = [n for n in src.objects if n == "Box_shadow"]
    linked = next(
        (o for o in bpy.data.objects if o.name == "Box_shadow" and o.library), None
    )
    check("library plane linked", linked is not None)
    check(
        "find_shadow_planes sees the linked plane",
        linked in ShadowRig.find_shadow_planes(),
        f"{[o.name for o in ShadowRig.find_shadow_planes()]}",
    )
    check("bake_planes skips the linked plane", ShadowRig.bake_planes() == [])
    check("delete_rigs skips the linked plane", ShadowRig.delete_rigs() == [])
    check(
        "linked plane survives the skipped teardown",
        any(o.name == "Box_shadow" and o.library for o in bpy.data.objects),
    )
    try:
        os.remove(lib_path)
    except OSError:
        pass

    # ============================ DELETE ============================
    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    tex = rig.texture_path
    deleted = ShadowRig.delete_rigs([rig.shadow_plane], delete_textures=True)
    check("delete_rigs returns the plane name", deleted == ["Box_shadow"], f"{deleted}")
    check(
        "delete removes plane/group/contact",
        not any(
            bpy.data.objects.get(n)
            for n in ("Box_shadow", "Box_shadow_grp", "Box_contact")
        ),
        f"{[o.name for o in bpy.data.objects]}",
    )
    check(
        "delete keeps target + shared source",
        bpy.data.objects.get("Box") is not None
        and bpy.data.objects.get("shadow_source") is not None,
    )
    check(
        "delete frees material + image datablocks",
        bpy.data.materials.get("Box_shadow_mat") is None
        and bpy.data.images.get("Box_shadow") is None,
    )
    check("delete_textures removes the PNG", not (tex and os.path.exists(tex)))
    check(
        "delete clears the metadata channel",
        DataNodes.get_export_string(ShadowRig.SHADOW_METADATA) is None,
    )

    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    rig.bake(1, 2)
    rig.delete()
    check(
        "baked rig still tears down fully",
        bpy.data.objects.get("Box_shadow") is None
        and bpy.data.objects.get("Box_contact") is None,
    )

    rig = ShadowRig.create([c], light_pos=(5, 5, 10), texture_res=32)
    grp = rig.shadow_plane.parent
    found = ShadowRig.find_shadow_planes([grp, rig.shadow_plane])
    check(
        "find_shadow_planes dedups an overlapping selection",
        len(found) == 1,
        f"{[o.name for o in found]}",
    )
    deleted = ShadowRig.delete_rigs([grp, rig.shadow_plane])
    check(
        "delete_rigs survives an overlapping selection",
        deleted == ["Box_shadow"] and bpy.data.objects.get("Box_shadow") is None,
        f"{deleted}",
    )
    check(
        "second delete on the stale ref no-ops (mirror of Maya)",
        ShadowRig.delete_rigs([rig.shadow_plane]) == [],
    )

    # ---- footprint includes descendants ----
    reset()
    parent = bpy.data.objects.new("Grp", None)  # empty
    bpy.context.collection.objects.link(parent)
    import bmesh

    cme = bpy.data.meshes.new("Big_mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=4.0)
    bm.to_mesh(cme)
    bm.free()
    child = bpy.data.objects.new("Big", cme)
    bpy.context.collection.objects.link(child)
    child.parent = parent
    bpy.context.view_layer.update()
    rig = ShadowRig.create([parent], texture_res=32)
    check(
        "footprint reflects descendant geometry (not the empty's unit cube)",
        rig.footprint_radius > 2.5,
        f"r={rig.footprint_radius:.3f}",
    )
    check(
        "contact uses descendant min-Z (-2)",
        approx(rig.contact.matrix_world.translation[2], -2.0),
        f"z={rig.contact.matrix_world.translation[2]:.3f}",
    )

    # ============================ HORIZON RIG ============================
    def record(name):
        payload = _json.loads(DataNodes.get_export_string(ShadowRig.SHADOW_METADATA))
        return {r["name"]: r for r in payload["planes"]}[name]

    reset()
    c = cube("Box")
    bpy.context.view_layer.update()
    rig = ShadowRig.create(
        [c],
        light_pos=(5, 5, 10),
        texture_res=32,
        rig_type="horizon",
        horizon_bins=8,
        horizon_size=(32, 16),
    )
    check(
        "rig_type='horizon' bakes the map",
        rig.rig_type == "horizon"
        and rig.horizon_path.endswith("Box_horizon.png")
        and os.path.exists(rig.horizon_path),
        f"{rig.rig_type} {rig.horizon_path}",
    )
    check(
        "plane carries the type stamp",
        ShadowRig.plane_type(rig.shadow_plane) == "horizon",
    )
    plane_matches_model(rig.shadow_plane, "horizon rig")
    rec = record("Box_shadow")
    check(
        "record: type horizon, silhouette unchanged",
        rec["type"] == "horizon" and rec["texture"] == "Box_shadow.png",
        f"{rec['type']} {rec['texture']}",
    )
    hz = rec["horizon"]
    check(
        "horizon block: texture / bins / layers / tile",
        hz["texture"] == "Box_horizon.png"
        and (hz["bins"], hz["layers"], hz["tile"]) == (8, 2, [32, 16]),
        f"{hz}",
    )
    check(
        "horizon block: layout is the 2 x bins tile grid",
        hz["layout"] == [4, 4],
        f"{hz['layout']}",
    )
    check(
        "horizon block: log-polar mapping over a positive range",
        hz["mapping"] == "logpolar" and hz["r_max"] > hz["r_min"] > 0,
        f"{hz['mapping']} {hz['r_min']}..{hz['r_max']}",
    )
    # The cotangents are encoded against the maxStretch the map was BAKED
    # with; the artist can retune the live property afterwards, so the block
    # carries its own scale rather than letting the engine read the live one.
    check(
        "horizon block: carries the encode scale it was baked with",
        approx(hz["max_stretch"], 6.0, 1e-6) and approx(rec["max_stretch"], 6.0, 1e-6),
        f"horizon {hz['max_stretch']} / record {rec['max_stretch']}",
    )
    # Blender's exporter maps local +Y to FBX -Z, so frame_b is (0, 0, -1)
    # where Maya writes (0, 0, 1) — the one axis divergence in the contract.
    check(
        "horizon block: the frame is written in FBX/glTF axes (Blender: b = -Z)",
        (hz["frame_a"], hz["frame_b"]) == ([1.0, 0.0, 0.0], [0.0, 0.0, -1.0]),
        f"{hz['frame_a']} {hz['frame_b']}",
    )
    check(
        "horizon block: frame matches the class constant",
        (tuple(hz["frame_a"]), tuple(hz["frame_b"])) == ShadowRig.HORIZON_FRAME,
    )
    check(
        "horizon block: encoding + identity rect while unpacked",
        hz["encoding"] == ShadowHorizon.ENCODING and hz["rect"] == [1.0, 1.0, 0.0, 0.0],
        f"{hz}",
    )
    hpx = ShadowRig._read_png(rig.horizon_path)
    check(
        "horizon PNG holds the 2 x bins tiles the layout says",
        hpx is not None and hpx.shape[:2] == (4 * 16, 4 * 32),
        f"{None if hpx is None else hpx.shape}",
    )
    # Orientation: the PNG's TOP row is the r_min ring (the contract), so the
    # grounded tile 0 falls off downward — a missing flip would invert this.
    tile0 = hpx[0:16, 0:32]
    check(
        "horizon PNG top row is the r_min ring (r_max row is the faint one)",
        int(tile0[0].sum()) > 2 * int(tile0[-1].sum()),
        f"top {int(tile0[0].sum())} vs bottom {int(tile0[-1].sum())}",
    )
    again = ShadowRig.from_plane(rig.shadow_plane)
    check(
        "from_plane restores the type and the map path",
        (again.rig_type, again.horizon_path) == ("horizon", rig.horizon_path),
        f"{again.rig_type} {again.horizon_path}",
    )

    # ---- Recalculate re-bakes the map only on a geometry change ----
    before = os.path.getmtime(rig.horizon_path)
    ShadowRig.refresh_silhouette([rig.shadow_plane])
    check(
        "Recalculate leaves the map alone when the geometry is unchanged",
        os.path.getmtime(rig.horizon_path) == before,
    )
    c.scale = (1.0, 1.0, 2.0)
    bpy.context.view_layer.update()
    ShadowRig.refresh_silhouette([rig.shadow_plane])
    check(
        "Recalculate re-bakes the map after a geometry change",
        os.path.getmtime(rig.horizon_path) > before,
        f"{os.path.getmtime(rig.horizon_path)} vs {before}",
    )
    # Retuning maxStretch changes what the map's cotangents mean, so it
    # re-bakes too — and the record carries the new encode scale.
    after = os.path.getmtime(rig.horizon_path)
    rig.shadow_plane["maxStretch"] = 3.0
    rig.shadow_plane.update_tag()
    bpy.context.view_layer.update()
    ShadowRig.refresh_silhouette([rig.shadow_plane])
    check(
        "retuning maxStretch re-bakes the map (it is the encode scale)",
        os.path.getmtime(rig.horizon_path) > after,
        f"{os.path.getmtime(rig.horizon_path)} vs {after}",
    )
    rec = record("Box_shadow")
    check(
        "the retuned scale reaches both the horizon block and the record",
        approx(rec["horizon"]["max_stretch"], 3.0, 1e-6)
        and approx(rec["max_stretch"], 3.0, 1e-6),
        f"horizon {rec['horizon']['max_stretch']} / record {rec['max_stretch']}",
    )

    # ---- rebuild keeps the type and the horizon params ----
    rebuilt = ShadowRig.rebuild(rig.shadow_plane)
    check(
        "rebuild keeps the horizon type",
        rebuilt is not None and rebuilt.rig_type == "horizon",
        f"{None if rebuilt is None else rebuilt.rig_type}",
    )
    check(
        "rebuild keeps the horizon bins",
        record("Box_shadow")["horizon"]["bins"] == 8,
        f"{record('Box_shadow')['horizon']}",
    )

    # ============================ PER OBJECT + ATLAS ============================
    reset()
    c = cube("Box")
    other = cube("Crate", loc=(4, 0, 0))
    bpy.context.view_layer.update()
    rigs = ShadowRig.create_per_object(
        [c, other], [ShadowRig.DEFAULT_SOURCE_NAME], texture_res=32
    )
    check(
        "create_per_object builds one rig per target",
        [r.shadow_plane.name for r in rigs] == ["Box_shadow", "Crate_shadow"],
        f"{[r.shadow_plane.name for r in rigs]}",
    )
    check(
        "each per-object rig gets its own contact",
        sorted(record(n)["contact"] for n in ("Box_shadow", "Crate_shadow"))
        == ["Box_contact", "Crate_contact"],
        f"{[record(n)['contact'] for n in ('Box_shadow', 'Crate_shadow')]}",
    )

    packed = ShadowRig.pack_atlas([r.shadow_plane for r in rigs])
    atlas = packed["projected"]
    check(
        "pack_atlas writes one silhouette atlas",
        atlas.endswith("shadow_atlas_projected.png") and os.path.exists(atlas),
        f"{packed}",
    )
    apx = ShadowRig._read_png(atlas)
    check(
        "the atlas is two 32 px cells side by side",
        apx is not None and apx.shape[:2] == (32, 64),
        f"{None if apx is None else apx.shape}",
    )
    box, crate = rigs[0].shadow_plane, rigs[1].shadow_plane
    check("the plane is marked atlased", ShadowRig.plane_is_atlased(box))
    check(
        "the material samples the atlas image",
        ShadowRig._plane_texture_image(box).name == "shadow_atlas_projected",
        f"{ShadowRig._plane_texture_image(box).name}",
    )
    # The plane's own PNG is still what the record and Recalculate use.
    check(
        "the plane's OWN png still resolves while packed",
        os.path.basename(ShadowRig._plane_texture_path(box)) == "Box_shadow.png",
        f"{ShadowRig._plane_texture_path(box)}",
    )
    rec = record("Box_shadow")
    check(
        "record: texture stays the plane's own, atlas rides its own block",
        rec["texture"] == "Box_shadow.png"
        and rec["atlas"]["texture"] == "shadow_atlas_projected.png",
        f"{rec.get('atlas')}",
    )
    sx, sy, ox, oy = rec["atlas"]["rect"]
    check(
        "the published rect is a gutter-inset half", sx < 0.5, f"{rec['atlas']['rect']}"
    )
    uvs = [
        (float(lp.uv[0]), float(lp.uv[1])) for lp in box.data.uv_layers["UVMap"].data
    ]
    check(
        "the quad's UVs are remapped into the inset rect",
        all(
            ox - 1e-6 <= u <= ox + sx + 1e-6 and oy - 1e-6 <= v <= oy + sy + 1e-6
            for u, v in uvs
        ),
        f"{[tuple(round(x, 4) for x in uv) for uv in uvs]}",
    )

    def cells():
        data = ShadowRig._read_png(atlas)
        return data[0:32, 0:32], data[0:32, 32:64]

    def tile(plane):
        return ShadowRig._read_png(ShadowRig._plane_texture_path(plane))

    box_cell, crate_cell = cells()
    check(
        "each atlas cell holds that plane's own tile",
        bool((box_cell == tile(box)).all()) and bool((crate_cell == tile(crate)).all()),
    )
    crate_before = crate_cell
    bpy.data.objects["shadow_source"].location = (-6.0, -6.0, 4.0)
    bpy.context.view_layer.update()
    ShadowRig.refresh_silhouette([box])
    box_cell, crate_cell = cells()
    check(
        "Recalculate rewrites one tile in place (the atlas is not repacked)",
        ShadowRig._read_png(atlas).shape[:2] == (32, 64),
    )
    check(
        "the rewritten cell is the plane's new tile",
        bool((box_cell == tile(box)).all()),
    )
    check(
        "the other plane's cell is byte-identical",
        bool((crate_cell == crate_before).all()),
    )
    check(
        "the material still samples the atlas",
        ShadowRig._plane_texture_image(box).name == "shadow_atlas_projected",
    )
    # Unpack restores unit UVs and the plane's own image.
    ShadowRig.unpack_atlas([box, crate])
    check("unpack clears the atlas stamp", not ShadowRig.plane_is_atlased(box))
    check(
        "unpack restores unit UVs",
        sorted(
            (round(float(lp.uv[0]), 6), round(float(lp.uv[1]), 6))
            for lp in box.data.uv_layers["UVMap"].data
        )
        == [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)],
        f"{[tuple(round(float(x), 4) for x in lp.uv) for lp in box.data.uv_layers['UVMap'].data]}",
    )
    check(
        "unpack rebinds the plane's own image",
        ShadowRig._plane_texture_image(box).name == "Box_shadow",
        f"{ShadowRig._plane_texture_image(box).name}",
    )
    check(
        "unpack drops the atlas block from the record",
        "atlas" not in record("Box_shadow"),
    )

    # ---- a packed plane whose tile PNG vanished leaves the atlas ----
    # Its rect would otherwise be handed to another plane by the next repack,
    # and it would render that prop's shadow.
    reset()
    c = cube("Box")
    other = cube("Crate", loc=(4, 0, 0))
    bpy.context.view_layer.update()
    rigs = ShadowRig.create_per_object([c, other], ["shadow_source"], texture_res=32)
    box, crate = rigs[0].shadow_plane, rigs[1].shadow_plane
    ShadowRig.pack_atlas([box, crate])
    os.remove(ShadowRig._plane_texture_path(box))
    ShadowRig.pack_atlas([crate])
    check(
        "a plane whose tile PNG is gone leaves the atlas",
        not ShadowRig.plane_is_atlased(box),
    )
    check(
        "the dropped plane's record loses its atlas block",
        "atlas" not in record("Box_shadow"),
    )
    check(
        "the dropped plane is back on unit UVs",
        sorted(
            (round(float(lp.uv[0]), 6), round(float(lp.uv[1]), 6))
            for lp in box.data.uv_layers["UVMap"].data
        )
        == [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)],
        f"{[tuple(round(float(x), 4) for x in lp.uv) for lp in box.data.uv_layers['UVMap'].data]}",
    )
    check(
        "the dropped plane samples its own image again",
        ShadowRig._plane_texture_image(box).name == "Box_shadow",
        f"{ShadowRig._plane_texture_image(box).name}",
    )
    # The survivor keeps its own tile, now the only one in the atlas: a single
    # 32 px cell, inset by the gutter on each side.
    check(
        "the survivor stays packed at the single-cell rect",
        ShadowRig.plane_is_atlased(crate)
        and approx(
            record("Crate_shadow")["atlas"]["rect"][0],
            (32 - 2 * ShadowAtlas.GUTTER) / 32,
            1e-6,
        ),
        f"{record('Crate_shadow')['atlas']['rect']}",
    )
    survivor = ShadowRig._read_png(ShadowRig._atlas_path("projected"))
    check(
        "the survivor's cell still holds its own tile",
        bool(
            (
                survivor[0:32, 0:32]
                == ShadowRig._read_png(ShadowRig._plane_texture_path(crate))
            ).all()
        ),
    )
    ShadowRig.delete_rigs(None, delete_textures=True)

    # ---- horizon maps pack into their own atlas ----
    reset()
    c = cube("Box")
    other = cube("Crate", loc=(4, 0, 0))
    bpy.context.view_layer.update()
    rigs = ShadowRig.create_per_object(
        [c, other],
        ["shadow_source"],
        texture_res=32,
        rig_type="horizon",
        horizon_bins=8,
        horizon_size=(32, 16),
    )
    packed = ShadowRig.pack_atlas([r.shadow_plane for r in rigs])
    check(
        "both kinds pack into their own atlas",
        sorted(packed) == ["horizon", "projected"],
        f"{sorted(packed)}",
    )
    hz = record("Box_shadow")["horizon"]
    check(
        "the horizon block names the horizon atlas and a packed rect",
        hz["texture"] == "shadow_atlas_horizon.png" and hz["rect"][0] < 0.5,
        f"{hz['texture']} {hz['rect']}",
    )
    check(
        "the horizon frame survives packing",
        (hz["frame_a"], hz["frame_b"]) == ([1.0, 0.0, 0.0], [0.0, 0.0, -1.0]),
    )
    # Deleting one rig repacks the survivor alone (a full-width rect).
    ShadowRig.delete_rigs([rigs[0].shadow_plane])
    hz = record("Crate_shadow")["horizon"]
    check(
        "deleting a packed rig repacks the survivor alone",
        hz["rect"][0] > 0.9,
        f"{hz['rect']}",
    )
    ShadowRig.delete_rigs(None, delete_textures=True)
    for kind in ShadowRig.RIG_TYPES:
        path = ShadowRig._atlas_path(kind)
        check(
            f"the {kind} atlas is removed once nothing samples it",
            not os.path.exists(path),
            path,
        )

    # ==================== PANEL: PACKING IS A COMMIT-ONLY STEP ====================
    # Headless Blender ships no Qt, so the panel seam is driven through a bare
    # slots instance with stubbed combos — the very methods Preview calls.
    import types

    def slots_stub(planes="Combined", atlas="On", rig="Projected", res=32):
        s = ShadowRigSlots.__new__(ShadowRigSlots)
        s.ui = types.SimpleNamespace(
            chk_combine=types.SimpleNamespace(isChecked=lambda: True),
            txt_source=types.SimpleNamespace(
                text=lambda: ShadowRig.DEFAULT_SOURCE_NAME
            ),
            cmb_type=types.SimpleNamespace(currentText=lambda: f"Rig:  {rig}"),
            cmb_planes=types.SimpleNamespace(currentText=lambda: f"Planes:  {planes}"),
            cmb_atlas=types.SimpleNamespace(currentText=lambda: f"Atlas:  {atlas}"),
            s000=types.SimpleNamespace(currentText=lambda: f"Resolution: {res}"),
        )
        s.sb = types.SimpleNamespace(message_box=lambda *a, **k: None)
        s._preview_textures = []
        s._built_rigs = []
        s._built_sources = None
        return s

    reset()
    c = cube("Box")
    other = cube("Crate", loc=(4, 0, 0))
    bpy.context.view_layer.update()
    slots = slots_stub(planes="Per object", atlas="On")
    slots.perform_operation([c, other])
    atlas_path = ShadowRig._atlas_path("projected")
    check(
        "a preview pass builds every plane",
        len(ShadowRig.find_shadow_planes()) == 2,
        f"{[p.name for p in ShadowRig.find_shadow_planes()]}",
    )
    check(
        "a preview pass never writes the atlas (it is a shared file)",
        not os.path.exists(atlas_path)
        and not any(
            ShadowRig.plane_is_atlased(p) for p in ShadowRig.find_shadow_planes()
        ),
        atlas_path,
    )
    check(
        "the preview tracks each rig's OWN pngs, never the atlas",
        all(
            not os.path.basename(t).startswith("shadow_atlas")
            for t in slots._preview_textures
        ),
        f"{[os.path.basename(t) for t in slots._preview_textures]}",
    )
    slots._pack_after_commit()
    check(
        "the commit hook packs the committed rigs",
        os.path.exists(atlas_path)
        and all(ShadowRig.plane_is_atlased(p) for p in ShadowRig.find_shadow_planes()),
    )

    committed = ShadowRig._read_png(atlas_path)
    third = cube("Prop", loc=(-4, 0, 0))
    bpy.context.view_layer.update()
    rehearsal = slots_stub(planes="Per object", atlas="On")
    rehearsal.perform_operation([third])
    # Preview's rollback, then its discard hook.
    ShadowRig.delete_rigs([r.shadow_plane for r in rehearsal._built_rigs])
    rehearsal._restore_after_preview()
    check(
        "a discarded preview leaves the committed atlas byte-identical",
        bool((ShadowRig._read_png(atlas_path) == committed).all()),
    )
    check(
        "the committed rigs are still packed after the discard",
        len(ShadowRig.find_shadow_planes()) == 2
        and all(ShadowRig.plane_is_atlased(p) for p in ShadowRig.find_shadow_planes()),
    )
    check(
        "a discarded preview removes only its own png",
        not os.path.exists(os.path.join(os.path.dirname(atlas_path), "Prop_shadow.png"))
        and all(
            os.path.exists(ShadowRig._plane_texture_path(p))
            for p in ShadowRig.find_shadow_planes()
        ),
    )
    check(
        "Atlas: Off never packs",
        slots_stub(atlas="Off")._pack_if_wanted(
            [types.SimpleNamespace(shadow_plane=None)]
        )
        == {},
    )
    check(
        "every Atlas combo label round-trips through _atlas_mode",
        [slots_stub(atlas=m)._atlas_mode() for m in ShadowRigSlots.ATLAS_MODES]
        == list(ShadowRigSlots.ATLAS_MODES),
        f"{ShadowRigSlots.ATLAS_MODES}",
    )
    check(
        "the Horizon rig type has a builder",
        slots_stub(rig="Horizon")._rig_builder()
        == ShadowRig.create_horizon_for_sources,
    )
    ShadowRig.delete_rigs(None, delete_textures=True)

    reset()
    check(
        "refresh clears the channel with no shadow planes",
        ShadowRig.refresh_export_metadata() is None
        and DataNodes.get_export_string(ShadowRig.SHADOW_METADATA) is None,
    )
    try:
        ShadowRig.create([])
        check("rejects empty target list", False)
    except ValueError:
        check("rejects empty target list", True)

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
