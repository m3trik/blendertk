# !/usr/bin/python
# coding=utf-8
"""Import a Maya scene (.ma/.mb) into Blender via a headless-Maya FBX round-trip.

The pull-direction sibling of :class:`MayaBridge` (which pushes the Blender selection
to a fresh interactive Maya). A pull inverts the hand-off pipeline -- the input is a
*path*, the payload is produced *Maya-side*, and the caller needs the result -- so it
deliberately does NOT subclass :class:`pythontk.HandoffBridge`; the shared pieces are
the :class:`pythontk.AppSpec` discovery (borrowed from ``_maya_bridge._SPEC``), the
``__KEY__`` template renderer, and pythontk's blocking
:func:`~pythontk.run_script_to_artifact` runner.

Flow: render ``templates/_import_scene.py`` -> run it under ``mayapy`` (fresh process
every time -- the ecosystem session-safety rule) -> the script opens the scene and
exports an FBX -> :meth:`blendertk.FbxUtils.import_fbx` brings it in -> temp payload
removed on success, kept + logged on failure (``TempArtifacts`` scoped policy).

``import bpy`` stays deferred (inside ``FbxUtils``) so this surface resolves under
headless ``blender --background`` and in plain-venv tests. Requires a local Maya
install (the conversion checks out a Maya license for the duration of the run).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pythontk as ptk
from pythontk.core_utils import script_template as _templates

from blendertk.env_utils.maya_bridge._maya_bridge import _SPEC, _TEMPLATE_DIR

_IMPORT_TEMPLATE = _TEMPLATE_DIR / "_import_scene.py"

# Maya scene formats cmds.file(open=...) accepts; FBX would be imported directly.
SUPPORTED_EXTENSIONS = (".ma", ".mb")

# Child-process env for the conversion mayapy: skip the startup baggage a
# headless one-shot converter never needs. userSetup.py is the big one on
# pipeline machines (it can bootstrap a whole toolkit); the CIP/CER/CLIC
# analytics trio adds network round-trips. Scene REQUIREMENTS (Arnold, USD,
# module plugins) are untouched -- ``cmds.file(open)`` still resolves them.
_FAST_MAYA_ENV = {
    "MAYA_SKIP_USERSETUP_PY": "1",
    "MAYA_DISABLE_CIP": "1",
    "MAYA_DISABLE_CER": "1",
    "MAYA_DISABLE_CLIC_IPM": "1",
}


def mayapy_from_maya_exe(maya_exe: str) -> Optional[str]:
    """Return the ``mayapy`` interpreter beside *maya_exe*, or ``None`` if absent.

    The bridge's :class:`pythontk.AppSpec` discovers the GUI binary
    (``.../bin/maya.exe``); the headless interpreter ships in the same ``bin`` dir.
    """
    exe = Path(maya_exe)
    # The install scan can return 'maya.EXE' — the suffix check must be case-insensitive.
    candidate = exe.with_name("mayapy.exe" if exe.suffix.lower() == ".exe" else "mayapy")
    return str(candidate) if candidate.is_file() else None


class MayaSceneImport(ptk.LoggingMixin):
    """Engine: convert a Maya scene to FBX via headless Maya, then import it.

    Scriptable and synchronous; async affordances belong to the calling UI layer.
    """

    def __init__(self, maya_path: Optional[str] = None, log_level: str = "INFO"):
        super().__init__()
        self.logger.setLevel(log_level)
        self._maya_path = maya_path

    # ------------------------------------------------------------------ discovery
    @property
    def maya_path(self) -> Optional[str]:
        """The Maya GUI executable (explicit, or discovered via the bridge's AppSpec)."""
        if not self._maya_path:
            self._maya_path = _SPEC.app.resolve()
        return self._maya_path

    @maya_path.setter
    def maya_path(self, value: Optional[str]) -> None:
        self._maya_path = value

    @property
    def mayapy_path(self) -> Optional[str]:
        """The headless ``mayapy`` interpreter derived from :attr:`maya_path`."""
        maya_exe = self.maya_path
        return mayapy_from_maya_exe(maya_exe) if maya_exe else None

    def require_mayapy(self) -> str:
        """Return :attr:`mayapy_path` or raise an error naming what's missing."""
        maya_exe = self.maya_path
        if not maya_exe:
            raise FileNotFoundError(_SPEC.app.not_found_message)
        mayapy = mayapy_from_maya_exe(maya_exe)
        if not mayapy:
            raise FileNotFoundError(f"mayapy not found beside {maya_exe}.")
        return mayapy

    # ------------------------------------------------------------------ conversion
    def render_script(
        self, src_path: str, out_fbx: str, *, embed_textures: bool = False,
        include_animation: bool = True,
    ) -> str:
        """Render the Maya-side conversion script (exposed for tests/preview)."""
        context = {
            "SRC_PATH": str(src_path).replace("\\", "/"),
            "OUT_FBX": str(out_fbx).replace("\\", "/"),
            "EMBED_TEXTURES": repr(bool(embed_textures)),
            "INCLUDE_ANIMATION": repr(bool(include_animation)),
        }
        return _templates.render_template(_IMPORT_TEMPLATE, context)

    def convert(
        self, src_path: str, out_fbx: str, *, timeout: float = 600, **script_opts: Any
    ) -> "ptk.ScriptRunResult":
        """Convert *src_path* to *out_fbx* in a fresh ``mayapy`` (blocking)."""
        src = os.path.abspath(os.path.expandvars(str(src_path)))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Maya scene not found: {src}")
        if not src.lower().endswith(SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported scene format: {src} (expected {SUPPORTED_EXTENSIONS})"
            )
        mayapy = self.require_mayapy()
        self.logger.info(f"Converting {os.path.basename(src)} via {mayapy} ...")
        env = dict(os.environ)
        env.update(_FAST_MAYA_ENV)
        result = self._run_script(
            mayapy,
            self.render_script(src, out_fbx, **script_opts),
            artifact=out_fbx,
            timeout=timeout,
            env=env,
        )
        self.logger.info(
            f"Converted to FBX in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the mayapy run without patching pythontk internals).
    @staticmethod
    def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.run_script_to_artifact(
            app_exe, script_text, artifact=artifact, timeout=timeout, env=env
        )

    @staticmethod
    def _cache_key(src: str, script_opts: Dict[str, Any]) -> str:
        """Deterministic tag for the conversion cache: scene identity (path +
        mtime + size), the Maya-side options that shape the FBX, and the
        conversion template's own identity -- a template fix must invalidate
        stale cached payloads, or a retry after an upgrade replays the old bug."""
        stat = os.stat(src)
        tpl = os.stat(_IMPORT_TEMPLATE)
        blob = (
            f"{src}|{stat.st_mtime_ns}|{stat.st_size}|{sorted(script_opts.items())}"
            f"|{tpl.st_mtime_ns}|{tpl.st_size}"
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ import
    def import_scene(
        self,
        src_path: str,
        *,
        cleanup: bool = True,
        use_cache: bool = True,
        timeout: float = 600,
        fbx_options: Optional[Dict[str, Any]] = None,
        **script_opts: Any,
    ) -> List[Any]:
        """Import the Maya scene at *src_path*; return the objects created.

        Parameters:
            src_path: A ``.ma`` / ``.mb`` file.
            cleanup: Remove the intermediate FBX on success (kept on failure
                either way, with its path logged, for debugging). Not applied
                to cached payloads -- persistence is the cache's point.
            use_cache: Reuse a prior conversion of the identical scene
                (path + mtime + size + options key) -- a cache hit skips the
                mayapy launch (and its license checkout) entirely. Cached
                payloads live in the temp dir under the detached-policy
                lifecycle (stale-swept after ``max_age_days``). Texture edits
                flow through even on a hit: the payload references textures
                on disk (``embed_textures`` defaults off), so Blender always
                loads the current files.
            timeout: Max seconds for the Maya-side conversion.
            fbx_options: Forwarded to ``bpy.ops.import_scene.fbx``.
            **script_opts: Maya-side knobs (``embed_textures`` / ``include_animation``).
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        src = os.path.abspath(os.path.expandvars(str(src_path)))
        use_cache = use_cache and os.path.isfile(src)
        cache_fbx = None
        if use_cache:
            store = ptk.TempArtifacts("maya_to_btk_cache", policy="detached")
            cache_fbx = store.path(
                extension=".fbx", name=self._cache_key(src, script_opts)
            )

        tmp = None
        if cache_fbx and os.path.isfile(cache_fbx) and os.path.getsize(cache_fbx) > 0:
            out_fbx = cache_fbx
            self.logger.info(
                f"Conversion cache hit ({os.path.basename(cache_fbx)}) -- "
                "skipping the Maya launch."
            )
        else:
            # Conversion always targets scoped SCRATCH; a completed conversion
            # is then atomically promoted into the cache slot. A timeout-killed
            # partial write can therefore never poison the cache (the failure
            # stays in scratch, kept + logged for debugging), and concurrent
            # imports of the same scene can't interleave into one file.
            tmp = ptk.TempArtifacts("maya_to_btk", policy="scoped")
            out_fbx = tmp.path(extension=".fbx")
            tmp.register(out_fbx + ".manifest.json")
            try:
                self.convert(src, out_fbx, timeout=timeout, **script_opts)
            except Exception:
                if os.path.isfile(out_fbx):
                    self.logger.warning(
                        f"Keeping intermediate FBX for debugging: {out_fbx}"
                    )
                raise
            if cache_fbx:
                os.replace(out_fbx, cache_fbx)
                if os.path.isfile(out_fbx + ".manifest.json"):
                    os.replace(out_fbx + ".manifest.json",
                               cache_fbx + ".manifest.json")
                elif os.path.isfile(cache_fbx + ".manifest.json"):
                    os.remove(cache_fbx + ".manifest.json")  # stale partial promote
                out_fbx = cache_fbx

        # Sidecar the template writes for the textures FBX cannot carry
        # (packed metallic/roughness/ao maps on translated materials).
        manifest_path = out_fbx + ".manifest.json"
        try:
            imported = FbxUtils.import_fbx(out_fbx, **(fbx_options or {}))
        except Exception:
            if tmp is not None and os.path.isfile(out_fbx):
                self.logger.warning(f"Keeping intermediate FBX for debugging: {out_fbx}")
            raise
        if os.path.isfile(manifest_path):
            # Structurally non-fatal: a bad sidecar must never abort an
            # import whose FBX already landed (materials just stay phong).
            try:
                self._apply_texture_manifest(manifest_path, imported)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"Texture-manifest rebuild failed ({e}); keeping FBX materials."
                )
        if cleanup and tmp is not None:
            tmp.cleanup()
        self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
        return imported

    def _apply_texture_manifest(self, manifest_path: str, imported: List[Any]) -> None:
        """Rebuild translated materials natively from the conversion's sidecar.

        The FBX carries only the classic-model approximation (color / normal /
        emissive); the manifest carries each translated material's ORIGINAL
        texture files, which the game-shader engine
        (:func:`blendertk.create_pbr_material`) wires into a Principled BSDF --
        including the packed game-engine maps FBX has no slot for
        (``Metallic_Smoothness``, ``MSAO``, ``ORM``), gloss->roughness inversion
        and AO-multiply. Classification is by filename via the shared
        ``ptk.MapFactory`` SSoT (the same classifier that built these networks
        Maya-side), so conventionally named sets round-trip; an entry whose
        files classify to nothing keeps its FBX material (logged). Per-entry
        failures degrade, never abort the import.
        """
        import json

        from blendertk.mat_utils._mat_utils import assign_mat, create_pbr_material

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            self.logger.warning(f"Texture manifest unreadable ({e}); keeping FBX materials.")
            return
        if not isinstance(manifest, dict):
            self.logger.warning("Texture manifest malformed; keeping FBX materials.")
            return

        # Fallback matching only (see below): objects by SHORT name.
        by_short = {}
        for obj in imported:
            by_short.setdefault(obj.name.split(".")[0], []).append(obj)

        for entry in manifest.get("materials", []):
            name = entry.get("name", "?")
            try:
                listed = entry.get("files", [])
                files = [f for f in listed if os.path.isfile(f)]
                if not files:
                    # Never silent: pink materials with no explanation cost a
                    # debugging session (live production report).
                    if listed:
                        self.logger.warning(
                            f"{name}: manifest texture file(s) missing on disk, "
                            f"e.g. {listed[0]} -- material stays untextured."
                        )
                    else:
                        self.logger.warning(
                            f"{name}: no texture paths resolved during conversion "
                            "-- the scene's Maya project may be missing "
                            "(workspace.mel not found above the scene) or the "
                            "textures need relinking. Material stays untextured."
                        )
                    continue
                material = create_pbr_material(files, name=name)
                if material is None:  # nothing classified -- keep the FBX phong
                    self.logger.warning(
                        f"{name}: no texture classified by filename; keeping the "
                        "FBX-carried material."
                    )
                    continue

                # Primary: swap at the SLOT level, keyed by the translated
                # phong's name (unique per shading group; the importer may
                # suffix ``.001``). Preserves multi-material/per-face layouts
                # — assign_mat would clobber every slot on the mesh — and is
                # immune to duplicate object leaf names.
                fbx_name = entry.get("fbx_material") or ""
                replaced, swapped = [], 0
                if fbx_name:
                    for obj in imported:
                        for slot in getattr(obj, "material_slots", []):
                            old = slot.material
                            if old is not None and old.name.split(".")[0] == fbx_name:
                                slot.material = material
                                swapped += 1
                                if old not in replaced:
                                    replaced.append(old)
                if swapped:
                    self._purge_orphans(replaced)
                    self.logger.info(
                        f"Rebuilt material {material.name} from {len(files)} "
                        f"file(s) into {swapped} slot(s)."
                    )
                    continue

                # Fallback (importer renamed the material): whole-object assign.
                targets = [
                    obj for member in entry.get("objects", [])
                    for obj in by_short.get(member, [])
                ]
                if not targets:
                    self._purge_orphans([material])  # nothing to attach it to
                    self.logger.warning(f"{name}: no matching slot or object found.")
                    continue
                assign_mat(targets, material)
                self.logger.info(
                    f"Rebuilt material {material.name} from {len(files)} file(s) "
                    f"on {len(targets)} object(s) (object-level fallback)."
                )
            except Exception as e:
                self.logger.warning(f"Manifest entry {name} skipped: {e}")

    def _purge_orphans(self, materials: List[Any]) -> None:
        """Remove replaced materials (and their now-exclusive images) once unused.

        Hygiene only -- every step is best-effort and must never break the
        import (headless/no-bpy contexts simply no-op).
        """
        try:
            import bpy
        except ImportError:
            return
        for mat in materials:
            try:
                if mat.users:
                    continue
                images = [
                    n.image for n in (mat.node_tree.nodes if mat.node_tree else [])
                    if getattr(n, "image", None) is not None
                ]
                bpy.data.materials.remove(mat)
                for img in images:
                    if img.users == 0:
                        bpy.data.images.remove(img)
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Orphan purge skipped: {e}")


def import_maya_scene(src_path: str, **kwargs: Any) -> List[Any]:
    """Import a Maya scene (.ma/.mb) into the current Blender scene.

    Convenience wrapper over :meth:`MayaSceneImport.import_scene` -- launches a fresh
    headless Maya to convert the scene to FBX, imports the FBX, and cleans up.
    Returns the objects created. Requires a local Maya install.
    """
    return MayaSceneImport().import_scene(src_path, **kwargs)


__all__ = ["MayaSceneImport", "import_maya_scene", "mayapy_from_maya_exe"]
