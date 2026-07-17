# !/usr/bin/python
# coding=utf-8
"""Symbolic-name -> Blender native-menu resolution + Qt wrapping for the both-button chord menu.

The Blender analogue of :class:`mayatk.ui_utils.maya_native_menus.MayaNativeMenus`. Maya wraps
its live menu by lifting the real ``QAction`` rows off the menu bar into an
``EmbeddedMenuWidget``; Blender draws its menus in OpenGL, so :meth:`get_menu` builds the same
widget by *harvesting* the menu's Python ``draw`` (see :mod:`.menu_harvest`) — always the live
definition (add-ons and mode included), no hand-authored content. The wrapper is re-filled on
every request, so content tracks the current mode; a draw that fails outside its mode
(``VIEW3D_MT_edit_armature`` derefs ``context.edit_object``) returns ``None`` and the caller
falls back to the hand-authored ``<name>#submenu`` overlay — exactly Maya's fallback.

``import bpy`` and Qt imports are deferred into the methods that need them, so this module —
and the ``BlenderUiHandler`` that consumes it — import cleanly without a running Blender or a
Qt binding (the Qt-only offscreen tests and headless Blender both rely on that).
"""
import pythontk as ptk


class BlenderNativeMenus(ptk.LoggingMixin):
    """Resolve the chord menu's symbolic node names to Blender ``*_MT_*`` menu idnames.

    Each node name (``"mesh"``, ``"vertex"``, ``"render"`` …) is the bare ``target`` of a
    ``MenuButton`` in ``tentacle/ui/blender_menus`` and the stem of its ``<name>#submenu.ui``
    hover file — exactly Maya's ``select#submenu.ui`` / ``target="select"`` convention. On
    release the marking menu resolves the bare target through ``BlenderUiHandler.can_resolve``
    (membership in :attr:`MENU_MAPPING` / :attr:`SELECT_KEY`) and shows the harvested Qt clone
    of that menu in a Switchboard window (pin header, hides with ``key_show`` — Maya parity).

    Two node names are deliberately **not** Blender's own menu-domain word (``uv`` / ``normals``)
    but compound (``mesh_uv`` / ``mesh_normals``): the shared ``ui/`` custom menus already own the
    bare ``uv`` / ``normals`` targets (``materials#submenu``, ``uv#submenu``, ``normals#submenu``),
    and ``can_resolve`` is global — a bare ``uv`` entry here would hijack those. Maya faces the same
    and disambiguates the same way (``edit_mesh`` / ``mesh_display`` / ``mesh_tools`` vs the bare
    ``mesh``). Every id is checked against a live Blender by ``test/blender/blender_menus_check.py``.
    """

    # node name -> Blender menu idname (fixed). Select is mode-adaptive -> SELECT_BY_MODE.
    MENU_MAPPING = {
        "view": "VIEW3D_MT_view",
        "add": "VIEW3D_MT_add",
        "object": "VIEW3D_MT_object",
        # Mesh hub
        "mesh": "VIEW3D_MT_edit_mesh",
        "vertex": "VIEW3D_MT_edit_mesh_vertices",
        "edge": "VIEW3D_MT_edit_mesh_edges",
        "face": "VIEW3D_MT_edit_mesh_faces",
        "mesh_uv": "VIEW3D_MT_uv_map",
        "mesh_normals": "VIEW3D_MT_edit_mesh_normals",
        # Curve hub
        "curve": "VIEW3D_MT_edit_curve",
        "ctrl_points": "VIEW3D_MT_edit_curve_ctrlpoints",
        "segments": "VIEW3D_MT_edit_curve_segments",
        "surface": "VIEW3D_MT_edit_surface",
        # Animation -> Rigging chain (mirrors Maya's Key -> Skeleton branch): startmenu
        # "Animation" -> rig hub -> armature/pose/constraint subcategories. Compound names
        # where the bare word is taken: the shared ui/ custom menus own "animation"/"rigging",
        # and the pose-constraints node owns bare "constraints" (same rule as mesh_uv).
        "object_animation": "VIEW3D_MT_object_animation",
        "armature": "VIEW3D_MT_edit_armature",
        "pose": "VIEW3D_MT_pose",
        "constraints": "VIEW3D_MT_pose_constraints",
        "ik": "VIEW3D_MT_pose_ik",
        "object_constraints": "VIEW3D_MT_object_constraints",
        # Object hub extras — Maya's Deform / Effects startmenu domains. These are submenus
        # of Blender's Object menu, unreachable under the old wm.call_menu wrap (no menu-bar
        # entry of their own) but freely harvestable now.
        "modifiers": "VIEW3D_MT_object_modifiers",
        "quick_effects": "VIEW3D_MT_object_quick_effects",
        # Render hub (topbar siblings — Blender has no Arnold/Stereo)
        "render": "TOPBAR_MT_render",
        "window": "TOPBAR_MT_window",
        "help": "TOPBAR_MT_help",
    }

    # Select's native menu tracks the live component mode — Maya's harvested Select menu adapts the
    # same way. Unmapped modes (sculpt / paint / particle-edit) fall back to the Object-mode menu.
    SELECT_BY_MODE = {
        "OBJECT": "VIEW3D_MT_select_object",
        "EDIT_MESH": "VIEW3D_MT_select_edit_mesh",
        "EDIT_CURVE": "VIEW3D_MT_select_edit_curve",
        "EDIT_SURFACE": "VIEW3D_MT_select_edit_surface",
        "EDIT_ARMATURE": "VIEW3D_MT_select_edit_armature",
        "POSE": "VIEW3D_MT_select_pose",
        "PARTICLE_EDIT": "VIEW3D_MT_select_particle",
    }

    # The mode-adaptive nodes; kept out of MENU_MAPPING so their ids resolve live.
    SELECT_KEY = "select"
    _SELECT_FALLBACK = "VIEW3D_MT_select_object"

    # "Rigging" fronts the armature domain: the Pose menu while posing, the Edit Armature
    # menu otherwise (out of mode it opens poll-greyed via the harvest's edit_object
    # injection — Maya's Skeleton menu is likewise always openable).
    RIG_BY_MODE = {
        "POSE": "VIEW3D_MT_pose",
        "EDIT_ARMATURE": "VIEW3D_MT_edit_armature",
    }
    _RIG_FALLBACK = "VIEW3D_MT_edit_armature"

    # name -> (mode -> idname, fallback). Select mirrors Maya's mode-tracking Select menu.
    MODE_ADAPTIVE = {
        SELECT_KEY: (SELECT_BY_MODE, _SELECT_FALLBACK),
        "rig": (RIG_BY_MODE, _RIG_FALLBACK),
    }

    def __init__(self, log_level: str = "WARNING"):
        super().__init__()
        # name -> EmbeddedMenuWidget. Widgets are cached (window identity, styling);
        # their QMenu CONTENT is re-harvested on every get_menu (mode-live), unlike
        # Maya where the cached wrapper hosts the self-updating real actions.
        self.menus = {}
        self.logger.setLevel(log_level)

    @classmethod
    def names(cls):
        """Every symbolic node name this handler resolves (mapping + mode-adaptive keys)."""
        return set(cls.MENU_MAPPING) | set(cls.MODE_ADAPTIVE)

    @classmethod
    def resolve(cls, name):
        """The Blender menu idname for symbolic ``name`` (mode-aware for Select / Rig), or
        ``None``.

        ``bpy`` is imported only for the mode-adaptive branch, so a fixed-name resolve — and
        every import of this module — stays Blender-free.
        """
        adaptive = cls.MODE_ADAPTIVE.get(name)
        if adaptive:
            import bpy

            by_mode, fallback = adaptive
            return by_mode.get(bpy.context.mode, fallback)
        return cls.MENU_MAPPING.get(name)

    def get_menu(self, name):
        """Build (or refresh) the Qt clone of native menu ``name``; the mirror of
        ``MayaNativeMenus.get_menu``.

        Returns an ``EmbeddedMenuWidget`` whose QMenu was re-filled from a fresh harvest —
        content is correct for the *current* mode on every call. Returns ``None`` when the
        name is unknown or the menu's draw fails / yields no rows right now (mode-dependent
        menus outside their mode) — the caller falls back to the hand-authored
        ``<name>#submenu`` overlay, mirroring Maya's fallback for unbuildable menus.
        """
        idname = self.resolve(name)
        if not idname:
            self.logger.error(f"No mapping found for menu '{name}'")
            return None

        from uitk.widgets.embeddedMenu import EmbeddedMenuWidget, PersistentMenu

        from blendertk.ui_utils import menu_harvest

        widget = self.menus.get(name)
        if widget is None:
            widget = EmbeddedMenuWidget(PersistentMenu(name))
            # Suffixed, never the bare name: MainWindow binds every registered
            # child's objectName as an attribute, and names like 'render'/'window'
            # match QWidget methods. Lookups go through self.menus, not objectName.
            widget.setObjectName(f"{name}_menu")

        try:
            count = menu_harvest.refill_qmenu(widget.menu, idname)
        except Exception as e:
            self.logger.debug(f"Native menu '{name}' ({idname}) harvest failed: {e}")
            count = 0
        if not count:
            # Never cache or return an empty wrapper; a fresh build is attempted on
            # the next call (the mode may match by then).
            if name not in self.menus:
                widget.deleteLater()
            return None

        self.menus[name] = widget
        return widget
