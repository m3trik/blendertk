# !/usr/bin/python
# coding=utf-8
"""Symbolic-name -> Blender native-menu resolution for the both-button chord menu.

The Blender analogue of :class:`mayatk.ui_utils.maya_native_menus.MayaNativeMenus`. Maya
*wraps* its live menu: ``MayaNativeMenus`` harvests the real ``QAction`` rows out of Maya's Qt
menu bar and re-hosts them in a floating Qt window. Blender draws its UI in OpenGL — there are no
``QAction`` objects to harvest — so the faithful "wrap, don't recreate" is to invoke Blender's
**own** menu via ``bpy.ops.wm.call_menu`` (see :func:`blendertk.call_native_menu`). This module is
just the routing table + resolver that the both-button menu's ``MenuButton`` targets resolve
through; it authors no menu content.

``import bpy`` is deferred into :meth:`resolve` (only the mode-adaptive *Select* branch needs it),
so this module — and the ``BlenderUiHandler`` that consumes it — import cleanly without a running
Blender (the Qt-only offscreen tests rely on that).
"""


class BlenderNativeMenus:
    """Resolve the chord menu's symbolic node names to Blender ``*_MT_*`` menu idnames.

    Each node name (``"mesh"``, ``"vertex"``, ``"render"`` …) is the bare ``target`` of a
    ``MenuButton`` in ``tentacle/ui/blender_menus`` and the stem of its ``<name>#submenu.ui``
    hover file — exactly Maya's ``select#submenu.ui`` / ``target="select"`` convention. On
    release the marking menu resolves the bare target through ``BlenderUiHandler.can_resolve``
    (membership in :attr:`MENU_MAPPING` / :attr:`SELECT_KEY`) and pops the real Blender menu.

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
        # Armature hub (3-level: armature -> pose -> pose/constraints/ik)
        "armature": "VIEW3D_MT_edit_armature",
        "pose": "VIEW3D_MT_pose",
        "constraints": "VIEW3D_MT_pose_constraints",
        "ik": "VIEW3D_MT_pose_ik",
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

    # The one mode-adaptive node; kept out of MENU_MAPPING so its id is resolved live.
    SELECT_KEY = "select"
    _SELECT_FALLBACK = "VIEW3D_MT_select_object"

    @classmethod
    def names(cls):
        """Every symbolic node name this handler resolves (mapping keys + Select)."""
        return set(cls.MENU_MAPPING) | {cls.SELECT_KEY}

    @classmethod
    def resolve(cls, name):
        """The Blender menu idname for symbolic ``name`` (mode-aware for Select), or ``None``.

        ``bpy`` is imported only for the Select branch, so a non-Select resolve — and every
        import of this module — stays Blender-free.
        """
        if name == cls.SELECT_KEY:
            import bpy

            return cls.SELECT_BY_MODE.get(bpy.context.mode, cls._SELECT_FALLBACK)
        return cls.MENU_MAPPING.get(name)
