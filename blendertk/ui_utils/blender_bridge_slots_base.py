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
    """Adds a Blender-flavored ``default_output_dir`` + Scope resolution to
    :class:`BridgeSlotsBase`."""

    def default_output_dir(self) -> str:
        """The saved ``.blend`` file's directory, or ``""`` if unsaved."""
        return CoreUtils.get_env_info("workspace") or ""

    # ------------------------------------------------------------------ scope
    def resolve_scope_objects(self, scope: str):
        """Objects to export for the chosen ``SCOPE`` param.

        ``"selected"`` is the default AND the fallback for any unknown value --
        an unrecognised scope must never silently widen a send to the whole
        scene.

        Lives on the shared base so every Blender bridge (Maya / Unity /
        Marmoset / Substance) resolves scope identically; the spec that drives
        it is :meth:`uitk.bridge.Parameters.scope_spec`, shared with mayatk's
        mirror (``MayaBridgeSlotsBase.resolve_scope_objects``).
        """
        import bpy
        import blendertk as btk

        if scope == "all":
            # The bridge's whole-scene hook (``BlenderExportMixin._scene_objects``:
            # the current SCENE's objects -- not ``bpy.data.objects``, which also
            # sweeps in unlinked/orphaned objects and other scenes' -- UNFILTERED by
            # type, since each bridge's export options decide what travels and
            # empties/armatures must reach the exporter or the hierarchy silently
            # flattens on the far side). Routed through the hook, not re-derived,
            # so "Entire Scene" and ``save_as``'s whole-scene default can never
            # drift apart. getattr: a panel that has not built its bridge yet has
            # no ``.bridge`` at all, and bridges without the hook (RPC bakers) fall
            # back to the scene's geometry. Mirror of mayatk's resolver.
            bridge = getattr(self, "bridge", None)
            scene = bridge._scene_objects() if bridge is not None else None
            if scene is not None:
                return scene
            return [o for o in bpy.context.scene.objects if o.type == "MESH"]
        if scope == "visible":
            # What the user sees -- every visible object, not just geometry:
            # a visible group Empty is part of the scene being sent, and the
            # export options filter types exactly as in the "all" scope.
            return [o for o in bpy.context.scene.objects if o.visible_get()]
        return btk.selected_objects()

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
