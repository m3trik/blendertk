"""Channels panel icon cells — a click toggles that row, a drag SETS every row it crosses.

Needs **Qt, not bpy** (``channels_slots`` imports uitk; the controller is a stand-in), so it runs
under the workspace ``.venv`` like ``test_blender_ui_handler.py``::

    .venv\\Scripts\\python.exe blendertk/test/test_channels_slots.py

Toggling a drag left the rows mixed whenever it crossed rows that started in different states, so
a drag locks / keys the lot, Alt clears and Ctrl breaks — and it reaches the panel once, so the
table refreshes once. Mirror of mayatk's ``TestIconCellDispatch``. Also pins the
``keyframe_changed`` state (its colour, a click keys it) and one undo bracket per gesture
(a drag-fill, a scrub, a wheel notch, an icon drag) -- the real undo stack is exercised by
``test_channels.py`` under Blender.
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
        f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + str(detail)) if detail else ''}"
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

    # --- keyframe_changed: its own colour, and a click keys it --------------
    # Mirror of mayatk's ``keyframe_changed`` state (lighter red; the value was
    # moved off its curve and the next frame change discards it).
    table = mock.MagicMock()
    owner = SimpleNamespace(
        ACTION_COLOR_MAP=ChannelsSlots.ACTION_COLOR_MAP,
        COL_LOCK=ChannelsSlots.COL_LOCK,
        COL_CONN=ChannelsSlots.COL_CONN,
        _on_icon_cell_clicked=mock.Mock(name="click"),
        _on_icon_cells_dragged=mock.Mock(name="drag"),
    )
    ChannelsSlots._setup_action_columns(owner, table)
    conn = next(
        (
            c.kwargs["states"]
            for c in table.actions.add.call_args_list
            if c.args and c.args[0] == ChannelsSlots.COL_CONN
        ),
        {},
    )
    changed_state = conn.get("keyframe_changed", {})
    check(
        "the Key column registers keyframe_changed in its own colour, click-wired",
        changed_state.get("color") == "#f08080"
        and changed_state.get("color") != ChannelsSlots.ACTION_COLOR_MAP["keyframe"]
        and changed_state.get("action") is owner._on_icon_cell_clicked,
        f"states={sorted(conn)} changed={changed_state}",
    )
    c = click(ChannelsSlots.COL_CONN, "keyframe_changed")
    check(
        "key click on a changed value -> key set (keeps the moved value)",
        c.set_key_at_current_time.call_args == mock.call(OBJECTS, LOC_X, keyed=True),
        str(c.method_calls),
    )

    # --- One undo bracket per gesture (Blender's undo_chunk), none per cell --
    # The panel's writes push no undo step of their own from the Qt pump; a
    # gesture that writes several values opens ONE bracket so nested steps fold
    # into it. The real undo stack is exercised in test_channels.py (Blender).
    import contextlib

    from blendertk.core_utils._core_utils import CoreUtils

    events = []

    @contextlib.contextmanager
    def spy_chunk(name=""):
        handle = SimpleNamespace(cancel=lambda: events.append("cancel"))
        events.append(("open", name))
        try:
            yield handle
        finally:
            events.append(("close", name))

    def live_stand_in(objects=("Cube", "Sphere")):
        """A ChannelsSlots instance (no __init__) whose engine records each write."""
        s = ChannelsSlots.__new__(ChannelsSlots)
        s.controller = mock.MagicMock()
        s.controller.get_selected_nodes.return_value = list(objects)
        s.controller.is_locked.return_value = False
        s.controller.get_channel_value.return_value = 1.0
        s.controller.set_channel_value.side_effect = lambda *a: events.append("write")
        s.controller.set_lock.side_effect = lambda *a: events.append("write")
        s._row_descriptors = [dict(LOC_X, type="float")] * 3
        s._scrub_state = None
        s._edit_chunk = None
        s.ui = SimpleNamespace(tbl000=mock.MagicMock())
        s.ui.tbl000.actions.get.return_value = "unlocked"
        s.sb = SimpleNamespace(
            QtCore=QtCore,
            QtWidgets=SimpleNamespace(
                QApplication=SimpleNamespace(keyboardModifiers=lambda: Qt.NoModifier),
                QLineEdit=type("QLineEdit", (), {}),
            ),
        )
        s._refresh_table = mock.Mock()
        return s

    with mock.patch.object(CoreUtils, "undo_chunk", spy_chunk):
        events.clear()
        s = live_stand_in()
        s._on_edit_batch_started()
        s._on_edit_batch_started()  # a nested emission opens nothing more
        s._handle_cell_edit = mock.Mock()  # cells land between the two signals
        s._on_edit_batch_finished()
        s._on_edit_batch_finished()  # an unmatched finish closes nothing
        check(
            "a drag-fill opens ONE 'Channels Edit' bracket and closes it once",
            events == [("open", "Channels Edit"), ("close", "Channels Edit")]
            and s._edit_chunk is None,
            str(events),
        )

        events.clear()
        s = live_stand_in()
        s._on_scrub_started(0, ChannelsSlots.COL_VALUE)
        s._on_scrub_moved(0, ChannelsSlots.COL_VALUE, 10, 0)
        s._on_scrub_moved(0, ChannelsSlots.COL_VALUE, 20, 0)
        open_during = list(events)
        s._on_scrub_finished(0, ChannelsSlots.COL_VALUE)
        check(
            "an MMB scrub holds one bracket from press to release",
            open_during[0] == ("open", "Scrub location_x")
            and ("close", "Scrub location_x") not in open_during
            and events[-1] == ("close", "Scrub location_x")
            and events.count("write") == 4
            and "cancel" not in events,
            str(events),
        )

        events.clear()
        s = live_stand_in()
        s._on_scrub_started(0, ChannelsSlots.COL_VALUE)
        s._on_scrub_finished(0, ChannelsSlots.COL_VALUE)
        check(
            "a scrub that never moved pushes no step",
            events
            == [("open", "Scrub location_x"), "cancel", ("close", "Scrub location_x")],
            str(events),
        )

        events.clear()
        s = live_stand_in()
        s._on_scrub_started(0, ChannelsSlots.COL_VALUE)
        s._on_scrub_started(0, ChannelsSlots.COL_VALUE)  # the release was lost
        check(
            "a scrub whose release was lost is closed by the next press",
            events[:3]
            == [
                ("open", "Scrub location_x"),
                "cancel",
                ("close", "Scrub location_x"),
            ]
            and events[3] == ("open", "Scrub location_x"),
            str(events),
        )
        s._on_scrub_finished(0, ChannelsSlots.COL_VALUE)  # else GC closes it later

        events.clear()
        s = live_stand_in()
        s._on_scrub_started(0, ChannelsSlots.COL_VALUE)
        s._on_scrub_started(0, ChannelsSlots.COL_CONN)  # lost release; no scrub here
        check(
            "a lost release is closed by the next press, even one that starts no scrub",
            events
            == [("open", "Scrub location_x"), "cancel", ("close", "Scrub location_x")]
            and s._scrub_state is None,
            str(events),
        )
        s._end_scrub()  # else GC closes it later

        events.clear()
        s = live_stand_in()
        s._on_wheel_scrolled(0, ChannelsSlots.COL_VALUE, 1, Qt.NoModifier)
        check(
            "a wheel notch over two objects is one bracket",
            events
            == [
                ("open", "Wheel location_x"),
                "write",
                "write",
                ("close", "Wheel location_x"),
            ],
            str(events),
        )

        events.clear()
        s = live_stand_in()
        s._on_icon_cells_dragged([0, 1, 2], ChannelsSlots.COL_LOCK)
        check(
            "an icon drag is one 'Channels Drag' bracket",
            events
            == [("open", "Channels Drag")]
            + ["write"] * 3
            + [("close", "Channels Drag")],
            str(events),
        )

    # --- The batch signals are wired with the rest of the table's ----------
    s = ChannelsSlots.__new__(ChannelsSlots)
    s.sb = SimpleNamespace(QtCore=QtCore)
    widget = mock.MagicMock()
    widget._ch_scrub_conns = {}
    ChannelsSlots._wire_table_signals(s, widget)
    check(
        "editBatchStarted / editBatchFinished reach the panel",
        widget.editBatchStarted.connect.call_args == mock.call(s._on_edit_batch_started)
        and widget.editBatchFinished.connect.call_args
        == mock.call(s._on_edit_batch_finished),
        f"{widget.editBatchStarted.connect.call_args} "
        f"{widget.editBatchFinished.connect.call_args}",
    )

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if lines and all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
