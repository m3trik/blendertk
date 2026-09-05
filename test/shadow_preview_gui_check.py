# !/usr/bin/python
# coding=utf-8
"""The horizon preview overlay, drawn, against the reference.

**GUI-only** -- ``--background`` has no GPU backend, so the overlay's shader
can neither compile nor draw in the headless runner; ``test_shadow_preview.py``
covers everything around it. This proves the part that needs a real draw
loop: the shader compiles through ``gpu.shader.create_from_info``, the overlay
draws, and its pixels ARE ``HorizonMap.alpha`` decoding the PNG the rig baked.
The non-``test_`` name keeps it out of the headless runner.

A table-shaped horizon rig is built, the preview attached, a top-down
orthographic camera renders the viewport (``render.opengl`` from the 3D view,
overlays included, over a white world) and every pixel inside the plane is
compared against the reference in the contact's LOCAL frame -- which is what
also settles ``SH_ROWS_FROM_BOTTOM`` for Blender in one measurement.

Run against a *fresh* Blender (never an existing session)::

    blender --factory-startup --python blendertk/test/shadow_preview_gui_check.py

Writes ``temp_tests/shadow_preview_shot.png`` and auto-quits with
``===RESULT: PASS|FAIL===``.
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

TEMP = os.path.join(HERE, "temp_tests")
SHOT = os.path.join(TEMP, "shadow_preview_shot.png")
SIZE = 256
ORTHO = 9.0
LIGHT = (6.0, -1.5, 5.0)  # Blender is Z-up: the light sits at +X, above
lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


def report():
    import bpy

    passed = sum(1 for line in lines if line.startswith("OK"))
    for line in lines:
        print(line)
    result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
    print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.wm.quit_blender()


def _view3d():
    import bpy

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next(r for r in area.regions if r.type == "WINDOW")
                return window, area, region
    return None, None, None


def run():
    import bpy
    import numpy as np

    from blendertk.rig_utils.shadow_preview import ShadowPreview
    from blendertk.rig_utils.shadow_rig import ShadowRig

    os.makedirs(TEMP, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    # A table: a slab on four legs (two layers in the map, legs sharing bins).
    # Built from data, not operators: this runs in a timer callback, whose
    # context has no active object for an operator to leave a result in.
    def box(name, size, location):
        sx, sy, sz = (0.5 * v for v in size)
        verts = [(x, y, z) for x in (-sx, sx) for y in (-sy, sy) for z in (-sz, sz)]
        faces = [
            (0, 1, 3, 2),
            (4, 6, 7, 5),
            (0, 4, 5, 1),
            (2, 3, 7, 6),
            (0, 2, 6, 4),
            (1, 5, 7, 3),
        ]
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        ob = bpy.data.objects.new(name, mesh)
        ob.location = location
        scene.collection.objects.link(ob)
        return ob

    slab = box("Slab", (2.0, 1.2, 0.2), (1.0, 0.5, 1.1))
    legs = [
        box(f"Leg{i}", (0.14, 0.14, 1.0), (1.0 + dx, 0.5 + dy, 0.5))
        for i, (dx, dy) in enumerate(
            ((-0.85, -0.45), (0.85, -0.45), (-0.85, 0.45), (0.85, 0.45))
        )
    ]
    rig = ShadowRig.create(
        [slab] + legs,
        source_name="keyLight",
        rig_type="horizon",
        light_pos=LIGHT,
        texture_res=128,
        horizon_bins=32,
        horizon_size=(128, 64),
    )
    plane = rig.shadow_plane
    record_before = ShadowRig.export_record(plane)
    check("a horizon rig was built", record_before["type"] == "horizon")
    check(
        "no refusal in a windowed Blender",
        ShadowPreview.refusal() == "",
        ShadowPreview.refusal(),
    )

    try:
        ShadowPreview.attach(plane)
        check("attached", ShadowPreview.is_attached(plane), ShadowPreview.status())
    except Exception as error:  # noqa: BLE001
        check("attached", False, repr(error))
        return
    check(
        "the shader compiled", ShadowPreview._shader is not None, ShadowPreview.status()
    )
    check("the plane is hidden while the overlay stands in", plane.hide_get())
    check(
        "record unchanged while attached",
        ShadowRig.export_record(plane) == record_before,
    )

    # -- the shot: the 3D view itself, straight down, over white -----------
    # A viewport render (render.opengl) came back fully transparent -- it does
    # not carry a custom draw handler -- so the AREA is screenshotted and its
    # WINDOW region mapped to the ground plane through the view's own
    # perspective matrix, which is exact for an orthographic top view.
    for ob in [slab] + legs + [rig.light, rig.contact]:
        ob.hide_set(True)
    px, py = plane.matrix_world.translation.x, plane.matrix_world.translation.y
    window, area, region = _view3d()
    check("a 3D view exists", area is not None)
    if area is None:
        return
    space = area.spaces.active
    rv3d = space.region_3d
    rv3d.view_perspective = "ORTHO"
    rv3d.view_rotation = (1.0, 0.0, 0.0, 0.0)  # identity: looking down -Z
    rv3d.view_location = (px, py, 0.0)
    rv3d.view_distance = ORTHO
    space.shading.type = "SOLID"
    space.shading.background_type = "VIEWPORT"
    space.shading.background_color = (1.0, 1.0, 1.0)
    space.overlay.show_overlays = True
    space.overlay.show_floor = False
    space.overlay.show_axis_x = space.overlay.show_axis_y = False
    space.overlay.show_extras = False
    space.overlay.show_outline_selected = False
    space.overlay.show_cursor = False
    space.overlay.show_object_origins = False
    space.overlay.show_text = False
    space.show_gizmo = False
    plane.select_set(False)
    # Let the view settle and the overlay draw before the capture -- and run
    # the draw body once by hand under the same context, so a failure inside
    # it surfaces as a traceback here instead of a silent stand-down.
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
        try:
            ShadowPreview._draw_impl()
            check("the draw body runs", True)
        except Exception as error:  # noqa: BLE001
            check("the draw body runs", False, repr(error))
            traceback.print_exc()
        check(
            "overlay still standing after the draw",
            ShadowPreview.status() == "ok",
            ShadowPreview.status(),
        )
        try:
            bpy.ops.screen.screenshot(filepath=SHOT)
            check("screenshot written", os.path.exists(SHOT), SHOT)
        except Exception as error:  # noqa: BLE001
            check("screenshot written", False, repr(error))
            return

    # -- the pixels vs the reference, in the contact's LOCAL frame -----------
    from PIL import Image

    shot = np.asarray(Image.open(SHOT).convert("RGB"), dtype=float) / 255.0
    # The window screenshot holds every area; cut the 3D view's WINDOW
    # region out by its window coordinates (Blender counts region.y from the
    # bottom of the window, the image's row 0 is the top).
    wh, ww = shot.shape[:2]
    rx, ry, rw, rh = region.x, region.y, region.width, region.height
    check(
        "screenshot is the window's size",
        wh == window.height and ww == window.width,
        f"{shot.shape[:2]} vs {(window.height, window.width)}",
    )
    STRIDE = 4
    view = shot[wh - ry - rh : wh - ry, rx : rx + rw][::STRIDE, ::STRIDE]
    Image.fromarray((view * 255).astype(np.uint8)).save(
        SHOT.replace(".png", "_view.png")
    )
    # White background, the overlay's black blended over it in LINEAR light,
    # then the viewport's display transform: the screen carries
    # sRGB(1 - a * fade), and the reference is compared in that space.
    darkness = 1.0 - view.mean(axis=2)
    SIZE_Y, SIZE_X = view.shape[:2]
    # Region pixel -> ground point through the view's perspective matrix.
    inv = np.array(rv3d.perspective_matrix.inverted(), dtype=float)
    cols = ((np.arange(SIZE_X) * STRIDE) + 0.5) / rw * 2.0 - 1.0
    rows = 1.0 - ((np.arange(SIZE_Y) * STRIDE) + 0.5) / rh * 2.0  # row 0 is the top
    nx, ny = np.meshgrid(cols, rows)
    ndc = np.stack(
        [nx.ravel(), ny.ravel(), np.zeros(nx.size), np.ones(nx.size)], axis=1
    )
    hom = ndc @ inv.T
    world = hom[:, :3] / hom[:, 3:4]
    world[:, 2] = 0.0  # an orthographic top view: x, y are exact, z is the ground
    xs, ys = world[:, 0].reshape(SIZE_Y, SIZE_X), world[:, 1].reshape(SIZE_Y, SIZE_X)
    contact = rig.contact
    cm = np.array(contact.matrix_world, dtype=float)
    origin, a_ax, b_ax, up, ground = ShadowPreview.frame_params(
        cm,
        record_before["ground"],
        ShadowPreview.LOCAL_A,
        ShadowPreview.LOCAL_B,
        ShadowPreview.LOCAL_UP,
    )
    hz = record_before["horizon"]
    png = np.asarray(Image.open(rig.horizon_path).convert("RGBA"))
    hmap = __import__("pythontk").HorizonMap.from_rgba(
        png,
        bins=hz["bins"],
        size=hz["tile"],
        r_min=hz["r_min"],
        r_max=hz["r_max"],
        ground=ground,
        max_stretch=hz["max_stretch"],
    )
    rel = world - np.asarray(origin)
    # The map's frame: (dot A, dot Up, dot B) -- the reference is Y-up.
    frame_pts = np.column_stack([rel @ a_ax, np.zeros(len(rel)), rel @ b_ax])
    lrel = np.asarray(LIGHT, dtype=float) - np.asarray(origin)
    frame_light = [lrel @ a_ax, lrel @ up, lrel @ b_ax]
    alpha = hmap.alpha(
        frame_pts, light=frame_light, source_size=record_before["source_size"]
    ).reshape(SIZE_Y, SIZE_X)
    fade = float(plane.get(ShadowRig.OPACITY_ATTR, 1.0)) * float(
        plane.get("shadowIntensity", 1.0)
    )
    corners = np.array([(plane.matrix_world @ v.co)[:2] for v in plane.data.vertices])
    centre = corners.mean(axis=0)
    e1 = corners[1] - corners[0]
    e2 = corners[2] - corners[0]
    l1, l2 = np.linalg.norm(e1), np.linalg.norm(e2)
    relxy = np.column_stack([xs.ravel(), ys.ravel()]) - centre
    inside = (
        (np.abs(relxy @ (e1 / l1)) <= 0.5 * l1)
        & (np.abs(relxy @ (e2 / l2)) <= 0.5 * l2)
    ).reshape(SIZE_Y, SIZE_X)

    def srgb(lin):
        return np.where(
            lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055
        )

    expected = 1.0 - srgb(1.0 - alpha * fade)
    # The physics, independent of the pin: the shadow must lie on the far
    # side of the table from the light, and the plane the drivers placed
    # must sit there too.
    light_x = rig.light.matrix_world.translation.x
    table_x = slab.matrix_world.translation.x
    shade = alpha > 0.5
    centroid_x = float(xs[shade].mean()) if shade.any() else float("nan")
    print(
        f"  physics: light x {light_x:.2f}, table x {table_x:.2f}, plane x {px:.2f}, reference shadow centroid x {centroid_x:.2f}"
    )
    check(
        "the reference shadow lies away from the light",
        (centroid_x - table_x) * (light_x - table_x) < 0,
        f"centroid {centroid_x:.2f}",
    )
    drawn = darkness > 0.1
    drawn_x = (
        float(xs[drawn & inside].mean()) if (drawn & inside).any() else float("nan")
    )
    check(
        "the drawn shadow lies away from the light",
        (drawn_x - table_x) * (light_x - table_x) < 0,
        f"drawn centroid {drawn_x:.2f}",
    )
    diff = np.abs(darkness[inside] - expected[inside])
    n_in, in_shadow = int(inside.sum()), int((alpha[inside] > 0.5).sum())
    # Relative to the reference's own umbra: a soft source (Blender's default
    # light has a radius) puts a penumbra under the slab, so the umbra is not
    # alpha 1 -- only the pointwise match says whether the shader tracks it.
    umbra_px = inside & (alpha > 0.5)
    umbra = (
        darkness[umbra_px].mean() / expected[umbra_px].mean()
        if in_shadow
        else float("nan")
    )
    print(
        f"  pixels: {n_in} in plane, {in_shadow} in shadow (reference), fade {fade:.3f}, "
        f"mean|d| {diff.mean():.4f}, p98 {np.percentile(diff, 98):.3f}, "
        f">0.05: {100.0 * (diff > 0.05).mean():.2f}%, umbra/reference {umbra:.3f}"
    )
    check("the plane covers pixels", n_in > 500, str(n_in))
    check("the fixture casts a shadow", in_shadow > 100, str(in_shadow))
    check("mean pixel error < 0.03", diff.mean() < 0.03, f"{diff.mean():.4f}")
    check(
        "< 8% of pixels off by > 0.05",
        (diff > 0.05).mean() < 0.08,
        f"{100.0 * (diff > 0.05).mean():.2f}%",
    )
    if in_shadow:
        check("the umbra is as dark as the reference's", umbra > 0.9, f"{umbra:.3f}")
    # Outside the plane but near it: the toolbar and header overlap the
    # region's edges and are not the overlay's doing.
    near = (np.abs(relxy @ (e1 / l1)) <= 0.75 * l1) & (
        np.abs(relxy @ (e2 / l2)) <= 0.75 * l2
    )
    ring = near.reshape(SIZE_Y, SIZE_X) & ~inside
    check(
        "nothing drawn just outside the plane",
        darkness[ring].max() < 0.05,
        f"{darkness[ring].max():.3f}",
    )

    # -- liveness and detach -------------------------------------------------
    rig.light.location = (-2.0, 5.5, 4.0)
    bpy.context.view_layer.update()
    params, _ = ShadowPreview._plane_params(plane)
    check(
        "the source tracks the light",
        np.allclose(params[16:19], (-2.0, 5.5, 4.0), atol=1e-5),
        str(params[16:19]),
    )
    check("detached", ShadowPreview.detach(plane))
    check("visibility handed back", not plane.hide_get())
    check("overlay gone with the last plane", not ShadowPreview.is_enabled())
    check(
        "record unchanged after detach", ShadowRig.export_record(plane) == record_before
    )


def main():
    import bpy

    # A timer callback's context carries no window: give the run the 3D
    # view's, so operators (the rig's own, the viewport render) have one.
    window, area, region = _view3d()
    try:
        if area is None:
            run()
        else:
            with bpy.context.temp_override(window=window, area=area, region=region):
                run()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("no unhandled exception", False)
    report()


if __name__ == "__main__":
    import bpy

    # After the window exists: the first timer tick runs inside the event loop.
    bpy.app.timers.register(lambda: (main(), None)[1], first_interval=0.5)
