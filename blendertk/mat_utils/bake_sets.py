# !/usr/bin/python
# coding=utf-8
"""Scene-stored bake sets: named object sets the bake tools read (Blender).

Mirror of mayatk's ``mat_utils.bake_sets``. Maya stores each set as an
``objectSet``; Blender has no object sets, so :class:`BakeSet` stores one as a
Collection, created stamped and found by that stamp rather than by name (the
:class:`blendertk.display_utils.color_id.ColorId` idiom), so a user's own
collection that happens to share the name is never adopted -- nor a set a
library links in, which is that file's. It saves with the .blend, shows up in
the Outliner, and can't go stale against a file it was never captured in. Each
subclass is one question the file answers:

* :class:`LightmapExcludeSet` -- the objects a lightmap bake gives no map of
  their own, while they stay in the render and light the rest.
* ``substance_bridge.HighPolySet`` -- the Substance bridge's bake source.

**Membership is neutral.** An object renders when ANY collection it is in
renders, so a set collection that rendered would put back in the render every
member the artist had switched off through its own collection -- measured: a
box hidden by its collection's render toggle shadowed the floor under it
(0.955 -> 0.000) once it was added to a plain set collection. The set's
collection is therefore disabled in viewports and renders: it adds nothing to
what is drawn or baked, and a member stays exactly as visible as its own
collections make it (a visible member still casts its shadow). Members are
*added* to the set and never moved out of their own collections -- the set is a
tag, not a re-parent. Blender's Move to Collection (M) unlinks an object from
every collection it is in, this one included, so a moved object leaves the set;
Link to Collection (Shift+M) keeps it.
"""

from typing import Any, List, Optional


class BakeSet:
    """A file's named object set, stored as a stamped, render-neutral Collection.

    Subclasses name the set (:attr:`SET_NAME`, the collection's name as
    created) and stamp it (:attr:`STAMP`, the custom property it is looked up
    by).
    """

    SET_NAME: str = ""
    #: Custom-property stamp identifying the set's collection.
    STAMP: str = ""

    @classmethod
    def collection(cls):
        """This file's stamped collection, or ``None`` when the file has no set.

        A stamped collection a library links in is the library file's set, not
        this one's: it is read-only here, and adopted it kept the library's
        objects out of this file's bake and made :meth:`define` raise. It is
        passed over, as a Maya reference's namespaced set is by mayatk's.
        """
        import bpy

        return next(
            (c for c in bpy.data.collections if cls.STAMP in c and c.library is None),
            None,
        )

    @classmethod
    def exists(cls) -> bool:
        """Whether the set's collection is present in the file."""
        return cls.collection() is not None

    @classmethod
    def members(cls) -> List[Any]:
        """The set's objects (an empty list when there is no set)."""
        col = cls.collection()
        return list(col.objects) if col is not None else []

    @classmethod
    def meshes(cls) -> List[Any]:
        """The mesh objects the set covers -- a member counts its descendants.

        A member is whatever was selected when the set was defined: a mesh, or
        an Empty grouping several. Each resolves through
        :meth:`blendertk.TextureBaker.resolve_meshes` (the one definition of a
        bakeable mesh) after a member has been expanded to what lies under it.
        """
        from blendertk.mat_utils.texture_baker import TextureBaker

        members = cls.members()
        if not members:
            return []
        below = [child for obj in members for child in obj.children_recursive]
        return TextureBaker.resolve_meshes(members + below)

    @classmethod
    def define(cls, objects: Optional[List[Any]] = None) -> List[Any]:
        """Replace the set's contents with *objects* (default: the selection).

        An empty input removes the collection -- "no set" is the absence of the
        collection, so a cleared set never lingers as a confusing empty
        container. ``None`` means "use the selection"; an explicit empty list
        means "clear" -- collapsing the two would make ``define([])`` capture
        whatever happened to be selected.

        One undo step, as mayatk's is: a raw ``bpy.data`` edit pushes none of
        its own, so a Ctrl+Z after Set From Selection would otherwise step back
        past it.

        Parameters:
            objects: Objects or object names; ``None`` for the selection.

        Returns:
            The set's members afterwards (``[]`` once cleared).
        """
        import bpy
        from blendertk.core_utils._core_utils import CoreUtils

        if objects is None:
            objects = CoreUtils.selected_objects()
        resolved = [
            bpy.data.objects.get(o) if isinstance(o, str) else o for o in objects or []
        ]
        resolved = list(dict.fromkeys(o for o in resolved if o is not None))
        with CoreUtils.undo_chunk(f"Define {cls.SET_NAME}"):
            if not resolved:
                cls._remove()
                return []
            col = cls.collection()
            if col is None:
                col = bpy.data.collections.new(cls.SET_NAME)
                col[cls.STAMP] = True
                bpy.context.scene.collection.children.link(col)
            else:  # redefining replaces membership wholesale
                for obj in list(col.objects):
                    cls._rehome_if_last(col, obj)
                    col.objects.unlink(obj)
            # On every define, so a set an older release created visible stops
            # putting its render-hidden members back into the render.
            col.hide_viewport = True
            col.hide_render = True
            for obj in resolved:
                if obj.name not in col.objects:
                    col.objects.link(obj)
        return cls.members()

    @classmethod
    def clear(cls) -> None:
        """Remove the set's collection (one undo step); its objects stay in the file."""
        from blendertk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_chunk(f"Clear {cls.SET_NAME}"):
            cls._remove()

    @classmethod
    def _remove(cls) -> None:
        """:meth:`clear`'s work, without its undo step (``define`` holds its own)."""
        import bpy

        col = cls.collection()
        if col is None:
            return
        for obj in list(col.objects):
            cls._rehome_if_last(col, obj)
        bpy.data.collections.remove(col)

    @staticmethod
    def _rehome_if_last(col, obj) -> None:
        """Link *obj* to the scene root if *col* is its only collection.

        A zero-collection object is orphaned data -- gone from the view layer
        and collected on the next save/load. Members are normally *added* to
        the set while staying in their own collections, so this only bites
        when the user unlinked an object's home afterwards; both the redefine
        path and :meth:`clear` call it, so neither can be the one that loses
        geometry.
        """
        import bpy

        if list(obj.users_collection) == [col]:
            bpy.context.scene.collection.objects.link(obj)


class LightmapExcludeSet(BakeSet):
    """Objects every lightmap bake of the file gives no map of their own.

    Excluded objects stay in the render: Cycles still traces them, so they
    keep casting shadows and bouncing light onto the objects that DO bake.
    Only their own bake is skipped, and a bake reverts nothing first, so a map
    baked earlier (a hero prop at a higher preset) survives a room re-bake.
    Read by :meth:`blendertk.LightmapBaker.bake_targets`, so the panel and a
    headless bake of the same file skip the same objects. Mirror of mayatk's
    ``LightmapExcludeSet`` (the same set name).
    """

    SET_NAME = "lightmapBaker_exclude"
    STAMP = "btk_lightmap_exclude"
