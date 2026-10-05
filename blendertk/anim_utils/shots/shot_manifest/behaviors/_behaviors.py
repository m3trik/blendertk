# coding=utf-8
"""Behaviors — Blender appliers over the engine's pure keying-recipe core.

Mirror of mayatk's ``shot_manifest.behaviors._behaviors`` (name + behavior).
Template discovery/loading (``Behaviors.load_behavior`` /
``list_behaviors`` / ``templates``), the schema, and the anchor/offset/duration
→ absolute-keyframe math (``Behaviors.resolve_keys``) live once, DCC-agnostic,
in ``pythontk.core_utils.engines.shots.manifest.behaviors`` (JSON templates,
shared with mayatk); :class:`Behaviors` extends that engine class.  This module
supplies the **scene-touching** half:

- :meth:`Behaviors.apply_behavior` keys a template onto ``bpy`` objects -- an
  ``effect`` template (the built-in fades and highlight) through
  ``RenderEffects.apply_effect`` from the scene's effect recipe, the keys the
  Render Effects panel writes by hand; :meth:`Behaviors.apply_to_shots` binds
  the engine's build loop to Blender's checks.  Maya's ``opacity`` ↔
  ``visibility`` dual-keying maps to :class:`RenderEffects`'s ``opacity``
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

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.manifest.behaviors import (  # noqa: F401
    Behaviors as _PyBehaviors,
    BehaviorSpec,  # published by this package's __init__
)

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
        the :class:`RenderEffects` ``opacity`` custom property (where
        :meth:`Behaviors.apply_behavior` places the smooth keys); ``visibility``
        on an object with no opacity property falls back to ``hide_render``,
        whose values are the inverse of Maya's ``visibility``.
        """
        from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

        if attr in ("visibility", "opacity"):
            if RenderEffects.ATTR_NAME in obj:
                return f'["{RenderEffects.ATTR_NAME}"]', False
            return RenderEffects.VIS_PATH, True
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
    def _ensure_opacity(obj) -> None:
        """Seed the :class:`RenderEffects` ``opacity`` property on *obj* when
        absent (Maya: ``OpacityAttributeMode.create``).

        Uses the unguarded setup (not :meth:`RenderEffects.create`, which wipes
        existing opacity curves first — a second behavior on the same object
        would erase the first's keys).  The prop is the whole channel: the
        material driver this used to wire was the viewport preview retired
        2026-09-05, and calling it raised on every manifest apply since.
        """
        from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

        RenderEffects._ensure_opacity_prop(obj)

    @staticmethod
    def _behavior_paths(obj, behavior_name: str) -> List[str]:
        """The fcurve data paths *behavior_name* keys on *obj*: its template's
        channels as :meth:`Behaviors.apply_behavior` targets them, plus the
        stepped ``hide_render`` mirror a presence channel brings.  ``[]`` when
        the template does not exist -- the manifest's key-ownership reads
        (``BlenderShotManifest._key_samples``)."""
        from blendertk.mat_utils.render_opacity.render_effects import RenderEffects

        try:
            attrs = Behaviors.keyed(behavior_name).get("attributes") or {}
        except (FileNotFoundError, ValueError):
            return []
        paths = {_BehaviorsInternal._data_path_for(obj, a)[0] for a in attrs}
        if set(attrs) & {"visibility", "opacity"}:
            paths.add(RenderEffects.VIS_PATH)
        return sorted(paths)


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
        recipe: Optional[Any] = None,
        fps: Optional[float] = None,
    ) -> List[Tuple[str, float]]:
        """Apply a named behavior template to an object over a time range.

        Returns ``(curve_key, time)`` for every key it set, the curve named
        by :meth:`BlenderShotStore.curve_key` -- what the manifest records as
        its own (``ShotEditLedger``), so a re-apply can replace exactly those.
        An audio clip returns ``[]`` (its strip belongs to the audio track).

        A template naming an ``effect`` is keyed by
        ``RenderEffects.apply_effect`` from *recipe* (the scene's effect recipe
        when omitted), placed by the template's ``place`` and
        *anchor_override* -- mirror of mayatk's.

        Templates targeting ``visibility`` or ``opacity`` are dual-keyed the
        Blender-native way: the value lands on :class:`RenderEffects`'s
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
            recipe: The scene's ``ptk.EffectRecipe``; queried when omitted.
            fps: The scene's rate; queried when omitted.

        Raises:
            RuntimeError: Blender (``bpy``) unavailable, or *obj* not in the
                scene.
        """
        try:
            import bpy
        except ImportError:
            raise RuntimeError("Blender (bpy) is required to apply behaviors")

        template = Behaviors.load_behavior(behavior_name, search_path)

        # Audio-clip behaviors delegate to the audio-specific helper.
        verify_mode = (template.get("verify") or {}).get("mode", "")
        if verify_mode == "audio_clip":
            Behaviors.apply_audio_clip(obj, start, end, source_path=source_path)
            return []

        from blendertk.mat_utils.render_opacity.render_effects import RenderEffects
        from blendertk.anim_utils.shots._shots import BlenderShotStore

        effect = Behaviors.effect_of(template)
        if effect is not None:
            return RenderEffects.apply_effect(
                str(obj),
                effect,
                start,
                end,
                recipe=recipe,
                fps=fps,
                place=Behaviors.place_of(template),
                anchor=anchor_override,
            )

        written: List[Tuple[str, float]] = []

        node = bpy.data.objects.get(str(obj))
        if node is None:
            raise RuntimeError(f"Object '{obj}' not found in the scene")

        # Auto-create the opacity property when the template targets visibility
        # OR opacity so the dual-keying path is always taken (mirror of Maya's
        # OpacityAttributeMode auto-create).
        template_attrs = template.get("attributes", {})
        if "visibility" in template_attrs or "opacity" in template_attrs:
            _BehaviorsInternal._ensure_opacity(node)
        if RenderEffects.HIGHLIGHT_ATTR in template_attrs:
            RenderEffects._ensure_highlight_props(node)

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

                for k in Behaviors.resolve_keys(block, start, end):
                    interp = _INTERP.get(str(k["tangent"]).lower(), "BEZIER")
                    RenderEffects._set_key(
                        node, target_path, k["time"], k["value"], interp
                    )
                    written.append(
                        (BlenderShotStore.curve_key(node.name, target_path), k["time"])
                    )
                    # Mirror: stepped render-visibility key so exports carry a
                    # real visibility curve (hide_render is the inverse).
                    if mirror_to_vis:
                        RenderEffects._set_key(
                            node,
                            RenderEffects.VIS_PATH,
                            k["time"],
                            0.0 if k["value"] > 0 else 1.0,
                            "CONSTANT",
                        )
                        written.append(
                            (
                                BlenderShotStore.curve_key(
                                    node.name, RenderEffects.VIS_PATH
                                ),
                                k["time"],
                            )
                        )
        return written

    @staticmethod
    def verify_behavior(
        obj: str,
        behavior_name: str,
        start: float,
        end: float,
        search_path: Optional[Path] = None,
        keyframe_fn: Optional[Any] = None,
        anchor_override: Optional[Any] = None,
        recipe: Optional[Any] = None,
        fps: Optional[float] = None,
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
            recipe: The scene's effect recipe -- an ``effect`` template is
                checked as :meth:`Behaviors.keyed` states it under *recipe*.
            fps: The scene's rate, for an effect's lengths.

        Returns:
            ``True`` if every expected keyframe is found.
        """
        template = Behaviors.keyed(
            Behaviors.load_behavior(behavior_name, search_path), recipe, fps
        )
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
                    for k in Behaviors.resolve_keys(block, start, end):
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

        return _PyBehaviors.compute_duration(
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
        resolve_fn=None,
        conflict_fn=None,
        release_fn=None,
    ) -> Dict[str, list]:
        """Apply declared behaviors from shot metadata to Blender objects.

        The engine's build loop (``ptk`` ``Behaviors.apply_to_shots`` -- two
        passes per shot, guards settled before anything is keyed, every
        previous key released first) bound to Blender's checks: *exists_fn*
        defaults to ``bpy.data.objects`` (an audio entry: a placed strip, or a
        ``source_path`` to place) and *has_keys_fn* to fcurve keys in the range
        (an audio entry: its strip placed at the shot start).  The other
        parameters and the result are the engine's (mirror of mayatk's).
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

        return _PyBehaviors.apply_to_shots(
            shots,
            apply_fn,
            exists_fn=exists_fn if exists_fn is not None else _default_exists,
            has_keys_fn=has_keys_fn if has_keys_fn is not None else _default_has_keys,
            store=store,
            resolve_fn=resolve_fn,
            conflict_fn=conflict_fn,
            release_fn=release_fn,
        )
