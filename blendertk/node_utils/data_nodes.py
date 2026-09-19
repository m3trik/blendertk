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

from typing import Any, Dict, List, Optional

import pythontk as ptk

_Scope = ptk.Scope


class DataNodes(ptk.SceneStoreBase):
    """Blender's scene store: the ``data_internal`` scene property group and
    the ``data_export`` Empty behind ``ptk.SceneStoreBase``."""

    INTERNAL = "data_internal"
    EXPORT = "data_export"
    NAMES: Dict[ptk.Scope, str] = {_Scope.PRIVATE: INTERNAL, _Scope.DELIVERABLE: EXPORT}

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
    _REMOVE_IN = "0.9.0"

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
            cls._fold_legacy(scene)
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
    def _fold_legacy(cls, scene) -> None:
        """One-time fold of a file saved before the group: the ``data_internal``
        Empty's custom properties and the top-level ``shot_store`` /
        ``key_stash`` scene properties move into the group, and the Empty is
        removed.

        A legacy value REPLACES the group's: a fold removes every local legacy
        source, so one found beside the group was written after it -- by an
        older blendertk that reopened the file -- and is the newer record
        (:meth:`read` agrees).  A library-linked Empty is not a source at all
        (:meth:`_local_object`)."""
        import bpy

        legacy = cls._local_object(cls.INTERNAL)
        top = [k for k in cls._LEGACY_SCENE_KEYS if k in scene.keys()]
        if legacy is None and not top:
            return
        group = scene.get(cls.INTERNAL)
        if group is None:
            group = cls._new_group(scene)
        # Top-level first, so the Empty wins a key both hold, as in a read.
        for key in top:
            group[key] = scene[key]
            del scene[key]
        if legacy is not None:
            for key, value in cls._object_values(legacy).items():
                group[key] = value
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
            cls._fold_legacy(cls._scene())
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
    # Retired channel methods -- the record layer replaced them (2026-09-18)
    # ------------------------------------------------------------------

    @ptk.Deprecation.symbol(
        "DataNodes.write(ptk.Scope.PRIVATE, key, value)", remove_in=_REMOVE_IN
    )
    @classmethod
    def set_internal_string(cls, key, value):
        return cls.write(_Scope.PRIVATE, key, value)

    @ptk.Deprecation.symbol(
        "DataNodes.read(ptk.Scope.PRIVATE, key)", remove_in=_REMOVE_IN
    )
    @classmethod
    def get_internal_string(cls, key):
        return cls.read(_Scope.PRIVATE, key)

    @ptk.Deprecation.symbol(
        "ptk.SceneRecords.<RECORD>.save(DataNodes, payload)", remove_in=_REMOVE_IN
    )
    @classmethod
    def set_internal_json(cls, key, payload):
        return cls.write(
            _Scope.PRIVATE, key, _LEGACY_SPEC.encode(payload) if payload else None
        )

    @ptk.Deprecation.symbol(
        "ptk.SceneRecords.<RECORD>.load(DataNodes)", remove_in=_REMOVE_IN
    )
    @classmethod
    def get_internal_json(cls, key, default=None):
        return _LEGACY_SPEC.decode(cls.read(_Scope.PRIVATE, key), default)

    @ptk.Deprecation.symbol(
        "DataNodes.write(ptk.Scope.DELIVERABLE, key, value)", remove_in=_REMOVE_IN
    )
    @classmethod
    def set_export_string(cls, key, value):
        return cls.write(_Scope.DELIVERABLE, key, value)

    @ptk.Deprecation.symbol(
        "DataNodes.read(ptk.Scope.DELIVERABLE, key)", remove_in=_REMOVE_IN
    )
    @classmethod
    def get_export_string(cls, key):
        return cls.read(_Scope.DELIVERABLE, key)

    @ptk.Deprecation.symbol(
        "FbxUtils.publish_authored({RECORD: payload})", remove_in=_REMOVE_IN
    )
    @classmethod
    def set_export_json(cls, key, payload):
        return cls.write(
            _Scope.DELIVERABLE, key, _LEGACY_SPEC.encode(payload) if payload else None
        )


#: A shapeless declaration the retired JSON getter decodes through: no
#: envelope, so a legacy caller's payload comes back exactly as stored.
_LEGACY_SPEC = ptk.RecordSpec(
    "_legacy", _Scope.PRIVATE, 0, "legacy", "", envelope=False
)
