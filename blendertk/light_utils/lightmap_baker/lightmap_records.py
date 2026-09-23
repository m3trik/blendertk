# !/usr/bin/python
# coding=utf-8
"""The scene record a lightmap bake leaves in Blender: markers, manifest, and the files they name.

Mirror of mayatk's ``LightmapRecords``. :class:`~blendertk.LightmapBaker` renders
the maps; :class:`LightmapRecords` records them and answers for them afterwards:

* **Markers** -- a JSON ``lightmapInfo`` custom property on each baked object:
  :meth:`LightmapRecords.commit`, :meth:`LightmapRecords.revert`,
  :meth:`LightmapRecords.baked_objects`, and
  :meth:`LightmapRecords.migrate_legacy` for markers older than the
  rect-binding contract.
* **The manifest** -- the ``lightmap_metadata`` record that rides the FBX on the
  ``data_export`` Empty, rebuilt from the markers:
  :meth:`LightmapRecords.export_record` (the ``FbxUtils.PRODUCERS`` entry) and
  :meth:`LightmapRecords.refresh_export_metadata`.
* **Dependencies** -- where the files the markers name are NOW, and rewriting
  the stored folders. The rules are pythontk's generic
  :class:`~pythontk.FileDependencies` (files a record names by name plus a
  recorded folder), which mayatk's twin uses too; this class hands it the
  markers and Blender's own ways of resolving a folder and copying a file.

Every method is a classmethod: the record is file state, so the Texture Path
Editor, the Scene Exporter and the FBX producer read it without building a
baker. The engine surface imports without ``bpy`` (deferred into the calls).
"""

import json
import os
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import pythontk as ptk


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

    # ------------------------------------------------------------------
    # Commit / revert
    # ------------------------------------------------------------------

    @classmethod
    def commit(
        cls,
        mapping: Dict[str, str],
        scale_offsets: Optional[Dict[str, List[float]]] = None,
        intensity: float = 1.0,
    ) -> Dict[str, str]:
        """Record a lighting-only bake (changes nothing about the material/UVs).

        Per object stamps the marker, then republishes the scene-wide manifest
        onto the shared ``data_export`` carrier so it rides the FBX. Files are
        never touched. Mirror of mayatk's.

        Parameters:
            mapping: ``{object name: lightmap path}``.
            scale_offsets: ``{object name: [scaleX, scaleY, offsetX, offsetY]}``
                -- THE atlas binding, the per-instance rect the engine applies
                (Unity ``lightmapScaleOffset``; glTF ``KHR_texture_transform``).
                Absent entries are the identity (a map of its own).
            intensity: Recorded in the marker, informationally: the multiplier
                the maps' texels already carry.

        Returns:
            ``{object name: lightmap path}`` for each object recorded (a name
            with no object in the scene is skipped).
        """
        from blendertk.uv_utils._uv_utils import LIGHTMAP_UV_SET, UvUtils

        scale_offsets = scale_offsets or {}
        recorded: Dict[str, str] = {}
        for name, path in mapping.items():
            obj = cls._object(name)
            if obj is None:
                continue
            lm = UvUtils.find_lightmap_uv_set(obj) or LIGHTMAP_UV_SET
            so = scale_offsets.get(name) or cls.IDENTITY_SCALE_OFFSET
            info = {
                "map": os.path.basename(path),
                # Where the map lives, in the PORTABLE spelling (``//``-relative
                # when inside the project -- the rule textures follow; mirrors
                # mayatk): a teammate's machine mounts the cloud project
                # elsewhere, and an absolute folder resolves nowhere there.
                # Resolved on the machine that builds (:meth:`search_dirs`); the
                # manifest itself names no folder.
                "dir": cls._portable_dir(path),
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

        if recorded:
            cls._publish()
        return recorded

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
        Returns the names cleared.
        """
        cleared = []
        for obj in cls._marked_objects(objects):
            info = cls._marker_info(obj)
            cls._restore_lightmap_uvs(obj, info)
            del obj[cls.LIGHTMAP_INFO_PROP]
            cleared.append(obj.name)
        if cleared:
            cls._publish()
        return cleared

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

        A bake runs this over its own objects before it bakes anything; *objects*
        ``None`` migrates the whole file. Idempotent. Returns the names of the
        objects whose marker changed.
        """
        changed: List[str] = []
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
            changed.append(obj.name)
        if changed:
            cls.logger.info(
                "Migrated %d legacy lightmap marker(s) to the rect binding.",
                len(changed),
            )
            cls._publish()
        return changed

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
            ctx: The export's decisions (unused: the manifest is a function of
                the markers alone).
        """
        return cls._record()

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

        record = cls._record()
        FbxUtils.publish_authored({ptk.SceneRecords.LIGHTMAPS: record})
        return record.text if record is not None else None

    @classmethod
    def _record(cls) -> Optional[ptk.Record]:
        """(Re)build the lightmap manifest record from the file's markers.

        One entry per marked object, or ``None`` when no lightmapped object
        remains. camelCase keys match unitytk's ``LightmapRecord``. No
        ``hierarchy``: Blender object names are unique in the file, so the name
        alone tells objects apart.
        """
        entries: List[Dict[str, Any]] = []
        for obj, info in cls._marker_records():
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
        # ``dirs``); a manifest carrying them still reads.
        return ptk.SceneRecords.LIGHTMAPS.make({"objects": entries})

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
        cls, objects=None, search_dirs=None, walk: bool = True
    ) -> List[Dict[str, Any]]:
        """The markers' maps through :meth:`ptk.FileDependencies.resolve`, Blender's way."""
        refs = [
            (obj.name, str(info.get("map") or ""), str(info.get("dir") or ""))
            for obj, info in cls._marker_records(objects)
        ]
        if not refs:
            return []
        return ptk.FileDependencies.resolve(
            refs,
            search_dirs=cls._texture_search_dirs()
            if search_dirs is None
            else search_dirs,
            walk_root=cls._walk_root() if walk else "",
            resolve_hint=cls._resolved_dir,
        )

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
        so the two can never disagree about a map: the marker's own ``dir``
        hint, then *search_dirs* (default :meth:`EnvUtils.texture_search_dirs`),
        each a plain join. With *walk* a map still missing is looked for under
        the whole textures folder; a UNIQUE hit resolves it, several same-named
        files leave it unresolved with the count in ``note`` rather than
        guessed at (:meth:`ptk.FileDependencies.resolve`). Mirror of mayatk's.
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
        for _obj, info in cls._marker_records(objects):
            basename = os.path.basename(str(info.get("map") or ""))
            folder = cls._resolved_dir(str(info.get("dir") or ""), basename)
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
    ) -> Dict[str, Any]:
        """Gather the scene's lightmaps into *dest_dir* and repoint the markers.

        The lightmap half of the Texture Path Editor's *Find & Copy*: a map
        that resolves is its own source; one that does not is searched for
        under *source_dir* (recursively; the newest same-named file wins). A
        source already in *dest_dir* needs no file operation and still gets its
        hint rewritten. Relocation goes through the panel's own collision
        policy (``_safe_relocate``: same-size = reuse, different-size = skip).

        Returns::

            {"relocate": [(src, dst)], "in_place": [src], "missing": [records],
             "copied": [(src, dst)], "updated": markers rewritten}
        """
        plan = ptk.FileDependencies.relocate(
            cls._resolve(objects),
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
        """The folder of *path* in the spelling a marker STORES: ``//``-relative
        when the map sits inside the project (:func:`btk.to_project_relative`,
        the rule textures follow), absolute otherwise -- so a project mounted
        elsewhere on a teammate's machine still resolves it. Forward slashes,
        except a UNC share's leading two (``ptk.FileUtils.format_path``):
        spelled ``//server/share`` it read as relative to the .blend."""
        from blendertk.mat_utils._mat_utils import MatUtils

        return ptk.FileUtils.format_path(
            os.path.dirname(MatUtils.to_project_relative(os.path.abspath(path)))
        )

    @classmethod
    def _resolved_dir(cls, folder: str, basename: str) -> str:
        """*folder* (a marker's stored spelling) as an absolute folder on THIS
        machine -- a ``//`` path resolved against the open .blend the way an
        image path is (``bpy.path.abspath``). ``""`` when nothing is recorded."""
        import bpy

        if not folder:
            return ""
        joined = os.path.join(folder, basename or "_")
        try:
            resolved = bpy.path.abspath(joined)
        except Exception:
            resolved = joined
        return ptk.FileUtils.format_path(os.path.dirname(os.path.normpath(resolved)))

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
        never touched. The folder is stored in its portable spelling
        (``//``-relative when inside the project) unless ``relative=False`` --
        the Make Paths Absolute case. The manifest is republished once. Returns
        how many markers changed; a marker already recording that folder is
        untouched.
        """
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
            if ptk.FileUtils.format_path(str(info.get("dir") or "")) == spelling:
                continue
            info["dir"] = spelling
            cls._write_marker(obj, info)
            count += 1
        if count:
            cls._publish()
        return count
