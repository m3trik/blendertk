"""_OutputCapture tests — the transcript buffer that feeds the Script Output console.

Covers what the capture uniquely owns: ANSI scrubbing at ingest, and the log level it
carries alongside each chunk. Both exist because of the tee: it delegates ``isatty()`` to
Blender's system console (a real TTY), so CPython emits its *colored* traceback and the
escape bytes land in the buffer; and past the tee a logging record is just characters, so
the level has to be captured here or not at all.

``_OutputCapture`` is deliberately free of Qt AND bpy (it installs at startup, before any
UI exists), so this suite needs neither; the one ``ScriptConsole`` section needs bpy and
runs only under the Blender harness, like every other suite::

    blender --background --factory-startup --python blendertk/test/test_script_output.py

and equally under the workspace ``.venv``::

    python blendertk/test/test_script_output.py
"""
import os
import sys
import logging
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)

lines = []


def check(label, ok, detail=""):
    lines.append(f"{'OK' if ok else 'FAIL'} {label}" + (f" — {detail}" if not ok and detail else ""))


def transcript(cap):
    """The buffer's text, levels dropped — the console reads `chunks()` (it needs the
    levels), so this join lives here rather than as a second accessor on the capture."""
    return "".join(text for text, _ in cap.chunks())


try:
    from blendertk.env_utils.script_output import _OutputCapture

    # -- ANSI is scrubbed at ingest ------------------------------------------
    cap = _OutputCapture()
    cap._write("\x1b[35mFile\x1b[0m \x1b[1;31mboom\x1b[0m\n")
    check("ANSI escapes are stripped from the buffer", transcript(cap) == "File boom\n",
          repr(transcript(cap)))

    cap = _OutputCapture()
    cap._write("\x1b[0m")  # a chunk that is nothing but escapes
    check("an escapes-only chunk buffers nothing", cap.chunks() == [], repr(cap.chunks()))

    # The cap must count characters a reader will actually see, not escape bytes.
    cap = _OutputCapture()
    cap._write("\x1b[35mabc\x1b[0m\n")
    check("the char cap counts visible text, not escapes", cap._size == 4, f"_size={cap._size}")

    # -- levels ride along with the chunk ------------------------------------
    cap = _OutputCapture()
    cap._write("plain print\n")
    cap._write("ERROR: boom\n", level=logging.ERROR)
    check("chunks carry (text, level); plain writes carry None",
          cap.chunks() == [("plain print\n", None), ("ERROR: boom\n", logging.ERROR)],
          repr(cap.chunks()))
    check("the buffered text is unchanged by the level tagging",
          transcript(cap) == "plain print\nERROR: boom\n", repr(transcript(cap)))

    # -- the logging handler is what supplies the level ----------------------
    cap = _OutputCapture()
    cap.install()
    try:
        seen = []
        cap.set_listener(lambda text, level: seen.append((text, level)))
        log = logging.getLogger("blendertk.test.script_output")
        log.setLevel(logging.DEBUG)
        log.debug("checking for error conditions")
        log.error("real failure")
        check("the logging handler forwards each record's levelno",
              [lv for _, lv in seen] == [logging.DEBUG, logging.ERROR], repr(seen))
        # The point of the level: this DEBUG line says "error" but is not one.
        check("a DEBUG record mentioning 'error' is tagged DEBUG, not ERROR",
              bool(seen) and "error" in seen[0][0] and seen[0][1] == logging.DEBUG,
              repr(seen[:1]))

        # print() has no level — the console falls back to its word/block rules.
        seen.clear()
        print("just a print")
        check("stdout writes carry no level",
              any(text.startswith("just a print") and lv is None for text, lv in seen),
              repr(seen))
    finally:
        cap.uninstall()

    check("uninstall restores the streams", sys.stdout is not cap._tee_out)

    # -- a rebuilt console must recolor history the way it was colored live ---
    cap = _OutputCapture()
    cap._write("a\n", level=logging.DEBUG)
    cap._write("b\n", level=logging.ERROR)
    check("chunks() preserves per-chunk levels for the console to seed from",
          [lv for _, lv in cap.chunks()] == [logging.DEBUG, logging.ERROR],
          repr(cap.chunks()))

    # -- the cap still trims, now over pairs ---------------------------------
    cap = _OutputCapture()
    cap.MAX_CHARS = 20
    for i in range(50):
        cap._write(f"line {i}\n")
    check("the transcript stays capped", len(transcript(cap)) <= 20 + len("line 49\n"),
          f"len={len(transcript(cap))}")
    check("trimming keeps the newest text", transcript(cap).endswith("line 49\n"), repr(transcript(cap)))

    # -- a headless session restores capture only: no window, no dock, no state ---
    # tentacle's register calls ScriptConsole.restore(); with the user's console left open
    # it docked a strip into --background Blender, whose cleanup (screen.area_close) blows
    # the C stack there, and saved visible=True over the user's own GUI state.
    try:
        import bpy
    except ImportError:
        bpy = None
    if bpy is not None:
        import json
        import pythontk as ptk
        from blendertk.env_utils.script_output import ScriptConsole

        store = ptk.TempArtifacts("btk_script_console", policy="scoped")
        ScriptConsole._state_dir_override = store.dir_path()
        state = ScriptConsole._state_path()
        with open(state, "w", encoding="utf-8") as f:
            json.dump({"visible": True, "height": 180}, f)
        with open(state, encoding="utf-8") as f:
            before = f.read()
        window = bpy.context.window_manager.windows[0]
        areas = len(window.screen.areas)
        console = ScriptConsole.restore()
        try:
            check("headless restore starts the capture", console._capture.installed)
            check("headless restore docks nothing",
                  not console.is_open() and len(window.screen.areas) == areas,
                  f"open={console.is_open()} areas {areas} -> {len(window.screen.areas)}")
            with open(state, encoding="utf-8") as f:
                check("headless restore leaves the persisted state alone", f.read() == before)
        finally:
            console._capture.uninstall()
            ScriptConsole._instance = None
            ScriptConsole._state_dir_override = None
            store.cleanup()

except Exception as e:
    traceback.print_exc()
    check("suite raised", False, repr(e))

passed = sum(1 for line in lines if line.startswith("OK"))
for line in lines:
    print(line)
result = "PASS" if lines and all(line.startswith("OK") for line in lines) else "FAIL"
print(f"===RESULT: {result}=== ({passed}/{len(lines)})")
