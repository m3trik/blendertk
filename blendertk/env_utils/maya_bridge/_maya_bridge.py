# !/usr/bin/python
# coding=utf-8
"""Maya bridge engine -- export the Blender selection and run a chosen import template in Maya.

The Blender half of the Maya<->Blender object hand-off (``btk.MayaBridge`` <-> ``mtk.BlenderBridge``).
A thin :class:`pythontk.ScriptLaunchBridge` subclass: the shared ``send()`` skeleton, the template
discovery / ``BRIDGE_MODES`` / ``__KEY__`` substitution machinery, and the
render-script-then-launch-a-fresh-app deliverer all live upstream in
:mod:`pythontk.core_utils.app_handoff`. The Blender-side selection + FBX export come from
:class:`blendertk.env_utils.handoff_export.BlenderExportMixin` (shared with the Unity bridge). This
file owns only the Maya-specific bits, declared as a :class:`pythontk.ScriptLaunchSpec` dataclass
(executable discovery + the ``-command`` MEL wrapper that exec's the rendered Python template) plus
the parameter bindings.

Co-located with its panel (``maya_bridge_slots.MayaBridgeSlots`` + ``maya_bridge.ui``) under
``env_utils``; discovered by ``BlenderUiHandler``. ``import bpy`` and the Qt-only ``parameters``
import are deferred so the engine surface resolves under headless ``blender --background`` (no Qt).
Windows-focused (Maya install layout).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils import script_template as _templates
from pythontk.core_utils.script_template import SEND_TO

from blendertk.env_utils.handoff_export import BlenderExportMixin


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


def _build_mel_command(script_path: str) -> str:
    """Return the MEL passed to ``maya -command`` that exec's the rendered import script.

    ``-command`` runs MEL on startup; have it exec our rendered Python template. The arg is a
    single list element (AppLauncher uses no shell), so only MEL-level quoting matters: the MEL
    string uses ``"``, the inner Python uses ``'`` + a raw string -> nothing to escape.
    """
    script_posix = str(script_path).replace("\\", "/")
    return f"python(\"exec(open(r'{script_posix}').read())\")"


# Declarative Maya hand-off config (target discovery + the ``-command`` launch args). Launches a
# FRESH Maya that exec's the rendered Python template (session-safety rule).
_SPEC = ptk.ScriptLaunchSpec(
    # ``$MAYA_EXE`` -> ``$MAYA_LOCATION/bin/maya.exe`` -> ``AppLauncher.find_app`` -> a scan of
    # ``Program Files\\Autodesk\\Maya*\\bin\\maya.exe`` (highest version wins).
    app=ptk.AppSpec(
        name="Maya",
        env_vars=("MAYA_EXE",),
        location_env_vars=(("MAYA_LOCATION", ("bin", "maya.exe")),),
        app_names=("maya",),
        scan_globs=(r"{program_files}\Autodesk\Maya*\bin\maya.exe",),
        not_found_msg=(
            "Maya executable not found. Install Maya or set $MAYA_EXE / $MAYA_LOCATION / "
            "MayaBridge.maya_path."
        ),
    ),
    template_dir=_TEMPLATE_DIR,
    launch_args=lambda script_path: ["-command", _build_mel_command(script_path)],
    payload_prefix="btk_to_maya",
)


# Module-level template discovery -- kept so the slots (and tests) can list templates without a
# live engine. Thin wrappers over the shared :mod:`pythontk.core_utils.script_template` helpers.
def list_templates() -> List[Path]:
    """User-visible templates in ``templates/`` (skips underscore-prefixed)."""
    return _templates.list_templates(_TEMPLATE_DIR, ".py")


def template_modes(template_path: Path) -> Tuple[str, ...]:
    """Modes a template declares via ``BRIDGE_MODES``; ``("send_to",)`` fallback."""
    return _templates.template_modes(template_path, (SEND_TO,))


def list_template_modes() -> List[Tuple[str, str]]:
    """``[(stem, mode), ...]`` for every (template, mode) pairing."""
    return _templates.list_template_modes(_TEMPLATE_DIR, ".py", (SEND_TO,))


class MayaBridge(BlenderExportMixin, ptk.ScriptLaunchBridge):
    """Export the Blender selection and run a chosen Maya import template.

    Named after its target app (``MayaBridge``), mirroring ``BlenderBridge``; the Maya-side
    counterpart is ``mayatk.BlenderBridge``. All Maya-specific config is the :data:`_SPEC`
    dataclass; this class adds only the Blender parameter bindings.
    """

    spec = _SPEC

    def __init__(self, maya_path: Optional[str] = None):
        super().__init__(app_path=maya_path)

    # Back-compat alias: existing callers / tests use ``.maya_path``.
    @property
    def maya_path(self) -> Optional[str]:
        return self.app_path

    @maya_path.setter
    def maya_path(self, value: Optional[str]) -> None:
        self.app_path = value

    # ------------------------------------------------------------------ parameter bindings
    def params_defaults(self) -> Dict[str, Any]:
        from blendertk.env_utils.maya_bridge import parameters as _params

        return _params.defaults()

    def render_context(self, params: Dict[str, Any]) -> Dict[str, str]:
        from blendertk.env_utils.maya_bridge import parameters as _params

        return _params.render_context(params)

    # Back-compat alias for tests that referenced the bound helper.
    _build_mel_command = staticmethod(_build_mel_command)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = MayaBridge()
    # bridge.send()                                  # default: import template
    # bridge.send(template="import_and_frame")
    # bridge.send(params={"INCLUDE_MATERIALS": False})
    bridge.send()
