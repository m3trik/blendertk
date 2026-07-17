# !/usr/bin/python
# coding=utf-8
"""Blender-flavored :class:`BridgeSlotsBase` -- adds Blender-side defaults.

The DCC-agnostic base lives upstream in :mod:`uitk.bridge.slots`
(re-exported through :mod:`uitk.bridge`). This thin subclass injects
the one piece every Blender bridge needs: a sensible Output Dir fallback
sourced from the saved ``.blend`` file's directory (Blender's analogue of
a Maya scene/workspace dir) when the user leaves the field blank.

Mirror of mayatk's :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase` --
the Marmoset, Substance, and Unity bridge slots subclass this instead of
inheriting from ``BridgeSlotsBase`` directly, so the fallback lives in one
place (Unity opts back out by overriding ``default_output_dir`` to return
``""`` -- mirroring mayatk, a ``.blend`` dir isn't a Unity project).
"""
from __future__ import annotations

from uitk.bridge import BridgeSlotsBase

from blendertk.core_utils._core_utils import get_env_info


class BlenderBridgeSlotsBase(BridgeSlotsBase):
    """Adds a Blender-flavored ``default_output_dir`` to :class:`BridgeSlotsBase`."""

    def default_output_dir(self) -> str:
        """The saved ``.blend`` file's directory, or ``""`` if unsaved."""
        return get_env_info("workspace") or ""
