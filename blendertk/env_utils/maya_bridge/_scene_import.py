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
_IMPORT_TEMPLATE_USD = _TEMPLATE_DIR / "_import_scene_usd.py"

# Conversion intermediates by route: "fbx" = classic material model + texture-
# manifest sidecar rebuilt via create_pbr_material; "usd" = native materials /
# instancing through each DCC's USD runtime (no manifest needed — see the
# templates' docstrings for the fidelity trade-offs).
_TEMPLATES = {"fbx": _IMPORT_TEMPLATE, "usd": _IMPORT_TEMPLATE_USD}

# Maya scene formats cmds.file(open=...) accepts; FBX would be imported directly.
SUPPORTED_EXTENSIONS = (".ma", ".mb")

# USD sources short-circuit the whole pipeline: both DCCs speak USD natively,
# so there is no conversion (and no Maya install/license) involved at all.
USD_EXTENSIONS = ptk.USD_EXTENSIONS

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
    @staticmethod
    def _template(via: str) -> Path:
        """The conversion template for *via*; raises on an unknown route."""
        try:
            return _TEMPLATES[via]
        except KeyError:
            raise ValueError(
                f"via must be one of {sorted(_TEMPLATES)}, got {via!r}"
            ) from None

    def render_script(
        self, src_path: str, out_path: str, *, via: str = "fbx",
        embed_textures: bool = False, include_animation: bool = True,
    ) -> str:
        """Render the Maya-side conversion script (exposed for tests/preview)."""
        context = {
            "SRC_PATH": str(src_path).replace("\\", "/"),
            "INCLUDE_ANIMATION": repr(bool(include_animation)),
        }
        if via == "usd":
            context["OUT_USD"] = str(out_path).replace("\\", "/")
            if embed_textures:
                self.logger.info(
                    "embed_textures has no USD-route equivalent (textures are "
                    "referenced on disk); ignored."
                )
        else:
            context["OUT_FBX"] = str(out_path).replace("\\", "/")
            context["EMBED_TEXTURES"] = repr(bool(embed_textures))
        return _templates.render_template(self._template(via), context)

    def convert(
        self, src_path: str, out_path: str, *, via: str = "fbx",
        timeout: float = 600, **script_opts: Any
    ) -> "ptk.ScriptRunResult":
        """Convert *src_path* to *out_path* in a fresh ``mayapy`` (blocking)."""
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
            self.render_script(src, out_path, via=via, **script_opts),
            artifact=out_path,
            timeout=timeout,
            env=env,
        )
        self.logger.info(
            f"Converted to {via.upper()} in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the mayapy run without patching pythontk internals).
    @staticmethod
    def _run_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.run_script_to_artifact(
            app_exe, script_text, artifact=artifact, timeout=timeout, env=env
        )

    @classmethod
    def _cache_key(cls, src: str, script_opts: Dict[str, Any], via: str = "fbx") -> str:
        """Deterministic tag for the conversion cache: scene identity (path +
        mtime + size), the Maya-side options that shape the artifact, and the
        conversion template's own identity (per *via*) -- a template fix must
        invalidate stale cached payloads, or a retry after an upgrade replays
        the old bug."""
        stat = os.stat(src)
        tpl = os.stat(cls._template(via))
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
        via: str = "fbx",
        cleanup: bool = True,
        use_cache: bool = True,
        timeout: float = 600,
        fbx_options: Optional[Dict[str, Any]] = None,
        **script_opts: Any,
    ) -> List[Any]:
        """Import the Maya scene at *src_path*; return the objects created.

        Parameters:
            src_path: A ``.ma`` / ``.mb`` file — or a USD file
                (``.usd``/``.usda``/``.usdc``/``.usdz``), which short-circuits
                the round-trip entirely: Blender imports USD natively, so no
                headless Maya, license checkout, cache or manifest is involved
                (``via``/``cleanup``/``use_cache``/``timeout``/``fbx_options``
                are inert for USD sources).
            via: Conversion intermediate for ``.ma``/``.mb`` sources.
                ``"fbx"`` (default) = classic material model + texture-manifest
                sidecar rebuilt through ``create_pbr_material``. ``"usd"`` =
                ``mayaUSDExport`` → ``wm.usd_import``: materials arrive as
                native UsdPreviewSurface→Principled conversions (metallic /
                roughness / normal textures included, no manifest), instancing
                survives, and ShaderFX game shaders are translated to
                standardSurface Maya-side (see the template docstrings).
            cleanup: Remove the intermediate artifact on success (kept on
                failure either way, with its path logged, for debugging). Not
                applied to cached payloads -- persistence is the cache's point.
            use_cache: Reuse a prior conversion of the identical scene
                (path + mtime + size + options + per-``via`` template key) --
                a cache hit skips the mayapy launch (and its license checkout)
                entirely. Cached payloads live in the temp dir under the
                detached-policy lifecycle (stale-swept after ``max_age_days``).
                Texture edits flow through even on a hit: the payload
                references textures on disk (``embed_textures`` defaults off),
                so Blender always loads the current files.
            timeout: Max seconds for the Maya-side conversion.
            fbx_options: Forwarded to ``bpy.ops.import_scene.fbx``
                (``via="fbx"`` only; the USD route imports with the native
                defaults).
            **script_opts: Maya-side knobs (``embed_textures`` /
                ``include_animation``; ``embed_textures`` is FBX-route only).
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        src = os.path.abspath(os.path.expandvars(str(src_path)))
        if os.path.splitext(src)[1].lower() in USD_EXTENSIONS:
            # USD fast path: native import, no headless-Maya round-trip at all.
            from blendertk.env_utils.usd import UsdUtils

            if not os.path.isfile(src):
                raise FileNotFoundError(f"USD file not found: {src}")
            self.logger.info(
                f"USD source — importing natively (no Maya conversion): {src}"
            )
            imported = UsdUtils.import_usd(src)
            self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
            return imported

        ext = ".usd" if via == "usd" else ".fbx"
        self._template(via)  # validate the route before any work
        use_cache = use_cache and os.path.isfile(src)
        cache_path = None
        if use_cache:
            store = ptk.TempArtifacts("maya_to_btk_cache", policy="detached")
            cache_path = store.path(
                extension=ext, name=self._cache_key(src, script_opts, via)
            )

        tmp = None
        if cache_path and os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            out_path = cache_path
            self.logger.info(
                f"Conversion cache hit ({os.path.basename(cache_path)}) -- "
                "skipping the Maya launch."
            )
        else:
            # Conversion always targets scoped SCRATCH; a completed conversion
            # is then atomically promoted into the cache slot. A timeout-killed
            # partial write can therefore never poison the cache (the failure
            # stays in scratch, kept + logged for debugging), and concurrent
            # imports of the same scene can't interleave into one file.
            tmp = ptk.TempArtifacts("maya_to_btk", policy="scoped")
            out_path = tmp.path(extension=ext)
            tmp.register(out_path + ".manifest.json")
            try:
                self.convert(src, out_path, via=via, timeout=timeout, **script_opts)
            except Exception:
                if os.path.isfile(out_path):
                    self.logger.warning(
                        f"Keeping intermediate {via.upper()} for debugging: {out_path}"
                    )
                raise
            if cache_path:
                os.replace(out_path, cache_path)
                if os.path.isfile(out_path + ".manifest.json"):
                    os.replace(out_path + ".manifest.json",
                               cache_path + ".manifest.json")
                elif os.path.isfile(cache_path + ".manifest.json"):
                    os.remove(cache_path + ".manifest.json")  # stale partial promote
                out_path = cache_path

        # Sidecar the FBX template writes for the textures FBX cannot carry
        # (packed metallic/roughness/ao maps on translated materials). The USD
        # route needs no manifest -- its materials arrive natively.
        manifest_path = out_path + ".manifest.json"
        try:
            if via == "usd":
                from blendertk.env_utils.usd import UsdUtils

                imported = UsdUtils.import_usd(out_path)
            else:
                imported = FbxUtils.import_fbx(out_path, **(fbx_options or {}))
        except Exception:
            if tmp is not None and os.path.isfile(out_path):
                self.logger.warning(
                    f"Keeping intermediate {via.upper()} for debugging: {out_path}"
                )
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
