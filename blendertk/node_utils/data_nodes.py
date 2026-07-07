# !/usr/bin/python
# coding=utf-8
"""Scene-wide export-metadata carrier — mirror of mayatk's ``node_utils.data_nodes``.

Split into its own module (not ``_node_utils``) to match mayatk's layout 1:1, so a change to
``mtk.DataNodes`` maps to the same file here. ``import bpy`` is deferred into the call bodies
(no import side effects).
"""


class DataNodes:
    """Scene-wide export-metadata carrier (mirror of mayatk's ``node_utils.DataNodes``).

    Maya rides per-producer JSON manifests (shot metadata, audio, lightmaps) into the FBX as
    *user properties* on one shared ``data_export`` transform; the Blender analogue is an
    **Empty object** of the same name carrying those manifests as *custom properties*. Blender's
    FBX exporter writes object custom properties as FBX user properties (enable *Custom
    Properties* on export), which unitytk reads via ``OnPostprocessGameObjectWithUserProperties``
    — so the same "sidecar benefits, no sidecar file" bridge works unchanged on the Unity side.

    ``data_internal`` is the companion carrier for tool-authored state that must persist with
    the scene but must never ride into the FBX (e.g. ``SmartBake``'s restore manifest) — same
    Empty-with-custom-properties mechanism, kept as a *separate* object so the export object-set
    builders can exclude it by name alone (see ``env_utils.scene_exporter``) without having to
    also strip individual keys out of ``data_export``.

    Additive per producer: each writes its own key; the carrier is created on first write and
    a key cleared (set to ``""``) rather than deleting the Empty, matching mayatk.
    """

    INTERNAL = "data_internal"
    EXPORT = "data_export"

    @staticmethod
    def _get_node(name, create=True):
        """Get-or-create the Empty named *name* (created + linked to the scene when *create*) —
        shared mechanism behind both the internal and export carriers (see class docstring)."""
        import bpy

        obj = bpy.data.objects.get(name)
        if obj is None and create:
            obj = bpy.data.objects.new(name, None)  # None data → Empty
            bpy.context.scene.collection.objects.link(obj)
        return obj

    @staticmethod
    def _set_string(name, key, value):
        """Set custom property *key* on the Empty named *name* to *value* (string).

        An empty *value* clears the key (set to ``""``) without creating the carrier just to
        hold an empty manifest. Returns the carrier object name, or ``None`` when nothing was
        written.
        """
        obj = DataNodes._get_node(name, create=bool(value))
        if obj is None:
            return None
        if value:
            obj[key] = value
        elif key in obj.keys():
            obj[key] = ""
        return obj.name

    @staticmethod
    def _get_string(name, key):
        """The Empty named *name*'s *key* custom property, or ``None`` (no carrier / key /
        cleared). A cleared key (stored as ``""``) reads back as ``None``."""
        obj = DataNodes._get_node(name, create=False)
        if obj is None:
            return None
        return obj.get(key) or None

    @staticmethod
    def get_internal_node(create=True):
        """The ``data_internal`` Empty (created + linked to the scene when *create*)."""
        return DataNodes._get_node(DataNodes.INTERNAL, create=create)

    @staticmethod
    def ensure_internal():
        """Get or create the ``data_internal`` Empty. Idempotent. Returns the object."""
        return DataNodes.get_internal_node(create=True)

    @staticmethod
    def set_internal_string(key, value):
        """Set custom property *key* on the internal carrier to *value* (string) — see
        ``_set_string`` for the clear/create semantics; mirror of
        ``mtk.DataNodes.set_internal_string``."""
        return DataNodes._set_string(DataNodes.INTERNAL, key, value)

    @staticmethod
    def get_internal_string(key):
        """The internal carrier's *key* custom property, or ``None`` — see ``_get_string``;
        matches mayatk's ``get_internal_string`` absent/empty semantics."""
        return DataNodes._get_string(DataNodes.INTERNAL, key)

    @staticmethod
    def get_export_node(create=True):
        """The ``data_export`` Empty (created + linked to the scene when *create*)."""
        return DataNodes._get_node(DataNodes.EXPORT, create=create)

    @staticmethod
    def set_export_string(key, value):
        """Set custom property *key* on the carrier to *value* (string) — see ``_set_string``
        for the clear/create semantics; mirror of ``mtk.DataNodes.set_export_string``."""
        return DataNodes._set_string(DataNodes.EXPORT, key, value)

    @staticmethod
    def get_export_string(key):
        """The carrier's *key* custom property, or ``None`` — see ``_get_string``; matches
        mayatk's ``get_export_string`` absent/empty semantics."""
        return DataNodes._get_string(DataNodes.EXPORT, key)
