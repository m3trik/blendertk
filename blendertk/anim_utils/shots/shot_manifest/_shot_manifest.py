# !/usr/bin/python
# coding=utf-8
"""Blender Shot Manifest adapter — the DCC layer over pythontk's manifest engine.

Mirror of mayatk's ``anim_utils.shots.shot_manifest`` at the class level
(:class:`BlenderShotManifest`).  The CSV parsing, column mapping, behavior
keying-math, range resolution, and build planning all live once, pure, in
``pythontk.core_utils.engines.shots.manifest``; this class subclasses that
engine's :class:`~pythontk.ShotManifest` and overrides only its scene hooks:

- ``_resolve_fps`` → ``scene.render.fps / fps_base``;
- ``_measure_audio`` → an existing VSE sound strip's duration;
- ``_resolve_names_keep_missing`` → identity (Blender names are unique);
- ``_discover_scene_objects`` / ``_filter_to_animated`` → objects carrying
  transform keys in a shot's range (fcurve walk);
- assess seams (``_object_exists`` / ``_keyframe_range`` / ``_audio_exists`` /
  ``_verify_behavior``) → ``bpy.data`` / fcurve queries;
- ``apply_behaviors`` → **fades keyed via blendertk's own** :class:`RenderOpacity`
  (opacity + ``hide_render`` dual-key, the Blender-idiomatic fade) using the pure
  engine's ``resolve_keys`` to place them, and **audio placed as VSE sound strips**
  via :class:`AudioUtils`.

Divergence from mayatk (by design, ledgered):
    * **Fades are opacity/visibility, not Maya's opacity↔visibility dual-attr.**
      The pure ``resolve_keys`` yields the same anchored keyframes; ``RenderOpacity``
      realises them Blender-natively (a driven material-alpha + stepped ``hide_render``).
    * **Audio is VSE sound strips**, not Maya's DG audio-node/compositor subsystem.
      Placement is faithful; audio-clip *auto-sizing* measures an already-placed
      strip (``_measure_audio``) — measuring an unplaced source file headlessly is a
      documented follow-up (the shot uses the placeholder length until the strip lands).
    * **``reapply_object`` is a Blender-side public name with no mayatk twin** —
      mayatk inlines the per-object re-apply in ``table_presenter._reapply_behavior``
      (its ``apply_behavior`` is a free function; Blender's applier needs the adapter's
      RenderOpacity/VSE seams, so the primitive lives here).  One-sided by design.
"""
import logging
from typing import List, Optional, Tuple

from pythontk import ShotManifest
from pythontk.core_utils.engines.shots.manifest.behaviors import (
    load_behavior,
    resolve_keys,
)

from blendertk.anim_utils.shots._shots import iter_action_fcurves, _is_transform_path

_log = logging.getLogger(__name__)


class BlenderShotManifest(ShotManifest):
    """:class:`pythontk.ShotManifest` with the scene hooks bound to Blender."""

    # ---- fps / audio measurement ----------------------------------------

    def _resolve_fps(self) -> float:
        try:
            import bpy
        except ImportError:
            return super()._resolve_fps()
        scene = bpy.context.scene
        if scene is None:
            return super()._resolve_fps()
        base = scene.render.fps_base or 1.0
        return float(scene.render.fps) / float(base)

    def _measure_audio(self, obj) -> Optional[float]:
        """Duration (frames) of an already-placed VSE sound strip named like *obj*.

        Measuring an unplaced source file is deferred; until a strip exists the
        engine's placeholder length sizes the shot (it grows on the next sync).
        """
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils
        except ImportError:
            return None
        name = getattr(obj, "name", "") or ""
        try:
            info = AudioUtils.get_clip(name)
        except Exception:
            info = None
        if not info:
            return None
        dur = info.get("duration")
        return float(dur) if dur else None

    # ---- name / scene resolution ----------------------------------------

    def _resolve_names_keep_missing(self, names) -> List[str]:
        # Blender object names are unique and stored verbatim — identity.
        return list(names)

    @staticmethod
    def _has_transform_key_in_range(obj, start, end) -> bool:
        """True if *obj* has a transform-channel key in ``[start, end]``."""
        for fc in iter_action_fcurves(obj):
            if not _is_transform_path(fc.data_path):
                continue
            if any(start - 1e-6 <= kp.co[0] <= end + 1e-6 for kp in fc.keyframe_points):
                return True
        return False

    def _discover_scene_objects(self, start, end, exclude_names=None) -> List[str]:
        # Discovery: scan the whole scene for animated objects in the range.
        try:
            import bpy
        except ImportError:
            return []
        scene = bpy.context.scene
        if scene is None:
            return []
        exclude = set(exclude_names or ())
        return sorted(
            obj.name
            for obj in scene.objects
            if obj.name not in exclude
            and self._has_transform_key_in_range(obj, start, end)
        )

    def _filter_to_animated(self, names, start, end) -> List[str]:
        # Filter a KNOWN name list — look each up directly (O(len(names)), not O(scene)).
        try:
            import bpy
        except ImportError:
            return list(names)
        out = []
        for n in names:
            obj = bpy.data.objects.get(n)
            if obj is not None and self._has_transform_key_in_range(obj, start, end):
                out.append(n)
        return out

    # ---- assess seams ----------------------------------------------------

    def _object_exists(self, name) -> bool:
        try:
            import bpy
        except ImportError:
            return True
        return name in bpy.data.objects

    def _keyframe_range(self, obj_name) -> Optional[Tuple[float, float]]:
        try:
            import bpy
        except ImportError:
            return None
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            return None
        lo = None
        hi = None
        for fc in iter_action_fcurves(obj):
            for kp in fc.keyframe_points:
                t = kp.co[0]
                lo = t if lo is None else min(lo, t)
                hi = t if hi is None else max(hi, t)
        return (lo, hi) if lo is not None else None

    def _audio_exists(self, name) -> bool:
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils
        except ImportError:
            return False
        try:
            return bool(AudioUtils.get_clip(name))
        except Exception:
            return False

    def _verify_behavior(self, obj, behavior, start, end, anchor_override=None) -> bool:
        """Route on the template's ``verify.mode`` (mirror of mayatk's ``verify_behavior``).

        ``audio_clip`` behaviors live as VSE sound strips — *obj* is a strip name,
        never a ``bpy.data.objects`` entry, so the object-key scan below would
        permanently flag every built audio step as ``missing_behavior``.  A fade is
        verified if the object carries an opacity/visibility key in range (the
        Blender-idiomatic check — ``RenderOpacity`` realises fades as opacity +
        stepped ``hide_render`` keys, not template-exact placements)."""
        try:
            template = load_behavior(behavior)
        except FileNotFoundError:
            template = {}
        if (template.get("verify") or {}).get("mode", "") == "audio_clip":
            return self._audio_exists(obj)
        try:
            import bpy
        except ImportError:
            return True
        o = bpy.data.objects.get(obj)
        if o is None:
            return False
        for fc in iter_action_fcurves(o):
            dp = fc.data_path
            if "opacity" in dp or dp == "hide_render":
                if any(start - 1e-6 <= kp.co[0] <= end + 1e-6 for kp in fc.keyframe_points):
                    return True
        return False

    # ---- behavior application (fades + audio) ---------------------------

    def apply_behaviors(self) -> dict:
        """Key fade behaviors via ``RenderOpacity`` and place audio as VSE strips.

        Reads each shot's ``metadata["behaviors"]`` — the ``(object, behavior)``
        pairs the engine's ``_step_metadata`` recorded — and, for every fade,
        resolves its template block to absolute keyframes (pure ``resolve_keys``)
        then realises them with :meth:`RenderOpacity.key_fade`.  Audio-kind entries
        place their ``source_path`` as a VSE sound strip at the shot start.

        Guards mirror mayatk's ``behaviors.apply_to_shots`` exactly:

        - **locked shots** are user-finalized — never modified (the panel help
          promises this);
        - **zero-duration shots** have no range to key;
        - an object with **existing keys in the shot range** is skipped so a
          rebuild never overwrites user animation ("skipped (already
          keyed/placed)" in the build footer) — this also makes repeated
          Builds idempotent;
        - an **already-placed audio strip** is skipped, not re-added (re-adding
          would stack duplicate overlapping strips on every Build).

        Returns ``{"applied", "skipped", "failed"}`` lists of
        ``{"object", "behavior", "shot"}`` dicts (``failed`` entries add
        ``"error"``) — the same record shape as mayatk, consumed by the build
        footer.  A failing entry is recorded and the batch continues instead of
        aborting the remaining behaviors mid-build.
        """
        applied: list = []
        skipped: list = []
        failed: list = []
        result = {"applied": applied, "skipped": skipped, "failed": failed}
        try:
            import bpy
        except ImportError:
            return result

        from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity

        for shot in self.store.sorted_shots():
            if shot.locked:
                continue  # user-finalized — never modified
            if abs(shot.end - shot.start) < 1e-6:
                continue  # nothing to key over
            for entry in shot.metadata.get("behaviors", []):
                oname = entry.get("name")
                bname = entry.get("behavior")
                kind = entry.get("kind")
                if not oname or not bname:
                    continue
                rec = {"object": oname, "behavior": bname, "shot": shot.name}
                try:
                    if kind == "audio" or entry.get("source_path"):
                        if self._audio_exists(oname):
                            skipped.append(rec)  # strip already placed
                        elif self._place_audio(entry, shot):
                            applied.append(rec)
                        else:
                            skipped.append(rec)  # no source path to place
                        continue
                    obj = bpy.data.objects.get(oname)
                    if obj is None:
                        continue  # missing object — surfaced by assess, not here
                    if self._has_any_key_in_range(obj, shot.start, shot.end):
                        skipped.append(rec)  # existing keys — never overwrite
                        continue
                    if self._key_fade(RenderOpacity, oname, bname, shot):
                        applied.append(rec)
                    else:
                        skipped.append(rec)  # unknown template
                except Exception as exc:
                    _log.warning(
                        "Behavior '%s' on '%s' (shot %s) failed: %s",
                        bname, oname, shot.name, exc,
                    )
                    failed.append(dict(rec, error=str(exc)))

        return result

    @staticmethod
    def _has_any_key_in_range(obj, start, end) -> bool:
        """True if *obj* carries ANY fcurve key in ``[start, end]`` — the Blender
        counterpart of mayatk's ``cmds.keyframe(obj, q=True, time=(start, end))``
        existing-keys guard (any channel counts, matching Maya's semantics)."""
        for fc in iter_action_fcurves(obj):
            if any(start - 1e-6 <= kp.co[0] <= end + 1e-6 for kp in fc.keyframe_points):
                return True
        return False

    def reapply_object(self, shot, obj) -> bool:
        """Re-key every behavior on a single *obj* over *shot*'s range.

        The panel's "Apply [behaviors]" context action; the per-object analogue
        of :meth:`apply_behaviors` (mirror of mayatk's ``_reapply_behavior``
        applier — an explicit user action, so fades re-key without the build's
        existing-keys guard).  Fades are keyed via ``RenderOpacity``; an audio
        object places its VSE strip at most ONCE (not once per behavior name,
        which stacked duplicates) and only when no strip with that name exists.
        Wrapped in a single undo step.  Returns whether anything was applied.
        """
        from blendertk.core_utils._core_utils import undo_chunk

        done = False
        with undo_chunk("ShotManifest_reapply"):
            if getattr(obj, "kind", "scene") == "audio":
                entry = {
                    "name": obj.name,
                    "source_path": getattr(obj, "source_path", ""),
                }
                return self._place_audio(entry, shot)
            from blendertk.mat_utils.render_opacity._render_opacity import (
                RenderOpacity,
            )

            for bname in getattr(obj, "behaviors", []) or []:
                done = self._key_fade(RenderOpacity, obj.name, bname, shot) or done
        return done

    def _key_fade(self, RenderOpacity, oname, bname, shot) -> bool:
        """Key a single fade behavior on *oname* over *shot*'s range. Returns applied?"""
        try:
            import bpy
        except ImportError:
            return False

        obj = bpy.data.objects.get(oname)
        if obj is None:
            return False
        try:
            tmpl = load_behavior(bname)
        except FileNotFoundError:
            return False
        done = False
        for _attr_name, attr_def in tmpl.get("attributes", {}).items():
            for phase in ("in", "out"):
                block = attr_def.get(phase)
                if not block:
                    continue
                keys = resolve_keys(block, shot.start, shot.end)
                if not keys:
                    continue
                lo = min(k["time"] for k in keys)
                hi = max(k["time"] for k in keys)
                # RenderOpacity realises the fade Blender-natively (opacity +
                # stepped hide_render); phase maps to its in/out direction.
                RenderOpacity.key_fade([obj], start=lo, end=hi, direction=phase)
                done = True
        return done

    def _place_audio(self, entry, shot) -> bool:
        """Place an audio entry's source as a VSE sound strip at the shot start.

        Already-placed guard: a strip with the entry's name is left alone —
        ``AudioUtils.add_clip`` always creates (Blender auto-renames collisions
        to ``name.001``), so re-placing on every Build/reapply would stack
        duplicate overlapping strips.
        """
        path = entry.get("source_path")
        if not path:
            return False
        name = entry.get("name") or ""
        if name and self._audio_exists(name):
            return False  # already placed
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils

            AudioUtils.add_clip(path, frame_start=int(shot.start), name=name or None)
            return True
        except Exception:
            _log.debug("audio strip placement failed for %s", entry.get("name"))
            return False

    # ---- from_csv ----------------------------------------------------------

    @classmethod
    def from_csv(cls, filepath, store=None, columns=None, post_process=None):
        """Parse a CSV and return a ready-to-build engine.

        Overrides the engine version so the default store is the **Blender**
        :meth:`BlenderShotStore.active` (auto-installing scene persistence) —
        the inherited default would silently create a separate, persistence-less
        pure-engine store.
        """
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        return super().from_csv(
            filepath,
            store=store or BlenderShotStore.active(),
            columns=columns,
            post_process=post_process,
        )
