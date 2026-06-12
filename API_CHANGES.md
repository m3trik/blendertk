# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-06-12._

## Added (25)

- `anim_utils/_anim_utils.py::AnimUtils(class)`
- `anim_utils/_anim_utils.py::copy_keys(source)`
- `anim_utils/_anim_utils.py::delete_keys(objects)`
- `anim_utils/_anim_utils.py::fit_playback_range(objects=None)`
- `anim_utils/_anim_utils.py::get_fcurves(objects)`
- `anim_utils/_anim_utils.py::invert_keys(objects)`
- `anim_utils/_anim_utils.py::paste_keys(objects, action)`
- `anim_utils/_anim_utils.py::scale_keys(objects, factor, pivot=None)`
- `anim_utils/_anim_utils.py::set_stepped(objects, stepped=True)`
- `anim_utils/_anim_utils.py::shift_keys(objects, offset)`
- `anim_utils/_anim_utils.py::snap_keys(objects)`
- `anim_utils/_anim_utils.py::stagger_keys(objects, spacing=5)`
- `core_utils/_core_utils.py::get_recent_autosave(filter_time=24, timestamp_format='%H:%M:%S')`
- `core_utils/_core_utils.py::get_recent_files(index=None)`
- `edit_utils/_edit_utils.py::boolean_op(objects, operation='DIFFERENCE', apply=True)`
- `mat_utils/_mat_utils.py::MatUtils(class)`
- `mat_utils/_mat_utils.py::assign_mat(objects, material)`
- `mat_utils/_mat_utils.py::create_mat(mat_type='standard', name='')`
- `mat_utils/_mat_utils.py::find_by_mat_id(material, objects=None)`
- `mat_utils/_mat_utils.py::get_mats(objects)`
- `mat_utils/_mat_utils.py::reload_textures()`
- `mat_utils/_mat_utils.py::select_by_material(material, add=False)`
- `ui_utils/_ui_utils.py::UiUtils(class)`
- `ui_utils/_ui_utils.py::get_editor_types()`
- `ui_utils/_ui_utils.py::open_editor(editor)`
