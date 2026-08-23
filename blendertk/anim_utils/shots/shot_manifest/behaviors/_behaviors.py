# coding=utf-8
"""Behaviors — Blender appliers over the engine's pure keying-recipe core.

Mirror of mayatk's ``shot_manifest.behaviors._behaviors`` (name + behavior).
Template discovery/loading (:func:`load_behavior`, :func:`list_behaviors`,
:func:`templates`), the schema, and the anchor/offset/duration →
absolute-keyframe math (:func:`resolve_keys`) live once, DCC-agnostic, in
``pythontk.core_utils.engines.shots.manifest.behaviors`` (JSON templates,
shared with mayatk).  This module supplies the **scene-touching** half:

- :meth:`Behaviors.apply_behavior` / :meth:`Behaviors.apply_to_shots` key the
  template's keyframes onto ``bpy`` objects.  Maya's ``opacity`` ↔
  ``visibility`` dual-keying maps to :class:`RenderOpacity`'s ``opacity``
  custom property (smooth channel, drives material alpha) mirrored onto a
  stepped ``hide_render`` curve (the native render-visibility channel);
- :meth:`Behaviors.verify_behavior` checks them (``exact`` /
  ``values_in_range`` / ``audio_clip`` modes, same as Maya);
- :meth:`Behaviors.apply_audio_clip` places a **VSE sound strip** at the shot
  start (Maya's start/stop track keys collapse into one strip whose length is
  its own source length — no separate stop key exists);
- :meth:`Behaviors.compute_duration` binds the pure duration math to Blender's
  audio measurement (``aud`` probe of the source file, falling back to a placed
  strip's path).
"""

import inspect
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.manifest.behaviors._behaviors import (
    Behaviors as _PyBehaviors,
)

# Pure-core staticmethods re-exported under their historical flat names;
# blendertk's ``Behaviors`` (below) wraps them with the Blender appliers.
templates = _PyBehaviors.templates  # noqa: F401
load_behavior = _PyBehaviors.load_behavior  # noqa: F401
list_behaviors = _PyBehaviors.list_behaviors  # noqa: F401
resolve_keys = _PyBehaviors.resolve_keys  # noqa: F401
_compute_duration_pure = _PyBehaviors.compute_duration

# Log under the package name, not this private impl module, so the logger name
# stays stable across the __init__ -> _behaviors split (mirror of mayatk).
log = logging.getLogger(__name__.rpartition(".")[0])

# Template tangent → Blender keyframe interpolation.
_INTERP = {"linear": "LINEAR", "step": "CONSTANT", "stepnext": "CONSTANT"}


class _BehaviorsInternal(object):
    """Internal helpers for Behaviors."""

    @staticmethod
    def _track_source_path(name: str) -> str:
        """Resolve an audio entry's source path from an already-placed VSE strip
        (the Blender stand-in for Maya's ``audio_clips`` track registry — a strip
        carries its source independently of the manifest CSV).

        The single lookup shared by :meth:`Behaviors.compute_duration`'s source
        fallback and the manifest adapter's ``_measure_audio`` hook.
        """
        if not name:
            return ""
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils as _AU

            info = _AU.get_clip(name)
            if info:
                return info.get("filepath") or ""
        except Exception as exc:
            log.debug("strip-path fallback failed for '%s': %s", name, exc)
        return ""

    @staticmethod
    def _audio_duration_frames(file_path: str, fps: float) -> Tuple[float, str]:
        """Return ``(duration_in_frames, file_path)`` for *file_path*.

        Mirror of mayatk's ``AudioUtils.audio_duration_frames``: probes the
        source headlessly — Blender's bundled ``aud`` decoder first (any format
        Blender can play), the stdlib ``wave`` reader as the fallback outside
        Blender.  Returns ``(0.0, file_path)`` when no readable audio is found.
        """
        if not file_path or not Path(file_path).is_file():
            return 0.0, file_path
        seconds = 0.0
        try:
            import aud

            snd = aud.Sound.file(file_path)
            rate = float(snd.specs[0]) or 0.0
            seconds = float(snd.length) / rate if rate > 0 else 0.0
        except Exception:
            try:
                import wave

                with wave.open(file_path, "rb") as w:
                    rate = w.getframerate() or 0
                    seconds = w.getnframes() / float(rate) if rate > 0 else 0.0
            except Exception as exc:
                log.debug("audio duration probe failed for '%s': %s", file_path, exc)
                return 0.0, file_path
        return (seconds * float(fps) if seconds > 0 else 0.0), file_path

    @staticmethod
    def _data_path_for(obj, attr: str) -> Tuple[str, bool]:
        """Map a template attribute name to *obj*'s fcurve data path.

        Returns ``(data_path, inverted)``.  ``visibility`` / ``opacity`` target
        the :class:`RenderOpacity` ``opacity`` custom property (where
        :meth:`Behaviors.apply_behavior` places the smooth keys); ``visibility``
        on an object with no opacity property falls back to ``hide_render``,
        whose values are the inverse of Maya's ``visibility``.
        """
        from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity

        if attr in ("visibility", "opacity"):
            if RenderOpacity.ATTR_NAME in obj:
                return f'["{RenderOpacity.ATTR_NAME}"]', False
            return RenderOpacity.VIS_PATH, True
        if hasattr(obj, attr):
            return attr, False
        return f'["{attr}"]', False

    @staticmethod
    def _fcurve_points(obj, data_path: str) -> list:
        """All ``(time, value)`` pairs on *obj*'s fcurve(s) for *data_path*."""
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        pts = []
        for fc in BlenderShotStore.iter_action_fcurves(obj):
            if fc.data_path == data_path:
                pts.extend(
                    (float(kp.co[0]), float(kp.co[1])) for kp in fc.keyframe_points
                )
        return pts

    @staticmethod
    def _verify_values_in_range(
        obj,
        attr: str,
        block: Dict,
        start: float,
        end: float,
    ) -> bool:
        """Check that every expected value exists on *attr* within the range.

        Uses a small epsilon (0.01) for floating-point comparison so that
        values like ``0.999999`` match an expected ``1.0``.
        """
        expected = block.get("values", [])
        if not expected:
            return True
        data_path, inverted = _BehaviorsInternal._data_path_for(obj, attr)
        vals = [
            v
            for t, v in _BehaviorsInternal._fcurve_points(obj, data_path)
            if start - 0.5 <= t <= end + 0.5
        ]
        if not vals:
            return False
        if inverted:
            vals = [1.0 - v for v in vals]
        eps = 0.01
        for ev in expected:
            if not any(abs(v - ev) < eps for v in vals):
                return False
        return True

    @staticmethod
    def _verify_audio_clip(obj: str, start: float, end: float) -> bool:
        """Check that a sound strip named *obj* starts at *start* and ends in range.

        Parameters:
            obj: VSE sound-strip name.
            start: Expected strip start frame.
            end: Shot end frame.

        Returns:
            ``True`` if the strip exists, begins at *start* (±0.5) and ends
            anywhere in ``[start, end]``.  The end is clip-length driven, not
            shot-end driven (the shot grows to fit the clip upstream), so it is
            not pinned to *end* here — mirror of Maya's start/stop-key check.
        """
        try:
            from blendertk.audio_utils._audio_utils import AudioUtils
        except ImportError:
            return False
        try:
            info = AudioUtils.get_clip(obj)
        except Exception:
            info = None
        if not info:
            return False
        s = float(info.get("frame_start", 0))
        e = float(info.get("frame_end", 0))
        return abs(s - start) < 0.5 and (start - 0.5) <= e <= (end + 0.5)

    @staticmethod
    def _binds(fn, *args, **kwargs) -> bool:
        """True when *fn* accepts the given call shape (no call is made).

        Callables without an introspectable signature (some builtins, C
        extensions, mocks) are assumed to accept the full modern contract.
        """
        try:
            inspect.signature(fn).bind(*args, **kwargs)
            return True
        except TypeError:
            return False
        except ValueError:
            return True

    @staticmethod
    def _ensure_opacity(obj) -> None:
        """Seed the :class:`RenderOpacity` ``opacity`` property + material-alpha
        driver on *obj* when absent (Maya: ``OpacityAttributeMode.create``).

        Uses the unguarded setup (not :meth:`RenderOpacity.create`, which wipes
        existing opacity curves first — a second behavior on the same object
        would erase the first's keys).
        """
        from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity

        if RenderOpacity.ATTR_NAME in obj:
            return
        RenderOpacity._ensure_opacity_prop(obj)
        RenderOpacity._refresh_drivers(RenderOpacity._drive_material_alpha(obj))


class Behaviors(_PyBehaviors, _BehaviorsInternal):
    """Behaviors — module namespace.

    Extends the pure engine class (so ``Behaviors.load_behavior`` /
    ``list_behaviors`` / ``resolve_keys`` / ``templates`` resolve through this
    one name) with the Blender appliers; :meth:`compute_duration` overrides the
    pure version with the Blender-bound binding.
    """

    @staticmethod
    def apply_behavior(
        obj: str,
        behavior_name: str,
        start: float,
        end: float,
        attrs: Optional[List[str]] = None,
        search_path: Optional[Path] = None,
        source_path: str = "",
        anchor_override: Optional[str] = None,
    ) -> None:
        """Apply a named behavior template to an object over a time range.

        Templates targeting ``visibility`` or ``opacity`` are dual-keyed the
        Blender-native way: the value lands on :class:`RenderOpacity`'s
        ``opacity`` property (smooth channel, drives material alpha) and is
        mirrored onto ``hide_render`` as a stepped curve (hidden when the value
        is ``<= 0``) so exports carry a real visibility track — the same
        contract as Maya's ``opacity`` + stepped ``visibility`` pair.

        Parameters:
            obj: Blender object name.
            behavior_name: Template stem name (e.g. ``"fade_in"``).
            start: First frame of the range.
            end: Last frame of the range.
            attrs: If given, only key these attributes. Otherwise key all
                attributes defined in the template.
            search_path: Optional custom behaviors directory.
            source_path: Audio file path, forwarded to
                :meth:`apply_audio_clip` for ``audio_clip`` behaviors.
            anchor_override: When provided, overrides the anchor defined
                in the template.  Accepts ``"start"``, ``"end"``, or
                a **float** between 0.0 and 1.0 (0.0 = start, 1.0 = end).
                Used by :meth:`apply_to_shots` to place behaviors based on
                their position in the object's behavior list rather than
                relying on hardcoded template anchors.

        Raises:
            RuntimeError: Blender (``bpy``) unavailable, or *obj* not in the
                scene.
        """
        try:
            import bpy
        except ImportError:
            raise RuntimeError("Blender (bpy) is required to apply behaviors")

        template = load_behavior(behavior_name, search_path)

        # Audio-clip behaviors delegate to the audio-specific helper.
        verify_mode = (template.get("verify") or {}).get("mode", "")
        if verify_mode == "audio_clip":
            Behaviors.apply_audio_clip(obj, start, end, source_path=source_path)
            return

        from blendertk.mat_utils.render_opacity._render_opacity import RenderOpacity

        node = bpy.data.objects.get(str(obj))
        if node is None:
            raise RuntimeError(f"Object '{obj}' not found in the scene")

        # Auto-create the opacity property when the template targets visibility
        # OR opacity so the dual-keying path is always taken (mirror of Maya's
        # OpacityAttributeMode auto-create).
        template_attrs = template.get("attributes", {})
        if "visibility" in template_attrs or "opacity" in template_attrs:
            _BehaviorsInternal._ensure_opacity(node)

        for attr_name, attr_def in template_attrs.items():
            if attrs and attr_name not in attrs:
                continue

            target_path, _inv = _BehaviorsInternal._data_path_for(node, attr_name)
            mirror_to_vis = attr_name in ("visibility", "opacity")

            for phase in ("in", "out"):
                block = attr_def.get(phase)
                if not block:
                    continue

                # Anchor: use override if provided, else the template's, else
                # phase-based default for backward compatibility.
                if anchor_override is not None:
                    block = dict(block, anchor=anchor_override)
                elif "anchor" not in block:
                    block = dict(block, anchor="start" if phase == "in" else "end")

                for k in resolve_keys(block, start, end):
                    interp = _INTERP.get(str(k["tangent"]).lower(), "BEZIER")
                    RenderOpacity._set_key(
                        node, target_path, k["time"], k["value"], interp
                    )
                    # Mirror: stepped render-visibility key so exports carry a
                    # real visibility curve (hide_render is the inverse).
                    if mirror_to_vis:
                        RenderOpacity._set_key(
                            node,
                            RenderOpacity.VIS_PATH,
                            k["time"],
                            0.0 if k["value"] > 0 else 1.0,
                            "CONSTANT",
                        )

    @staticmethod
    def verify_behavior(
        obj: str,
        behavior_name: str,
        start: float,
        end: float,
        search_path: Optional[Path] = None,
        keyframe_fn: Optional[Any] = None,
        anchor_override: Optional[Any] = None,
    ) -> bool:
        """Check whether expected behavior keyframes exist on an object.

        The verification strategy is controlled by the template's optional
        ``verify.mode`` key:

        ``"exact"`` (default)
            Every keyframe must exist at the exact time computed from the
            template offsets/durations.
        ``"values_in_range"``
            Every expected *value* must appear on at least one keyframe
            somewhere within the shot range.  Timing is ignored, so
            user-repositioned keys still pass.
        ``"audio_clip"``
            A VSE sound strip named *obj* starts at *start* (see
            :meth:`_verify_audio_clip`).

        Parameters:
            obj: Blender object name (or strip name for ``audio_clip``).
            behavior_name: Template stem name (e.g. ``"fade_in"``).
            start: First frame of the scene range.
            end: Last frame of the scene range.
            search_path: Optional custom behaviors directory.
            keyframe_fn: Callable ``(obj, attribute, time) -> list``.
                Defaults to the object's fcurve keys at that exact frame.
                Only used for ``exact`` mode.
            anchor_override: Same semantics as :meth:`apply_behavior` —
                when the keys were placed with a distributed anchor
                (multi-behavior objects), ``exact`` verification must model
                the same anchor or it checks the template's default
                positions and permanently flags the object as broken.

        Returns:
            ``True`` if every expected keyframe is found.
        """
        template = load_behavior(behavior_name, search_path)
        verify_mode = (template.get("verify") or {}).get("mode", "exact")

        # Audio clip verification — strip exists at the shot start.
        if verify_mode == "audio_clip":
            return _BehaviorsInternal._verify_audio_clip(obj, start, end)

        try:
            import bpy
        except ImportError:
            raise RuntimeError("Blender is required to verify behaviors")

        # No object in the scene → no keys → cannot verify.
        node = bpy.data.objects.get(str(obj))
        if node is None:
            return False

        if keyframe_fn is None:

            def keyframe_fn(o, attr, t):
                data_path, _inv = _BehaviorsInternal._data_path_for(o, attr)
                return [
                    (kt, kv)
                    for kt, kv in _BehaviorsInternal._fcurve_points(o, data_path)
                    if abs(kt - t) < 0.5
                ]

        for attr_name, attr_def in template.get("attributes", {}).items():
            for phase in ("in", "out"):
                block = attr_def.get(phase)
                if not block:
                    continue

                if verify_mode == "values_in_range":
                    if not _BehaviorsInternal._verify_values_in_range(
                        node, attr_name, block, start, end
                    ):
                        return False
                else:
                    # Mirror apply_behavior's anchor precedence exactly:
                    # override > template > phase-based default.
                    if anchor_override is not None:
                        block = dict(block, anchor=anchor_override)
                    elif "anchor" not in block:
                        block = dict(block, anchor="start" if phase == "in" else "end")
                    for k in resolve_keys(block, start, end):
                        if not keyframe_fn(node, attr_name, k["time"]):
                            return False
        return True

    @staticmethod
    def apply_audio_clip(
        obj: str,
        start: float,
        end: float,
        source_path: str = "",
    ) -> None:
        """Place (or re-place) the sound strip *obj* at *start*.

        Maya writes an on-key at *start* and an off-key at the clip's natural
        end; a VSE strip already carries both (its position and its source
        length), so this collapses into one placement.  Idempotent: an existing
        strip is moved so it always begins at the current shot start; a new
        one is created from *source_path*.

        Parameters:
            obj: Sound-strip name.
            start: Shot start frame.
            end: Shot end frame — only validated against *start* (the strip's
                own length sets its end: keys drive shot size, grow-only, via
                the engine's plan; not the other way around).
            source_path: Path to the audio file (used when creating a new
                strip).  Ignored when the strip already exists.
        """
        from blendertk.audio_utils._audio_utils import AudioUtils

        if end <= start:
            log.warning(
                "apply_audio_clip: non-positive range for '%s' (start=%s end=%s) "
                "— skipping.",
                obj,
                start,
                end,
            )
            return

        if AudioUtils.get_clip(obj):
            AudioUtils.move_clip(obj, int(start))
            return
        if not source_path:
            log.warning(
                "Audio strip '%s' not found and no source_path — cannot create.", obj
            )
            return
        AudioUtils.add_clip(source_path, frame_start=int(start), name=obj or None)

    @staticmethod
    def compute_duration(
        behavior_entries: List[Dict[str, str]],
        fallback: float = 30,
        fps: Optional[float] = None,
    ) -> float:
        """Derive duration from the behavior templates in *behavior_entries*.

        Blender-bound facade over the engine's pure
        :func:`~pythontk.core_utils.engines.shots.manifest.behaviors.compute_duration`:
        injects Blender's audio measurement (``from_source`` templates probe the
        entry's ``source_path`` against the scene FPS) and the placed-strip
        fallback (an audio entry with no ``source_path`` may still resolve a
        path via its VSE strip).

        Parameters:
            behavior_entries: List of dicts with a ``"behavior"`` key, or
                ``BuilderObject``-like objects with a ``.behaviors`` list
                and optional ``.kind`` / ``.source_path`` attributes.
            fallback: Duration when no behavior-driven duration exists.
            fps: Scene frame-rate used to resolve ``from_source`` audio
                durations.  Queried from Blender when omitted.

        Returns:
            Duration in frames.
        """
        # Resolved lazily on the first from_source probe so a template-only
        # manifest never touches the scene.
        state = {"fps": fps}

        def _audio_duration_fn(source_path: str) -> Optional[float]:
            if state["fps"] is None:
                from blendertk.anim_utils.shots.shot_manifest._shot_manifest import (
                    BlenderShotManifest,
                )

                state["fps"] = BlenderShotManifest._scene_fps()
            dur_frames, _ = _BehaviorsInternal._audio_duration_frames(
                source_path, state["fps"]
            )
            return dur_frames

        def _resolve_source_fn(name: str, kind: str) -> Optional[str]:
            # Audio-kind entries only: a manifest entry with no source_path may
            # still resolve a path via its placed strip (see _track_source_path).
            if kind != "audio":
                return None
            return _BehaviorsInternal._track_source_path(name) or None

        return _compute_duration_pure(
            behavior_entries,
            fallback=fallback,
            fps=fps,
            audio_duration_fn=_audio_duration_fn,
            resolve_source_fn=_resolve_source_fn,
        )

    @staticmethod
    def apply_to_shots(
        shots: list,
        apply_fn,
        exists_fn=None,
        has_keys_fn=None,
        store=None,
    ) -> Dict[str, list]:
        """Apply declared behaviors from shot metadata to Blender objects.

        Reads ``metadata["behaviors"]`` from each shot and applies keyframe
        patterns via *apply_fn*.  Objects with existing keyframes in the
        shot range are skipped to avoid overwriting user animation; locked
        and zero-duration shots are never touched.

        Audio-grow (expanding shot.end to fit audio clips and rippling
        downstream shots) is handled upstream by
        ``ShotManifest._compute_plan`` / ``_execute_plan``.  By the time
        this function runs, ``shot.start`` / ``shot.end`` are already at
        their final positions.

        Processing uses a **two-pass-per-shot** design:

        1. **Audio pass** — audio entries are applied first so their strips
           exist before non-audio anchors are computed.
        2. **Non-audio pass** — fade and other behavior entries are applied
           using the finalized ``shot.start`` / ``shot.end``.  Positional
           anchors are computed here.

        Parameters:
            shots: :class:`ShotBlock` instances to process.
            apply_fn: Callable ``(obj, behavior, start, end)`` that applies
                a behavior template.  May optionally accept ``source_path``
                and/or ``anchor_override`` keywords — support is detected
                once via signature introspection and the richest supported
                form is used.
            exists_fn: Callable ``(name) -> bool`` that checks whether an
                object exists in the scene.  Defaults to ``bpy.data.objects``
                (audio entries: a placed strip, or a ``source_path`` to place).
            has_keys_fn: Callable ``(obj, start, end) -> bool``.  Defaults
                to checking fcurve keys in range (audio: strip placed at
                the shot start).
            store: Accepted for signature parity with mayatk; unused.

        Returns:
            Dict with ``"applied"``, ``"skipped"``, and ``"failed"`` lists
            of dicts containing ``object``, ``behavior``, and ``shot`` keys
            (``failed`` entries also carry ``error``).  A failing entry —
            e.g. keying a library-linked object — is recorded and the batch
            continues instead of aborting the remaining behaviors mid-build.
        """
        try:
            import bpy
        except ImportError:
            bpy = None  # type: ignore[assignment]

        def _is_audio(entry):
            return (entry.get("kind") == "audio") or bool(entry.get("source_path"))

        def _default_exists(obj_name, entry=None):
            if entry is not None and _is_audio(entry):
                try:
                    from blendertk.audio_utils._audio_utils import AudioUtils

                    if AudioUtils.get_clip(obj_name):
                        return True
                except Exception:
                    pass
                # New audio with a source_path counts as "buildable".
                if entry.get("source_path"):
                    return True
            if bpy is None:
                return False
            return obj_name in bpy.data.objects

        if exists_fn is None:
            exists_fn = _default_exists

        def _default_has_keys(obj_name, start, end, entry=None):
            if entry is not None and _is_audio(entry):
                return _BehaviorsInternal._verify_audio_clip(obj_name, start, end)
            if bpy is None:
                return False
            node = bpy.data.objects.get(obj_name)
            if node is None:
                return False
            from blendertk.anim_utils.shots._shots import BlenderShotStore

            for fc in BlenderShotStore.iter_action_fcurves(node):
                if any(
                    start - 1e-6 <= kp.co[0] <= end + 1e-6 for kp in fc.keyframe_points
                ):
                    return True
            return False

        if has_keys_fn is None:
            has_keys_fn = _default_has_keys

        # Adapters so callers that pass their own fns (old 3-arg signature)
        # still work, while default fns may use the entry for audio dispatch.
        # Signature support is probed once via ``inspect`` binding — calling
        # inside ``except TypeError`` would conflate "wrong signature" with
        # genuine TypeErrors raised *inside* the callable.
        exists_takes_entry = _BehaviorsInternal._binds(exists_fn, "", None)
        has_keys_takes_entry = _BehaviorsInternal._binds(
            has_keys_fn, "", 0.0, 0.0, None
        )
        apply_takes_anchor = _BehaviorsInternal._binds(
            apply_fn, "", "", 0.0, 0.0, source_path="", anchor_override=0.0
        )
        apply_takes_source = _BehaviorsInternal._binds(
            apply_fn, "", "", 0.0, 0.0, source_path=""
        )

        def _call_exists(obj_name, entry):
            if exists_takes_entry:
                return exists_fn(obj_name, entry)
            return exists_fn(obj_name)

        def _call_has_keys(obj_name, start, end, entry):
            if has_keys_takes_entry:
                return has_keys_fn(obj_name, start, end, entry)
            return has_keys_fn(obj_name, start, end)

        def _call_apply(obj_name, behavior, shot, source_path, anchor):
            if anchor is not None and apply_takes_anchor:
                apply_fn(
                    obj_name,
                    behavior,
                    shot.start,
                    shot.end,
                    source_path=source_path,
                    anchor_override=anchor,
                )
            elif apply_takes_source:
                apply_fn(
                    obj_name, behavior, shot.start, shot.end, source_path=source_path
                )
            else:
                apply_fn(obj_name, behavior, shot.start, shot.end)

        applied: list = []
        skipped: list = []
        failed: list = []

        def _record_failure(obj_name, behavior, shot, exc):
            log.warning(
                "Behavior '%s' on '%s' (shot %s) failed: %s",
                behavior,
                obj_name,
                shot.name,
                exc,
            )
            failed.append(
                {
                    "object": obj_name,
                    "behavior": behavior,
                    "shot": shot.name,
                    "error": str(exc),
                }
            )

        for shot in shots:
            if shot.locked:
                continue  # user-finalized — never modified
            if abs(shot.end - shot.start) < 1e-6:
                continue  # nothing to key over

            entries = shot.metadata.get("behaviors", [])

            # Pass 1 — audio entries first so their strips exist before the
            # non-audio behaviors compute positional anchors.
            for entry in entries:
                obj_name = entry.get("name", "")
                behavior = entry.get("behavior", "")
                if not behavior or not obj_name or not _is_audio(entry):
                    continue
                if not _call_exists(obj_name, entry):
                    continue
                rec = {"object": obj_name, "behavior": behavior, "shot": shot.name}
                if _call_has_keys(obj_name, shot.start, shot.end, entry):
                    skipped.append(rec)  # strip already placed
                    continue
                try:
                    _call_apply(
                        obj_name, behavior, shot, entry.get("source_path") or "", 0.0
                    )
                except Exception as exc:
                    _record_failure(obj_name, behavior, shot, exc)
                    continue
                applied.append(rec)

            # Pass 2 — non-audio entries over the finalized shot range.
            non_audio = [e for e in entries if not _is_audio(e)]
            obj_indices: Dict[str, int] = {}  # obj_name → count seen so far
            obj_counts: Dict[str, int] = {}  # obj_name → total behaviors
            # The existing-keys guard is evaluated ONCE per object, before any
            # of its behaviors are applied: the first applied behavior keys the
            # object, and a per-entry re-check would then skip its remaining
            # behaviors (a "fade_in, fade_out" object would only ever get the
            # fade_in).
            obj_keyed: Dict[str, bool] = {}
            for entry in non_audio:
                n = entry.get("name", "")
                if n:
                    obj_counts[n] = obj_counts.get(n, 0) + 1
                    if n not in obj_keyed:
                        obj_keyed[n] = _call_has_keys(n, shot.start, shot.end, entry)

            for entry in non_audio:
                obj_name = entry.get("name", "")
                behavior = entry.get("behavior", "")
                if not behavior or not obj_name:
                    continue
                if not _call_exists(obj_name, entry):
                    continue  # missing object — surfaced by assess, not here
                rec = {"object": obj_name, "behavior": behavior, "shot": shot.name}
                if obj_keyed.get(obj_name):
                    skipped.append(rec)  # existing keys — never overwrite
                    continue

                # Positional anchor: distribute evenly across the shot when
                # an object carries 2+ behaviors (2 → 0.0, 1.0; 3 → 0.0,
                # 0.5, 1.0; N → idx / max(total-1, 1)).  A single behavior
                # keeps its template anchor (e.g. ``anchor: end`` for
                # fade_out).
                idx = obj_indices.get(obj_name, 0)
                obj_indices[obj_name] = idx + 1
                total = obj_counts.get(obj_name, 1)
                anchor = idx / max(total - 1, 1) if total > 1 else None
                try:
                    _call_apply(
                        obj_name, behavior, shot, entry.get("source_path") or "", anchor
                    )
                except Exception as exc:
                    _record_failure(obj_name, behavior, shot, exc)
                    continue
                applied.append(rec)

        return {"applied": applied, "skipped": skipped, "failed": failed}
