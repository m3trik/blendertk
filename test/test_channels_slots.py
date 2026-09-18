"""Channels panel icon cells — a click toggles that row, a drag SETS every row it crosses.

Needs **Qt, not bpy** (``channels_slots`` imports uitk; the controller is a stand-in), so it runs
under the workspace ``.venv`` like ``test_blender_ui_handler.py``::

    .venv\\Scripts\\python.exe blendertk/test/test_channels_slots.py

Toggling a drag left the rows mixed whenever it crossed rows that started in different states, so
a drag locks / keys the lot, Alt clears and Ctrl breaks — and it reaches the panel once, so the
table refreshes once. Mirror of mayatk's ``TestIconCellDispatch``.
Under the Blender harness (no Qt) it SKIPS with a PASS sentinel.
"""

import os
import sys
import traceback
from types import MethodType, SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

lines = []


def check(name, cond, detail=""):
    lines.append(
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}"
    )


try:
    from qtpy import QtCore
except Exception:
    print("SKIP test_channels_slots (no Qt binding — run under the workspace .venv)")
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

try:
    from blendertk.node_utils.attributes.channels.channels_slots import ChannelsSlots

    Qt = QtCore.Qt
    OBJECTS = ["Cube"]
    LOC_X = {
        "name": "location_x",
        "kind": "transform",
        "data_path": "location",
        "index": 0,
    }

    def stand_in(state, modifiers=Qt.NoModifier):
        """A stand-in panel whose every row is location_x in *state*, with a mock controller."""
        table = mock.MagicMock()
        table.actions.get.return_value = state
        controller = mock.MagicMock()
        controller.get_selected_nodes.return_value = OBJECTS
        slots = SimpleNamespace(
            COL_LOCK=ChannelsSlots.COL_LOCK,
            COL_CONN=ChannelsSlots.COL_CONN,
            ui=SimpleNamespace(tbl000=table),
            controller=controller,
            sb=SimpleNamespace(
                QtCore=QtCore,
                QtWidgets=SimpleNamespace(
                    QApplication=SimpleNamespace(keyboardModifiers=lambda: modifiers)
                ),
            ),
            _descriptor_at=lambda row: LOC_X,
            _refresh_table=mock.Mock(),
        )
        slots._apply_icon_cell = MethodType(ChannelsSlots._apply_icon_cell, slots)
        return slots

    def click(col, state, modifiers=Qt.NoModifier):
        """Click one icon cell; return the stand-in controller."""
        slots = stand_in(state, modifiers)
        ChannelsSlots._on_icon_cell_clicked(slots, 0, col)
        return slots.controller

    # --- Click toggles the row it hit --------------------------------------
    c = click(ChannelsSlots.COL_LOCK, "locked")
    check(
        "lock click on a locked row -> set_lock(False)",
        c.set_lock.call_args == mock.call(OBJECTS, [LOC_X], False)
        and not c.toggle_lock.called,
        str(c.method_calls),
    )
    c = click(ChannelsSlots.COL_LOCK, "unlocked")
    check(
        "lock click on an unlocked row -> set_lock(True)",
        c.set_lock.call_args == mock.call(OBJECTS, [LOC_X], True),
        str(c.method_calls),
    )
    c = click(ChannelsSlots.COL_CONN, "keyframe_active")
    check(
        "key click on a keyed frame -> key removed",
        c.set_key_at_current_time.call_args == mock.call(OBJECTS, LOC_X, keyed=False)
        and not c.toggle_key_at_current_time.called,
        str(c.method_calls),
    )
    c = click(ChannelsSlots.COL_CONN, "none")
    check(
        "key click on an unkeyed row -> key set",
        c.set_key_at_current_time.call_args == mock.call(OBJECTS, LOC_X, keyed=True),
        str(c.method_calls),
    )

    # --- Modifiers stay explicit, click or drag ----------------------------
    c = click(ChannelsSlots.COL_LOCK, "unlocked", Qt.AltModifier)
    check(
        "lock Alt+click -> set_lock(False)",
        c.set_lock.call_args == mock.call(OBJECTS, [LOC_X], False),
        str(c.method_calls),
    )
    c = click(ChannelsSlots.COL_CONN, "keyframe", Qt.AltModifier)
    check(
        "key Alt+click -> set_key_at_current_time(keyed=False)",
        c.set_key_at_current_time.call_args == mock.call(OBJECTS, LOC_X, keyed=False),
        str(c.method_calls),
    )
    c = click(ChannelsSlots.COL_CONN, "keyframe", Qt.ControlModifier)
    check(
        "key Ctrl+click -> break_connections only",
        c.break_connections.called and not c.set_key_at_current_time.called,
        str(c.method_calls),
    )

    # --- A drag sets, whatever each row started as -------------------------
    s = stand_in("locked")
    ChannelsSlots._on_icon_cells_dragged(s, [0, 1, 2], ChannelsSlots.COL_LOCK)
    check(
        "drag over locked rows -> 3x set_lock(True), ONE refresh",
        [c.args[2] for c in s.controller.set_lock.call_args_list] == [True, True, True]
        and s._refresh_table.call_count == 1,
        f"{s.controller.set_lock.call_args_list} refreshes={s._refresh_table.call_count}",
    )

    s = stand_in("keyframe_active")
    ChannelsSlots._on_icon_cells_dragged(s, [0, 1, 2], ChannelsSlots.COL_CONN)
    check(
        "drag over keyed rows -> 3x key set (not removed)",
        [c.kwargs["keyed"] for c in s.controller.set_key_at_current_time.call_args_list]
        == [True, True, True],
        str(s.controller.set_key_at_current_time.call_args_list),
    )

    s = stand_in("keyframe_active", Qt.AltModifier)
    ChannelsSlots._on_icon_cells_dragged(s, [0, 1, 2], ChannelsSlots.COL_CONN)
    check(
        "Alt-drag over 3 rows -> 3 key removals, ONE refresh",
        [c.kwargs["keyed"] for c in s.controller.set_key_at_current_time.call_args_list]
        == [False, False, False]
        and s._refresh_table.call_count == 1,
        f"keys={s.controller.set_key_at_current_time.call_args_list} "
        f"refreshes={s._refresh_table.call_count}",
    )

    s = stand_in("keyframe", Qt.ControlModifier)
    ChannelsSlots._on_icon_cells_dragged(s, [0, 1, 2], ChannelsSlots.COL_CONN)
    check(
        "Ctrl-drag over 3 rows -> 3 animation breaks, ONE refresh",
        s.controller.break_connections.call_count == 3
        and not s.controller.set_key_at_current_time.called
        and s._refresh_table.call_count == 1,
        f"breaks={s.controller.break_connections.call_count} "
        f"refreshes={s._refresh_table.call_count}",
    )

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if lines and all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
