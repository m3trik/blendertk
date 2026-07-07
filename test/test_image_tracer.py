"""blendertk ImageTracer + NurbsUtils headless test (dual-mode).

Two independent dependency seams, so the file degrades gracefully in either environment:

  * **bpy** present (Blender harness) → tests ``NurbsUtils.create_curve`` / ``add_spline`` /
    ``curve_to_mesh`` and ``ImageTracer.create_mesh`` / ``create_negative_space_mesh`` (2D-fill +
    holes) + ``ImageTracerSlots`` routing. Blender ships no cv2, so the contour checks self-skip.
  * **cv2** present (workspace ``.venv``) → tests the pure ``ImageTracer._contours_from_image``
    contour extraction on a synthetic image. The .venv has no bpy, so the curve/mesh checks skip.

Run (bpy path):  blender --background --factory-startup --python blendertk/test/test_image_tracer.py
Run (cv2 path):  .venv\\Scripts\\python.exe blendertk/test/test_image_tracer.py
"""
import sys, os, tempfile, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import bpy
except Exception:
    bpy = None
try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    from blendertk.nurbs_utils._nurbs_utils import NurbsUtils
    from blendertk.nurbs_utils.image_tracer import ImageTracer, ImageTracerSlots

    # ============================ cv2 path (.venv) ===========================================
    if cv2 is not None:
        # synthetic 100x100: white square [20..80] on black, with a black hole [40..60] inside it
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[20:80, 20:80] = 255
        img[40:60, 40:60] = 0
        tmp = os.path.join(tempfile.gettempdir(), "btk_tracer_test.png")
        cv2.imwrite(tmp, img)

        contours = ImageTracer._contours_from_image(tmp, scale=1.0, simplify=1.0)
        check("contours: outer + hole detected", len(contours) >= 2, f"n={len(contours)}")
        # the square outline simplifies to ~4 corners
        biggest = max(contours, key=len)
        check("contour simplifies a square to ~4 corners", 4 <= len(biggest) <= 6,
              f"pts={len(biggest)}")
        # image-Y flip → points are placed in +Y (origin top-left maps to +Y up)
        check("contour points are on the XY ground plane (z=0)",
              all(abs(p[2]) < 1e-9 for c in contours for p in c))
        check("scale applied (square spans ~60 units at scale=1)",
              58 <= (max(p[0] for p in biggest) - min(p[0] for p in biggest)) <= 62,
              str(round(max(p[0] for p in biggest) - min(p[0] for p in biggest), 2)))
        # simplify=0 keeps every contour point (more than the simplified version)
        dense = ImageTracer._contours_from_image(tmp, scale=1.0, simplify=0)
        check("simplify=0 keeps more points than simplify=1",
              max(len(c) for c in dense) >= max(len(c) for c in contours))
        # missing file raises
        raised = False
        try:
            ImageTracer._contours_from_image("/no/such/file.png")
        except FileNotFoundError:
            raised = True
        check("missing image raises FileNotFoundError", raised)
        try:
            os.remove(tmp)
        except OSError:
            pass
    else:
        check("cv2 contour path (skipped — no cv2 in this interpreter)", True)

    # ============================ bpy path (Blender) =========================================
    if bpy is not None:
        def reset():
            bpy.ops.object.select_all(action="DESELECT")
            for o in list(bpy.data.objects):
                bpy.data.objects.remove(o, do_unlink=True)

        # ---- NurbsUtils.create_curve / add_spline -------------------------------------------
        reset()
        square = [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)]
        cobj = NurbsUtils.create_curve(square, name="C", cyclic=True, kind="POLY")
        check("create_curve makes a curve object", cobj.type == "CURVE", cobj.type)
        check("create_curve: one spline, cyclic, right point count",
              len(cobj.data.splines) == 1 and cobj.data.splines[0].use_cyclic_u
              and len(cobj.data.splines[0].points) == 4)
        NurbsUtils.add_spline(cobj, [(1, 1, 0), (3, 1, 0), (3, 3, 0), (1, 3, 0)], cyclic=True)
        check("add_spline appends a second spline", len(cobj.data.splines) == 2)

        # ---- curve_to_mesh: a 2D-fill curve bakes to a filled mesh (with the inner hole) ----
        cobj.data.dimensions = "2D"
        cobj.data.fill_mode = "BOTH"
        mesh_obj = NurbsUtils.curve_to_mesh(cobj, name="filled")
        check("curve_to_mesh returns a mesh object",
              mesh_obj is not None and mesh_obj.type == "MESH", getattr(mesh_obj, "type", None))
        check("curve_to_mesh produced faces (2D fill)", len(mesh_obj.data.polygons) > 0,
              f"polys={len(mesh_obj.data.polygons)}")
        check("curve_to_mesh purged the source curve object", "C" not in bpy.data.objects)

        # ---- curve_to_mesh keep_curve=True leaves the source ---------------------------------
        reset()
        c2 = NurbsUtils.create_curve(square, name="K", cyclic=True)
        c2.data.bevel_depth = 0.2  # round bevel → tube mesh
        m2 = NurbsUtils.curve_to_mesh(c2, name="kept", keep_curve=True)
        check("curve_to_mesh keep_curve leaves the source curve", "K" in bpy.data.objects)
        check("beveled curve bakes to a tube mesh", len(m2.data.polygons) > 0)

        # ---- ImageTracer.create_mesh from an explicit curve (fill + nested hole) -------------
        reset()
        tracer = ImageTracer(image_path=None)
        outer = NurbsUtils.create_curve(
            [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)], name="contours", cyclic=True
        )
        NurbsUtils.add_spline(outer, [(3, 3, 0), (7, 3, 0), (7, 7, 0), (3, 7, 0)], cyclic=True)
        filled = tracer.create_mesh(curve=outer, name="traced")
        check("create_mesh fills the contour into a mesh",
              filled is not None and len(filled.data.polygons) > 0)
        # the inner spline punches a hole → the filled area is the frame, not the whole 10x10
        area = sum(p.area for p in filled.data.polygons)
        check("create_mesh punches the nested contour as a hole (area < full square)",
              area < 100.0, f"area={round(area, 2)}")

        # ---- create_negative_space_mesh: boundary rect with the contour as a hole -----------
        reset()
        tracer = ImageTracer(image_path=None)
        shape = NurbsUtils.create_curve(
            [(2, 2, 0), (8, 2, 0), (8, 8, 0), (2, 8, 0)], name="neg", cyclic=True
        )
        neg = tracer.create_negative_space_mesh(curve=shape, margin_scale=0.25, name="negspace")
        check("create_negative_space_mesh makes a mesh",
              neg is not None and len(neg.data.polygons) > 0)

        # ---- NurbsUtils.create_plane / duplicate_curve (project_on_plane primitives) --------
        reset()
        plane = NurbsUtils.create_plane(width=4.0, height=2.0, location=(1.0, 1.0, -1.0), name="P")
        check("create_plane makes a mesh object", plane is not None and plane.type == "MESH")
        check("create_plane sizes + positions the quad",
              len(plane.data.polygons) == 1 and tuple(plane.location) == (1.0, 1.0, -1.0))
        src = NurbsUtils.create_curve(square, name="Dup", cyclic=True)
        dup = NurbsUtils.duplicate_curve(src, name="Dup_copy")
        check("duplicate_curve makes a distinct curve object with its own data",
              dup is not None and dup.type == "CURVE" and dup.name != src.name
              and dup.data is not src.data)
        check("duplicate_curve links the copy into the source's collection",
              dup.name in bpy.context.collection.objects)

        # ---- project_on_plane: backing plane + Z-shifted curve duplicate --------------------
        reset()
        tracer = ImageTracer(image_path=None)
        shape2 = NurbsUtils.create_curve(
            [(0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)], name="proj_src", cyclic=True
        )
        projected = tracer.project_on_plane(curve=shape2, name="projected")
        check("project_on_plane returns a curve object", projected is not None
              and projected.type == "CURVE")
        check("project_on_plane duplicate is shifted below the source (Z projected to -1)",
              projected is not None and abs(projected.location.z - (-1.0)) < 1e-6)
        check("project_on_plane created a backing plane", "projection_plane" in bpy.data.objects)

        # ---- engine fails loudly with no curve + no image (the Slots layer guards this) ------
        reset()
        raised = False
        try:
            ImageTracer(image_path=None).create_mesh()
        except (FileNotFoundError, ImportError):
            raised = True
        check("create_mesh with nothing to trace raises (Slots guards it upstream)", raised)

        # ---- ImageTracerSlots routing (stub ui/sb; no cv2 in Blender -> graceful message) ----
        class _Field:
            def __init__(self, v):
                self.v = v
            def value(self):
                return self.v
            def text(self):
                return self.v
            def isChecked(self):
                return bool(self.v)

        class _SB:
            def __init__(self, ui):
                self.loaded_ui = type("L", (), {"image_tracer": ui})()
                self.messages = []
            def message_box(self, msg):
                self.messages.append(msg)

        ui = type("U", (), {})()
        ui.txt000 = _Field("")  # empty path
        ui.s000 = _Field(0.1)
        ui.s001 = _Field(1.0)
        ui.chk000 = _Field(False)  # Use Blue Pencil — always disabled/unchecked, see header_init
        sb = _SB(ui)
        slots = ImageTracerSlots(sb)
        slots.b002()
        check("slot b002 with empty path warns",
              any("Select an image" in m for m in sb.messages), str(sb.messages))

        ui.txt000 = _Field("C:/fake/x.png")  # path set, but Blender has no cv2
        sb.messages.clear()
        slots.b003()
        check("slot b003 without cv2 reports the missing dependency",
              any("cv2" in m for m in sb.messages), str(sb.messages))
    else:
        check("bpy curve/mesh/slot path (skipped — no bpy in this interpreter)", True)

except Exception:
    traceback.print_exc()
    lines.append("FAIL unhandled exception")

print("\n".join(lines))
ok = all(l.startswith("OK") for l in lines) and lines
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({sum(1 for l in lines if l.startswith('OK'))}/{len(lines)})")
