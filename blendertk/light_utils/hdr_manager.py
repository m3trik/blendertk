# !/usr/bin/python
# coding=utf-8
"""Blender world-HDRI environment manager.

:class:`HdrManager` mirrors mayatk's (name + behavior): the scene's one HDR
environment and the knobs tentacle's HDR Manager panel drives. Maya's is an Arnold
``aiSkyDomeLight`` + ``file`` texture network; Blender's rides the world shader --
Environment Texture -> Mapping -> Background -> World Output, found-or-created by
fixed node names (:meth:`LightUtils.set_world_hdri`).

Knobs (:attr:`HdrManager.KNOBS`), each a property re-read from the scene:

* ``intensity`` / ``exposure`` -- linear multiplier and stops. The world has one
  Strength (``intensity * 2**exposure``), so the pair round-trips through custom
  properties on the world.
* ``rotation``   -- Z rotation (azimuth) on the Mapping node, in degrees.
* ``visibility`` -- the environment as the render's backdrop (``film_transparent``
  off); the world lights the scene either way.
* ``resolution`` -- Cycles' importance-sampling map size, the analogue of Arnold's
  skydome Resolution. Reads None while Cycles sizes the map from the image
  (automatic); setting it switches to a manual size.
* ``diffuse`` / ``specular`` -- whether the world lights diffuse / glossy rays
  (Cycles ray visibility: on above 0, off at 0, where Arnold scales continuously).

mayatk's ``samples`` (a per-light sample count: Cycles samples the world with the
scene) and ``preview`` (a viewport-only backdrop: a per-3D-view shading setting in
Blender, not scene data) are not knobs here; neither are its Arnold-plugin checks or
``select`` (a world is not a selectable object).

``import bpy`` is deferred into the call bodies. Without bpy (a Qt-only harness)
every read answers its default and every write is a no-op, so a panel over this
loads anywhere.
"""

import os
from typing import Callable, Optional

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.light_utils._light_utils import LightUtils


class HdrManager(ptk.LoggingMixin):
    """Manage the scene world's HDR environment (mirror of mayatk's ``HdrManager``).

    Stateless like mayatk's: every property re-queries the scene, so an edit made
    elsewhere is read on the next access.
    """

    #: The knob properties of this host's environment: tentacle's HDR Manager
    #: enables a control only for a knob listed here (mayatk's also lists
    #: ``samples`` and ``preview``).
    KNOBS: tuple = (
        "rotation",
        "visibility",
        "intensity",
        "exposure",
        "resolution",
        "diffuse",
        "specular",
    )

    # ------------------------------------------------------------------
    # Scene access (every bpy touch degrades without Blender)
    # ------------------------------------------------------------------

    @staticmethod
    def _query(fn: Callable, *args, default=None, **kwargs):
        """``fn(*args, **kwargs)``, or *default* without Blender (no bpy)."""
        try:
            return fn(*args, **kwargs)
        except ImportError:  # a Qt-only harness: no scene to read or write
            return default

    @classmethod
    def _state(cls) -> Optional[dict]:
        """The managed environment's state (``LightUtils.get_world_hdri``), or None."""
        return cls._query(LightUtils.get_world_hdri)

    @classmethod
    def _apply(cls, filepath: Optional[str], levels: dict) -> None:
        """``LightUtils.set_world_hdri`` at *levels* (a ``get_world_hdri`` dict;
        each missing level at its default). *filepath* None keeps the map."""
        cls._query(
            LightUtils.set_world_hdri,
            filepath,
            intensity=levels.get("intensity", 1.0),
            exposure=levels.get("exposure", 0.0),
            rotation=levels.get("rotation", 0.0),
            visible=levels.get("visible", True),
        )

    def _set_level(self, key: str, value) -> None:
        """Re-apply the environment with *key* changed; a no-op without one (as
        mayatk's knobs on a missing skydome)."""
        state = self._state()
        if state is None:
            return
        state[key] = value
        self._apply(None, state)

    def _usable(self, tex: str) -> bool:
        """False (logged) for an existing but incomplete image -- a partly synced
        cloud HDR, which Blender's loader chokes on; a missing file passes (as
        mayatk's guard)."""
        if not os.path.isfile(tex):
            return True
        ok, reason = ptk.ImgUtils.validate_image_integrity(tex)
        if not ok:
            self.logger.warning(
                "HDR rejected (%s); refusing to load the incomplete image: %s",
                reason,
                tex,
            )
        return ok

    # ------------------------------------------------------------------
    # The environment
    # ------------------------------------------------------------------

    @property
    def hdr_env(self):
        """The scene world while it carries a managed environment map, else None."""
        if self._state() is None:
            return None
        import bpy

        return bpy.context.scene.world

    @hdr_env.setter
    @CoreUtils.undoable
    def hdr_env(self, tex: Optional[str]) -> None:
        """Set (and lazily build) the environment's map, its levels kept; ONE undo
        step. ``None`` / ``""`` is a no-op (:meth:`clear` removes it); an
        incomplete image is refused."""
        if not tex or not self._usable(str(tex)):
            return
        self._apply(str(tex), self._state() or {})

    @property
    def hdr_file_path(self) -> Optional[str]:
        """The environment map's file (absolute), or None."""
        return (self._state() or {}).get("filepath") or None

    # ------------------------------------------------------------------
    # Knobs
    # ------------------------------------------------------------------

    @property
    def visibility(self) -> bool:
        """The environment as the render's backdrop: ``film_transparent`` off
        (scene state, so it reads with no environment too; True without bpy)."""

        def read():
            import bpy

            return not bpy.context.scene.render.film_transparent

        return bool(self._query(read, default=True))

    @visibility.setter
    def visibility(self, state: bool) -> None:
        self._set_level("visible", bool(state))

    @property
    def rotation(self) -> float:
        """Z rotation (degrees, azimuth) of the environment; 0 without one."""
        return float((self._state() or {}).get("rotation", 0.0))

    @rotation.setter
    def rotation(self, degrees: float) -> None:
        self._set_level("rotation", float(degrees))

    @property
    def intensity(self) -> float:
        """Linear light-output multiplier; 1.0 without an environment."""
        return float((self._state() or {}).get("intensity", 1.0))

    @intensity.setter
    def intensity(self, value: float) -> None:
        self._set_level("intensity", float(value))

    @property
    def exposure(self) -> float:
        """Photographic stops (log2); 0.0 without an environment."""
        return float((self._state() or {}).get("exposure", 0.0))

    @exposure.setter
    def exposure(self, stops: float) -> None:
        self._set_level("exposure", float(stops))

    @property
    def resolution(self) -> Optional[int]:
        """The world's manual importance-sampling map size; None while Cycles
        sizes it from the image (automatic), off Cycles, or without a world."""
        return self._query(LightUtils.get_world_importance_resolution)

    @resolution.setter
    def resolution(self, value: int) -> None:
        """Switch the world to a manual map size of *value* (Cycles)."""
        self._query(LightUtils.set_world_importance_resolution, int(value))

    def _ray(self, key: str) -> float:
        visibility = self._query(LightUtils.get_world_ray_visibility) or {}
        return 1.0 if visibility.get(key, True) else 0.0

    @property
    def diffuse(self) -> float:
        """1.0 while the world lights diffuse rays, else 0.0 (Cycles)."""
        return self._ray("diffuse")

    @diffuse.setter
    def diffuse(self, value: float) -> None:
        self._query(LightUtils.set_world_ray_visibility, diffuse=float(value) > 0.0)

    @property
    def specular(self) -> float:
        """1.0 while the world lights glossy rays, else 0.0 (Cycles)."""
        return self._ray("glossy")

    @specular.setter
    def specular(self, value: float) -> None:
        self._query(LightUtils.set_world_ray_visibility, glossy=float(value) > 0.0)

    # ------------------------------------------------------------------
    # Network lifecycle
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_network(
        self,
        hdrMap: str = "",
        hdrMapVisibility: bool = False,
        intensity: Optional[float] = None,
        exposure: Optional[float] = None,
        rotation: Optional[float] = None,
        resolution: Optional[int] = None,
        samples: Optional[int] = None,
        diffuse: Optional[float] = None,
        specular: Optional[float] = None,
        preview: Optional[bool] = None,
    ):
        """Apply settings to the (lazily-built) environment as ONE undo step --
        mirror of mayatk's signature and behavior.

        Only non-None knobs are written. ``resolution`` applies only while the
        world already samples at a manual size: a build keeps Cycles' automatic
        sizing from the image (the ``resolution`` setter switches to manual).
        ``samples`` / ``preview`` are accepted for mayatk's signature and ignored
        (not knobs here). Returns the scene world, or None when no environment
        is set (no map, or an incomplete image refused).
        """
        state = self._state()
        levels = dict(state or {})
        for key, value in (
            ("intensity", intensity),
            ("exposure", exposure),
            ("rotation", rotation),
        ):
            if value is not None:
                levels[key] = float(value)
        levels["visible"] = bool(hdrMapVisibility)
        if hdrMap and self._usable(str(hdrMap)):
            self._apply(str(hdrMap), levels)
        elif state is not None:
            self._apply(None, levels)
        if diffuse is not None or specular is not None:
            self._query(
                LightUtils.set_world_ray_visibility,
                diffuse=None if diffuse is None else float(diffuse) > 0.0,
                glossy=None if specular is None else float(specular) > 0.0,
            )
        if resolution is not None and self.resolution is not None:
            self.resolution = resolution
        return self.hdr_env

    @CoreUtils.undoable
    def clear(self) -> None:
        """Remove the managed environment (its Environment Texture / Mapping /
        coordinate nodes), leaving the world's own Background -> World Output;
        ONE undo step (mirror of mayatk's)."""
        self._query(LightUtils.clear_world_hdri)

    @staticmethod
    def defer_apply(callback: Callable[[], None]) -> None:
        """Run *callback*, a scene edit a dropdown pick drives -- at once: a world
        edit has no popup-teardown hazard. Mirror of mayatk's, which defers to
        Maya's idle queue (a live Arnold IPR re-translating inside the popup's
        re-entrant Qt loop sticks the render black)."""
        callback()


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
