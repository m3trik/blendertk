# !/usr/bin/python
# coding=utf-8
"""Blender-flavored :class:`BridgeSlotsBase` -- adds Blender-side defaults.

The DCC-agnostic base lives upstream in :mod:`uitk.bridge.slots`
(re-exported through :mod:`uitk.bridge`). This thin subclass injects
what every Blender bridge shares: the meaning of a blank Output Dir and the
Scope resolution (``BridgeHost``'s).

A blank Output Dir means "the bridge's own rule" (uitk's ``default_output_dir``,
``""``, is kept): hand-off intermediates go to a ``TempArtifacts`` scratch per
send, and what is kept goes to a folder of the file's project (a bake's maps to
``sourceimages/baked``, a mesh Painter re-reads to ``cache/substance``, a saved
Toolbag scene to ``data/marmoset``). mayatk's base means the same.

Mirror of mayatk's :class:`mayatk.ui_utils.maya_bridge_slots_base.MayaBridgeSlotsBase` --
the Rizom and Maya bridge slots subclass this instead of inheriting from
``BridgeSlotsBase`` directly, so the rule lives in one place. A bridge that
has moved to tentacle (Unity, Marmoset, Substance) sits on tentacle's
``HostBridge`` instead, over the same ``BridgeHost`` API; the Bake Source row
(``BakeSourceSet``) lives there, once for both hosts.
"""

from __future__ import annotations

from uitk.bridge import BridgeSlotsBase


class BlenderBridgeSlotsBase(BridgeSlotsBase):
    """Adds the Blender Scope resolution to :class:`BridgeSlotsBase`."""

    # ------------------------------------------------------------------ scope
    def resolve_scope_objects(self, scope: str):
        """Objects to export for the chosen ``SCOPE`` param.

        :meth:`blendertk.BridgeHost.resolve_scope`, with this panel's bridge as
        the whole-scene hook -- the one resolver every Blender bridge shares, and
        the one tentacle's bridge panels call (``btk.BridgeHost``). Mirror of
        mayatk's (``MayaBridgeSlotsBase.resolve_scope_objects``). ``getattr``:
        a panel that has not built its bridge yet has no ``.bridge`` at all.
        """
        from blendertk.env_utils.handoff_export import BridgeHost

        return BridgeHost.resolve_scope(scope, getattr(self, "bridge", None))

    def scoped_objects(self, params, warn: bool = True):
        """The Scope's objects closed over their hierarchy -- what the send ships.

        Every Blender bridge panel resolves its scope here, so this is where a
        scoped GROUP becomes the group. Blender's FBX writes exactly the objects
        it is handed, never their children, and Maya's export-selection writes
        the subtree: a panel send of a selected Empty used to ship the Empty
        alone (measured on the RizomUV send, whose engine does not close the
        scope itself). The rule is :meth:`blendertk.BridgeHost.scope_closure`'s,
        Visible Only included; the export-mixin and texture bridges apply it
        again in their own produce step for callers that bypass the panel,
        which is idempotent.
        """
        objects = super().scoped_objects(params, warn=warn)
        if not objects:
            return objects
        from blendertk.env_utils.handoff_export import BridgeHost

        return BridgeHost.scope_closure(objects, params)

    def _install_optional_package(self, spec: str) -> None:
        """Install an optional package where Blender will actually import it.

        Overrides the base's ``pip install --user``: Blender's bundled
        interpreter does not put the user-site on ``sys.path``
        (:meth:`blendertk.BridgeHost.install_package`).
        """
        from blendertk.env_utils.handoff_export import BridgeHost

        BridgeHost.install_package(spec)
