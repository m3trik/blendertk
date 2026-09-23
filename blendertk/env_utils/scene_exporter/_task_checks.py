# !/usr/bin/python
# coding=utf-8
"""The pre-export validation checks (mirror of mayatk's) -- each returns
``(passed, messages)`` and is hoisted to the earliest point its
``ptk.ExportProfile.CHECK_DEPENDENCIES`` entry allows.
"""

import logging
import os
from collections import defaultdict
from typing import Any, Dict, List

import pythontk as ptk

from blendertk.core_utils._core_utils import CoreUtils
from blendertk.env_utils.scene_exporter._task_data import (
    _LOD_SUFFIX_RE,
    _TaskDataMixin,
)


class _TaskChecksMixin(_TaskDataMixin):
    """The pre-export validation checks (mirror of mayatk's) -- each returns
    ``(passed, messages)`` and is hoisted to the earliest point its ``ptk.ExportProfile.CHECK_DEPENDENCIES`` entry allows."""

    def _warn_unseen_animation(self, check_name: str) -> None:
        """One WARNING per run when the export objects carry NLA-strip or data-level
        (object data / shape-key) animation the active-action anim checks cannot see.

        Those checks keep their pass/fail semantics on active object actions — NLA
        strip actions may be shared/library-linked and carry their own frame mapping,
        so extending the *edit* tasks to them is deliberately off the table — but the
        FBX write bakes the evaluated scene, so a silently-green check over animation
        it never validated is a lie. The flag is reset per run in
        :meth:`begin_run`."""
        if self._unseen_anim_warned:
            return
        from blendertk.anim_utils._anim_utils import AnimUtils

        if AnimUtils.has_nla_or_data_animation(self.objects or []):
            self._unseen_anim_warned = True
            self.logger.warning(
                "NLA/data-level animation present — "
                f"{check_name} only validates active object actions."
            )

    def check_framerate(self, target_key) -> tuple:
        if not target_key:
            return True, []
        target = ptk.VidUtils.FRAME_RATES.get(target_key)
        if target is None:
            return True, []
        self._warn_unseen_animation("check_framerate")
        if not self._has_keyframes:
            return True, []
        scene = self._scene()
        actual = scene.render.fps / scene.render.fps_base
        if abs(actual - target) > 1e-3:
            return False, [
                f"Scene FPS ({actual:g}) does not match target ({target:g})."
            ]
        return True, []

    def check_referenced_objects(self, enabled) -> tuple:
        if not enabled:
            return True, []
        from blendertk.env_utils._env_utils import EnvUtils

        libs = EnvUtils.list_libraries()
        if libs:
            names = ", ".join(r["name"] for r in libs)
            return False, [
                f"Scene has {len(libs)} linked librar{'y' if len(libs) == 1 else 'ies'}: {names}"
            ]
        return True, []

    def check_geometry_lod_suffix(self, enabled) -> tuple:
        """Informational only -- always succeeds (mirrors mayatk's contract)."""
        if not enabled or not self.objects:
            return True, []
        found = [
            o.name
            for o in self.objects
            if o.type == "MESH" and _LOD_SUFFIX_RE.search(o.name)
        ]
        if found:
            shown = ", ".join(found[:10]) + (" …" if len(found) > 10 else "")
            return True, [f"{len(found)} object(s) use an LOD suffix: {shown}"]
        return True, []

    #: Duplicate Names -- how wide the base-name scan casts, narrowest first;
    #: each tier is a superset of the one above it.  Labels and scope tokens
    #: are mayatk's verbatim (one dial, one portable preset across both DCCs);
    #: the mapping is the obvious one -- a Maya locator is an Empty, a Maya
    #: joint is an armature bone.
    #:
    #: Blender force-uniques ``bpy.data.objects`` names, so the collision that
    #: actually reaches an FBX is the auto ``.001`` suffix -- stripped before
    #: comparing, which is the name a downstream consumer matches.
    _duplicate_name_options: Dict[str, Any] = {
        "OFF": None,
        "Locators": "locators",
        "Locators & Joints": "joints",
        "Connected & Animated": "connected",
        "All Export Objects": "all",
    }

    #: Scope token -> its combo label, for the failure report's header.
    _duplicate_name_labels: Dict[str, str] = {
        v: k for k, v in _duplicate_name_options.items() if v
    }

    @staticmethod
    def _name_is_load_bearing(obj) -> bool:
        """Is *obj* driven, keyed or constrained -- is its NAME what rebuilds
        that plumbing downstream resolves against?"""
        if obj.constraints:
            return True
        anim = obj.animation_data
        return bool(anim and (anim.action or anim.drivers or anim.nla_tracks))

    def _duplicate_name_scope(self, scope: str) -> List[str]:
        """The export-set names *scope* puts in front of the duplicate scan.

        *scope* is validated by :meth:`check_duplicate_names`; anything it did
        not recognize never reaches here (the widest branch is the fallthrough,
        so an unvalidated typo would silently scan a NARROWER tier and pass).
        """
        objects = list(self.objects or [])
        if not objects:
            return []

        if scope == "all":
            picked = objects
        else:
            picked = [o for o in objects if o.type == "EMPTY"]
            if scope != "locators":
                picked += [o for o in objects if o.type == "ARMATURE"]
                if scope != "joints":
                    picked += [
                        o
                        for o in objects
                        if o.type not in ("EMPTY", "ARMATURE")
                        and self._name_is_load_bearing(o)
                    ]

        names = [o.name for o in picked]
        # Bones share the FBX node namespace with objects, so two armatures
        # carrying a same-named bone collide exactly like two same-named
        # Empties -- and bone names are unique only WITHIN an armature.
        names += [
            b.name
            for o in picked
            if o.type == "ARMATURE" and o.data
            for b in o.data.bones
        ]
        return names

    def check_duplicate_names(self, scope=None) -> tuple:
        """Nodes sharing a base name once Blender's auto ``.001``-style suffix is stripped --
        the Blender analogue of Maya's same-short-name-under-different-parents collision
        (Blender itself force-uniques ``bpy.data.objects`` names, so the exact Maya failure
        mode can't occur; this catches the case that motivated the check).

        Parameters:
            scope: One of :attr:`_duplicate_name_options`' values --
                ``"locators"``, ``"joints"``, ``"connected"`` or ``"all"``.
                Falsy (or ``"OFF"``) skips the check; ``True`` is read as
                ``"locators"``, the scope the pre-dial checkbox had.
        """
        if not scope or str(scope).upper() == "OFF":
            return True, []
        scope = "locators" if scope is True else str(scope).lower()
        if scope not in self._duplicate_name_labels:
            # Loud, not a fallthrough: the resolver's widest branch is its
            # default, so a typo'd scope would quietly scan a NARROWER tier
            # than the caller asked for and PASS the export on that basis.
            valid = ", ".join(sorted(self._duplicate_name_labels))
            return False, [f"Unknown duplicate-name scope {scope!r}. Valid: {valid}."]

        groups = defaultdict(list)
        for name in self._duplicate_name_scope(scope):
            groups[CoreUtils.strip_dup_suffix(name)].append(name)
        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if not dupes:
            return True, []

        label = self._duplicate_name_labels.get(scope, scope)
        messages = [f"{len(dupes)} duplicate base name(s) in scope '{label}':"]
        messages += [
            f"  - {base} (x{len(names)}): {', '.join(names)}"
            for base, names in sorted(dupes.items())
        ]
        return False, messages

    def check_root_default_transforms(self, enabled) -> tuple:
        """Root groups (an Empty with children) should sit at identity transform."""
        if not enabled or not self.objects:
            return True, []
        from blendertk.node_utils._node_utils import NodeUtils

        roots = set()
        for o in self.objects:
            chain = NodeUtils.get_parent(o, all=True)
            root = chain[-1] if chain else o
            if root.type == "EMPTY" and root.children:
                roots.add(root)

        bad = []
        for root in roots:
            loc = tuple(round(v, 5) for v in root.location)
            rot = tuple(round(v, 5) for v in root.rotation_euler)
            scale = tuple(round(v, 5) for v in root.scale)
            if (
                loc != (0.0, 0.0, 0.0)
                or rot != (0.0, 0.0, 0.0)
                or scale != (1.0, 1.0, 1.0)
            ):
                bad.append(root.name)
        if bad:
            return False, [
                f"Root group(s) with non-default transform: {', '.join(bad)}"
            ]
        return True, []

    def check_hidden_geometry(self, enabled) -> tuple:
        if not enabled or not self.objects:
            return True, []
        hidden = [
            o.name for o in self.objects if o.type == "MESH" and not o.visible_get()
        ]
        if hidden:
            shown = ", ".join(hidden[:10]) + (" …" if len(hidden) > 10 else "")
            # Opposite consequence to the Maya mirror: the export funnel is
            # selection-based (use_selection=True) and hidden objects can't be
            # selected, so hidden meshes are silently DROPPED from the FBX —
            # they do not ship hidden.
            return False, [
                f"{len(hidden)} hidden mesh object(s) would be silently OMITTED "
                f"from the export: {shown}"
            ]
        return True, []

    def check_overlapping_duplicate_mesh(self, enabled) -> tuple:
        if not enabled or not self.objects:
            return True, []
        from blendertk.edit_utils._edit_utils import EditUtils

        dupes = EditUtils.get_overlapping_duplicates(objects=self.objects)
        if dupes:
            shown = ", ".join(o.name for o in dupes[:10]) + (
                " …" if len(dupes) > 10 else ""
            )
            return False, [
                f"{len(dupes)} overlapping duplicate mesh object(s) found: {shown}"
            ]
        return True, []

    #: How far geometry may reach below the floor by default (mirror of mayatk's).
    _DEFAULT_FLOOR_TOLERANCE = 0.5

    def check_objects_below_floor(
        self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE
    ) -> tuple:
        """Fail when a mesh reaches deeper than *tolerance* below Z=0.

        Blender is Z-up natively (Maya's version checks Y). *tolerance* is the
        panel's **Max Depth Below Floor** spin box: ``0`` (its OFF), ``None``
        or ``False`` disable the check; ``True`` (a checkbox-era template)
        means the default depth rather than ``float(True)``.
        """
        if tolerance is True:
            tolerance = self._DEFAULT_FLOOR_TOLERANCE
        if not tolerance or float(tolerance) <= 0.0 or not self.objects:
            return True, []
        tolerance = float(tolerance)
        from blendertk.xform_utils._xform_utils import XformUtils

        below = []
        for o in self.objects:
            if o.type != "MESH":
                continue
            mn, _mx = XformUtils.get_world_bbox(o)
            if mn.z < -tolerance:
                below.append(o.name)
        if below:
            shown = ", ".join(below[:10]) + (" …" if len(below) > 10 else "")
            return False, [
                f"{len(below)} object(s) reach deeper than {tolerance:.3f} below "
                f"the floor (Z=0): {shown}"
            ]
        return True, []

    def check_duplicate_materials(self, enabled) -> tuple:
        if not enabled:
            return True, []
        from blendertk.mat_utils._mat_utils import MatUtils

        groups = MatUtils.find_materials_with_duplicate_textures(
            materials=self._get_all_materials()
        )
        if groups:
            messages = [", ".join(m.name for m in g) for g in groups]
            return False, [
                f"{len(groups)} duplicate material group(s) found:"
            ] + messages
        return True, []

    def check_material_compatibility(self, template) -> tuple:
        """Every mask map matches the chosen texture template (mirror of mayatk's).

        The check half of the Texture Template combobox: armed only when a
        template is selected, alongside :meth:`convert_textures`. Checks run
        after the task phase, so this validates the **converted** state -- it
        fails only for a mask map the conversion could not bring to the
        template, naming the residuals rather than blocking the fix.

        The judgement is pythontk's (``MeshConvert.sidecar_foreign_packings``
        -> ``MapFactory.foreign_packings``), keyed by the registry workflow the
        combobox named, so no engine name or channel layout is spelled out here
        and this cannot drift from the Maya twin.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        from blendertk.env_utils.scene_state import SceneState

        try:
            sections = SceneState.read(self.objects or [])
        except Exception:  # noqa: BLE001 — a read failure must not block an export
            self.logger.warning("Material compatibility check skipped.", exc_info=True)
            return True, []

        foreign = ptk.MeshConvert.sidecar_foreign_packings(
            {"sections": sections}, workflow=template
        )
        if not foreign:
            return True, []

        # Count header then indented offenders, as check_path_length does.
        messages = [
            f"{len(foreign)} mask map(s) do not match the {template!r} template "
            "after conversion:"
        ]
        messages.extend(
            f"  - {map_type}: {os.path.basename(path)}"
            for path, map_type in sorted(foreign.items())
        )
        return False, messages + [
            "See the Map Updater log above for why these did not convert, or "
            "set Textures back to 'As Authored' to ship them as they are."
        ]

    def check_texture_optimization(self, template) -> tuple:
        """Every shipping texture is optimized for its map type (mirror of mayatk's).

        The check half of the Optimize Textures checkbox: armed alongside
        :meth:`optimize_textures` by the same setting, judged through the same
        :meth:`_assess_optimization`, and — because checks run after tasks —
        validating the **staged/written** state the export will actually
        read. It FAILS only for a texture the task should have optimized but
        could not (a per-texture failure), naming the residuals rather than
        blocking the fix.

        Everything the pass deliberately does not touch is reported without
        failing: tiled/UDIM sets (measured via their 1001 tile), and the
        active template's ``DeliveryBudget`` advisories — advisory means
        REPORTED, not resampled, and never a blocked export. With a Max
        Texture Size clamp set the resize IS part of the pass, so an
        over-size residual the task could not shrink fails here like any
        other unoptimized map. Those notes are logged directly (the runner
        only surfaces messages from failing checks). Unreadable or missing files are :meth:`check_valid_paths`'
        domain and are skipped here.

        Returns:
            tuple: (status: bool, messages: list)
        """
        if not template:
            return True, []
        tpl = template if isinstance(template, str) else None

        offenders: List[str] = []
        notes: List[str] = []
        advisories: Dict[str, List[str]] = {}  # warning text -> texture names
        # include_tiled: the task cannot TOUCH a tiled set, but the gate
        # should still measure it (via its 1001 tile) so an unoptimized one
        # is at least reported instead of slipping past the scan.
        for _key, entry in sorted(
            self._export_texture_sources(include_tiled=True).items()
        ):
            verdict = self._assess_optimization(entry["path"], tpl)
            if verdict is None:
                continue
            name = os.path.basename(entry["path"])
            if verdict["needed"]:
                names = ", ".join(sorted(img.name for img in entry["images"]))
                line = f"  - {names} -> {name}: {'; '.join(verdict['reasons'])}"
                if entry["tiled"]:
                    notes.append(line + " (tiled set — not auto-optimized)")
                else:
                    offenders.append(line)
            for warning in verdict["warnings"]:
                advisories.setdefault(warning, []).append(name)

        # One line per advisory, not one per texture: a 4K set over a 2K budget
        # printed the same sentence 49 times (measured 2026-09-13), burying the
        # tiled-set notes it shared the group with.
        limit = 8
        for warning, names in advisories.items():
            more = len(names) - limit
            notes.append(
                f"  - {warning} -- {len(names)} texture(s): {', '.join(names[:limit])}"
                + (f" (+{more} more)" if more > 0 else "")
            )

        # Advisory tier: budget notes and untouchable residuals inform, never
        # gate. Logged directly — the runner only surfaces messages from
        # FAILING checks, so returning them on a pass would be a silent no-op.
        if notes and self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(f"Texture optimization notes ({len(notes)})", notes)

        if offenders:
            pass_desc = f"the {tpl!r} template" if tpl else "their map type"
            return False, [
                f"{len(offenders)} texture(s) are not optimized for "
                f"{pass_desc} after the optimization task:"
            ] + offenders

        return True, []

    def check_path_length(self, max_length) -> tuple:
        """No export path exceeds the OS path-length limit (mirror of mayatk's).

        Covers the export destination and every export texture, measured in
        ABSOLUTE form — that is the string the filesystem, the FBX exporter and
        the receiving pipeline all see, and a ``//``-relative path can be short
        while resolving to a very long one. PACKED images are exempt: they ship
        embedded from memory, so their (often stale) bookkeeping path never
        travels.

        ``max_length`` may be the spin box's value or any numeric-ish string;
        ``0`` (the spin box's "OFF" position) or ``"OFF"`` disables the check,
        and a non-numeric value logs a warning and skips.
        """
        if max_length is not None:
            if not max_length or str(max_length).upper() == "OFF":
                return True, []
            try:
                limit = int(max_length)
            except (TypeError, ValueError):
                self.logger.warning(
                    f"Invalid max path length '{max_length}'. Skipping length check."
                )
                return True, []
        else:
            limit = ptk.FileUtils.path_length_limit()

        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        offenders = []

        export_path = self.export_path
        if export_path:
            # ``_abspath`` already resolves an image's ``//`` path against its own
            # .blend; only the export path needs expanding here.
            resolved = os.path.abspath(os.path.expandvars(export_path))
            if ptk.FileUtils.exceeds_path_length(resolved, limit):
                offenders.append(f"export path ({len(resolved)} chars)")

        seen = set()
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                continue  # ships embedded from memory; its stored path never travels
            path = _MatUtilsInternal._abspath(img)
            if not path or path in seen:
                continue
            seen.add(path)
            if ptk.FileUtils.exceeds_path_length(path, limit):
                offenders.append(f"{img.name} ({len(path)} chars)")

        if offenders:
            shown = ", ".join(offenders[:10]) + (" …" if len(offenders) > 10 else "")
            return False, [
                f"{len(offenders)} path(s) exceed the {limit}-character limit: {shown}"
            ]
        return True, []

    def _deliverable_paths(self) -> List[str]:
        """The files this run will write, in the order it writes them.

        A GLB-only run writes its FBX to a throwaway temp dir, so only the
        ``.glb`` is a destination there; every other mode writes the export
        path itself, plus a sibling ``.glb`` when one is produced. Mirror of
        mayatk's.
        """
        export_path = self.export_path or ""
        if not export_path:
            return []
        glb_only = bool(self.run.glb_only)
        paths = [] if glb_only else [export_path]
        if glb_only or self.run.create_glb:
            paths.append(os.path.splitext(export_path)[0] + ".glb")
        return paths

    def check_output_writable(self) -> tuple:
        """Check that every file this run will write can actually be replaced.

        Windows refuses to delete a file, or rename onto it, while another
        process holds it open -- so a deliverable someone is previewing fails
        the write with ``[WinError 32]``. The cost is not the failure but its
        TIMING: the write is the last thing an export does, so a file handle
        that was there all along discards the entire pipeline's work, and
        (for GLB-only) a finished conversion with it.

        Which is why this check declares no task dependencies: it is decidable
        before the first mutation, the scheduler hoists it ahead of everything,
        and a locked destination stops the run in milliseconds with the name of
        the process to close rather than in minutes with an errno. A path that
        does not exist yet cannot be held, and passes. Mirror of mayatk's.

        Returns:
            tuple: (status: bool, messages: list)
        """
        # Resolved rather than called directly: blendertk and pythontk update
        # independently, and an older pythontk would raise AttributeError
        # HERE -- aborting the very export this check exists to protect.
        describe = getattr(ptk.FileUtils, "describe_lock", None)
        if describe is None:
            self.logger.warning(
                "Output-writability check skipped: this pythontk predates "
                "FileUtils.describe_lock. A destination held open by another "
                "process will not be caught until the write fails."
            )
            return True, []
        blocked = [(path, describe(path)) for path in self._deliverable_paths()]
        blocked = [(path, why) for path, why in blocked if why]
        if not blocked:
            return True, []
        return False, [
            f"{len(blocked)} destination file(s) cannot be replaced:",
        ] + [
            f"  - {os.path.basename(path)} is {why} -> {path}" for path, why in blocked
        ] + [
            "Close whatever holds the file (a viewer, the WebXR preview, an "
            "engine import) and re-run.",
        ]

    def check_valid_paths(self, enabled) -> tuple:
        """Every export texture and every linked library resolves on disk.

        Image scope is ``_get_export_images`` — the datablocks feeding the
        materials assigned to ``self.objects`` — not every FILE image in the
        .blend (mirrors mayatk's ``check_valid_paths``).  Whole-file scope
        flagged maps that never ship: the World/Environment-Texture HDR (never
        part of the object export set at all) and the zero-user images left
        behind after a duplicate-material cleanup.  Linked libraries stay
        whole-file — they are the analogue of Maya's scene references, not of a
        texture.

        Storage-aware (mirrors mayatk's ``resolve_path`` semantics):

        * PACKED images are treated as valid — the FBX embeds them from memory,
          so a stale disk path is irrelevant (it used to fail the export over a
          file that never ships).
        * TILED (UDIM) images never appear in ``get_image_records`` (FILE-only),
          so a deleted tile set used to pass unseen.  They are validated here by
          probing the first declared tile on disk (``<UDIM>`` collapsed via
          ``tiles[0].number``, 1001 fallback — the same probe-tile collapse
          mayatk applies).
        """
        if not enabled:
            return True, []
        from blendertk.env_utils._env_utils import EnvUtils
        from blendertk.mat_utils._mat_utils import MatUtils, _MatUtilsInternal

        # Filter get_image_records() by the export set rather than re-deriving
        # "is a FILE image whose abspath exists" — that predicate belongs to
        # get_image_records, and a second copy of it here would be free to drift.
        records = {r["image"]: r for r in MatUtils.get_image_records()}
        missing = []
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                continue  # ships embedded from memory regardless of the stored path
            if getattr(img, "source", "") == "TILED":
                probe = _MatUtilsInternal._udim_first_tile_path(img)
                if not (probe and os.path.isfile(probe)):
                    missing.append(img.name)
            elif img in records and not records[img]["exists"]:
                missing.append(records[img]["name"])
        missing += [r["name"] for r in EnvUtils.list_libraries() if not r["exists"]]
        messages = []
        if missing:
            shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
            messages.append(f"{len(missing)} missing file(s): {shown}")

        # Lightmap dependencies (mirror of mayatk's check) -- baked maps the
        # bake markers name. No Image datablock references them, so the gate
        # above never sees them, and a scene migrated with its textures ships
        # its GLB unlit and its FBX manifest pointing at nothing. Resolved the
        # way the GLB applier resolves them; a map found only by search still
        # ships (the conversion is handed that folder) but says so, since the
        # manifest's hint is stale until the resolve task rewrites it.
        missing_lightmaps = []
        stale_lightmaps = []
        for dep in self._lightmap_dependencies():
            if not dep["path"]:
                missing_lightmaps.append(dep)
            elif dep["found_by"] != "hint":
                stale_lightmaps.append(dep)
        if missing_lightmaps:
            entries = []
            for dep in missing_lightmaps:
                where = f"{dep['dir']}/{dep['map']}" if dep["dir"] else dep["map"]
                note = f" ({dep['note']})" if dep.get("note") else ""
                entries.append(
                    f"Missing Lightmap: {', '.join(dep['objects'])} -> {where}{note}"
                )
            messages.append(
                f"{len(missing_lightmaps)} lightmap(s) the bake markers name are "
                "not on disk. The GLB would ship unlit and the FBX manifest would "
                "point at nothing. Relocate them (Texture Path Editor ▸ Find & "
                "Copy Textures, lightmaps included) or revert the bake (Lightmap "
                "Baker ▸ Revert)."
            )
            messages.extend(entries[:10] + (["…"] if len(entries) > 10 else []))
        for dep in stale_lightmaps:
            messages.append(
                f"Lightmap {dep['map']}: the recorded folder "
                f"{dep['dir'] or '<none>'} no longer holds it; found at "
                f"{dep['path']} (shipped from there; enable the Resolve Invalid "
                "Texture Paths task to rewrite the marker)."
            )

        if missing or missing_lightmaps:
            return False, messages
        return True, messages

    def check_texture_file_size(self, max_mb) -> tuple:
        """No export texture exceeds ``max_mb`` on disk.

        Iterates the export image *datablocks* (not bare paths) so storage is
        respected:

        * PACKED images are skipped — they ship embedded from memory, so the
          on-disk copy (if any) is not what exports.
        * TILED (UDIM) images are size-probed at their **largest existing
          tile** (the ``<UDIM>`` token globbed on disk).  Previously
          ``os.path.getsize`` on the raw token path raised ``OSError`` into a
          silent ``continue``, letting entire multi-GB tile sets through the
          gate unmeasured (mayatk collapses the token to a probe tile the same
          way).

        ``max_mb`` is read by ``ptk.ExportProfile.texture_size_limit_bytes``,
        the one reading both exporters share: the spin box's value or any
        numeric-ish string (e.g. ``"16"``) is megabytes; ``None``, ``0`` (the
        spin box's "OFF" position), ``""`` and ``"OFF"`` disable the check, and
        a negative or non-numeric value logs a warning and skips (mirror of
        mayatk's).

        Measures what the deliverable carries (mirror of mayatk's): a GLB-only
        export ships no scene map -- its GLB pass resizes and re-encodes every
        one -- so the check passes there; for FBX + GLB the failure names the
        FBX as the carrier. Unlike mayatk's, nothing holds the GLB's own images
        to the limit afterwards: blendertk has no post-write verify pass
        (mayatk's ``verify_deliverables(max_image_bytes=)``).
        """
        limit_bytes = ptk.ExportProfile.texture_size_limit_bytes(max_mb)
        if limit_bytes is None:
            if max_mb and str(max_mb).strip().upper() != "OFF":
                self.logger.warning(
                    f"Invalid max texture size '{max_mb}'. Skipping size check."
                )
            return True, []
        limit_mb = limit_bytes / (1024 * 1024)
        if self.run.glb_only:
            # A GLB-only export ships no scene map: the GLB pass resizes and
            # re-encodes each one (mirror of mayatk's). Parity gap: mayatk hands
            # this limit to its post-write verify pass, which measures the
            # images the GLB holds; blendertk has no such pass, so a GLB-only
            # export's images are never checked against Max Texture Size.
            self.logger.info(
                "Texture size check skipped: a GLB-only export ships its own "
                "re-encoded copies, not the scene's maps, and nothing measures "
                f"those against {limit_mb:g} MB."
            )
            return True, []
        from blendertk.mat_utils._mat_utils import _MatUtilsInternal

        oversized = []
        seen = set()
        for img in self._get_export_images():
            if getattr(img, "packed_file", None):
                continue  # ships embedded from memory; the disk copy is not what exports
            if getattr(img, "source", "") == "TILED":
                sizes = []
                for t in _MatUtilsInternal._udim_tile_paths(img):
                    try:
                        sizes.append((os.path.getsize(t), t))
                    except OSError:
                        continue
                if not sizes:
                    continue  # no tiles on disk — check_valid_paths' domain
                size, p = max(sizes)
            else:
                p = _MatUtilsInternal._abspath(img)
                if not p:
                    continue
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue  # missing file — check_valid_paths' domain
            if p in seen:
                continue
            seen.add(p)
            if size > limit_bytes:
                size_mb = size / (1024 * 1024)
                oversized.append(f"{os.path.basename(p)} ({size_mb:.1f} MB)")
        if oversized:
            shown = ", ".join(oversized[:10]) + (" …" if len(oversized) > 10 else "")
            # FBX + GLB: only the FBX carries these files (mirror of mayatk's).
            carrier = " the FBX carries" if self.run.create_glb else ""
            return False, [
                f"{len(oversized)} texture(s){carrier} exceed {limit_mb:g} MB: {shown}",
                self._texture_size_limit_remedy(),
            ]
        return True, []

    def _texture_size_limit_remedy(self) -> str:
        """What would bring an over-limit map under :meth:`check_texture_file_size`
        (mirror of mayatk's).

        Keyed on the Optimize Textures dial this run used (stamped by
        ``perform_export``): it is the export's only fix for an oversized
        map, and a failure right after an "Optimize" run otherwise reads as if
        the pass never ran -- without a ceiling it never resamples, by design,
        so it cannot bring a map under a byte limit.
        """
        if not self.run.optimize_textures:
            return (
                "Optimize Textures is OFF: an 'Optimize + Max …' ceiling "
                "downsamples the maps this export ships. Or raise this limit."
            )
        # The ceiling in pixels, 0 for none: the resolution the GLB pass takes,
        # which also reads the budget sentinel under an unbudgeted template as
        # no ceiling at all.
        ceiling = self.run.glb_max_size(logger=self.logger)
        if not ceiling:
            return (
                "Optimize Textures ran with no size ceiling, and without one the "
                "pass never resamples. Choose an 'Optimize + Max …' ceiling (or "
                "'Optimize + Template Budget' with a budgeted Textures template), "
                "or raise this limit."
            )
        return (
            f"Still over the limit with Optimize Textures clamped to {ceiling} px. "
            "Lower the ceiling, or raise this limit."
        )

    def check_untied_keyframes(self, enabled) -> tuple:
        """Verify every animated channel has a bookend key at its object's own keyed extent
        (the inverse of what ``tie_all_keyframes`` fixes)."""
        if not enabled:
            return True, []
        self._warn_unseen_animation("check_untied_keyframes")
        if not self._has_keyframes:
            return True, []

        from blendertk.anim_utils._anim_utils import AnimUtils

        untied = []
        for o in self.objects:
            bounds = [
                (fc, fc.keyframe_points[0].co.x, fc.keyframe_points[-1].co.x)
                for fc in AnimUtils.get_fcurves([o])
                if len(fc.keyframe_points)
            ]
            if not bounds:
                continue
            min_start = min(b[1] for b in bounds)
            max_end = max(b[2] for b in bounds)
            for fc, start, end in bounds:
                if start > min_start or end < max_end:
                    untied.append(
                        f"{o.name}.{fc.data_path}[{fc.array_index}] ({start:g}-{end:g} != "
                        f"{min_start:g}-{max_end:g})"
                    )

        if untied:
            shown = ", ".join(untied[:10]) + (" …" if len(untied) > 10 else "")
            return False, [f"{len(untied)} curve(s) with untied keyframes: {shown}"]
        return True, []

    def check_floating_point_keys(self, enabled) -> tuple:
        """Detect keyframes that don't sit on a whole frame."""
        if not enabled:
            return True, []
        self._warn_unseen_animation("check_floating_point_keys")
        if not self._has_keyframes:
            return True, []

        from blendertk.anim_utils._anim_utils import AnimUtils

        offenders = []
        for o in self.objects:
            for fc in AnimUtils.get_fcurves([o]):
                for k in fc.keyframe_points:
                    if abs(k.co.x - round(k.co.x)) > 1e-4:
                        offenders.append(
                            f"{o.name}.{fc.data_path}[{fc.array_index}] (frame {k.co.x:.3f})"
                        )
                        break

        if offenders:
            shown = ", ".join(offenders[:10]) + (" …" if len(offenders) > 10 else "")
            return False, [
                f"{len(offenders)} curve(s) have floating point keys: {shown}"
            ]
        return True, []
