# blendertk — API Changes

_Diff vs the last release (origin/main @ 14b7a75)._

## Added (4)

- `anim_utils/shots/shot_sequencer/clip_motion.py::ClipMotionMixin.on_clips_batch_resized(self, resizes) -> None`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.channel_colors(cls, objects=None, channel='highlight') -> dict`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.objects_with_channel(cls, channel='highlight') -> list`
- `mat_utils/render_opacity/render_effects.py::RenderEffects.set_channel_color(cls, objects=None, color=None, channel='highlight') -> list`

## Signature changed (1)

- `rig_utils/tube_rig.py::TubeRig.build`
  - was: `(self, strategy='spline', **opts) -> TubeRigBundle`
  - now: `(self, strategy='spline', progress: Optional[Callable] = None, **opts) -> TubeRigBundle`
