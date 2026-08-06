# !/usr/bin/python
# coding=utf-8
"""Import a Maya scene (.ma/.mb) into Blender via a headless-Maya round-trip
(FBX intermediate by default; USD per call via ``via="usd"``).

FBX is the default because its instancing is carried by the format itself on both
sides -- no sidecar replay stands between a Maya instance set and Blender linked
duplicates. The USD route reaches parity by replaying a recorded grouping from the
conversion's v2 sidecar (sanitized prim PATHS), and that replay is
GUARANTEED-OR-FAIL: a failed replay removes everything it imported and raises, so
a USD pull either preserves the sharing exactly or fails loudly -- never a
silently flattened scene. Pick USD per call/panel for look-heavy scenes.

The pull-direction sibling of :class:`MayaBridge` (which pushes the Blender selection
to a fresh interactive Maya). A pull inverts the hand-off pipeline -- the input is a
*path*, the payload is produced *Maya-side*, and the caller needs the result -- so it
deliberately does NOT subclass :class:`pythontk.HandoffBridge`; the shared pieces are
the :class:`pythontk.AppSpec` discovery (borrowed from ``_maya_bridge._SPEC``), the
``__KEY__`` template renderer, and pythontk's blocking
:func:`~pythontk.run_script_to_artifact` runner.

Flow: render the per-route conversion template (``_TEMPLATES``) -> run it under
``mayapy`` (fresh process every time -- the ecosystem session-safety rule) -> the
script opens the scene and exports the USD/FBX intermediate -> imported natively
(``UsdUtils.import_usd`` / :meth:`blendertk.FbxUtils.import_fbx`) -> temp payload
removed on success, kept + logged on failure (``TempArtifacts`` scoped policy).

``import bpy`` stays deferred (inside ``FbxUtils``) so this surface resolves under
headless ``blender --background`` and in plain-venv tests. Requires a local Maya
install (the conversion checks out a Maya license for the duration of the run).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pythontk as ptk
from pythontk.core_utils import script_template as _templates

from blendertk.env_utils.maya_bridge._maya_bridge import (
    _FAST_MAYA_ENV,
    _SPEC,
    _TEMPLATE_DIR,
    MayaBridge,
)

_IMPORT_TEMPLATE = _TEMPLATE_DIR / "_import_scene.py"
_IMPORT_TEMPLATE_USD = _TEMPLATE_DIR / "_import_scene_usd.py"
_BAKE_TEMPLATE = _TEMPLATE_DIR / "_bake_scene.py"

# Conversion intermediates by route: "fbx" = classic material model + texture-
# manifest sidecar rebuilt via create_pbr_material; "usd" = native materials /
# animation / visibility through each DCC's USD runtime, with Maya's instance
# sets recorded in the sidecar and rebuilt Blender-side as shared mesh data
# (USD's own instancing cannot express Blender linked duplicates — see the
# templates' docstrings).
_TEMPLATES = {"fbx": _IMPORT_TEMPLATE, "usd": _IMPORT_TEMPLATE_USD}

# Maya scene formats cmds.file(open=...) accepts; FBX would be imported directly.
SUPPORTED_EXTENSIONS = (".ma", ".mb")

# Sources bake_scene turns into a linkable .blend. A .ma/.mb needs the headless-Maya
# conversion first; an .fbx is already the bake's own input, so it skips that hop (and
# the Maya license checkout) entirely.
BAKE_SOURCE_EXTENSIONS = (".ma", ".mb", ".fbx")

# Sidecar written beside every bake naming the scene it came from. The Reference Manager
# lists SOURCE rows but links the BAKED file, so "is this row referenced?" can only be
# answered by walking back from a linked library to its origin. On disk rather than in
# panel settings so the mapping survives a session change and is shared by every panel.
BAKE_SOURCE_SUFFIX = ".source.json"

# Child-process argv for the bake Blender: headless, factory settings (deterministic,
# and skips any startup toolkit the user's Blender autoloads), then our rendered script.
_BAKE_LAUNCH_ARGS = ("--background", "--factory-startup", "--python")

# USD sources short-circuit the whole pipeline: both DCCs speak USD natively,
# so there is no conversion (and no Maya install/license) involved at all.
USD_EXTENSIONS = ptk.USD_EXTENSIONS

# Maya driver node types whose animation the plain FBX round trip would lose or
# mangle — the scene-scan mirror of the conversion template's Maya-side
# ``_detect_complex_anim`` probe (which can't be imported here: it lives in the
# dependency-free mayapy template). Kept in step with it by hand. Consumed two
# ways by :meth:`MayaSceneImport.scene_has_complex_animation`: as ``createNode``
# line types in a ``.ma``, and as byte tokens in a ``.mb`` (node type names are
# stored as plain strings in the binary IFF blocks).
_DRIVER_NODE_TYPES = frozenset(
    {
        "parentConstraint", "pointConstraint", "orientConstraint", "scaleConstraint",
        "aimConstraint", "poleVectorConstraint", "geometryConstraint",
        "normalConstraint", "tangentConstraint",
        "expression", "ikHandle", "motionPath",
        "animCurveUL", "animCurveUA", "animCurveUU",  # set-driven keys
    }
)

# The ``.mb`` byte-scan tokens: every driver type plus the auto-named
# ``<node>_visibility`` curve (keyed visibility — the ``.ma`` scan's
# ``animCurveTU`` signal, spelled as the curve NAME the binary also stores).
_MB_DRIVER_TOKENS = tuple(t.encode("ascii") for t in sorted(_DRIVER_NODE_TYPES)) + (
    b"_visibility",
)


def _smart_bake_syspath(mayatk_path: Optional[str] = None) -> List[str]:
    """Package-parent dirs to add to the conversion mayapy's ``PYTHONPATH`` so the
    template's optional smart-bake pre-pass can ``import mayatk`` (which itself needs
    ``pythontk``). Returns ``[]`` when mayatk can't be located -- the template then
    degrades to the plain FBX bake.

    Resolution order: an explicit *mayatk_path*; an importable ``mayatk`` (installed
    or already on ``sys.path``); else the monorepo sibling of ``pythontk``
    (``.../pythontk`` and ``.../mayatk`` share a parent). Each candidate is verified
    to actually contain the package before being returned.
    """
    import importlib.util

    def _holds_mayatk(parent: Optional[str]) -> bool:
        # A real package parent, not a namespace-package dir (the repo root
        # ``_scripts/mayatk`` has no ``__init__.py`` and must be rejected — putting
        # it on PYTHONPATH would NOT make ``import mayatk`` resolve).
        return bool(parent) and os.path.isfile(
            os.path.join(parent, "mayatk", "__init__.py")
        )

    dirs: List[str] = []
    pythontk_file = getattr(ptk, "__file__", None)
    if pythontk_file:  # SmartBake imports pythontk -> its parent must be on the path
        dirs.append(os.path.dirname(os.path.dirname(pythontk_file)))

    candidates: List[str] = []
    if mayatk_path:
        candidates.append(mayatk_path)
    else:
        spec = importlib.util.find_spec("mayatk")
        # ``spec.origin`` is the package __init__ for a REGULAR package and ``None``
        # for a namespace package — using it (not submodule_search_locations) skips
        # the namespace trap where a bare repo dir masquerades as ``mayatk``.
        if spec and spec.origin:
            candidates.append(os.path.dirname(os.path.dirname(spec.origin)))
    if pythontk_file:  # monorepo fallback: _scripts/{pythontk,mayatk} are siblings
        scripts_root = os.path.dirname(
            os.path.dirname(os.path.dirname(pythontk_file))
        )
        candidates.append(os.path.join(scripts_root, "mayatk"))

    for parent in candidates:
        if _holds_mayatk(parent):
            dirs.append(parent)
            return [d for d in dict.fromkeys(dirs) if d and os.path.isdir(d)]
    return []  # mayatk unresolvable -> no injection, plain bake in the child

class MayaSceneImport(ptk.LoggingMixin):
    """Engine: convert a Maya scene to FBX via headless Maya, then import it.

    Scriptable and synchronous; async affordances belong to the calling UI layer.
    """

    def __init__(
        self,
        maya_path: Optional[str] = None,
        log_level: str = "INFO",
        blender_path: Optional[str] = None,
        mayatk_path: Optional[str] = None,
    ):
        super().__init__()
        self.logger.setLevel(log_level)
        self._maya_path = maya_path
        # Host binary for the FBX -> .blend bake (see the blender_path property).
        self._blender_path = blender_path
        # Optional explicit mayatk location for the smart-bake pre-pass (else it is
        # auto-resolved; see _smart_bake_syspath).
        self._mayatk_path = mayatk_path

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
        return MayaSceneImport.mayapy_from_maya_exe(maya_exe) if maya_exe else None

    def require_mayapy(self) -> str:
        """Return :attr:`mayapy_path` or raise an error naming what's missing."""
        maya_exe = self.maya_path
        if not maya_exe:
            raise FileNotFoundError(_SPEC.app.not_found_message)
        mayapy = MayaSceneImport.mayapy_from_maya_exe(maya_exe)
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
        self,
        src_path: str,
        out_path: str,
        *,
        via: str = "fbx",
        embed_textures: bool = False,
        include_animation: bool = True,
        smart_bake: Union[bool, str] = "auto",
    ) -> str:
        """Render the Maya-side conversion script (exposed for tests/preview).

        *smart_bake* (FBX route only): ``"auto"`` bakes driven animation to keys
        via mayatk's ``SmartBake`` only when a cheap probe detects it; ``True``
        always attempts it; ``False`` reproduces the pre-smart-bake plain bake.
        """
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
            if smart_bake not in (False, "auto"):
                self.logger.info(
                    "smart_bake applies to the FBX route only (the USD route bakes "
                    "animation natively); ignored."
                )
        else:
            context["OUT_FBX"] = str(out_path).replace("\\", "/")
            context["EMBED_TEXTURES"] = repr(bool(embed_textures))
            context["SMART_BAKE"] = repr(smart_bake)
        return _templates.ScriptTemplate.render_template(self._template(via), context)

    def convert(
        self,
        src_path: str,
        out_path: str,
        *,
        via: str = "fbx",
        timeout: float = 600,
        **script_opts: Any,
    ) -> "ptk.ScriptRunResult":
        """Convert *src_path* to *out_path* in a fresh ``mayapy`` (blocking)."""
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Maya scene not found: {src}")
        if not src.lower().endswith(SUPPORTED_EXTENSIONS):
            raise ValueError(
                f"Unsupported scene format: {src} (expected {SUPPORTED_EXTENSIONS})"
            )
        mayapy = self.require_mayapy()
        self.logger.info(f"Converting {os.path.basename(src)} via {mayapy} ...")
        # The conversion mayapy is launched FROM Blender, so it inherits Blender's
        # OCIO -- a 2.5-profile config Maya 2025's OCIO 2.3 cannot load, failing
        # color-management init on every conversion. Same hand-off hazard the send
        # path already handles; reuse MayaBridge's helper rather than a second copy
        # (a studio config outside Blender's tree passes through untouched).
        env = dict(MayaBridge._launch_env() or os.environ)
        env.update(_FAST_MAYA_ENV)
        # Smart-bake pre-pass needs mayatk (+ pythontk) importable in the child
        # mayapy — inject their package parents on PYTHONPATH. "auto"/True enable it;
        # False (or the USD route) skips injection entirely. Missing mayatk -> [] ->
        # the template's guarded import degrades to the plain FBX bake.
        smart_bake = script_opts.get("smart_bake", "auto")
        if via == "fbx" and smart_bake is not False:
            extra = _smart_bake_syspath(self._mayatk_path)
            if extra:
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = os.pathsep.join(
                    extra + ([existing] if existing else [])
                )
            elif smart_bake is True:
                self.logger.warning(
                    "smart_bake=True but mayatk could not be located; the conversion "
                    "will fall back to the plain FBX bake."
                )
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
        return ptk.ScriptRunner.run_script_to_artifact(
            app_exe, script_text, artifact=artifact, timeout=timeout, env=env
        )

    @classmethod
    def _cache_key(cls, src: str, script_opts: Dict[str, Any], via: str = "usd") -> str:
        """Deterministic tag for the conversion cache: scene identity (path +
        mtime + size), the Maya-side options that shape the artifact, and the
        conversion template's own identity (per *via*) -- a template fix must
        invalidate stale cached payloads, or a retry after an upgrade replays
        the old bug."""
        return ptk.CachedArtifact.key(
            sorted(script_opts.items()), files=[src, cls._template(via)]
        )

    def _cached_conversion(
        self,
        src: str,
        *,
        via: str,
        use_cache: bool,
        timeout: float,
        script_opts: Dict[str, Any],
    ) -> "ptk.CachedArtifact.Result":
        """The cached FBX/USD conversion of *src*, produced on a miss.

        Shared by :meth:`import_scene` and :meth:`bake_scene`: both need the SAME
        intermediate, so a scene that was already imported bakes without a second Maya
        launch (and without a second license checkout).
        """
        ext = ".usd" if via == "usd" else ".fbx"
        self._template(via)  # validate the route before any work
        return ptk.CachedArtifact("maya_to_btk", extension=ext).get(
            self._cache_key(src, script_opts, via),
            lambda out: self.convert(src, out, via=via, timeout=timeout, **script_opts),
            sidecars=(".manifest.json",),
            use_cache=use_cache and os.path.isfile(src),
        )

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
        smart_bake: Union[bool, str] = "auto",
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
                ``"fbx"`` (default) = the classic material model + texture-
                manifest sidecar rebuilt through ``create_pbr_material``;
                instancing is carried by the FBX format itself, so a Maya
                instance set arrives as linked duplicates with no replay in
                the path.
                ``"usd"`` = ``mayaUSDExport`` → ``wm.usd_import``:
                materials arrive as native UsdPreviewSurface→Principled
                conversions (metallic / roughness / normal textures included,
                no manifest), animated visibility survives, and ShaderFX game
                shaders are translated to standardSurface Maya-side (see the
                template docstrings). Maya instance sets are rebuilt as shared
                mesh datablocks via :meth:`_apply_instance_manifest` — the USD
                is written flattened (mayaUsd's instance path drops materials
                wholesale) and the relationship replayed from the conversion's
                REQUIRED v2 sidecar, GUARANTEED-OR-FAIL: a failed replay
                removes everything it imported and raises instead of leaving
                a silently flattened scene.
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
            smart_bake: Pre-bake driven animation to keys via mayatk's
                ``SmartBake`` before the FBX export, so channels FBX's plain
                bake loses -- inherited visibility, set-driven keys, constraints,
                IK, motion paths, driven blend shapes -- survive the round trip.
                ``"auto"`` (default) does it only when a cheap probe detects such
                animation; ``True`` always attempts it; ``False`` reproduces the
                pre-smart-bake plain bake. FBX route only (inert for USD, which
                bakes animation natively); needs mayatk importable (auto-located,
                or ``mayatk_path`` on the constructor) and degrades to the plain
                bake without it.
            **script_opts: Maya-side knobs (``embed_textures`` /
                ``include_animation``; ``embed_textures`` is FBX-route only).
        """
        from blendertk.env_utils.fbx_utils import FbxUtils

        # smart_bake shapes only the FBX template; keep it out of the USD route's
        # cache key so identical USD conversions can't fragment on an inert option.
        if via == "fbx":
            # Surface the option into the cache key + the Maya-side render context.
            script_opts["smart_bake"] = smart_bake
        elif smart_bake not in (False, "auto"):
            self.logger.info(
                "smart_bake applies to the FBX route only (the USD route bakes "
                "animation natively); ignored."
            )

        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
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

        got = self._cached_conversion(
            src, via=via, use_cache=use_cache, timeout=timeout, script_opts=script_opts
        )
        out_path, tmp = got.path, got.scratch

        # Both routes sidecar what their intermediate cannot carry. FBX: the
        # textures (packed metallic/roughness/ao on translated materials) plus
        # baked visibility. USD: materials arrive natively, but instance
        # RELATIONSHIPS do not survive a flattened export -- they are replayed
        # below as Blender-native shared mesh data.
        manifest_path = out_path + ".manifest.json"
        if via == "usd" and not os.path.isfile(manifest_path):
            # The v2 conversion ALWAYS writes the sidecar (empty groups included)
            # and withholds the USD when it can't -- a missing manifest means a
            # stale or hand-damaged payload, and importing it could silently
            # flatten the scene's instance sets. Refuse before touching the scene.
            raise RuntimeError(
                f"USD conversion sidecar missing: {manifest_path}. Nothing was "
                "imported (a flat import could silently lose instancing); clear "
                "the conversion cache or re-pull via FBX."
            )
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
        if via == "usd":
            # Rebuild Blender-native linked duplicates from Maya's instance
            # sets. GUARANTEED-OR-FAIL: a partially-shared scene renders
            # correctly and only betrays itself when an artist edits one
            # duplicate and its siblings don't follow -- so a failed replay
            # rolls the whole import back and raises.
            try:
                self._apply_instance_manifest(manifest_path, imported)
            except Exception:
                self._rollback_import(imported)
                if tmp is not None and os.path.isfile(out_path):
                    self.logger.warning(
                        f"Keeping intermediate USD for debugging: {out_path}"
                    )
                raise
            # Cosmetic, after the structural work: drop the Empty Blender
            # materializes for the exporter's materials Scope prim ("mtl").
            imported = self._strip_materials_scope(imported, out_path)
        elif os.path.isfile(manifest_path):
            # Node-type tags first (cheap, structural): a ``maya_node_type``
            # custom property on each Empty that was a Maya group/locator, so
            # a later send BACK restores the correct node type. Non-fatal.
            try:
                self._tag_maya_node_types(manifest_path, imported)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Node-type tagging failed ({e}); skipped.")
            # Structurally non-fatal: a bad sidecar must never abort an
            # import whose FBX already landed (materials just stay phong).
            try:
                self._apply_texture_manifest(manifest_path, imported)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"Texture-manifest rebuild failed ({e}); keeping FBX materials."
                )
            # Smart-bake visibility (the manifest's ``visibility`` section — FBX
            # carries the curve, Blender's importer drops it) — replayed as
            # hide_render/hide_viewport keys, shifted by the same anim_offset the
            # FBX importer applied to the transforms (default 1.0). Non-fatal.
            try:
                self._apply_visibility_manifest(
                    manifest_path,
                    imported,
                    frame_offset=float((fbx_options or {}).get("anim_offset", 1.0)),
                )
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Visibility replay failed ({e}); skipped.")
        if cleanup and tmp is not None:
            tmp.cleanup()
        self.logger.info(f"Imported {len(imported)} object(s) from {src_path}.")
        return imported

    @staticmethod
    def _tag_maya_node_types(manifest_path: str, imported: List[Any]) -> int:
        """Stamp ``maya_node_type`` custom props from the manifest's ``transforms``.

        Maya groups (shapeless transforms) and locators travel as identical FBX
        nulls and arrive as look-alike Empties; the conversion sidecar records
        which was which (``scene_node_types`` in the conversion template). The
        custom property persists in the .blend, so the send direction's
        ``empties`` manifest can restore each one as the CORRECT Maya node
        type instead of guessing from the children heuristic. Returns the
        number of Empties tagged. Mirror of the tagging in mayatk's
        ``blender_bridge/templates/import.py`` (the Maya->Blender send).
        """
        import json

        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        types = data.get("transforms") if isinstance(data, dict) else None
        if not types:
            return 0
        tagged = 0
        for obj in imported:
            if getattr(obj, "type", None) != "EMPTY":
                continue
            # Tolerate Blender's rename-on-collision suffix ("grp1.001").
            node_type = types.get(obj.name) or types.get(obj.name.rsplit(".", 1)[0])
            if node_type:
                obj["maya_node_type"] = str(node_type)
                tagged += 1
        return tagged

    def _apply_instance_manifest(self, manifest_path: str, imported: List[Any]) -> int:
        """Rebuild Blender-native linked duplicates from Maya's instance sets.

        The USD export flattens instances -- that is what keeps materials intact
        (mayaUsd's instance path writes none) and what keeps every object
        editable. But flattening alone would hand Blender N independent meshes
        and wreck the scene's memory profile, so the *relationship* travels in
        the conversion sidecar and is replayed here: every transform that shared
        a shape in Maya is pointed at ONE mesh datablock, which is exactly
        Blender's linked-duplicate model (edit one, all follow).

        Per-object materials survive the sharing. Maya allows a different shader
        per instance, and Blender expresses that with an OBJECT-linked material
        slot; the group only stays DATA-linked when every member agreed, so the
        common case keeps the simpler wiring.

        The v2 sidecar records SANITIZED PRIM PATHS -- mayaUSDExport rewrites
        names the prim grammar forbids (probe-verified: ``ref:nsCube`` ->
        ``ref_nsCube``) -- and the matcher resolves each path against the
        imported objects' parent chains with Blender's ``.001`` collision
        suffixes stripped (a ``.NNN`` suffix can ONLY be a collision rename;
        prim names cannot contain dots). Prim paths are unique, so duplicate
        LEAF names (``/g1/wheel`` vs ``/g2/wheel``) stay unambiguous even after
        the importer renames one.

        GUARANTEED-OR-FAIL: any member the import can't account for raises.
        A partially-shared scene renders correctly and only betrays itself when
        an artist edits one duplicate and its siblings don't follow, so the
        conversion must fail loudly instead (callers roll the import back).

        Returns the number of objects re-linked.
        """
        import json
        import re

        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh) or {}
        if not isinstance(data, dict) or data.get("version") != 2 or data.get(
            "format"
        ) != "paths":
            raise RuntimeError(
                "Unsupported instance sidecar (expected a v2 'paths' manifest, "
                f"got version={data.get('version') if isinstance(data, dict) else data!r}). "
                "Stale conversion cache? Clear it or re-pull via FBX."
            )
        groups = data.get("instances") or []
        if not groups:
            return 0

        def stripped(name: str) -> str:
            return re.sub(r"\.\d+$", "", name)

        by_path: Dict[str, Any] = {}
        ambiguous: set = set()
        for o in imported:
            if getattr(o, "type", None) != "MESH":
                continue
            parts, cur = [], o
            while cur is not None:
                parts.append(stripped(cur.name))
                cur = cur.parent
            path = "/" + "/".join(reversed(parts))
            if path in by_path:
                ambiguous.add(path)
            else:
                by_path[path] = o

        wanted = [p for group in groups for p in group]
        problems = sorted({p for p in wanted if p in ambiguous})
        if problems:
            raise RuntimeError(
                "Instance sidecar paths are ambiguous in the import: "
                + ", ".join(problems)
            )
        problems = sorted({p for p in wanted if p not in by_path})
        if problems:
            raise RuntimeError(
                "Instance sidecar paths not found in the import: "
                + ", ".join(problems)
            )

        relinked = 0
        orphaned = []
        for group in groups:
            if len(group) < 2:
                raise RuntimeError(
                    f"Malformed instance sidecar group (needs >= 2 members): {group}"
                )
            objs = [by_path[p] for p in group]
            master, rest = objs[0], objs[1:]
            # Snapshot each follower's materials BEFORE its data is swapped --
            # material_slots is derived from the mesh, so the swap rewrites it.
            per_object = [
                [s.material for s in o.material_slots] for o in rest
            ]
            master_mats = [s.material for s in master.material_slots]
            for obj, mats in zip(rest, per_object):
                if obj.data is master.data:
                    continue
                previous = obj.data
                obj.data = master.data
                orphaned.append(previous)
                relinked += 1
                if mats == master_mats:
                    continue  # agreed with the master -- DATA linkage is right
                for slot, mat in zip(obj.material_slots, mats):
                    if mat is None:
                        continue
                    slot.link = "OBJECT"  # per-instance shader, Blender's way
                    slot.material = mat
        # Drop the meshes the re-link displaced. Blender would not save a
        # 0-user datablock anyway, but leaving them costs the running session
        # the very memory the sharing exists to reclaim -- and the memory
        # profile is the whole point of instancing in a production scene.
        # Guarded on users_count so a datablock something else picked up
        # (a group whose members overlap) is never removed out from under it.
        import bpy

        purged = 0
        for mesh in orphaned:
            try:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
                    purged += 1
            except (ReferenceError, RuntimeError):
                continue  # already gone; nothing to reclaim
        if relinked:
            self.logger.info(
                f"Instances rebuilt: {relinked} object(s) re-linked across "
                f"{len(groups)} Maya instance set(s); {purged} displaced mesh(es) freed."
            )
        return relinked

    def _rollback_import(self, imported: List[Any]) -> None:
        """Remove *imported* objects and the mesh data only they used.

        The failed-replay path must leave the scene as it was -- a
        half-imported scene that LOOKS correct is the outcome the
        guaranteed-or-fail contract exists to prevent. Non-mesh datablocks
        (materials the import created, lights, cameras) are left to Blender's
        0-user reclaim on save; meshes are purged eagerly because the memory
        profile is the point of the instancing work.
        """
        import bpy

        data_blocks = {o.data for o in imported if getattr(o, "data", None)}
        for o in imported:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except (ReferenceError, RuntimeError):
                continue  # already gone
        for block in data_blocks:
            try:
                if isinstance(block, bpy.types.Mesh) and block.users == 0:
                    bpy.data.meshes.remove(block)
            except (ReferenceError, RuntimeError):
                continue

    def _strip_materials_scope(
        self, imported: List[Any], usd_path: str
    ) -> List[Any]:
        """Drop the Empty Blender materializes for a pure-materials Scope prim
        (mayaUSDExport's ``mtl``) and return the surviving imported objects.

        PRIM-PATH KEYED, never name-keyed: only a root-level ``Scope`` prim
        whose whole subtree is material machinery qualifies, so a user object
        that happens to be named ``mtl`` (not in *imported*), or a real
        Xform/Mesh prim named ``mtl``, is untouched. Blender's ``.001``
        collision suffix is stripped before comparing, since the Empty may
        arrive renamed when the open scene already holds that name.

        Cosmetic by contract: any failure logs and keeps everything --
        structural fidelity is the replay's job, not this sweep's.
        """
        import re

        try:
            from pxr import Usd

            stage = Usd.Stage.Open(usd_path)
        except Exception as e:  # noqa: BLE001 -- cosmetic; never fail the import
            self.logger.warning(f"Materials-scope strip skipped ({e}).")
            return imported
        if not stage:
            return imported
        material_types = {"Scope", "Material", "Shader", "NodeGraph"}
        scope_names = set()
        for prim in stage.GetPseudoRoot().GetChildren():
            if prim.GetTypeName() != "Scope":
                continue
            descendants = list(Usd.PrimRange(prim))[1:]
            if descendants and all(
                p.GetTypeName() in material_types for p in descendants
            ):
                scope_names.add(prim.GetName())
        if not scope_names:
            return imported

        import bpy

        kept, removed = [], 0
        for o in imported:
            if (
                getattr(o, "type", None) == "EMPTY"
                and o.parent is None
                and not o.children
                and re.sub(r"\.\d+$", "", o.name) in scope_names
            ):
                try:
                    bpy.data.objects.remove(o, do_unlink=True)
                    removed += 1
                    continue
                except (ReferenceError, RuntimeError):
                    pass
            kept.append(o)
        if removed:
            self.logger.info(
                f"Removed {removed} materials-scope Empty(ies) "
                f"({', '.join(sorted(scope_names))})."
            )
        return kept

    def _plan_with_slot_fallback(
        self, files: List[str], slots: Any, name: str
    ) -> Any:
        """Wiring plan for *files*, rescuing unclassifiable ones via *slots*.

        Filename classification stays authoritative: only a filename reveals how a
        map is PACKED (``MSAO`` wired into a metallic slot is still an MSAO, and
        the channel cannot say so). So the plan is resolved normally first, and the
        manifest's logical channels are consulted **only** for files that landed in
        ``unknown`` -- a plain color map named after a product (``Agilent_PNA.png``)
        carries no map-type token, yet Maya knew it was the ``baseColor``.

        Returns ``None`` when there is nothing to add, so the caller's
        ``create_pbr_material`` resolves the plan itself exactly as before.
        """
        if not slots or not isinstance(slots, dict):
            return None

        import pythontk as ptk
        from blendertk.mat_utils._mat_utils import MatUtils

        plan = MatUtils.resolve_pbr_plan(files)
        unknown = list(plan.get("unknown") or [])
        if not unknown:
            return plan

        by_path = {}
        for channel, path in slots.items():
            if path:
                by_path.setdefault(os.path.normcase(os.path.abspath(path)), channel)

        rescued = {}
        for path in unknown:
            channel = by_path.get(os.path.normcase(os.path.abspath(path)))
            map_type = ptk.MapRegistry.resolve_type_from_channel(channel)
            # Never displace a map the filename already resolved.
            if map_type and map_type not in plan["by_type"]:
                plan["by_type"][map_type] = path
                rescued[map_type] = path

        if rescued:
            claimed = set(rescued.values())
            plan["unknown"] = [p for p in unknown if p not in claimed]
            self.logger.info(
                f"{name}: {len(rescued)} unclassifiable texture(s) rebuilt from the "
                f"manifest's shader slots ({', '.join(sorted(rescued))})."
            )
        return plan

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

        from blendertk.mat_utils._mat_utils import MatUtils

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            self.logger.warning(
                f"Texture manifest unreadable ({e}); keeping FBX materials."
            )
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
                plan = self._plan_with_slot_fallback(files, entry.get("slots"), name)
                material = MatUtils.create_pbr_material(files, name=name, plan=plan)
                if material is None:  # nothing classified -- keep the FBX phong
                    self.logger.warning(
                        f"{name}: no texture classified by filename and no "
                        "authoritative slot in the manifest; keeping the "
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
                    # Purge FIRST: the source name is only free once the
                    # FBX-carried material holding it is gone.
                    self._purge_orphans(replaced)
                    self._claim_material_name(material, name)
                    self.logger.info(
                        f"Rebuilt material {material.name} from {len(files)} "
                        f"file(s) into {swapped} slot(s)."
                    )
                    continue

                # Fallback (importer renamed the material): whole-object assign.
                targets = [
                    obj
                    for member in entry.get("objects", [])
                    for obj in by_short.get(member, [])
                ]
                if not targets:
                    self._purge_orphans([material])  # nothing to attach it to
                    self.logger.warning(f"{name}: no matching slot or object found.")
                    continue
                MatUtils.assign_mat(targets, material)
                self._claim_material_name(material, name)
                self.logger.info(
                    f"Rebuilt material {material.name} from {len(files)} file(s) "
                    f"on {len(targets)} object(s) (object-level fallback)."
                )
            except Exception as e:
                self.logger.warning(f"Manifest entry {name} skipped: {e}")

    def _apply_visibility_manifest(
        self, manifest_path: str, imported: List[Any], frame_offset: float = 1.0
    ) -> None:
        """Replay the manifest's ``visibility`` section (smart-bake output) as
        ``hide_render`` / ``hide_viewport`` keyframes on the imported objects.

        FBX carries a visibility curve but Blender's FBX importer silently drops it
        (verified empirically), so baked visibility — chiefly *inherited* visibility,
        which Maya's own FBX exporter never writes for the child at all — reaches
        Blender through the conversion's ``.manifest.json`` (one sidecar, shared
        with the texture section) instead of the FBX stream. Shared by the direct
        import path and the ``.blend`` bake template, exactly like
        :meth:`_apply_texture_manifest`, so there is one copy of the logic.

        *frame_offset* MUST match the FBX importer's ``anim_offset`` (default 1.0):
        Blender shifts every FBX-imported curve by that many frames (Maya frame N
        lands on Blender frame N + anim_offset — verified), but these visibility
        values arrive as raw Maya frames, so the same shift is applied here to keep
        the show/hide aligned with the transform animation. Omitting it desyncs
        visibility by a frame.

        Best-effort by contract: a failed replay must never break an import whose
        geometry + transform animation already landed. Objects match by SHORT name
        (the importer may suffix ``.001``), the same convention the texture manifest
        uses. Values are Maya ``.visibility`` (``0`` = hidden); interpolation is
        forced CONSTANT (visibility is boolean — Bezier would ramp it)."""
        import json

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            self.logger.warning(f"Visibility manifest unreadable ({e}); skipped.")
            return
        vis = data.get("visibility", {}) if isinstance(data, dict) else {}
        if not vis:
            return
        try:
            import bpy  # noqa: F401
        except ImportError:
            return  # no Blender (the .venv degradation path) -> nothing to key

        by_short: Dict[str, List[Any]] = {}
        for obj in imported:
            by_short.setdefault(obj.name.split(".")[0], []).append(obj)

        keyed = 0
        for short, keys in vis.items():
            for obj in by_short.get(short, []):
                try:
                    for frame, value in keys:
                        hidden = float(value) == 0.0  # Maya .visibility 0 = hidden
                        obj.hide_render = hidden
                        obj.hide_viewport = hidden
                        obj.keyframe_insert("hide_render", frame=frame + frame_offset)
                        obj.keyframe_insert("hide_viewport", frame=frame + frame_offset)
                    self._step_visibility_fcurves(obj)
                    keyed += 1
                except Exception as e:  # noqa: BLE001
                    self.logger.debug(f"Visibility replay skipped for {obj.name}: {e}")
        if keyed:
            self.logger.info(
                f"Replayed baked visibility onto {keyed} object(s) "
                "(hide_render / hide_viewport)."
            )

    @staticmethod
    def _step_visibility_fcurves(obj: Any) -> None:
        """Force CONSTANT interpolation on *obj*'s ``hide_*`` fcurves — visibility is
        boolean, so the default Bezier would ramp the toggle. Handles the Blender
        4.4+/5.x slotted-action layout (layer → strip → channelbag) and the legacy
        ``action.fcurves``."""
        ad = getattr(obj, "animation_data", None)
        action = getattr(ad, "action", None) if ad else None
        if action is None:
            return
        fcurves: List[Any] = []
        for layer in getattr(action, "layers", []) or []:
            for strip in getattr(layer, "strips", []) or []:
                for cbag in getattr(strip, "channelbags", []) or []:
                    fcurves.extend(cbag.fcurves)
        try:
            fcurves.extend(action.fcurves)
        except (AttributeError, TypeError):
            pass
        for fc in fcurves:
            if fc.data_path in ("hide_render", "hide_viewport"):
                for kp in fc.keyframe_points:
                    kp.interpolation = "CONSTANT"

    def _claim_material_name(self, material: Any, desired: str) -> None:
        """Rename *material* to *desired* once that name is free.

        The rebuild is necessarily created while the FBX-carried material still
        owns the name, so Blender hands it the clash spelling ("M_x.001"); the
        FBX one is purged moments later and the name falls free. Reclaiming it
        keeps the hand-off non-destructive -- downstream (a game engine, a
        material library, the next round-trip) binds by material NAME, and the
        suffix compounds on every re-import.

        Yields silently whenever the name is still taken: the object-level
        fallback runs exactly when the FBX material was never matched, so it may
        still be in use and keeps its claim. Cosmetic and best-effort; the
        material is already correctly assigned either way. Mirror of the
        Maya-side ``BlenderSceneImport._claim_material_name``.
        """
        try:
            import bpy
        except ImportError:
            return
        if not desired or material is None or material.name == desired:
            return
        try:
            if desired in bpy.data.materials:
                return
            material.name = desired
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Name reclaim skipped: {e}")

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
                    n.image
                    for n in (mat.node_tree.nodes if mat.node_tree else [])
                    if getattr(n, "image", None) is not None
                ]
                bpy.data.materials.remove(mat)
                for img in images:
                    if img.users == 0:
                        bpy.data.images.remove(img)
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Orphan purge skipped: {e}")

    # ------------------------------------------------------------------ bake (FBX -> .blend)
    @property
    def blender_path(self) -> Optional[str]:
        """The Blender executable used for the bake — this host's own binary.

        Unlike :attr:`maya_path` (a foreign app, discovered through an ``AppSpec``), the
        bake runs the SAME Blender the panel runs in, so the binary is already known:
        ``bpy.app.binary_path``. Falls back to ``PATH`` for plain-venv/test contexts.
        """
        if not self._blender_path:
            try:
                import bpy

                self._blender_path = bpy.app.binary_path or None
            except Exception:
                self._blender_path = None
            if not self._blender_path:
                self._blender_path = shutil.which("blender")
        return self._blender_path

    @blender_path.setter
    def blender_path(self, value: Optional[str]) -> None:
        self._blender_path = value

    def require_blender(self) -> str:
        """Return :attr:`blender_path` or raise an error naming what's missing."""
        blender_exe = self.blender_path
        if not blender_exe:
            raise FileNotFoundError(
                "No Blender executable found for the bake (bpy.app.binary_path is empty "
                "and 'blender' is not on PATH). Set MayaSceneImport.blender_path."
            )
        return blender_exe

    def render_bake_script(self, src_path: str, out_path: str) -> str:
        """Render the Blender-side intermediate->.blend bake script (exposed for
        tests/preview). *src_path* may be a USD or FBX intermediate -- the template
        dispatches on extension."""
        return _templates.ScriptTemplate.render_template(
            _BAKE_TEMPLATE,
            {
                "SRC_FILE": str(src_path).replace("\\", "/"),
                "OUT_BLEND": str(out_path).replace("\\", "/"),
                # The child is the same Blender build, so the parent's sys.path entries
                # are valid there -- this is what makes the shared manifest replay
                # (blendertk in the child) reliable rather than best-effort.
                "EXTRA_SYS_PATH": repr(list(sys.path)),
            },
        )

    def bake(self, src_path: str, out_path: str, *, timeout: float = 600) -> Any:
        """Bake the USD/FBX intermediate *src_path* into the .blend at *out_path*
        in a fresh headless Blender."""
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Bake source not found: {src}")
        blender_exe = self.require_blender()
        self.logger.info(f"Baking {os.path.basename(src)} to .blend via {blender_exe} ...")
        result = self._run_bake_script(
            blender_exe,
            self.render_bake_script(src, out_path),
            artifact=out_path,
            timeout=timeout,
        )
        self.logger.info(
            f"Baked to .blend in {result.duration:.1f}s "
            f"({os.path.getsize(result.artifact) // 1024} KB)."
        )
        return result

    # Seam for tests (stub the Blender run without patching pythontk internals).
    @staticmethod
    def _run_bake_script(app_exe, script_text, *, artifact, timeout, env=None):
        return ptk.ScriptRunner.run_script_to_artifact(
            app_exe,
            script_text,
            artifact=artifact,
            launch_args=lambda script_path: [*_BAKE_LAUNCH_ARGS, script_path],
            timeout=timeout,
            env=env,
        )

    def bake_scene(
        self,
        src_path: str,
        *,
        via: str = "fbx",
        use_cache: bool = True,
        timeout: float = 600,
        smart_bake: Union[bool, str] = "auto",
        **script_opts: Any,
    ) -> str:
        """Bake *src_path* to a cached ``.blend`` and return its path — the link path.

        Blender can only link a ``.blend``, so a foreign row's reference toggle needs a
        native stand-in: ``.ma``/``.mb`` are converted to an FBX (default) or USD
        intermediate in a headless Maya (the cached intermediate :meth:`import_scene`
        already uses), then that intermediate is baked into a ``.blend`` in a headless
        Blender. An ``.fbx`` source skips straight to the bake — no Maya, no license.

        Both stages are cached independently, and the bake's key includes the
        intermediate's identity **and the bake template's**, so a template fix
        invalidates stale bakes (a retry after an upgrade must not replay the old bug).

        Parameters:
            src_path: A ``.ma`` / ``.mb`` / ``.fbx`` file.
            via: Conversion intermediate for ``.ma``/``.mb`` sources — ``"fbx"``
                (default: format-native instancing + classic model / manifest
                replay) or ``"usd"`` (native materials / animation / visibility,
                instances rebuilt guaranteed-or-fail from the conversion's
                required sidecar; see :meth:`import_scene`).
            use_cache: Reuse a prior conversion + bake of the identical source.
            timeout: Max seconds for EACH headless stage.
            smart_bake: Pre-bake driven animation to keys via mayatk's
                ``SmartBake`` before the FBX export (see :meth:`import_scene`).
                ``"auto"`` (default) acts only when a cheap probe detects it;
                FBX route only — inert for ``via="usd"`` (which samples animation
                natively) and for an ``.fbx`` source (no Maya stage to bake in).
            **script_opts: Maya-side conversion knobs (``embed_textures`` /
                ``include_animation``); inert for an ``.fbx`` source.

        Returns:
            str: Path to the cached ``.blend`` — pass it to
            :func:`blendertk.link_blend_file`.
        """
        src = os.path.abspath(os.path.expanduser(os.path.expandvars(str(src_path))))
        ext = os.path.splitext(src)[1].lower()
        if ext not in BAKE_SOURCE_EXTENSIONS:
            raise ValueError(
                f"Unsupported bake source: {src} (expected {BAKE_SOURCE_EXTENSIONS})"
            )
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Scene not found: {src}")

        if ext == ".fbx":
            inter_path, conversion = src, None
        else:
            if via == "fbx":  # FBX-only option — keep out of the USD cache key
                script_opts["smart_bake"] = smart_bake
            elif smart_bake not in (False, "auto"):
                self.logger.info(
                    "smart_bake applies to the FBX route only (the USD route bakes "
                    "animation natively); ignored."
                )
            conversion = self._cached_conversion(
                src,
                via=via,
                use_cache=use_cache,
                timeout=timeout,
                script_opts=script_opts,
            )
            inter_path = conversion.path
            if via == "usd" and not os.path.isfile(inter_path + ".manifest.json"):
                # The v2 conversion always writes the sidecar; without it the
                # bake could silently cache a flattened .blend (see the bake
                # template's apply_instances).
                raise RuntimeError(
                    f"USD conversion sidecar missing: {inter_path}.manifest.json. "
                    "Refusing to bake (a flat bake could silently lose "
                    "instancing); clear the conversion cache or bake via FBX."
                )

        got = ptk.CachedArtifact("maya_bake_btk", extension=".blend").get(
            ptk.CachedArtifact.key(files=[inter_path, _BAKE_TEMPLATE]),
            lambda out: self.bake(inter_path, out, timeout=timeout),
            use_cache=use_cache,
        )
        # The intermediate scratch is consumed once the bake has read it; the .blend
        # scratch is NOT cleaned up -- the caller links that file, so it must outlive
        # this call (an uncached bake therefore lives under the scoped store's stale
        # sweep).
        if conversion is not None and conversion.scratch is not None:
            conversion.scratch.cleanup()
        # Rewritten on a cache hit too: cheap, and it self-heals a sidecar lost to a
        # partial sweep (without it the panel silently forgets the row is linked).
        self._write_bake_source(got.path, src)
        self.logger.info(f"Baked {src_path} -> {got.path}")
        return got.path

    def _write_bake_source(self, baked_path: str, src: str) -> None:
        """Record beside *baked_path* which foreign scene it was baked from."""
        import json

        try:
            with open(baked_path + BAKE_SOURCE_SUFFIX, "w", encoding="utf-8") as fh:
                json.dump({"source": os.path.abspath(src)}, fh)
        except OSError as e:  # cosmetic bookkeeping — never fail a completed bake
            self.logger.debug(f"Could not write the bake source sidecar: {e}")

    @staticmethod
    def bake_source(baked_path: str) -> Optional[str]:
        """The foreign scene *baked_path* was baked from, or None if it is not a bake.

        The inverse of :meth:`bake_scene` — lets a browser map a linked library back to
        the source row the user actually sees.
        """
        import json

        try:
            with open(baked_path + BAKE_SOURCE_SUFFIX, "r", encoding="utf-8") as fh:
                return json.load(fh).get("source") or None
        except (OSError, ValueError):
            return None

    @staticmethod
    def mayapy_from_maya_exe(maya_exe: str) -> Optional[str]:
        """Return the ``mayapy`` interpreter beside *maya_exe*, or ``None`` if absent.

        Owned by :meth:`MayaBridge.mayapy_from_maya_exe` (with the rest of the Maya
        discovery config, which both directions share); kept here so the pull
        direction's callers have one obvious entry point.
        """
        return MayaBridge.mayapy_from_maya_exe(maya_exe)

    # ------------------------------------------------------------------ discovery (browser API)
    @staticmethod
    def scene_has_complex_animation(src_path: str) -> bool:
        """Cheap pre-conversion probe: does the scene declare *driven* animation the
        plain FBX round trip would lose (constraints, set-driven keys, expressions,
        IK, motion paths, or keyed visibility)? Lets a browser prompt bake-vs-raw
        WITHOUT launching Maya.

        A ``.ma`` gets a ``createNode``-line scan mirroring the node-type signals
        of the conversion template's Maya-side :func:`_detect_complex_anim` (the
        authoritative check, run during the actual bake); a ``.mb`` gets a chunked
        byte scan for the same type names (stored as plain strings in the binary
        IFF blocks) — a heuristic whose rare false positive (the token appearing
        in string data) only costs an unnecessary bake attempt, never a wrong
        result, because the Maya-side probe re-decides authoritatively under
        ``"auto"``. Both early-exit on the first hit. Returns ``False`` for
        ``.fbx`` (already baked — no Maya drivers)."""
        ext = os.path.splitext(str(src_path))[1].lower()
        if ext not in SUPPORTED_EXTENSIONS or not os.path.isfile(src_path):
            return False
        if ext == ".mb":
            return MayaSceneImport._mb_declares_drivers(src_path)
        try:
            with open(src_path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if not line.startswith("createNode "):
                        continue
                    parts = line.split(None, 2)
                    if len(parts) < 2:
                        continue
                    node_type = parts[1]
                    if node_type in _DRIVER_NODE_TYPES:
                        return True
                    # Keyed visibility: Maya auto-names the curve ``<node>_visibility``.
                    if node_type == "animCurveTU" and '_visibility"' in line:
                        return True
        except OSError:
            return False
        return False

    @staticmethod
    def _mb_declares_drivers(src_path: str, chunk_size: int = 1 << 20) -> bool:
        """Chunked byte scan of a ``.mb`` for :data:`_MB_DRIVER_TOKENS`.

        Overlapping reads so a token straddling a chunk boundary still matches;
        early-exits on the first hit, so the common driven scene reads only its
        head. Bounded memory on multi-GB scenes."""
        overlap = max(len(t) for t in _MB_DRIVER_TOKENS) - 1
        try:
            with open(src_path, "rb") as fh:
                tail = b""
                while True:
                    chunk = fh.read(chunk_size)
                    if not chunk:
                        return False
                    window = tail + chunk
                    if any(token in window for token in _MB_DRIVER_TOKENS):
                        return True
                    tail = window[-overlap:]
        except OSError:
            return False

    @staticmethod
    def find_scenes(
        root_dir: str,
        recursive: bool = False,
        extensions: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Every importable Maya scene (``.ma`` / ``.mb``) under *root_dir* — sorted abs paths.

        The discovery half of the import: pairs with :meth:`import_scene` so a browser can
        list convertible Maya scenes with one call, using the SAME extension set the importer
        accepts. USD sources are import-capable too but are not *Maya scenes*, so they are
        intentionally excluded here (list them from their own project, not as "Maya files").

        *extensions* narrows or widens that default — a browser listing *bakeable* rows
        passes :data:`BAKE_SOURCE_EXTENSIONS` (which adds ``.fbx``), or the subset the
        user has enabled.
        """
        if not (root_dir and os.path.isdir(root_dir)):
            return []
        inc = [f"*{ext}" for ext in (extensions or SUPPORTED_EXTENSIONS)]
        found = ptk.FileUtils.get_dir_contents(
            root_dir, content="filepath", recursive=recursive, inc_files=inc
        )
        return sorted(os.path.normpath(p) for p in found)


__all__ = ["MayaSceneImport"]
