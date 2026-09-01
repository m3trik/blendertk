# !/usr/bin/python
# coding=utf-8
"""Core blendertk utilities — DCC-environment info + cross-cutting decorators.

Mirrors the mayatk ``CoreUtils`` public surface (``btk.undoable`` ↔ ``mtk.undoable``,
``btk.get_env_info`` ↔ ``mtk.get_env_info``) so the shared tentacle slots stay branch-free.

``import bpy`` is deferred into the call bodies so importing this module (and resolving
the package surface) never requires a running Blender — matching the ecosystem's
no-import-side-effects rule.
"""

import os
import re
from contextlib import contextmanager
from functools import wraps

import pythontk as ptk

_DUP_SUFFIX_RE = re.compile(r"\.\d{3}$")


# NOTE: ``export_selection_fbx`` moved to ``env_utils/fbx_utils.py`` (consolidated into
# ``FbxUtils`` with ``import_fbx``, mirroring mayatk's FBX home). Reached as
# ``btk.FbxUtils.export_selection_fbx`` (class-only — a generic ``export`` would collide flat).


_SCENE_SECTIONS = (
    "summary",
    "fix_first",
    "pareto",
    "offenders",
    "categories",
    "textures",
    "pipeline",
    "assumptions",
)
_GENERIC_TRI_BUDGET = 100_000  # flat per-object triangle budget (Generic profile)


class _CoreUtilsInternal(object):
    """Internal helpers for CoreUtils."""

    @staticmethod
    def _object_mode(fn):
        """Run ``fn`` in OBJECT mode, restoring the caller's prior mode afterward.

        Blender's object operators (``transform_apply``, ``origin_set``, ``modifier_apply``) require
        OBJECT mode and raise from a component/edit context. This guard makes the helpers that wrap
        them safe to call from anywhere. Shared by ``xform_utils`` and ``edit_utils``.

        The whole body runs under :func:`window_context_override`: the guarded helpers (and the
        ``mode_set`` calls here) invoke operators whose poll/exec read *screen-context* members
        (``edit_object``, ``selected_editable_objects``), which are dead when tentacle drives a slot
        from its Qt event-pump timer (``bpy.context.window`` is ``None``) — the mode switch itself
        poll-failed from Edit Mode in exactly that state. The override is a no-op when a window is
        already active, so decorated helpers stay headless-safe.
        """

        @wraps(fn)
        def wrapper(*args, **kwargs):
            import bpy

            with CoreUtils.window_context_override():
                view_layer = bpy.context.view_layer
                active = view_layer.objects.active
                prior = getattr(active, "mode", "OBJECT")
                if prior != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                try:
                    return fn(*args, **kwargs)
                finally:
                    if prior != "OBJECT" and active is not None:
                        # fn may have re-activated one of its targets (the helpers select what they
                        # operate on); mode_set acts on the ACTIVE object, so restore the caller's
                        # active first or the wrong object ends up in edit mode.
                        try:
                            view_layer.objects.active = active
                            bpy.ops.object.mode_set(mode=prior)
                        except (RuntimeError, ReferenceError):
                            pass  # active was deleted by fn, or the mode no longer applies

        return wrapper

    @staticmethod
    def _blender_python_exe():
        """Blender's *bundled* Python interpreter (``sys.executable`` is the Blender binary, not a
        python — so it can't be driven with ``-m pip``). Looks in ``<sys.prefix>/bin`` for the
        python launcher; falls back to ``sys.executable``. Mirrors ``tcl_blender._QtBootstrap.blender_python_exe``
        but kept local so blendertk carries no back-dependency on tentacle."""
        import sys

        for name in ("python.exe", "python3.exe", "python", "python3"):
            exe = os.path.join(sys.prefix, "bin", name)
            if os.path.isfile(exe):
                return exe
        return sys.executable

    @staticmethod
    def _engine_install_dirs():
        """(target, legacy) provisioning dirs for on-demand installs, from ``bpy``.

        *target* is ``<user scripts>/addons/modules`` — Blender puts it on
        ``sys.path`` natively (even before it exists) **after** the bundled
        site-packages, so nothing provisioned there can ever shadow a dist
        Blender ships (probed 2026-08-24: idx 11 vs bundled idx 9). *legacy* is
        the old ``<user scripts>/modules`` target — that one sits at ``sys.path``
        index 0, AHEAD of the bundled site-packages, which is exactly the
        shadowing hazard the move exists to close.

        Raises whatever ``bpy`` raises outside Blender — callers gate on that.
        """
        import os
        import bpy

        target = bpy.utils.user_resource("SCRIPTS", path="addons/modules", create=True)
        legacy = os.path.join(bpy.utils.user_resource("SCRIPTS"), "modules")
        return os.path.normpath(target), os.path.normpath(legacy)

    @staticmethod
    def _ensure_packages(pkgs, add_to_path=True):
        """Install any of ``{pip_spec: import_name}`` that is not importable.

        Blender's provisioning policy, package-agnostic: resolver-aware install
        into the per-version ``addons/modules`` dir (natively on ``sys.path`` at
        TAIL precedence — see :meth:`_engine_install_dirs`), driven against
        Blender's **bundled** interpreter via
        :meth:`pythontk.PackageManager.install_targeted` — pip's own resolver
        plans against the bundled site-packages, so a dep Blender already ships
        (numpy) is never re-downloaded or duplicated the way a raw
        ``pip install --target`` would. Then re-resolve importability.
        Backs both :meth:`CoreUtils.ensure_packages` and the Pillow-specific
        :meth:`CoreUtils.ensure_image_deps`.
        """
        import os
        import sys
        import logging
        import importlib
        import importlib.util

        def _available():
            names = []
            for imp in pkgs.values():
                try:
                    if importlib.util.find_spec(imp) is not None:
                        names.append(imp)
                except (ImportError, ValueError):
                    pass
            return names

        available = _available()
        missing = [spec for spec, imp in pkgs.items() if imp not in available]
        if not missing:
            return available

        try:
            install_dir, legacy_dir = _CoreUtilsInternal._engine_install_dirs()
        except Exception:
            # Not in Blender — the caller's interpreter must supply these.
            return available

        log = logging.getLogger(__name__)
        try:
            if any(n.endswith(".dist-info") for n in os.listdir(legacy_dir)):
                # Installs from the pre-2026-08 policy: they sit AHEAD of the
                # bundled site-packages and can shadow dists Blender ships.
                log.warning(
                    f"[ensure_packages] pip-installed content found in the legacy "
                    f"top-precedence dir {legacy_dir!r}; it can shadow Blender's "
                    f"bundled packages — consider moving it to {install_dir!r}."
                )
        except OSError:
            pass

        pm = ptk.PackageManager(python_path=_CoreUtilsInternal._blender_python_exe())
        try:
            pm.install_targeted(missing, install_dir)
        except Exception as error:
            # The actual install is reported by the importability re-check
            # below, so a pip-layer complaint is debug-level.
            log.debug(f"[ensure_packages] pip note for {missing!r}: {error}")

        # Trust the import, not the exit code: make the dir importable and re-resolve.
        # APPEND, never insert(0): the bundled site-packages must keep import
        # precedence over anything provisioned here.
        # normcase as well as normpath: Windows paths differing only in case are the
        # same dir, and appending a second spelling would shadow-by-duplicate.
        if add_to_path:
            seen = {os.path.normcase(os.path.normpath(q)) for q in sys.path}
            if os.path.normcase(install_dir) not in seen:
                sys.path.append(install_dir)
        importlib.invalidate_caches()
        return _available()

    @staticmethod
    def _rebind_pil_globals():
        """Re-bind PIL globals in pythontk's already-imported image modules.

        pythontk's image modules do ``try: from PIL import Image …; except: Image = None`` *at import
        time*. In Blender ``import pythontk`` runs at startup — before :func:`ensure_image_deps` can
        provision Pillow — so those modules cache ``Image = None`` and the already-loaded
        ``ImgUtils`` / ``MapFactory`` classes keep seeing "no PIL" even once it's installed and on
        ``sys.path``. Patch the un-provisioned names in place (never clobbering a working binding) —
        surgical, and avoids a fragile module reload that would desync the cached ``ptk.MapFactory``
        reference.

        Two passes, because there are two un-provisioned states:

        * **``None``** — the guard created the name. Repaired across *every* loaded ``pythontk``
          module, not a hand-listed few: seven modules carry such a guard today (``_img_utils``,
          ``_map_factory``, ``processor``, ``map_optimizer``, ``region_masks``, ``mask_generator``,
          ``ktx2_encoder``) and a list would go stale the next time one is added. Only a name the
          module itself set to ``None`` is touched, so this can't reach anything it shouldn't.
        * **absent** — the guard imported the name but never bound it (a pre-2026-08-21 pythontk
          assigns only ``Image = None`` for a six-name import). An undefined name is a ``NameError``
          at its call site that the ``None`` pass can never reach — it is what stopped the Material
          Updater packing Metallic/Smoothness *with Pillow installed*. pythontk binds them all now;
          blendertk runs against whatever pythontk the user has, so repair it here too, for the
          modules known to have carried that shape.
        """
        import sys

        try:
            from PIL import (
                Image,
                ImageOps,
                ImageEnhance,
                ImageFilter,
                ImageChops,
                ImageDraw,
                ImageMode,
            )
        except Exception:
            return
        names = {
            "Image": Image,
            "PILImage": Image,
            "ImageOps": ImageOps,
            "ImageEnhance": ImageEnhance,
            "ImageFilter": ImageFilter,
            "ImageChops": ImageChops,
            "ImageDraw": ImageDraw,
            "ImageMode": ImageMode,
        }
        # Both passes read ``mod.__dict__`` rather than ``getattr``: that is the exact
        # question being asked (did this module itself bind the name?), and it cannot be
        # answered by a lazy ``__getattr__`` — which pythontk's package roots DO install
        # (``bootstrap_package(allow_getattr=True)``), so a getattr sweep would run their
        # resolver once per miss per module, and this helper promises never to raise.
        # Pass 1 — every loaded pythontk module, None-valued names only.
        for modname, mod in list(sys.modules.items()):
            if mod is None or not (
                modname == "pythontk" or modname.startswith("pythontk.")
            ):
                continue
            globals_ = mod.__dict__
            for name, obj in names.items():
                # Bound BY THIS MODULE and bound to None -- an absent name is pass
                # 2's case, and binding it here would inject PIL names into modules
                # that never imported them.
                if name in globals_ and globals_[name] is None:
                    setattr(mod, name, obj)
        # Pass 2 — the multi-name guards, whose unbound names pass 1 cannot see.
        for modname in (
            "pythontk.img_utils._img_utils",
            "pythontk.core_utils.engines.textures.map_factory._map_factory",
            "pythontk.core_utils.engines.textures.map_factory.processor",
        ):
            mod = sys.modules.get(modname)
            if mod is None:
                continue
            for name, obj in names.items():
                if name not in mod.__dict__:
                    setattr(mod, name, obj)

    @staticmethod
    def _active_view_layer():
        """The active view layer, resolved **without** depending on a context *window*.

        ``bpy.context.selected_objects`` / ``active_object`` are *screen-context* members: they are
        populated only when ``bpy.context.window`` is non-``None``. tentacle drives the Blender slots
        from Qt events delivered inside a ``bpy.app.timers`` callback (see
        ``tcl_blender._QtHost.start_pump``) — a context whose ``window`` is frequently ``None`` (proven:
        with ``window=None`` those members return ``[]`` / ``None`` while a cube is selected). The view
        layer is window-independent, so reading selection through ``view_layer.objects`` is correct from
        that context. Falls back to the scene's first view layer if even ``context.view_layer`` is unset.
        """
        import bpy

        vl = getattr(bpy.context, "view_layer", None)
        if vl is not None:
            return vl
        scene = getattr(bpy.context, "scene", None)
        view_layers = getattr(scene, "view_layers", None) if scene else None
        return view_layers[0] if view_layers else None

    @staticmethod
    def _mesh_face_counts(mesh) -> tuple[int, int]:
        """Fan-triangle and ngon counts for one mesh datablock, in a single C-level fetch.

        The naive form (``sum(max(len(p.vertices) - 2, 0) for p in me.polygons)``) pays a
        Python-level RNA property read per polygon and was reimplemented at five call sites.
        ``polygons.foreach_get("loop_total", buf)`` pulls every per-face corner count in ONE
        call, so the arithmetic vectorises: measured 6-12x faster on 10k-100k-poly meshes
        (see the fan-count parity test in ``test/test_core_utils.py``).

        The counts are exactly those of the loop form: a polygon fan-triangulates into
        ``n - 2`` triangles, and a face with more than four corners is an ngon.

        Parameters:
            mesh: A ``bpy.types.Mesh`` datablock (``obj.data``). ``None``, an object with no
                ``polygons``, or an empty mesh yields ``(0, 0)``. A non-bpy sequence (test
                double) falls back to the per-face read.

        Returns:
            ``(triangles, ngons)``.
        """
        polys = getattr(mesh, "polygons", None)
        if polys is None:
            return 0, 0
        count = len(polys)
        if not count:
            return 0, 0
        foreach_get = getattr(polys, "foreach_get", None)
        if foreach_get is None:  # mock/test double -- no RNA buffer protocol
            totals = [len(getattr(p, "vertices", ())) for p in polys]
            return sum(max(n - 2, 0) for n in totals), sum(1 for n in totals if n > 4)

        import numpy as np

        totals = np.empty(count, dtype=np.int32)
        foreach_get("loop_total", totals)
        return int(np.maximum(totals - 2, 0).sum()), int((totals > 4).sum())


class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal):
    """Blender ``CoreUtils`` — extends pythontk's DCC-agnostic ``CoreUtils`` (mirrors
    ``mayatk.CoreUtils(ptk.CoreUtils, ...)``), inheriting the shared helpers and adding the
    Blender-specific ones rather than duplicating logic (SSoT).

    The Blender helpers are also exposed module-level (``btk.undoable`` / ``btk.get_env_info``)
    so slots can call either form, matching mayatk.
    """

    @staticmethod
    def strip_dup_suffix(name: str) -> str:
        """Strip Blender's ``.NNN`` name-collision suffix (``Cube.001`` -> ``Cube``).

        Blender appends ``.001``/``.002``/… when a new datablock's name collides with an existing
        one; this returns the base name. The single SSoT for that convention across blendertk (used
        by the scene exporter's duplicate-name guard and the hierarchy sync's pull matching).
        """
        return _DUP_SUFFIX_RE.sub("", name)

    @staticmethod
    @contextmanager
    def undo_chunk(name: str = ""):
        """Collapse every change made inside the block into ONE Blender undo step.

        Context-manager mirror of mayatk's ``CoreUtils.undo_chunk`` (name + behavior,
        not mechanism) so the shared tentacle slots — and controllers ported from
        mayatk — can wrap a mutation sequence with ``with undo_chunk():`` unchanged.

        Blender exposes no Python-callable "begin/end undo group" bracket (``bpy.ops.ed``
        has ``undo``/``undo_history``/``undo_push``/``undo_redo`` only — verified live).
        The documented technique for collapsing a mixed raw-``bpy.data`` + operator-call
        sequence into one step is to toggle ``bpy.context.preferences.edit.use_global_undo``
        off for the duration (which suppresses the steps nested operators would each push,
        without disabling the operators) and push exactly one consolidated step on exit.
        A no-op outside Blender (headless import / no ``bpy``).
        """
        try:
            import bpy
        except Exception:
            bpy = None
        prefs = None
        prior_global_undo = None
        if bpy is not None:
            try:
                prefs = bpy.context.preferences.edit
                prior_global_undo = prefs.use_global_undo
                prefs.use_global_undo = False
            except Exception:
                prefs = None
        try:
            yield
        finally:
            if prefs is not None:
                try:
                    prefs.use_global_undo = prior_global_undo
                except Exception:
                    pass
            if bpy is not None:
                try:
                    bpy.ops.ed.undo_push(message=name or "blendertk op")
                except Exception:
                    pass

    @staticmethod
    @contextmanager
    def visible_override(objects):
        """Yield with *objects* temporarily visible, selectable and renderable.

        Hiding is not a property an object-processing operator should have an
        opinion about, but Blender makes it one: ``bpy.ops.object.mode_set``
        refuses a hidden object outright (``poll() ... Cannot edit hidden
        object``), and ``bpy.ops.object.bake`` silently returns an empty image
        for one carrying ``hide_render``. So a single hidden mesh anywhere in a
        batch either kills the whole run or -- worse -- ships a black map for
        itself. Both are wrong: a mesh hidden for authoring convenience (or by
        the visibility manifest the Maya bridge replays, where hiding may be
        *animated* and the object is on screen at some other frame) is still
        part of the export set and still needs its unwrap and its lightmap.

        Scoped deliberately narrow -- the caller reveals ONE object for the
        duration of that object's own operation. Revealing a whole batch would
        let geometry the scene hides start occluding and bouncing light into
        every other object's bake, which is a lighting change, not a fix.

        A collection's own hide flags override the object's, so clearing the
        object's alone is not enough (measured: with the parent collection
        hidden, an object whose every own flag is clear stays out of the
        depsgraph entirely). The remedy is NOT to clear the collection's flags
        -- measured too, and it reveals every OTHER member of that collection,
        which is the contamination this scoping exists to prevent. Instead the
        object is temporarily linked into the scene's master collection, which
        reveals it and nothing else, and unlinked again on the way out.

        Restores every flag it cleared and every link it added, and tolerates
        an object removed inside the block.

        Parameters:
            objects: Object refs (or a single object) to reveal.

        Example:
            >>> with CoreUtils.visible_override(obj):
            ...     bpy.ops.object.mode_set(mode="EDIT")
        """
        import bpy

        restore = []
        linked = []
        view_layer = getattr(bpy.context, "view_layer", None)
        master = getattr(getattr(bpy.context, "scene", None), "collection", None)
        for obj in ptk.make_iterable(objects):
            if obj is None:
                continue
            for flag in ("hide_viewport", "hide_render", "hide_select"):
                if getattr(obj, flag, False):
                    restore.append((obj, flag, True))
                    setattr(obj, flag, False)
            # A visible path to the object, without touching what else its
            # collections hold. Unconditional rather than conditional on a
            # visibility probe, because no cheap probe covers every case:
            # `visible_get` answers for the VIEWPORT, so a collection carrying
            # only `hide_render` excludes the object from the bake while
            # reading as visible -- and `users_collection` names only the DIRECT
            # parents, so a hidden grandparent would be missed either way. A
            # link/unlink pair is nothing beside a Cycles bake.
            if master is not None and obj.name not in master.objects:
                try:
                    master.objects.link(obj)
                    linked.append(obj)
                except (RuntimeError, ReferenceError):
                    pass
            # The view-layer "eye" is a separate axis from hide_viewport and is
            # the one FBX/USD imports usually set. Cleared AFTER the link, so it
            # also covers the layer-collection entry the link just created.
            if view_layer is not None:
                try:
                    if obj.hide_get(view_layer=view_layer):
                        restore.append((obj, "hide_get", True))
                        obj.hide_set(False, view_layer=view_layer)
                except (RuntimeError, ReferenceError):
                    pass
        try:
            yield
        finally:
            for obj in linked:
                try:
                    master.objects.unlink(obj)
                except (RuntimeError, ReferenceError):
                    pass
            for holder, flag, value in reversed(restore):
                try:
                    if flag == "hide_get":
                        holder.hide_set(value, view_layer=view_layer)
                    else:
                        setattr(holder, flag, value)
                except (RuntimeError, ReferenceError, AttributeError):
                    pass

    @staticmethod
    def undoable(fn):
        """Wrap ``fn`` so its changes collapse into a single Blender undo step.

        Decorator form of :func:`undo_chunk` — an operator, or an explicit
        ``bpy.ops.ed.undo_push``, marks a restore point; a raw ``bpy.data``/``bmesh``
        edit pushes nothing on its own, but a nested ``bpy.ops`` call (e.g. ``nla.bake``,
        ``bl_options={'REGISTER', 'UNDO'}``) pushes its OWN step the moment it finishes.
        So ``fn`` mixing raw edits with operator calls would otherwise leave several
        separate undo-stack entries instead of one.  Delegates to :func:`undo_chunk`
        so the toggle-and-push technique has a single definition.
        """

        @wraps(fn)
        def wrapper(*args, **kwargs):
            with CoreUtils.undo_chunk(getattr(fn, "__name__", "blendertk op")):
                return fn(*args, **kwargs)

        return wrapper

    @staticmethod
    def undo_checkpoint(fn):
        """Like :func:`undoable`, but pushes the restore point BEFORE ``fn`` runs (not after).

        Use this when ``fn`` builds **drivers** as its final act: a script-built driver only compiles
        correctly once its expression is re-assigned as the *last* operation (see
        ``RigUtils.refresh_drivers``), and a trailing ``undo_push`` (as :func:`undoable` does) re-stales
        it. Pushing the checkpoint first keeps the build a single undo step while leaving the driver
        recompile as the final operation.
        """

        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                import bpy

                bpy.ops.ed.undo_push(message=getattr(fn, "__name__", "blendertk op"))
            except Exception:
                pass
            return fn(*args, **kwargs)

        return wrapper

    @staticmethod
    def get_env_info(key=None):
        """Return Blender scene / environment info (mirror of ``mtk.get_env_info``).

        With ``key`` returns that single value, else the whole dict. camelCase keys to match
        the ecosystem's cross-DCC info convention (also what the Unity bridge expects).
        """
        import bpy

        from blendertk.env_utils._env_utils import EnvUtils

        scene = bpy.context.scene
        filepath = bpy.data.filepath
        ws = (
            EnvUtils.current_workspace()
        )  # ambient: session pin → marked root → the .blend's dir
        workspace = ws.root if ws else ""
        info = {
            "sceneName": filepath or "untitled",
            "blenderVersion": bpy.app.version_string,
            "fps": scene.render.fps,
            "currentFrame": scene.frame_current,
            "frameRange": (scene.frame_start, scene.frame_end),
            "unitSystem": scene.unit_settings.system,
            "selectionCount": len(CoreUtils.selected_objects()),
            # Blender's analogue of Maya's project workspace — the current-workspace resolver:
            # session pin → marked (workspace.mel) root containing the .blend → the .blend's dir.
            "workspace": workspace,
            "workspace_dir": os.path.basename(workspace) if workspace else "",
        }
        return info.get(key) if key is not None else info

    @staticmethod
    def ensure_packages(packages, add_to_path=True):
        """Make arbitrary pip packages importable in Blender's Python.

        The general form of :meth:`ensure_image_deps` — same provisioning policy
        (install into Blender's per-version *user-modules* dir, which is already
        on ``sys.path``, driven against Blender's **bundled** interpreter), with
        no assumption about what is being installed. Used for any optional
        distribution a panel needs in-session, e.g. ``unitytk`` behind the Unity
        Bridge.

        Args:
            packages: ``{pip_spec: import_name}`` to ensure.
            add_to_path: Prepend the install dir to ``sys.path`` (default True).

        Returns:
            list[str]: the import names importable after the call.

        Idempotent and Blender-gated: a no-op outside Blender, or when
        everything already imports. Never raises.
        """
        return _CoreUtilsInternal._ensure_packages(packages, add_to_path)

    @staticmethod
    def ensure_image_deps(packages=None, add_to_path=True):
        """Make image-processing libraries importable in Blender's Python (default: Pillow → ``PIL``).

        Blender bundles numpy but **not** Pillow/cv2, which the shared pythontk texture factory
        (:class:`pythontk.ImgUtils` / :class:`pythontk.MapFactory`) needs for the material/texture
        tools (Material Updater, Map Converter/Packer, bridge map-staging). This pip-installs the
        missing wheels into Blender's per-version *user-modules* dir (already on ``sys.path``) via
        :class:`pythontk.PackageManager`, driven against Blender's **bundled** interpreter — the same
        on-demand model tcl_blender uses for Qt, but owned here so the provisioning *policy* lives in
        the Blender library layer (blendertk), not the entry point.

        Args:
            packages: ``{pip_spec: import_name}`` to ensure. Defaults to ``{"Pillow": "PIL"}``.
                (e.g. add ``{"opencv-python-headless": "cv2"}`` for EXR/float ops.)
            add_to_path: Prepend the install dir to ``sys.path`` after installing (default True).

        Returns:
            list[str]: the import names importable after the call (a subset of ``packages`` values).

        Idempotent and Blender-gated: a no-op outside Blender, or when everything already imports.
        Never raises — a failed install logs a warning and the caller falls back to its own handling.
        """
        pkgs = dict(packages) if packages is not None else {"Pillow": "PIL"}
        result = _CoreUtilsInternal._ensure_packages(pkgs, add_to_path)
        if "PIL" in result:  # make sure pythontk's globals saw the new import
            _CoreUtilsInternal._rebind_pil_globals()
        return result

    @staticmethod
    def user_config_path(filename, base=None):
        """Absolute path of ``filename`` in Blender's user ``CONFIG`` dir (where Blender keeps
        ``userpref.blend`` / ``recent-files.txt`` — the home of every blendertk sidecar that
        must outlive a session), or None when it cannot be resolved (no ``bpy``, no config
        dir). ``base`` overrides the dir (tests sandbox their sidecars with it).
        """
        if base is None:
            try:
                import bpy

                base = bpy.utils.user_resource("CONFIG")
            except Exception:
                return None
        return os.path.join(base, filename) if base else None

    @staticmethod
    def get_recent_files(index=None):
        """Recently-opened .blend paths, most recent first (mirror of ``mtk.get_recent_files``).

        Reads Blender's own ``recent-files.txt`` (the source of File ▸ Open Recent). ``index``
        may be an int or slice. Missing files are filtered out.
        """
        path = CoreUtils.user_config_path("recent-files.txt") or ""
        files = []
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                files = [line.strip() for line in f if line.strip()]
            files = [p for p in files if os.path.isfile(p)]
        return files[index] if index is not None else files

    @staticmethod
    def get_recent_autosave(filter_time=24, timestamp_format="%H:%M:%S"):
        """Recent autosave .blend files as ``(path, timestamp)`` pairs, newest first
        (mirror of ``mtk.get_recent_autosave``). ``filter_time`` is the max age in hours.

        Blender autosaves land in the temporary directory (Preferences ▸ File Paths, falling
        back to the OS temp dir) as ``.blend`` files.
        """
        import time
        import glob
        import tempfile
        import bpy

        temp_dir = (
            bpy.context.preferences.filepaths.temporary_directory
            or tempfile.gettempdir()
        )
        cutoff = time.time() - filter_time * 3600
        results = []
        for path in glob.glob(os.path.join(temp_dir, "*.blend")):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= cutoff:
                results.append((path, mtime))
        results.sort(key=lambda x: x[1], reverse=True)
        return [
            (path, time.strftime(timestamp_format, time.localtime(mtime)))
            for path, mtime in results
        ]

    @staticmethod
    def get_scene_info(objects=None):
        """Scene audit record — the Blender analogue of Maya's Get Scene Info (a focused
        object/poly/material summary, not Maya's adaptive game-ready tri-budget profiler).

        ``objects`` defaults to every object in the current scene. Returns a dict with object
        counts by type and aggregate mesh stats (verts/edges/faces/triangles/ngons), plus
        material/image/light/camera counts. Pair with :func:`format_scene_info_html`.
        """
        import bpy

        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else list(bpy.context.scene.objects)
        )
        by_type = {}
        verts = edges = faces = tris = ngons = 0
        meshes = 0
        no_material = 0
        for o in pool:
            by_type[o.type] = by_type.get(o.type, 0) + 1
            if o.type != "MESH":
                continue
            meshes += 1
            me = o.data
            verts += len(me.vertices)
            edges += len(me.edges)
            faces += len(me.polygons)
            t, g = _CoreUtilsInternal._mesh_face_counts(me)
            tris += t
            ngons += g
            if not any(s.material for s in o.material_slots):
                no_material += 1
        return {
            "objects": len(pool),
            "byType": dict(sorted(by_type.items())),
            "meshes": meshes,
            "vertices": verts,
            "edges": edges,
            "faces": faces,
            "triangles": tris,
            "ngons": ngons,
            "meshesWithoutMaterial": no_material,
            "materials": len(bpy.data.materials),
            "images": len([i for i in bpy.data.images if i.source == "FILE"]),
            "lights": by_type.get("LIGHT", 0),
            "cameras": by_type.get("CAMERA", 0),
        }

    @staticmethod
    def format_scene_info_html(info):
        """Render a :func:`get_scene_info` record as an HTML report for the text-view dialog."""
        if not info:
            return ""
        rows = [
            ("Objects", f"{info['objects']:,}"),
            ("Meshes", f"{info['meshes']:,}"),
            ("Vertices", f"{info['vertices']:,}"),
            ("Edges", f"{info['edges']:,}"),
            ("Faces", f"{info['faces']:,}"),
            ("Triangles", f"{info['triangles']:,}"),
            ("N-gons (5+ sides)", f"{info['ngons']:,}"),
            ("Meshes without material", f"{info['meshesWithoutMaterial']:,}"),
            ("Materials", f"{info['materials']:,}"),
            ("Image textures", f"{info['images']:,}"),
            ("Lights", f"{info['lights']:,}"),
            ("Cameras", f"{info['cameras']:,}"),
        ]
        summary = "".join(
            f"<tr><td>{label}</td><td align='right'>&nbsp;{value}</td></tr>"
            for label, value in rows
        )
        by_type = "".join(
            f"<tr><td>{t.title()}</td><td align='right'>&nbsp;{n:,}</td></tr>"
            for t, n in info["byType"].items()
        )
        return (
            "<h3>Scene Info</h3>"
            f"<table cellspacing='6'>{summary}</table>"
            "<h4>Objects by type</h4>"
            f"<table cellspacing='6'>{by_type}</table>"
        )

    @staticmethod
    def analyze_scene(objects=None, adaptive=True, sections=None):
        """Game-readiness scene audit — the Blender port of mayatk's ``SceneAnalyzer`` (the budgeted,
        sectioned report behind Get Scene Info). Returns ``{section_key: html}`` for the requested
        ``sections`` (default all, rendered in canonical order).

        ``adaptive`` picks the triangle-budget profile: **Adaptive (Game Ready)** scales each mesh's
        budget by its world-space size (a hero mesh gets a larger budget than a small prop, clamped
        10k–1M); **Generic** applies a flat 100k budget to every mesh. Meshes over budget are the
        offenders driving Fix-First / Offenders. Sections: ``summary`` (totals + over-budget count),
        ``fix_first`` (worst offenders), ``pareto`` (top-10 triangle contributors), ``offenders``
        (per-asset table), ``categories`` (multi-material meshes), ``textures`` (4K+ histogram),
        ``pipeline`` (missing texture files), ``assumptions`` (methodology). ``objects`` defaults to the
        whole scene. Headless-safe (pure bpy queries)."""
        import bpy

        wanted = [s for s in _SCENE_SECTIONS if s in (sections or _SCENE_SECTIONS)]
        pool = (
            ptk.make_iterable(objects)
            if objects is not None
            else list(bpy.context.scene.objects)
        )
        meshes = [o for o in pool if o.type == "MESH"]

        recs = []
        for o in meshes:
            tris = _CoreUtilsInternal._mesh_face_counts(o.data)[0]
            d = o.dimensions
            diag = (d.x * d.x + d.y * d.y + d.z * d.z) ** 0.5
            recs.append(
                {
                    "name": o.name,
                    "tris": tris,
                    "diag": diag,
                    "mats": len([s for s in o.material_slots if s.material]),
                }
            )
        total_tris = sum(r["tris"] for r in recs)
        diags = sorted((r["diag"] for r in recs)) or [0.0]
        median = diags[len(diags) // 2] or 1.0
        for r in recs:
            if adaptive:
                scale = (r["diag"] / median) if median else 1.0
                r["budget"] = int(
                    min(1_000_000, max(10_000, _GENERIC_TRI_BUDGET * scale))
                )
            else:
                r["budget"] = _GENERIC_TRI_BUDGET
            r["over"] = r["tris"] - r["budget"]

        offenders = sorted((r for r in recs if r["over"] > 0), key=lambda r: -r["over"])
        pareto = sorted(recs, key=lambda r: -r["tris"])[:10]

        def _table(header, rows):
            body = "".join(
                "<tr>"
                + "".join(f"<td align='right'>&nbsp;{c}</td>" for c in row)
                + "</tr>"
                for row in rows
            )
            head = "".join(f"<th align='right'>&nbsp;{h}</th>" for h in header)
            return f"<table cellspacing='6'><tr>{head}</tr>{body}</table>"

        out = {}
        if "summary" in wanted:
            out["summary"] = (
                "<h3>Executive Summary</h3>"
                f"<table cellspacing='6'>"
                f"<tr><td>Profile</td><td align='right'>&nbsp;{'Adaptive (Game Ready)' if adaptive else 'Generic'}</td></tr>"
                f"<tr><td>Meshes</td><td align='right'>&nbsp;{len(meshes):,}</td></tr>"
                f"<tr><td>Triangles</td><td align='right'>&nbsp;{total_tris:,}</td></tr>"
                f"<tr><td>Materials</td><td align='right'>&nbsp;{len(bpy.data.materials):,}</td></tr>"
                f"<tr><td>Over-budget meshes</td><td align='right'>&nbsp;{len(offenders):,}</td></tr>"
                "</table>"
            )
        if "fix_first" in wanted:
            rows = [
                (r["name"], f"{r['tris']:,}", f"{r['budget']:,}", f"+{r['over']:,}")
                for r in offenders[:5]
            ]
            out["fix_first"] = "<h4>Fix First (High Impact)</h4>" + (
                _table(("Asset", "Tris", "Budget", "Over"), rows)
                if rows
                else "<p>No meshes exceed their triangle budget. ✓</p>"
            )
        if "pareto" in wanted:
            rows = [
                (
                    r["name"],
                    f"{r['tris']:,}",
                    f"{(100 * r['tris'] / total_tris):.1f}%" if total_tris else "0%",
                )
                for r in pareto
            ]
            out["pareto"] = "<h4>Pareto View — top triangle contributors</h4>" + _table(
                ("Asset", "Tris", "% of total"), rows
            )
        if "offenders" in wanted:
            rows = [
                (r["name"], f"{r['tris']:,}", f"{r['budget']:,}", f"+{r['over']:,}")
                for r in offenders
            ]
            out["offenders"] = "<h4>Top Issues by Asset</h4>" + (
                _table(("Asset", "Tris", "Budget", "Over"), rows)
                if rows
                else "<p>No over-budget assets. ✓</p>"
            )
        if "categories" in wanted:
            multi = sorted(
                (r for r in recs if r["mats"] > 1), key=lambda r: -r["mats"]
            )[:10]
            rows = [(r["name"], r["mats"]) for r in multi]
            out["categories"] = (
                "<h4>Top Offenders by Category — multi-material meshes</h4>"
                + (
                    _table(("Asset", "Material slots"), rows)
                    if rows
                    else "<p>No multi-material meshes.</p>"
                )
            )
        if "textures" in wanted:
            imgs = [i for i in bpy.data.images if i.source == "FILE"]
            buckets = {"<1K": 0, "1K": 0, "2K": 0, "4K+": 0}
            for i in imgs:
                m = max(i.size[0], i.size[1])
                buckets[
                    "4K+"
                    if m >= 4096
                    else "2K"
                    if m >= 2048
                    else "1K"
                    if m >= 1024
                    else "<1K"
                ] += 1
            rows = [(k, v) for k, v in buckets.items()]
            out["textures"] = f"<h4>Textures — {len(imgs)} file image(s)</h4>" + _table(
                ("Max dimension", "Count"), rows
            )
        if "pipeline" in wanted:
            missing = [
                f"{i.name} ({i.filepath})"
                for i in bpy.data.images
                if i.source == "FILE"
                and i.filepath
                and not os.path.exists(bpy.path.abspath(i.filepath))
            ]
            out["pipeline"] = "<h4>Pipeline Integrity</h4>" + (
                "<p>Missing texture files:<br> • " + "<br> • ".join(missing) + "</p>"
                if missing
                else "<p>All referenced texture files resolve. ✓</p>"
            )
        if "assumptions" in wanted:
            note = (
                "Adaptive budget scales the 100k base by each mesh's world-size relative to the "
                "scene median (clamped 10k-1M)."
                if adaptive
                else "Generic budget is a flat 100k triangles per mesh."
            )
            out["assumptions"] = (
                f"<h4>Data Assumptions</h4><p>Triangles are fan-count per face (n-2). {note}</p>"
            )
        return out

    @staticmethod
    def cleanup_scene(quiet=False):
        """Purge orphan datablocks (0 users, no fake user) across the main collections — the
        headless-safe analogue of Blender's File ▸ Clean Up ▸ Purge and Maya's Cleanup. Repeats
        until stable so cascaded orphans (a mesh's material, an image's node group …) are caught.

        Returns ``{collection: count}`` of what was removed. Render-result / viewer images and the
        scene/world datablocks are never touched.
        """
        import bpy

        collections = (
            "meshes",
            "curves",
            "metaballs",
            "lattices",
            "grease_pencils",
            "armatures",
            "materials",
            "textures",
            "images",
            "node_groups",
            "actions",
            "lights",
            "cameras",
            "speakers",
            "fonts",
            "particles",
            "volumes",
        )
        skip_image_types = {"RENDER_RESULT", "COMPOSITING"}
        removed = {}
        changed = True
        while changed:
            changed = False
            for name in collections:
                coll = getattr(bpy.data, name, None)
                if coll is None:
                    continue
                for db in list(coll):
                    if db.users:  # a fake user counts here, so use_fake_user is implicitly honoured
                        continue
                    if name == "images" and getattr(db, "type", "") in skip_image_types:
                        continue
                    coll.remove(db)
                    removed[name] = removed.get(name, 0) + 1
                    changed = True
        if not quiet:
            print(f"[blendertk] cleanup_scene removed: {removed or 'nothing'}")
        return removed

    @staticmethod
    def selected_objects():
        """The current object selection, filtered of ``None`` (mirror of Maya's
        ``cmds.ls(selection=True)`` idiom that the slots use).

        Read from ``view_layer.objects.selected`` rather than ``bpy.context.selected_objects``: the
        latter is empty whenever ``bpy.context.window`` is ``None`` — exactly the state the Qt event-pump
        timer runs the slots in, which surfaced as "many operations report *nothing selected* although an
        object is selected". See :func:`_active_view_layer`.

        Shared by the co-located tool Slots (curtain / mirror / duplicate …) so they resolve the
        selection without depending on tentacle's ``SlotsBlender`` base — keeping blendertk free of
        any back-dependency on tentacle, exactly as mayatk's co-located slots stay tentacle-free.
        """
        vl = _CoreUtilsInternal._active_view_layer()
        if vl is None:
            return []
        return [o for o in vl.objects.selected if o]

    @staticmethod
    def active_object():
        """The active object, resolved window-independently (``view_layer.objects.active``).

        The Blender companion to :func:`selected_objects`: ``bpy.context.active_object`` is a
        screen-context member that returns ``None`` when ``bpy.context.window`` is ``None`` (the Qt
        event-pump timer context), so the slots read the active object through the view layer instead.
        Returns ``None`` when nothing is active.
        """
        vl = _CoreUtilsInternal._active_view_layer()
        return vl.objects.active if vl is not None else None

    @staticmethod
    def reorder_objects(objects=None, method="name", reverse=False):
        """Reorder a set of objects by a sorting method — mirror of ``mtk.reorder_objects``
        (``name`` / ``hierarchy`` / ``x``/``y``/``z`` / ``distance`` / ``volume`` /
        ``vertex_count`` / ``random`` / ``creation_time``).

        ``creation_time`` sorts by ``Object.session_uid`` — Blender keeps no creation
        timestamp, but the session uid increases monotonically per created datablock, so
        within a session it IS creation order (documented divergence: it resets across
        sessions, where Maya reads persistent node order). Returns the sorted list; feed it
        to ``SelectionOrder.set_order`` to make the new order selection-visible.
        """
        import random as _random

        from mathutils import Vector

        objs = [
            o
            for o in (
                ptk.make_iterable(objects)
                if objects is not None
                else CoreUtils.selected_objects()
            )
            if o is not None
        ]
        if not objs:
            return []

        def _depth(o):
            d, cur = 0, o.parent
            while cur is not None:
                d, cur = d + 1, cur.parent
            return d

        def _bb_volume(o):
            if getattr(o, "bound_box", None) is None:
                return 0.0
            cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
            return (
                (max(c.x for c in cs) - min(c.x for c in cs))
                * (max(c.y for c in cs) - min(c.y for c in cs))
                * (max(c.z for c in cs) - min(c.z for c in cs))
            )

        def _vertex_count(o):
            data = getattr(o, "data", None)
            if data is None:
                return 0
            if hasattr(data, "vertices"):
                return len(data.vertices)
            if hasattr(data, "splines"):  # curves: control-point count
                return sum(
                    len(sp.points) or len(sp.bezier_points) for sp in data.splines
                )
            return 0

        keys = {
            "name": lambda o: o.name,
            "hierarchy": _depth,
            "x": lambda o: o.matrix_world.translation.x,
            "y": lambda o: o.matrix_world.translation.y,
            "z": lambda o: o.matrix_world.translation.z,
            "distance": lambda o: o.matrix_world.translation.length,
            "volume": _bb_volume,
            "vertex_count": _vertex_count,
            "creation_time": lambda o: o.session_uid,
        }
        if method == "random":
            objs = list(objs)
            _random.shuffle(objs)
            result = objs
        else:
            result = sorted(objs, key=keys.get(method, keys["name"]))
        return list(reversed(result)) if reverse else result

    @staticmethod
    def get_areas(area_type=None):
        """All areas of ``area_type`` (``"VIEW_3D"``, ``"IMAGE_EDITOR"``, …) across every open
        window, resolved through the window manager — NOT ``bpy.context.screen``, which is a
        screen-context member and ``None`` whenever ``bpy.context.window`` is ``None`` (the Qt
        event-pump timer state the tentacle slots run in; same family as :func:`selected_objects`).
        A ``context.screen.areas`` loop there crashes with ``AttributeError``; this reads every
        window's screen instead (a superset — multi-window setups stay in lockstep, matching the
        all-viewports convention the display toggles already document). Even ``--background`` keeps
        one window with the default screen, so the result is normally non-empty headless too.

        ``area_type=None`` returns every area regardless of type — what an editor-agnostic sweep
        (a theme restyle, a blanket repaint) needs.
        """
        import bpy

        return [
            area
            for win in bpy.context.window_manager.windows
            for area in win.screen.areas
            if area_type is None or area.type == area_type
        ]

    @classmethod
    def tag_redraw(cls, area_type=None) -> int:
        """Queue a redraw of every area of ``area_type`` (all areas when None); returns how many.

        A raw ``bpy.data`` write (``obj.color``, a space's shading knobs, a theme change …) made
        from the Qt event-pump timer the tentacle slots run in does not repaint on its own —
        Blender only redraws on its own event loop, so the change sits invisible until the user
        nudges the editor. Companion to :func:`get_areas` (same window-manager walk, so it is safe
        where ``bpy.context.screen`` is ``None``).

        Note this is the *panel* redraw path. Inside a **modal operator** the running
        ``context.area`` is the area being drawn into, and `context.area.tag_redraw()` is both
        correct and cheaper — don't broaden those to every window.
        """
        areas = cls.get_areas(area_type)
        for area in areas:
            area.tag_redraw()
        return len(areas)

    @staticmethod
    def get_view3d_context():
        """Context-override dict targeting the first VIEW_3D area/region, or ``None`` if there is no
        3D viewport.

        Region-centric viewport work — ``view3d.*`` ops, popping a native menu over the viewport with
        ``wm.call_menu`` — needs an explicit override when invoked from the Qt marking menu, because the
        active area isn't the 3D view. Shared home for the pattern that was duplicated across slots
        (``cameras``) and the ``call_native_menu`` helper. The dict carries window/screen/area/region/
        scene so it serves every such caller; ``region`` may be ``None`` (no WINDOW region) — callers
        guard on it.
        """
        import bpy

        wm = bpy.context.window_manager
        for win in getattr(wm, "windows", []) or []:
            screen = win.screen
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    region = next((r for r in area.regions if r.type == "WINDOW"), None)
                    return {
                        "window": win,
                        "screen": screen,
                        "area": area,
                        "region": region,
                        "scene": bpy.context.scene,
                    }
        return None

    @staticmethod
    @contextmanager
    def window_context_override():
        """Yield with a valid ``window`` in context when ``bpy.context.window`` is ``None``.

        The window-only companion to :func:`get_view3d_context` (which targets a VIEW_3D *region*).
        Some ``bpy.ops`` don't need a viewport but still read *screen-context* members: e.g. Blender's
        ``io_scene_fbx`` exporter accesses ``context.selected_objects`` (raises ``AttributeError`` when
        ``context.window`` is ``None``). tentacle drives the slots from the Qt event-pump timer where
        ``context.window`` is ``None`` (see :func:`selected_objects`), so those ops fault. Wrapping them
        in ``with window_context_override():`` supplies the first open window so the operator's
        screen-context reads resolve.

        A no-op (plain ``yield``) when a window is already active — so it's harmless to wrap
        unconditionally — or when no window exists at all (leaves the caller to fail as it would have).
        """
        import bpy

        if getattr(bpy.context, "window", None) is not None:
            yield
            return
        wm = getattr(bpy.context, "window_manager", None)
        windows = getattr(wm, "windows", None) or []
        if not windows:
            yield
            return
        with bpy.context.temp_override(window=windows[0]):
            yield
