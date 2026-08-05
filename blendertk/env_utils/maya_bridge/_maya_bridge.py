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

Two delivery *modes* ride the one export pipeline (:attr:`spec` / :attr:`run_spec`, dispatched by
``HandoffBridge.deliverers``): ``send_to`` launches an interactive Maya on the ``import`` template,
and ``save_as`` (:meth:`~pythontk.ScriptLaunchBridge.save_as`) runs ``mayapy`` headlessly on
``templates/_save_scene.py`` and returns a written ``.ma`` -- same FBX, same material sidecar, no
second export path.

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
from pythontk.core_utils.script_template import SAVE_AS, SEND_TO

from blendertk.env_utils.handoff_export import BlenderExportMixin


_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"


# Parameter defaults, declared HERE (Qt-free) rather than inside the widget specs:
# ``parameters.py`` reads them for its ``AttributeSpec`` defaults, so there is one source
# of truth, and the engine can still answer ``params_defaults()`` where the panel's Qt
# stack is unavailable -- a headless ``blender --background`` calling ``save_as`` must not
# need a UI toolkit to know that materials default to on. Mirror of mayatk's.
DEFAULTS: Dict[str, Any] = {
    "SCOPE": "selected",
    "INCLUDE_MATERIALS": True,
    # GameShader's own vocabulary (standard_surface / open_pbr / stingray) -- the
    # Maya side passes it straight to that engine rather than translating.
    # Stingray by default: it is the game-engine target these hand-offs feed, it
    # matches GameShader's own default, and it is the only shader family that
    # DECLARES its texture slots -- so a material sent this way can come back
    # with its maps intact instead of being re-guessed from filenames. A Maya
    # without shaderFX degrades to standardSurface with a named warning.
    "SHADER_TYPE": "stingray",
    "EMBED_TEXTURES": True,
    "APPLY_UNIT_SCALE": True,
    "INCLUDE_ANIMATION": False,
    "TRIANGULATE": False,
    "CLEAR_SCENE": False,
    "FRAME_VIEW": False,
}


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


# Child-process env for a headless one-shot Maya: skip the startup baggage it never
# needs. userSetup.py is the big one on pipeline machines (it can bootstrap a whole
# toolkit); the CIP/CER/CLIC analytics trio adds network round-trips. Scene REQUIREMENTS
# (Arnold, USD, module plugins) are untouched. Shared with the pull-direction conversion
# (``_scene_import`` imports it from here -- one definition, both directions).
_FAST_MAYA_ENV = {
    "MAYA_SKIP_USERSETUP_PY": "1",
    "MAYA_DISABLE_CIP": "1",
    "MAYA_DISABLE_CER": "1",
    "MAYA_DISABLE_CLIC_IPM": "1",
}


# The BLOCKING route (``save_as``): a DIFFERENT binary from the interactive spec --
# ``mayapy`` is the ecosystem's headless Maya, and the bridge resolves it from whatever
# ``maya.exe`` discovery (or the user) produced, so one path setting drives both.
_RUN_SPEC = ptk.ScriptLaunchSpec(
    app=ptk.AppSpec(
        name="mayapy",
        env_vars=("MAYAPY_EXE",),
        location_env_vars=(("MAYA_LOCATION", ("bin", "mayapy.exe")),),
        app_names=("mayapy",),
        scan_globs=(r"{program_files}\Autodesk\Maya*\bin\mayapy.exe",),
        not_found_msg=(
            "mayapy interpreter not found. Install Maya or set $MAYAPY_EXE / "
            "$MAYA_LOCATION / MayaBridge.maya_path."
        ),
    ),
    template_dir=_TEMPLATE_DIR,
    # Interpreter style: mayapy runs the script file directly.
    launch_args=lambda script_path: [script_path],
    modes=(SAVE_AS,),
    launch_env=lambda: MayaBridge._headless_env(),
)


# Module-level template discovery -- kept so the slots (and tests) can list templates without a
# live engine. Thin wrappers over the shared :mod:`pythontk.core_utils.script_template` helpers.


class MayaBridge(BlenderExportMixin, ptk.ScriptLaunchBridge):
    """Export the Blender selection and run a chosen Maya import template.

    Named after its target app (``MayaBridge``), mirroring ``BlenderBridge``; the Maya-side
    counterpart is ``mayatk.BlenderBridge``. All Maya-specific config is the :data:`_SPEC` /
    :data:`_RUN_SPEC` dataclasses; this class adds only the Blender parameter bindings.

    Two ways out::

        bridge.send(objects)                  # -> a fresh interactive Maya
        bridge.save_as("C:/out/asset.ma")     # -> a .ma on disk (blocking, headless mayapy)

    ``save_as`` is Blender's "export to Maya's native format": no Maya window, no manual import
    step, and ``objects=None`` means the whole scene rather than the selection.
    """

    spec = _SPEC
    run_spec = _RUN_SPEC
    # ``save_as`` writes Maya's native scene format; a bare path gets ".ma" (ascii is
    # diffable, greppable, and survives a version bump -- ``.mb`` only on request).
    save_extensions = (".ma", ".mb")

    def __init__(self, maya_path: Optional[str] = None):
        super().__init__(app_path=maya_path)

    # Back-compat alias: existing callers / tests use ``.maya_path``.
    @property
    def maya_path(self) -> Optional[str]:
        return self.app_path

    @maya_path.setter
    def maya_path(self, value: Optional[str]) -> None:
        self.app_path = value

    @property
    def headless_app_path(self) -> Optional[str]:
        """The ``mayapy`` interpreter for the blocking ``save_as`` run.

        Derivation from :attr:`maya_path` WINS, so an explicit user path (or
        ``$MAYA_EXE``) drives both routes -- picking Maya 2024 for the send and 2025 for
        the save would be a silent version mismatch, and version consistency is worth
        more here than honouring a second, separately-set variable. The headless
        AppSpec's own discovery (``$MAYAPY_EXE`` / ``$MAYA_LOCATION`` / PATH / install
        scan) is the fallback, reached when the GUI binary is unresolvable OR when no
        ``mayapy`` sits beside it.
        """
        maya_exe = self.app_path
        derived = MayaBridge.mayapy_from_maya_exe(maya_exe) if maya_exe else None
        return derived or self.run_spec.app.resolve()

    @staticmethod
    def mayapy_from_maya_exe(maya_exe: str) -> Optional[str]:
        """Return the ``mayapy`` interpreter beside *maya_exe*, or ``None`` if absent.

        The bridge's :class:`pythontk.AppSpec` discovers the GUI binary
        (``.../bin/maya.exe``); the headless interpreter ships in the same ``bin`` dir.
        Lives here, with the rest of the Maya discovery config; ``MayaSceneImport``
        re-exports it for the pull direction.
        """
        exe = Path(maya_exe)
        # The install scan can return 'maya.EXE' -- the suffix check must be case-insensitive.
        candidate = exe.with_name(
            "mayapy.exe" if exe.suffix.lower() == ".exe" else "mayapy"
        )
        return str(candidate) if candidate.is_file() else None

    # ------------------------------------------------------------------ parameter bindings
    def params_defaults(self) -> Dict[str, Any]:
        try:
            from blendertk.env_utils.maya_bridge import parameters as _params
        except ImportError:  # no Qt -- see DEFAULTS
            return dict(DEFAULTS)
        return _params.Parameters.defaults()

    def render_context(self, params: Dict[str, Any]) -> Dict[str, str]:
        try:
            from blendertk.env_utils.maya_bridge import parameters as _params

            context = _params.Parameters.render_context(params)
        except ImportError:  # no Qt -- pythontk's plain Python-literal formatting
            context = super().render_context(params)
        # Mirror of mtk.BlenderBridge: the launched child must be able to import
        # the toolkit or the material rebuild silently degrades to "mayatk
        # unavailable". Maya honors PYTHONPATH where Blender does not, so this leg
        # happened to work wherever mayatk was already on it -- by environment
        # luck, not by construction. Only the two roots the template needs are
        # passed, never Blender's whole sys.path (its 3.13 stdlib must not shadow
        # Maya's 3.11).
        context["EXTRA_SYS_PATH"] = repr(self.import_roots("mayatk", "pythontk"))
        return context

    # ------------------------------------------------------------------ payload
    def _produce(self, objects, request):
        """Export the FBX (via the mixin), then sidecar the scene manifest.

        Blender's FBX exporter only carries images wired (almost) directly into
        Principled sockets -- packed ORM/MSAO through SeparateColor, AO multiplies
        and node-group plumbing export as NOTHING, so real production materials
        arrived in Maya gray (live report). The manifest carries each textured
        material's ORIGINAL image files; the Maya-side ``import`` template replays
        it through mayatk's existing ``BlenderSceneImport`` applier -- the same
        sidecar contract the pull direction has always used. It also carries the
        exported Empties (name + display type), which the Maya side reads to
        restore each one as the CORRECT node type -- group vs locator -- so the
        section is written even for a materials-off send. Best-effort: a
        manifest failure must never cost the user the send itself.

        The walk covers ``Payload.extras["export_set"]`` -- the mixin's
        hierarchy closure -- not the caller's seed list: a group-Empty send
        must manifest its DESCENDANT meshes' materials.
        """
        payload = super()._produce(objects, request)
        export_set = payload.extras.get("export_set") or objects
        try:
            self._write_manifest(
                export_set,
                payload.primary,
                include_materials=bool(request.params.get("INCLUDE_MATERIALS", True)),
            )
        except Exception:  # noqa: BLE001
            self.logger.warning(
                "Manifest sidecar failed; Maya keeps the FBX-carried materials "
                "and falls back to the children-based Empty repair.",
                exc_info=True,
            )
        return payload

    def _write_manifest(
        self, objects, fbx_path: str, include_materials: bool = True
    ) -> None:
        """Write ``<fbx>.manifest.json`` for *objects* (no-op when there is nothing to say).

        Same schema as the pull direction's collector in
        ``mayatk/env_utils/blender_bridge/templates/_import_scene.py`` (kept in
        step by hand -- that copy is dependency-free by template contract and runs
        the whole scene; this one is in-process and scoped to the exported set):
        one entry per material with its resolved image files, plus
        ``scene_materials`` naming EVERY material on the set so the Maya side's
        rename-on-clash matching can't claim an untextured sibling, plus
        ``empties`` (see :meth:`_manifest_empties`).
        """
        import json

        import bpy

        empties = self._manifest_empties(objects)
        entries: List[Dict[str, Any]] = []
        by_material: Dict[str, Dict[str, Any]] = {}
        scene_materials: List[str] = []
        for obj in objects if include_materials else ():
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
                    # Rides ALONGSIDE files: the Maya side classifies by filename
                    # first (only a filename reveals packing) and falls back to
                    # these for images that classify to nothing.
                    "slots": self._material_slots(mat),
                }
                by_material[mat.name] = entry
                # File-less entries are written too: a textured material whose
                # image paths never resolved (packed-only / broken links) must
                # surface as a NAMED warning Maya-side, never as gray geometry.
                entries.append(entry)
        if not entries and not empties:
            return
        with open(fbx_path + ".manifest.json", "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 1,
                    "materials": entries,
                    "scene_materials": scene_materials,
                    "empties": empties,
                },
                fh,
                indent=1,
            )
        self.logger.info(
            f"Manifest: {len(entries)} textured material(s), "
            f"{len(empties)} Empty(ies) sidecarred."
        )

    @staticmethod
    def _manifest_empties(objects) -> List[Dict[str, str]]:
        """``[{name, display_type}, ...]`` for every EMPTY in the export set.

        FBX cannot say what an Empty *was*: every null becomes a Maya locator on
        import, and the old children-based repair then demoted EVERY parent to a
        plain group -- including locators that legitimately parent geometry
        (snap points, rig handles). The display type carries the author's
        intent: ``PLAIN_AXES`` (Blender's default) reads as "structure" -- a
        parent becomes a Maya group, a leaf a locator -- while any OTHER
        display type is a deliberate marker and stays a locator even with
        children. A ``maya_node_type`` custom property (stamped by the pull
        direction on round-tripped scenes) overrides both.
        """
        import bpy

        empties = []
        for obj in objects:
            obj = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
            if obj is None or obj.type != "EMPTY":
                continue
            entry = {"name": obj.name, "display_type": obj.empty_display_type}
            node_type = obj.get("maya_node_type")
            if node_type:
                entry["maya_node_type"] = str(node_type)
            empties.append(entry)
        return empties

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

    # Principled input -> the manifest's logical-channel vocabulary (resolved
    # Maya-side via ``ptk.MapRegistry.resolve_type_from_channel``). Both the 4.x/5.x
    # and legacy socket spellings are listed so this survives a Blender rename.
    # ``Normal`` is absent deliberately -- see :meth:`_material_slots`.
    _PRINCIPLED_CHANNELS = {
        "Base Color": "baseColor",
        "Metallic": "metallic",
        "Roughness": "roughness",
        "Alpha": "opacity",
        "Emission Color": "emission",
        "Emission": "emission",
        "Specular IOR Level": "specular",
        "Specular": "specular",
    }

    @classmethod
    def _material_slots(cls, mat) -> Dict[str, str]:
        """``{logical channel: file}`` for images whose destination is UNAMBIGUOUS.

        The Maya side rebuilds manifest materials by classifying FILENAMES, which
        fails for a texture named after a product rather than a map type. Maya's
        own shaders declare their inputs, so the send/pull directions from Maya
        just read them; a Blender material has to be traced -- each image node is
        walked FORWARD through its links to a Principled input, crossing the
        converter nodes the wiring emits (Normal Map, Bump, Invert, Separate
        Color, AO multiply).

        Traced per OUTPUT SOCKET, not per node: an image's ``Color`` and
        ``Alpha`` outputs are different data and must be followed separately.
        The canonical Blender cutout material wires one image's ``Color`` into
        Base Color AND its ``Alpha`` into the Principled ``Alpha`` -- read
        node-wide that is two channels, i.e. indistinguishable from a packed
        map, so BOTH were dropped and a real production material arrived with
        neither its color nor its cutout. Per socket each chain resolves to one
        channel and both are recorded; the Maya side then binds baseColor from
        ``outColor`` and opacity from ``outAlpha`` off the same file node,
        exactly as ``ShaderAttributeMap`` declares.

        Only a socket reaching EXACTLY ONE channel is recorded. Zero means it
        feeds nothing resolvable; several means it is packed (an ORM whose
        ``Color`` reaches Metallic + Roughness + AO through a Separate Color),
        and a packed map's identity lives in its filename, which the Maya side
        already reads correctly. Guessing a single channel for it would be worse
        than staying silent.

        ``Normal`` is resolved by WHICH converter got there: a Normal Map node
        means a normal map, a Bump node means a bump/height map. A raw image wired
        straight into the Principled ``Normal`` input is ambiguous and skipped.

        Scope: the material's own node tree. An image inside a node group (or one
        feeding into a group) is not traced -- it simply yields no slot, which
        degrades to today's filename-only behavior rather than to a wrong answer.
        """
        if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            return {}

        tree = mat.node_tree
        # Interior hops stay node-keyed (a converter's outputs are one signal);
        # only the SEED hop is socket-keyed, which is where Color vs Alpha
        # diverge.
        outgoing: Dict[str, list] = {}
        by_socket: Dict[tuple, list] = {}
        for link in tree.links:
            outgoing.setdefault(link.from_node.name, []).append(link)
            by_socket.setdefault(
                (link.from_node.name, link.from_socket.name), []
            ).append(link)

        def follow(links, via_bump, via_normal_map, seen):
            found = set()
            for link in links:
                to_node = link.to_node
                if to_node.bl_idname == "ShaderNodeBsdfPrincipled":
                    socket = link.to_socket.name
                    if socket == "Normal":
                        if via_normal_map:
                            found.add("normal")
                        elif via_bump:
                            found.add("bump")
                        # else: raw image into Normal -- ambiguous, contribute nothing
                    else:
                        channel = cls._PRINCIPLED_CHANNELS.get(socket)
                        if channel:
                            found.add(channel)
                    continue
                if to_node.name in seen:
                    continue
                found |= follow(
                    outgoing.get(to_node.name, []),
                    via_bump or to_node.bl_idname == "ShaderNodeBump",
                    via_normal_map or to_node.bl_idname == "ShaderNodeNormalMap",
                    seen | {to_node.name},
                )
            return found

        # Ambiguity is symmetric and both directions must be rejected: one socket
        # reaching several channels is packed, and several images reaching ONE
        # channel is equally unresolvable -- an AO multiply feeds the AO map and
        # the color map into the same Base Color input, so picking by node order
        # would hand Maya the AO map as the base color. Collect candidates first,
        # then keep only the channels exactly one image claims.
        candidates: Dict[str, List[str]] = {}
        for node in tree.nodes:
            if node.bl_idname != "ShaderNodeTexImage" or node.image is None:
                continue
            path = cls._resolved_image_file(node.image)
            if not path:
                continue
            for socket in node.outputs:
                links = by_socket.get((node.name, socket.name))
                if not links:
                    continue
                channels = follow(links, False, False, {node.name})
                if len(channels) == 1:
                    candidates.setdefault(next(iter(channels)), []).append(path)

        return {
            channel: paths[0]
            for channel, paths in candidates.items()
            if len(set(paths)) == 1
        }

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

    @staticmethod
    def _headless_env():
        """Child env for the ``save_as`` mayapy: :meth:`_launch_env` + the fast-start vars.

        Always a concrete dict (never ``None``): the fast-start vars have to be ADDED,
        and there is nothing to add them to unless the inherited env is materialized.
        """
        env = dict(MayaBridge._launch_env() or os.environ)
        env.update(_FAST_MAYA_ENV)
        return env

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
    # bridge.save_as("C:/out/asset.ma")                   # whole scene -> .ma (blocking)
    # bridge.save_as("C:/out/asset.mb", objects)          # just these objects, binary
    bridge.send()
