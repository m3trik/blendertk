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

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pythontk as ptk
from pythontk.core_utils import script_template as _templates
from pythontk.core_utils.script_template import SEND_TO

from blendertk.env_utils.handoff_export import BlenderExportMixin


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


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
    launch_args=lambda script_path: [
        "-command",
        MayaBridge._build_mel_command(script_path),
    ],
    payload_prefix="btk_to_maya",
    # The launched Maya inherits Blender's whole environment; an OCIO var pointing
    # inside Blender's own install (its bundled v2.5 config) fails Maya's
    # color-management init on every send. Strip exactly that case at launch time.
    launch_env=lambda: MayaBridge._launch_env(),
)


# Module-level template discovery -- kept so the slots (and tests) can list templates without a
# live engine. Thin wrappers over the shared :mod:`pythontk.core_utils.script_template` helpers.


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

        return _params.Parameters.defaults()

    def render_context(self, params: Dict[str, Any]) -> Dict[str, str]:
        from blendertk.env_utils.maya_bridge import parameters as _params

        return _params.Parameters.render_context(params)

    # ------------------------------------------------------------------ payload
    def _produce(self, objects, request):
        """Export the FBX (via the mixin), then sidecar the texture manifest.

        Blender's FBX exporter only carries images wired (almost) directly into
        Principled sockets -- packed ORM/MSAO through SeparateColor, AO multiplies
        and node-group plumbing export as NOTHING, so real production materials
        arrived in Maya gray (live report). The manifest carries each textured
        material's ORIGINAL image files; the Maya-side ``import`` template replays
        it through mayatk's existing ``BlenderSceneImport`` applier -- the same
        sidecar contract the pull direction has always used. Best-effort: a
        manifest failure must never cost the user the send itself.
        """
        payload = super()._produce(objects, request)
        if bool(request.params.get("INCLUDE_MATERIALS", True)):
            try:
                self._write_texture_manifest(objects, payload.primary)
            except Exception:  # noqa: BLE001
                self.logger.warning(
                    "Texture-manifest sidecar failed; Maya keeps the FBX-carried "
                    "materials.",
                    exc_info=True,
                )
        return payload

    def _write_texture_manifest(self, objects, fbx_path: str) -> None:
        """Write ``<fbx>.manifest.json`` for *objects* (no-op when nothing is textured).

        Same schema as the pull direction's collector in
        ``mayatk/env_utils/blender_bridge/templates/_import_scene.py`` (kept in
        step by hand -- that copy is dependency-free by template contract and runs
        the whole scene; this one is in-process and scoped to the exported set):
        one entry per material with its resolved image files, plus
        ``scene_materials`` naming EVERY material on the set so the Maya side's
        rename-on-clash matching can't claim an untextured sibling.
        """
        import json

        import bpy

        entries: List[Dict[str, Any]] = []
        by_material: Dict[str, Dict[str, Any]] = {}
        scene_materials: List[str] = []
        for obj in objects:
            obj = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
            if obj is None or obj.type != "MESH":
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    continue
                if mat.name not in scene_materials:
                    scene_materials.append(mat.name)
                if mat.name in by_material:
                    entry = by_material[mat.name]
                    if obj.name not in entry["objects"]:
                        entry["objects"].append(obj.name)
                    continue
                files, image_nodes = self._material_files(mat)
                if image_nodes == 0:
                    continue  # flat colors ride the FBX fine
                entry = {
                    "name": mat.name,
                    "shader_type": "principled_bsdf",
                    "fbx_material": mat.name,
                    "objects": [obj.name],
                    "files": files,
                }
                by_material[mat.name] = entry
                # File-less entries are written too: a textured material whose
                # image paths never resolved (packed-only / broken links) must
                # surface as a NAMED warning Maya-side, never as gray geometry.
                entries.append(entry)
        if not entries:
            return
        with open(fbx_path + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": entries,
                    "scene_materials": scene_materials,
                },
                fh,
                indent=1,
            )
        self.logger.info(
            f"Texture manifest: {len(entries)} textured material(s) sidecarred."
        )

    @classmethod
    def _material_files(cls, mat) -> Tuple[List[str], int]:
        """``(files, image_node_count)`` -- every image-texture file feeding *mat*.

        Free-form walk of the whole node tree (node groups included): the manifest
        carries EVERYTHING; classification into map types happens Maya-side via the
        shared ``ptk.MapFactory`` filename taxonomy.
        """
        files: List[str] = []
        node_count = 0

        def walk(tree, seen):
            nonlocal node_count
            if tree is None or tree in seen:
                return
            seen.add(tree)
            for node in tree.nodes:
                if node.bl_idname == "ShaderNodeTexImage":
                    if node.image is not None:
                        node_count += 1
                    path = cls._resolved_image_file(node.image)
                    if path and path not in files:
                        files.append(path)
                elif node.bl_idname == "ShaderNodeGroup":
                    walk(node.node_tree, seen)

        walk(mat.node_tree if mat.use_nodes else None, set())
        return files, node_count

    # Tiled-image filename tokens -> the glob that finds their tiles on disk.
    _TILE_TOKENS = (("<UDIM>", "[0-9]" * 4), ("<UVTILE>", "u*_v*"))

    @classmethod
    def _resolved_image_file(cls, image) -> Optional[str]:
        """Absolute on-disk path of *image*, or None (packed-only / missing / generated).

        UDIM/UVTILE sets flatten to their lowest existing tile -- neither FBX nor
        the manifest's per-file classification has a tiling concept, and one real
        tile beats an unresolvable token (mirror of the pull collector's rule).
        """
        import glob as _glob

        import bpy

        if image is None:
            return None
        try:
            path = bpy.path.abspath(image.filepath, library=image.library)
        except Exception:
            return None
        if not path:
            return None
        path = os.path.abspath(path)
        for token, pattern in cls._TILE_TOKENS:
            if token in path:
                tiles = sorted(
                    _glob.glob(_glob.escape(path).replace(token, pattern))
                )
                return tiles[0] if tiles else None
        return path if os.path.isfile(path) else None

    # ------------------------------------------------------------------ launch env
    @staticmethod
    def _launch_env():
        """Child env for the launched Maya (see the spec comment); None = inherit.

        Import-safe outside Blender (tests, headless surface resolution): no bpy
        -> nothing to strip.
        """
        try:
            import bpy

            root = os.path.dirname(bpy.app.binary_path or "")
        except Exception:
            return None
        return ptk.AppLauncher.handoff_env(root or None)

    # Back-compat alias for tests that referenced the bound helper.
    @staticmethod
    def _build_mel_command(script_path: str) -> str:
        """Return the MEL passed to ``maya -command`` that exec's the rendered import script.

        ``-command`` runs MEL on startup; have it exec our rendered Python template. The arg is a
        single list element (AppLauncher uses no shell), so only MEL-level quoting matters: the MEL
        string uses ``"``, the inner Python uses ``'`` + a raw string -> nothing to escape.
        """
        script_posix = str(script_path).replace("\\", "/")
        return f"python(\"exec(open(r'{script_posix}').read())\")"

    @staticmethod
    def list_templates() -> List[Path]:
        """User-visible templates in ``templates/`` (skips underscore-prefixed)."""
        return _templates.ScriptTemplate.list_templates(_TEMPLATE_DIR, ".py")

    @staticmethod
    def template_modes(template_path: Path) -> Tuple[str, ...]:
        """Modes a template declares via ``BRIDGE_MODES``; ``("send_to",)`` fallback."""
        return _templates.ScriptTemplate.template_modes(template_path, (SEND_TO,))

    @staticmethod
    def list_template_modes() -> List[Tuple[str, str]]:
        """``[(stem, mode), ...]`` for every (template, mode) pairing."""
        return _templates.ScriptTemplate.list_template_modes(
            _TEMPLATE_DIR, ".py", (SEND_TO,)
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    bridge = MayaBridge()
    # bridge.send()                                       # additive import
    # bridge.send(params={"CLEAR_SCENE": True})           # clean-slate / new scene
    # bridge.send(params={"FRAME_VIEW": True})            # import + frame in view
    # bridge.send(params={"INCLUDE_MATERIALS": False})    # geometry only
    bridge.send()
