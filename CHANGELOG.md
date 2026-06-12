# Changelog

## [Unreleased] — 2026-06-12 (post-completion stub deepening)

- `anim_utils`: + `move_keys_to_frame(objects, frame=None, retain_spacing=True)` — mirror of
  `mtk.move_keys_to_frame` (backs the shared `animation tb006` Move Keys): one global offset
  lands the selection's earliest key on the target frame preserving relative timing, or
  per-action first-key alignment. Returns the number of actions moved. Shift mechanics
  extracted to `_shift_fcurves` (now shared by `shift_keys`/`stagger_keys`/
  `move_keys_to_frame`). Headless suite extended to 20/20.

## [Unreleased] — 2026-06-12 (Phase-4 completion)

The full tentacle Blender slot surface is now backed — blendertk = 9 util modules:

- `ui_utils`: `open_editor` / `get_editor_types` (new-window `Area.ui_type` switch — the
  Blender analogue of Maya's editor windows; 22 editors).
- `mat_utils`: `get_mats`, `create_mat`, `assign_mat`, `find_by_mat_id`,
  `select_by_material`, `reload_textures` (datablock-level; no shading engines).
- `anim_utils`: `get_fcurves`, `shift/invert/stagger/snap/scale_keys`, `set_stepped`,
  `delete_keys`, `fit_playback_range`, `copy/paste_keys`. **Slot-aware:** Blender 5.x drops
  the legacy `Action.fcurves` (slotted/layered actions) — fcurves resolve via
  `layers → strips → channelbag(slot)` with a legacy fallback; paste assigns the copied
  action and its first slot.
- `edit_utils.boolean_op`: Boolean-modifier orchestration (base = first object).
- `core_utils`: `get_recent_files` (Blender's recent-files.txt) and `get_recent_autosave`
  (temp-dir scan), mirroring the mayatk names.
- New headless suite `test_mat_anim_utils.py` (16 checks); edit_utils suite extended.

## [Unreleased] — 2026-06-12 (review fixes)

Full-port review fixes (regression cases added to `test/test_edit_utils.py`):

- `edit_utils.clean_geometry`: new `merge=True` flag decouples the doubles merge from the
  degenerate dissolve — previously a caller disabling merge by passing `merge_distance=0`
  silently disabled `degenerate` too (its `dist` was the same value). The degenerate threshold
  is now floored (`max(merge_distance, 1e-6)`) so exact-zero geometry dissolves even with
  merging off.
- `core_utils._object_mode`: the mode restore now re-activates the **caller's** active object
  first — helpers select/activate their own targets, and `mode_set` acts on the active object,
  so e.g. invoking `decimate(B)` while editing `A` previously put `B` into edit mode on exit.
  Guards `ReferenceError` for the case where the helper deleted the original active.

## [Unreleased] — 2026-06-12

Util modules added to back the tentacle Blender slot ports (all headless-tested via
`blender --background`):

- `xform_utils`: `freeze_transforms`, `drop_to_grid`, `center_pivot`, `get_pivot_modes`,
  `match_scale`, `move_to`, `get_world_bbox`.
- `node_utils`: `replace_with_instances`, `get_instances`, `uninstance` (linked-data instancing;
  counts object users, not `data.users`, to ignore fake users).
- `cam_utils`: `adjust_camera_clipping` (auto from scene bbox / reset / explicit).
- `edit_utils`: `decimate`, `dissolve_coplanar`, `triangulate`, `tris_to_quads`, `subdivide_mesh`,
  `set_subdivision`, `set_shading`, `set_edge_hardness`, `flip_normals`, `recalculate_normals`,
  `crease_edges` (5.1: crease layer is `bm.edges.layers.float["crease_edge"]`), `clean_geometry`.
- `uv_utils`: `move_uvs`, `delete_extra_uv_sets`.
- `core_utils`: shared `_object_mode` guard (promoted here; used by xform + edit utils);
  `get_env_info` gained `workspace` / `workspace_dir` (the saved `.blend` directory).

## [0.1.0] — 2026-06-11

Initial scaffold.

- `bootstrap_package` wiring mirroring mayatk; public surface registered via `DEFAULT_INCLUDE`.
- `core_utils`: `undoable` (single Blender undo step) + `get_env_info` (scene/env info),
  exposed module-level and on `CoreUtils` (`btk.undoable` / `btk.get_env_info`).
- `pyproject.toml`, `CLAUDE.md`, headless `blender --background` smoke test.
