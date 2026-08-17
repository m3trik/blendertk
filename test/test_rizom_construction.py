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
            # Tag the sentinel so the harness reports SKIP rather than counting
            # this as a green suite that silently contributed zero checks.
            print("===RESULT: PASS=== (skipped)")
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

    # Field-presence checks must ignore Lua comments: the shared pack_block.lua
    # header discusses the gated fields BY NAME (why Resolution is sent on
    # 2020.1 and MaxMutations is not), and a raw substring scan reads that prose
    # as code. Same hazard the zom_lines() helper below exists for.
    def code_lines(script):
        return "\n".join(
            ln for ln in script.splitlines() if not ln.lstrip().startswith("--")
        )

    pack = (_SCRIPT_DIR / "pack.lua").read_text(encoding="utf-8")
    full22 = b._construct_full_script(pack)
    leftover = re.findall(r"__[A-Z][A-Z_]*__", full22)  # unresolved __KEY__ tokens
    check("2022: wrapper adds ZomLoad/ZomSave/ZomQuit",
          all(z in full22 for z in ("ZomLoad", "ZomSave", "ZomQuit")))
    check("2022: no unresolved __KEY__ placeholders left", not leftover, str(leftover))
    check("2022: gated pack fields kept (MaxMutations/Resolution)",
          "MaxMutations" in full22 and "Resolution" in full22)
    check("2022: Rotate.Enable kept above the gate", "Enable=" in code_lines(full22))
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
    check("2020.1: gated ZomPack fields stripped (no MaxMutations/Rotate.Enable)",
          "MaxMutations" not in code_lines(full20)
          and "__PACK_MAX_MUTATIONS__" not in full20
          and "Enable=" not in code_lines(full20))
    # Resolution is deliberately NOT gated: probed safe on 2020.1, and sending it
    # is what makes a single send converge instead of needing a second one.
    check("2020.1: Resolution survives (ungated -- converges the pack in one send)",
          "Resolution=" in code_lines(full20)
          and "__PACK_RESOLUTION__" not in full20)
    check("2020.1: non-gated fields survive (RecursionDepth resolved)",
          "RecursionDepth" in full20 and "__RECURSION_DEPTH__" not in full20)
    check("2020.1: no FBX={UseUVSetNames} flag on the ZomLoad/ZomSave lines (below the gate)",
          "UseUVSetNames" not in zom_lines(full20), zom_lines(full20))
    check("2022.2: FBX={UseUVSetNames=true} flag on the ZomLoad/ZomSave lines (above the gate)",
          "FBX={UseUVSetNames=true}" in zom_lines(full22))

    # ---- pack gutter is DERIVED, not a user knob ---------------------------------
    # Island spacing / tile margin come off the shared UV-padding rule so a Rizom
    # round-trip and an in-Blender repack agree on the gutter.
    from blendertk.uv_utils._uv_utils import UvUtils

    pad = UvUtils.calculate_uv_padding(1024, normalize=True)
    check("gutter tokens are not UI knobs",
          not any(k in P.PARAMS for k in P.DERIVED_KEYS), str(P.DERIVED_KEYS))
    derived = P.Parameters.derived_values({"PACK_RESOLUTION": 1024})
    check("derived spacing == normalized padding", derived["PACK_SPACING"] == pad)
    check("derived margin == half the spacing", derived["PACK_MARGIN"] == pad / 2)
    check("padding is map-size-invariant",
          P.Parameters.derived_values({"PACK_RESOLUTION": 4096})["PACK_SPACING"] == pad)
    stale = P.Parameters.render_context(
        {"PACK_RESOLUTION": 1024, "PACK_SPACING": 0.5, "PACK_MARGIN": 0.5}
    )
    check("stale preset values lose to the derived gutter",
          float(stale["PACK_SPACING"]) == pad and float(stale["PACK_MARGIN"]) == pad / 2)
    check("2022: gutter substituted into the pack block",
          f"MarginSize={pad / 2}" in full22 and f"PaddingSize={pad}" in full22)
    # "PaddingSize=" (with the assignment) — pack_block.lua's header comment names the
    # field, so a bare substring scan would false-positive.
    check("2020.1: gutter uses the pre-rename SpacingSize field",
          f"SpacingSize={pad}" in full20 and "PaddingSize=" not in full20)

    # ---- param overrides flow into the script ------------------------------------
    full_ovr = b._construct_full_script(pack)  # defaults
    b._params = {"RECURSION_DEPTH": 5}
    full_ovr2 = b._construct_full_script(pack)
    check("param override changes the rendered script",
          "RecursionDepth=5" in full_ovr2 and "RecursionDepth=5" not in full_ovr)

    # ---- keep-stacked: pack-only opt-in behind a Lua literal, Lua vendored from mayatk ----
    keys = lambda name: P.Parameters.referenced_keys(  # noqa: E731
        (_SCRIPT_DIR / f"{name}.lua").read_text(encoding="utf-8"))
    check("PACK_KEEP_STACKED is a bool knob, off by default",
          P.PARAMS["PACK_KEEP_STACKED"].kind == "bool"
          and P.PARAMS["PACK_KEEP_STACKED"].default is False)
    check("keep-stacked shows for the pack-type presets only",
          all("PACK_KEEP_STACKED" in keys(n) for n in ("pack", "optimize"))
          and not any("PACK_KEEP_STACKED" in keys(n)
                      for n in ("unwrap_hard", "unwrap_organic", "unwrap_hybrid")))
    b._params = {"PACK_KEEP_STACKED": True}
    on = code_lines(b._construct_full_script(pack))
    b._params = {"PACK_KEEP_STACKED": False}
    off = code_lines(b._construct_full_script(pack))
    check("keep-stacked renders the overlap grouping gated by the knob",
          'Mode="DefineGroupsByOverlapness"' in on and "if true then" in on
          and "Properties={Pack={Stacked=true}}" in on
          and "if false then" in off and "__KEEP_STACKED_BLOCK__" not in on + off)
    check("keep-stacked grouping precedes the pack",
          on.index('Mode="DefineGroupsByOverlapness"') < on.index("ZomPack("))
    _grp = on.index('Mode="DefineGroupsByOverlapness"')
    check("keep-stacked brackets the grouping with the MultiCOG shrink / unshrink",
          on.index('CenterMode="MultiCOG"') < _grp < on.index('CenterMode="MultiCOG"', _grp)
          and "Transform={0.001, 0, 0, 0, 0.001, 0, 0, 0, 1}" in on
          and "Transform={1000, 0, 0, 0, 1000, 0, 0, 0, 1}" in on)
    b._params = {}
    import filecmp
    _maya_rb = _SCRIPT_DIR.parents[4] / "mayatk" / "mayatk" / "uv_utils" / "rizom_bridge"
    if _maya_rb.is_dir():
        vendored = ("scripts/pack.lua", "scripts/optimize.lua",
                    "templates/pack_block.lua", "templates/keep_stacked_block.lua")
        stale = [f for f in vendored
                 if not filecmp.cmp(_maya_rb / f, _SCRIPT_DIR.parent / f, shallow=False)]
        check("pack-side Lua stays byte-identical to mayatk's", not stale, str(stale))

    # ---- a script that carries its own ZomLoad/ZomSave bypasses the wrapper -------
    custom = 'ZomLoad({File={Path="p"}})\nZomSave({File={Path="p"}})\n'
    passthru = b._construct_full_script(custom)
    check("self-managed ZomLoad/ZomSave script passes through (no double wrapper)",
          passthru.count("ZomLoad") == 1 and "ZomQuit" not in passthru)

    # ---- round-trip temp payloads are unique per bridge ---------------------------
    # Regression: the FBX and the -cfi Lua were fixed tempdir names -- the SAME two the
    # Maya bridge used, so the twin panels raced for one script file. RizomUV re-reads
    # the script after launch, so a mid-run overwrite made it exit 0 without reaching
    # ZomSave. Lifecycle is now ptk.TempArtifacts (unique tag per allocation).
    from pathlib import Path as _Path

    one = RizomUVBridge(rizom_path="not-used.exe")
    two = RizomUVBridge(rizom_path="not-used.exe")
    for br in (one, two):
        br.script_path = 'ZomSelect({PrimType="Edge"})'
    check("two bridges get different round-trip FBX paths", one.export_path != two.export_path,
          one.export_path)
    check("two bridges get different round-trip Lua paths", one.script_path != two.script_path,
          one.script_path)
    check("payloads are prefix-scoped for the stale sweep",
          all(_Path(p).name.startswith("rizom_roundtrip_")
              for p in (one.export_path, one.script_path)))

    # ---- the no-save error diagnoses a clobber instead of promising a traceback ---
    script_text = _Path(one.script_path).read_text(encoding="utf-8")
    intact = one._no_save_diagnosis(script_text)
    clobbered = one._no_save_diagnosis(script_text + "-- not what we wrote\n")
    check("no-save error names the script path", str(_Path(one.script_path)) in intact)
    check("no-save error names the FBX path", str(_Path(one.export_path)) in intact)
    check("intact script -> no clobber claim", "no longer matches" not in intact)
    check("changed script -> clobber reported", "no longer matches" in clobbered)
    check("no-save error drops the impossible traceback advice",
          "enable debug logging" not in intact)

    # ---- a reused bridge re-allocates, or run 2's payloads leak untracked ---------
    first_lua = one.script_path
    one._release_temp_payloads()
    check("clean run removes the Lua payload", not _Path(first_lua).exists())
    one.script_path = 'ZomSelect({PrimType="Edge"})'
    check("a second run allocates a fresh Lua path", one.script_path != first_lua)
    one._release_temp_payloads()
    two._release_temp_payloads()

except SystemExit:
    raise
except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines) and bool(lines)
for line in lines:
    print(line)
# Carry the tally: without it the suite counts for zero checks in run_tests.py's
# totals, and a run that asserted nothing reads as green.
_ok = sum(1 for line in lines if line.startswith("OK"))
print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({_ok}/{len(lines)})")
