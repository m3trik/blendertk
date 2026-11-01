# !/usr/bin/python
# coding=utf-8
"""Display utilities — the exploded-view toggle (mirror of mayatk's
``display_utils.ExplodedView`` workflow: push objects apart for inspection, restore their
exact positions afterwards).

Maya's version runs an iterative repulsive-force arrangement; here the same outcome (no
world-bbox overlaps, restorable) comes from scaling the objects' positions away from the
group's bbox center until nothing overlaps — deterministic and headless-testable. The
pre-explode location is stamped as a custom prop (like the freeze bakes), so the toggle
survives a save/reload.

``import bpy`` / ``mathutils`` are deferred into the call bodies (no import side effects).
"""

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.xform_utils._xform_utils import XformUtils

_ORIG_LOCATION = "btk_explode_orig"  # custom-prop key holding the pre-explode location


class _DisplayUtilsInternal(object):
    """Internal helpers for DisplayUtils."""

    @staticmethod
    def _bboxes_overlap(a, b, margin=0.0):
        (amn, amx), (bmn, bmx) = a, b
        return all(
            amn[i] - margin <= bmx[i] and bmn[i] - margin <= amx[i] for i in range(3)
        )

    @staticmethod
    def _objects(objects):
        """*objects* as a list of objects (names resolved; missing and None dropped)."""
        import bpy

        found = [
            bpy.data.objects.get(o) if isinstance(o, str) else o
            for o in ptk.make_iterable(objects)
        ]
        return [o for o in found if o is not None]

    @staticmethod
    def _scene_objects(kind=None):
        """The current scene's objects, of Blender type *kind* when given."""
        import bpy

        return [
            o for o in bpy.context.scene.objects if kind is None or o.type == kind
        ]

    @staticmethod
    def _spaces(area_type):
        """The active space of every *area_type* area, across every window: the
        display toggles drive them in lockstep, off the first one's state."""
        return [area.spaces.active for area in CoreUtils.get_areas(area_type)]

    @staticmethod
    def _flip_overlay(name):
        """Toggle the 3D viewports' overlay flag *name* in lockstep; the state
        set, or None with no 3D viewport."""
        spaces = _DisplayUtilsInternal._spaces("VIEW_3D")
        if not spaces:
            return None
        state = not getattr(spaces[0].overlay, name)
        for space in spaces:
            setattr(space.overlay, name, state)
        CoreUtils.tag_redraw("VIEW_3D")
        return state

    @staticmethod
    def _toggle_xray(objects):
        """Uniform ``show_in_front`` toggle over *objects*: any off turns them all on."""
        if not objects:
            return None
        state = not all(o.show_in_front for o in objects)
        for o in objects:
            o.show_in_front = state
        return state, len(objects)

    @staticmethod
    def _set_wireframe(objects, state):
        for o in objects:
            o.display_type = "WIRE" if state else "TEXTURED"
        return objects


class DisplayUtils(_DisplayUtilsInternal):
    """Namespace mirror of mayatk's ``DisplayUtils`` (helpers also exposed module-level)."""

    @staticmethod
    def get_smooth_level(objects=None):
        """The subdivision levels of the first object carrying a Subdivision Surface
        modifier among *objects* (None: the active object, then the selection):
        ``{"level": levels, "adaptive_level": render_levels}``, or None without one.
        Mirror of ``mtk.DisplayUtils.get_smooth_level`` (Maya's smooth-preview
        divisions and OpenSubdiv adaptive level): the viewport level is the
        preview, the render level the second one Blender has.
        """
        if objects is None:
            active = CoreUtils.active_object()
            pool = ([active] if active is not None else []) + CoreUtils.selected_objects()
        else:
            pool = list(ptk.make_iterable(objects))
        for obj in pool:
            mod = next(
                (m for m in getattr(obj, "modifiers", ()) if m.type == "SUBSURF"), None
            )
            if mod is not None:
                return {"level": mod.levels, "adaptive_level": mod.render_levels}
        return None

    @staticmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    def set_smooth_level(objects=None, level=None, adaptive_level=None):
        """Set the Subdivision Surface levels of the meshes in *objects* (None: the
        selection), adding the modifier where there is none: *level* the viewport
        level, *adaptive_level* the render level -- mirror of
        ``mtk.DisplayUtils.set_smooth_level`` (``EditUtils.set_subdivision``). One
        undo step.

        Returns:
            (list): The meshes set; ``[]`` when *objects* holds none.
        """
        from blendertk.edit_utils._edit_utils import EditUtils  # a lower part

        return EditUtils.set_subdivision(
            objects, viewport_levels=level, render_levels=adaptive_level
        )

    @staticmethod
    def is_exploded(objects):
        """True when any of the given objects carries an exploded-view origin stamp."""
        return any(_ORIG_LOCATION in o for o in ptk.make_iterable(objects) if o)

    @staticmethod
    def explode_view(objects, step=1.2, margin=0.05, max_iterations=50):
        """Push the given objects apart for inspection — each moves away from the group's bbox
        center along its own bbox-center offset (grown ×``step`` per pass) until no world
        bounding boxes overlap (or ``max_iterations``). Separation is driven by the **geometry**
        centers, not the origins, so frozen objects (origins all at world zero) explode too.
        Each object's location is stamped so :func:`unexplode_view` restores it exactly;
        already-stamped objects are left alone (re-explode safe). Returns the objects moved.
        """
        import bpy
        from mathutils import Vector

        objects = [
            o
            for o in ptk.make_iterable(objects)
            if o and getattr(o, "type", None) == "MESH"
        ]
        if len(objects) < 2:
            return []
        targets = [o for o in objects if _ORIG_LOCATION not in o]
        if not targets:
            return []

        boxes = [XformUtils.get_world_bbox(o) for o in objects]
        mn = Vector(tuple(min(b[0][i] for b in boxes) for i in range(3)))
        mx = Vector(tuple(max(b[1][i] for b in boxes) for i in range(3)))
        center = (mn + mx) / 2.0

        for o in targets:
            o[_ORIG_LOCATION] = list(o.location)

        for _ in range(max_iterations):
            bpy.context.view_layer.update()
            boxes = [XformUtils.get_world_bbox(o) for o in objects]
            if not any(
                _DisplayUtilsInternal._bboxes_overlap(boxes[i], boxes[j], margin)
                for i in range(len(boxes))
                for j in range(i + 1, len(boxes))
            ):
                break
            for n, o in enumerate(targets):
                o_mn, o_mx = XformUtils.get_world_bbox(o)
                d = (o_mn + o_mx) / 2.0 - center
                if (
                    d.length < 1e-6
                ):  # exactly centered geometry — nudge deterministically
                    d = Vector((1e-2 * (n + 1), 0.0, 0.0))
                # `d` is a world-space separation; write it in world space so parented
                # children move along world axes. Reassigning the whole matrix lets Blender
                # back-solve matrix_basis through the parent (an in-place
                # ``matrix_world.translation +=`` is not guaranteed to propagate).
                mw = o.matrix_world.copy()
                mw.translation += d * (step - 1.0)
                o.matrix_world = mw
        bpy.context.view_layer.update()
        return targets

    @staticmethod
    def unexplode_view(objects):
        """Restore the exact pre-explode locations stamped by :func:`explode_view`.
        Returns the objects restored."""
        import bpy
        from mathutils import Vector

        restored = []
        for o in (o for o in ptk.make_iterable(objects) if o):
            if _ORIG_LOCATION in o:
                o.location = Vector(o[_ORIG_LOCATION])
                del o[_ORIG_LOCATION]
                restored.append(o)
        if restored:
            bpy.context.view_layer.update()
        return restored

    @staticmethod
    def unexplode_all():
        """Restore every exploded object in the scene, regardless of selection (mirror of mayatk's
        ``ExplodedView.un_explode_all``). Returns the objects restored."""
        import bpy

        return DisplayUtils.unexplode_view(list(bpy.data.objects))

    @staticmethod
    def get_visible_geometry(objects=None):
        """Mesh objects visible in the current view layer — mirror of mayatk's
        ``DisplayUtils.get_visible_geometry``. ``obj.visible_get()`` already resolves the full
        visibility chain (hide_viewport / hide_get() / collection exclusion), so unlike Maya's
        version this needs no extra templated/animated-visibility flags.

        ``objects=None`` scans the current view layer (the default); pass an explicit list to
        restrict the scan.
        """
        # the window's layer (windowless, ``context.view_layer`` is the scene default)
        vl = CoreUtils._active_view_layer()
        pool = objects if objects is not None else vl.objects
        return [o for o in pool if o.type == "MESH" and o.visible_get(view_layer=vl)]

    @staticmethod
    def set_viewport_overlay(**flags):
        """Set ``View3DOverlay`` properties on every 3D viewport of the file -- each
        workspace's screens, not only the visible ones -- e.g.
        ``set_viewport_overlay(show_relationship_lines=False)``. Returns how many
        viewports changed.

        Overlays are per-viewport state with no per-object switch, so hiding one for
        some content means hiding it in the viewport. A flag the running Blender
        doesn't know raises ``AttributeError`` (a typo must not silently no-op).
        """
        import bpy

        changed = 0
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for space in area.spaces:
                    overlay = getattr(space, "overlay", None)
                    if overlay is None:
                        continue
                    diff = {k: v for k, v in flags.items() if getattr(overlay, k) != v}
                    for key, value in diff.items():
                        setattr(overlay, key, value)
                    changed += bool(diff)
        if changed:
            CoreUtils.tag_redraw("VIEW_3D")
        return changed

    # --- display toggles --------------------------------------------------
    # Twins of mayatk's ``DisplayUtils`` toggles (the Display menu's rows):
    # each answers what it did, or None when there was nothing to act on.
    # Object edits are one undo step; viewport overlays are screen state,
    # which Blender's undo does not hold.

    #: mayatk's node-type names (:meth:`set_visibility`) -> the Blender object
    #: types each stands for; ``"transform"`` is every object.
    VISIBILITY_TYPES = {
        "mesh": ("MESH",),
        "nurbsCurve": ("CURVE",),
        "nurbsSurface": ("SURFACE",),
        "camera": ("CAMERA",),
        "light": ("LIGHT",),
        "locator": ("EMPTY",),
        "joint": ("ARMATURE",),
        "transform": None,
    }

    @staticmethod
    @CoreUtils.undoable
    def set_visibility(
        elements, visibility=True, include_ancestors=True, affect_layers=True
    ):
        """Show or hide *elements* in the window's view layer (the eye,
        ``hide_set``). Mirror of mayatk's ``DisplayUtils.set_visibility``.

        Parameters:
            elements: Objects or names, or one of mayatk's node-type names
                (:attr:`VISIBILITY_TYPES`: ``"mesh"``, ``"light"`` ...) for every
                object of that type in the view layer.
            visibility (bool): Show (True) or hide.
            include_ancestors (bool): Their parents too.
            affect_layers (bool): Every collection of the view layer too --
                Blender's analogue of Maya's display layers.
        """
        vl = CoreUtils.active_view_layer()
        if vl is None:
            return
        names = [e for e in ptk.make_iterable(elements) if isinstance(e, str)]
        types = DisplayUtils.VISIBILITY_TYPES
        if names and all(n in types for n in names):
            kinds = [types[n] for n in names]
            every = any(k is None for k in kinds)
            wanted = {t for k in kinds if k for t in k}
            objects = [o for o in vl.objects if every or o.type in wanted]
        else:
            objects = _DisplayUtilsInternal._objects(elements)
        if affect_layers:
            stack = list(vl.layer_collection.children)
            while stack:
                layer = stack.pop()
                layer.hide_viewport = not visibility
                stack.extend(layer.children)
        targets = list(objects)
        if include_ancestors:
            for o in objects:
                parent = o.parent
                while parent is not None:
                    targets.append(parent)
                    parent = parent.parent
        for o in dict.fromkeys(targets):
            try:
                o.hide_set(not visibility, view_layer=vl)
            except RuntimeError:  # not in this view layer (another scene)
                pass

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def hide_objects(objects=None):
        """Hide *objects* in the window's view layer (the eye, Blender's H).
        Twin of mayatk's ``DisplayUtils.hide_objects``.

        Returns:
            list: The objects hidden; ``[]`` with nothing selected.
        """
        vl = CoreUtils.active_view_layer()
        hidden = []
        for o in _DisplayUtilsInternal._objects(objects):
            try:
                o.hide_set(True, view_layer=vl)
                hidden.append(o)
            except RuntimeError:  # not in this view layer
                pass
        return hidden

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def show_objects(objects=None):
        """Show hidden *objects* in the window's view layer. Twin of mayatk's
        ``DisplayUtils.show_objects``. Blender cannot select a hidden object, so called
        bare (the selection) it shows what is already visible: pass the objects.

        Returns:
            list: The objects shown; ``[]`` with nothing selected.
        """
        vl = CoreUtils.active_view_layer()
        shown = []
        for o in _DisplayUtilsInternal._objects(objects):
            try:
                o.hide_set(False, view_layer=vl)
                shown.append(o)
            except RuntimeError:  # not in this view layer
                pass
        return shown

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def toggle_xray(objects=None):
        """Uniform x-ray (``show_in_front``) toggle: any off turns them all on;
        only a fully x-rayed set turns off. Twin of mayatk's.

        Returns:
            tuple: (applied state, object count), or None when there was
            nothing to operate on; ``[]`` with nothing selected.
        """
        return _DisplayUtilsInternal._toggle_xray(_DisplayUtilsInternal._objects(objects))

    @staticmethod
    @CoreUtils.undoable
    def clear_xray():
        """Turn x-ray off on every object in the scene ("Un-Xray All"). Twin of
        mayatk's ``DisplayUtils.clear_xray``.

        Returns:
            list: The objects cleared.
        """
        objects = _DisplayUtilsInternal._scene_objects()
        for o in objects:
            o.show_in_front = False
        return objects

    @staticmethod
    @CoreUtils.undoable
    def toggle_xray_others(objects=None):
        """Uniform x-ray toggle over every mesh in the scene EXCEPT *objects*
        ("X-Ray Other"). Twin of mayatk's.

        Parameters:
            objects: The objects to leave out; ``None``: the selection, and
                nothing selected toggles every mesh.

        Returns:
            tuple: (applied state, object count), or None when nothing is left.
        """
        mine = set(
            CoreUtils.selected_objects()
            if objects is None
            else _DisplayUtilsInternal._objects(objects)
        )
        others = [
            o for o in _DisplayUtilsInternal._scene_objects("MESH") if o not in mine
        ]
        return _DisplayUtilsInternal._toggle_xray(others)

    @staticmethod
    def cycle_component_ids():
        """Toggle the 3D viewports' index overlay (``show_extra_indices``: the
        selected components' indices in Edit Mode, with Developer Extras on).
        Twin of mayatk's, which steps vertex / edge / face / UV IDs.

        Returns:
            (str/None): ``"on"`` or ``"off"``; None with no 3D viewport.
        """
        state = _DisplayUtilsInternal._flip_overlay("show_extra_indices")
        return None if state is None else ("on" if state else "off")

    @staticmethod
    def cycle_normals_display():
        """Step the 3D viewports' normals overlay: face, vertex, off (shown in
        Edit Mode). Twin of mayatk's, minus its tangents (Blender has no
        tangent overlay). Every viewport follows the first one.

        Returns:
            (str/None): ``"face"``, ``"vertex"`` or ``"off"``; None with no 3D viewport.
        """
        spaces = _DisplayUtilsInternal._spaces("VIEW_3D")
        if not spaces:
            return None
        first = spaces[0].overlay
        if first.show_vertex_normals:
            state, face, vertex = "off", False, False
        elif first.show_face_normals:
            state, face, vertex = "vertex", False, True
        else:
            state, face, vertex = "face", True, False
        for space in spaces:
            space.overlay.show_face_normals = face
            space.overlay.show_vertex_normals = vertex
        CoreUtils.tag_redraw("VIEW_3D")
        return state

    @staticmethod
    def toggle_soft_edge_display():
        """Toggle the 3D viewports' sharp-edge overlay (``show_edge_sharp``).
        Twin of mayatk's soft-edge display.

        Returns:
            (bool/None): The state set; None with no 3D viewport.
        """
        return _DisplayUtilsInternal._flip_overlay("show_edge_sharp")

    @staticmethod
    def toggle_material_override():
        """Toggle the 3D viewports' solid colour between Material and a flat
        Single colour: Maya's default-material override. Any viewport already
        on Single turns them all back. Twin of mayatk's.

        Returns:
            (bool/None): The state set; None with no 3D viewport.
        """
        spaces = _DisplayUtilsInternal._spaces("VIEW_3D")
        if not spaces:
            return None
        state = not any(s.shading.color_type == "SINGLE" for s in spaces)
        for space in spaces:
            space.shading.color_type = "SINGLE" if state else "MATERIAL"
        CoreUtils.tag_redraw("VIEW_3D")
        return state

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def set_wireframe(objects=None, state=True):
        """Draw *objects* as wireframe, or textured again (*state* False:
        ``display_type``). Twin of mayatk's ``DisplayUtils.set_wireframe``.

        Returns:
            list: The objects set; ``[]`` with nothing selected.
        """
        return _DisplayUtilsInternal._set_wireframe(
            _DisplayUtilsInternal._objects(objects), state
        )

    @staticmethod
    @CoreUtils.undoable
    @CoreUtils.selected
    def toggle_wireframe(objects=None):
        """Toggle *objects* between wireframe and textured, together: the active
        object's state decides (the first one's when it is not among them;
        mayatk's lead is its first selected). Twin of mayatk's.

        Returns:
            (bool/list): True when they are now wireframe; ``[]`` with nothing selected.
        """
        objects = _DisplayUtilsInternal._objects(objects)
        if not objects:
            return []
        active = CoreUtils.active_object()
        lead = active if active in objects else objects[0]
        wire = lead.display_type != "WIRE"
        _DisplayUtilsInternal._set_wireframe(objects, wire)
        return wire

    @staticmethod
    @CoreUtils.undoable
    def toggle_wireframe_inactive():
        """Draw every unselected mesh in the scene as wireframe, or textured
        again when any already is: Maya's wireframe-on-inactive. Twin of mayatk's.

        Returns:
            bool: The state set (True: the unselected meshes are wireframe).
        """
        selected = set(CoreUtils.selected_objects())
        inactive = [
            o for o in _DisplayUtilsInternal._scene_objects("MESH") if o not in selected
        ]
        state = not any(o.display_type == "WIRE" for o in inactive)
        _DisplayUtilsInternal._set_wireframe(inactive, state)
        return state

    @staticmethod
    @CoreUtils.undoable
    def toggle_local_axes(objects=None, joints=False):
        """Toggle local axes display, the lead (the first) deciding the state for
        all. Mirror of mayatk's ``DisplayUtils.toggle_local_axes``
        (``displayLocalAxis``).

        Parameters:
            objects (obj/list): The objects (or names); ``None``: the selection.
            joints (bool): Toggle the bone axes (``Armature.show_axes``) of the
                armatures among them; called bare with none selected, of every
                armature in the scene. False: the objects' own axes
                (``Object.show_axis``).

        Returns:
            (bool/None): True when the axes now show; None with nothing to toggle.
        """
        bare = objects is None
        objects = (
            CoreUtils.selected_objects()
            if bare
            else _DisplayUtilsInternal._objects(objects)
        )
        if joints:
            armatures = [o for o in objects if o.type == "ARMATURE"]
            if not armatures and bare:
                armatures = _DisplayUtilsInternal._scene_objects("ARMATURE")
            datas = list(dict.fromkeys(a.data for a in armatures))
            if not datas:
                return None
            state = not datas[0].show_axes
            for data in datas:
                data.show_axes = state
            return state
        if not objects:
            return None
        state = not objects[0].show_axis
        for obj in objects:
            obj.show_axis = state
        return state

    @staticmethod
    @CoreUtils.undoable
    def toggle_template(objects=None):
        """Template *objects*: wireframe, not rendered, not selectable -- Maya's
        template. Twin of mayatk's ``DisplayUtils.toggle_template``.

        Blender deselects an object made unselectable, so a templated object
        can never be selected to untemplate it: called bare with nothing
        selected, this releases every templated object in the scene (wireframe
        AND unselectable, a pair only this sets together).

        Returns:
            (tuple/list): ``(templated, objects)``; ``[]`` with nothing to do.
        """
        objects = (
            CoreUtils.selected_objects()
            if objects is None
            else _DisplayUtilsInternal._objects(objects)
        )
        if objects:
            for o in objects:
                o.display_type = "WIRE"
                o.hide_render = True
                o.hide_select = True  # deselects it, as a side effect
            return True, objects
        templated = [
            o
            for o in _DisplayUtilsInternal._scene_objects()
            if o.hide_select and o.display_type == "WIRE"
        ]
        for o in templated:
            o.hide_select = False
            o.hide_render = False
            o.display_type = "TEXTURED"
        return (False, templated) if templated else []

    #: The generated image :meth:`toggle_uv_checker` shows in the UV editors.
    UV_CHECKER_IMAGE = "uv_checker_grid"

    @staticmethod
    def toggle_uv_checker():
        """Toggle a checker in the UV editors: a generated ``UV_GRID`` image
        swapped into every open Image / UV editor (Blender has no checker
        overlay). The first editor's own image is stashed on the scene and put
        back on toggle-off. Twin of mayatk's ``DisplayUtils.toggle_uv_checker``.

        Returns:
            (bool/None): The state set; None with no UV editor open.
        """
        import bpy

        spaces = _DisplayUtilsInternal._spaces("IMAGE_EDITOR")
        if not spaces:
            return None
        name = DisplayUtils.UV_CHECKER_IMAGE
        scene = bpy.context.scene
        first = spaces[0].image
        state = not (first and first.name == name)
        if state:
            image = bpy.data.images.get(name)
            if image is None:
                image = bpy.data.images.new(name, 1024, 1024)
                image.generated_type = "UV_GRID"
            scene["_uv_checker_prev"] = first.name if first else ""
        else:
            image = bpy.data.images.get(scene.get("_uv_checker_prev") or "")
        for space in spaces:
            space.image = image
        CoreUtils.tag_redraw("IMAGE_EDITOR")
        return state

    @staticmethod
    def toggle_uv_distortion():
        """Step the UV editors' stretch overlay: angle, area, off. Twin of
        mayatk's (Maya's distortion shading is on / off). Every editor follows
        the first one.

        Returns:
            (str/None): ``"angle"``, ``"area"`` or ``"off"``; None with no UV editor open.
        """
        spaces = _DisplayUtilsInternal._spaces("IMAGE_EDITOR")
        if not spaces:
            return None
        first = spaces[0].uv_editor
        if not first.show_stretch:
            show, kind, state = True, "ANGLE", "angle"
        elif first.display_stretch_type == "ANGLE":
            show, kind, state = True, "AREA", "area"
        else:
            show, kind, state = False, first.display_stretch_type, "off"
        for space in spaces:
            space.uv_editor.show_stretch = show
            space.uv_editor.display_stretch_type = kind
        CoreUtils.tag_redraw("IMAGE_EDITOR")
        return state
