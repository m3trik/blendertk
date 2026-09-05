# !/usr/bin/python
# coding=utf-8
"""A live viewport preview of a horizon rig: the artist drags the light and
sees the outline morph, exactly as Unity and the WebXR viewer will show it.

Mirror of mayatk's ``rig_utils/shadow_preview.py`` -- same name, same
behaviour, a different mechanism. Maya gets a hardware *material* (a
``GLSLShader`` the viewport shades the plane with); Blender's EEVEE nodes have
no bitwise test and no integer texel fetch, so the map cannot be read in a
node graph at all. Here the preview is a ``gpu`` **overlay**: a
``SpaceView3D`` draw handler that draws the plane's quad in world space with a
shader built through ``gpu.shader.create_from_info`` -- the only route to a
custom shader in Blender 4.2+, where ``GPUShader`` can no longer be
instantiated. The real plane is hidden while the overlay stands in for it
(viewport visibility only; nothing in its material or data changes), and the
effect does not appear in renders.

The evaluation is not written here. It is ``pythontk/geo_utils/shadow_horizon.glsl``
-- the one text every engine and both DCCs run -- assembled at run time
through :meth:`pythontk.ShadowHorizon.shader_source` behind a Blender host
prologue: the interface block, the uniform buffer, and ``SH_Fetch``, the texel
hook that owns Blender's texture-row convention. Blender is a Python process
when it needs the shader, so it carries no mirror.

**The frame is the contact's LOCAL frame, not the record's.** The record's
``frame_a`` / ``frame_b`` are the exporter's axes (``HORIZON_FRAME``, with
Blender's local Y turned into the file's -Z), but the map was baked with
``up = 2`` in the contact's own frame: bearing zero along local X, increasing
toward local Y, up along local Z. The preview feeds those through the contact
empty's world matrix. Using the record's frame here would produce a
plausible, mirrored shadow.

**Nothing of it is exported.** The overlay touches no material, no image and
no custom prop the record reads, so the export record is the same with it on
or off; only the plane's viewport visibility is borrowed, and the ``"shadow"``
export preparer hands it back before an FBX is written.

**Never headless.** ``--background`` has no GPU backend
(``SystemError: GPU functions for drawing are not available``), so the
overlay cannot be compiled or drawn in the test runner; ``enable`` reports
that as a refusal, and the windowed ``test/shadow_preview_gui_check.py`` is
the only place the shader is proven.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import pythontk as ptk

__all__ = ["ShadowPreview"]

#: The uniform buffer the host fills per draw. Everything but the MVP travels
#: here: a push-constant block is capped at 128 bytes on the Vulkan backend,
#: and the frame alone is four vec4s. Layout is std140, so every member is a
#: vec4 and the scalars ride in the w lanes.
_PARAMS_STRUCT = """struct ShParams
{
    vec4 origin;      // xyz the contact origin (world), w the ground height in the frame
    vec4 axisA;       // xyz, w = bins
    vec4 axisB;       // xyz, w = cols
    vec4 axisUp;      // xyz, w = layers
    vec4 source;      // xyz world; w 1 = position, 0 = the direction it shines
    vec4 sourceSize;  // x diameter, y angular diameter, z tileW, w tileH
    vec4 range;       // rMin, rMax, maxStretch, opacity * intensity
    vec4 rect;        // the tile block inside its image: sx, sy, ox, oy
};
"""

_VERTEX = """
void main()
{
    world = pos;
    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
}
"""

# ``SH_ROWS_FROM_BOTTOM`` names the row assumption so the windowed check can
# refute it in one line. Blender's image buffer runs bottom-up and
# ``ShadowRig._save_image(flip=True)`` writes the map so the FILE's top row is
# the tile grid's row 0 -- which puts that row at the TOP of the buffer, i.e.
# at the highest texel row. Maya measured the other way (row 0 = the top).
_FRAGMENT_HEAD = """
#define SH_ROWS_FROM_BOTTOM {rows_from_bottom}

vec4 SH_Fetch(int col, int row, int xi, int yi)
{{
    ivec2 size = textureSize(horizonMap, 0);
    int tileW = int(params.sourceSize.z + 0.5);
    int tileH = int(params.sourceSize.w + 0.5);
    int cols = int(params.axisB.w + 0.5);
    int rows = (int(params.axisUp.w + 0.5) * int(params.axisA.w + 0.5) + cols - 1) / cols;
    int px = int(params.rect.z * float(size.x) + 0.5) + col * tileW + xi;
#if SH_ROWS_FROM_BOTTOM
    int py = int(params.rect.w * float(size.y) + 0.5) + (rows - 1 - row) * tileH + (tileH - 1 - yi);
#else
    int py = int((1.0 - params.rect.w - params.rect.y) * float(size.y) + 0.5) + row * tileH + yi;
#endif
    return texelFetch(horizonMap, ivec2(px, py), 0);
}}
"""

_FRAGMENT_MAIN = """
void main()
{
    ShGrid g = ShMakeGrid(
        int(params.axisA.w + 0.5), int(params.axisB.w + 0.5), int(params.axisUp.w + 0.5),
        int(params.sourceSize.z + 0.5), int(params.sourceSize.w + 0.5),
        params.range.x, params.range.y, params.range.z, params.origin.w);
    float a = ShAlpha(g, world, params.origin.xyz, params.axisA.xyz, params.axisB.xyz,
                      params.axisUp.xyz, params.source, params.sourceSize.xy);
    fragColor = vec4(0.0, 0.0, 0.0, a * params.range.w);
}
"""


class _ShadowPreviewInternal:
    """The DCC-free half: the shader text and the frame math."""

    #: Blender's image buffer runs bottom-up (see ``_FRAGMENT_HEAD``).
    ROWS_FROM_BOTTOM = True
    #: The contact's LOCAL frame the map was baked in (``bake_horizon``:
    #: ``up = 2``, so ``horizontal_axes`` gives X then Y).
    LOCAL_A = (1.0, 0.0, 0.0)
    LOCAL_B = (0.0, 1.0, 0.0)
    LOCAL_UP = (0.0, 0.0, 1.0)

    @classmethod
    def fragment_source(cls) -> str:
        """The fragment shader: the texel hook, the shared body, ``main``."""
        head = _FRAGMENT_HEAD.format(rows_from_bottom=1 if cls.ROWS_FROM_BOTTOM else 0)
        return head + ptk.ShadowHorizon.shader_source("glsl") + _FRAGMENT_MAIN

    @staticmethod
    def vertex_source() -> str:
        return _VERTEX

    @staticmethod
    def params_struct() -> str:
        return _PARAMS_STRUCT

    @staticmethod
    def frame_params(
        contact_matrix, ground_height: float, local_a, local_b, local_up
    ) -> Tuple[List[float], List[float], List[float], List[float], float]:
        """``(origin, A, B, up, ground)`` in world space from the contact's
        4x4 world matrix (column-vector convention, ``M @ v``) and the world
        ground height along Z. Pure: what the windowed check pins headless."""
        import numpy as np

        m = np.asarray(contact_matrix, dtype=float).reshape(4, 4)
        origin = m[:3, 3]

        def axis(local):
            v = m[:3, :3] @ np.asarray(local, dtype=float)
            n = float(np.linalg.norm(v))
            return v / n if n > 1e-12 else v

        a, b, up = axis(local_a), axis(local_b), axis(local_up)
        # The ground plane's height along up: any point on it, minus the
        # origin, dotted with up. Blender's ground is a world Z height.
        ground = float(np.dot(np.array([0.0, 0.0, ground_height]) - origin, up))
        return origin.tolist(), a.tolist(), b.tolist(), up.tolist(), ground


class ShadowPreview(_ShadowPreviewInternal, ptk.LoggingMixin):
    """Enable / disable the live horizon overlay on shadow planes (module doc)."""

    #: Custom prop on the plane holding its viewport visibility before the
    #: overlay borrowed it (``hide_get``), so disable hands back what it took.
    HIDDEN_PROP = "horizonPreviewWasHidden"

    _handle = None
    _shader = None
    _planes: List[str] = []
    #: One uniform buffer per attached plane, updated in place each draw
    #: rather than allocated per frame.
    _blocks: dict = {}
    _disarming = False
    _state = "unknown"

    # ------------------------------------------------------------ availability
    @classmethod
    def refusal(cls) -> str:
        """Why the overlay cannot run in this session (empty when it can)."""
        import bpy

        if bpy.app.background:
            return (
                "The horizon preview draws with the GPU module, which has no "
                "backend in a background (headless) Blender."
            )
        return ""

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._handle is not None

    @classmethod
    def status(cls) -> str:
        """``"ok"``, or the reason the overlay stood down."""
        return cls._state

    # ------------------------------------------------------------------- state
    @classmethod
    def is_attached(cls, plane) -> bool:
        return cls.is_enabled() and plane.name in cls._planes

    @classmethod
    def attached_planes(cls) -> List:
        import bpy

        return [
            o
            for name in list(cls._planes)
            for o in (bpy.data.objects.get(name),)
            if o is not None
        ]

    # ------------------------------------------------------------ attach/detach
    @classmethod
    def attach(cls, plane) -> None:
        """Show the live preview on *plane* (a horizon rig's plane).

        Raises:
            ValueError: *plane* is not a horizon plane, has no map, or the
                session cannot draw (headless).
        """
        from blendertk.rig_utils.shadow_rig import ShadowRig

        if ShadowRig.plane_type(plane) != "horizon":
            raise ValueError(f"{plane.name}: not a horizon rig plane.")
        if cls._horizon_image(plane) is None:
            raise ValueError(f"{plane.name}: its horizon map image is missing.")
        refusal = cls.refusal()
        if refusal:
            raise ValueError(refusal)
        if not cls._enable():
            raise ValueError(f"The horizon preview could not start: {cls._state}")
        if plane.name not in cls._planes:
            # Borrow the visibility only once it can actually be taken: a
            # plane outside the active view layer raises here, and must not
            # be left stamped as borrowed.
            was_hidden = bool(plane.hide_get())
            plane.hide_set(True)
            plane[cls.HIDDEN_PROP] = was_hidden
            cls._planes.append(plane.name)
        cls._register_export_preparer()
        cls._tag_views()
        cls.logger.info(f"{plane.name}: horizon preview on.")

    @classmethod
    def detach(cls, plane) -> bool:
        """Give the plane its visibility back; drop the overlay with the last
        plane. Returns whether anything was attached."""
        attached = plane.name in cls._planes
        if attached:
            cls._planes.remove(plane.name)
        if cls.HIDDEN_PROP in plane:
            try:
                plane.hide_set(bool(plane[cls.HIDDEN_PROP]))
            except RuntimeError:  # not in this view layer
                pass
            del plane[cls.HIDDEN_PROP]
        if not cls._planes:
            cls._disable()
        cls._tag_views()
        if attached:
            cls.logger.info(f"{plane.name}: horizon preview off.")
        return attached

    @classmethod
    def toggle(cls, planes: Sequence, on: bool) -> Tuple[List, List[str]]:
        """Attach (*on*) or detach the preview on *planes*; returns
        ``(done, failed)`` where *failed* carries ``"<plane>: <reason>"``."""
        done, failed = [], []
        for plane in planes:
            try:
                if on:
                    cls.attach(plane)
                else:
                    cls.detach(plane)
                done.append(plane)
            except Exception as error:  # noqa: BLE001 -- one plane never blocks the rest
                failed.append(f"{getattr(plane, 'name', plane)}: {error}")
        return done, failed

    @classmethod
    def detach_all(cls) -> List:
        planes = cls.attached_planes()
        for plane in planes:
            cls.detach(plane)
        # Planes whose objects are gone still hold the overlay open.
        cls._planes.clear()
        cls._disable()
        return planes

    # ----------------------------------------------------------------- export
    @classmethod
    def prepare_for_export(cls) -> None:
        """The ``"shadow"`` export preparer: every plane visible again before
        an exporter that honours visibility walks the file, then the metadata
        republished by the producer this replaces."""
        from blendertk.rig_utils.shadow_rig import ShadowRig

        cls.detach_all()
        ShadowRig.refresh_export_metadata()

    @classmethod
    def _register_export_preparer(cls) -> None:
        from blendertk.env_utils.fbx_utils import FbxUtils

        FbxUtils.register_export_preparer("shadow", cls.prepare_for_export)

    # ---------------------------------------------------------------- overlay
    @classmethod
    def _enable(cls) -> bool:
        """Register the draw handler (idempotent); False when refused."""
        import bpy

        if cls._handle is not None:
            return True
        try:
            cls._shader = cls._build_shader()
        except Exception as error:  # noqa: BLE001 -- a compile failure is a refusal
            cls._state = f"shader failed: {error!r}"
            cls.logger.warning(cls._state)
            return False
        try:
            cls._handle = bpy.types.SpaceView3D.draw_handler_add(
                cls._draw, (), "WINDOW", "POST_VIEW"
            )
        except Exception as error:  # noqa: BLE001 -- draw handlers refused
            cls._state = f"draw handler refused: {error!r}"
            cls.logger.warning(cls._state)
            return False
        cls._state = "ok"
        cls._disarming = False
        return True

    @classmethod
    def _disable(cls) -> None:
        import bpy

        cls._disarming = False
        if cls._handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(cls._handle, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            cls._handle = None
        cls._shader = None
        cls._blocks.clear()

    @classmethod
    def _stand_down(cls, reason: str) -> None:
        """Disable from INSIDE the draw callback -- on the next tick, since a
        handler must not be removed while Blender iterates its handler list.
        Guarded: the view may redraw several times before the timer runs."""
        import bpy

        cls._state = reason
        cls.logger.warning(f"horizon preview stood down: {reason}")
        if cls._disarming:
            return
        cls._disarming = True

        def _remove():
            for plane in cls.attached_planes():
                cls.detach(plane)
            cls._planes.clear()
            cls._disable()
            return None

        try:
            bpy.app.timers.register(_remove, first_interval=0.0)
        except Exception:  # noqa: BLE001 -- no timer: remove now
            _remove()

    @staticmethod
    def _tag_views() -> None:
        from blendertk.core_utils._core_utils import CoreUtils

        CoreUtils.tag_redraw("VIEW_3D")

    @classmethod
    def _build_shader(cls):
        """``gpu.shader.create_from_info`` over the shared body."""
        import gpu

        info = gpu.types.GPUShaderCreateInfo()
        info.typedef_source(cls.params_struct())
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.uniform_buf(0, "ShParams", "params")
        info.sampler(0, "FLOAT_2D", "horizonMap")
        info.vertex_in(0, "VEC3", "pos")
        interface = gpu.types.GPUStageInterfaceInfo("sh_interface")
        interface.smooth("VEC3", "world")
        info.vertex_out(interface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(cls.vertex_source())
        info.fragment_source(cls.fragment_source())
        return gpu.shader.create_from_info(info)

    # ------------------------------------------------------------------ draw
    @classmethod
    def _draw(cls) -> None:
        """POST_VIEW callback. Wrapped whole and self-disabling: this runs
        inside Blender's draw loop, where a raised exception would spam the
        console every frame."""
        try:
            cls._draw_impl()
        except Exception as error:  # noqa: BLE001
            cls._stand_down(f"draw failed: {error!r}")

    @classmethod
    def _draw_impl(cls) -> None:
        import bpy
        import gpu
        from gpu_extras.batch import batch_for_shader

        shader = cls._shader
        if shader is None:
            return
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)
        try:
            mvp = (
                gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
            )
            shader.bind()
            shader.uniform_float("ModelViewProjectionMatrix", mvp)
            for name in list(cls._planes):
                plane = bpy.data.objects.get(name)
                if plane is None:
                    continue
                params, image = cls._plane_params(plane)
                if params is None:
                    continue
                # The map's GPU texture, re-fetched every draw: Recalculate
                # recreates the datablock on a resolution change, and a held
                # texture would then point at a freed image.
                texture = gpu.texture.from_image(image)
                # Both GPU objects must OUTLIVE the draw call: a GPUUniformBuf
                # or texture handed over as a temporary is freed by Python
                # before batch.draw reads it, and a freed block samples as
                # zeros (measured: a compiled, running overlay that drew
                # nothing at all). The block is kept per plane and refilled.
                block = cls._params_buffer(name, params)
                shader.uniform_sampler("horizonMap", texture)
                shader.uniform_block("params", block)
                corners = cls._plane_corners(plane)
                batch = batch_for_shader(
                    shader,
                    "TRIS",
                    {"pos": corners},
                    indices=[(0, 1, 2), (0, 2, 3)],
                )
                batch.draw(shader)
                del texture
        finally:
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")

    @staticmethod
    def _plane_corners(plane) -> List[Tuple[float, float, float]]:
        """The plane's four vertices in world space, in quad order."""
        mw = plane.matrix_world
        verts = plane.data.vertices
        # The rig's plane is a single quad; a vertex order the two triangles
        # (0,1,2)(0,2,3) close correctly is the face's own loop order.
        face = plane.data.polygons[0] if plane.data.polygons else None
        order = list(face.vertices) if face is not None else [0, 1, 2, 3]
        return [tuple(mw @ verts[i].co) for i in order[:4]]

    @classmethod
    def _horizon_image(cls, plane):
        """The plane's horizon map datablock (``<base>_horizon``), or None."""
        import bpy
        import os

        from blendertk.rig_utils.shadow_rig import ShadowRig

        name = ShadowRig._plane_prop(plane, ShadowRig._HORIZON_TEX_PROP, "")
        if not name:
            return None
        return bpy.data.images.get(os.path.splitext(str(name))[0])

    @classmethod
    def _plane_params(cls, plane):
        """``(params, image)`` for one plane, ``(None, None)`` when the rig's
        pieces are gone: the frame off the contact's live matrix, the source
        off the light's, the layout off the record's horizon block."""
        from blendertk.rig_utils.shadow_rig import ShadowRig

        image = cls._horizon_image(plane)
        contact = ShadowRig._plane_contact(plane)
        if image is None or contact is None:
            return None, None
        # This runs on every viewport redraw: the horizon block and a few
        # props, never the full export record (which also resolves texture
        # paths and rig links the shader has no use for).
        prop = ShadowRig._plane_prop
        horizon = ShadowRig._horizon_params(plane)
        origin, a, b, up, ground = cls.frame_params(
            contact.matrix_world,
            float(prop(plane, "groundHeight", 0.0) or 0.0),
            cls.LOCAL_A,
            cls.LOCAL_B,
            cls.LOCAL_UP,
        )
        _, source = ShadowRig._rig_links(plane)
        size = float(prop(plane, "sourceSize", 0.0) or 0.0)
        diameter, angle = size, 0.0
        if source is not None:
            mw = source.matrix_world
            if ShadowRig.source_is_directional(source):
                d = -(mw.col[2].xyz)
                d.normalize()
                src = [d.x, d.y, d.z, 0.0]
                diameter, angle = 0.0, size
            else:
                t = mw.translation
                src = [t.x, t.y, t.z, 1.0]
        else:
            src = [0.0, 0.0, -1.0, 0.0]  # no source: noon
        rect = horizon.get("rect") or [1.0, 1.0, 0.0, 0.0]
        tile = horizon.get("tile") or [1, 1]
        fade = float(plane.get(ShadowRig.OPACITY_ATTR, 1.0)) * float(
            plane.get("shadowIntensity", 1.0)
        )
        params = [
            *origin,
            ground,
            *a,
            float(horizon.get("bins", 1)),
            *b,
            float((horizon.get("layout") or [1, 1])[0]),
            *up,
            float(horizon.get("layers", ptk.ShadowHorizon.LAYERS)),
            *src,
            diameter,
            angle,
            float(tile[0]),
            float(tile[1]),
            float(horizon.get("r_min", 0.1)),
            float(horizon.get("r_max", 1.0)),
            # The BAKE's cot scale, never the plane's live placement cap.
            float(horizon.get("max_stretch", ptk.ShadowProjection.DEFAULT_MAX_STRETCH)),
            fade,
            *[float(v) for v in rect],
        ]
        return params, image

    @classmethod
    def _params_buffer(cls, name: str, params: List[float]):
        """The plane's uniform block, refilled in place; created on first use."""
        import gpu

        data = gpu.types.Buffer("FLOAT", len(params), params)
        block = cls._blocks.get(name)
        if block is None:
            block = cls._blocks[name] = gpu.types.GPUUniformBuf(data)
        else:
            block.update(data)
        return block
