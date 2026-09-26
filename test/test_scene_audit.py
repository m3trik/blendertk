"""blendertk SceneAnalyzer test -- Get Scene Info's report in Blender (mirror of mayatk's
``core_utils.diagnostics.scene_audit``).

Meshes are measured as they ship: evaluated (modifiers applied), with every instance the
depsgraph expands (linked duplicates, collection, geometry-node and particle instances)
counted as rendered and each evaluated mesh once as unique; a mesh with no faces is not
measured; a selected root brings everything under it; judged per unique mesh on mayatk's
budget model. Image sizes come from the file HEADER; images are keyed by FILE; a packed
image is never "missing"; an image node that reaches nothing is not audited; names link
to their object / material. Sections are ``SceneInfoSection``'s, and the collection skips
what no requested section shows.

Run: blender --background --factory-startup --python blendertk/test/test_scene_audit.py
"""

import importlib.util
import os
import re
import struct
import sys
import traceback
import warnings

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


def write_png_header(path, width, height):
    """A PNG signature + IHDR and nothing else: a header read gets the size, a decode
    gets nothing -- so a size that comes back proves the header path."""
    with open(path, "wb") as fh:
        fh.write(
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
            + b"\0\0\0\0"
        )
    return path


store = None
try:
    import bpy
    import pythontk as ptk
    import blendertk as btk
    from blendertk.core_utils._core_utils import _CoreUtilsInternal
    from blendertk.core_utils.diagnostics import scene_audit
    from blendertk.core_utils.diagnostics.scene_audit import (
        SceneAnalyzer,
        SceneInfoSection,
    )

    store = ptk.TempArtifacts("blendertk_test_scene_audit", policy="scoped")
    tmp = store.dir_path()

    def reset():
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for coll in (
            bpy.data.meshes,
            bpy.data.materials,
            bpy.data.images,
            bpy.data.node_groups,
            bpy.data.particles,
        ):
            for block in list(coll):
                coll.remove(block)
        for coll in list(bpy.data.collections):
            bpy.data.collections.remove(coll)

    def principled(mat):
        return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    def image_node(mat, image, socket=None):
        tree = mat.node_tree
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        if socket:
            tree.links.new(node.outputs["Color"], principled(mat).inputs[socket])
        return node

    def new_material(name):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        return mat

    def row_of(text, first_cell):
        """The cells of the text-table row whose first cell starts with *first_cell*."""
        row = next((ln for ln in text.splitlines() if ln.startswith(first_cell)), "")
        return [c.strip() for c in row.split("|")]

    def scatter(plane, instance_object):
        """Geometry nodes on *plane*: Instance on Points of *instance_object*'s geometry
        (Object Info, not as an instance) on the plane's vertices -- an object whose own
        output is ONLY instances."""
        tree = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
        tree.interface.new_socket(
            "Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
        )
        tree.interface.new_socket(
            "Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        group_in = tree.nodes.new("NodeGroupInput")
        group_out = tree.nodes.new("NodeGroupOutput")
        to_points = tree.nodes.new("GeometryNodeMeshToPoints")
        on_points = tree.nodes.new("GeometryNodeInstanceOnPoints")
        info = tree.nodes.new("GeometryNodeObjectInfo")
        info.inputs["Object"].default_value = instance_object
        tree.links.new(group_in.outputs[0], to_points.inputs["Mesh"])
        tree.links.new(to_points.outputs["Points"], on_points.inputs["Points"])
        tree.links.new(info.outputs["Geometry"], on_points.inputs["Instance"])
        tree.links.new(on_points.outputs["Instances"], group_out.inputs[0])
        plane.modifiers.new("Scatter", "NODES").node_group = tree

    class counting:
        """Count the calls a ``SceneAnalyzer`` helper takes inside the block."""

        def __init__(self, *names):
            self.names, self.calls, self._saved = names, dict.fromkeys(names, 0), {}

        def __enter__(self):
            for name in self.names:
                original = SceneAnalyzer.__dict__[name]  # classmethod / staticmethod
                self._saved[name] = original

                def wrapper(*args, _name=name, _func=original.__func__, **kwargs):
                    self.calls[_name] += 1
                    return _func(*args, **kwargs)

                setattr(SceneAnalyzer, name, type(original)(wrapper))
            return self.calls

        def __exit__(self, *exc):
            for name, original in self._saved.items():
                setattr(SceneAnalyzer, name, original)

    check(
        "btk.SceneAnalyzer / btk.SceneInfoSection are the diagnostics classes",
        btk.SceneAnalyzer is SceneAnalyzer and btk.SceneInfoSection is SceneInfoSection,
    )
    # Every public module-level class joins btk.Diagnostics (the "->Diagnostics"
    # alias): an imported helper (ReportDoc, typing.Any) must not be one.
    public = [
        name
        for name in dir(scene_audit)
        if not name.startswith("_") and isinstance(getattr(scene_audit, name), type)
    ]
    check(
        "the module's public classes are its own two",
        public == ["SceneAnalyzer", "SceneInfoSection"],
        f"{public}",
    )

    # ---- the budget model is mayatk's (a mirrored copy, guarded against drift) ----
    records_path = os.path.join(
        MONO, "mayatk", "mayatk", "core_utils", "diagnostics", "audit_records.py"
    )
    audit_path = os.path.join(os.path.dirname(records_path), "scene_audit.py")
    if not os.path.isfile(records_path):
        # Neither OK nor FAIL: a guard that cannot run must not read as a pass (the
        # runner tallies OK/FAIL lines; a SKIP line fails this suite's sentinel).
        lines.append(
            "SKIP mayatk drift guard: sibling checkout absent, budgets and sections "
            f"NOT compared | {records_path}"
        )
    else:
        spec = importlib.util.spec_from_file_location(
            "_mtk_audit_records", records_path
        )
        records = importlib.util.module_from_spec(spec)
        # Registered first: its dataclasses resolve their (postponed) annotations
        # through sys.modules[cls.__module__].
        sys.modules[spec.name] = records
        spec.loader.exec_module(records)
        profile = records.AuditProfile()
        with open(audit_path, encoding="utf-8") as fh:
            texels = int(re.search(r"TEXELS_PER_METER = (\d+)", fh.read()).group(1))
        mirrored = {
            "MAX_TRIS": profile.max_tris,
            "MIN_TRIS": profile.min_tris,
            "REFERENCE_DIAG_CM": profile.reference_diag,
            "MAX_SLOTS": profile.max_slots,
            "MAX_UVS": profile.max_uvs,
            "TEXTURE_BUDGET_MB": profile.max_texture_mb,
            "TEXELS_PER_METER": texels,
        }
        drift = {
            k: (getattr(SceneAnalyzer, k), v)
            for k, v in mirrored.items()
            if getattr(SceneAnalyzer, k) != v
        }
        check("budget constants mirror mtk.AuditProfile", not drift, f"{drift}")
        mtk_sections = records.SceneInfoSection
        check(
            "section keys, labels and retired key are mtk.SceneInfoSection's",
            dict(mtk_sections.LABELS) == SceneInfoSection.LABELS
            and tuple(mtk_sections.ALL) == SceneInfoSection.ALL
            and mtk_sections.CATEGORIES == SceneInfoSection.CATEGORIES,
        )
        # Pareto needs no material walk here: its slot counts come with the meshes.
        check(
            "section gating is mtk.SceneInfoSection's (pareto aside)",
            SceneInfoSection._NEEDS_TEXTURES == mtk_sections._NEEDS_TEXTURES
            and SceneInfoSection._NEEDS_MATERIALS
            == mtk_sections._NEEDS_MATERIALS - {mtk_sections.PARETO},
            f"{sorted(SceneInfoSection._NEEDS_MATERIALS)}",
        )

    # ---- linked duplicates: rendered vs unique ---------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    twin = cube.copy()  # a linked duplicate: shares cube.data
    bpy.context.collection.objects.link(twin)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, location=(4, 0, 0))
    ico = bpy.context.active_object
    ico_tris = _CoreUtilsInternal._mesh_face_counts(ico.data)[0]

    rep = SceneAnalyzer.format_audit_text(
        scope="all", sections=["overview", "summary", "pareto"]
    )
    check(
        "3 instances of 2 unique meshes (1 instanced)",
        "3 instances of 2 unique meshes (1 instanced)" in rep["summary"],
        rep["summary"],
    )
    geometry = next(
        (ln for ln in rep["overview"].splitlines() if ln.startswith("Geometry")), ""
    )
    check(
        "the overview's Geometry row counts objects over datablocks",
        geometry.split(":", 1)[-1].strip() == "3 mesh objects of 2 mesh datablocks",
        rep["overview"],
    )
    check(
        "rendered counts every instance, unique each mesh once",
        f"{2 * 12 + ico_tris:,} rendered · {12 + ico_tris:,} unique" in rep["summary"],
        rep["summary"],
    )
    check("pareto groups the linked duplicates", "×2" in rep["pareto"], rep["pareto"])

    # Selection scope reads the view layer, not the (window-less) screen context.
    for obj in bpy.data.objects:
        obj.select_set(obj is ico)
    rep = SceneAnalyzer.format_audit_text(sections=["summary"])
    check(
        "selection scope audits only the selection",
        "1 instance of 1 unique mesh" in rep["summary"],
        rep["summary"],
    )

    rep = SceneAnalyzer.format_audit_text(objects=[ico.name], sections=["summary"])
    check(
        "objects may be given by name (as mayatk takes names)",
        "1 instance of 1 unique mesh" in rep["summary"],
        rep["summary"],
    )

    # Hidden objects render nothing, and the Scene Exporter ships only what is visible.
    ico.hide_set(True)
    rep = SceneAnalyzer.format_audit_text(scope="all", sections=["summary"])
    check(
        "a hidden object is not measured",
        "2 instances of 1 unique mesh (1 instanced)" in rep["summary"],
        rep["summary"],
    )

    # Judged per unique mesh: three instances over budget are ONE mesh to decimate,
    # with every instance's excess counted (Generic: a flat MAX_TRIS each).
    over = SceneAnalyzer.MAX_TRIS
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128)
    dense = bpy.context.active_object
    for i in range(2):
        dup = dense.copy()
        bpy.context.collection.objects.link(dup)
    dense_tris = _CoreUtilsInternal._mesh_face_counts(dense.data)[0]
    # HTML: the text table wraps the message's prose column across lines.
    rep = SceneAnalyzer.format_audit_html(scope="all", sections=["fix_first"])
    check(
        "linked duplicates are judged as one mesh, excess counted per instance",
        "Triangle budget exceeded on 1 mesh: "
        f"{3 * (dense_tris - over):,} rendered triangles" in rep["fix_first"],
        rep["fix_first"],
    )

    # ---- what ships: modifiers applied, collection instances counted ------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    arrayed = bpy.context.active_object
    modifier = arrayed.modifiers.new("Array", "ARRAY")
    modifier.count = 3
    rep = SceneAnalyzer.format_audit_text(objects=[arrayed], sections=["summary"])
    check(
        "triangles are the evaluated mesh's (Array x3 = 36)",
        "36 rendered · 36 unique" in rep["summary"],
        rep["summary"],
    )

    reset()
    source_coll = bpy.data.collections.new("Props")
    bpy.context.scene.collection.children.link(source_coll)
    bpy.ops.mesh.primitive_cube_add()
    prop = bpy.context.active_object
    for coll in list(prop.users_collection):
        coll.objects.unlink(prop)
    source_coll.objects.link(prop)
    layer_coll = bpy.context.view_layer.layer_collection.children["Props"]
    layer_coll.exclude = True  # the source renders only through its instances
    empties = []
    for i in range(2):
        empty = bpy.data.objects.new(f"PropInst{i}", None)
        empty.instance_type = "COLLECTION"
        empty.instance_collection = source_coll
        empty.location = (i * 3.0, 0.0, 0.0)
        bpy.context.scene.collection.objects.link(empty)
        empties.append(empty)
    rep = SceneAnalyzer.format_audit_text(scope="all", sections=["summary"])
    check(
        "collection instances are rendered instances of one mesh",
        "2 instances of 1 unique mesh (1 instanced)" in rep["summary"]
        and "24 rendered · 12 unique" in rep["summary"],
        rep["summary"],
    )
    rep = SceneAnalyzer.format_audit_text(objects=[empties[0]], sections=["summary"])
    check(
        "an instancer in scope brings its instances",
        "1 instance of 1 unique mesh" in rep["summary"],
        rep["summary"],
    )
    html = SceneAnalyzer.format_audit_html(objects=[empties[0]], sections=["pareto"])
    check(
        "an instance links to the object that generates it",
        "action://select?node=PropInst0" in html["pareto"],
        html["pareto"],
    )

    # ---- a mesh with no faces renders nothing, so it is not measured --------------
    # It used no slot, so it read as "No material assigned" (medium) whatever its slots
    # held, with a draw call and a unique mesh of its own.
    reset()
    worn = new_material("Worn")
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.data.materials.append(worn)
    points = bpy.data.meshes.new("points")
    points.from_pydata([(0, 0, 0), (3, 0, 0), (6, 0, 0)], [], [])
    points.materials.append(worn)
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Points", points))
    rep = SceneAnalyzer.format_audit_text(
        scope="all", sections=["summary", "fix_first", "pipeline"]
    )
    check(
        "a vertex-only mesh is not measured: no instance, no draw call",
        "1 instance of 1 unique mesh" in rep["summary"]
        and "~1 (one per material slot" in rep["summary"],
        rep["summary"],
    )
    check(
        "a vertex-only mesh wearing a material is not 'without a material'",
        "No material" not in rep["fix_first"]
        and "without a material" not in rep["pipeline"],
        f"{rep['fix_first']} | {rep['pipeline']}",
    )

    # ---- geometry-node instances: rendered, and owned by the object that makes them --
    reset()
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 5))
    gn_source = bpy.context.active_object
    gn_source.data.name = "PebbleMesh"
    gn_source.data.materials.append(new_material("Pebble"))
    bpy.ops.mesh.primitive_plane_add(size=4)  # four vertices -> four points
    field = bpy.context.active_object
    field.name = "Field"
    scatter(field, gn_source)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    gn_originals = {
        inst.object.original.name
        for inst in depsgraph.object_instances
        if inst.is_instance and inst.parent and inst.parent.original.name == "Field"
    }
    check(
        "(premise) a geometry instance's object resolves to the instancer itself",
        gn_originals == {"Field"},
        f"{gn_originals}",
    )
    rep = SceneAnalyzer.format_audit_text(
        objects=[field], sections=["summary", "pareto", "fix_first"]
    )
    check(
        "geometry-node instances are rendered: 4 points x 12 tris, one unique mesh",
        "4 instances of 1 unique mesh (1 instanced)" in rep["summary"]
        and "48 rendered · 12 unique" in rep["summary"],
        rep["summary"],
    )
    check(
        "an instancer whose output is only instances is not 'without a material'",
        "No material" not in rep["fix_first"],
        rep["fix_first"],
    )
    html = SceneAnalyzer.format_audit_html(objects=[field], sections=["pareto"])
    check(
        "a geometry instance names its mesh and links to its instancer",
        "PebbleMesh (Field)" in rep["pareto"]
        and "Field (Field)" not in rep["pareto"]
        and "action://select?node=Field" in html["pareto"],
        rep["pareto"],
    )

    # ---- particle instances ----------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 5))
    grain = bpy.context.active_object
    grain.name = "Grain"
    bpy.ops.mesh.primitive_plane_add(size=4)
    emitter = bpy.context.active_object
    emitter.name = "Emitter"
    emitter.modifiers.new("Grains", "PARTICLE_SYSTEM")
    settings = emitter.particle_systems[0].settings
    settings.count = 5
    settings.frame_start = settings.frame_end = 1
    settings.physics_type = "NO"
    settings.render_type = "OBJECT"
    settings.instance_object = grain
    bpy.context.scene.frame_set(1)
    txt = SceneAnalyzer.format_audit_text(
        objects=[emitter], sections=["summary", "pareto"]
    )
    html = SceneAnalyzer.format_audit_html(objects=[emitter], sections=["pareto"])
    check(
        "particle instances are rendered instances of their object, owned by the "
        "emitter (5 x 12 tris + the 2-tri emitter)",
        "6 instances of 2 unique meshes (1 instanced)" in txt["summary"]
        and "62 rendered · 14 unique" in txt["summary"]
        and "Grain (Emitter)" in txt["pareto"]
        and "action://select?node=Emitter" in html["pareto"],
        f"{txt['summary']} | {txt['pareto']}",
    )

    # ---- a selected root brings everything under it (mayatk's group resolution) -----
    reset()
    root = bpy.data.objects.new("AssetRoot", None)  # an imported FBX's root Empty
    bpy.context.scene.collection.objects.link(root)
    children = []
    for i in range(2):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3.0, 0.0, 0.0))
        children.append(bpy.context.active_object)
        children[-1].parent = root
    rep = SceneAnalyzer.format_audit_text(objects=[root], sections=["summary"])
    check(
        "an Empty root audits its mesh children",
        "2 instances of 2 unique meshes" in rep["summary"],
        rep["summary"],
    )
    rep = SceneAnalyzer.format_audit_text(
        objects=[root, children[0], root.name], sections=["summary"]
    )
    check(
        "a root and its child named together count the child once",
        "2 instances of 2 unique meshes" in rep["summary"],
        rep["summary"],
    )
    for obj in bpy.data.objects:
        obj.select_set(obj == root)
    rep = SceneAnalyzer.format_audit_text(sections=["summary"])
    check(
        "a selected root brings its children into the selection scope",
        "2 instances of 2 unique meshes" in rep["summary"],
        rep["summary"],
    )
    children[1].parent = children[0]  # a parent MESH with a child mesh
    rep = SceneAnalyzer.format_audit_text(objects=[children[0]], sections=["summary"])
    check(
        "a selected parent mesh brings its child meshes",
        "2 instances of 2 unique meshes" in rep["summary"],
        rep["summary"],
    )

    # ---- textures ----------------------------------------------------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    mat = new_material("CrateMat")
    obj.data.materials.append(mat)

    color_path = write_png_header(os.path.join(tmp, "crate_Base_Color.png"), 4096, 2048)
    image_node(mat, bpy.data.images.load(color_path), "Base Color")
    gone = bpy.data.images.new("gone", 8, 8)
    gone.source = "FILE"
    gone.filepath = os.path.join(tmp, "gone_Roughness.png")  # never written
    image_node(mat, gone, "Roughness")
    # Read by the material, but no surface input: listed, not counted.
    lut_path = write_png_header(os.path.join(tmp, "ibl_brdf_lut.png"), 128, 128)
    lut = image_node(mat, bpy.data.images.load(lut_path))
    output = next(n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
    mat.node_tree.links.new(lut.outputs["Color"], output.inputs["Displacement"])
    # Wired to nothing at all: never read, so neither listed nor missing.
    stray = bpy.data.images.new("stray", 8, 8)
    stray.source = "FILE"
    stray.filepath = os.path.join(tmp, "stray_Normal.png")  # never written
    image_node(mat, stray)
    # A FILE image packed into the .blend, whose file is then deleted from disk.
    baked_path = os.path.join(tmp, "baked_Metallic.png")
    source = bpy.data.images.new("baked_src", 16, 16)
    source.filepath_raw = baked_path
    source.file_format = "PNG"
    source.save()
    packed = bpy.data.images.load(baked_path)
    packed.pack()
    os.remove(baked_path)
    image_node(mat, packed, "Metallic")

    sections = ["materials", "textures", "pipeline"]
    txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=sections)
    html = SceneAnalyzer.format_audit_html(objects=[obj], sections=sections)
    check(
        "size read from the header (Blender cannot decode the file)",
        "4096×2048" in txt["textures"],
        txt["textures"],
    )
    gpu = 4096 * 2048 * 0.5 * 4 / 3 / 2**20  # BC1 with mips
    check(
        "GPU estimate is the shared block-compressed one",
        f"{gpu:,.1f} MB" in txt["textures"],
        txt["textures"],
    )
    check(
        "the missing map is reported",
        "gone_Roughness.png" in txt["pipeline"],
        txt["pipeline"],
    )
    check(
        "a packed image is never missing (its file is gone from disk)",
        packed.source == "FILE"
        and packed.packed_file is not None
        and not os.path.exists(baked_path)
        and "baked_Metallic" not in txt["pipeline"]
        and "(packed)" in txt["textures"],
        f"source={packed.source} | {txt['textures']}",
    )
    check(
        "a non-surface image is listed, not counted",
        "Not counted" in txt["textures"] and "ibl_brdf_lut" in txt["textures"],
        txt["textures"],
    )
    check(
        "an image node wired to nothing is not audited (nor its missing file)",
        "stray" not in txt["textures"] and "stray_Normal" not in txt["pipeline"],
        f"{txt['textures']} | {txt['pipeline']}",
    )
    check(
        "material names open the Shader Editor",
        "action://graph?node=CrateMat" in html["materials"],
    )
    check(
        "html is newline-free (the viewer turns newlines into <br>)",
        all("\n" not in v for v in html.values()),
    )
    obj.data.materials.append(mat)  # the same material in a second slot
    txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["materials"])
    cells = row_of(txt["materials"], "CrateMat")
    check(
        "a material in two slots of one object counts that object once",
        len(cells) > 3 and cells[3] == "1",
        f"{cells}",
    )

    # A non-surface image whose file is gone is still a missing file.
    os.remove(lut_path)
    lut.image.reload()
    txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["pipeline"])
    check(
        "a missing file is reported whatever its role",
        "ibl_brdf_lut.png" in txt["pipeline"],
        txt["pipeline"],
    )

    # One file loaded as two datablocks is one texture, costed once.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    same = write_png_header(os.path.join(tmp, "same_Base_Color.png"), 1024, 1024)
    mat_a, mat_b = new_material("SameA"), new_material("SameB")
    image_node(mat_a, bpy.data.images.load(same), "Base Color")
    image_node(mat_b, bpy.data.images.load(same, check_existing=False), "Base Color")
    obj.data.materials.append(mat_a)
    obj.data.materials.append(mat_b)
    obj.data.polygons[0].material_index = 1
    txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["textures"])
    check(
        "a file loaded twice is one texture",
        len(bpy.data.images) == 2 and txt["textures"].count("same_Base_Color") == 1,
        txt["textures"],
    )

    # A tokenless map through a Bump node is height data, not a normal map.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    mat = new_material("BumpMat")
    obj.data.materials.append(mat)
    height = image_node(
        mat,
        bpy.data.images.load(write_png_header(os.path.join(tmp, "plank.png"), 64, 64)),
    )
    bump = mat.node_tree.nodes.new("ShaderNodeBump")
    mat.node_tree.links.new(height.outputs["Color"], bump.inputs["Height"])
    mat.node_tree.links.new(bump.outputs["Normal"], principled(mat).inputs["Normal"])
    txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["textures"])
    check(
        "a map through a Bump node is typed Bump",
        row_of(txt["textures"], "plank.png")[1:2] == ["Bump"],
        txt["textures"],
    )

    # A UDIM set in a folder whose name holds glob syntax still finds its tiles.
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    folder = os.path.join(tmp, "tex [v2]")
    os.makedirs(folder, exist_ok=True)
    for tile in (1001, 1002):
        write_png_header(os.path.join(folder, f"rock_Base_Color.{tile}.png"), 256, 256)
    tiled = bpy.data.images.load(os.path.join(folder, "rock_Base_Color.1001.png"))
    tiled.source = "TILED"
    tiled.filepath = os.path.join(folder, "rock_Base_Color.<UDIM>.png")
    mat = new_material("TileMat")
    obj.data.materials.append(mat)
    image_node(mat, tiled, "Base Color")
    txt = SceneAnalyzer.format_audit_text(
        objects=[obj], sections=["textures", "pipeline"]
    )
    check(
        "a UDIM set under a [bracketed] folder resolves every tile",
        "×2" in txt["textures"] and "rock_Base_Color" not in txt["pipeline"],
        f"{txt['textures']} | {txt['pipeline']}",
    )

    # ---- slots: only slots with faces draw; mayatk's slot budget applies --------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    for i in range(6):
        obj.data.materials.append(new_material(f"Slot{i}"))
    txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["summary"])
    check(
        "unused slots add no draw calls",
        "~1 (one per material slot" in txt["summary"],
        txt["summary"],
    )
    for i, polygon in enumerate(obj.data.polygons):
        polygon.material_index = i
    txt = SceneAnalyzer.format_audit_text(
        objects=[obj], sections=["summary", "fix_first", "pareto"]
    )
    check(
        "six used slots are six draw calls, over the four-slot budget",
        "~6 (one per material slot" in txt["summary"]
        and "1 over 4 material slots" in txt["summary"]
        and "More than 4 material slots on 1 mesh" in txt["fix_first"]
        and "Multi-material meshes by draw calls" in txt["pareto"],
        f"{txt['summary']} | {txt['fix_first']}",
    )

    # ---- transparency: the 4.2+ render method is the truth ----------------------
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    mat = new_material("GlassMat")
    obj.data.materials.append(mat)
    principled(mat).inputs["Alpha"].default_value = 0.5
    if hasattr(mat, "surface_render_method"):
        # A pre-4.2 Blend material: the legacy setter syncs the render method...
        mat.blend_method = "BLEND"
        # ...but switching the render method never writes back: blend_method is stale.
        mat.surface_render_method = "DITHERED"
        txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["materials"])
        check(
            "surface_render_method decides, not a stale blend_method",
            "Alpha-tested" in txt["materials"]
            and "Alpha-blended" not in txt["materials"],
            txt["materials"],
        )
        mat.surface_render_method = "BLENDED"
        txt = SceneAnalyzer.format_audit_text(objects=[obj], sections=["summary"])
        check(
            "one blended mesh reads as English",
            "1 mesh alpha-blended" in txt["summary"],
            txt["summary"],
        )

    # ---- oversized unique texture set: unique across the SCENE ------------------
    reset()
    bpy.ops.mesh.primitive_cube_add(size=0.2)
    small = bpy.context.active_object
    big = write_png_header(os.path.join(tmp, "small_Base_Color.png"), 4096, 4096)
    mat = new_material("SmallMat")
    small.data.materials.append(mat)
    image_node(mat, bpy.data.images.load(big), "Base Color")
    rep = SceneAnalyzer.format_audit_text(objects=[small], sections=["offenders"])
    check(
        "a unique 4K set on a 20 cm cube is oversized",
        "Unique 4096px texture set" in rep["offenders"],
        rep["offenders"],
    )
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(3, 0, 0))
    other = bpy.context.active_object
    other.data.materials.append(mat)  # worn by a second mesh, out of scope
    rep = SceneAnalyzer.format_audit_text(objects=[small], sections=["offenders"])
    check(
        "a set another mesh wears -- in scope or not -- is not unique",
        "Unique 4096px" not in rep["offenders"],
        rep["offenders"],
    )
    # A trim sheet: ANOTHER material reads the same file, on a mesh out of scope.
    reset()
    bpy.ops.mesh.primitive_cube_add(size=0.2)
    small = bpy.context.active_object
    trim_a, trim_b = new_material("TrimA"), new_material("TrimB")
    small.data.materials.append(trim_a)
    image_node(trim_a, bpy.data.images.load(big), "Base Color")
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(3, 0, 0))
    bpy.context.active_object.data.materials.append(trim_b)
    image_node(trim_b, bpy.data.images.load(big), "Base Color")
    rep = SceneAnalyzer.format_audit_text(objects=[small], sections=["offenders"])
    check(
        "a file another material reads on another mesh is not unique",
        "Unique 4096px" not in rep["offenders"],
        rep["offenders"],
    )

    # ---- sections ----------------------------------------------------------------
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rep = SceneAnalyzer.format_audit_html(scope="all", sections=["categories"])
    check(
        "retired 'categories' resolves to 'materials', warning",
        list(rep) == ["_header", "materials"]
        and any(issubclass(w.category, DeprecationWarning) for w in caught),
        f"{list(rep)}",
    )
    check(
        "sections keep the caller's order (as mayatk's normalize)",
        SceneInfoSection.normalize(["pipeline", "summary", "pipeline", "nope"])
        == ["pipeline", "summary"]
        and SceneInfoSection.normalize(None) == list(SceneInfoSection.ALL),
    )
    rep = btk.analyze_scene()
    check(
        "analyze_scene renders every section, header first",
        list(rep) == ["_header"] + list(SceneInfoSection.ALL),
        f"{list(rep)}",
    )

    # The collection skips what no requested section shows (mayatk's gating); the
    # trim-sheet scene above: two materials, each reading a 4K file.
    helpers = ("_material_entry", "_image_info", "_texture_totals")
    with counting(*helpers) as calls:
        SceneAnalyzer.format_audit_text(
            objects=[small], sections=["overview", "pareto", "assumptions"]
        )
    check(
        "overview / pareto / assumptions walk no material and read no image",
        calls["_material_entry"] == 0 and calls["_image_info"] == 0,
        f"{calls}",
    )
    with counting(*helpers) as calls:
        rep = SceneAnalyzer.format_audit_text(
            objects=[small], sections=["summary", "fix_first", "textures", "pipeline"]
        )
    check(
        "texture totals are computed once per report, however many sections read them",
        calls["_texture_totals"] == 1 and calls["_image_info"] > 0,
        f"{calls}",
    )
    check(
        "a gated report still reads the textures it shows",
        "4096×4096" in rep["textures"],
        rep["textures"],
    )

    ticks = []
    SceneAnalyzer.format_audit_html(
        objects=[small],
        progress_callback=lambda *args: ticks.append(args),
        sections=["summary"],
    )
    percents = [t[0] for t in ticks]
    check(
        "progress ticks (percent, 100, message) from 0 to 100, never backwards",
        ticks
        and percents[0] == 0
        and ticks[-1] == (100, 100, "Done")
        and all(t[1] == 100 and isinstance(t[2], str) for t in ticks)
        and percents == sorted(percents),
        f"{ticks}",
    )

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())
finally:
    if store is not None:
        store.cleanup()

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
