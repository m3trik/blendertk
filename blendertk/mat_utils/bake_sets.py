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

* :class:`BakeSourceSet` -- the ONE cross-tool definition of "this file's bake
  source" (the *bake from* geometry). Both texture bridges ship it as a
  companion ``<name>_source.fbx``: Painter wires it as its *Hipoly Mesh*, and
  the Marmoset bake template parents it into the baker's *High* container
  while the scoped selection becomes the bake *target*.
* :class:`LightmapExcludeSet` -- the objects a lightmap bake gives no map of
  their own, while they stay in the render and light the rest.
* :class:`LightmapBakeSet` -- the objects the last lightmap bake acted on, so
  the bake can be re-selected and re-run.

A set is found by its stamp, so its collection's NAME is free: plain
(:attr:`BakeSet.SET_NAME`) or the naming convention's spelling of it (the
``objectSet`` affix, ``_SET`` by default -- :meth:`BakeSet.conventional_name`),
as mayatk names its ``objectSet``.

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

import os
from typing import Any, List, Optional, Tuple


class BakeSet:
    """A file's named object set, stored as a stamped, render-neutral Collection.

    Subclasses name the set (:attr:`SET_NAME`, the collection's name as
    created) and stamp it (:attr:`STAMP`, the custom property it is looked up
    by), and may list stamps it was saved under before (:attr:`LEGACY_STAMPS`);
    reading resolves those transparently and the next :meth:`define` migrates
    the file to the canonical set.
    """

    SET_NAME: str = ""
    #: Custom-property stamp identifying the set's collection.
    STAMP: str = ""
    #: Prior stamps, newest first. Read-only fallback: :meth:`define` restamps
    #: (and renames) the collection it finds under one, and :meth:`clear`
    #: removes those too, so a file never carries two competing definitions.
    #: Mirror of mayatk's ``LEGACY_SET_NAMES`` -- a Maya set is identified by
    #: its name, a Blender one by its stamp.
    LEGACY_STAMPS: Tuple[str, ...] = ()
    #: The ``pythontk.NamingConvention`` entry whose affix a conventional
    #: spelling carries (mirror of mayatk's).
    CONVENTION_KEY: str = "objectSet"

    @classmethod
    def conventional_name(cls) -> str:
        """:attr:`SET_NAME` spelled by the naming convention (``lightmapBaker_baked_SET``).

        The plain name when the convention's set entry is empty.
        """
        import pythontk as ptk

        return ptk.NamingConvention.apply(cls.SET_NAME, cls.CONVENTION_KEY)

    @classmethod
    def name(cls) -> Optional[str]:
        """The name of this file's set collection, or ``None``."""
        col = cls.collection()
        return col.name if col is not None else None

    @classmethod
    def respell(cls, conventional: bool) -> Optional[str]:
        """Rename the set's collection plain or by the convention; the name it ends under.

        One undo step. Blender appends a ``.001`` when another datablock holds
        the name.

        Parameters:
            conventional: Spell it :meth:`conventional_name` rather than
                :attr:`SET_NAME`.

        Returns:
            The collection's name afterwards, or ``None`` when the file has no set.
        """
        from blendertk.core_utils._core_utils import CoreUtils

        col = cls.collection()
        if col is None:
            return None
        wanted = cls.conventional_name() if conventional else cls.SET_NAME
        if col.name != wanted:
            with CoreUtils.undo_chunk(f"Rename {col.name}"):
                col.name = wanted
        return col.name

    @classmethod
    def _collections(cls) -> List[Any]:
        """Every local collection stamped as this set, canonical stamp first."""
        import bpy

        local = [c for c in bpy.data.collections if c.library is None]
        found: List[Any] = []
        for stamp in (cls.STAMP,) + tuple(cls.LEGACY_STAMPS):
            found.extend(c for c in local if stamp in c and c not in found)
        return found

    @classmethod
    def collection(cls):
        """This file's stamped collection, or ``None`` when the file has no set.

        The canonical stamp wins over a legacy one. A stamped collection a
        library links in is the library file's set, not this one's: it is
        read-only here, and adopted it kept the library's objects out of this
        file's bake and made :meth:`define` raise. It is passed over, as a Maya
        reference's namespaced set is by mayatk's.
        """
        found = cls._collections()
        return found[0] if found else None

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
        return TextureBaker.resolve_meshes(members, descendants=True)

    @classmethod
    def define(
        cls, objects: Optional[List[Any]] = None, conventional: bool = False
    ) -> List[Any]:
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
            conventional: Name the collection :meth:`conventional_name` (the
                naming convention's Set affix) rather than :attr:`SET_NAME`
                -- on every define, so an existing set takes the name asked
                for. The set is found by its stamp either way.

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
        name = cls.conventional_name() if conventional else cls.SET_NAME
        with CoreUtils.undo_chunk(f"Define {name}"):
            if not resolved:
                cls._remove()
                return []
            found = cls._collections()
            col = found[0] if found else None
            # A second stamped collection is a competing definition (a legacy
            # one beside the canonical, or an appended copy); the one read wins.
            for extra in found[1:]:
                cls._remove_collection(extra)
            if col is None:
                col = bpy.data.collections.new(name)
                bpy.context.scene.collection.children.link(col)
            else:  # redefining replaces membership wholesale
                for obj in list(col.objects):
                    cls._rehome_if_last(col, obj)
                    col.objects.unlink(obj)
            # Migrate a set saved under a prior stamp: the canonical stamp
            # from here on, as mayatk's define recreates a legacy set.
            for stamp in cls.LEGACY_STAMPS:
                if stamp in col:
                    del col[stamp]
            if col.name != name:
                col.name = name
            col[cls.STAMP] = True
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
        """:meth:`clear`'s work, without its undo step (``define`` holds its own).

        Every stamped collection goes, legacy ones included, as mayatk's clear
        deletes every name the set was ever saved under.
        """
        for col in cls._collections():
            cls._remove_collection(col)

    @classmethod
    def _remove_collection(cls, col) -> None:
        """Remove one set collection, re-homing any object it alone held."""
        import bpy

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


class BakeSourceSet(BakeSet):
    """The file's bake source, shared by the Substance and Marmoset bridges.

    Mirror of mayatk's ``BakeSourceSet`` (the same set name). Lived in
    ``substance_bridge`` as ``HighPolySet``; promoted here once the Marmoset
    bake needed the same concept -- one file, one bake-source definition, every
    bridge agrees on it. A file saved under that class's stamp is adopted
    (:attr:`LEGACY_STAMPS`). Source/target vocabulary because the set is often
    a texture-set donor at matching resolution, not a high-poly mesh.

    A bridge ships :meth:`meshes`, not the bare members: Blender's FBX export
    writes exactly the objects it is handed, never a subtree, so a member that
    groups the source would otherwise send an Empty and none of its geometry.
    """

    SET_NAME = "bakeBridge_source"
    STAMP = "btk_bake_source"
    LEGACY_STAMPS = ("btk_substance_high_poly",)
    #: Suffix appended to an export stem for the companion bake-source file.
    #: One convention across every bridge (see :meth:`companion_path`).
    FILE_SUFFIX = "_source"

    @classmethod
    def companion_path(cls, export_path: str) -> str:
        """``.../asset.fbx`` -> ``.../asset_source.fbx``.

        The single source of truth for where a bridge's bake-source companion
        export lands relative to its main export -- Substance and Marmoset both
        derive their paths here so the convention can't drift.

        Parameters:
            export_path: The bridge's main export path.

        Returns:
            The companion's path, beside it.
        """
        stem, ext = os.path.splitext(export_path)
        return f"{stem}{cls.FILE_SUFFIX}{ext}"


class LightmapExcludeSet(BakeSet):
    """Objects every lightmap bake of the file gives no map of their own.

    Excluded objects stay in the render: Cycles still traces them, so they
    keep casting shadows and bouncing light onto the objects that DO bake --
    the way to keep a static object's shadow without baking it, since a mesh
    neither baked nor excluded moves and is out of the render
    (``LightmapBaker._out_of_render``).
    Only their own bake is skipped, and a bake reverts nothing first, so a map
    baked earlier (a hero prop at a higher preset) survives a room re-bake.
    Read by :meth:`blendertk.LightmapBaker.bake_targets`, so the panel and a
    headless bake of the same file skip the same objects. Mirror of mayatk's
    ``LightmapExcludeSet`` (the same set name).
    """

    SET_NAME = "lightmapBaker_exclude"
    STAMP = "btk_lightmap_exclude"


class LightmapBakeSet(BakeSet):
    """The objects the last lightmap bake acted on.

    Mirror of mayatk's ``LightmapBakeSet`` (the same set name). Written by
    the Lightmap Baker panel after a bake while its *Save After Bake* switch
    is on: every object the bake was asked to bake -- finished or not -- so
    selecting the set and baking again re-runs that bake. Each bake replaces
    it; the panel names it plain or by the naming convention.
    """

    SET_NAME = "lightmapBaker_baked"
    STAMP = "btk_lightmap_baked"
