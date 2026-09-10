# blendertk — API Changes

_Diff vs the last release (origin/main @ 9397cfc)._

## Added (3)

- `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py::ShotSequencerController.move_shot_to_position(self, shot_id: int, position: int) -> None`
- `anim_utils/shots/shots_slots.py::ShotsController.on_shift_all_shots(self, start: float) -> None`
- `anim_utils/shots/shots_slots.py::ShotsSlots.btn_shift_all(self)`

## Signature changed (6)

- `anim_utils/key_stash/_key_stash.py::KeyStash.stash`
  - was: `(self, objects=None, time_range: Optional[Tuple[float, float]] = None, selected_keys: bool = False, attributes: Optional[Sequence[str]] = None, fcurves=None, label: Optional[str] = None, source_shot_id: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[StashedClip]`
  - now: `(self, objects=None, time_range: Optional[Tuple[float, float]] = None, selected_keys: bool = False, attributes: Optional[Sequence[str]] = None, fcurves=None, label: Optional[str] = None, source_shot_id: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None, targets: Optional[Sequence[Tuple[str, Any, float, float]]] = None) -> Optional[StashedClip]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.apply_gap`
  - was: `(self, gap: float, scope: str = 'all', shot_id: Optional[int] = None) -> bool`
  - now: `(self, gap: float, scope: str = 'all', shot_id: Optional[int] = None, respect_locks: bool = True) -> bool`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.extend_shot_to_fit`
  - was: `(self, shot_id: int) -> Tuple[float, float]`
  - now: `(self, shot_id: int, edge: str = 'both', reach: Optional[float] = None) -> Tuple[float, float]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.fit_shot_to_content`
  - was: `(self, shot_id: int, mode: str = 'fit', edge: str = 'both') -> Tuple[float, float]`
  - now: `(self, shot_id: int, mode: str = 'fit', edge: str = 'both', reach: Optional[float] = None) -> Tuple[float, float]`
- `anim_utils/shots/shot_sequencer/_shot_sequencer.py::ShotSequencer.respace`
  - was: `(self, gap: float = 0, start_frame: float = 1) -> None`
  - now: `(self, gap: float = 0, start_frame: float = 1, respect_locks: bool = True) -> None`
- `anim_utils/shots/shots_slots.py::ShotsController.on_gap_changed`
  - was: `(self, value, scope: str = 'all') -> None`
  - now: `(self, value, scope: str = 'all', respect_locks: bool = True) -> None`
