"""blendertk Hierarchy Sync tree_utils test (Qt, no bpy) — runs under the workspace .venv.

Covers ``_extract_object_name_from_item``'s UserRole-object preference and placeholder guard (the
parity fix mirroring mayatk's audit). Qt-only: no Blender runtime needed, so it runs under the
.venv rather than the Blender harness.

Run:  o:/Cloud/Code/_scripts/.venv/Scripts/python.exe blendertk/test/test_hierarchy_tree_utils.py
"""
import os, sys, traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from qtpy import QtWidgets  # noqa: F401
except Exception:
    # Blender headless ships no Qt binding — this suite is a .venv target. Skip cleanly.
    print("SKIP test_hierarchy_tree_utils (no Qt binding — run under the workspace .venv)")
    print("===RESULT: PASS=== (skipped)")
    sys.exit(0)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    from qtpy import QtWidgets, QtCore
    from blendertk.env_utils.hierarchy_sync import tree_utils

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class FakeObj:
        """Stand-in for a bpy Object — build_path only touches .name and .parent."""

        def __init__(self, name, parent=None):
            self.name = name
            self.parent = parent

    tree = QtWidgets.QTreeWidget()

    # (1) A real object payload → its canonical build_path, even when the DISPLAY text differs.
    grp = FakeObj("Grp")
    child = FakeObj("Child", parent=grp)
    gi = QtWidgets.QTreeWidgetItem(tree, ["DISPLAY_Grp"])
    ci = QtWidgets.QTreeWidgetItem(gi, ["DISPLAY_Child"])
    ci.setData(0, QtCore.Qt.UserRole, child)
    check("prefers the UserRole object's build_path over display text",
          tree_utils._extract_object_name_from_item(ci) == "Grp|Child",
          tree_utils._extract_object_name_from_item(ci))

    # (2) Placeholder rows resolve to nothing (not their prompt text).
    for token in ("browse_placeholder", "open_scene_placeholder"):
        pi = QtWidgets.QTreeWidgetItem(tree, ["Browse for reference…"])
        pi.setData(0, QtCore.Qt.UserRole, token)
        check(f"placeholder '{token}' resolves to empty",
              tree_utils._extract_object_name_from_item(pi) == "")

    # (3) No payload → text-chain fallback (unambiguous full path).
    ti = QtWidgets.QTreeWidgetItem(tree, ["Solo"])
    check("falls back to the text chain when there is no object payload",
          tree_utils._extract_object_name_from_item(ti) == "Solo")
    a = QtWidgets.QTreeWidgetItem(tree, ["A"])
    b = QtWidgets.QTreeWidgetItem(a, ["B"])
    check("text-chain fallback yields the full path", tree_utils._extract_object_name_from_item(b) == "A|B")

    # (4) get_selected_object_names skips placeholder selections.
    ci.setSelected(True)
    tree.topLevelItem(1).setSelected(True)  # the first placeholder row
    names = tree_utils.get_selected_object_names(tree)
    check("get_selected_object_names returns only real objects", names == ["Grp|Child"], str(names))

    # (5) 'Hide Ignored' toggle: apply_ignore_styling hides ignored items (direct + inherited)
    #     when _hide_ignored is set, and reveals them when it is cleared.
    import logging
    from blendertk.env_utils.hierarchy_sync.tree_renderer import HierarchyTreeRenderer

    class _FakeSb:
        QtCore, QtWidgets, QtGui = QtCore, QtWidgets, __import__("qtpy.QtGui", fromlist=["x"])

    class _FakeCtrl:
        def __init__(self):
            self.logger = logging.getLogger("hier_fake")
            self.logger.setLevel(logging.WARNING)
            self.sb = _FakeSb()
            self._ignored = set()
            self._hide_ignored = False

        def _redirect_logger(self, logger):
            pass

        def _get_ignored_set(self, tree_widget):
            return self._ignored

        def is_path_ignored(self, tree_widget, path):
            return path in self._ignored or any(path.startswith(ip + "|") for ip in self._ignored)

    htree = QtWidgets.QTreeWidget()
    g = QtWidgets.QTreeWidgetItem(htree, ["Grp"])
    c = QtWidgets.QTreeWidgetItem(g, ["Child"])          # inherited-ignored under Grp
    other = QtWidgets.QTreeWidgetItem(htree, ["Other"])  # never ignored

    fctrl = _FakeCtrl()
    renderer = HierarchyTreeRenderer(fctrl)
    fctrl._ignored = {"Grp"}  # Grp direct, Child inherited

    fctrl._hide_ignored = False
    renderer.apply_ignore_styling(htree)
    check("hide off: ignored items stay visible (dimmed only)",
          not g.isHidden() and not c.isHidden() and not other.isHidden())
    check("hide off: direct-ignored item is struck through", g.font(0).strikeOut())

    fctrl._hide_ignored = True
    renderer.apply_ignore_styling(htree)
    check("hide on: direct + inherited ignored items are hidden", g.isHidden() and c.isHidden())
    check("hide on: non-ignored item stays visible", not other.isHidden())

    fctrl._hide_ignored = False
    renderer.apply_ignore_styling(htree)
    check("toggling hide back off reveals the ignored items again",
          not g.isHidden() and not c.isHidden())

except Exception as e:
    traceback.print_exc()
    check("test raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
