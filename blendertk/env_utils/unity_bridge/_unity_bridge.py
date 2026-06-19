# !/usr/bin/python
# coding=utf-8
"""Unity bridge engine -- export the Blender selection into a Unity project's Assets/.

The Blender->Unity hand-off, mirror of mayatk's ``UnityBridge``: same
:class:`pythontk.HandoffBridge` skeleton + shared :class:`unitytk.CopyToAssetsDeliverer`
copy-to-assets Strategy, with the Blender-side selection + FBX export from
:class:`blendertk.env_utils.handoff_export.BlenderExportMixin`. Unity ingests any file dropped into
``Assets/`` on focus, so the hand-off is simply *copy the FBX into the project* (and optionally
launch the editor) -- no fresh-instance launch, never disturbs a running editor.

``import bpy`` is deferred (via the export mixin) so the engine surface resolves under headless
``blender --background``; the ``parameters`` import (Qt) is deferred into ``params_defaults``.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pythontk as ptk

from unitytk import CopyToAssetsDeliverer

from blendertk.env_utils.handoff_export import BlenderExportMixin


_PKG_DIR = Path(__file__).resolve().parent


def list_delivery_modes() -> List[Tuple[str, str]]:
    """``[(mode_stem, ""), ...]`` for the panel's delivery combo.

    Single-sources the modes from the shared deliverer (seam for a scripted-import mode).
    """
    return list(CopyToAssetsDeliverer.DELIVERY_MODES)


class UnityBridge(BlenderExportMixin, ptk.HandoffBridge):
    """Export the Blender selection and copy it into a Unity project's ``Assets/``.

    Set :attr:`project_path` to the target Unity project (the folder containing ``Assets/``) before
    calling :meth:`send` -- the panel wires this from its 'Unity Project' field. Delivery is the
    :class:`unitytk.CopyToAssetsDeliverer` Strategy.
    """

    payload_prefix = "btk_to_unity"

    def __init__(self, project_path: Optional[str] = None):
        super().__init__()
        self.project_path = project_path
        self.deliverer = CopyToAssetsDeliverer()

    # ------------------------------------------------------------------ bindings
    def list_template_modes(self):
        return list_delivery_modes()

    def params_defaults(self):
        from blendertk.env_utils.unity_bridge import parameters as _params

        return _params.defaults()

    def _produce(self, objects, request) -> ptk.Payload:
        """Export the FBX (via the mixin) and stamp the default asset name for the deliverer."""
        payload = super()._produce(objects, request)
        payload.extras["default_asset_name"] = self._default_asset_name(objects)
        return payload

    def _default_asset_name(self, objects) -> str:
        """Asset stem from the first selected object."""
        obj = objects[0]
        return getattr(obj, "name", str(obj))


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = UnityBridge(project_path=None)
    # bridge.project_path = r"C:/path/to/UnityProject"
    # bridge.send(template="copy_to_assets", mode="send_to")
