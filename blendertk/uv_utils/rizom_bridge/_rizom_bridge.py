# !/usr/bin/python
# coding=utf-8
"""RizomUV bridge engine — Blender mirror of mayatk's ``RizomUVBridge``.

Two flows, mirroring mayatk (``btk.RizomUVBridge`` ↔ ``mtk.RizomUVBridge`` at the name+behavior
level, not signatures — Maya uses ``cmds`` string-node idioms, Blender uses ``bpy`` object refs):

* **send** (one-way): export the selection to FBX, write a small RizomUV Lua load-script, launch
  RizomUV detached with ``-cfi <script>``. Nothing round-trips back — the artist saves inside
  RizomUV. Unchanged from the original port.
* **process_with_rizomuv** (round-trip): export *copies* of the selection to a temp FBX, run the
  chosen preset (``pack`` / ``unwrap_hard`` / ``unwrap_organic`` / ``optimize``) headlessly via
  ``-cfi``, re-import the RizomUV-written FBX, and transfer the new UVs back onto the originals.

The round-trip's Lua assets are vendored byte-identical from mayatk (``scripts/*.lua`` +
``templates/wrapper.lua`` — pure RizomUV Lua, DCC-agnostic), and the DCC-agnostic Python helpers
(version parsing, script construction / version-gating, the headless run + FBX-modified
verification) mirror mayatk's engine. Only the four DCC-specific operations diverge to Blender
idioms: exporting *copies*, re-importing, mapping imports back to originals, and the UV transfer.

Divergences from the Maya round-trip, by construction:

* **Copies, not namespaced duplicates.** Blender's FBX import never overwrites — it always creates
  new datablocks — so there's no namespace dance. Copies still get a unique ``__RZTMP`` name so the
  re-import maps cleanly back onto the originals (Blender object names are globally unique, unlike
  Maya leaf names, so no full-path disambiguation is needed).
* **Loop-data UV copy, not ``transferAttributes``.** RizomUV rewrites only UVs (never 3D geometry),
  so the re-imported mesh is topologically identical to the original: a direct per-loop UV copy is
  exact and context-free (works in tentacle's windowless Qt-timer state). ``data_transfer`` with
  spatial loop-mapping is the fallback if the FBX round-trip ever changes the loop count.

``import bpy`` is deferred into call bodies so resolving the package surface never needs a running
Blender. RizomUV is Windows-only.
"""

import os
import re
import subprocess
from pathlib import Path

import pythontk as ptk

import blendertk as btk

# Qt-free / bpy-deferred selection reader + windowless-context override (tentacle drives the slots
# from a bpy.app.timers callback where bpy.context.window is None — the same reason FbxUtils wraps
# its operators). Importing these never needs a running Blender.
from blendertk.core_utils._core_utils import CoreUtils
from blendertk.env_utils.fbx_utils import FbxUtils


_PKG_DIR = Path(__file__).resolve().parent
# Bundled RizomUV Lua: ``scripts/*.lua`` recipe bodies (send + the four round-trip presets) and
# ``templates/wrapper.lua`` (the Load -> [preset] -> Save -> Quit boilerplate). Vendored
# byte-identical from mayatk's ``uv_utils.rizom_bridge`` (pure RizomUV Lua, DCC-agnostic).
_SCRIPT_DIR = _PKG_DIR / "scripts"
_TEMPLATE_DIR = _PKG_DIR / "templates"

# Candidate RizomUV executable names (AppLauncher.find_app), newest install wins on the dir scan.
_RIZOM_APP_NAMES = ("Rizomuv_VS", "rizomuv", "RizomUV")
# Install-dir fallback (the Rizom installer doesn't register the exe with the Windows App-Paths
# key). Newest ``Rizom Lab\<version>`` folder wins. Shared scan via ``AppLauncher.resolve_app_path``.
_RIZOM_SCAN_GLOBS = (
    r"{program_files}\Rizom Lab\*\Rizomuv_VS.exe",
    r"{program_files}\Rizom Lab\*\rizomuv_RS.exe",
    r"{program_files}\Rizom Lab\*\rizomuv.exe",
)

# Version segment inside a Rizom install-dir name. Anchored on a 4-digit year (every supported
# release is year-versioned) so it survives the naming variants: "RizomUV 2020.1", "RizomUV_2022",
# "RizomUV VS RS 2022.2". Mirrors mayatk's ``_VERSION_RE``.
_VERSION_RE = re.compile(r"(\d{4}(?:\.\d+)*)")


class _RizomUVBridgeInternal(object):
    """Internal helpers for RizomUVBridge."""

    @staticmethod
    def _parse_rizom_version(exe_path) -> "tuple[int, ...]":
        """Parse ``(major, minor, ...)`` from *exe_path*'s install-dir name.

        Walks the path's parents looking for a folder whose name mentions Rizom and contains a
        year-anchored version. Padded to at least length 2 (``(2020, 1)`` / ``(2022, 0)``) so
        single-segment names still compare correctly against the ``(year, minor)`` gates in
        :data:`parameters.MIN_VERSIONS`. Returns ``(0, 0)`` when nothing parses. Mirror of mayatk.
        """
        for parent in Path(exe_path).resolve().parents:
            if "rizom" not in parent.name.lower():
                continue
            matches = _VERSION_RE.findall(parent.name)
            if matches:
                parsed = tuple(int(p) for p in matches[-1].split("."))
                return parsed if len(parsed) >= 2 else parsed + (0,) * (2 - len(parsed))
        return (0, 0)


class RizomUVBridge(ptk.LoggingMixin, _RizomUVBridgeInternal):
    """Engine: discover the RizomUV exe, export the selection, run RizomUV (send or round-trip).

    Named to mirror mayatk's ``RizomUVBridge`` (``btk.RizomUVBridge`` ↔ ``mtk.RizomUVBridge``)."""

    # Suffix appended to the temporary export copies so the FBX re-import maps cleanly back onto
    # the originals (and never collides with a real object name).
    _TEMP_SUFFIX = "__RZTMP"

    def __init__(self, rizom_path=None, timeout=600):
        """Initialize the RizomUV bridge.

        Parameters:
            rizom_path: Explicit path to the RizomUV executable. If *None*, ``AppLauncher`` searches
                PATH / registry / the standard install dirs using ``_RIZOM_APP_NAMES``.
            timeout: Max seconds to wait for the headless round-trip run before killing RizomUV.
                Simple meshes finish in seconds; dense meshes with high pack mutations take minutes.
        """
        super().__init__()
        self._rizom_path = rizom_path
        self.timeout = timeout
        self._export_path = None
        self._script_path = None
        self._temp = None  # Round-trip temp store (see _temp_store)
        self._lua_path = None  # Store-allocated path the generated Lua is written to
        # Mapping of exported (unique-suffixed) copy name -> original bpy object.
        self._export_name_map = {}
        # Per-run placeholder overrides (set by process_with_rizomuv).
        self._params: dict = {}
        # (materials, images) snapshot taken before the FBX re-import, so cleanup can purge only
        # the datablocks that import created. Set in _import_objects; consumed in cleanup.
        self._pre_import_ids = (None, None)

    @property
    def rizom_path(self):
        """Resolved RizomUV executable path (cached), or None. Discovery runs through the shared
        :meth:`pythontk.AppLauncher.resolve_app_path`: ``AppLauncher.find_app`` for each candidate
        name, then a scan of ``Program Files\\Rizom Lab`` (the Rizom installer doesn't register the
        exe with the Windows App-Paths key, so PATH lookup alone misses it; newest
        ``Rizom Lab\\<version>`` folder wins)."""
        if self._rizom_path:
            return self._rizom_path
        found = ptk.AppLauncher.resolve_app_path(
            app_names=_RIZOM_APP_NAMES,
            scan_globs=_RIZOM_SCAN_GLOBS,
        )
        if found:
            self._rizom_path = found
        return found

    @rizom_path.setter
    def rizom_path(self, value):
        self._rizom_path = value

    @property
    def rizom_version(self) -> "tuple[int, ...]":
        """The installed Rizom version, parsed from the install-dir name (mirror of mayatk).

        Returns ``(0, 0)`` when no version can be extracted -- conservative: gates every
        version-flagged param off, matching what a fresh / unknown Rizom install would need."""
        path = self.rizom_path
        if not path:
            self.logger.debug("rizom_version: no executable resolved yet -> (0, 0).")
            return (0, 0)
        version = _RizomUVBridgeInternal._parse_rizom_version(path)
        if version == (0, 0):
            self.logger.debug(
                f"rizom_version: could not parse version from {path!r}; "
                f"gating all version-flagged params off -> (0, 0)."
            )
        return version

    @property
    def _temp_store(self) -> "ptk.TempArtifacts":
        """Lifecycle owner of the round-trip's temp FBX + Lua script (mirrors mayatk).

        Both used to be FIXED tempdir names (``rizomuv_exported.fbx`` / ``riz_uv_script.lua``) --
        the *same* two the Maya bridge used, so the twin panels raced each other for one script
        file. RizomUV keeps re-reading the ``-cfi`` script *after* launch (the mtime-watch
        behaviour the send flow designs around), so whichever run got overwritten mid-flight
        exited 0 without ever reaching ``ZomSave`` (reproduced; a user-reported failure).

        ``"scoped"`` is this flow's exact shape: the run blocks until RizomUV exits, so a clean
        run deletes both payloads while a **failure keeps them** -- what makes the no-save error's
        "open the script in RizomUV's Script Editor" advice actionable. Allocation also age-sweeps
        same-prefix leftovers, so crashed runs can't accumulate.
        """
        if self._temp is None:
            self._temp = ptk.TempArtifacts("rizom_roundtrip", policy="scoped")
        return self._temp

    def _release_temp_payloads(self) -> None:
        """Delete this run's temp payloads and forget the paths they used.

        Forgetting matters: ``cleanup`` *untracks* what it removed, so a second run through the
        same bridge (the panel keeps one per session) would rewrite the same now-untracked paths
        and never clean them again. Only store-allocated paths are dropped -- an explicitly
        assigned ``export_path`` was never tracked, so it is never removed nor forgotten.
        """
        if self._temp is None:
            return
        removed = self._temp.cleanup()
        for attr in ("_export_path", "_lua_path"):
            path = getattr(self, attr)
            if path is not None and str(path) in removed:
                setattr(self, attr, None)

    @property
    def export_path(self):
        """Lazy temp FBX path for the round-trip (POSIX string)."""
        if self._export_path is None:
            self._export_path = Path(self._temp_store.path(extension=".fbx"))
        return self._export_path.as_posix()

    @export_path.setter
    def export_path(self, value):
        # FBX only: the exporter, wrapper flags (UseUVSetNames) and the re-import are FBX-shaped.
        if value and not str(value).lower().endswith(".fbx"):
            raise ValueError("The specified export path must end with '.fbx'")
        self._export_path = Path(value)

    @property
    def script_path(self):
        """The prepared Lua script file path as a POSIX string."""
        if self._script_path is None:
            raise ValueError("Script path is not set.")
        return self._script_path.as_posix()

    @script_path.setter
    def script_path(self, value):
        """Set the script from a file path, or save raw Lua content to a file."""
        if Path(value).is_file():
            self._script_path = Path(value)
        else:
            self._script_path = self._prepare_script_file(value)

    # ------------------------------------------------------------------
    # send helpers (one-way) — unchanged
    # ------------------------------------------------------------------

    @staticmethod
    def _lua_bool(value):
        return "true" if value else "false"

    def _texture_loads(self, objects):
        """RizomUV ``ZomLoadTexture`` calls for the selection's textures (each ``pcall``-wrapped so
        an older RizomUV that lacks the command fails soft — the mesh still loads). '' if none."""
        try:
            paths = btk.get_texture_paths(objects=objects, absolute=True)
        except Exception as error:
            self.logger.warning(f"Texture collection failed: {error}")
            return ""
        # Order-preserving dedupe -- shared materials report the same file once per assignment.
        existing = [p for p in dict.fromkeys(paths) if p and os.path.isfile(p)]
        if not existing:
            return ""
        self.logger.info(f"Binding {len(existing)} texture(s) in RizomUV.")
        return "\n".join(
            'pcall(function() ZomLoadTexture({{File={{Path="{0}"}}}}) end)'.format(
                p.replace("\\", "/")
            )
            for p in existing
        )

    def build_send_script(
        self,
        fbx_path,
        objects=None,
        load_uvs=True,
        import_groups=True,
        load_uvw_props=True,
        load_textures=True,
    ):
        """Render the RizomUV Lua load-script (``ZomLoad`` + optional ``ZomLoadTexture`` block).

        Mirrors mayatk's ``send_wrapper.lua`` (no ``ZomSave``/``ZomQuit`` — RizomUV stays open)."""
        load = (
            'ZomLoad({{File={{Path="{path}", ImportGroups={groups}, '
            "XYZUVW={uvs}, UVWProps={props}}}}})"
        ).format(
            path=str(fbx_path).replace("\\", "/"),
            groups=self._lua_bool(import_groups),
            uvs=self._lua_bool(load_uvs),
            props=self._lua_bool(load_uvw_props),
        )
        textures = self._texture_loads(objects) if load_textures else ""
        return f"{load}\n\n{textures}\n" if textures else f"{load}\n"

    def send(
        self,
        objects,
        load_uvs=True,
        import_groups=True,
        load_uvw_props=True,
        load_textures=True,
    ):
        """Export ``objects`` to FBX and open them in a fresh RizomUV session (one-way).

        Per-send unique FBX + Lua paths so a second send doesn't clobber a still-open earlier
        session (RizomUV's ``-cfi`` watches the script). Both come from ``ptk.TempArtifacts`` with
        the ``detached`` policy — the honest one here: the session may outlive us and never signals
        completion, so nothing may delete these; allocation age-sweeps the same prefix instead,
        which is what keeps a send-per-click habit from filling the temp dir forever. (The
        round-trip uses the same primitive with ``scoped`` — see :meth:`_temp_store`.) Returns the
        written Lua script path."""
        if not objects:
            raise ValueError("No objects specified for sending.")
        exe = self.rizom_path
        if not exe:
            raise RuntimeError(
                "RizomUV executable not found. Install RizomUV (Rizom Lab) or set rizom_path."
            )
        # One store, two payloads: they share a tag prefix and a sweep scope, and the store's
        # monotonic tag can't repeat -- ``time.time_ns()`` alone can, its Windows resolution
        # (~15ms) being coarser than two back-to-back sends.
        send_store = ptk.TempArtifacts("riz_send", policy="detached")
        fbx_path = send_store.path(extension=".fbx")
        btk.FbxUtils.export_selection_fbx(filepath=fbx_path, objects=objects)

        script = self.build_send_script(
            fbx_path,
            objects=objects,
            load_uvs=load_uvs,
            import_groups=import_groups,
            load_uvw_props=load_uvw_props,
            load_textures=load_textures,
        )
        script_path = send_store.path(extension=".lua")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        proc = ptk.AppLauncher.launch(exe, args=["-cfi", script_path], detached=True)
        if proc is None:
            raise RuntimeError(f"Failed to launch RizomUV: {exe}")
        self.logger.info(
            f"Sent {len(objects)} object(s) to RizomUV (interactive session)."
        )
        return script_path

    # ------------------------------------------------------------------
    # round-trip (export copies -> headless RizomUV -> re-import -> transfer)
    # ------------------------------------------------------------------

    def process_with_rizomuv(
        self,
        objects,
        uv_script=None,
        preset=None,
        params=None,
        select_objects=None,
        skip_instances=True,
    ):
        """Run the full export -> RizomUV -> re-import -> transfer-UVs-back workflow.

        The whole round-trip is wrapped in one ``undo_chunk`` so a single Ctrl+Z reverts the UV
        transfer (and the temp copy/import churn) as one step; RizomUV modifies a temp FBX on disk
        only. Mirror of mayatk's ``process_with_rizomuv``.

        Parameters:
            objects: bpy mesh objects (or names) to process.
            uv_script: Raw Lua string **or** path to a ``.lua`` file. Mutually exclusive with
                *preset*.
            preset: Name of a built-in preset (``"pack"``, ``"unwrap_hard"``, ``"unwrap_organic"``,
                ``"unwrap_hybrid"``, ``"optimize"``, ``"pack_into_existing"``). Loaded from
                ``scripts/<preset>.lua``. Mutually exclusive with *uv_script*.
            params: Optional dict of placeholder overrides (e.g. ``{"ITERATIONS": 25}``). Keys map
                to ``__KEY__`` tokens in the script (see ``parameters.PARAMS``).
            select_objects: Subset of *objects* whose islands the script should select in Rizom
                (rendered into the ``PACK_SELECT_NAMES`` token as a Lua table of exported group
                names). Required by presets operating on a sub-selection -- e.g.
                ``pack_into_existing`` packs these objects' islands into the gaps left by the rest.
            skip_instances: When True (default), collapse linked duplicates (objects sharing one
                mesh datablock) to a single representative before export -- the Blender analogue of
                mayatk's shared-shape instances. RizomUV would otherwise unwrap each independently
                and the UV transfer back onto the shared datablock is last-write-wins. Ignored when
                a preset selects a sub-set of islands (its mapping needs every named object).
        """
        from blendertk.uv_utils.rizom_bridge import parameters as _params

        if not objects:
            raise ValueError("No objects specified for processing.")

        originals = self._as_mesh_objects(objects)
        if not originals:
            raise ValueError("No valid mesh objects supplied for processing.")

        resolved = self._resolve_script(uv_script=uv_script, preset=preset)
        if resolved is not None:
            self.script_path = resolved

        # Preset-level version gate (e.g. pack_into_existing needs the ZomPack WorkingSet field,
        # absent below 2022.2). Fails loudly instead of letting an unsupported field no-op or
        # crash Rizom. Mirror of mayatk.
        required = _params.Parameters.preset_min_version(resolved or "")
        if required and self.rizom_version < required:
            raise RuntimeError(
                f"Preset '{preset or 'script'}' requires RizomUV >= "
                f"{'.'.join(map(str, required))}; installed version is "
                f"{'.'.join(map(str, self.rizom_version))} ({self.rizom_path})."
            )

        # Presets that select a sub-set of islands need to know which objects that is -- refuse to
        # render a script whose selection token would otherwise survive as a Lua syntax error.
        needs_selection = bool(resolved) and "__PACK_SELECT_NAMES__" in resolved
        if needs_selection and not select_objects:
            raise ValueError(
                f"Preset '{preset or 'script'}' operates on a sub-selection; pass select_objects= "
                "with the mesh objects whose islands should be packed."
            )

        # Collapse linked duplicates (shared mesh datablock) to one representative per datablock --
        # the Blender twin of mayatk's shared-shape instance dedupe. Unless a preset needs every
        # named object for its island-group selection.
        if skip_instances and not needs_selection and len(originals) > 1:
            seen, deduped = set(), []
            for obj in originals:
                if obj.data not in seen:
                    seen.add(obj.data)
                    deduped.append(obj)
            if len(deduped) < len(originals):
                self.logger.info(
                    f"Skipping {len(originals) - len(deduped)} linked-duplicate(s): one "
                    "representative per shared mesh is unwrapped; the result applies to all."
                )
                originals = deduped

        self._params = params or {}

        with CoreUtils.undo_chunk(f"RizomUV: {preset or 'script'}"):
            self._export_objects(originals)
            if needs_selection:
                self._params = dict(self._params)
                self._params.setdefault(
                    "PACK_SELECT_NAMES", self._select_names_lua(select_objects)
                )
            self._execute_uv_script()
            imported = self._import_objects()
            self._transfer_uvs_and_cleanup(imported, originals)

        self._announce_handoff(preset or "script", len(originals))

        # The FBX has been consumed and its UVs are on the originals, so both payloads can go. A
        # raise above skips this on purpose: the scoped policy keeps them so a failure stays
        # debuggable -- the no-save error tells the user to open that very script in RizomUV.
        self._release_temp_payloads()

    def _select_names_lua(self, select_objects) -> str:
        """Render *select_objects* as a Lua table of exported group names.

        Must run after :meth:`_export_objects` -- it resolves each object through
        ``_export_name_map`` to the suffixed copy name the FBX (and therefore Rizom's imported
        island groups) actually carries. Mirror of mayatk.
        """
        sel = set(self._as_mesh_objects(select_objects))
        names = [dup for dup, orig in self._export_name_map.items() if orig in sel]
        if not names:
            raise ValueError(
                "select_objects did not match any exported object -- they must be a subset of "
                "the objects passed for processing."
            )
        return "{" + ", ".join(f'"{n}"' for n in names) + "}"

    @staticmethod
    def expand_by_materials(objects) -> "tuple[list, list]":
        """Expand *objects* to every mesh object sharing their assigned materials.

        Companion to the ``pack_into_existing`` preset: the caller selects only the NEW meshes;
        the full set Rizom needs (so the existing layout is present as the locked forbidden area)
        is every mesh using the same material(s) -- the material defines "the map". Mirror of
        mayatk (bpy refs instead of DAG-path strings).

        Returns ``(all_objects, selected_objects)`` where *selected_objects* is the normalized
        input (the pack subset).
        """
        import bpy

        selected = RizomUVBridge._as_mesh_objects(objects)
        mats = {
            slot.material
            for obj in selected
            for slot in obj.material_slots
            if slot.material is not None
        }
        expanded = set(selected)
        if mats:
            for obj in bpy.data.objects:
                if obj.type != "MESH":
                    continue
                if any(slot.material in mats for slot in obj.material_slots):
                    expanded.add(obj)
        return sorted(expanded, key=lambda o: o.name), selected

    @staticmethod
    def _as_mesh_objects(objects):
        """Coerce *objects* (bpy objects or names) to a list of existing bpy MESH objects."""
        import bpy

        out = []
        for o in ptk.make_iterable(objects):
            obj = bpy.data.objects.get(o) if isinstance(o, str) else o
            if obj is not None and getattr(obj, "type", None) == "MESH":
                out.append(obj)
        return out

    def _export_objects(self, originals):
        """Export unique-suffixed *copies* of *originals* to :attr:`export_path`.

        Copies (not the originals) are exported so the FBX carries ``__RZTMP`` names the re-import
        maps back to originals; the user's objects are never renamed. Each copy is linked into its
        original's collections (so it's in the export set / view layer), exported, then removed
        along with its copied mesh data. Populates :attr:`_export_name_map`.
        """
        import bpy

        self._export_name_map = {}
        copies = []
        for i, orig in enumerate(originals):
            copy = orig.copy()
            copy.data = (
                orig.data.copy()
            )  # independent mesh data so removing it can't touch orig
            safe = re.sub(r"[^0-9A-Za-z_]", "_", orig.name)
            copy.name = f"{safe}_{i}{self._TEMP_SUFFIX}"
            collections = list(orig.users_collection) or [bpy.context.scene.collection]
            for coll in collections:
                coll.objects.link(copy)
            # Key on the name Blender ACTUALLY assigned — link() uniquifies on a stale collision
            # (a copy left over from a crashed run), so a map keyed on the requested name would
            # silently skip that object's UV transfer on re-import.
            self._export_name_map[copy.name] = orig
            copies.append(copy)

        if not copies:
            raise RuntimeError("Failed to create any export copies.")

        Path(self.export_path).parent.mkdir(parents=True, exist_ok=True)
        self.logger.info(
            f"Exporting {len(copies)} object(s) to "
            f'<a href="action://open?path={self.export_path}">{self.export_path}</a>'
        )
        try:
            # use_mesh_modifiers=False: round-trip the BASE topology, not the modifier-evaluated
            # mesh. UVs are authored on the base cage; exporting an evaluated Subsurf/Mirror result
            # would re-import a denser mesh whose loop count no longer matches the original, forcing
            # the lossy spatial fallback. Base-topology round-trip keeps the per-loop transfer exact
            # (and matches mayatk, whose FBX export carries the base poly mesh, not the smooth preview).
            FbxUtils.export(
                filepath=self.export_path,
                objects=copies,
                selection_only=True,
                use_mesh_modifiers=False,
            )
            self.logger.debug("FBX export completed successfully")
        finally:
            # Remove the copies (and their orphaned mesh data) before re-import so the re-imported
            # objects come back under the exact __RZTMP names (nothing to collide with -> no .001).
            for copy in copies:
                mesh = copy.data
                try:
                    bpy.data.objects.remove(copy, do_unlink=True)
                except Exception as error:  # noqa: BLE001
                    self.logger.warning(f"Failed to remove export copy: {error}")
                    continue
                if getattr(mesh, "users", 1) == 0:
                    try:
                        bpy.data.meshes.remove(mesh)
                    except Exception:  # noqa: BLE001
                        pass

    def _execute_uv_script(self):
        """Wrap the resolved script, run RizomUV headlessly, and verify it rewrote the FBX.

        DCC-agnostic — mirror of mayatk's ``_execute_uv_script`` (same version-gating, same
        exit-code + FBX-modified verification)."""
        user_script_content = (
            Path(self._script_path).read_text(encoding="utf-8")
            if self._script_path
            else ""
        )
        full_script_content = self._construct_full_script(user_script_content)
        self._script_path = self._prepare_script_file(full_script_content)

        self.logger.info(
            f"Running RizomUV with script "
            f'<a href="action://open?path={self._script_path}">{self._script_path}</a>'
        )
        self.logger.debug(f"Script content:\n{full_script_content}")

        exe = self.rizom_path
        if not exe:
            raise RuntimeError(
                "RizomUV executable not found. Pass rizom_path= or add RizomUV to PATH."
            )

        export_file = Path(self.export_path)
        if not export_file.exists():
            self.logger.warning("Export file does not exist before RizomUV!")
        # Snapshot the pre-run state so we can verify RizomUV actually wrote new UVs. A non-zero
        # exit, a Lua error before ZomSave, or a license failure all leave the file untouched.
        pre_mtime = export_file.stat().st_mtime if export_file.exists() else 0
        pre_size = export_file.stat().st_size if export_file.exists() else 0

        self.logger.debug(f"Executing command: {exe} -cfi {self.script_path}")
        try:
            result = ptk.AppLauncher.run(
                exe,
                args=["-cfi", self.script_path],
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"RizomUV did not exit within {self.timeout}s -- killed. "
                f"For dense meshes, raise RizomUVBridge(timeout=...)."
            ) from e
        except FileNotFoundError as e:
            raise RuntimeError(f"RizomUV executable not runnable: {e}") from e

        self.logger.debug(f"RizomUV return code: {result.returncode}")
        if result.stdout:
            self.logger.debug(f"RizomUV stdout:\n{result.stdout}")
        if result.stderr:
            self.logger.debug(f"RizomUV stderr:\n{result.stderr}")

        if result.returncode != 0:

            def tail(s, n=2048):
                return (s or "")[-n:].rstrip()

            stdout_tail, stderr_tail = tail(result.stdout), tail(result.stderr)
            msg = [
                f"RizomUV exited with code {result.returncode} "
                f"(version detected: {self.rizom_version}, script: {self._script_path})."
            ]
            if stdout_tail:
                msg.append(f"--- stdout (tail) ---\n{stdout_tail}")
            if stderr_tail:
                msg.append(f"--- stderr (tail) ---\n{stderr_tail}")
            if not stdout_tail and not stderr_tail:
                msg.append(
                    "(RizomUV produced no captured output -- the process likely crashed before "
                    "flushing. Try running the script manually in RizomUV's Script Editor.)"
                )
            raise RuntimeError("\n".join(msg))

        if not export_file.exists():
            raise RuntimeError(
                f"RizomUV claimed success but the export file is gone: {export_file}"
            )
        post_mtime, post_size = export_file.stat().st_mtime, export_file.stat().st_size
        if post_mtime == pre_mtime and post_size == pre_size:
            raise RuntimeError(self._no_save_diagnosis(full_script_content))

    def _no_save_diagnosis(self, expected_script: str) -> str:
        """Explain a clean RizomUV exit that never reached ``ZomSave``.

        RizomUV writes nothing to stdout/stderr in ``-cfi`` mode, so there is no Lua traceback to
        enable -- the script itself is the only evidence. The first thing worth ruling out is a
        mid-run overwrite of that script (see :meth:`_process_tag`), so compare what's on disk
        against what we handed RizomUV instead of guessing.
        """
        # Both rendered OS-native: ``export_path`` is a POSIX string (Rizom's Lua wants forward
        # slashes) and ``_script_path`` a Path, so quoting them as-is mixed separators in one
        # message the user has to act on.
        lines = [
            "RizomUV exited cleanly but never wrote the FBX -- the Lua script stopped before "
            "ZomSave.",
            f"Script: {Path(self._script_path)}",
            f"FBX:    {Path(self.export_path)}",
        ]
        try:
            on_disk = Path(self._script_path).read_text(encoding="utf-8")
        except OSError as e:
            lines.append(f"The script file is no longer readable: {e}")
        else:
            if on_disk != expected_script:
                lines.append(
                    "The script on disk no longer matches what the bridge wrote -- another "
                    "process replaced it while RizomUV was running (RizomUV re-reads the -cfi "
                    "file after launch). Run one bridge at a time."
                )
        lines.append(
            "RizomUV prints no diagnostics in -cfi mode, so no amount of debug logging will "
            "surface the failing Lua line -- open the script above in RizomUV's Script Editor "
            "and run it there. Degenerate input UVs (every coordinate collapsed onto one point) "
            "are one known trigger."
        )
        return "\n".join(lines)

    def _import_objects(self):
        """Import the RizomUV-processed FBX; return ALL new objects.

        The mesh subset drives the UV transfer; the full set drives cleanup (a mesh-only FBX rarely
        imports a non-mesh, but if it does it must still be removed). Snapshots the material/image
        datablocks first so :meth:`_purge_import_orphans` can drop the ones the import creates.
        """
        import bpy

        self._pre_import_ids = (set(bpy.data.materials), set(bpy.data.images))
        self.logger.debug(f"Importing objects from: {self.export_path}")
        with CoreUtils.window_context_override():
            new_objs = FbxUtils.import_fbx(self.export_path)
        self.logger.debug(
            f"Imported {len(new_objs)} object(s): {[o.name for o in new_objs]}"
        )
        return new_objs

    def _transfer_uvs_and_cleanup(self, imported, originals):
        """Transfer UVs from *imported* back onto the mapped *originals*, then remove the imports.

        Cleanup runs in a ``finally`` so a failed transfer never strands the temporary import
        objects in the scene. Mirror of mayatk's ``_transfer_uvs_and_cleanup``.
        """
        import bpy

        try:
            meshes = [o for o in imported if getattr(o, "type", None) == "MESH"]
            if not meshes or not originals:
                self.logger.warning("No mesh objects to transfer UVs between!")
                return

            pairs = []
            for imp in meshes:
                dst = self._map_import_to_original(imp, originals)
                if dst is None:
                    self.logger.debug(
                        f"Imported object {imp.name} not mapped; skipping."
                    )
                    continue
                pairs.append((imp, dst))

            if not pairs:
                self.logger.warning("No valid mapped object pairs for UV transfer.")
                return

            self.logger.info(f"Transferring UVs to {len(pairs)} object(s).")
            for src, dst in pairs:
                try:
                    self._transfer_uv_pair(src, dst)
                    self.logger.debug(f"UV transfer success: {src.name} -> {dst.name}")
                except Exception as error:  # noqa: BLE001
                    self.logger.error(
                        f"UV transfer failed for {src.name} -> {dst.name}: {error}"
                    )
        finally:
            # Remove EVERY imported object (not just the mapped meshes) + its orphaned mesh data.
            for imp in imported:
                mesh = imp.data if getattr(imp, "type", None) == "MESH" else None
                try:
                    bpy.data.objects.remove(imp, do_unlink=True)
                except Exception as error:  # noqa: BLE001
                    self.logger.warning(f"Failed to remove imported node: {error}")
                    continue
                if mesh is not None and mesh.users == 0:
                    try:
                        bpy.data.meshes.remove(mesh)
                    except Exception:  # noqa: BLE001
                        pass
            self._purge_import_orphans()
            self.logger.debug("Cleanup completed.")

    def _purge_import_orphans(self):
        """Remove the material/image datablocks the FBX import created that are now unused.

        The round-trip only needs UVs, but ``import_scene.fbx`` also creates material (and image)
        datablocks; once the imported objects are gone these are 0-user orphans that would otherwise
        accumulate in the .blend on every run. Purge only what THIS import added (diffed against the
        pre-import snapshot) -- never the user's own orphan data. Materials first so their images
        drop to 0 users before the image pass.
        """
        import bpy

        pre_mats, pre_imgs = getattr(self, "_pre_import_ids", (None, None))
        if pre_mats is None:
            return
        for collection, pre in (
            (bpy.data.materials, pre_mats),
            (bpy.data.images, pre_imgs),
        ):
            for db in list(collection):
                if db not in pre and db.users == 0:
                    try:
                        collection.remove(db)
                    except Exception:  # noqa: BLE001
                        pass
        self._pre_import_ids = (None, None)

    def _map_import_to_original(self, imp, originals):
        """Resolve the original bpy object a re-imported object corresponds to.

        Primary: exact ``_export_name_map`` hit (import names == exported copy names because the
        copies were removed before import, so nothing collides -> no ``.001`` suffix). Fallbacks:
        strip a Blender ``.NNN`` duplicate suffix, then parse the ``_<index>__RZTMP`` token.
        """
        dst = self._export_name_map.get(imp.name)
        if dst is not None:
            return dst
        base = re.sub(r"\.\d{3}$", "", imp.name)
        dst = self._export_name_map.get(base)
        if dst is not None:
            return dst
        match = re.search(rf"_(\d+){re.escape(self._TEMP_SUFFIX)}", imp.name)
        if match:
            idx = int(match.group(1))
            if 0 <= idx < len(originals):
                return originals[idx]
        return None

    def _transfer_uv_pair(self, src, dst):
        """Copy the active UV layer from *src* (imported) onto *dst* (original).

        Delegates to :meth:`blendertk.UvUtils.transfer_uvs`, which owns the
        exact-copy / spatial-fallback pair logic shared with the auto-unwrap
        round-trip; this wrapper only adds the bridge's logging.
        """
        from blendertk.uv_utils._uv_utils import UvUtils

        if src.data.uv_layers.active is None:
            self.logger.warning(
                f"{src.name} has no active UV layer; nothing to transfer."
            )
            return
        src_loops, dst_loops = len(src.data.loops), len(dst.data.loops)
        if src_loops != dst_loops:
            self.logger.warning(
                f"Loop-count mismatch ({src_loops} vs {dst_loops}) for "
                f"{src.name} -> {dst.name}; falling back to spatial data_transfer."
            )
        UvUtils.transfer_uvs(src, dst, match_by_similarity=False)

    # ------------------------------------------------------------------
    # script resolution / construction (DCC-agnostic — mirror of mayatk)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_script(uv_script=None, preset=None):
        """Return the Lua body to run inside the wrapper (raw string, file, or preset name)."""
        if uv_script and preset:
            raise ValueError("Provide either uv_script or preset, not both.")
        if preset:
            lua_path = _SCRIPT_DIR / f"{preset}.lua"
            if not lua_path.is_file():
                raise FileNotFoundError(
                    f"Preset '{preset}' not found. Expected: {lua_path}\n"
                    f"Available: {[p.stem for p in _SCRIPT_DIR.glob('*.lua')]}"
                )
            return lua_path.read_text(encoding="utf-8")
        if uv_script is not None:
            p = Path(uv_script)
            return p.read_text(encoding="utf-8") if p.is_file() else uv_script
        return None

    def _construct_full_script(self, user_script):
        """Wrap *user_script* in the ZomLoad/ZomSave/ZomQuit boilerplate (``templates/wrapper.lua``).

        Version-strips unsupported placeholders, substitutes registered ``__KEY__`` tokens, and
        gates the nested ``FBX={UseUVSetNames=true}`` flag. Mirror of mayatk's
        ``_construct_full_script``."""
        from blendertk.uv_utils.rizom_bridge import parameters as _params

        export_path_normalized = str(self.export_path).replace("\\", "/")
        is_fbx = Path(self.export_path).suffix.lower() == ".fbx"
        version = self.rizom_version

        # Expand shared includes (__PACK_BLOCK__) so their lines participate in version-stripping +
        # substitution below.
        user_script = _params.Parameters.expand_includes(user_script)

        # Drop lines referencing placeholders the installed Rizom doesn't support (older Rizom
        # access-violates on unknown fields; see MIN_VERSIONS).
        user_script = _params.Parameters.strip_unsupported(user_script, version)

        merged = _params.Parameters.defaults()
        merged.update(self._params or {})
        param_context = _params.Parameters.render_context(merged)
        # User-script substitution first, so its placeholders see the resolved values before the
        # wrapper inlines the (already-substituted) body.
        user_script = ptk.StrUtils.replace_delimited(user_script, param_context)

        if "ZomLoad" in user_script and "ZomSave" in user_script:
            self.logger.debug("User script contains ZomLoad/ZomSave; using as-is.")
            return user_script

        # FBX={UseUVSetNames=true} (nested table) preserves the UV-set name across the round-trip;
        # only exists on newer Rizom -- below the gate, emit nothing and rely on extension detect.
        fbx_flag = (
            ", FBX={UseUVSetNames=true}"
            if is_fbx and version >= _params.FBX_USE_UV_SET_NAMES_MIN_VERSION
            else ""
        )

        wrapper = (_TEMPLATE_DIR / "wrapper.lua").read_text(encoding="utf-8")
        full_script = ptk.StrUtils.replace_delimited(
            wrapper,
            {
                "EXPORT_PATH": export_path_normalized,
                "FBX_FLAG": fbx_flag,
                "USER_SCRIPT": user_script,
            },
        )
        self.logger.debug(f"Constructed full script:\n{full_script}")
        return full_script

    def _prepare_script_file(self, script_contents) -> Path:
        """Save the Lua script for RizomUV; store + return its Path (kept a Path for script_path).

        One store-allocated path per bridge, reused across the two writes a run makes (the raw
        preset via the ``script_path`` setter, then the wrapped script): the second must *replace*
        the first, and RizomUV is handed the path only after both have happened.
        """
        if self._lua_path is None:
            self._lua_path = Path(self._temp_store.path(extension=".lua"))
        self._lua_path.write_text(script_contents, encoding="utf-8")
        self._script_path = self._lua_path
        return self._lua_path

    def _announce_handoff(self, preset: str, count: int) -> None:
        """Log the round-trip success summary (parallel to the send announce)."""
        self.logger.info(f"RizomUV '{preset}' applied to {count} object(s).")
