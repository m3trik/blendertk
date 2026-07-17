"""blendertk menu_harvest pure-logic test (Qt-free, bpy-free).

Run: blender --background --factory-startup --python blendertk/test/test_menu_harvest.py
Also runs under the workspace ``.venv`` — the recorder/serialization layer under test
imports no ``bpy`` or Qt at all.

Covers the harvest recorder that turns a native menu's ``draw`` into a row list:
``_OpProps`` assignment recording (incl. the nested macro-props tree that fixed the
dead Vertices/Edges menus), ``_plain_props`` serialization (empty groups dropped),
``_LayoutRecorder`` row capture (sub-layouts share the parent's items; ``enabled``
greys rows recorded through that sub-layout; unknown layout members swallow), and
``_MenuShim`` attribute forwarding (helpers bound, staticmethods NOT bound). The
bpy/Qt half (``refill_qmenu`` against live menus) is exercised by tentacle's
``blender_menus_check.py`` GUI harness.
"""
import os
import sys
import traceback

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
    from blendertk.ui_utils.menu_harvest import (
        _LayoutRecorder,
        _MenuShim,
        _OpProps,
        _plain_props,
    )

    # ---- _OpProps / _plain_props ------------------------------------------------
    rec = _OpProps()
    rec.url = "https://example.com"
    check("op-props records plain assignment", rec.props == {"url": "https://example.com"})

    # Macro configuration: props.MESH_OT_rip.use_fill = False must record a
    # nested group, not raise (the dead Vertices/Edges menus regression).
    macro = _OpProps()
    macro.MESH_OT_rip.use_fill = False
    macro.MESH_OT_rip.mirror = True
    macro.TRANSFORM_OT_translate.value = (1, 2, 3)
    plain = _plain_props(macro)
    check("nested macro groups serialize to nested dicts",
          plain == {"MESH_OT_rip": {"use_fill": False, "mirror": True},
                    "TRANSFORM_OT_translate": {"value": (1, 2, 3)}}, repr(plain))

    probe = _OpProps()
    probe.SOME_OT_group  # a read that never assigns
    probe.real = 1
    check("empty nested groups are dropped from serialization",
          _plain_props(probe) == {"real": 1}, repr(_plain_props(probe)))

    check("assigned None round-trips (not confused with an unset read)",
          _plain_props(_OpProps()) == {})

    # ---- _LayoutRecorder ----------------------------------------------------------
    layout = _LayoutRecorder()
    op_rec = layout.operator("mesh.select_all", text="All")
    layout.separator()
    layout.menu("VIEW3D_MT_snap", text="Snap")
    layout.label(text="Section")
    layout.label(text="")  # empty labels are not rows
    layout.prop("DATA", "use_snap", None)
    check("operator row recorded with its props recorder",
          layout.items[0] == ("operator", "mesh.select_all", "All", True, op_rec))
    check("separator / menu / label rows recorded",
          layout.items[1:4] == [("separator",),
                                ("menu", "VIEW3D_MT_snap", "Snap"),
                                ("label", "Section")])
    check("empty label is skipped",
          all(i[:2] != ("label", "") for i in layout.items))
    check("prop row records the parent's enabled state",
          layout.items[4] == ("prop", "DATA", "use_snap", None, True))

    sub = layout.row()
    sub.enabled = False
    sub.operator("mesh.delete", text=None)
    check("sub-layout shares the parent's item list",
          layout.items[-1][:2] == ("operator", "mesh.delete"))
    check("sub-layout enabled=False greys rows recorded through it",
          layout.items[-1][3] is False)
    layout.operator("mesh.dupli", text=None)
    check("parent layout stays enabled after a greyed sub-layout",
          layout.items[-1][3] is True)

    # Unknown layout members must swallow whole call/attr chains, not raise.
    layout.template_asset_view("x").whatever.chain(1, key=2)
    layout.context_pointer_set("a", None)
    layout.operator_context = "INVOKE_DEFAULT"  # absorbed state set
    check("unknown layout members swallow silently", True)

    # ---- _MenuShim ------------------------------------------------------------------
    class _FakeMenu:
        bl_label = "Fake"

        def draw_helper(self, context):
            return ("helper", self, context)

        @staticmethod
        def util(x):
            return x * 2

    shim = _MenuShim(_FakeMenu, layout)
    check("shim exposes .layout", shim.layout is layout)
    check("plain class attrs come back as-is", shim.bl_label == "Fake")
    kind, bound_self, ctx = shim.draw_helper("CTX")
    check("helper methods come back bound to the shim",
          kind == "helper" and bound_self is shim and ctx == "CTX")
    check("staticmethods are NOT bound (real instances don't pass self)",
          shim.util(21) == 42)

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
