"""blendertk RizomUV script-construction + version-gating test (Qt/venv side — no bpy).

Run under the workspace venv:  python blendertk/test/test_rizom_construction.py

Covers the DCC-agnostic half of the round-trip that the Blender-side plumbing test
(``test_rizom_roundtrip.py``) deliberately stubs out: preset resolution, the
ZomLoad/ZomSave/ZomQuit wrapper substitution, placeholder resolution, and the RizomUV-version
gating (older Rizom access-violates on newer ZomPack fields, so gated lines must be stripped).

This needs ``uitk.bridge`` (``parameters.py`` builds its registry from ``AttributeSpec``), which
pulls in a Qt binding — present under the workspace venv, absent under headless Blender. When run
under the Blender harness (no ``qtpy``) it SKIPs cleanly so the aggregate stays green. The bpy-side
export/import/transfer is verified separately by ``test_rizom_roundtrip.py``.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk"), os.path.join(MONO, "uitk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


try:
    try:
        from blendertk.uv_utils.rizom_bridge import parameters as P  # noqa: F401 (needs qtpy)
    except ModuleNotFoundError as e:
        if "qtpy" in str(e) or "PySide" in str(e):
            print("SKIP: needs a Qt binding (run under the workspace venv, not headless Blender).")
            print("===RESULT: PASS===")
            raise SystemExit(0)
        raise

    from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge, _SCRIPT_DIR

    V2020 = r"C:/Program Files/Rizom Lab/RizomUV VS RS 2020.1/Rizomuv_VS.exe"
    V2022 = r"C:/Program Files/Rizom Lab/RizomUV 2022.2/Rizomuv_VS.exe"

    # ---- preset resolution -------------------------------------------------------
    b = RizomUVBridge(rizom_path=V2022)
    b.export_path = "C:/tmp/x.fbx"
    for preset in ("pack", "unwrap_hard", "unwrap_organic", "optimize", "send"):
        body = b._resolve_script(preset=preset)
        check(f"resolve preset '{preset}'", isinstance(body, str) and len(body) > 0)
    try:
        b._resolve_script(preset="nope")
        check("unknown preset -> FileNotFoundError", False)
    except FileNotFoundError:
        check("unknown preset -> FileNotFoundError", True)
    try:
        b._resolve_script(uv_script="x", preset="pack")
        check("both uv_script+preset -> ValueError", False)
    except ValueError:
        check("both uv_script+preset -> ValueError", True)

    # ---- wrapper substitution (2022: all placeholders resolve) -------------------
    import re

    pack = (_SCRIPT_DIR / "pack.lua").read_text(encoding="utf-8")
    full22 = b._construct_full_script(pack)
    leftover = re.findall(r"__[A-Z][A-Z_]*__", full22)  # unresolved __KEY__ tokens
    check("2022: wrapper adds ZomLoad/ZomSave/ZomQuit",
          all(z in full22 for z in ("ZomLoad", "ZomSave", "ZomQuit")))
    check("2022: no unresolved __KEY__ placeholders left", not leftover, str(leftover))
    check("2022: gated pack fields kept (MaxMutations/Resolution)",
          "MaxMutations" in full22 and "Resolution" in full22)
    check("2022: export path inlined into ZomLoad/ZomSave",
          full22.count('Path="C:/tmp/x.fbx"') >= 2)

    # ---- version gating (2020.1 must strip the gated lines) ----------------------
    # The FBX flag rides on the ZomLoad/ZomSave *code* lines; check those, not the whole script
    # (wrapper.lua's header comment mentions "UseUVSetNames" and would false-positive a raw scan).
    def zom_lines(script):
        return "\n".join(
            ln for ln in script.splitlines() if ln.lstrip().startswith(("ZomLoad", "ZomSave"))
        )

    b20 = RizomUVBridge(rizom_path=V2020)
    b20.export_path = "C:/tmp/x.fbx"
    full20 = b20._construct_full_script(pack)
    check("2020.1: gated ZomPack fields stripped (no MaxMutations)",
          "MaxMutations" not in full20 and "__PACK_MAX_MUTATIONS__" not in full20)
    check("2020.1: non-gated fields survive (RecursionDepth resolved)",
          "RecursionDepth" in full20 and "__RECURSION_DEPTH__" not in full20)
    check("2020.1: no FBX={UseUVSetNames} flag on the ZomLoad/ZomSave lines (below the gate)",
          "UseUVSetNames" not in zom_lines(full20), zom_lines(full20))
    check("2022.2: FBX={UseUVSetNames=true} flag on the ZomLoad/ZomSave lines (above the gate)",
          "FBX={UseUVSetNames=true}" in zom_lines(full22))

    # ---- param overrides flow into the script ------------------------------------
    full_ovr = b._construct_full_script(pack)  # defaults
    b._params = {"RECURSION_DEPTH": 5}
    full_ovr2 = b._construct_full_script(pack)
    check("param override changes the rendered script",
          "RecursionDepth=5" in full_ovr2 and "RecursionDepth=5" not in full_ovr)

    # ---- a script that carries its own ZomLoad/ZomSave bypasses the wrapper -------
    custom = 'ZomLoad({File={Path="p"}})\nZomSave({File={Path="p"}})\n'
    passthru = b._construct_full_script(custom)
    check("self-managed ZomLoad/ZomSave script passes through (no double wrapper)",
          passthru.count("ZomLoad") == 1 and "ZomQuit" not in passthru)

except SystemExit:
    raise
except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
