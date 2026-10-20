"""blendertk.mat_utils.mat_manifest headless test -- restore's opacity wiring.
Run: blender --background --factory-startup --python blendertk/test/test_mat_manifest.py

Mirror of mayatk's ``test_mat_manifest`` opacity contract: opacity is the image's
ALPHA where it has one (never colour-managed, so it never retags an image another
slot reads for colour), and an image with no alpha drives opacity off its value as
data -- in either key order.
"""

import os
import struct
import sys
import traceback
import zlib

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


def png(path, alpha):
    """A real 8x8 PNG -- RGBA when *alpha*, else RGB (Blender reads its depth)."""

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    px = bytes((40, 200, 40)) + (b"\x80" if alpha else b"")
    raw = (b"\x00" + px * 8) * 8
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6 if alpha else 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )
    return path


try:
    import bpy
    import pythontk as ptk
    from blendertk.mat_utils._mat_utils import MatUtils
    from blendertk.mat_utils.mat_manifest import MatManifest

    def restore(name, slots):
        """A fresh material restored from *slots*; returns (material, count)."""
        for img in list(bpy.data.images):
            bpy.data.images.remove(img)
        mat = bpy.data.materials.new(name)
        MatUtils.get_node_tree(mat, create=True)
        count = MatManifest.restore(mat.name, {"materials": {mat.name: slots}})
        return mat, count

    def alpha_source(mat):
        """``(image name, output socket)`` linked into the Principled Alpha."""
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        link = next(
            (
                lk
                for lk in mat.node_tree.links
                if lk.to_node == bsdf and lk.to_socket.name == "Alpha"
            ),
            None,
        )
        if link is None:
            return None
        return (link.from_node.image.name, link.from_socket.name)

    with ptk.TempArtifacts.scoped("btk_mat_manifest") as tmp:
        work = tmp.dir_path()
        rgba = png(os.path.join(work, "cutout.png"), alpha=True)
        rgb = png(os.path.join(work, "opacity.png"), alpha=False)

        # A cutout: ONE image is the base colour and, through its alpha, the
        # opacity. Restored opacity-last the image read as Non-Color, so the
        # base colour was gamma-wrong; opacity-first it came out right.
        for order in (("baseColor", "opacity"), ("opacity", "baseColor")):
            mat, count = restore(f"cut_{order[0]}", {k: rgba for k in order})
            image = bpy.data.images["cutout.png"]
            check(
                f"cutout {order}: both slots restored",
                count == 2,
                count,
            )
            check(
                f"cutout {order}: opacity rides the image ALPHA",
                alpha_source(mat) == ("cutout.png", "Alpha"),
                alpha_source(mat),
            )
            check(
                f"cutout {order}: the shared image stays sRGB for its colour",
                image.colorspace_settings.name == "sRGB",
                image.colorspace_settings.name,
            )

        # No alpha channel: Blender's Alpha output is a constant 1, so wired
        # there the map connects and does nothing (mayatk enables
        # alphaIsLuminance for the same reason). Its value is the opacity.
        mat, count = restore("gray_opacity", {"opacity": rgb})
        image = bpy.data.images["opacity.png"]
        check(
            "alpha-less opacity map drives Alpha off its value",
            count == 1 and alpha_source(mat) == ("opacity.png", "Color"),
            alpha_source(mat),
        )
        check(
            "alpha-less opacity map reads as data (Non-Color)",
            image.colorspace_settings.name == "Non-Color",
            image.colorspace_settings.name,
        )

except Exception as e:
    traceback.print_exc()
    check("test harness raised", False, repr(e))

passed = sum(1 for ln in lines if ln.startswith("OK"))
for ln in lines:
    print(ln)
result = "PASS" if all(ln.startswith("OK") for ln in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
