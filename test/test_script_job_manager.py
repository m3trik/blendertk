"""blendertk.core_utils.script_job_manager headless test — subscribe/dispatch/suppress/ephemeral/
owner cleanup + real bpy.app.handlers install/remove + selection-diff gating.

Run: blender --background --factory-startup --python blendertk/test/test_script_job_manager.py
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []
def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")

try:
    import bpy
    from blendertk.core_utils.script_job_manager import ScriptJobManager

    def hlist(name):
        return getattr(bpy.app.handlers, name)
    def has_master(name):
        return any(getattr(f, "__name__", "") == "_master" for f in hlist(name))
    def tokens(mgr):
        return [s["token"] for s in mgr.status()["subscriptions"]]

    ScriptJobManager.reset()
    mgr = ScriptJobManager.instance()

    # 1. subscribe installs the backing @persistent master handler in bpy.app.handlers
    calls = []
    t_scene = mgr.subscribe("SceneOpened", lambda: calls.append("scene"), owner="A")
    check("subscribe installs load_post master", has_master("load_post")
          and "load_post" in mgr.status()["installed_handlers"])
    check("master is @persistent (survives file load)",
          hasattr(mgr._handlers["load_post"], "_bpy_persistent"))

    # 2. dispatch calls the subscriber (no args)
    mgr._dispatch("SceneOpened")
    check("dispatch calls subscriber", calls == ["scene"], f"{calls}")

    # 3. NewSceneOpened multiplexes onto the SAME load_post handler (no second handler)
    n0 = len(hlist("load_post"))
    t_new = mgr.subscribe("NewSceneOpened", lambda: None, owner="A")
    check("NewSceneOpened reuses load_post (one handler)", len(hlist("load_post")) == n0,
          f"n={len(hlist('load_post'))} was {n0}")

    # 4. suppress / resume
    calls.clear(); mgr.suppress(t_scene); mgr._dispatch("SceneOpened")
    check("suppress silences the listener", calls == [], f"{calls}")
    mgr.resume(t_scene); mgr._dispatch("SceneOpened")
    check("resume restores the listener", calls == ["scene"], f"{calls}")

    # 4b. suppressed() context manager: silences inside the block, restores after,
    #     preserves prior suppression, and skips None tokens
    calls.clear()
    with mgr.suppressed(None, t_scene):
        mgr._dispatch("SceneOpened")
    check("suppressed() silences inside the block", calls == [], f"{calls}")
    mgr._dispatch("SceneOpened")
    check("suppressed() restores on exit", calls == ["scene"], f"{calls}")
    mgr.suppress(t_scene)
    with mgr.suppressed(t_scene):
        pass
    check("suppressed() preserves prior suppression", t_scene in mgr._suppressed)
    mgr.resume(t_scene)

    # 4b'. counted suppression: nested blocks compose; fully resumed at exit
    with mgr.suppressed(t_scene):
        with mgr.suppressed(t_scene):
            pass
        calls.clear(); mgr._dispatch("SceneOpened")
        check("inner suppressed() exit keeps outer block silenced", calls == [], f"{calls}")
    check("nested suppressed() fully resumes at outer exit", t_scene not in mgr._suppressed)

    # 4c. connect_cleanup: two owners sharing one widget each get their own cleanup
    class _FakeSignal:
        def __init__(self): self.slots = []
        def connect(self, fn): self.slots.append(fn)
    class _FakeWidget:
        def __init__(self): self.destroyed = _FakeSignal()
    w = _FakeWidget()
    t_x = mgr.subscribe("SceneSaved", lambda: None, owner="X")
    t_y = mgr.subscribe("SceneSaved", lambda: None, owner="Y")
    mgr.connect_cleanup(w, owner="X")
    mgr.connect_cleanup(w, owner="X")  # same pair → idempotent
    mgr.connect_cleanup(w, owner="Y")  # second owner, same widget → own cleanup
    check("connect_cleanup idempotent per pair, distinct per owner", len(w.destroyed.slots) == 2,
          f"n={len(w.destroyed.slots)}")
    for fn in list(w.destroyed.slots):
        fn()
    check("widget destroy cleans up BOTH owners",
          t_x not in tokens(mgr) and t_y not in tokens(mgr))

    # 5. ephemeral pruned the next time a scene-change event fires; its handler is then removed
    eph = []
    t_eph = mgr.subscribe("SelectionChanged", lambda: eph.append(1), owner="A", ephemeral=True)
    check("SelectionChanged installs depsgraph_update_post handler",
          "depsgraph_update_post" in mgr.status()["installed_handlers"])
    mgr._dispatch("SceneOpened")  # scene-change → prune ephemerals
    check("ephemeral pruned on scene change", t_eph not in tokens(mgr))
    check("depsgraph handler removed once its only sub is pruned",
          "depsgraph_update_post" not in mgr.status()["installed_handlers"] and not has_master("depsgraph_update_post"))

    # 6. unsubscribe_all(owner) clears subs AND detaches the now-unused handlers from bpy
    mgr.unsubscribe_all("A")
    check("unsubscribe_all clears subscriptions", mgr.status()["subscriptions"] == [])
    check("unsubscribe_all removes installed handlers", mgr.status()["installed_handlers"] == [])
    check("load_post master detached from bpy.app.handlers", not has_master("load_post"))

    # 7. unknown event name raises ValueError
    try:
        mgr.subscribe("Bogus", lambda: None); raised = False
    except ValueError:
        raised = True
    check("unknown event raises ValueError", raised)

    # 8. selection-diff gating: the depsgraph master dispatches SelectionChanged only on change
    sel = []
    mgr.subscribe("SelectionChanged", lambda: sel.append(1), owner="B")
    master = mgr._handlers["depsgraph_update_post"]
    state = {"cur": frozenset()}
    mgr._current_selection = lambda: state["cur"]  # deterministic, independent of bg context
    mgr._last_selection = frozenset()
    master(None, None)  # unchanged → no dispatch
    check("selection master no-op when selection unchanged", sel == [], f"{sel}")
    state["cur"] = frozenset({"Cube"})
    master(None, None)  # changed → dispatch once
    check("selection master dispatches on selection change", sel == [1], f"{sel}")
    master(None, None)  # unchanged again → no further dispatch
    check("selection master debounces repeats", sel == [1], f"{sel}")

    # 9. the real _current_selection reads bpy without raising (smoke, fresh instance)
    ScriptJobManager.reset()
    check("_current_selection returns a frozenset (no raise)",
          isinstance(ScriptJobManager.instance()._current_selection(), frozenset))

    # 10. reset tears everything down (singleton + bpy handlers)
    ScriptJobManager.reset()
    fresh = ScriptJobManager.instance()
    check("reset clears subscriptions + handlers",
          fresh.status()["subscriptions"] == [] and fresh.status()["installed_handlers"] == [])
    check("reset detaches all masters from bpy",
          not any(has_master(n) for n in ("load_post", "depsgraph_update_post", "frame_change_post",
                                          "save_post", "undo_post", "redo_post")))
    ScriptJobManager.reset()

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(l.startswith("OK") for l in lines)
print("\n===SCRIPT-JOB-MANAGER===")
print("\n".join(lines))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
