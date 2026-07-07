"""blendertk ImageToPlane (btk.ImageToPlane + ImageToPlaneSlots) headless test.
Run: blender --background --factory-startup --python blendertk/test/test_image_to_plane.py

Writes a couple of real images to a temp dir, then exercises the engine (aspect-sized plane, image
wired into a Principled material, alpha detection, grouping, remove cleanup) and the slot routing
(affix resolution + queue→create over a stubbed list widget).
"""
import sys, os, tempfile, traceback

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
    import pythontk as ptk
    import blendertk as btk
    from blendertk.mat_utils.image_to_plane._image_to_plane import ImageToPlane
    from blendertk.mat_utils.image_to_plane.image_to_plane_slots import ImageToPlaneSlots

    tmp = tempfile.mkdtemp(prefix="itp_test_")

    def write_png(name, w, h, alpha=True):
        """Write a real RGBA/RGB image file and return its path."""
        path = os.path.join(tmp, name)
        img = bpy.data.images.new(name, width=w, height=h, alpha=alpha)
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)  # drop the authoring copy; engine reloads from disk
        return path

    def reset():
        bpy.ops.object.select_all(action="DESELECT")
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)

    wide = write_png("wide.png", 4, 2, alpha=True)   # aspect 2.0, RGBA
    tall = write_png("tall.png", 2, 4, alpha=True)    # aspect 0.5, RGBA

    # ---- engine: one plane per image, sized to aspect, with an image-wired material -----------
    reset()
    results = ImageToPlane.create([wide], plane_height=10.0)
    check("create returns one plane keyed by stem", set(results) == {"wide"}, str(list(results)))
    plane = results["wide"]
    xs = [v.co.x for v in plane.data.vertices]
    zs = [v.co.z for v in plane.data.vertices]
    width = round(max(xs) - min(xs), 4)
    height = round(max(zs) - min(zs), 4)
    check("plane height matches request", height == 10.0, f"h={height}")
    check("plane width = height × aspect (2.0)", width == 20.0, f"w={width}")
    check("plane has a material", len(plane.data.materials) == 1)
    mat = plane.data.materials[0]
    img_nodes = [n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE"]
    check("material has an image-texture node", len(img_nodes) == 1)
    # Compare by socket/node TYPE, not `is` — bpy hands back fresh Python wrappers per access, so
    # node identity (`is`) is unreliable.
    base_linked = any(
        l.from_node.type == "TEX_IMAGE" and l.to_socket.name == "Base Color"
        for l in mat.node_tree.links
    )
    alpha_linked = any(
        l.from_node.type == "TEX_IMAGE" and l.to_socket.name == "Alpha"
        for l in mat.node_tree.links
    )
    check("image color → Base Color", base_linked)
    check("RGBA image → Alpha linked", alpha_linked)
    check("material named with the _MAT suffix", mat.name.startswith("wide_MAT"), mat.name)

    # ---- aspect for a tall image (0.5) --------------------------------------------------------
    reset()
    plane = ImageToPlane.create([tall], plane_height=10.0)["tall"]
    xs = [v.co.x for v in plane.data.vertices]
    width = round(max(xs) - min(xs), 4)
    check("tall image → width = height × 0.5 = 5", width == 5.0, f"w={width}")

    # ---- prefix naming + grouping under an Empty ----------------------------------------------
    reset()
    results = ImageToPlane.create([wide, tall], prefix="MAT_", suffix="", group=True)
    check("two planes created", {"wide", "tall"} <= set(results))
    check("group Empty created", "__group__" in results and results["__group__"].type == "EMPTY")
    grp = results["__group__"]
    check("planes parented under the group", all(
        results[s].parent is grp for s in ("wide", "tall")
    ))
    check("prefix applied to material name",
          results["wide"].data.materials[0].name.startswith("MAT_wide"),
          results["wide"].data.materials[0].name)

    # ---- missing path is skipped, not fatal ---------------------------------------------------
    reset()
    results = ImageToPlane.create([os.path.join(tmp, "nope.png"), wide])
    check("missing image skipped, valid one still created", set(results) == {"wide"}, str(list(results)))

    # ---- remove: deletes planes + orphaned material/image -------------------------------------
    reset()
    plane = ImageToPlane.create([wide])["wide"]
    mat_name = plane.data.materials[0].name
    n = ImageToPlane.remove([plane])
    check("remove returns the count", n == 1, f"n={n}")
    check("plane object gone", "wide" not in bpy.data.objects)
    check("orphaned material cleaned up", mat_name not in bpy.data.materials)

    # ---- affix resolution: the slot delegates to pythontk's split_affix primitive -------------
    # (the Qt option-box wiring itself is covered by uitk's test_affix_option; here we assert the
    # library behaviour the slot relies on, so this stays runnable in the no-Qt Blender harness).
    check("affix '_MAT' → suffix", ptk.StrUtils.split_affix("_MAT", mode="auto", default="suffix") == ("", "_MAT"))
    check("affix 'MAT_' → prefix", ptk.StrUtils.split_affix("MAT_", mode="auto", default="suffix") == ("MAT_", ""))
    check("affix empty → no split", ptk.StrUtils.split_affix("", mode="auto", default="suffix") == ("", ""))

    # ---- slot _create_planes over a stubbed UI (queue → engine) -------------------------------
    class _List:
        def __init__(self, items):
            self._items = list(items)
        def count(self):
            return len(self._items)
        def item(self, i):
            return type("It", (), {"text": lambda self, p=self._items[i]: p})()
    class _Menu:
        def __init__(self):
            self.chk_group_result = type("C", (), {"isChecked": lambda self: False})()
    class _Header:
        def __init__(self):
            self.menu = _Menu()
    class _OptBox:
        """Minimal option-box stub mirroring the real ``resolve_affix`` delegation."""
        affix_mode = "auto"
        def __init__(self, field):
            self._field = field
        def resolve_affix(self, *, default="prefix"):
            return ptk.StrUtils.split_affix(self._field.text(), mode="auto", default=default)
    class _Field:
        def __init__(self, v):
            self._v = v
        def text(self):
            return self._v
        def value(self):
            return self._v
        def setText(self, t):
            self._v = t
        @property
        def option_box(self):
            return _OptBox(self)

    reset()
    ui = type("U", (), {})()
    ui.lst_files = _List([wide, tall])
    ui.txt_suffix = _Field("_MAT")
    ui.spn_height = _Field(4.0)
    ui.header = _Header()
    ui.footer = _Field("")
    ui.b000 = ui.b001 = ui.b002 = ui.b004 = type("B", (), {"clicked": type("S", (), {"connect": lambda self, fn: None})()})()
    sb = type("SB", (), {"loaded_ui": type("L", (), {"image_to_plane": ui})()})()
    slots = ImageToPlaneSlots(sb)
    slots._create_planes()
    check("slot create made both planes", "wide" in bpy.data.objects and "tall" in bpy.data.objects)
    check("slot footer reports", "Created 2 plane" in ui.footer.text(), ui.footer.text())

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
