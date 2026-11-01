# !/usr/bin/python
# coding=utf-8
"""Unity bridge engine -- the Blender half of the Unity Workflow app.

Mirror of mayatk's ``env_utils.unity_bridge._unity_bridge``. The Unity Workflow app (extapps'
``unity_workflow``) is the one Unity panel; inside Blender, tentacle opens it with this class
as its engine (``ui.slots.engine = btk.UnityBridge``), and the app's Current Scene source sends
through it. This half resolves the app's Scope, exports it -- the FBX with the scene records
(the ``data_export`` carrier) and the curve-proxy transport the Unity importers rebind --
finishes it as the Scene Exporter finishes its FBX, and hands the file to the delivery the app
attached (``deliverer=``: unitytk's copy into ``Assets/`` and optional Editor launch). The
delivery, the project and the launch are the app's; nothing here names the Unity side.

Composition:

* :class:`blendertk.env_utils.handoff_export.BlenderExportMixin` -- the Blender selection + FBX
  export (shared with :class:`blendertk.env_utils.maya_bridge.MayaBridge`).
* :class:`pythontk.HandoffBridge` -- the ``send()`` orchestration (``scope_objects``, the
  delivery's preflight before the export) over the attached :class:`pythontk.Deliverer`.

``import bpy`` is deferred (via the mixin) so the engine surface resolves headlessly.
"""

from __future__ import annotations

from typing import Optional

import pythontk as ptk


from blendertk.env_utils.handoff_export import BlenderExportMixin, BridgeHost


class UnityBridge(BlenderExportMixin, ptk.HandoffBridge):
    """Export the Blender scope for a Unity project, through the delivery the caller attaches.

    The Unity Workflow app builds it with its own deliverer (``UnityBridge(deliverer=...)``)
    and sets :attr:`project_path` (the folder containing ``Assets/``) from its 'Unity
    Project' field before calling :meth:`send`; the produced payload -- the FBX, and its
    ``default_asset_name`` -- is what that deliverer copies in. A send with no delivery
    attached refuses before it exports. Mirror of mayatk's.
    """

    payload_prefix = "btk_to_unity"
    #: Unity IS the consumer of the in-band metadata, so the carrier ships.
    #:
    #: Unlike a DCC hand-off, this FBX is not an intermediate -- it lands in ``Assets/``
    #: as the deliverable, and unitytk's ``LightmapMetadataController`` binds Unity's
    #: native lightmap slots by reading ``lightmaps`` off ``data_export``. The
    #: flag also turns on ``use_custom_props``, without which the carrier would arrive
    #: as an Empty holding nothing. Mirror of mayatk's ``UnityBridge``.
    include_data_export = True
    #: unitytk's ``RenderEffectsImporter`` and ``EmissiveGroupController``
    #: rebind the curve proxies -- ``<object>__<channel>`` and one per keyed
    #: emissive weight -- so this hand-off stages them exactly like the Scene
    #: Exporter's FBX (mirror of mayatk's, which names ``render_effects``:
    #: Maya's FBX carries the keyed weights on the carrier itself).
    export_stagers = ("render_effects", "emissive_groups")
    #: Unity builds a model's AnimationClips from the FBX's takes, and Blender's
    #: exporter writes none unless it bakes; the app offers no animation
    #: knob, so the hand-off bakes (and splits the declared shots) by default.
    #: Measured in Unity before this: the curve proxies arrived as static nodes.
    include_animation = True

    def __init__(
        self,
        project_path: Optional[str] = None,
        deliverer: Optional[ptk.Deliverer] = None,
    ):
        super().__init__()
        self.project_path = project_path
        self.deliverer = deliverer

    # ------------------------------------------------------------------ bindings
    @classmethod
    def params_registry(cls):
        """The export rows the Unity Workflow app shows for its Current Scene
        source (``parameters.Parameters``, a ``uitk.bridge.ParamRegistry``), and
        what :meth:`params_defaults` reads. Imported on use, as the defaults always
        were: the registry is uitk's, and the engine surface imports without uitk
        (a headless Blender ships no Qt binding)."""
        from blendertk.env_utils.unity_bridge import parameters as _params

        return _params.Parameters

    def params_defaults(self):
        return self.params_registry().defaults()

    @staticmethod
    def install_package(spec: str) -> None:
        """Install the pip requirement *spec* where this Blender session imports
        it from (``BridgeHost.install_package``: Blender's user modules, as the
        bundled interpreter keeps the user site off ``sys.path``): the route the
        app takes for its optional unitytk when Blender opened it. Mirror of
        mayatk's.

        Parameters:
            spec: A requirement, e.g. ``unitytk>=0.1.0``.
        """
        BridgeHost.install_package(spec)

    def _produce(self, objects, request) -> ptk.Payload:
        """Export the FBX (via the mixin), embed what it depends on, and stamp
        the default asset name for the deliverer.

        The FBX lands in ``Assets/`` as the deliverable, so it is finished as
        the Scene Exporter's is (:meth:`FbxUtils.finish_deliverable`, which
        never raises and resolves the scene's map folders inside its own
        guard): every file its records name and the audio its strips play
        inside it, the probe's highlight copies, a mesh per pivot set. The
        deliverer embeds only the files a record names by file name, and an
        audio event names its strip, which only this scene can resolve.
        Mirror of mayatk's.
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        payload = super()._produce(objects, request)
        FbxUtils.finish_deliverable(payload.primary, logger=self.logger)
        payload.extras["default_asset_name"] = self._default_asset_name(objects)
        return payload

    @staticmethod
    def _default_asset_name(objects) -> str:
        """Asset stem from the first selected object."""
        return objects[0].name
