# !/usr/bin/python
# coding=utf-8
"""The Blender scene store -- mirror of mayatk's ``node_utils.data_nodes``.

Same class name, same ``ptk.SceneStoreBase`` contract, so a producer ported
across DCCs changes nothing but the store it is handed.  Two carriers, each
the primitive that makes its guarantee structural:

- :attr:`ptk.Scope.PRIVATE` records live in ONE scene ID property group,
  ``scene["data_internal"]``.  A scene ID property is outside every
  object-set export by construction, so a current file has nothing to
  exclude by name from an export set; it draws no Outliner row; and it is
  where the shots and key-stash records had always lived
  (``scene["shot_store"]``).  A file saved before the group is folded into it
  on the first private write: the pre-2026-09-18 ``data_internal`` Empty's
  custom properties and the two top-level scene properties move over, then
  the Empty is removed.  Until then the export sets still drop that Empty by
  name, and reads find its records without migrating anything.  Only this
  file's OWN objects are carriers: a library-linked ``data_internal`` holds
  the library's records, and a linked ``data_export`` is listed
  (:meth:`DataNodes.get_export_nodes`) but never written.
- :attr:`ptk.Scope.DELIVERABLE` records are custom properties on the
  ``data_export`` Empty, which Blender's FBX exporter writes as user
  properties (``use_custom_props`` on) -- the same in-band surface unitytk
  reads via ``OnPostprocessGameObjectWithUserProperties``.  The Empty stays
  visible and selectable: a ``use_selection`` export can only ship what it
  can select.

``import bpy`` is deferred into the call bodies (no import side effects).
"""

import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pythontk as ptk

_Scope = ptk.Scope
logger = logging.getLogger(__name__)


class DataNodes(ptk.SceneStoreBase):
    """Blender's scene store: the ``data_internal`` scene property group and
    the ``data_export`` Empty behind ``ptk.SceneStoreBase``."""

    INTERNAL = "data_internal"
    EXPORT = "data_export"
    NAMES: Dict[ptk.Scope, str] = {_Scope.PRIVATE: INTERNAL, _Scope.DELIVERABLE: EXPORT}
    #: ``{(library path, mtime): its private records}`` -- one linked read per
    #: file (:meth:`_library_scene_values`).
    _LIBRARY_SCENE_VALUES: Dict[tuple, Dict[str, Any]] = {}

    #: Record keys readers used to spell here; the declarations are
    #: ``ptk.SceneRecords`` and these are the same strings, not copies.
    FBX_TAKES = ptk.SceneRecords.FBX_TAKES.key
    SHOT_METADATA = ptk.SceneRecords.SHOTS.key

    #: Top-level scene properties the shots and key-stash records lived under
    #: before the group; folded into it on the first write.
    _LEGACY_SCENE_KEYS = (
        ptk.SceneRecords.SHOT_STORE.key,
        ptk.SceneRecords.KEY_STASH.key,
    )

    # ------------------------------------------------------------------
    # The private carrier: one scene ID property group
    # ------------------------------------------------------------------

    @staticmethod
    def _scene():
        try:
            import bpy
        except ImportError:
            return None
        return bpy.context.scene

    @staticmethod
    def _local_object(name: str):
        """This file's OWN object named *name*, else ``None``.

        ``bpy.data.objects.get(name)`` returns a LIBRARY object when the file
        has no local one of that name (measured 2026-09-18), and a linked
        module's carrier is the library's: its private records are not this
        file's, and it is not this file's to write.  The ``(name, None)`` key
        asks for the local one only.
        """
        import bpy

        return bpy.data.objects.get((name, None))

    @classmethod
    def _private_group(cls, create: bool = False):
        """The ``data_internal`` property group, else ``None``.

        With *create* (a write, which always runs where Blender allows data
        edits) a pre-group file is folded in first and the group is created on
        demand.  Without it nothing is written: a READ can come from a context
        Blender forbids data edits in (a panel draw), so it never migrates --
        :meth:`_legacy_values` supplies what an unfolded file still holds.
        """
        scene = cls._scene()
        if scene is None:
            return None
        if create:
            cls._fold_legacy()
            group = scene.get(cls.INTERNAL)
            return group if group is not None else cls._new_group(scene)
        return scene.get(cls.INTERNAL)

    @classmethod
    def _legacy_values(cls) -> Dict[str, Any]:
        """What an UNFOLDED file holds in the pre-group locations -- the
        ``data_internal`` Empty's properties and the top-level ``shot_store`` /
        ``key_stash`` scene properties -- read without writing anything."""
        scene = cls._scene()
        if scene is None:
            return {}
        found: Dict[str, Any] = {}
        legacy = cls._local_object(cls.INTERNAL)
        if legacy is not None:
            found.update(cls._object_values(legacy))
        for key in cls._LEGACY_SCENE_KEYS:
            if key in scene.keys():
                found.setdefault(key, cls._jsonable(scene.get(key)))
        return found

    @classmethod
    def _legacy_value(cls, key: str) -> Any:
        """The one pre-group value :meth:`read` asks about -- the legacy
        Empty's, else the top-level scene property's -- looked up alone rather
        than by dumping every legacy record (a store polls its record)."""
        scene = cls._scene()
        if scene is None:
            return None
        legacy = cls._local_object(cls.INTERNAL)
        value = legacy.get(key) if legacy is not None else None
        if value is None and key in cls._LEGACY_SCENE_KEYS:
            value = scene.get(key)
        return value

    @classmethod
    def _new_group(cls, scene):
        """Create the empty group on *scene* and return it."""
        scene[cls.INTERNAL] = {}
        return scene[cls.INTERNAL]

    @classmethod
    def _fold_legacy(cls) -> None:
        """One-time fold of a file saved before the group: each local scene's
        top-level ``shot_store`` / ``key_stash`` properties move into that
        scene's group, the ``data_internal`` Empty's custom properties are
        copied into EVERY local scene's group, and the Empty is removed.

        Every scene, not the current one alone: the Empty was file-global, so
        each scene read its records (:meth:`read`), and folding them into one
        scene before removing it cost every other scene its emissive registry
        and bake manifests.  A legacy value REPLACES the group's: a fold
        removes every local legacy source, so one found beside the group was
        written after it -- by an older blendertk that reopened the file -- and
        is the newer record (:meth:`read` agrees).  A library-linked Empty or
        scene is not a source at all (:meth:`_local_object`)."""
        import bpy

        legacy = cls._local_object(cls.INTERNAL)
        values = cls._object_values(legacy) if legacy is not None else {}
        for scene in bpy.data.scenes:
            if scene.library is not None:
                continue
            top = [k for k in cls._LEGACY_SCENE_KEYS if k in scene.keys()]
            if not top and not values:
                continue
            group = scene.get(cls.INTERNAL)
            if group is None:
                group = cls._new_group(scene)
            # Top-level first, so the Empty wins a key both hold, as in a read.
            for key in top:
                group[key] = scene[key]
                del scene[key]
            for key, value in values.items():
                group[key] = value
        if legacy is not None:
            bpy.data.objects.remove(legacy, do_unlink=True)

    # ------------------------------------------------------------------
    # The deliverable carrier: the ``data_export`` Empty
    # ------------------------------------------------------------------

    @classmethod
    def _export_object(cls, create: bool = False):
        """The ``data_export`` Empty (created + linked to the scene when
        *create*), else ``None``.

        Heals an *unlinked* carrier on the create path: ``bpy.data.objects``
        can hold an Empty no collection references (its collection was
        deleted) -- writes to it would succeed while the exporter never ships
        it, so a carrier about to be written is relinked into the scene.
        """
        import bpy

        obj = cls._local_object(cls.EXPORT)
        if obj is None:
            if create:
                obj = bpy.data.objects.new(cls.EXPORT, None)  # None data -> Empty
                bpy.context.scene.collection.objects.link(obj)
        elif create and bpy.context.scene not in obj.users_scene:
            # By identity, not name: a linked module's carrier shares the name.
            bpy.context.scene.collection.objects.link(obj)
        return obj

    @classmethod
    def get_internal_node(cls, create: bool = True):
        """The ``data_internal`` scene property group (created when *create*).

        Mirror of ``mtk.DataNodes.get_internal_node`` at the behaviour level:
        the private carrier, resolved without creating it when *create* is
        False.  A property group, not an object, since 2026-09-18 (see the
        module docstring); it supports ``[]``, ``.get`` and ``.keys()``.
        """
        return cls._private_group(create=create)

    @classmethod
    def ensure_internal(cls):
        """Get or create the ``data_internal`` property group (folding an
        unfolded file in first). Idempotent."""
        return cls._private_group(create=True)

    @classmethod
    def get_export_node(cls, create: bool = True):
        """The ``data_export`` Empty (created + linked to the scene when *create*)."""
        return cls._export_object(create=create)

    @classmethod
    def get_export_nodes(cls) -> List[Any]:
        """Every ``data_export`` carrier in the file, the file's own first
        (mirror of ``mtk.DataNodes.get_export_nodes``).

        The plural of :meth:`get_export_node`, and a different question: the
        carrier to WRITE to collapses to this file's own, but what the file
        CARRIES does not -- a library-linked module keeps its records on its
        own ``data_export`` (Blender's analogue of Maya's referenced
        ``NS:data_export``).  Linked carriers follow the file's own, ordered
        by library path.  Creates nothing.
        """
        try:
            import bpy
        except ImportError:
            return []
        found = [o for o in bpy.data.objects if o.name == cls.EXPORT]
        return sorted(
            found,
            key=lambda o: (o.library is not None, getattr(o.library, "filepath", "")),
        )

    @classmethod
    def ensure_export(cls):
        """Get or create the ``data_export`` Empty. Idempotent. Returns the object.

        Mirror of ``mtk.DataNodes.ensure_export`` (there a locked, hidden
        transform; here a plain Empty -- Blender's FBX exporter can only ship a
        selectable, visible object, so the carrier is deliberately not hidden).
        """
        return cls._export_object(create=True)

    # ------------------------------------------------------------------
    # The store contract (ptk.SceneStoreBase)
    # ------------------------------------------------------------------

    @classmethod
    def _carrier(cls, scope: ptk.Scope, create: bool = False):
        if _Scope(scope) is _Scope.PRIVATE:
            return cls._private_group(create=create)
        try:
            import bpy  # noqa: F401
        except ImportError:
            return None
        return cls._export_object(create=create)

    @classmethod
    def read(cls, scope: ptk.Scope, key: str) -> Optional[str]:
        """The string channel *key* in *scope*, or ``None`` when the carrier,
        the key or a value is absent.  A cleared key (stored as ``""``) reads
        as ``None``; a non-string property is not a channel.  A private key an
        unfolded file still holds in a pre-group location is read from there
        (and wins over the group: :meth:`_fold_legacy`)."""
        value = None
        if _Scope(scope) is _Scope.PRIVATE:
            # First: a pre-group source beside the group is the NEWER record
            # (see _fold_legacy).
            value = cls._legacy_value(key)
        if value is None:
            carrier = cls._carrier(scope)
            value = None if carrier is None else carrier.get(key)
        return value if isinstance(value, str) and value else None

    @classmethod
    def write(cls, scope: ptk.Scope, key: str, text: Optional[str]) -> Optional[str]:
        """Store *text* on *key* in *scope*.

        Creates the carrier on demand for a real *text*.  An empty *text*
        CLEARS: the key is set to ``""`` when it exists (matching mayatk, where
        the attr stays) and nothing is created otherwise.

        Returns:
            str | None: The carrier's name, or ``None`` when a clear had
            nothing to clear.
        """
        if not text and _Scope(scope) is _Scope.PRIVATE and cls._scene() is not None:
            # A private clear migrates an unfolded file first, as a write does
            # (the create path folds): clearing only the group would leave the
            # legacy copy for the next read to find.
            cls._fold_legacy()
        carrier = cls._carrier(scope, create=bool(text))
        if carrier is None:
            return None
        if text:
            carrier[key] = text
        elif key in carrier.keys():
            carrier[key] = ""
        else:
            return None
        return cls.name(scope)

    @classmethod
    def values(cls, scope: ptk.Scope) -> Dict[str, Any]:
        """Every property on the carrier of *scope*, strings and otherwise,
        coerced to a JSON-serializable form; ``_``-prefixed internals (e.g.
        ``_RNA_UI``) are skipped.  The private scope of an unfolded file also
        reports its pre-group records (which win a clash, as in :meth:`read`)."""
        carrier = cls._carrier(scope)
        found = cls._object_values(carrier) if carrier is not None else {}
        if _Scope(scope) is _Scope.PRIVATE:
            found.update(cls._legacy_values())
        return found

    @classmethod
    def _object_values(cls, carrier) -> Dict[str, Any]:
        """*carrier*'s properties (an object or a property group), coerced by
        :meth:`_jsonable`, ``_``-prefixed internals skipped."""
        return {
            key: cls._jsonable(carrier.get(key))
            for key in carrier.keys()
            if not key.startswith("_")
        }

    @staticmethod
    def _jsonable(val):
        """A non-string Blender custom-property value in JSON-serializable
        form: ``IDPropertyArray`` -> list, ``IDPropertyGroup`` -> dict;
        scalars and strings pass through."""
        if hasattr(val, "to_dict"):
            return val.to_dict()
        if hasattr(val, "to_list"):
            return val.to_list()
        return val

    @classmethod
    def dump_export_nodes(cls, decode: bool = True) -> Dict[str, Dict[str, Any]]:
        """Every ``data_export`` carrier's channels (:meth:`get_export_nodes`),
        keyed by the object's full name -- ``data_export`` for the file's own,
        ``data_export [lib.blend]`` for a linked module's (mirror of mayatk's,
        which keys by long path).  ``dump``'s value rules; ``{}`` when there is
        no carrier.  Creates nothing.
        """
        return {
            obj.name_full: cls._dumped(cls._object_values(obj), decode)
            for obj in cls.get_export_nodes()
        }

    # ------------------------------------------------------------------
    # Crossings -- another file's records meeting this file's
    # ------------------------------------------------------------------

    #: The record owners (``ptk.SceneStoreBase.OWNERS``: what each hook means
    #: and when it runs), mirror of mayatk's table.  No bake-session owner: a
    #: Blender bake parks nothing beside its record (its references are names,
    #: which a crossing respells).
    OWNERS: Dict[str, Tuple[str, str]] = {
        ptk.SceneRecords.SHOT_STORE.key: (
            "blendertk.anim_utils.shots._shots",
            "BlenderShotStore",
        ),
        ptk.SceneRecords.KEY_STASH.key: (
            "blendertk.anim_utils.key_stash._key_stash",
            "KeyStash",
        ),
        ptk.SceneRecords.EMISSIVE_REGISTRY.key: (
            "blendertk.mat_utils.emissive_groups",
            "EmissiveGroups",
        ),
    }

    @classmethod
    def carriers_in(cls, library) -> Dict[ptk.Scope, "_Carrier"]:
        """The carriers a linked *library* brings, by scope, read while it is
        still linked (mirror of mayatk's ``carriers_in(namespace)``): its
        ``data_export`` Empty, and its private records -- the ``data_internal``
        group of its first scene holding one (no link reaches a scene
        property; :meth:`_library_scene_values`) plus a pre-group
        ``data_internal`` Empty it still carries, whose values win as in a
        fold.  What ``merge_carriers`` / ``discard_carriers`` settle once the
        library is local.  Leaves nothing behind."""
        import bpy

        found: Dict[ptk.Scope, _Carrier] = {}

        def linked(name):
            return [
                o for o in bpy.data.objects if o.library == library and o.name == name
            ]

        export = linked(cls.EXPORT)[:1]
        if export:
            found[_Scope.DELIVERABLE] = _Carrier(cls._object_values(export[0]), export)
        legacy = linked(cls.INTERNAL)[:1]
        private = cls._library_scene_values(library)
        if legacy:
            private.update(cls._object_values(legacy[0]))
        if private or legacy:
            found[_Scope.PRIVATE] = _Carrier(private, legacy)
        return found

    @classmethod
    def _library_scene_values(cls, library) -> Dict[str, Any]:
        """The ``data_internal`` group of *library*'s first scene that holds
        one, else ``{}``.  A scene is never linked with a library's
        collections, so its group is read by linking the scenes for the read
        -- and every datablock that read linked is removed again."""
        import bpy

        from blendertk.core_utils._core_utils import CoreUtils

        path = bpy.path.abspath(library.filepath)
        if not os.path.isfile(path):
            return {}
        # The read links every scene's graph and unlinks it again -- a whole
        # assembly, for one property group.  A linked file cannot change under
        # us, so one read per (file, mtime) serves the decide / cancel / retry
        # round trips of a make-local.
        stamp = (path, os.path.getmtime(path))
        cached = cls._LIBRARY_SCENE_VALUES.get(stamp)
        if cached is not None:
            return dict(cached)
        before = {id_.session_uid for id_ in CoreUtils.all_ids()}
        with bpy.data.libraries.load(path, link=True) as (source, target):
            target.scenes = list(source.scenes)
        try:
            values: Dict[str, Any] = {}
            for scene in target.scenes:
                group = scene.get(cls.INTERNAL) if scene is not None else None
                if group is not None and group.keys():
                    values = cls._object_values(group)
                    break
            cls._LIBRARY_SCENE_VALUES[stamp] = dict(values)
            return values
        finally:
            fresh = [
                id_ for id_ in CoreUtils.all_ids() if id_.session_uid not in before
            ]
            if fresh:
                bpy.data.batch_remove(fresh)

    @classmethod
    def _drop_legacy_carrier(cls, carriers: Mapping[Any, "_Carrier"]) -> None:
        """Remove the pre-group ``data_internal`` Empty *carriers* hold, once
        its library is local -- its values were read while linked.  Left
        under its canonical name, the next private write would fold it into
        this file's records (:meth:`_fold_legacy`), silently REPLACING this
        file's values with the library's."""
        import bpy

        carrier = (carriers or {}).get(_Scope.PRIVATE)
        for obj in list(getattr(carrier, "objects", ())):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        if carrier is not None:
            carrier.objects = []

    @staticmethod
    def library_renames(before) -> Any:
        """``rename(name)`` for a library made local: *before* is
        ``[(datablock, name while linked), ...]`` for its objects and actions,
        and each one the move renamed (Blender's ``.001`` clash suffix) maps
        to its name now.  An ``object|data_path|index`` key -- the shot
        ledger's -- maps through its object.  A name the move did not treat
        alike for every datablock holding it -- an object kept it while an
        action became ``.001`` -- is ambiguous and left alone: a record names
        no type."""
        renamed: Dict[str, set] = {}
        kept: set = set()
        for datablock, old in before:
            try:
                new = datablock.name
            except ReferenceError:
                continue
            if new == old:
                kept.add(old)
            else:
                renamed.setdefault(old, set()).add(new)
        table = {
            old: next(iter(news))
            for old, news in renamed.items()
            if len(news) == 1 and old not in kept
        }

        def rename(name: str) -> Optional[str]:
            hit = table.get(name)
            if hit:
                return hit
            head, sep, tail = str(name).partition("|")
            hit = table.get(head) if sep else None
            return f"{hit}|{tail}" if hit else None

        return rename

    # -- the carrier hooks of ``ptk.SceneStoreBase``'s crossings --------------

    @classmethod
    def _live_carriers(
        cls, carriers: Mapping[Any, "_Carrier"]
    ) -> Dict[ptk.Scope, "_Carrier"]:
        return {_Scope(s): c for s, c in (carriers or {}).items() if c is not None}

    @classmethod
    def _foreign_carriers(cls, carriers) -> Dict[ptk.Scope, "_Carrier"]:
        """:meth:`_live_carriers` less a ``data_export`` the move made this
        file's own (it had none): adopted, not merged.  A private carrier is
        never this file's -- that is the scene group, which no library
        brings."""
        own = cls._local_object(cls.EXPORT)
        return {
            scope: carrier
            for scope, carrier in cls._live_carriers(carriers).items()
            if not (own is not None and any(o == own for o in carrier.objects))
        }

    @classmethod
    def _carrier_values(cls, carrier: "_Carrier") -> Dict[str, Any]:
        return dict(carrier.values)

    @classmethod
    def _carry_attributes(cls, carrier: "_Carrier", scope: ptk.Scope, ctx) -> None:
        """Copy the other ``data_export``'s non-record custom properties -- an
        emissive group's weight -- to this file's carrier when it has none of
        that name; one this file has stays its own, and a keyed one's curve
        does not move (both noted)."""
        if _Scope(scope) is not _Scope.DELIVERABLE:
            return
        props = {
            k: v
            for k, v in carrier.values.items()
            if not isinstance(v, str) and v is not None
        }
        if not props:
            return
        target = cls._export_object(create=True)
        for key, value in props.items():
            if key in target.keys():
                ctx.note(
                    f"{cls.EXPORT}.{key}: this file's own was kept; the other "
                    "file's was not moved."
                )
                continue
            target[key] = value
            if any(cls._keyed(obj, key) for obj in carrier.objects):
                ctx.note(f"{cls.EXPORT}.{key}: its value moved; its animation did not.")

    @staticmethod
    def _keyed(obj, key: str) -> bool:
        """Whether *obj* animates its custom property *key*."""
        from blendertk.anim_utils._anim_utils import AnimUtils

        path = f'["{key}"]'
        try:
            return any(fc.data_path == path for fc in AnimUtils.get_fcurves([obj]))
        except ReferenceError:
            return False

    @classmethod
    def _rederive(cls, specs, ctx) -> None:
        """Produce *specs* again from the merged file (authoring context,
        ``FbxUtils.publish``); mirror of mayatk's."""
        from blendertk.env_utils.fbx_utils import FbxUtils

        try:
            FbxUtils.publish(
                FbxUtils.export_context(mode=ptk.ExportContext.AUTHORING), only=specs
            )
        except Exception as error:  # noqa: BLE001 - the merge stands without them
            logger.warning("Deliverable records not re-derived.", exc_info=True)
            ctx.note(f"Deliverable records were not produced again ({error}).")

    @classmethod
    def _delete_carrier(cls, carrier: "_Carrier") -> None:
        """Remove the objects that held another file's records."""
        import bpy

        for obj in carrier.objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                continue


class _Carrier:
    """Another file's carrier as a crossing holds it: the *values* it held,
    read while its library was still linked, and the *objects* that held them
    -- local now, removed when the carrier is settled (none for a scene
    group, which no link brings)."""

    __slots__ = ("values", "objects")

    def __init__(self, values: Mapping[str, Any], objects=()):
        self.values = dict(values)
        self.objects = list(objects)
