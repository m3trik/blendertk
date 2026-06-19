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

    Additive per producer: each writes its own key; the carrier is created on first write and
    a key cleared (set to ``""``) rather than deleting the Empty, matching mayatk.
    """

    EXPORT = "data_export"

    @staticmethod
    def get_export_node(create=True):
        """The ``data_export`` Empty (created + linked to the scene when *create*)."""
        import bpy

        obj = bpy.data.objects.get(DataNodes.EXPORT)
        if obj is None and create:
            obj = bpy.data.objects.new(DataNodes.EXPORT, None)  # None data → Empty
            bpy.context.scene.collection.objects.link(obj)
        return obj

    @staticmethod
    def set_export_string(key, value):
        """Set custom property *key* on the carrier to *value* (string).

        An empty *value* clears the key (set to ``""``) without creating the carrier just to
        hold an empty manifest — mirror of ``mtk.DataNodes.set_export_string``. Returns the
        carrier object name, or ``None`` when nothing was written.
        """
        obj = DataNodes.get_export_node(create=bool(value))
        if obj is None:
            return None
        if value:
            obj[key] = value
        elif key in obj.keys():
            obj[key] = ""
        return obj.name

    @staticmethod
    def get_export_string(key):
        """The carrier's *key* custom property, or ``None`` (no carrier / key)."""
        obj = DataNodes.get_export_node(create=False)
        return obj.get(key) if obj is not None else None
