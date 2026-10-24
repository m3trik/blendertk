# !/usr/bin/python
# coding=utf-8
"""Push the Blender selection to a live browser / WebXR preview.

Mirror of mayatk's ``env_utils.webxr_preview.WebXrPreview``
(``btk.WebXrPreview`` <-> ``mtk.WebXrPreview``).

The lightest of the hand-off bridges: there is no target application to
discover or launch, because the target is a browser tab the user already has
open. :class:`pythontk.PreviewDeliverer` converts the exported FBX to GLB and
publishes it to a loopback :class:`pythontk.PreviewServer`; a page already open
-- including one open inside a PC-tethered headset -- picks the new version up
on its next poll.

The mirror is literal rather than parallel: :class:`pythontk.PreviewBridge`
owns the export defaults and the public ``push`` / ``url`` / ``stop`` surface
for both packages, and :class:`BlenderExportMixin` supplies the selection read
and the FBX export every Blender-originating bridge shares -- its carrier
states every material and the handoff, as the Scene Exporter's FBX does, so
the preview and the production deliverable describe the scene identically.
``bpy`` is never imported at module scope (call bodies defer it), so the
package surface still resolves without a running Blender.

Example:
    >>> preview = btk.WebXrPreview()
    >>> preview.push()              # opens a tab on the first call
    >>> preview.push()              # the open tab swaps to the new version
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pythontk as ptk

from blendertk.env_utils.fbx_utils import FbxUtils
from blendertk.env_utils.handoff_export import BlenderExportMixin


class WebXrPreview(BlenderExportMixin, ptk.PreviewBridge):
    """Live browser / WebXR preview of the Blender selection.

    One :class:`pythontk.PreviewDeliverer` is shared by every instance, so the
    server -- and therefore the port and the tab pointed at it -- survives
    across pushes and across panel reopens for the life of the Blender session.
    """

    payload_prefix = "blender_webxr_preview"
    deliverer = ptk.PreviewDeliverer(title="Blender")
    #: The preview READS the in-band metadata, so it has to ship the carrier.
    #:
    #: This is what makes the button self-feeding after a lightmap bake: the bake
    #: commits ``lightmaps`` to ``data_export``, this export carries that
    #: object, and ``MeshConvert.fbx_to_glb`` -> ``apply_glb_lightmaps`` binds the maps
    #: during the GLB conversion. Without it a *selection* push exports the meshes
    #: alone, the manifest never reaches the GLB, and the preview renders unlit with no
    #: error to explain why. Mirror of mayatk's ``WebXrPreview``.
    include_data_export = True
    #: The page plays only the baked motion, so the payload leaves out the rig
    #: bones the Scene Exporter's Exclude Rig Helpers row leaves out (on by
    #: default there): the preview shows what the deliverable ships. Mirror of
    #: mayatk's ``WebXrPreview``.
    drop_rig_apparatus = True

    def _fbx_options(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """The hand-off's FBX options, with cameras let through.

        The start camera (the deliverer's ``user_pos``) is what the page opens
        its views through and stands a headset under; every camera in the
        pushed set rides along, and the page looks the start up by name and
        ignores the rest. The hand-off default keeps cameras out because a
        receiving DCC has its own; the preview is the one receiver that reads
        one. Mirror of mayatk's.
        """
        options = super()._fbx_options(params)
        options["object_types"] = FbxUtils._as_object_types(
            options.get("object_types") or {"MESH"}
        ) | {"CAMERA"}
        return options

    def _start_node(self, name: str) -> Optional[Any]:
        """The current scene's object named *name*, or ``None``.

        The current scene's, not ``bpy.data``'s: an object only another scene
        links would ship into a push of this one.
        """
        import bpy

        return bpy.context.scene.objects.get(name)
