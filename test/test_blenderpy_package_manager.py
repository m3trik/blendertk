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
    """Blank out a comment line. Batch has two forms -- `::` and the `REM` keyword -- and both
    must go: a checker that reads REM lines flags prose, not code."""
    s = line.strip().lower()
    return "" if s.startswith("::") or s == "rem" or s.startswith("rem ") else line


def section(text_lines, labels, name):
    """Source text of one label's body: from ``:name`` to the next label (exclusive)."""
    start = labels[name]
    after = sorted(ln for ln in labels.values() if ln > start)
    end = after[0] - 1 if after else len(text_lines)
    return "\n".join(text_lines[start:end])


def analyze(path):
    text_lines = open(path, encoding="utf-8").read().splitlines()
    labels, dups = {}, []
    for i, raw in enumerate(text_lines, start=1):
        m = LABEL_RE.match(_strip(raw))
        if m:
            if m.group(1) in labels:
                dups.append(m.group(1))
            labels[m.group(1)] = i
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

        # Messages are single-quoted PowerShell literals, so a word like "pip's" closes the
        # string early and the line dies on a parser error (a deliberate one is doubled, '').
        quotes = [
            l.strip()[:70] for raw in wlines for l in [_strip(raw)]
            if "powershell" in l.lower() and re.search(r"[A-Za-z]'[A-Za-z]", l)
        ]
        check("powershell message literals have no bare apostrophe", not quotes, f"{quotes}")

        # Anything that can hold a path must reach PowerShell as $env:NAME. Inlined into a
        # single-quoted literal, one apostrophe in a profile directory name closes the string
        # and the line dies on a parser error (proven live -- the remedies block vanished).
        path_vars = ("%blenderpy%", "%fetch_dir%", "%fetch_target%", "%~dp0", "%~f0", "%cd%")
        inlined = [
            (v, l.strip()[:60]) for raw in wlines for l in [_strip(raw)]
            if "powershell" in l.lower() for v in path_vars if v in l
        ]
        check("powershell reads paths from the environment, not the command line",
              not inlined, f"{inlined}")

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

        # `[!!]` under EnableDelayedExpansion prints as `[]` — the marker must be `[^!^!]`.
        bangs = [l.strip()[:70] for raw in wlines for l in [_strip(raw)] if "[!!]" in l]
        check("status markers escape the bang ([^!^!], not [!!])", not bangs, f"{bangs}")

        # One detected install is not a choice — use it instead of asking the user to confirm.
        one_install = wtext.find('else if "%version_count%"=="1" (')
        branch_end = wtext.find(") else if", one_install)
        branch = wtext[one_install : branch_end if branch_end != -1 else len(wtext)]
        check("single install skips the version prompt",
              "set /a version_count+=1" in wtext and one_install != -1 and "set /p" not in branch)

        # Regression: a firewall-blocked curl left no menu to hand off to and the window
        # closed on a 3s timer. The dead end must explain itself and wait for a keypress;
        # an unusable menu (empty, truncated, an intercepted HTML page, a stub left by an
        # earlier failure) must be rejected rather than called, whatever candidate it came
        # from, since calling it returns instantly and closes the window in silence.
        has_fetch = {"fetchShared", "fetchWheel", "fetchFailed", "checkGeneric"} <= set(wlabels)
        check("bootstrap has :fetchShared / :fetchWheel / :fetchFailed / :checkGeneric", has_fetch,
              f"{sorted(wlabels)}")
        if has_fetch:
            failed = section(wlines, wlabels, "fetchFailed")
            check("blocked download holds the window open and names the firewall",
                  "pause" in failed.lower() and "firewall" in failed.lower()
                  and re.search(r"(?i)timeout\s+/t", failed) is None)
            vet = section(wlines, wlabels, "checkGeneric")
            check("every menu candidate is vetted by signature, not by existence",
                  'findstr /b /c:":main"' in vet and 'set "generic="' in vet
                  and section(wlines, wlabels, "handoff").count("call :checkGeneric") == 2)
            fetch = section(wlines, wlabels, "fetchShared")
            wheel = section(wlines, wlabels, "fetchWheel")
            check("the bootstrap fetches through the resolved interpreter, with pip",
                  '"%blenderpy%" -m pip download' in wheel and "-m pip install" not in wheel
                  and all(f in wheel for f in
                          ("--no-deps", "--only-binary=:all:", "--retries", "--timeout")))
            check("an unusable download is deleted and the wheel scratch dir is cleared",
                  "call :checkGeneric" in fetch and "del " in fetch.lower()
                  and fetch.count('rd /s /q "%fetch_dir%"') == 2)

        # `set /p` returns instantly on EOF, so the version/path prompts bounced forever
        # whenever stdin was not a console — a busy loop spawning powershell.
        no_version = "noVersion" in wlabels
        check("an unentered version ends the run instead of re-prompting forever",
              no_version and "if not defined blender_version goto noVersion" in wtext
              and "pause" in section(wlines, wlabels, "noVersion").lower() if no_version else False,
              f"noVersion={no_version}")

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

    if os.path.isfile(GENERIC):
        glines, glabels, _, _, _, _ = analyze(GENERIC)
        validate = section(glines, glabels, "validateInterp")
        check("shared menu waits on a bad interpreter instead of auto-closing",
              "pause" in validate.lower() and re.search(r"(?i)timeout\s+/t", validate) is None)
        result = section(glines, glabels, "result")
        check("shared menu reports a failed op and names the firewall",
              'set "op_rc=%ERRORLEVEL%"' in result and "firewall" in result.lower())

        # One prompt feeds every operation, so the comma-separated list lives there. A comma is
        # also part of a requirement in a version range or extras list, and the guards that tell
        # them apart must stay unpiped -- a pipe re-parses an expanded `<`/`>` as redirection.
        # `%module%` is substituted before the line is parsed, so a typed `&` becomes a command
        # separator and what follows it runs. Delayed expansion passes the value through as data.
        gtext = "\n".join(_strip(l) for l in glines)
        check("the typed value is never re-parsed by cmd", "%module%" not in gtext)

        prompt = section(glines, glabels, "promptModule")
        check("a comma-separated list splits without breaking a version range or extras list",
              'set "module=!module:, = !"' in prompt and "|" not in prompt
              and "delims==" in prompt
              and all(f"!module:{c}=!" in prompt for c in "<>["))

except Exception as e:
    lines.append(f"FAIL setup: {e!r}")
    lines.append(traceback.format_exc())

ok = all(line.startswith("OK") for line in lines)
for line in lines:
    print(line)
print(f"===RESULT: {'PASS' if ok else 'FAIL'}===")
