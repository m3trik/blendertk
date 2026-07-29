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

from blendertk.core_utils._core_utils import CoreUtils


class BlenderBridgeSlotsBase(BridgeSlotsBase):
    """Adds a Blender-flavored ``default_output_dir`` to :class:`BridgeSlotsBase`."""

    def default_output_dir(self) -> str:
        """The saved ``.blend`` file's directory, or ``""`` if unsaved."""
        return CoreUtils.get_env_info("workspace") or ""

    def _install_optional_package(self, spec: str) -> None:
        """Install an optional package where Blender will actually import it.

        Overrides the base's ``pip install --user``: Blender's bundled
        interpreter does not put the user-site on ``sys.path``, so a ``--user``
        install would succeed and still be unimportable. Routes through
        :meth:`CoreUtils.ensure_packages`, which installs into Blender's
        per-version user-modules dir (already on ``sys.path``) using the
        bundled interpreter, and adds it to ``sys.path`` for this session.
        """
        CoreUtils.ensure_packages({spec: spec.replace("-", "_")})
