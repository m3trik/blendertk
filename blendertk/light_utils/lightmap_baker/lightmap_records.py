# !/usr/bin/python
# coding=utf-8
"""The scene record a lightmap bake leaves in Blender: markers, manifest, and the files they name.

Mirror of mayatk's ``LightmapRecords``. :class:`~blendertk.LightmapBaker` renders
the maps; :class:`LightmapRecords` records them and answers for them afterwards:

* **Markers** -- a JSON ``lightmapInfo`` custom property on each baked object:
  :meth:`LightmapRecords.commit`, :meth:`LightmapRecords.revert`,
  :meth:`LightmapRecords.baked_objects`,
  :meth:`LightmapRecords.superseding` (what a re-bake leaves behind, set
  aside),
  and :meth:`LightmapRecords.migrate_legacy` for markers older than the
  rect-binding contract.
* **The manifest** -- the ``lightmap_metadata`` record that rides the FBX on the
  ``data_export`` Empty, rebuilt from the markers:
  :meth:`LightmapRecords.export_record` (the ``FbxUtils.PRODUCERS`` entry) and
  :meth:`LightmapRecords.refresh_export_metadata`.
* **The reflection probe** -- the room the bake lit, as an HDR
  (``LightmapBaker.bake_probe``): :meth:`LightmapRecords.probe` /
  :meth:`LightmapRecords.commit_probe`, published in the manifest as ``probe``.
* **Dependencies** -- where the files the markers name are NOW, and rewriting
  the stored folders. The rules are pythontk's generic
  :class:`~pythontk.FileDependencies` (files a record names by name plus a
  recorded folder), which mayatk's twin uses too; this class hands it the
  markers and Blender's own ways of resolving a folder and copying a file.
* **The bake baseline** -- what each map was baked against, so a re-bake
  redoes only what changed (``LightmapBaker.rebake_changed``): every commit
  rolls the private ``ptk.SceneRecords.LIGHTMAP_BAKE_STATE`` record forward
  (``ptk.BakeBaseline.advance``) from :meth:`LightmapRecords.scene_state`;
  :meth:`LightmapRecords.bake_state` reads it back. Mirror of mayatk's.

Every method is a classmethod: the record is file state, so the Texture Path
Editor, the Scene Exporter and the FBX producer read it without building a
baker. The engine surface imports without ``bpy`` (deferred into the calls).
"""

import contextlib
import hashlib
import json
import os
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
)

import pythontk as ptk

if TYPE_CHECKING:
    import numpy as np


class LightmapRecords(ptk.LoggingMixin):
    """Blender's lightmap markers and manifest, and the files they name."""

    #: The identity binding: an object's 0-1 lightmap UVs cover its whole map.
    IDENTITY_SCALE_OFFSET: Tuple[float, float, float, float] = (1.0, 1.0, 0.0, 0.0)

    # Custom-property name stamped on a committed object (JSON). Persisting the
    # record on the object -- not in memory -- is what makes commit
    # non-destructive across save/reload and independent of any baker instance.
    LIGHTMAP_INFO_PROP: str = "lightmapInfo"
    #: mayatk's name for the same key (``mtk.LightmapRecords.LIGHTMAP_INFO_ATTR``),
    #: so a caller spelling either twin's constant reads the same marker.
    LIGHTMAP_INFO_ATTR: str = LIGHTMAP_INFO_PROP

    # ``data_export`` channel: a scene-wide JSON manifest of every lighting-only
    # lightmap, regenerated from the per-object markers and ridden into the FBX
    # (informational; consumed by unitytk's optional Unity-native binder). The
    # key and schema of the ``ptk.SceneRecords.LIGHTMAPS`` record -- stamped by
    # the declaration, never spelled here.
    LIGHTMAP_METADATA: str = ptk.SceneRecords.LIGHTMAPS.key
    LIGHTMAP_METADATA_VERSION: int = ptk.SceneRecords.LIGHTMAPS.version

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------

    @staticmethod
    def _object(obj):
        """*obj* as an object datablock (a name resolves), or ``None``."""
        import bpy

        return bpy.data.objects.get(obj) if isinstance(obj, str) else obj

    @classmethod
    def _marked_objects(cls, objects=None) -> List[Any]:
        """Objects carrying a marker: ``None`` -> all in the file; else the given subset."""
        import bpy

        prop = cls.LIGHTMAP_INFO_PROP
        if objects is None:
            return [o for o in bpy.data.objects if prop in o]
        out = []
        for o in ptk.make_iterable(objects):
            obj = cls._object(o)
            if obj is not None and prop in obj:
                out.append(obj)
        return out

    @classmethod
    def _marker_info(cls, obj) -> Dict[str, Any]:
        """*obj*'s marker as a dict (``{}`` if absent or unparsable)."""
        obj = cls._object(obj)
        if obj is None or cls.LIGHTMAP_INFO_PROP not in obj:
            return {}
        try:
            return json.loads(obj[cls.LIGHTMAP_INFO_PROP] or "{}")
        except (ValueError, TypeError):
            return {}

    @classmethod
    def _write_marker(cls, obj, info: Dict[str, Any]) -> None:
        """Store *info* as *obj*'s marker."""
        cls._object(obj)[cls.LIGHTMAP_INFO_PROP] = json.dumps(info)

    # ------------------------------------------------------------------
    # Folder hints -- private, never on a marker
    # ------------------------------------------------------------------

    @staticmethod
    def _hint_key(map_name) -> str:
        """A map's key in the folder record: its lower-case file name."""
        return os.path.basename(str(map_name or "")).lower()

    @classmethod
    def _folder_hints(cls) -> Dict[str, str]:
        """``{lower-case map name: stored folder}`` -- where each map was written.

        The private ``ptk.SceneRecords.LIGHTMAP_DIRS`` record, not the marker: a
        marker is an object property and rides every FBX, so a folder on it put
        build-setup data on the deliverable. A LINKED library's maps are read
        from its own record as well (:meth:`_folder_hint`). Mirror of mayatk's.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        data = ptk.SceneRecords.LIGHTMAP_DIRS.load(DataNodes, {})
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}

    @classmethod
    def _save_folder_hints(cls, hints: Dict[str, str]) -> None:
        """Store *hints* as the folder record (an empty one clears it)."""
        from blendertk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.LIGHTMAP_DIRS.save(DataNodes, dict(sorted(hints.items())))

    @classmethod
    def _library_folder_hints(cls, library) -> Dict[str, str]:
        """The folder record a linked *library* keeps in ITS scene data -- where
        a library baked in its own file records its maps -- resolved ABSOLUTE:
        spelled from the LIBRARY's project, which this file does not share
        (mirror of mayatk's ``_module_folder_hints``). ``{}`` when it has none.
        One linked read per library file and mtime
        (``DataNodes._library_scene_values``), which leaves nothing behind."""
        import bpy

        from blendertk.node_utils.data_nodes import DataNodes

        spec = ptk.SceneRecords.LIGHTMAP_DIRS
        try:
            raw = DataNodes._library_scene_values(library).get(spec.key)
        except Exception:  # noqa: BLE001 - an unreadable library resolves as none
            cls.logger.debug("No folder record read from %s.", library, exc_info=True)
            return {}
        data = spec.decode(raw, {}) if isinstance(raw, str) else {}
        if not isinstance(data, dict):
            return {}
        # From THIS file, a library linked through another included: Blender
        # re-spells an indirect library's path from the host (measured 5.1),
        # so ``library=library.parent`` would name a file that is not there.
        library_file = bpy.path.abspath(library.filepath)
        base = DataNodes.project_root_of(library_file)
        hints: Dict[str, str] = {}
        for key, folder in data.items():
            folder = str(folder or "")
            if not folder:
                continue
            if folder.startswith("//"):  # before the rule: the library's own folder
                folder = os.path.dirname(
                    bpy.path.abspath(folder.rstrip("/") + "/_", library=library)
                )
            hints[str(key)] = ptk.FileUtils.resolve_portable_path(folder, base)
        return hints

    @staticmethod
    def _library_of(obj):
        """The library *obj* comes from: its own when linked, its reference's
        when it is a library override; else ``None``."""
        library = getattr(obj, "library", None)
        if library is None:
            override = getattr(obj, "override_library", None)
            reference = getattr(override, "reference", None) if override else None
            library = getattr(reference, "library", None)
        return library

    @classmethod
    def _folder_hint(
        cls,
        info: Dict[str, Any],
        hints: Dict[str, str],
        obj=None,
        libraries: Optional[Dict[int, Dict[str, str]]] = None,
    ) -> str:
        """The stored folder of the map *info* names.

        This file's record first (a repath here speaks for every reader of the
        map), then the record of the library *obj* comes from -- the library
        first, then the one that linked it (``parent``); *libraries* caches
        them across one pass -- then a LEGACY marker's own ``dir``, read until
        :meth:`migrate_folder_hints` lifts it. Mirror of mayatk's.
        """
        key = cls._hint_key(info.get("map"))
        folder = hints.get(key)
        library = cls._library_of(obj) if obj is not None else None
        libraries = {} if libraries is None else libraries
        while not folder and library is not None:
            uid = library.session_uid
            if uid not in libraries:
                libraries[uid] = cls._library_folder_hints(library)
            folder = libraries[uid].get(key)
            library = library.parent
        return str(folder or info.get("dir") or "")

    @classmethod
    def _prune_folder_hints(cls) -> None:
        """Drop the folder and writer entries no marker names any more (a revert)."""
        hints, writers = cls._folder_hints(), cls._writers()
        if not (hints or writers):
            return
        named = {cls._hint_key(i.get("map")) for _o, i in cls._marker_records()}
        probe = cls.probe()
        if probe:
            named.add(cls._hint_key(probe["map"]))
        kept = {k: v for k, v in hints.items() if k in named}
        if kept != hints:
            cls._save_folder_hints(kept)
        kept = {k: v for k, v in writers.items() if k in named}
        if kept != writers:
            cls._save_writers(kept)

    # ------------------------------------------------------------------
    # Writers -- which file wrote each map
    # ------------------------------------------------------------------

    @classmethod
    def _writers(cls) -> Dict[str, str]:
        """``{lower-case map name: stored .blend}`` -- who wrote each map.

        The ``ptk.SceneRecords.LIGHTMAP_WRITERS`` record, stamped by
        :meth:`commit` (mirror of mayatk's). ``""`` is a map committed while
        the file was unsaved -- its own while it still is; the first save
        stamps it with the file written (``DataNodes.install_path_rebase``).
        A map with no entry is nobody's to set aside.
        """
        from blendertk.node_utils.data_nodes import DataNodes

        data = ptk.SceneRecords.LIGHTMAP_WRITERS.load(DataNodes, {})
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v or "") for k, v in data.items()}

    @classmethod
    def _save_writers(cls, writers: Dict[str, str]) -> None:
        """Store *writers* as the writer record (an empty one clears it)."""
        from blendertk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.LIGHTMAP_WRITERS.save(DataNodes, dict(sorted(writers.items())))

    @classmethod
    def _marker_records(cls, objects=None) -> List[Tuple[Any, Dict[str, Any]]]:
        """``[(object, marker info)]`` for every marked object in scope.

        *objects* (names or datablocks) scopes to those objects AND their
        descendants (an export set names roots; the lightmapped meshes sit
        under them). ``None`` is the whole file; an empty list is nothing.
        """
        if objects is None:
            scoped = cls._marked_objects(None)
        else:
            seen: set = set()
            scoped = []
            for o in ptk.make_iterable(objects):
                root = cls._object(o)
                if root is None:
                    continue
                for obj in (root, *root.children_recursive):
                    if obj.name in seen or cls.LIGHTMAP_INFO_PROP not in obj:
                        continue
                    seen.add(obj.name)
                    scoped.append(obj)
        records: List[Tuple[Any, Dict[str, Any]]] = []
        for obj in sorted(scoped, key=lambda o: o.name):
            info = cls._marker_info(obj)
            if info.get("map"):
                records.append((obj, info))
        return records

    @classmethod
    def baked_objects(cls, objects=None) -> List[str]:
        """The objects :meth:`revert` would take the lightmap from (names).

        Those of *objects* that carry a lightmap marker, or, with ``None``,
        every marked object in the file. What a confirmation states before a
        revert runs (mirror of mayatk's).
        """
        return [obj.name for obj in cls._marked_objects(objects)]

    @classmethod
    def lightmap_info(cls, obj) -> Dict[str, Any]:
        """*obj*'s committed lightmap -- its marker plus where the map is NOW.

        The marker's own keys (``map``, ``uv_set``, ``intensity``,
        ``scaleOffset`` -- the identity for a marker that predates it --
        ``mode``) plus ``path``: the map resolved on disk by
        :meth:`lightmap_dependencies`' rules, ``None`` when it is nowhere.
        ``{}`` when *obj* (a name or an object) carries no marker. Mirror of
        mayatk's: what :meth:`transfer_lightmaps` reads instead of
        parsing the marker itself.
        """
        found = cls._object(obj)
        info = dict(cls._marker_info(found)) if found is not None else {}
        if not info.get("map"):
            return {}
        info.setdefault("scaleOffset", list(cls.IDENTITY_SCALE_OFFSET))
        info["path"] = next(
            (d["path"] for d in cls._resolve([found]) if found.name in d["owners"]),
            None,
        )
        return info

    # ------------------------------------------------------------------
    # Commit / revert
    # ------------------------------------------------------------------

    @classmethod
    def commit(
        cls,
        mapping: Dict[str, str],
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        intensity: float = 1.0,
        written: bool = True,
        cells: Optional[Dict[str, Dict[str, Any]]] = None,
        lighting: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Record a lighting-only bake (changes nothing about the material/UVs).

        Per object stamps the marker and records the map's folder and writer
        (this file) in the private records, then republishes the scene-wide
        manifest onto the shared ``data_export`` carrier so it rides the FBX.
        Files are never touched: a re-bake sets aside the maps it superseded
        around its commit (:meth:`superseding`). Last, the bake baseline steps
        forward (:meth:`bake_state`): each recorded object as baked now,
        against the file as it stands (:meth:`scene_state`) -- what a later
        re-bake of the changed objects diffs against. A baseline that cannot be
        read or written is logged and never costs the commit. Mirror of
        mayatk's.

        Parameters:
            mapping: ``{object name: lightmap path}``.
            scale_offsets: ``{object name: [scaleX, scaleY, offsetX, offsetY]}``
                -- THE atlas binding, the per-instance rect the engine applies
                (Unity ``lightmapScaleOffset``; glTF ``KHR_texture_transform``).
                Absent entries are the identity (a map of its own).
            intensity: Recorded in the marker, informationally: the multiplier
                the maps' texels already carry.
            written: Whether THIS file wrote the maps (a bake). ``False`` binds
                objects to maps that already exist -- a lightmap carried to
                another object -- and leaves the writer record alone, so a
                later re-bake never sets aside a file another file still
                reads (:meth:`superseding`).
            cells: ``{object name: {"cell": [sx, sy, ox, oy], "extent": [w,
                h], "size": px, "packing": "atlas" | "per_object"}}`` -- where
                in its map each object was written: its atlas cell, the extent
                the cell was sized for and the map's size. Only an object whose
                cell is recorded can be written back into it later
                (``LightmapBaker._in_place_plan``); any key may be absent.
            lighting: The lighting the bake rendered with
                (``LightmapBaker._lighting_state``); ``None`` records none, and a
                later lighting change then stales nothing.

        Returns:
            ``{object name: lightmap path}`` for each object recorded (a name
            with no object in the scene is skipped).
        """
        from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET, UvUtils

        scale_offsets = scale_offsets or {}
        cells = cells or {}
        recorded: Dict[str, str] = {}
        baked: Dict[str, Dict[str, Any]] = {}
        folders: Dict[str, str] = {}
        for name, path in mapping.items():
            obj = cls._object(name)
            if obj is None:
                continue
            lm = UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            so = scale_offsets.get(name) or cls.IDENTITY_SCALE_OFFSET
            # Where the map lives goes in the PRIVATE folder record, never on
            # the marker: the marker is an object property and rides every FBX,
            # and a folder there is build-setup data on the deliverable. The
            # PORTABLE spelling (relative to the file's own project; mirrors
            # mayatk), resolved on the machine that builds (:meth:`search_dirs`).
            folders[cls._hint_key(path)] = cls._portable_dir(path)
            info = {
                "map": os.path.basename(path),
                "uv_set": lm,
                "intensity": float(intensity),
                "scaleOffset": [float(v) for v in so],
                "mode": "separated",
            }
            # A LEGACY marker's UV remap (see :meth:`migrate_legacy`) is still in
            # the UVs until something restores it, so a re-commit carries its
            # record forward: dropped, the remap would be invisible to revert
            # and migration alike.
            rect = cls._marker_info(obj).get("uvRect")
            if rect and [float(v) for v in rect] != list(cls.IDENTITY_SCALE_OFFSET):
                info["uvRect"] = [float(v) for v in rect]
            cls._write_marker(obj, info)
            recorded[name] = path
            baked[obj.name] = dict(cells.get(name) or {}, map=info["map"])

        if recorded:
            hints = cls._folder_hints()
            hints.update(folders)
            cls._save_folder_hints(hints)
            # ...and that THIS file wrote them: what lets a later re-bake
            # delete them once superseded (:meth:`superseding`).
            if written:
                from blendertk.node_utils.data_nodes import DataNodes

                writers = cls._writers()
                writers.update(dict.fromkeys(folders, DataNodes.writer_stamp()))
                cls._save_writers(writers)
            cls._publish()
            cls._advance_bake_state(baked, lighting)
        return recorded

    # ------------------------------------------------------------------
    # The bake baseline -- what each map was baked against
    # ------------------------------------------------------------------

    @classmethod
    def bake_state(cls) -> Dict[str, Any]:
        """This file's bake baseline, decoded (``ptk.BakeBaseline.decode``).

        ``{"version", "serial", "objects", "lightings"}``: per object (its
        name) the state its map was baked against, when it last baked, its
        atlas cell, and the lighting it saw -- the private
        ``ptk.SceneRecords.LIGHTMAP_BAKE_STATE`` record on ``data_internal``,
        rolled forward by every :meth:`commit`. Never on a marker: a marker
        rides the FBX, and this is build state. An empty record (serial 0)
        when the file has none or it cannot be read. Mirror of mayatk's.
        """
        stored = ptk.SceneRecords.LIGHTMAP_BAKE_STATE.load(cls._data_nodes(), None)
        return ptk.BakeBaseline.decode(stored)

    @classmethod
    def scene_state(cls, objects=None) -> Dict[str, Any]:
        """The file as a bake sees it, for ``ptk.BakeBaseline``: every static
        participant's state, role and marker.

        The participants are what a bake renders as the room
        (``LightmapBaker._out_of_render``): every object carrying a marker,
        the :class:`~blendertk.LightmapExcludeSet`'s meshes and the
        :class:`~blendertk.LightmapIsolateSet`'s -- each keyed by its object
        name. Per object (:meth:`_object_state`): its world matrix and box,
        fingerprints of its evaluated object-space points and of its lightmap
        UV layer (``ptk.BakeBaseline.signature``), its material slots and
        primary material (the atlas group), and whether Cycles renders it into
        the other objects' passes. Its role: ``excluded`` (in the Exclude set,
        which wins), ``isolated``, else ``baked``. Mirror of mayatk's.

        Parameters:
            objects: Limit the read to these objects (names or datablocks) and
                what lies under them; ``None`` reads every participant -- what
                a commit and the stale rule need, since a change anywhere may
                shade an object.

        Returns:
            ``{name: {"state", "role", "marker"}}`` (see ``ptk.BakeBaseline``).
        """
        import bpy

        from blendertk.mat_utils.bake_sets import LightmapExcludeSet, LightmapIsolateSet

        Role = ptk.BakeBaseline.Role
        markers = {obj.name: info for obj, info in cls._marker_records()}
        excluded = {obj.name for obj in LightmapExcludeSet.meshes()}
        isolated = {obj.name for obj in LightmapIsolateSet.meshes()}
        names = list(markers) + sorted((excluded | isolated) - set(markers))
        if objects is not None:
            scoped: set = set()
            for o in ptk.make_iterable(objects):
                root = cls._object(o)
                if root is not None:
                    scoped.update(x.name for x in (root, *root.children_recursive))
            names = [n for n in names if n in scoped]
        from blendertk.core_utils._core_utils import CoreUtils
        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker

        rendered = LightmapBaker._rendered_collections()
        # The window's layer, as the bake renders: windowless, the context's
        # depsgraph is the default layer's and skips what is excluded there.
        depsgraph = CoreUtils._evaluated_depsgraph()
        scene: Dict[str, Any] = {}
        for name in names:
            obj = bpy.data.objects.get(name)
            state = cls._object_state(obj, rendered, depsgraph) if obj else None
            if state is None:
                continue
            info = markers.get(name)
            scene[name] = {
                "state": state,
                "role": (
                    Role.EXCLUDED
                    if name in excluded
                    else Role.ISOLATED
                    if name in isolated
                    else Role.BAKED
                ).value,
                "marker": {
                    "map": info.get("map"),
                    "scaleOffset": info.get(
                        "scaleOffset", list(cls.IDENTITY_SCALE_OFFSET)
                    ),
                }
                if info
                else None,
            }
        return scene

    @classmethod
    def _object_state(
        cls, obj, rendered=None, depsgraph=None
    ) -> Optional[Dict[str, Any]]:
        """*obj*'s state for :meth:`scene_state`, or ``None`` when it has no mesh.

        Read off the EVALUATED mesh (what Cycles renders: its modifiers
        applied) through ``foreach_get``: the object-space points and the
        polygons' vertex loops (``ptk.BakeBaseline.signature``), the world box
        of those points under ``matrix_world``, and the lightmap UV layer's
        loop UVs -- one id per distinct (vertex, uv), Blender's twin of Maya's
        shared UV ids. ``matrix_world`` column by column, its translation at
        ``[12:15]`` (``ptk.BakeBaseline``'s order). Its ``rendered`` is whether
        Cycles renders it into the OTHER objects' passes
        (``LightmapBaker._renders``): one hidden from the render shades
        nothing. *rendered* (``LightmapBaker._rendered_collections``) and
        *depsgraph* (the evaluated one) are passed when the caller read them
        for many objects. Mirror of mayatk's.
        """
        import numpy as np

        from blendertk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
        from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET, UvUtils

        if obj is None or obj.type != "MESH":
            return None
        if depsgraph is None:
            from blendertk.core_utils._core_utils import CoreUtils

            depsgraph = CoreUtils._evaluated_depsgraph()
        if rendered is None:
            rendered = LightmapBaker._rendered_collections()
        evaluated = mesh = None
        try:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            n_verts, n_loops = len(mesh.vertices), len(mesh.loops)
            points = np.empty(n_verts * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", points)
            points = points.astype(np.float64).reshape(-1, 3)
            counts = np.empty(len(mesh.polygons), dtype=np.int64)
            mesh.polygons.foreach_get("loop_total", counts)
            verts = np.empty(n_loops, dtype=np.int64)
            mesh.loops.foreach_get("vertex_index", verts)
            m = np.array(obj.matrix_world, dtype=np.float64)
            matrix = [float(v) for v in m.T.reshape(-1)]  # column by column
            world = points @ m[:3, :3].T + m[:3, 3]
            layout = None
            layer = mesh.uv_layers.get(
                UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            )
            if layer is not None and n_loops:
                uv = np.empty(n_loops * 2, dtype=np.float32)
                layer.data.foreach_get("uv", uv)
                keys = np.column_stack([verts, uv.reshape(-1, 2).view(np.int32)])
                _unique, first, ids = np.unique(
                    keys, axis=0, return_index=True, return_inverse=True
                )
                layout = ptk.BakeBaseline.signature(
                    uv.astype(np.float64).reshape(-1, 2)[first],
                    counts,
                    ids.reshape(-1),
                    dims=2,
                )
            slots = [s.material.name if s.material else "" for s in obj.material_slots]
            indices = np.zeros(len(mesh.polygons), dtype=np.int64)
            mesh.polygons.foreach_get("material_index", indices)
            materials = hashlib.blake2b(digest_size=16)
            materials.update(json.dumps(slots).encode("utf-8"))
            materials.update(indices.astype("<i8").tobytes())
            return {
                "matrix": matrix,
                "bbox": [world.min(axis=0).tolist(), world.max(axis=0).tolist()]
                if len(world)
                else None,
                "geometry": ptk.BakeBaseline.signature(points, counts, verts),
                "layout": layout,
                "materials": materials.hexdigest(),
                "group": cls._primary_material(obj) or "",
                "rendered": bool(LightmapBaker._renders(obj, rendered)),
            }
        except Exception as error:  # a participant unread is gone, never a raise
            cls.logger.debug("Lightmap baseline: %s not read (%s).", obj.name, error)
            return None
        finally:
            if evaluated is not None and mesh is not None:
                evaluated.to_mesh_clear()

    @staticmethod
    def _primary_material(obj) -> Optional[str]:
        """Name of the material covering the most faces of *obj* -- its atlas group.

        A single-slot object's material wins outright; otherwise the slot most
        polygons use. ``None`` when nothing is assigned. One rule for the
        atlas plan (``LightmapBaker._primary_material``) and the baseline's
        ``group`` (mirror of mayatk's).
        """
        slots = getattr(obj, "material_slots", None)
        if not slots:
            return None
        mats = [s.material for s in slots]
        named = [m for m in mats if m is not None]
        if not named:
            return None
        if len(mats) == 1:
            return mats[0].name
        import numpy as np

        # One bulk read (every participant passes here at each commit): faces
        # per slot, a tie going to the material whose first face comes first.
        polygons = obj.data.polygons
        indices = np.empty(len(polygons), dtype=np.int64)
        polygons.foreach_get("material_index", indices)
        slots, first, per_slot = np.unique(
            indices, return_index=True, return_counts=True
        )
        counts: Dict[str, int] = {}
        for _first, slot, count in sorted(zip(first, slots, per_slot)):
            if 0 <= slot < len(mats) and mats[slot] is not None:
                name = mats[slot].name
                counts[name] = counts.get(name, 0) + int(count)
        return max(counts, key=counts.get) if counts else named[0].name

    @classmethod
    def _missing_maps(cls) -> List[str]:
        """The marked objects whose map is on disk nowhere (:meth:`_reads`)."""
        return [name for name, _map, path in cls._reads() if not path]

    @classmethod
    def _advance_bake_state(
        cls,
        baked: Dict[str, Dict[str, Any]],
        lighting: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Roll the bake baseline forward over *baked* (``ptk.BakeBaseline.advance``).

        *baked*: ``{name: {"map", "cell", "extent", "size", "packing"}}`` --
        what :meth:`commit` just recorded. Logged, never raised: the markers
        are committed by now, and a baseline that cannot be written costs only
        the next re-bake's precision. Mirror of mayatk's.
        """
        try:
            spec = ptk.SceneRecords.LIGHTMAP_BAKE_STATE
            store = cls._data_nodes()
            record = ptk.BakeBaseline.advance(
                spec.load(store, None),
                cls.scene_state(),
                baked=baked,
                lighting=lighting,
            )
            spec.save(store, record)
        except Exception as error:
            cls.logger.warning(
                "Lightmap bake baseline not recorded (%s): Re-bake Changed will "
                "not see this bake's objects as baked.",
                error,
            )

    @classmethod
    def _inherit_bake_state(cls, pairs: Dict[str, Tuple[str, bool]]) -> None:
        """Give each carried target its source's bake (:meth:`transfer_lightmaps`).

        *pairs*: ``{target: (source, rebound)}`` (names). A target's map was
        baked when, and under the lighting, its source's was; a REBOUND one
        also reads the very cell its source owns, so it takes the cell with it
        -- a resampled one has a map of its own. Mirror of mayatk's.
        """
        try:
            spec = ptk.SceneRecords.LIGHTMAP_BAKE_STATE
            store = cls._data_nodes()
            record = ptk.BakeBaseline.decode(spec.load(store, None))
            entries = record["objects"]
            for target, (source, rebound) in pairs.items():
                if target not in entries or source not in entries:
                    continue
                inherited = ["baked_at", "lighting"]
                if rebound:
                    inherited += ["cell", "extent", "size", "packing"]
                else:
                    entries[target].update(
                        cell=None, extent=None, size=None, packing="per_object"
                    )
                entries[target].update({k: entries[source][k] for k in inherited})
            spec.save(store, ptk.BakeBaseline.encode(record))
        except Exception as error:
            cls.logger.warning("Lightmap bake baseline not carried over (%s).", error)

    @classmethod
    def revert(cls, objects=None) -> List[str]:
        """Undo :meth:`commit` -- restore any legacy UV remap, drop the markers, republish.

        Current commits change nothing about the material/UVs (the atlas rect is
        a ``scaleOffset`` binding, not a UV edit), so reverting them just drops
        the marker. A LEGACY atlas commit squeezed the object's lightmap UVs
        into its rect (recorded as the marker's ``uvRect``); that was a UV
        change, so it is inverted here first -- restoring the original 0-1
        layout so a re-bake starts clean. The baked texture and UV layer are
        otherwise left in place. ``objects=None`` clears every marked object.
        One undo step (mirror of mayatk's chunk), so a single Ctrl+Z puts every
        marker, the manifest and the folder record back -- a raw ``bpy.data``
        edit pushes none of its own. When no marker is left, the reflection
        probe's record and the bake baseline (:meth:`bake_state`) go too; a
        partial revert leaves the baseline, which then reads the objects it
        took out as gone from the room, so their neighbours read as changed.
        Returns the names cleared.
        """
        from blendertk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_chunk("Revert Lightmaps"):
            return cls._revert_markers(objects)

    @classmethod
    def _revert_markers(cls, objects=None) -> List[str]:
        """:meth:`revert`'s work, inside its undo step."""
        cleared = []
        for obj in cls._marked_objects(objects):
            info = cls._marker_info(obj)
            cls._restore_lightmap_uvs(obj, info)
            del obj[cls.LIGHTMAP_INFO_PROP]
            cleared.append(obj.name)
        if cleared and not cls._marked_objects(None):
            # The probe was the bake's: with no bake left, nothing reads it --
            # nor any baseline: a bake from here is a first bake.
            ptk.SceneRecords.LIGHTMAP_PROBE.clear(cls._data_nodes())
            ptk.SceneRecords.LIGHTMAP_BAKE_STATE.clear(cls._data_nodes())
        if cleared:
            cls._publish()
        return cleared

    # ------------------------------------------------------------------
    # The reflection probe -- the room the bake lit, as an HDR
    # ------------------------------------------------------------------

    @staticmethod
    def _data_nodes():
        """The scene store the records live in (``DataNodes``), imported late."""
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes

    @classmethod
    def probe(cls) -> Optional[Dict[str, Any]]:
        """This file's reflection probe, or ``None`` (mirror of mayatk's).

        ``{"map", "position", "box", "unit_scale"}``: the HDR's file name, the
        point it was captured from and the box its reflections project onto
        (``[[min], [max]]``, or ``None`` for an open scene), in Blender units
        and in the probe's Y-up axes -- glTF's, which Blender's Z-up ones are
        read as ``(x, z, -y)`` (``LightmapBaker.bake_probe``) -- what
        :meth:`commit_probe` recorded. ``unit_scale`` is the metres per unit
        at the BAKE; the manifest publishes the probe labelled with the
        export's own (:meth:`_record`), which the geometry is written in.
        """
        data = ptk.SceneRecords.LIGHTMAP_PROBE.load(cls._data_nodes(), None)
        if not isinstance(data, dict) or not data.get("map"):
            return None
        return {k: data.get(k) for k in ("map", "position", "box", "unit_scale")}

    @classmethod
    def commit_probe(
        cls,
        path: str,
        position: Sequence[float],
        box: Optional[Sequence[Sequence[float]]] = None,
    ) -> Dict[str, Any]:
        """Record the reflection probe the bake wrote to *path*, and republish.

        Mirror of mayatk's. The probe is the room the bake lit, captured as an
        HDR from *position* (``LightmapBaker.bake_probe``): what the lightmaps
        cannot hold -- the reflections on every baked surface, which are a
        metal's whole look, and the light on everything unbaked. Its file's
        folder and writer go in the private records like any map's
        (:meth:`search_dirs` then finds it for a GLB build); the file's
        previous probe file, when it is another file -- another name, or the
        same name in another folder -- is set aside when this file wrote it
        inside its project: the rule :meth:`superseding` applies to maps.

        Parameters:
            path: The probe's EXR.
            position: Where it was captured, in scene units and the probe's
                Y-up axes (see :meth:`probe`).
            box: ``[[min], [max]]`` its reflections project onto, likewise;
                ``None`` for an open scene, read as distant.

        Returns:
            The record (see :meth:`probe`).
        """
        from blendertk.core_utils.units import Units

        previous = cls.probe()
        name = os.path.basename(path)
        record = {
            "map": name,
            "position": [float(c) for c in position],
            "box": [[float(c) for c in corner] for corner in box] if box else None,
            # Metres per Blender unit at the bake: what the numbers were read
            # under. An export labels them with its own (:meth:`_record`).
            "unit_scale": Units.metres_per("internal"),
        }
        data_nodes = cls._data_nodes()
        hints = cls._folder_hints()
        writers = cls._writers()
        # Resolved BEFORE this probe's records replace the previous one's: a
        # re-bake into another folder writes the same NAME, and its record
        # then names the new folder alone -- the old file was left behind,
        # read by nothing.
        superseded = cls._own_probe_file(previous, hints, writers) if previous else None
        ptk.SceneRecords.LIGHTMAP_PROBE.save(data_nodes, record)
        key = cls._hint_key(name)
        hints[key] = cls._portable_dir(path)
        cls._save_folder_hints(hints)
        writers[key] = data_nodes.writer_stamp()
        cls._save_writers(writers)
        if superseded:
            # Kept when it IS the new file, however spelled (the new probe
            # reads it); set aside when it is another.
            retired = ptk.FileDependencies.remove_superseded(
                [superseded], [("", name, path)]
            )
            if retired:
                cls.logger.info(
                    "Set aside the superseded reflection probe %s -- in the "
                    "Recycle Bin, or a temp folder kept 30 days.",
                    retired[0],
                )
        cls._publish()
        return record

    @classmethod
    def _own_probe_file(
        cls,
        probe: Dict[str, Any],
        hints: Dict[str, str],
        writers: Dict[str, str],
    ) -> Optional[str]:
        """*probe*'s file, when it is this file's to set aside once superseded.

        Written by this file (``DataNodes.written_here``: never a Save As
        copy's source, which still reads it), recorded in its folder record,
        on disk, and inside its project (:meth:`project_root`) -- the rules
        :meth:`superseding` holds a map to. ``None`` otherwise.
        """
        name = os.path.basename(str(probe["map"]))
        key = cls._hint_key(name)
        folder = hints.get(key)
        root = cls.project_root()
        if not (folder and root and cls._data_nodes().written_here(writers.get(key))):
            return None
        path = os.path.join(cls._resolved_dir(folder, name), name)
        if os.path.isfile(path) and ptk.FileUtils.is_under(os.path.abspath(path), root):
            return path
        return None

    @classmethod
    def _probe_ref(cls) -> Optional[Tuple[str, str, str]]:
        """The probe as a file reference for :meth:`_resolve`: no owner (it is
        the file's, no object's), its file name and its recorded folder."""
        probe = cls.probe()
        if not probe:
            return None
        name = os.path.basename(str(probe["map"]))
        return ("", name, cls._folder_hints().get(cls._hint_key(name), ""))

    @staticmethod
    def project_root() -> Optional[str]:
        """The project this file's lightmaps belong to; ``None`` without one
        (``DataNodes.scene_project_root``: the workspace blendertk's workspace
        tool names, narrowed to the .blend's own marked project inside it; the
        .blend's own project when a pin does not hold it; a foreign scene
        opened as new resolved as its source). Mirror of mayatk's. A bake
        writes no map outside it (``LightmapBaker.beside_textures``) and sets
        none aside there (:meth:`superseding`).
        """
        from blendertk.node_utils.data_nodes import DataNodes

        return DataNodes.scene_project_root()

    @classmethod
    @contextlib.contextmanager
    def superseding(cls, objects) -> Iterator[List[str]]:
        """Around a re-bake's :meth:`commit`: set aside the maps *objects* stop reading.

        Mirror of mayatk's. A re-bake that changes where or how its maps are
        written -- another output folder, Beside Material Textures, another
        name affix, per-object maps folded into an atlas or back -- leaves the
        old files behind, read by nobody; the maps *objects* read on entry are
        set aside on a clean exit once no marker in the file reads them
        (:meth:`ptk.FileDependencies.remove_superseded`): to the Recycle Bin
        or trash, else the ``superseded`` temp store -- never deleted.
        A block that raises moves nothing.

        Only this file's own maps are candidates: recorded in its folder record
        AND written by it (``DataNodes.written_here``) -- never a map another .blend
        still reads, nor one committed before writers were recorded -- never
        one a LINKED object reads, which its library may name too, and never
        one outside the file's project (:meth:`project_root`): a folder two
        projects share is the other one's too, and no file of this project can
        see that one's reads (mayatk measured the loss, 2026-10-03).

        Parameters:
            objects: The objects (names) the block commits new maps for.

        Yields:
            A list, filled with the set-aside paths on exit.
        """
        retired: List[str] = []
        root = cls.project_root()
        before = [
            p
            for p in cls._written_reads(objects)
            if root and ptk.FileUtils.is_under(os.path.abspath(p), root)
        ]
        yield retired
        if not before:
            return
        retired.extend(ptk.FileDependencies.remove_superseded(before, cls._reads()))
        if retired:
            cls.logger.info(
                "Set aside %d superseded lightmap(s) nothing reads any more -- "
                "in the Recycle Bin, or a temp folder kept 30 days: %s",
                len(retired),
                ", ".join(os.path.basename(p) for p in retired),
            )

    @classmethod
    def _written_reads(cls, objects) -> List[str]:
        """The map files *objects* read now that are this file's to set aside
        once superseded (:meth:`superseding`)."""
        if not objects:
            return []
        from blendertk.node_utils.data_nodes import DataNodes

        hints, writers = cls._folder_hints(), cls._writers()
        readers = cls.claims()
        own: Dict[Optional[str], bool] = {}
        paths: List[str] = []
        for _obj, info in cls._marker_records(list(objects)):
            name = os.path.basename(str(info.get("map") or ""))
            key = cls._hint_key(name)
            writer = writers.get(key)
            if writer not in own:
                own[writer] = DataNodes.written_here(writer)
            if key not in hints or not own[writer]:
                continue
            if any(cls._referenced(o) for o in readers.get(key, ())):
                continue
            path = os.path.join(cls._resolved_dir(hints[key], name), name)
            if os.path.isfile(path):
                paths.append(path)
        return paths

    @classmethod
    def _reads(cls) -> List[Tuple[str, str, Optional[str]]]:
        """``(object name, map name, path)`` per marker: the file each reads NOW.

        Mirror of mayatk's: the marker's own recorded folder when the map is
        there, else where the texture search folders find it (:meth:`_resolve`,
        without the walk), else ``None``.
        """
        hints = cls._folder_hints()
        libraries: Dict[int, Dict[str, str]] = {}
        found = {d["name"].lower(): d["path"] for d in cls._resolve(walk=False)}
        reads: List[Tuple[str, str, Optional[str]]] = []
        for obj, info in cls._marker_records():
            name = os.path.basename(str(info.get("map") or ""))
            folder = cls._folder_hint(info, hints, obj, libraries)
            path = os.path.join(cls._resolved_dir(folder, name), name) if folder else ""
            if not os.path.isfile(path):
                path = found.get(name.lower())
            reads.append((obj.name, name, path))
        return reads

    @classmethod
    def _referenced(cls, name: str) -> bool:
        """Whether the object *name* comes from a library: linked, or overridden."""
        obj = cls._object(name)
        return obj is not None and bool(obj.library or obj.override_library)

    # ------------------------------------------------------------------
    # Legacy markers
    # ------------------------------------------------------------------

    @classmethod
    def migrate_legacy(cls, objects=None) -> List[str]:
        """Bring markers older than the rect-binding contract up to date, losslessly.

        A commit before rect binding packed an atlas by squeezing the object's
        lightmap UVs into its cell and recording the remap as ``uvRect``. The
        migration restores the UVs to their 0-1 layout and folds the rect into
        the object's ``scaleOffset`` (:meth:`ptk.ImgUtils.compose_rect`), so the object
        samples exactly the texels it sampled before -- in the file and in
        every export. Mirror of mayatk's, which also moves a shape marker to
        its transform (Blender has no such split).

        A rect whose recorded UV map the mesh no longer has (deleted, or
        renamed) is dropped with nothing restored or folded: no remap is left
        to undo, and the identity binding samples whatever UVs the object has
        exactly as its map was baked. Kept, it rode the next commit onto the
        UV map that bake builds, for a later migration to invert over a fresh
        unwrap.

        A marker's legacy folder moves to the private record on the way
        (:meth:`migrate_folder_hints`).

        A bake runs this over its own objects before it bakes anything; *objects*
        ``None`` migrates the whole file. Idempotent. Returns the names of the
        objects whose marker changed.
        """
        changed: List[str] = cls.migrate_folder_hints(objects)
        rebound: List[str] = []
        for obj, info in cls._marker_records(objects):
            if "uvRect" not in info:
                continue  # current format
            rect = info.get("uvRect")
            remapped = bool(rect) and [float(v) for v in rect] != list(
                cls.IDENTITY_SCALE_OFFSET
            )
            recorded = info.get("uv_set")
            layers = getattr(getattr(obj, "data", None), "uv_layers", None)
            if remapped and recorded and layers is not None and recorded not in layers:
                cls.logger.info(
                    "%s: the lightmap UV map %r its legacy atlas rect was squeezed "
                    "into is gone; the rect is dropped.",
                    obj.name,
                    recorded,
                )
            elif remapped:
                if not cls._restore_lightmap_uvs(obj, info):
                    cls.logger.warning(
                        "%s: legacy lightmap UVs could not be restored; its marker "
                        "is left as it was.",
                        obj.name,
                    )
                    continue
                info["scaleOffset"] = ptk.ImgUtils.compose_rect(
                    info.get("scaleOffset"), rect
                )
            info.pop("uvRect", None)
            cls._write_marker(obj, info)
            rebound.append(obj.name)
        if rebound:
            cls.logger.info(
                "Migrated %d legacy lightmap marker(s) to the rect binding.",
                len(rebound),
            )
            cls._publish()
        return changed + [name for name in rebound if name not in changed]

    @classmethod
    def migrate_folder_hints(cls, objects=None) -> List[str]:
        """Lift a legacy marker's ``dir`` into the private folder record.

        Until 2026-09-23 every marker carried its map's folder, and a marker is
        an object property that rides every FBX: build-setup data on the
        deliverable. The folder now lives in ``ptk.SceneRecords.LIGHTMAP_DIRS``
        (:meth:`_folder_hints`); this moves an old marker's folder there -- a
        folder the record already holds for that map wins -- and strips it
        from the marker. Lossless and idempotent. A bake runs it through
        :meth:`migrate_legacy`, and every export bracket stages it
        (``FbxUtils.STAGERS``), so a file baked before the move ships clean
        without a re-bake. Mirror of mayatk's.

        Returns:
            The names of the objects whose marker changed.
        """
        legacy = [(o, i) for o, i in cls._marker_records(objects) if "dir" in i]
        if not legacy:
            return []
        hints = cls._folder_hints()
        for obj, info in legacy:
            folder = str(info.pop("dir") or "")
            if folder:
                hints.setdefault(cls._hint_key(info.get("map")), folder)
            cls._write_marker(obj, info)
        cls._save_folder_hints(hints)
        cls.logger.info(
            "Moved %d lightmap marker folder(s) into the file's private record.",
            len(legacy),
        )
        return [obj.name for obj, _info in legacy]

    @staticmethod
    def _transform_lightmap_uvs(obj, uv_set, rect, invert=False) -> None:
        """Affine-transform *obj*'s *uv_set* by a ``[sx, sy, ox, oy]`` rect.

        Forward maps the unit square into the rect (``uv' = uv*s + o``);
        ``invert=True`` applies the exact inverse. The LEGACY primitive: new
        commits never touch UVs (the rect is the engine binding), so its callers
        are the ``uvRect`` restores -- :meth:`migrate_legacy` and :meth:`revert`.
        """
        import numpy as np

        sx, sy, ox, oy = (float(v) for v in rect)
        obj = LightmapRecords._object(obj)
        layer = obj.data.uv_layers.get(uv_set)
        if layer is None:
            raise RuntimeError(f"no lightmap UV set '{uv_set}'")
        data = layer.data
        buf = np.empty(len(data) * 2, dtype=np.float32)
        data.foreach_get("uv", buf)
        uv = buf.reshape(-1, 2)
        if invert:
            uv[:, 0] = (uv[:, 0] - ox) / sx
            uv[:, 1] = (uv[:, 1] - oy) / sy
        else:
            uv[:, 0] = uv[:, 0] * sx + ox
            uv[:, 1] = uv[:, 1] * sy + oy
        data.foreach_set("uv", buf.reshape(-1))
        obj.data.update()

    @classmethod
    def _restore_lightmap_uvs(cls, obj, info: Dict[str, Any]) -> bool:
        """Undo a LEGACY pack-time UV remap recorded on a marker (``uvRect``).

        Returns True when a non-identity rect was present and inverted.
        """
        from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET, UvUtils

        rect = (info or {}).get("uvRect")
        if not rect or [float(v) for v in rect] == list(cls.IDENTITY_SCALE_OFFSET):
            return False
        uv_set = (
            info.get("uv_set") or UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
        )
        try:
            cls._transform_lightmap_uvs(obj, uv_set, rect, invert=True)
        except Exception as e:
            cls.logger.warning(
                "Could not restore atlased lightmap UVs on %s: %s", obj.name, e
            )
            return False
        return True

    @classmethod
    def _stamp_uv_rect(cls, obj, rect: Optional[List[float]]) -> None:
        """Record (or, for the identity, drop) a legacy ``uvRect`` on *obj*'s marker.

        Only :meth:`LightmapBaker.commit_lightmap`'s deprecated ``uv_rects``
        writes one: a caller asserting that it already squeezed these UVs.
        """
        info = cls._marker_info(obj)
        if not info:
            return
        if rect and [float(v) for v in rect] != list(cls.IDENTITY_SCALE_OFFSET):
            info["uvRect"] = [float(v) for v in rect]
        else:
            info.pop("uvRect", None)
        cls._write_marker(obj, info)

    # ------------------------------------------------------------------
    # The export manifest
    # ------------------------------------------------------------------

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``lightmap_metadata`` record for this file, or ``None`` when no
        lightmapped object remains -- the ``ptk.SceneRecords.LIGHTMAPS`` producer
        (``FbxUtils.PRODUCERS``, mirror of mayatk's). Pure: it reads the markers
        and never writes.

        Parameters:
            ctx: The export's decisions; only its ``scope`` is read. A scoped
                export names the export set's objects alone (the record is
                ``export_scoped``): a marker outside it ships its map with
                nothing to bind it to.
        """
        return cls._record(ctx.scope)

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Rebuild the ``lightmap_metadata`` export channel from the file's markers.

        The authoring-time publish of :meth:`export_record`: the record is
        committed through ``FbxUtils.publish_authored`` (a file with no markers
        CLEARS the channel). An export pipeline runs the producer itself
        (``FbxUtils.PRODUCERS``).

        Returns:
            The published JSON string, or ``None`` when cleared.
        """
        return cls._publish()

    @classmethod
    def _publish(cls) -> Optional[str]:
        """Publish :meth:`_record` onto the shared ``data_export`` carrier.

        Regenerating from the markers (not the last bake) keeps incremental bakes
        additive and a revert subtractive. Clears the channel when no lightmapped
        objects remain; never creates the carrier just to write an empty manifest.
        Returns the published JSON string, or ``None`` when cleared.
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        cls._prune_folder_hints()
        record = cls._record()
        FbxUtils.publish_authored({ptk.SceneRecords.LIGHTMAPS: record})
        return record.text if record is not None else None

    @classmethod
    def _record(cls, objects=None) -> Optional[ptk.Record]:
        """(Re)build the lightmap manifest record from the file's markers.

        One entry per marked object, or ``None`` when no lightmapped object
        remains; *objects* scopes it like :meth:`_marker_records` (``None`` =
        the file). camelCase keys match unitytk's ``LightmapRecord``. No
        ``hierarchy``: Blender object names are unique in the file, so the name
        alone tells objects apart.
        """
        entries: List[Dict[str, Any]] = []
        for obj, info in cls._marker_records(objects):
            # Publish the lightmap layer's REAL channel index (mirrors mayatk):
            # Unity's native lightmaps only sample uv2 (index 1), so anything
            # else is warned about instead of hidden behind a hardcoded 1.
            uv_set = info.get("uv_set")
            uv_index = 1
            layers = getattr(getattr(obj, "data", None), "uv_layers", None)
            if layers is not None and uv_set:
                found = layers.find(uv_set)
                if found >= 0:
                    uv_index = found
                else:
                    cls.logger.warning(
                        "%s: committed lightmap layer %r no longer exists; "
                        "publishing uvIndex 1 on faith. Re-run "
                        "create_lightmap_uvs if the layer was renamed or "
                        "removed.",
                        obj.name,
                        uv_set,
                    )
            if uv_index != 1:
                cls.logger.warning(
                    "%s: lightmap layer %r sits at UV index %d, but Unity "
                    "samples uv2 (index 1). Re-run create_lightmap_uvs before "
                    "exporting.",
                    obj.name,
                    uv_set,
                    uv_index,
                )
            entries.append(
                {
                    "name": obj.name,  # the Unity GameObject join key
                    "map": info.get("map"),
                    "uvIndex": uv_index,
                    "intensity": info.get("intensity", 1.0),
                    "scaleOffset": info.get(
                        "scaleOffset", list(cls.IDENTITY_SCALE_OFFSET)
                    ),
                }
            )

        if not entries:
            return None
        # Objects only, no folder (mirror of mayatk's): wherever the manifest
        # becomes a GLB the maps are EMBEDDED, and the host hands that build
        # where they live (:meth:`search_dirs`, from the markers' own portable
        # folders) -- a build-time hint that does not belong in a deliverable.
        # Before 0.8.0 this published the ABSOLUTE authoring folders (``dir`` /
        # ``dirs``); a manifest carrying them still reads. The bake's
        # reflection probe rides beside them, by file name like a map
        # (``ptk.MeshConvert.apply_glb_lightmaps`` embeds it).
        manifest: Dict[str, Any] = {"objects": entries}
        probe = cls.probe()
        if probe:
            # Its Blender units, labelled with the metres per unit NOW -- what
            # the FBX write scales the geometry by (the Scene Exporter sets the
            # units before it produces this) -- never the bake's: a centimetre
            # bake exported in metres labelled its probe 100x too near.
            from blendertk.core_utils.units import Units

            manifest["probe"] = dict(probe, unit_scale=Units.metres_per("internal"))
        return ptk.SceneRecords.LIGHTMAPS.make(manifest)

    # ------------------------------------------------------------------
    # Dependencies -- the maps the markers name, on disk NOW
    # ------------------------------------------------------------------
    #
    # Mirror of mayatk's: a committed lightmap is a texture dependency no Image
    # datablock references -- the marker records a basename plus the folder the
    # bake was COMMITTED from, and that folder is history. These are the one
    # lightmap-side answer the Texture Path Editor, the exporter's path check
    # and the GLB conversion consume, over pythontk's ``FileDependencies``.

    #: How a dependency was located: ``"hint"`` (the marker's own folder),
    #: ``"search"`` (elsewhere -- the hint is stale), ``None`` (nowhere).
    FOUND_BY_HINT: str = ptk.FileDependencies.FOUND_BY_HINT
    FOUND_BY_SEARCH: str = ptk.FileDependencies.FOUND_BY_SEARCH

    @classmethod
    def claims(cls, objects=None) -> Dict[str, FrozenSet[str]]:
        """``{file name: object names}`` -- which objects read which map (mirror of mayatk's).

        What a bake hands ``ptk.FileUtils.unique_path`` (``claims=``) with the
        objects it is writing as ``owners``: a map only the bake's own objects
        read is theirs to replace, so re-baking an object keeps its file name,
        and a map anything else reads -- an object outside the bake, or one the
        bake fails on -- is left alone.
        """
        return ptk.FileDependencies.claims(
            (obj.name, info.get("map")) for obj, info in cls._marker_records(objects)
        )

    @classmethod
    def _resolve(
        cls,
        objects=None,
        search_dirs=None,
        walk: bool = True,
        maps: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """The markers' maps through :meth:`ptk.FileDependencies.resolve`, Blender's way.

        *maps* narrows the read to those file names (any case), the probe's
        included -- a scope by FILE, where *objects* scopes by reader. Mirror
        of mayatk's.
        """
        hints = cls._folder_hints()
        libraries: Dict[int, Dict[str, str]] = {}
        refs = [
            (
                obj.name,
                str(info.get("map") or ""),
                cls._folder_hint(info, hints, obj, libraries),
            )
            for obj, info in cls._marker_records(objects)
        ]
        # The probe is the file's, no object's: a whole-file read lists it,
        # and so does any read holding a baked object -- the manifest carries
        # the probe beside every set of entries, so what ships it must find it.
        probe = cls._probe_ref() if objects is None or refs else None
        if probe:
            refs.append(probe)
        if maps is not None:
            named = {os.path.basename(str(m)).lower() for m in maps}
            refs = [ref for ref in refs if ref[1].lower() in named]
        if not refs:
            return []
        deps = ptk.FileDependencies.resolve(
            refs,
            search_dirs=cls._texture_search_dirs()
            if search_dirs is None
            else search_dirs,
            walk_root=cls._walk_root() if walk else "",
            resolve_hint=cls._resolved_dir,
        )
        for dep in deps:
            dep["owners"] = [o for o in dep["owners"] if o]
        return deps

    @staticmethod
    def _as_lightmap(dep: Dict[str, Any]) -> Dict[str, Any]:
        """A :class:`ptk.FileDependencies` record in the lightmap spelling
        (``map`` / ``objects``) the texture tools read."""
        return {
            "map": dep["name"],
            "dir": dep["dir"],
            "objects": dep["owners"],
            "path": dep["path"],
            "found_by": dep["found_by"],
            "note": dep["note"],
        }

    @classmethod
    def lightmap_dependencies(
        cls, objects=None, search_dirs=None, walk: bool = True
    ) -> List[Dict[str, Any]]:
        """Every lightmap the scene's markers name, resolved on disk NOW.

        One record per unique map::

            {"map": basename, "dir": recorded folder, "objects": [object names],
             "path": absolute path or None, "found_by": "hint" | "search" | None,
             "note": "" | why an unresolved map stayed unresolved}

        Resolution order is the GLB applier's (``ptk.MeshConvert.apply_glb_lightmaps``)
        so the two can never disagree about a map: the map's recorded folder
        (:meth:`_folder_hints`), then *search_dirs* (default :meth:`EnvUtils.texture_search_dirs`),
        each a plain join. With *walk* a map still missing is looked for under
        the whole textures folder; a UNIQUE hit resolves it, several same-named
        files leave it unresolved with the count in ``note`` rather than
        guessed at (:meth:`ptk.FileDependencies.resolve`). Mirror of mayatk's.

        *objects* scopes to those objects and their descendants (``None``: every
        marker in the file). The file's reflection probe comes along whenever
        the scope holds a baked object, as it does in the manifest, with no
        object of its own (``"objects": []``).
        """
        return [cls._as_lightmap(d) for d in cls._resolve(objects, search_dirs, walk)]

    @classmethod
    def search_dirs(cls, objects=None) -> List[str]:
        """Where this scene's lightmaps can be found NOW, for a consumer that joins.

        Mirror of mayatk's: the folders the bake markers' maps resolve to
        FIRST -- most-named first, ties broken on the path -- then
        :meth:`EnvUtils.texture_search_dirs` (the order rule is
        :meth:`ptk.FileDependencies.search_dirs`). The GLB applier's
        ``search_dirs`` joins a basename against the list and takes the first
        hit, so the order is a priority: a texture folder holding a same-named
        atlas from an earlier bake must not win.
        """
        texture_dirs = cls._texture_search_dirs()
        return ptk.FileDependencies.search_dirs(
            cls._resolve(objects, search_dirs=texture_dirs), then=texture_dirs
        )

    @classmethod
    def heal_lightmap_paths(cls, objects=None) -> Dict[str, Any]:
        """Rewrite stale marker hints to where the maps actually are; republish.

        The lightmap half of the exporter's *Resolve Invalid Texture Paths*
        task: a map found by search has a hint that resolves nowhere, so the
        scene's own answer to where its maps live (:meth:`search_dirs`, which
        every GLB build is handed) rests on a guess. Files are never touched.
        Returns ``{"healed": [(map, old_dir, new_dir)], "missing": [records]}``.
        """
        deps = cls.lightmap_dependencies(objects)
        moves: Dict[str, str] = {}
        healed: List[Tuple[str, str, str]] = []
        for dep in deps:
            if dep["path"] and dep["found_by"] == cls.FOUND_BY_SEARCH:
                new_dir = os.path.dirname(dep["path"])
                moves[dep["map"].lower()] = new_dir
                healed.append((dep["map"], dep["dir"], new_dir))
        if moves:
            cls.repath_lightmaps(moves, objects)
        return {"healed": healed, "missing": [d for d in deps if not d["path"]]}

    @classmethod
    def normalize_lightmap_paths(cls, objects=None, relative: bool = True) -> int:
        """Rewrite every in-scope marker's folder to its portable (or absolute) spelling.

        The lightmap half of the Texture Path Editor's *Normalize Paths* /
        *Make Paths Absolute* (mirror of mayatk): files are never touched, the
        folder is re-spelled ``//``-relative when it lies inside the project
        (``relative=True``) or expanded to absolute (``relative=False``), and
        the manifest is republished. Returns how many markers changed.
        """
        dirs_by_map: Dict[str, str] = {}
        hints = cls._folder_hints()
        libraries: Dict[int, Dict[str, str]] = {}
        for obj, info in cls._marker_records(objects):
            basename = os.path.basename(str(info.get("map") or ""))
            folder = cls._resolved_dir(
                cls._folder_hint(info, hints, obj, libraries), basename
            )
            if folder:
                dirs_by_map[basename.lower()] = folder
        if not dirs_by_map:
            return 0
        return cls.repath_lightmaps(dirs_by_map, objects, relative=relative)

    @classmethod
    def relocate_lightmaps(
        cls,
        dest_dir: str,
        source_dir: str = "",
        mode: str = "copy",
        objects=None,
        dry_run: bool = False,
        maps: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Gather the scene's lightmaps into *dest_dir* and repoint the markers.

        The lightmap half of the Texture Path Editor's *Find & Copy*: a map
        that resolves is its own source; one that does not is searched for
        under *source_dir* (recursively; the newest same-named file wins). A
        source already in *dest_dir* needs no file operation and still gets its
        hint rewritten. Relocation goes through the panel's own collision
        policy (``_safe_relocate``: same-size = reuse, different-size = skip).
        *maps* narrows it to those file names -- the reflection probe's too,
        which no object reads -- where *objects* narrows it to their readers
        (mirror of mayatk's).

        Returns::

            {"relocate": [(src, dst)], "in_place": [src], "missing": [records],
             "copied": [(src, dst)], "updated": markers rewritten}
        """
        plan = ptk.FileDependencies.relocate(
            cls._resolve(objects, maps=maps),
            dest_dir,
            source_dir=source_dir,
            mode=mode,
            dry_run=dry_run,
            copy=cls._copy_files,
        )
        result: Dict[str, Any] = dict(
            plan, missing=[cls._as_lightmap(d) for d in plan["missing"]], updated=0
        )
        if dry_run:
            return result
        landed = {os.path.basename(dst).lower() for _src, dst in plan["copied"]}
        landed.update(os.path.basename(p).lower() for p in plan["in_place"])
        if landed:
            folder = ptk.FileUtils.format_path(dest_dir)
            result["updated"] = cls.repath_lightmaps(
                {key: folder for key in landed}, objects
            )
        return result

    @staticmethod
    def _portable_dir(path: str) -> str:
        """The folder of *path* in the spelling the folder record STORES
        (``ptk.FileUtils.portable_path``; mirror of mayatk's): relative to the
        file's own project (``DataNodes.project_root``) wherever a relative
        spelling reaches -- a ``../`` chain to a shared library beside it
        included -- absolute on another drive. The same spelling mayatk
        stores, so the record reads the same on both sides of the bridge; a
        Save As into another project re-spells it."""
        from blendertk.node_utils.data_nodes import DataNodes

        folder = os.path.dirname(os.path.abspath(path))
        spelled = ptk.FileUtils.portable_path(folder, DataNodes.project_root())
        # An absolute UNC share keeps its two leading backslashes
        # (``ptk.FileUtils.format_path``): spelled ``//server/share`` it reads
        # as relative to the .blend wherever ``bpy.path.abspath`` meets it.
        return (
            ptk.FileUtils.format_path(folder) if spelled.startswith("//") else spelled
        )

    @classmethod
    def _resolved_dir(cls, folder: str, basename: str) -> str:
        """*folder* (a stored spelling) as an absolute folder on THIS machine:
        from the file's own project (``ptk.FileUtils.resolve_portable_path``).
        A ``//`` spelling written before that rule is .blend-relative, and is
        read the way an image path is (``bpy.path.abspath``) -- unless only a
        UNC share of that spelling exists, which ``//server/share`` also is.
        ``""`` when nothing is recorded."""
        import bpy

        from blendertk.node_utils.data_nodes import DataNodes

        if not folder:
            return ""
        if not folder.startswith("//"):
            resolved = ptk.FileUtils.resolve_portable_path(
                folder, DataNodes.project_root()
            )
            return ptk.FileUtils.format_path(os.path.normpath(resolved))
        joined = os.path.join(folder, basename or "_")
        try:
            resolved = bpy.path.abspath(joined)
        except Exception:
            resolved = joined
        blend_relative = os.path.dirname(os.path.normpath(resolved))
        if not os.path.isdir(blend_relative) and os.path.isdir(folder):
            return ptk.FileUtils.format_path(folder)  # a UNC share after all
        return ptk.FileUtils.format_path(blend_relative)

    @classmethod
    def _texture_search_dirs(cls) -> List[str]:
        """The workspace's texture folder and the .blend's own folder."""
        from blendertk.env_utils._env_utils import EnvUtils

        return list(EnvUtils.texture_search_dirs())

    @classmethod
    def _walk_root(cls) -> str:
        """The workspace's texture folder: where a map found nowhere else is walked for."""
        from blendertk.env_utils._env_utils import EnvUtils

        return EnvUtils.source_images_dir() or ""

    @staticmethod
    def _copy_files(
        sources: List[str], dest_dir: str, mode: str
    ) -> List[Tuple[str, str]]:
        """Copy or move *sources* under the Texture Path Editor's own collision
        policy (``_safe_relocate``: same size = reuse, different size = skip)."""
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        landed = []
        for src in sources:
            dst = ptk.FileUtils.format_path(
                os.path.join(dest_dir, os.path.basename(src))
            )
            if _MatUtilsInternal._safe_relocate(src, dst, mode) in (
                "relocated",
                "rebind",
            ):
                landed.append((src, dst))
        return landed

    @classmethod
    def repath_lightmaps(
        cls, dirs_by_map: Dict[str, str], objects=None, relative: bool = True
    ) -> int:
        """Point every in-scope marker naming a map in *dirs_by_map* at its new folder.

        Keys are lower-case basenames. The manual repath (Browse for File / a
        typed path on a lightmap row) and the last step of
        :meth:`heal_lightmap_paths` and :meth:`relocate_lightmaps`. Files are
        never touched. The folder is stored -- in the private folder record,
        per map (:meth:`_folder_hints`) -- in its portable spelling
        (``//``-relative when inside the project) unless ``relative=False`` --
        the Make Paths Absolute case; a LEGACY marker's own ``dir`` naming the
        map is lifted off it on the way. Returns how many markers now resolve
        to a different folder; one already there is untouched.
        """
        hints = cls._folder_hints()
        before = dict(hints)
        libraries: Dict[int, Dict[str, str]] = {}
        count = 0
        for obj, info in cls._marker_records(objects):
            basename = os.path.basename(str(info.get("map") or ""))
            new_dir = dirs_by_map.get(basename.lower())
            if new_dir is None:
                continue
            if relative:
                spelling = cls._portable_dir(os.path.join(new_dir, basename))
            else:
                spelling = ptk.FileUtils.format_path(os.path.abspath(new_dir))
            current = cls._folder_hint(info, hints, obj, libraries)
            if ptk.FileUtils.format_path(current) != spelling:
                count += 1
            hints[cls._hint_key(basename)] = spelling
            if "dir" in info:
                info.pop("dir")
                cls._write_marker(obj, info)
        # The probe has no marker and no objects: it follows when its file is
        # named, whatever the scope.
        probe = cls.probe()
        name = os.path.basename(str(probe["map"])) if probe else ""
        new_dir = dirs_by_map.get(name.lower()) if name else None
        if new_dir is not None:
            spelling = (
                cls._portable_dir(os.path.join(new_dir, name))
                if relative
                else ptk.FileUtils.format_path(os.path.abspath(new_dir))
            )
            current = hints.get(cls._hint_key(name), "")
            if ptk.FileUtils.format_path(current) != spelling:
                count += 1
            hints[cls._hint_key(name)] = spelling
        if hints != before:
            cls._save_folder_hints(hints)
        if count:
            cls._publish()
        return count

    @classmethod
    def rename_lightmap(cls, old_name: str, new_name: str) -> int:
        """Re-stamp every record naming map *old_name* with *new_name*.

        Mirror of mayatk's. A lightmap is bound by its file NAME: the markers
        (``info["map"]``), this file's folder and writer records (keyed by the
        lower-case name) and the manifest the deliverable ships all name it,
        so a file renamed on disk read as a missing lightmap. The rename
        follows the file -- which the caller renames
        (``MatUtils.rename_texture_file``); files are never touched here.
        Matched case-insensitively, file-wide (a map is one file whoever reads
        it). The reflection probe's record is re-stamped too when it names the
        file. One undo step.

        Parameters:
            old_name: The map's file name (or path) as the records name it.
            new_name: Its new file name (or path; only the name is kept).

        Returns:
            How many records now name *new_name*: the markers, plus one for
            the probe.

        Raises:
            ValueError: A LINKED object's marker names the map -- its marker
                is its library's, so the rename belongs there; nothing is
                re-stamped.
        """
        from blendertk.core_utils._core_utils import CoreUtils

        old_key = cls._hint_key(old_name)
        new_name = os.path.basename(str(new_name or ""))
        if not old_key or not new_name or os.path.basename(str(old_name)) == new_name:
            return 0
        named = [
            (obj, info)
            for obj, info in cls._marker_records()
            if cls._hint_key(info.get("map")) == old_key
        ]
        linked = [obj.name for obj, _info in named if obj.library is not None]
        if linked:
            raise ValueError(
                f"{old_name} is bound by linked object(s) {', '.join(linked)}: "
                "their markers are their library's -- rename it there."
            )
        count = len(named)
        with CoreUtils.undo_chunk("Rename Lightmap"):
            for obj, info in named:
                info["map"] = new_name
                cls._write_marker(obj, info)
            probe = ptk.SceneRecords.LIGHTMAP_PROBE.load(cls._data_nodes(), None)
            if isinstance(probe, dict) and cls._hint_key(probe.get("map")) == old_key:
                probe["map"] = new_name
                ptk.SceneRecords.LIGHTMAP_PROBE.save(cls._data_nodes(), probe)
                count += 1
            new_key = cls._hint_key(new_name)
            for load, save in (
                (cls._folder_hints, cls._save_folder_hints),
                (cls._writers, cls._save_writers),
            ):
                record = load()
                if old_key in record:
                    record[new_key] = record.pop(old_key)
                    save(record)
            if count:
                cls._publish()
        return count

    # --------------------------------------------------------- transfer
    @classmethod
    def transfer_lightmaps(
        cls,
        targets,
        source,
        *,
        output_dir: Optional[str] = None,
        output_name: Optional[str] = None,
        size: Optional[int] = None,
        supersample: int = 2,
        padding: int = -1,
    ) -> Dict[str, Dict[str, str]]:
        """Carry each source mesh's committed lightmap onto its paired target.

        Same contract as :meth:`mayatk.LightmapRecords.transfer_lightmaps` --
        *targets* / *source* are Blender objects (or names), paired as
        :meth:`TextureTransfer.transfer` pairs them. Per pair: a target whose
        lightmap UV map IS the source's is **rebound** to the same map and atlas rect (nothing
        written); otherwise the source's texels are **resampled** into the
        target's lightmap layout (:meth:`pythontk.UvTransfer.resample_lightmaps`)
        and written as a half-float EXR, ``<output_name>_Lightmap.exr``
        (several targets append each one's name). Either way the target is
        committed (:meth:`commit`) with the source's
        intensity, and takes its source's place in the bake baseline
        (:meth:`bake_state`): baked when and under the lighting its source was
        -- in its source's cell, when rebound. A target with no lightmap UV
        map is GIVEN the source's (the
        pair shares topology: :meth:`UvUtils.export_uv_layout` ->
        :meth:`UvUtils.apply_uv_layout`), and is then rebound -- mirror of
        mayatk. A source with no lightmap is skipped quietly; a missing map or
        source lightmap UV map, with a warning.

        Returns:
            ``{target name: {"path": lightmap path, "how": "rebound" |
            "resampled"}}``.

        Raises:
            ValueError: No targets or sources, or a pair whose topology differs.
        """
        try:
            import numpy  # noqa: F401 -- the resample's array math
        except ImportError:
            raise RuntimeError("numpy is required") from None
        from blendertk.uv_utils._uv_utils import UvUtils
        from blendertk.uv_utils.texture_transfer import TextureTransfer

        targets = [TextureTransfer._mesh(t) for t in ptk.make_iterable(targets)]
        sources = (
            [TextureTransfer._mesh(s) for s in ptk.make_iterable(source)]
            if source is not None
            else []
        )
        if not targets:
            raise ValueError("no target meshes")
        if not sources:
            raise ValueError("a lightmap travels between meshes: name the source(s)")
        if len(sources) > len(targets):
            # Mirror of mayatk: each source's lightmap is its own object's
            # lighting, bound per object -- several cannot become one.
            raise ValueError(
                "a lightmap travels one mesh to one mesh; a target joined "
                "from several sources needs its own (Lightmap Baker)"
            )

        # ---- plan: what each pair carries, before anything is written ----
        checked: List[Tuple[Any, Any, Dict[str, Any], str, Optional[str]]] = []
        for tgt, src in TextureTransfer.pair_sources(targets, sources).items():
            info = cls.lightmap_info(src)
            if not info:
                continue
            if not info.get("path"):
                cls.logger.warning(
                    f"{tgt.name}: the source's lightmap {info['map']!r} is not "
                    "on disk; skipped. Repair its path first."
                )
                continue
            ok, why = TextureTransfer.topology_matches(tgt, src)
            if not ok:
                raise ValueError(f"{tgt.name} / {src.name}: topology differs ({why})")
            if not TextureTransfer.positions_match(tgt, src):
                # Mirror of mayatk: carried anyway, but the lighting is the
                # source's place's.
                cls.logger.warning(
                    f"{tgt.name}: target and source stand in different places; "
                    "it now carries the lighting baked where the source stands."
                )
            s_set = info.get("uv_set")
            # An old marker may name no set at all; bpy's ``in`` rejects None.
            if not s_set or s_set not in src.data.uv_layers:
                s_set = UvUtils.find_lightmap_uv_set(src)
            if not s_set:
                cls.logger.warning(
                    f"{tgt.name}: the source's lightmap UV map is gone; skipped. "
                    "Re-bake the source first (Lightmap Baker)."
                )
                continue
            checked.append((tgt, src, info, s_set, UvUtils.find_lightmap_uv_set(tgt)))

        # ---- a target without lightmap UVs takes the source's layout ------
        # Mirror of mayatk: only once every pair has passed its checks.
        plan: List[Dict[str, Any]] = []
        for tgt, src, info, s_set, t_set in checked:
            if not t_set:
                layout = UvUtils.export_uv_layout([src], uv_set=s_set).get(src.name)
                t_set = (
                    UvUtils.apply_uv_layout({tgt.name: layout}, quiet=True).get(
                        tgt.name
                    )
                    if layout
                    else None
                )
                if not t_set:
                    cls.logger.warning(
                        f"{tgt.name}: could not be given the source's lightmap "
                        "UVs; skipped."
                    )
                    continue
                cls.logger.info(
                    f"{tgt.name}: no lightmap UV map -- given the source's "
                    f"({s_set} -> {t_set})."
                )
            corr = TextureTransfer.correspondence(
                tgt, src, source_uv_set=s_set, target_uv_set=t_set
            )
            plan.append(
                {
                    "owner": tgt.name,
                    "source": src.name,
                    "name": tgt.name,
                    "path": info["path"],
                    "scale_offset": info["scaleOffset"],
                    "intensity": float(info.get("intensity", 1.0)),
                    "src": corr["src_tris"],
                    "dst": corr["dst_tris"],
                }
            )

        # ---- resample the pairs whose layouts differ (pythontk) -----------
        # Mirror of mayatk: resolved only when something is written.
        resample = [
            j for j in plan if not ptk.UvTransfer.layouts_match(j["src"], j["dst"])
        ]
        written: Dict[str, str] = {}
        if resample:
            # The remap's masks and padding go through pythontk's image utils.
            from blendertk.core_utils._core_utils import CoreUtils

            CoreUtils.ensure_image_deps()
            written = ptk.UvTransfer.resample_lightmaps(
                resample,
                output_dir=TextureTransfer.resolve_output_dir(output_dir),
                read=cls._read_lightmap,
                write=cls._write_lightmap,
                output_name=output_name,
                claims=cls.claims(),
                size=size,
                supersample=supersample,
                padding=padding,
                log=cls.logger.info,
            )

        # ---- bind: one commit per (intensity, written) group ---------------
        groups: Dict[Tuple[float, bool], Tuple[Dict[str, str], Dict[str, Any]]] = {}
        for job in plan:
            name, fresh = job["owner"], job["owner"] in written
            mapping, rects = groups.setdefault((job["intensity"], fresh), ({}, {}))
            mapping[name] = written[name] if fresh else job["path"]
            if not fresh:
                rects[name] = job["scale_offset"]
        results: Dict[str, Dict[str, str]] = {}
        for (intensity, fresh), (mapping, rects) in groups.items():
            recorded = cls.commit(mapping, rects, intensity=intensity, written=fresh)
            for name, path in recorded.items():
                results[name] = {
                    "path": path,
                    "how": "resampled" if fresh else "rebound",
                }
        # The bake baseline: a target's map was baked when its source's was.
        cls._inherit_bake_state(
            {
                job["owner"]: (job["source"], job["owner"] not in written)
                for job in plan
                if job["owner"] in results
            }
        )
        return results

    # ----------------------------------------------------- lightmap IO
    @staticmethod
    def _read_lightmap(path: str) -> "np.ndarray":
        """*path*'s texels as top-down float RGB, read through Blender (which
        ships no cv2) the way the Lightmap Baker reads one."""
        import numpy as np

        from blendertk.light_utils.lightmap_baker.lightmap_baker import (
            LightmapBaker,
        )

        img = np.asarray(LightmapBaker._read_exr_top_down(path), dtype=np.float32)
        return img[..., :3] if img.shape[2] >= 3 else np.repeat(img[..., :1], 3, 2)

    @staticmethod
    def _write_lightmap(path: str, image: "np.ndarray") -> None:
        """Write *image* (top-down float) the way the Lightmap Baker writes one."""
        import numpy as np

        from blendertk.light_utils.lightmap_baker.lightmap_baker import (
            LightmapBaker,
        )

        LightmapBaker._write_lightmap_exr(path, np.flipud(image))  # bpy: bottom-up
