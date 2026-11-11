# !/usr/bin/python
# coding=utf-8
"""LOD groups in Blender: read, group, generate and conform to the one
convention -- twin of mayatk's ``LodUtils``.

A LOD group is an object ``<base>`` whose children ``<base>_LOD0..n`` are its
levels, most detailed first (``ptk.LodChain``: the names Unity's importer
reads). Blender has no LOD node, so the group is an Empty that states what an
FBX should make of it: ``fbx_type = "LodGroup"``, the custom property Blender's
FBX exporter writes an Empty's node type from (measured 2026-11-10: a LodGroup
NodeAttribute, which Unreal and Maya read), and ``lod_heights``, its switch
heights (one per level, the cull last).

A level is what the FBX writes: the mesh with its modifier stack applied
(``use_mesh_modifiers``), so triangles are counted on the evaluated mesh and a
generated level is that mesh baked, carrying no modifier of its own. Grouping
keeps every object's own transform (``matrix_basis``) and places the group
where the object's parent space was, so nothing moves and nothing is
re-decomposed.

Adopted, not rebuilt: the Decimate modifier (COLLAPSE), applied off the
evaluated mesh so no operator context is needed -- the best-measured kernel of
the LOD bake-off (2026-11-10: lowest peak deviation in 16 of 20 NIST cases).
``EditUtils.decimate`` was weighed: it takes a percentage of the base mesh, not
a triangle target on the evaluated one.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils

logger = logging.getLogger(__name__)


class LodUtils(ptk.HelpMixin):
    """Read, group, generate and conform LOD groups (``ptk.LodChain``)."""

    #: The custom properties a group Empty carries (see the module notes):
    #: the one ``io_scene_fbx`` reads an Empty's node type from.
    FBX_TYPE = "fbx_type"
    HEIGHTS = "lod_heights"
    #: The :meth:`generate` keywords this host honours (a shared panel greys
    #: the rest): Blender's COLLAPSE decimation takes no border flags.
    GENERATE_OPTIONS = ("levels", "ratio")

    @staticmethod
    def _path(obj) -> str:
        names = []
        while obj is not None:
            names.append(obj.name)
            obj = obj.parent
        return "|" + "|".join(reversed(names))

    @staticmethod
    def _objects(objects) -> List[Any]:
        if objects is None:
            return CoreUtils.selected_objects()
        return [o for o in objects if o is not None]

    @staticmethod
    def triangles(obj) -> Optional[int]:
        """The triangles *obj* writes: its evaluated mesh's (the modifier stack
        applied, as the FBX writes it), or ``None`` for an object that is no
        mesh."""
        if obj is None or obj.type != "MESH" or obj.data is None:
            return None
        evaluated = obj.evaluated_get(CoreUtils._evaluated_depsgraph())
        return CoreUtils._mesh_face_counts(evaluated.data)[0]

    @staticmethod
    def _world_bounds(obj, depsgraph=None):
        """*obj*'s world box with its subtree, as written (evaluated meshes,
        evaluated matrices: an original's ``matrix_world`` is stale until the
        scene evaluates): ``((xmin, ymin, zmin), (xmax, ymax, zmax))``, or
        ``None`` when nothing under it has a box."""
        from mathutils import Vector

        depsgraph = depsgraph or CoreUtils._evaluated_depsgraph()
        corners = []
        for node in (obj, *obj.children_recursive):
            if node.type in ("EMPTY", "LIGHT", "CAMERA"):
                continue
            evaluated = node.evaluated_get(depsgraph)
            corners.extend(evaluated.matrix_world @ Vector(c) for c in evaluated.bound_box)
        if not corners:
            return None
        return tuple(min(c[i] for c in corners) for i in range(3)), tuple(max(c[i] for c in corners) for i in range(3))

    # ------------------------------------------------------------ read
    @classmethod
    def groups(cls, objects=None) -> List[Dict[str, Any]]:
        """The LOD groups among *objects* and below them (the whole scene when
        ``None``), as ``ptk.LodChain.groups`` reads them: each level with its
        triangles and its world box (:meth:`_world_bounds`)."""
        import bpy

        found = {}
        for obj in (objects if objects is not None else bpy.data.objects):
            for node in (obj, *obj.children_recursive):
                found[cls._path(node)] = node
        return ptk.LodChain.groups(cls.nodes(dict(sorted(found.items()))))

    @classmethod
    def nodes(cls, by_path: Dict[str, Any]) -> List[Dict[str, Any]]:
        """``{path: object}`` as ``ptk.LodChain`` reads nodes -- mirror of
        mayatk's: a level with the triangles and world box it writes (the
        evaluated mesh, :meth:`_world_bounds`), counted for levels alone."""
        nodes, depsgraph = [], None
        for path, obj in by_path.items():
            triangles = bounds = None
            if ptk.LodChain.parse(path):
                depsgraph = depsgraph or CoreUtils._evaluated_depsgraph()
                triangles, bounds = cls.triangles(obj), cls._world_bounds(obj, depsgraph)
            nodes.append(
                {"path": path, "parent": path.rpartition("|")[0] or None, "triangles": triangles, "bounds": bounds}
            )
        return nodes

    # ------------------------------------------------------------ the record
    @classmethod
    def heights(cls, group, count: int) -> List[float]:
        """*group*'s switch heights for *count* levels: its ``lod_heights``
        custom property where it states as many, else the default ladder."""
        stated = group.get(cls.HEIGHTS) if group is not None else None
        try:
            heights = [float(h) for h in stated] if stated is not None else []
            if len(heights) == count:
                ptk.LodChain.check_heights(heights)
                return heights
        except (TypeError, ValueError):
            pass
        return ptk.LodChain.default_heights(count)

    @classmethod
    def _heights_at(cls, path: str, count: int) -> List[float]:
        """:meth:`heights` of the group Empty at *path* (``|``-joined, as
        :meth:`groups` names it)."""
        import bpy

        return cls.heights(bpy.data.objects.get(path.rsplit("|", 1)[-1]), count)

    @classmethod
    def export_record(cls, ctx) -> Optional[Any]:
        """The ``lods`` record for the export, or ``None`` when it ships no LOD
        group -- mirror of mayatk's (``FbxUtils.PRODUCERS``)."""
        import bpy

        roots = None
        if ctx.scope:
            roots = [bpy.data.objects.get(str(n).rsplit("|", 1)[-1]) for n in ctx.scope]
            roots = [o for o in roots if o is not None]
        entries = ptk.LodChain.record_groups(cls.groups(roots), ctx.ships, cls._heights_at)
        if not entries:
            return None
        return ptk.SceneRecords.LODS.make({"groups": entries})

    # ------------------------------------------------------------ author
    @classmethod
    def _new_group(cls, name: str, beside, collection):
        """A group Empty named *name* where *beside*'s parent space is: under
        its parent, with its parent inverse, at identity -- so an object moved
        under it with an identity parent inverse keeps its world placement."""
        import bpy

        group = bpy.data.objects.new(name, None)
        collection.objects.link(group)
        group.parent = beside.parent
        group.matrix_parent_inverse = beside.matrix_parent_inverse.copy()
        group[cls.FBX_TYPE] = "LodGroup"
        group.name = name  # a level renamed first has freed the name
        return group

    @classmethod
    def set_heights(cls, group, heights: Sequence[float]) -> None:
        """Set *group*'s switch heights (one per level, the cull last)."""
        ptk.LodChain.check_heights(heights)
        group[cls.HEIGHTS] = [float(h) for h in heights]

    @classmethod
    def _world(cls, obj):
        """*obj*'s world matrix from the authored matrices alone: its parent's
        world, its parent inverse and its own transform (``matrix_world`` is
        only as fresh as the last evaluation, and an object made in the same
        call has none yet). A parent inverse counts only under a parent."""
        if obj.parent is None:
            return obj.matrix_basis.copy()
        return cls._world(obj.parent) @ obj.matrix_parent_inverse @ obj.matrix_basis

    @classmethod
    def _adopt(cls, obj, group) -> None:
        """Parent *obj* under *group*, its world placement and its own
        transform (``matrix_basis``) kept: the parent inverse takes up the
        difference between its old parent space and the group's."""
        from mathutils import Matrix

        if obj.parent is not None:
            before = cls._world(obj.parent) @ obj.matrix_parent_inverse
        else:
            before = Matrix.Identity(4)
        after = cls._world(group)
        obj.parent = group
        obj.matrix_parent_inverse = after.inverted() @ before

    @classmethod
    @CoreUtils.undoable
    def create_group(
        cls,
        objects=None,
        name: Optional[str] = None,
        heights: Optional[Sequence[float]] = None,
    ):
        """Group *objects* (the selection when ``None``), most detailed first,
        as the levels of one LOD group: each renamed ``<base>_LOD<n>`` under a
        new group Empty ``<base>`` beside the first, world placement kept. One
        undo step. Returns the group, or None for fewer than two objects.

        Blender's selection holds no order, so the selection's levels are
        taken most triangles first (the most detailed is LOD0)."""
        levels = cls._objects(objects)
        if objects is None:
            levels.sort(key=lambda o: cls.triangles(o) or 0, reverse=True)
        if len(levels) < 2:
            return None
        parsed = ptk.LodChain.parse(levels[0].name)
        base = name or (parsed[0] if parsed else levels[0].name)
        collection = levels[0].users_collection[0]
        for index, level in enumerate(levels):  # first: frees <base> for the group
            level.name = ptk.LodChain.level_name(base, index)
        group = cls._new_group(base, levels[0], collection)
        for level in levels:
            cls._adopt(level, group)
        cls.set_heights(group, heights or ptk.LodChain.default_heights(len(levels)))
        return group

    @classmethod
    def _reduce_copy(cls, source, name: str, target: int, collection):
        """A copy of *source* named *name*: its evaluated mesh (the modifier
        stack applied, as the FBX writes LOD0) reduced to about *target*
        triangles by a Decimate (COLLAPSE) modifier last on the stack, baked,
        the copy left with no modifier (a kept one would apply again on
        export)."""
        import bpy

        copy = source.copy()
        copy.data = source.data.copy()
        copy.name = name
        collection.objects.link(copy)
        total = cls.triangles(source) or 1
        modifier = copy.modifiers.new("LOD Decimate", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = max(0.0, min(1.0, float(target) / total))
        modifier.use_collapse_triangulate = True
        depsgraph = CoreUtils._evaluated_depsgraph()  # the window's view layer
        reduced = bpy.data.meshes.new_from_object(copy.evaluated_get(depsgraph))
        copy.modifiers.clear()
        old = copy.data
        copy.data = reduced
        reduced.name = name
        if old.users == 0:
            bpy.data.meshes.remove(old)
        return copy

    @classmethod
    def ineligible(
        cls, obj, levels: int = ptk.LodChain.DEFAULT_LEVELS, ratio: float = ptk.LodChain.DEFAULT_RATIO
    ) -> Optional[str]:
        """Why :meth:`generate` leaves *obj* alone, or None -- twin of
        mayatk's: a level is a static copy of it, so a deforming mesh (an
        Armature modifier, shape keys), an instanced one (shared mesh data), a
        moving one (an action, NLA strips, drivers or constraints, itself or
        above), one parented to a bone or vertices (its place is not its
        parent's matrix), one holding other objects, or one too light for a
        lower level needs authored LODs."""
        if obj is None or obj.type != "MESH" or obj.data is None:
            return "no mesh"
        if ptk.LodChain.parse(obj.name):
            return "a LOD level already"
        if obj.data.shape_keys or any(m.type == "ARMATURE" for m in obj.modifiers):
            return "skinned or with blend shapes"
        if obj.data.users > 1:
            return "instanced"
        node = obj
        while node is not None:
            data = node.animation_data
            if (data and (data.action or data.drivers or data.nla_tracks)) or node.constraints:
                return "animated"
            node = node.parent
        if obj.parent is not None and obj.parent_type != "OBJECT":
            return "parented to a bone or vertices"
        if obj.children:
            return "holds other nodes"
        if not ptk.LodChain.ladder(cls.triangles(obj) or 0, levels, ratio):
            return "too few triangles for a lower level"
        return None

    @classmethod
    @CoreUtils.undoable
    def generate(
        cls,
        objects=None,
        levels: int = ptk.LodChain.DEFAULT_LEVELS,
        ratio: float = ptk.LodChain.DEFAULT_RATIO,
        *,
        heights: Optional[Sequence[float]] = None,
    ) -> List[Any]:
        """Make a LOD group of each mesh in *objects* (the selection when
        ``None``): the mesh becomes ``<name>_LOD0`` under a new group Empty
        ``<name>`` (where its parent space was; it keeps its own transform) and
        each lower level a decimated copy beside it, half the last's triangles
        by default (``ptk.LodChain.ladder``), stopping above the floor. A mesh
        :meth:`ineligible` names is skipped and said (a level is a static copy:
        a skinned, animated or instanced mesh needs authored LODs). One undo
        step. Returns the groups made."""
        return cls._generate(objects, levels, ratio, heights=heights)

    @classmethod
    def _generate(
        cls,
        objects=None,
        levels: int = ptk.LodChain.DEFAULT_LEVELS,
        ratio: float = ptk.LodChain.DEFAULT_RATIO,
        *,
        heights: Optional[Sequence[float]] = None,
    ) -> List[Any]:
        """:meth:`generate`'s work, pushing no undo step: the Scene Exporter's
        Export Copies makes groups it removes again, outside the user's history."""
        made, skipped = [], {}
        for mesh in cls._objects(objects):
            reason = cls.ineligible(mesh, levels, ratio)
            if reason is not None:
                if reason != "no mesh":
                    skipped.setdefault(reason, []).append(mesh.name)
                continue
            targets = ptk.LodChain.ladder(cls.triangles(mesh) or 0, levels, ratio)
            if not targets:
                continue
            base = mesh.name
            collection = mesh.users_collection[0]
            mesh.name = ptk.LodChain.level_name(base, 0)
            group = cls._new_group(base, mesh, collection)
            cls._adopt(mesh, group)
            for index, target in enumerate(targets, 1):
                cls._reduce_copy(mesh, ptk.LodChain.level_name(base, index), target, collection)
            count = len(targets) + 1
            cls.set_heights(group, heights if heights and len(heights) == count else ptk.LodChain.default_heights(count))
            made.append(group)
        for reason, names in skipped.items():
            logger.info("Create LODs left %d mesh(es) alone (%s): %s", len(names), reason, ", ".join(sorted(names)))
        return made

    @classmethod
    @CoreUtils.undoable
    def conform(cls, objects=None) -> List[Any]:
        """Turn ``LOD_<n>`` container groups among *objects* (Houdini's, or an
        FBX from Maya's Generate LOD Meshes) into the convention: each
        container's one mesh moves up as ``<mesh>_LOD<n>`` and the emptied
        container goes; a container of several becomes ``<group>_LOD<n>``.
        One undo step. Returns the groups conformed."""
        import bpy

        conformed = []
        by_path = {}
        for obj in (objects if objects is not None else bpy.data.objects):
            for node in (obj, *obj.children_recursive):
                by_path[cls._path(node)] = node
        for group in cls.groups(objects):
            if group["form"] != "containers" or group["issues"]:
                continue
            holder = by_path[group["parent"]]
            for index, path in enumerate(group["levels"]):
                container = by_path[path]
                inside = list(container.children)
                if len(inside) == 1:
                    level = inside[0]
                    cls._adopt(level, holder)
                    # Only Blender's own ".001": a name's dots of its own stay (Wheel.L).
                    level.name = ptk.LodChain.level_name(re.sub(r"\.\d{3,}$", "", level.name), index)
                    bpy.data.objects.remove(container, do_unlink=True)
                else:
                    container.name = ptk.LodChain.level_name(holder.name, index)
            holder[cls.FBX_TYPE] = "LodGroup"
            conformed.append(holder)
        return conformed
