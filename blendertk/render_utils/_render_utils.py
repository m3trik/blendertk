# !/usr/bin/python
# coding=utf-8
"""Render control -- mirror of mayatk's ``render_utils.RenderUtils``.

The render engines a user can pick, the scene's key render settings with named
presets over them, the cameras a render looks through, and one rendered frame
(``render.render`` in Blender's render window, its Render View). Maya's IPR and
render redo have no Blender counterpart: Blender's interactive render is the
viewport's Rendered shading, and its Render Result slots keep earlier renders,
so :meth:`RenderUtils.supports_ipr` / :meth:`RenderUtils.start_ipr` answer
False and :meth:`RenderUtils.redo_previous_render` renders again.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

from typing import Any, Dict, List, Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils


class RenderUtils:
    """Render-engine enumeration / selection, render settings and a rendered frame."""

    #: Blender's own Workbench engine, never add-on registered.
    _WORKBENCH = ("BLENDER_WORKBENCH", "Workbench")

    #: The key render settings, ``{key: RenderSettings property}``: mayatk's
    #: resolution keys (its Arnold sampling keys are Maya's), so a field, a
    #: preset and a script name a setting the same way in both hosts.
    RENDER_SETTINGS = {"width": "resolution_x", "height": "resolution_y"}

    @staticmethod
    def _scene():
        """The window's scene (windowless, ``bpy.context`` holds the default one)."""
        import bpy

        with CoreUtils.window_context_override():
            return bpy.context.scene

    @staticmethod
    def _eevee_id() -> str:
        """EEVEE's engine identifier in this Blender: 4.2-4.4 register it as
        ``BLENDER_EEVEE_NEXT``, 4.5+ (and pre-4.2) as ``BLENDER_EEVEE``, and
        assigning the wrong one raises TypeError."""
        import bpy

        items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
        idents = {item.identifier for item in items}
        return next(
            (i for i in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT") if i in idents),
            "BLENDER_EEVEE",
        )

    # ----------------------------------------------------------- enumeration
    @classmethod
    def get_available_renderers(cls) -> List[Dict[str, object]]:
        """The engines ``scene.render.engine`` accepts: EEVEE and Workbench,
        then Cycles and any installed add-on engine. Mirror of mayatk's
        ``RenderUtils.get_available_renderers``.

        The add-on engines are read off the ``bpy.types.RenderEngine``
        subclasses: the engine enum itself under-reports them with no window
        (``--background``).

        Returns:
            list: ``{"name", "label", "loaded"}`` dicts in a stable order
            (built-ins first), de-duplicated by ``name``.
        """
        import bpy

        out, seen = [], set()
        for ident, label in ((cls._eevee_id(), "EEVEE"), cls._WORKBENCH):
            seen.add(ident)
            out.append({"name": ident, "label": label, "loaded": True})
        for engine in bpy.types.RenderEngine.__subclasses__():
            ident = getattr(engine, "bl_idname", None)
            if ident and ident not in seen:
                seen.add(ident)
                label = getattr(engine, "bl_label", None) or ident
                out.append({"name": ident, "label": label, "loaded": True})
        return out

    # ------------------------------------------------------------- selection
    @classmethod
    def current_renderer(cls) -> str:
        """The scene's render engine (``scene.render.engine``)."""
        return cls._scene().render.engine

    @classmethod
    @CoreUtils.undoable
    def set_renderer(cls, name: str) -> None:
        """Make *name* the scene's render engine. Mirror of mayatk's.

        Raises:
            ValueError: *name* is no engine this Blender registers.
        """
        try:
            cls._scene().render.engine = name
        except TypeError as error:
            raise ValueError(f"Unknown render engine {name!r}: {error}") from error

    # ------------------------------------------------------- render settings
    @classmethod
    def render_settings(cls) -> Dict[str, Any]:
        """The scene's value of every :attr:`RENDER_SETTINGS` key. Mirror of
        mayatk's."""
        render = cls._scene().render
        return {key: getattr(render, prop) for key, prop in cls.RENDER_SETTINGS.items()}

    @classmethod
    @CoreUtils.undoable
    def set_render_settings(cls, values: Dict[str, Any]) -> int:
        """Write render settings to the scene; keys outside
        :attr:`RENDER_SETTINGS` (a Maya preset's Arnold keys, a ``_meta``) are
        skipped. Mirror of mayatk's. One undo step.

        Returns:
            int: The number of settings written.
        """
        known = {k: v for k, v in values.items() if k in cls.RENDER_SETTINGS}
        render = cls._scene().render
        for key, value in known.items():
            setattr(render, cls.RENDER_SETTINGS[key], int(value))
        return len(known)

    @staticmethod
    def render_preset_store() -> "ptk.PresetStore":
        """Named render-setting presets: ``{key: value}`` dicts of
        :attr:`RENDER_SETTINGS` keys, applied with :meth:`set_render_settings`,
        under the shared config root. Mirror of mayatk's (which also ships
        Arnold tiers; Blender ships none)."""
        return ptk.PresetStore("render", package="blendertk")

    # --------------------------------------------------------------- cameras
    @classmethod
    def render_cameras(cls) -> List[str]:
        """The names of the scene's cameras, the scene camera first. Mirror of
        mayatk's ``RenderUtils.render_cameras`` (its perspective camera first).

        Returns:
            list: Camera object names; ``[]`` in a scene without one.
        """
        scene = cls._scene()
        names = [o.name for o in scene.objects if o.type == "CAMERA"]
        default = scene.camera.name if scene.camera is not None else None
        if default not in names:
            return names
        return [default] + [n for n in names if n != default]

    # ----------------------------------------------------------- render view
    @classmethod
    def render_camera(cls, camera=None, render_mode: str = "render") -> None:
        """Render the current frame through *camera*, made the scene camera, in
        Blender's render window (``render.render``, invoked: its progress and
        Render Result). Mirror of mayatk's ``RenderUtils.render_camera`` (the
        Render View there).

        Parameters:
            camera: A camera object or name; ``None`` keeps the scene camera.
            render_mode (str): mayatk's Render View mode, accepted for the panel
                both hosts share: Blender renders the frame.

        Raises:
            ValueError: *camera* names no camera object.
            RuntimeError: Blender's render refused (no camera, no window).
        """
        import bpy

        scene = cls._scene()
        if camera is not None:
            obj = bpy.data.objects.get(camera) if isinstance(camera, str) else camera
            if obj is None or obj.type != "CAMERA":
                raise ValueError(f"Camera '{camera}' does not exist.")
            scene.camera = obj
        # INVOKE opens the render window and its progress UI, which read the
        # window / screen context (dead in the Qt-pump state).
        with CoreUtils.window_context_override():
            bpy.ops.render.render("INVOKE_DEFAULT")

    @classmethod
    def redo_previous_render(cls, render_mode: str = "render") -> None:
        """Render again through the scene camera: Blender keeps earlier renders
        in its Render Result slots, so Maya's redo has nothing to reuse. Mirror
        of mayatk's."""
        cls.render_camera(None, render_mode)

    @staticmethod
    def supports_ipr(renderer: Optional[str] = None) -> bool:
        """False: Blender's interactive render is the viewport's Rendered
        shading, not a render session. Mirror of mayatk's (a panel disables its
        IPR option on it)."""
        return False

    @staticmethod
    def start_ipr(camera: str, renderer: Optional[str] = None) -> bool:
        """False: no IPR session (:meth:`supports_ipr`); the caller falls back
        to a single render. Mirror of mayatk's."""
        return False
