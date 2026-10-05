# !/usr/bin/python
# coding=utf-8
"""Audio Clips panel slots (``blendertk.audio_utils.audio_clips``) headless test.

bpy-only: the slots' Qt widgets are stood in for by plain objects (headless
Blender ships no Qt binding), so what is proven is the slot logic over real VSE
sound strips -- ``select_track``, the Shot Manifest's "Open in Audio Clips".

Run headless (fresh instance -- session-safety rule):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background \\
    --factory-startup --python blendertk/test/test_audio_clips.py
"""

import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MONO = os.path.dirname(REPO)
for p in (REPO, os.path.join(MONO, "pythontk")):
    if p not in sys.path:
        sys.path.insert(0, p)


class _Combo:
    """The clips combo's surface the slots use."""

    def __init__(self):
        self.items, self.index = [], -1

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def currentText(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else ""

    def setCurrentIndex(self, index):
        self.index = index

    def clear(self):
        self.items, self.index = [], -1

    def addItems(self, names):
        self.items.extend(names)
        if self.index < 0 and self.items:
            self.index = 0

    def blockSignals(self, _block):
        return False

    def repaint(self):
        pass


class _Spin:
    def __init__(self):
        self.value = None

    def blockSignals(self, _block):
        return False

    def setValue(self, value):
        self.value = value


class _Footer:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


def _run_audio_clips_checks():
    lines = []

    def check(label, cond, detail=""):
        ok = bool(cond)
        lines.append(
            f"{'OK' if ok else 'FAIL'}: {label}"
            + (f" — {detail}" if detail and not ok else "")
        )
        return ok

    import types

    import pythontk as ptk

    from blendertk.audio_utils._audio_utils import AudioUtils
    from blendertk.audio_utils.audio_clips import AudioClipsSlots

    slots = AudioClipsSlots.__new__(AudioClipsSlots)  # no Switchboard headless
    slots.ui = types.SimpleNamespace(
        cmb000=_Combo(), s000=_Spin(), s001=_Spin(), footer=_Footer()
    )
    AudioUtils.remove_all_clips()
    with ptk.TempArtifacts(prefix="btk_audio_clips_") as tmp:
        wav = str(tmp.path(".wav"))
        with wave.open(wav, "wb") as fh:  # a tenth of a second of silence
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(44100)
            fh.writeframes(b"\x00\x00" * 4410)
        AudioUtils.add_clip(wav, frame_start=1, name="early")
        slots._refresh_combo()
        # A strip added after the combo was filled: found on a refresh.
        AudioUtils.add_clip(wav, frame_start=20, name="late")
        found = slots.select_track("late")
        check(
            "select_track: a strip the combo does not list yet is found and shown",
            found
            and slots.ui.cmb000.currentText() == "late"
            and slots.ui.footer.text == "Clip 'late'.",
            f"found={found} combo={slots.ui.cmb000.items} "
            f"current={slots.ui.cmb000.currentText()!r} footer={slots.ui.footer.text!r}",
        )
        missing = slots.select_track("nowhere")
        check(
            "select_track: a name no strip answers to reports it and selects nothing",
            missing is False
            and slots.ui.cmb000.currentText() == "late"
            and "nowhere" in slots.ui.footer.text,
            f"returned={missing} footer={slots.ui.footer.text!r}",
        )
        AudioUtils.remove_all_clips()
    return lines


if __name__ == "__main__":
    try:
        result_lines = _run_audio_clips_checks()
    except Exception as e:  # pragma: no cover - harness failure prints its own trace
        import traceback

        traceback.print_exc()
        result_lines = [f"FAIL: harness raised — {e!r}"]

    print("\n".join(result_lines))
    passed = sum(1 for ln in result_lines if ln.startswith("OK"))
    ok = bool(result_lines) and all(ln.startswith("OK") for ln in result_lines)
    print(f"===RESULT: {'PASS' if ok else 'FAIL'}=== ({passed}/{len(result_lines)})")
