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
from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

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
            # The engines' hook, same as "all" above -- but unconditionally,
            # because it is a STATICMETHOD that consults only the scene. A
            # bridge without the mixin therefore still gets the real answer
            # rather than a second, drifting copy of it here (this WAS that
            # copy; the preview bridge needed the same read and two would have
            # been three). Mirror of mayatk's resolver.
            from blendertk.env_utils.handoff_export import BlenderExportMixin

            return BlenderExportMixin._visible_objects()
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

    # ------------------------------------------------------------------
    # Bake Source set (param-row actions + the live member tooltip)
    # ------------------------------------------------------------------

    #: Param key the row is declared under, so the tooltip is registered only
    #: for a bridge that actually HAS the row (mirrors
    #: ``MayaBridgeSlotsBase.BAKE_SOURCE_KEY``).
    BAKE_SOURCE_KEY = "BAKE_SOURCE_SET"

    @staticmethod
    def _bake_source_set():
        """The stamped-Collection bake-source set the actions below operate on.

        A hook, not a module-level import: ``ui_utils`` is the base every
        bridge's slots inherit from, so importing one bridge's package here
        would make a UI base depend on a specific bridge -- and, because that
        bridge's slots import THIS module, risk a cycle. A bridge that stamps
        its set differently overrides this one method.
        """
        from blendertk.mat_utils.substance_bridge._substance_bridge import HighPolySet

        return HighPolySet

    def live_param_tooltip_blocks(self):
        """Make the Bake Source row report the file's CURRENT members.

        The set is a stamped Collection in the .blend, not panel state, so it
        moves under an open panel -- a new file, a redefine, an unlink in the
        Outliner. A build-time tooltip would describe the set the panel opened
        on, which is exactly the case the user is trying to check.

        Registered as a BLOCK, not a whole tooltip: the row's three buttons each
        carry their own description, and the member list has to reach the one the
        user is actually hovering when they capture a selection -- not just the
        label off to its left.

        Mirrors ``MayaBridgeSlotsBase.live_param_tooltip_blocks``, including its
        extend-don't-replace contract: the hook is a registry, so a subclass that
        has to remember to merge is one that will forget.
        """
        tips = dict(super().live_param_tooltip_blocks() or {})
        # Gated on the row being DECLARED: this base is inherited by every
        # blendertk bridge, and only the material ones carry a bake source.
        # The binder already no-ops on an unknown key, so an ungated entry is
        # harmless -- but it makes the registry describe rows that do not
        # exist, and mayatk's twin gates it for the same reason.
        params = getattr(self.params_module, "PARAMS", {}) or {}
        if self.BAKE_SOURCE_KEY in params:
            tips[self.BAKE_SOURCE_KEY] = self._bake_source_tooltip
        return tips

    def _bake_source_tooltip(self) -> str:
        """The Bake Source set's live member list (appended to each hover target)."""
        try:
            members = self._bake_source_set().members()
        except Exception:  # noqa: BLE001 -- a tooltip must never raise into Qt
            return ""
        return TooltipFormat.stored_items(
            members,
            formatter=lambda o: o.name,
            noun="object(s) in this file's set",
            empty_text="No bake source defined in this file.",
        )

    def set_bake_source_from_selection(self) -> None:
        """Store the current selection as this file's bake source.

        Defining the set IS the opt-in: every send from here on exports it as
        the companion bake-source FBX. There is no second checkbox to tick --
        the pairing this replaced could be silently half-on (a set defined, the
        box left clear), which reads as the tool ignoring you.
        """
        bake_set = self._bake_source_set()
        members = bake_set.define()
        if not members:
            self.bridge.logger.warning(
                "Nothing selected; the bake-source set was cleared."
            )
            return
        self.bridge.logger.info(
            f"Bake Source set: {len(members)} object(s) -> {bake_set.SET_NAME}. "
            f"Sends now ship it as the companion bake source."
        )

    def select_bake_source(self) -> None:
        """Select the high-poly set's members.

        Members outside the active view layer (an excluded collection) can't
        be selected at all -- ``select_set`` raises there -- and one whose
        ``hide_select`` is on silently refuses. Both are reported rather than
        forced: unhiding geometry behind the user's back to satisfy a
        *select* action would be the one thing this feature promises not to
        do. The export itself doesn't care either way.
        """
        import bpy

        members = self._bake_source_set().members()
        if not members:
            self.bridge.logger.warning("This file has no high-poly set.")
            return
        bpy.ops.object.select_all(action="DESELECT")
        selected = []
        for obj in members:
            try:
                obj.select_set(True)
            except RuntimeError:  # not in the active view layer
                continue
            if obj.select_get():
                selected.append(obj)
        if selected:
            bpy.context.view_layer.objects.active = selected[0]
        unreachable = len(members) - len(selected)
        self.bridge.logger.info(
            f"Selected {len(selected)} high-poly object(s)."
            + (
                f" {unreachable} could not be selected (hidden from selection "
                "or outside the active view layer); they still export."
                if unreachable
                else ""
            )
        )

    def clear_bake_source(self) -> None:
        """Remove the high-poly collection; its objects are left alone."""
        bake_set = self._bake_source_set()
        if not bake_set.exists():
            self.bridge.logger.warning("This file has no high-poly set.")
            return
        bake_set.clear()
        self.bridge.logger.info("High-poly set cleared.")
