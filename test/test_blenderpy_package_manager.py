"""blendertk structural test for blenderpy-package-manager.bat (the thin Blender wrapper) and
the shared m3trik\\package-manager.bat it hands off to (mirror of mayatk's mayapy wrapper).

Pure .bat parsing (no bpy / Qt needed); runs under the Blender harness like the other suites.

Run: blender --background --factory-startup --python blendertk/test/test_blenderpy_package_manager.py
"""
import sys
import os
import re
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)               # blendertk/
MONO = os.path.dirname(REPO)               # _scripts/
WRAPPER = os.path.join(REPO, "blendertk", "env_utils", "blenderpy-package-manager.bat")
GENERIC = os.path.join(MONO, "m3trik", "package-manager.bat")
# The shared menu is mirrored next to the wrapper (by m3trik/scripts/sync_shared_bat.py) so it
# ships in the wheel — after a bare pip install there is no m3trik/ to fall back to.
MIRROR = os.path.join(REPO, "blendertk", "env_utils", "package-manager.bat")


def _norm_eol(data):
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

lines = []


def check(name, cond, detail=""):
    lines.append(f"{'OK  ' if cond else 'FAIL'} {name}{(' | ' + detail) if detail else ''}")


LABEL_RE = re.compile(r"^\s*:([A-Za-z_][A-Za-z0-9_]*)\s*$")
GOTO_RE = re.compile(r"\bgoto\s+([A-Za-z_:][A-Za-z0-9_]*)", re.IGNORECASE)
CALL_SUB_RE = re.compile(r"\bcall\s+:([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _strip(line):
    return "" if line.strip().startswith("::") else line


def analyze(path):
    text_lines = open(path, encoding="utf-8").read().splitlines()
    labels, dups = {}, []
    for raw in text_lines:
        m = LABEL_RE.match(_strip(raw))
        if m:
            if m.group(1) in labels:
                dups.append(m.group(1))
            labels[m.group(1)] = True
    gotos = [m.group(1) for raw in text_lines for m in GOTO_RE.finditer(_strip(raw))]
    calls = [m.group(1) for raw in text_lines for m in CALL_SUB_RE.finditer(_strip(raw))]
    text = "\n".join(text_lines)
    return text_lines, labels, dups, gotos, calls, text


try:
    check("blenderpy wrapper exists", os.path.isfile(WRAPPER), WRAPPER)
    check("shared generic package-manager.bat exists (handoff target)", os.path.isfile(GENERIC), GENERIC)

    if os.path.isfile(WRAPPER):
        wlines, wlabels, wdups, wgotos, wcalls, wtext = analyze(WRAPPER)

        check("no duplicate labels", not wdups, f"{wdups}")
        unresolved_goto = [g for g in wgotos if g.lower() != ":eof" and g.lstrip(":") not in wlabels]
        check("all goto targets resolve", not unresolved_goto, f"{unresolved_goto}")
        unresolved_call = [c for c in wcalls if c not in wlabels]
        check("all call :sub targets resolve", not unresolved_call, f"{unresolved_call}")

        sl = len(re.findall(r"\bSETLOCAL\b", wtext, re.IGNORECASE))
        el = len(re.findall(r"\bENDLOCAL\b", wtext, re.IGNORECASE))
        # ENDLOCAL >= SETLOCAL: one scope, closed on each exit path (strict equality wrongly
        # flags multi-exit scripts; the real bug is a SETLOCAL never closed -> el < sl).
        check("SETLOCAL closed on every exit path (ENDLOCAL >= SETLOCAL >= 1)",
              sl >= 1 and el >= sl, f"setlocal={sl} endlocal={el}")

        ps_offenders = [
            l.strip()[:70] for raw in wlines for l in [_strip(raw)]
            if re.search(r"\bpowershell\b\s+(?!.*-NoProfile)", l, re.IGNORECASE) and "-Command" in l
        ]
        check("powershell invocations use -NoProfile", not ps_offenders, f"{ps_offenders}")

        required = {"setVersion", "validateBlenderPyPath", "handoff"}
        missing = sorted(required - set(wlabels))
        check("required wrapper labels present", not missing, f"missing={missing}")

        check("hands off to the shared package-manager.bat",
              "package-manager.bat" in wtext.lower() and re.search(r'call\s+"%generic%"', wtext, re.IGNORECASE) is not None)

        # Blender bundles its python at <install>\<ver>\python\bin\python.exe — the wrapper must
        # build that path (the one DCC-specific bit vs the mayapy wrapper).
        check("resolves Blender's bundled python path",
              re.search(r"python\\bin\\python\.exe", wtext, re.IGNORECASE) is not None)
        check("scans the Blender Foundation install dir",
              "blender foundation" in wtext.lower())

        # The wrapper's first handoff candidate is `%~dp0package-manager.bat` (the wheel case):
        # the shared menu must be mirrored beside the wrapper and match the m3trik SSoT verbatim.
        mirror_ok = os.path.isfile(MIRROR)
        check("shared menu mirrored next to wrapper (ships in wheel)", mirror_ok, MIRROR)
        if mirror_ok and os.path.isfile(GENERIC):
            with open(MIRROR, "rb") as f:
                mirror_bytes = f.read()
            with open(GENERIC, "rb") as f:
                generic_bytes = f.read()
            check("mirror matches the m3trik SSoT (run sync_shared_bat.py on drift)",
                  _norm_eol(mirror_bytes) == _norm_eol(generic_bytes))

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
