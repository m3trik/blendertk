"""Headless suite for blendertk.mat_utils.emissive_groups.EmissiveGroups.

Mirror-contract checks: same registry schema, slot stability, manifest wire
format, and bake behaviors as mayatk's EmissiveGroups (test_emissive_groups.py
on the Maya side covers the same surface).

Run: blender --background --factory-startup --python blendertk/test/test_emissive_groups.py
"""

import os
import sys
import json
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
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
    )


try:
    import bpy

    from blendertk.mat_utils.emissive_groups import EmissiveGroups
    from blendertk.node_utils.data_nodes import DataNodes

    # Fresh scene with one cube (factory startup ships one; make it explicit).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "eg_cube"

    # --- authoring -------------------------------------------------------
    attr = EmissiveGroups.add_group("front", {"eg_cube": [0]})
    check("add_group returns attr name", attr == "emissiveGroup_front", attr)
    check(
        "boolean FACE attribute created",
        cube.data.attributes["emissiveGroup_front"].domain == "FACE",
    )
    EmissiveGroups.add_group("top", {"eg_cube": [1, 3]})
    groups = EmissiveGroups.list_groups()
    check("slots 0,1", (groups["front"]["slot"], groups["top"]["slot"]) == (0, 1), groups)
    check("face counts", (groups["front"]["faces"], groups["top"]["faces"]) == (1, 2))

    EmissiveGroups.add_group("front", {"eg_cube": [2]})  # extend
    check("extend keeps slot", EmissiveGroups.list_groups()["front"]["slot"] == 0)
    check("extend adds faces", EmissiveGroups.list_groups()["front"]["faces"] == 2)

    # Whole-mesh entry resolves to all faces.
    EmissiveGroups.add_group("shell", {"eg_cube": []})
    check("whole-mesh membership", EmissiveGroups.list_groups()["shell"]["faces"] == 6)
    EmissiveGroups.remove_group("shell")

    # --- registry hygiene + slot stability -------------------------------
    check(
        "registry on data_internal",
        DataNodes.get_internal_string("emissive_groups") is not None,
    )
    EmissiveGroups.remove_group("front")
    EmissiveGroups.add_group("new", {"eg_cube": [4]})
    slots = EmissiveGroups.list_groups()
    check("removed slot retired (shell=2 retired, front=0 retired -> new=3)",
          slots["new"]["slot"] == 3, slots)
    reclaimed = EmissiveGroups.compact_slots()
    check("compact reclaims", sorted(reclaimed) == [0, 2], reclaimed)

    # --- validate ---------------------------------------------------------
    EmissiveGroups.add_group("overlap", {"eg_cube": [1]})  # overlaps 'top'
    warnings = EmissiveGroups.validate()
    check("overlap warned", any("overlaps" in w for w in warnings), warnings)
    EmissiveGroups.remove_group("overlap")
    EmissiveGroups.compact_slots()

    # --- context safety: no screen-context reads ---------------------------
    # The slots run from tentacle's Qt event-pump timer, where
    # `bpy.context.window` is None: `selected_objects` is then absent
    # entirely (AttributeError) and `context.scene` unreliable. Simulate it
    # by driving the selection path with the window context stripped.
    import ast
    import blendertk.mat_utils.emissive_groups as eg_mod

    def _chain(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    # AST, not text: docstrings legitimately NAME these members while
    # explaining why they aren't used.
    tree = ast.parse(open(eg_mod.__file__, encoding="utf-8").read())
    screen_ctx = ("bpy.context.selected_objects", "bpy.context.scene",
                  "bpy.context.active_object", "bpy.context.view_layer")
    offenders = sorted(
        {
            c
            for c in (_chain(n) for n in ast.walk(tree) if isinstance(n, ast.Attribute))
            if c.startswith(screen_ctx)
        }
    )
    check("no screen-context reads in the engine", not offenders, offenders)

    for obj in bpy.data.objects:
        obj.select_set(obj.name == "eg_cube")
    check(
        "selection resolves via the view layer",
        list(EmissiveGroups._faces_from()) == ["eg_cube"],
        list(EmissiveGroups._faces_from()),
    )

    # --- scene hygiene: authoring must not create the export carrier -------
    check(
        "authoring left no data_export carrier",
        bpy.data.objects.get("data_export") is None,
        [o.name for o in bpy.data.objects if o.name.startswith("data_")],
    )
    EmissiveGroups.refresh_export_metadata()  # explicit publish
    EmissiveGroups.add_group("hygiene", {"eg_cube": [5]})
    published = DataNodes.get_export_string("emissive_groups")
    check(
        "published manifest kept current by authoring",
        published is not None and "hygiene" in published,
        published,
    )
    EmissiveGroups.remove_group("hygiene")
    EmissiveGroups.compact_slots()

    # --- keyable weights (opt-in) -----------------------------------------
    plugs = EmissiveGroups.make_weights_keyable(["top"])
    check(
        "keyable prop created",
        plugs.get("top", "").endswith('["emissiveGroup_top"]'),
        plugs,
    )
    carrier = DataNodes.get_export_node(create=False)
    check(
        "keyable prop on carrier",
        carrier is not None and "emissiveGroup_top" in carrier,
    )
    payload = json.loads(DataNodes.get_export_string("emissive_groups"))
    by_name = {g["name"]: g for g in payload["groups"]}
    check("manifest records attr", by_name["top"].get("attr") == "emissiveGroup_top")
    check("non-keyable group has no attr", "attr" not in by_name["new"], by_name)

    EmissiveGroups.key_weight("top", value=1.0, frame=1)
    EmissiveGroups.key_weight("top", value=0.0, frame=10)
    fc = EmissiveGroups._weight_fcurve("top")
    check("keyed 2 frames", fc is not None and len(fc.keyframe_points) == 2)
    check(
        "key values",
        fc is not None
        and [kp.co[1] for kp in sorted(fc.keyframe_points, key=lambda k: k.co[0])]
        == [1.0, 0.0],
    )

    EmissiveGroups.set_default("new", 0.25)
    EmissiveGroups.make_weights_keyable(["new"])
    check(
        "keyable seeds from default",
        abs(carrier["emissiveGroup_new"] - 0.25) < 1e-6,
        carrier.get("emissiveGroup_new"),
    )
    EmissiveGroups.set_default("new", 0.75)
    check(
        "unkeyed prop follows default",
        abs(carrier["emissiveGroup_new"] - 0.75) < 1e-6,
    )
    EmissiveGroups.set_default("top", 0.5)  # keyed: animation owns the value
    check("keyed prop keeps its fcurve", EmissiveGroups._weight_fcurve("top") is not None)

    removed = EmissiveGroups.remove_keyable_weights()
    check("remove strips props", sorted(removed) == ["new", "top"], removed)
    check(
        "props gone from carrier",
        "emissiveGroup_top" not in carrier and "emissiveGroup_new" not in carrier,
    )
    check(
        "groups intact after strip",
        sorted(EmissiveGroups.list_groups()) == ["new", "top"],
    )
    payload = json.loads(DataNodes.get_export_string("emissive_groups"))
    check(
        "manifest attr records cleared",
        all("attr" not in g for g in payload["groups"]),
    )

    # Orphan carrier prop: a reimport restores keyable props, not the registry.
    carrier["emissiveGroup_ghost"] = 1.0
    warnings = EmissiveGroups.validate()
    check(
        "orphan carrier prop warned",
        any("emissiveGroup_ghost" in w and "no registry entry" in w for w in warnings),
        warnings,
    )
    del carrier["emissiveGroup_ghost"]

    # --- vertex-color bake ------------------------------------------------
    manifest = EmissiveGroups.bake_vertex_colors()
    check("bake manifest encoding", manifest["encoding"] == "vertex-color")
    check("color_set name", manifest["color_set"] == "emissiveGroups")
    cattr = cube.data.color_attributes.get("emissiveGroups")
    check("color attribute exists", cattr is not None)
    check("corner domain", cattr is not None and cattr.domain == "CORNER")
    if cattr is not None:
        # top = slot 1 -> G on faces 1,3; new = slot 3 -> A on face 4.
        poly = cube.data.polygons[1]
        cols = [tuple(cattr.data[lo].color) for lo in poly.loop_indices]
        check("top face G=1", all(abs(c[1] - 1.0) < 0.02 for c in cols), cols[0])
        check("top face R=0", all(abs(c[0]) < 0.02 for c in cols))
        poly4 = cube.data.polygons[4]
        cols4 = [tuple(cattr.data[lo].color) for lo in poly4.loop_indices]
        check("new face A=1", all(abs(c[3] - 1.0) < 0.02 for c in cols4), cols4[0])
        poly5 = cube.data.polygons[5]
        cols5 = [tuple(cattr.data[lo].color) for lo in poly5.loop_indices]
        check(
            "unmember face zeroed",
            all(all(abs(v) < 0.02 for v in c[:3]) and abs(c[3]) < 0.02 for c in cols5),
        )

    payload = DataNodes.get_export_string("emissive_groups")
    check("manifest published to data_export", payload is not None)
    if payload:
        data = json.loads(payload)
        check("manifest schema 1", data.get("schema") == 1)
        check(
            "manifest groups sorted by slot",
            [g["slot"] for g in data["groups"]]
            == sorted(g["slot"] for g in data["groups"]),
        )

    # Foreign color attribute guard.
    cube.data.color_attributes.new(name="paintjob", type="BYTE_COLOR", domain="CORNER")
    try:
        EmissiveGroups.bake_vertex_colors()
        check("foreign color attr refused", False)
    except ValueError:
        check("foreign color attr refused", True)
    manifest = EmissiveGroups.bake_vertex_colors(force=True)
    check("force overrides foreign guard", manifest["encoding"] == "vertex-color")

    # --- mask bake (channels) --------------------------------------------
    tmp = tempfile.mkdtemp()
    mask_path = os.path.join(tmp, "eg_EMask.png")
    manifest = EmissiveGroups.bake_mask(output_path=mask_path, resolution=64)
    check("mask written", os.path.isfile(mask_path))
    check("mask manifest encoding", manifest["encoding"] == "channels", manifest)
    check("mask manifest sidecar", os.path.isfile(os.path.join(tmp, "eg_EMask.json")))
    payload = json.loads(DataNodes.get_export_string("emissive_groups"))
    check("export carrier switched to channels", payload["encoding"] == "channels")

    # --- teardown hygiene -------------------------------------------------
    for name in list(EmissiveGroups.list_groups()):
        EmissiveGroups.remove_group(name)
    EmissiveGroups.compact_slots()
    check(
        "registry cleared when empty",
        DataNodes.get_internal_string("emissive_groups") is None,
    )
    check(
        "export carrier cleared when empty",
        DataNodes.get_export_string("emissive_groups") is None,
    )

    # --- keyed-weight curve transport (export proxies) ---------------------
    # Fresh scene: the FBX round-trip below imports objects, which must not
    # bleed into the sections above.
    from blendertk.anim_utils._anim_utils import AnimUtils
    from blendertk.env_utils.fbx_utils import FbxUtils

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    kcube = bpy.context.active_object
    kcube.name = "kw_cube"
    EmissiveGroups.add_group("glow", {"kw_cube": [0]})
    EmissiveGroups.make_weights_keyable(["glow"])
    EmissiveGroups.key_weight("glow", value=1.0, frame=1)
    EmissiveGroups.key_weight("glow", value=0.0, frame=10)

    proxies = EmissiveGroups.create_export_curve_proxies()
    check("one proxy per keyed group", len(proxies) == 1, [o.name for o in proxies])
    proxy = proxies[0] if proxies else None
    check("proxy named the manifest attr", proxy is not None and proxy.name == "emissiveGroup_glow")
    check(
        "proxy marked + parented under the carrier",
        proxy is not None
        and proxy.get(EmissiveGroups.PROXY_MARKER)
        and proxy.parent is DataNodes.get_export_node(create=False),
    )
    pfc = next(
        (
            f
            for f in AnimUtils.get_fcurves([proxy] if proxy else [])
            if f.data_path == "scale" and f.array_index == 0
        ),
        None,
    )
    check(
        "proxy scale.x carries the weight keys",
        pfc is not None
        and [kp.co[1] for kp in sorted(pfc.keyframe_points, key=lambda k: k.co[0])]
        == [1.0, 0.0],
        None if pfc is None else [tuple(kp.co) for kp in pfc.keyframe_points],
    )

    # Round-trip: the proxy's curve must survive a real FBX export→import —
    # the exact transport Unity's importer reads (m_LocalScale.x on the
    # proxy node). Empty-inclusive object_types + bake_anim mirror the scene
    # exporter's defaults (fbx_utils' bridge defaults pin mesh-only).
    fbx_path = os.path.join(tmp, "kw_proxy.fbx")
    FbxUtils.export_selection_fbx(
        filepath=fbx_path,
        objects=[kcube, DataNodes.get_export_node(create=False)] + proxies,
        object_types={"EMPTY", "MESH"},
        use_custom_props=True,
        bake_anim=True,
    )
    check("proxy FBX written", os.path.isfile(fbx_path))

    removed = EmissiveGroups.remove_export_curve_proxies()
    check("proxies removed after the write", removed == ["emissiveGroup_glow"], removed)
    check(
        "no proxy object survives in the scene",
        not any(o.get(EmissiveGroups.PROXY_MARKER) for o in bpy.data.objects),
    )

    imported = FbxUtils.import_fbx(fbx_path, use_custom_props=True)
    iproxy = next(
        (o for o in imported if o.name.startswith("emissiveGroup_glow")), None
    )
    check(
        "proxy node rides the FBX",
        iproxy is not None,
        [o.name for o in imported],
    )
    ifc = next(
        (
            f
            for f in AnimUtils.get_fcurves([iproxy] if iproxy else [])
            if f.data_path == "scale" and f.array_index == 0
        ),
        None,
    )
    check(
        "imported scale.x curve reproduces the weight animation",
        ifc is not None
        and abs(ifc.evaluate(1) - 1.0) < 0.01
        and abs(ifc.evaluate(10) - 0.0) < 0.01,
        None if ifc is None else (ifc.evaluate(1), ifc.evaluate(10)),
    )

    # Name-collision guard: an unrelated object squatting the attr name means
    # that group's animation is skipped (warned), never clobbered. Sweep the
    # imported transport artifacts first — the imported proxy still carries
    # the marker prop (user props ride the FBX), so the pre-clean would
    # otherwise delete it and hand its name to the blocker's .001 twin.
    EmissiveGroups.remove_export_curve_proxies()
    blocker = bpy.data.objects.new("emissiveGroup_glow", None)
    check("blocker owns the exact attr name", blocker.name == "emissiveGroup_glow")
    check(
        "proxy creation skips a squatted name",
        EmissiveGroups.create_export_curve_proxies() == [],
    )
    bpy.data.objects.remove(blocker)

    # Leftover sweep: a proxy outside an export is an interrupted run.
    EmissiveGroups.create_export_curve_proxies()
    check(
        "validate flags a leftover proxy",
        any("Leftover export curve proxy" in w for w in EmissiveGroups.validate()),
    )
    EmissiveGroups.remove_export_curve_proxies()

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = not any(line.startswith("FAIL") for line in lines)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
