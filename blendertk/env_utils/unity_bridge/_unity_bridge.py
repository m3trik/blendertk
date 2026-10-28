# !/usr/bin/python
# coding=utf-8
"""Unity bridge engine -- export the Blender selection into a Unity project's Assets/.

Mirror of mayatk's ``env_utils.unity_bridge._unity_bridge``. The Blender->Unity hand-off, built
on the same :class:`pythontk.HandoffBridge` skeleton as the Maya bridge but with a
**copy-to-assets** deliverer instead of a script-launch one: Unity ingests any file dropped into
``Assets/`` through its own asset pipeline on focus, so the session-safe, license-free hand-off
is simply *copy the FBX into the project* (and optionally launch the editor). Unlike the
Blender<->Maya bridges there is no fresh-instance-launch dance -- dropping a file into
``Assets/`` never disturbs a running editor or unsaved work.

Composition:

* :class:`blendertk.env_utils.handoff_export.BlenderExportMixin` -- the Blender selection + FBX
  export (shared with :class:`blendertk.env_utils.maya_bridge.MayaBridge`).
* :class:`unitytk.CopyToAssetsDeliverer` -- the copy-into-``Assets/`` + optional editor launch
  Strategy -- the SAME class mayatk's Unity bridge uses (built shared across both DCC bridges
  from the start; no per-DCC copy needed here).
* :class:`pythontk.HandoffBridge` -- the ``send()`` orchestration.

``import bpy`` is deferred (via the mixin) so the engine surface resolves headlessly; the
``unitytk`` import is module-level (pure-Python, no DCC/Qt).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pythontk as ptk


from blendertk.env_utils.handoff_export import BlenderExportMixin


_PKG_DIR = Path(__file__).resolve().parent


class UnityBridge(BlenderExportMixin, ptk.HandoffBridge):
    """Export the Blender selection and copy it into a Unity project's ``Assets/``.

    Set :attr:`project_path` to the target Unity project (the folder containing ``Assets/``)
    before calling :meth:`send` -- the panel wires this from its 'Unity Project' field. Delivery
    is the :class:`unitytk.CopyToAssetsDeliverer` Strategy.
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
    #: exporter writes none unless it bakes; this panel offers no animation
    #: knob, so the hand-off bakes (and splits the declared shots) by default.
    #: Measured in Unity before this: the curve proxies arrived as static nodes.
    include_animation = True

    @staticmethod
    def _deliverer_cls():
        """unitytk's deliverer, imported on use rather than at module scope.

        ``unitytk`` is an optional distribution (``pip install blendertk[unity]``).
        Deferring the import keeps a missing one from raising while the module is
        merely being imported -- which would fire before any UI exists to say so --
        and lets the panel offer to install it on demand instead
        (:meth:`uitk.bridge.BridgeSlotsBase.ensure_optional_package`).
        """
        from unitytk import CopyToAssetsDeliverer

        return CopyToAssetsDeliverer

    def __init__(self, project_path: Optional[str] = None):
        super().__init__()
        self.project_path = project_path
        self.deliverer = self._deliverer_cls()()

    # ------------------------------------------------------------------ bindings
    def list_template_modes(self):
        return UnityBridge.list_delivery_modes()

    @classmethod
    def params_registry(cls):
        """The panel's parameter registry (``parameters.Parameters``, a
        ``uitk.bridge.ParamRegistry``): what tentacle's Unity Bridge builds its
        rows from, and :meth:`params_defaults` reads. Imported on use, as the
        defaults always were: the registry is uitk's, and the engine surface
        imports without uitk (a headless Blender ships no Qt binding)."""
        from blendertk.env_utils.unity_bridge import parameters as _params

        return _params.Parameters

    def params_defaults(self):
        return self.params_registry().defaults()

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

    @classmethod
    def list_delivery_modes(cls) -> List[Tuple[str, str]]:
        """``[(mode_stem, ""), ...]`` for the panel's delivery combo."""
        return list(cls._deliverer_cls().DELIVERY_MODES)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = UnityBridge(project_path=None)
    import blendertk as btk

    sel = btk.selected_objects()
    # bridge.project_path = "C:/path/to/UnityProject"
    # bridge.send(sel, template="copy_to_assets", mode="send_to")
