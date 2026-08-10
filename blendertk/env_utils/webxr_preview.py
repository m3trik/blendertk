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
for both packages, :class:`BlenderExportMixin` supplies the selection read,
and :class:`~blendertk.env_utils.scene_state.SceneState` owns the sidecar
readers -- shared with the Scene Exporter's GLB task, so the preview and the
production deliverable describe the scene identically. ``bpy`` is never
imported at module scope (call bodies defer it), so the package surface still
resolves without a running Blender.

Example:
    >>> preview = btk.WebXrPreview()
    >>> preview.push()              # opens a tab on the first call
    >>> preview.push()              # the open tab swaps to the new version
"""
from __future__ import annotations

from typing import Optional

import pythontk as ptk

from blendertk.env_utils.handoff_export import BlenderExportMixin
from blendertk.env_utils.scene_state import SceneState


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
    #: commits ``lightmap_metadata`` to ``data_export``, this export carries that
    #: object, and ``MeshConvert.fbx_to_glb`` -> ``apply_glb_lightmaps`` binds the maps
    #: during the GLB conversion. Without it a *selection* push exports the meshes
    #: alone, the manifest never reaches the GLB, and the preview renders unlit with no
    #: error to explain why. Mirror of mayatk's ``WebXrPreview``.
    include_data_export = True

    def _produce(self, objects, request) -> Optional[ptk.Payload]:
        """Export the FBX, then attach the scene sidecar the FBX can't carry.

        Mirror of the Maya producer: the skeleton's FBX payload plus a sidecar
        riding on ``Payload.extras``. The sections come from
        :class:`SceneState` (the shared reader column) and the versioned
        envelope they travel in is built by
        :meth:`pythontk.MeshConvert.build_scene_sidecar` via the bridge's
        ``_attach_sidecar``, so neither can fork against mayatk's twin.
        """
        payload = super()._produce(objects, request)
        if payload is None or not request.params.get("SCENE_SIDECAR", True):
            return payload

        # The closed export set, not the raw selection: a group Empty ships its
        # descendants, and their materials must travel with them.
        sections = SceneState.read(
            payload.extras.get("export_set") or objects,
            include_textures=request.params.get("EMBED_TEXTURES", True),
        )
        return self._attach_sidecar(payload, sections, source=SceneState.source())
