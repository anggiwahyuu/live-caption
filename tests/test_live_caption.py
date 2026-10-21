"""Tests for live_caption.py. Run with: .venv/bin/python -m unittest discover -s tests -v

No Whisper model is loaded: the transcriber is tested with a fake model. Tests that record
real audio need parec / pw-record (Linux) and are skipped when those aren't usable.
"""

from __future__ import annotations

import datetime as dt
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import live_caption as lc  # noqa: E402

SPEECH = (0.1 * np.sin(2 * np.pi * 220 * np.arange(lc.FRAME_SAMPLES) / lc.SAMPLE_RATE)).astype(np.float32)
SILENCE = np.zeros(lc.FRAME_SAMPLES, dtype=np.float32)


def frames(ms: int) -> int:
    return ms // lc.FRAME_MS


class FrameCounter:
    """Stands in for a Segmenter and checks every frame it receives."""

    label = "Test"

    def __init__(self):
        self.frames = 0
        self.bad = 0

    def feed(self, frame: np.ndarray) -> None:
        self.frames += 1
        if len(frame) != lc.FRAME_SAMPLES or frame.dtype != np.float32:
            self.bad += 1

    def flush(self) -> None:
        pass


def drain(hub: lc.AudioHub) -> list:
    items = []
    while (item := hub.get(timeout=0)) is not None:
        items.append(item)
    return items


# --------------------------------------------------------------------------- audio pipeline

class AudioHubTest(unittest.TestCase):
    def test_keeps_only_latest_draft_per_source(self):
        hub = lc.AudioHub()
        hub.put_draft("A", 0, np.ones(1))
        hub.put_draft("A", 0, np.ones(2))
        kinds = [(kind, len(audio)) for kind, _, _, audio, _ in drain(hub)]
        self.assertEqual(kinds, [("draft", 2)])

    def test_finals_come_before_drafts_and_replace_stale_draft(self):
        hub = lc.AudioHub()
        hub.put_draft("A", 0, np.ones(1))
        hub.put_draft("B", 3, np.ones(1))
        hub.put_final("A", 0, np.ones(1), dt.datetime.now())
        got = [(kind, label) for kind, label, *_ in drain(hub)]
        self.assertEqual(got, [("final", "A"), ("draft", "B")])  # A's draft for the same sentence is dropped


class SegmenterTest(unittest.TestCase):
    def make(self, hub):
        return lc.Segmenter("A", hub, threshold=0.008, silence_ms=700, max_seconds=10, draft_interval=0.25)

    def test_drafts_while_speaking_then_final_on_pause(self):
        hub = lc.AudioHub()
        seg = self.make(hub)
        for _ in range(frames(1000)):
            seg.feed(SPEECH)
        mid = drain(hub)
        self.assertTrue(mid and all(kind == "draft" for kind, *_ in mid))

        for _ in range(frames(800)):
            seg.feed(SILENCE)
        end = drain(hub)
        self.assertEqual([kind for kind, *_ in end], ["final"])
        _, _, seg_id, audio, ts = end[0]
        self.assertEqual(seg_id, 0)
        self.assertGreater(len(audio), lc.SAMPLE_RATE)  # speech + preroll + trailing silence
        self.assertIsInstance(ts, dt.datetime)

    def test_ignores_clicks_and_short_noises(self):
        hub = lc.AudioHub()
        seg = self.make(hub)
        for burst_ms in (60, 150):  # too short to start speech / too short to count as a sentence
            for _ in range(frames(burst_ms)):
                seg.feed(SPEECH)
            for _ in range(frames(800)):
                seg.feed(SILENCE)
        self.assertEqual(drain(hub), [])

    def test_long_sentence_is_split_at_max_seconds(self):
        hub = lc.AudioHub()
        seg = lc.Segmenter("A", hub, threshold=0.008, silence_ms=700, max_seconds=2, draft_interval=0)
        for _ in range(frames(5000)):
            seg.feed(SPEECH)
        finals = [item for item in drain(hub) if item[0] == "final"]
        self.assertEqual([item[2] for item in finals], [0, 1])


class ResamplerTest(unittest.TestCase):
    def test_48k_stereo_to_16k_mono_keeps_level_and_pitch(self):
        resample = lc.Resampler(48_000, 2)
        t = np.arange(48_000) / 48_000
        stereo = np.stack([np.sin(2 * np.pi * 440 * t)] * 2, axis=1).astype(np.float32).reshape(-1)
        block = 2 * 1440  # 30 ms of interleaved stereo, like an audio callback delivers
        out = np.concatenate([resample(stereo[i:i + block]) for i in range(0, len(stereo), block)])

        self.assertEqual(out.dtype, np.float32)
        self.assertAlmostEqual(len(out), lc.SAMPLE_RATE, delta=100)
        self.assertAlmostEqual(float(np.abs(out).max()), 1.0, delta=0.02)  # no +3 dB from the downmix
        spectrum = np.abs(np.fft.rfft(out[2000:14000]))
        self.assertAlmostEqual(np.argmax(spectrum) * lc.SAMPLE_RATE / 12000, 440, delta=2)

    def test_16k_mono_passes_through(self):
        block = np.arange(10, dtype=np.float32)
        np.testing.assert_array_equal(lc.Resampler(lc.SAMPLE_RATE, 1)(block), block)


class AudioCaptureTest(unittest.TestCase):
    def make(self, source="system", device=None):
        seg, stop, msgs = FrameCounter(), threading.Event(), []
        cap = lc.AudioCapture(source, device, seg, stop, lambda msg, kind="error": msgs.append((kind, msg)))
        return cap, seg, stop, msgs

    def test_rechunks_blocks_of_any_size(self):
        cap, seg, _, _ = self.make()
        for n in (100, 1000, 7, 4000, 333):
            cap._feed(np.zeros(n, dtype=np.float32))
        full, rest = divmod(5440, lc.FRAME_SAMPLES)
        self.assertEqual((seg.frames, len(cap._pending), seg.bad), (full, rest, 0))

    def test_falls_back_when_first_backend_fails_before_audio(self):
        cap, seg, stop, msgs = self.make()

        def broken():
            raise FileNotFoundError("no such command")
            yield  # pragma: no cover  (makes this a generator)

        def working():
            for _ in range(5):
                yield SILENCE

        cap._backends = lambda: [("first", broken), ("second", working)]
        cap.run()
        self.assertEqual(seg.frames, 5)
        self.assertEqual(len(msgs), 1)
        kind, msg = msgs[0]
        self.assertEqual(kind, "info")
        self.assertIn("first unavailable", msg)
        self.assertIn("switching to second", msg)

    def test_does_not_switch_backend_after_audio_started(self):
        cap, seg, stop, msgs = self.make()

        def dies_midway():
            yield SILENCE
            raise RuntimeError("device unplugged")

        def never_used():
            raise AssertionError("should not be tried")
            yield  # pragma: no cover

        cap._backends = lambda: [("first", dies_midway), ("second", never_used)]
        cap.run()
        self.assertEqual(seg.frames, 1)
        self.assertEqual(msgs[0][0], "error")
        self.assertIn("device unplugged", msgs[0][1])

    def test_reports_error_when_last_backend_fails(self):
        cap, _, _, msgs = self.make()

        def broken():
            raise RuntimeError("no BlackHole")
            yield  # pragma: no cover

        cap._backends = lambda: [("only", broken)]
        cap.run()
        self.assertEqual(msgs, [("error", "Recording 'Test' stopped (only): no BlackHole")])

    def test_pw_record_command_for_specific_monitor(self):
        cap, *_ = self.make(device="alsa_output.usb-headset.analog-stereo.monitor")
        commands = []
        cap._blocks_subprocess = lambda cmd: commands.append(cmd) or iter(())
        cap._blocks_pw_record()
        cmd = commands[0]
        self.assertIn("stream.capture.sink=true", cmd)
        self.assertEqual(cmd[cmd.index("--target") + 1], "alsa_output.usb-headset.analog-stereo")
        self.assertEqual(cmd[-1], "-")


def _command_ok(*cmd: str) -> bool:
    if shutil.which(cmd[0]) is None:
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(lc.IS_LINUX, "Linux recorders")
class LinuxRecorderTest(unittest.TestCase):
    """Records ~1 s of real audio (silence is fine) through each Linux backend."""

    def record(self, source: str, backend: str, secs: float = 1.0):
        seg, stop, msgs = FrameCounter(), threading.Event(), []
        cap = lc.AudioCapture(source, None, seg, stop, lambda msg, kind="error": msgs.append((kind, msg)))
        if backend:
            cap._backends = lambda: [(backend, getattr(cap, f"_blocks_{backend.replace('-', '_')}"))]
        cap.start()
        time.sleep(secs)
        stop.set()
        cap.stop()
        cap.join(3)
        self.assertEqual(seg.bad, 0)
        self.assertGreater(seg.frames, frames(int(secs * 1000)) * 0.6, msgs)
        return msgs

    @unittest.skipUnless(_command_ok("pactl", "info"), "no PulseAudio / pipewire-pulse server")
    def test_parec(self):
        for source in ("system", "mic"):
            with self.subTest(source=source):
                self.assertEqual(self.record(source, "parec"), [])

    @unittest.skipUnless(_command_ok("pw-cli", "info", "0"), "PipeWire not running")
    def test_pw_record(self):
        for source in ("system", "mic"):
            with self.subTest(source=source):
                self.assertEqual(self.record(source, "pw-record"), [])

    @unittest.skipUnless(shutil.which("parec") and _command_ok("pw-cli", "info", "0"), "needs parec and PipeWire")
    def test_parec_without_server_falls_back_to_pw_record(self):
        old = os.environ.get("PULSE_SERVER")
        os.environ["PULSE_SERVER"] = "unix:/nonexistent"  # parec can't connect, like a system without pipewire-pulse
        try:
            msgs = self.record("system", None)
        finally:
            if old is None:
                os.environ.pop("PULSE_SERVER")
            else:
                os.environ["PULSE_SERVER"] = old
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0][0], "info")
        self.assertIn("switching to pw-record", msgs[0][1])


# --------------------------------------------------------------------------- transcriber

class TranscriberTest(unittest.TestCase):
    """Runs Transcriber.run with fake models: checks draft/final routing and the language hand-off."""

    def run_transcriber(self, items, outputs, lang="auto", same_model=False):
        main_model, draft_model = object(), object()
        calls = []

        def fake_transcribe(model, audio, lang_arg, final):
            calls.append(("main" if model is main_model else "draft", lang_arg, final))
            text, language = outputs.pop(0)
            return [types.SimpleNamespace(text=f" {text} ")], types.SimpleNamespace(language=language)

        args = types.SimpleNamespace(lang=lang, model="small", draft_model="base", device="auto",
                                     _whisper_transcribe=fake_transcribe)
        hub, ui_q, stop = lc.AudioHub(), queue.Queue(), threading.Event()
        tr = lc.Transcriber(args, hub, ui_q, stop)
        tr._load = lambda: (main_model, main_model if same_model else draft_model, "cuda")

        thread = threading.Thread(target=tr.run, daemon=True)
        thread.start()
        for put, *params in items:
            getattr(hub, put)(*params)
            time.sleep(0.3)
        stop.set()
        thread.join(2)

        msgs = []
        while not ui_q.empty():
            msgs.append(ui_q.get())
        return calls, [m for m in msgs if m[0] != "status"]

    def test_drafts_use_draft_model_and_finals_use_main_model(self):
        audio = np.zeros(lc.SAMPLE_RATE * 4, dtype=np.float32)
        now = dt.datetime.now()
        calls, msgs = self.run_transcriber(
            [("put_draft", "Participant", 0, audio),
             ("put_final", "Participant", 0, audio, now),
             ("put_draft", "Participant", 1, audio)],
            outputs=[("Halo", "id"), ("Halo semuanya.", "id"), ("Agenda", "id")])

        self.assertEqual(calls, [("draft", None, False),   # language unknown yet
                                 ("main", None, True),
                                 ("draft", "id", False)])  # reuses the final's detected language
        self.assertEqual([m[0] for m in msgs], ["draft", "line", "draft"])
        self.assertEqual(msgs[1][1:], (now, "Participant", "Halo semuanya.", "id", 0))

    def test_fixed_language_is_always_passed(self):
        audio = np.zeros(lc.SAMPLE_RATE, dtype=np.float32)
        calls, _ = self.run_transcriber([("put_draft", "Me", 0, audio)], outputs=[("Hello", "en")], lang="en")
        self.assertEqual(calls, [("draft", "en", False)])

    def test_hallucination_on_short_final_is_dropped(self):
        short = np.zeros(lc.SAMPLE_RATE, dtype=np.float32)
        _, msgs = self.run_transcriber([("put_final", "Participant", 0, short, dt.datetime.now())],
                                       outputs=[("Terima kasih.", "id")])
        self.assertEqual(msgs, [("drop", "Participant", 0)])


# --------------------------------------------------------------------------- translation & transcript

class TranslatorTest(unittest.TestCase):
    def test_skips_empty_text_and_target_language(self):
        tr = lc.Translator("id", "google")
        self.assertIsNone(tr.translate("", "en"))
        self.assertIsNone(tr.translate("Selamat pagi", "id"))
        self.assertIsNone(lc.Translator("pt-BR", "google").translate("Bom dia", "pt"))

    def test_failure_is_reported_inline(self):
        tr = lc.Translator("id", "google")
        tr._via_google = lambda text: (_ for _ in ()).throw(ConnectionError("offline"))
        self.assertEqual(tr.translate("Hello", "en"), "[translation failed: offline]")


class TranscriptTest(unittest.TestCase):
    def test_markdown_format(self):
        app = types.SimpleNamespace(
            started=dt.datetime(2026, 10, 21, 14, 0),
            records={0: {"ts": dt.datetime(2026, 10, 21, 14, 2, 15), "label": "Participant",
                         "text": "Good morning.", "tr": "Selamat pagi."},
                     1: {"ts": dt.datetime(2026, 10, 21, 14, 2, 20), "label": "Me",
                         "text": "Hi.", "tr": None}})
        self.assertEqual(lc.App._render(app),
                         "# Meeting transcript — October 21, 2026, 14:00\n\n"
                         "- **[14:02:15] Participant:** Good morning.\n"
                         "  - _Selamat pagi._\n"
                         "- **[14:02:20] Me:** Hi.\n")


class AppScrollTest(unittest.TestCase):
    """A new translation must not scroll the window down while the user reads older lines."""

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        args = types.SimpleNamespace(out_dir=self.tmp.name, geometry="400x150", opacity=1.0, font_size=11)
        self.app = lc.App(self.root, args, queue.Queue(), queue.Queue(), lc.Translator("id", "google"), self.root.destroy)

    def tearDown(self):
        self.root.destroy()
        self.tmp.cleanup()

    def test_translation_keeps_scroll_position(self):
        for i in range(60):
            self.app._add_line(dt.datetime.now(), "Participant", f"Sentence {i}", "en", i)
        self.app.text.yview_moveto(0)
        self.root.update()
        self.app._add_translation(0, "Kalimat 0")
        self.root.update()
        self.assertEqual(self.app.text.yview()[0], 0.0)

    def test_translation_follows_when_at_bottom(self):
        for i in range(60):
            self.app._add_line(dt.datetime.now(), "Participant", f"Sentence {i}", "en", i)
        self.app.text.see("end")
        self.root.update()
        self.app._add_translation(59, "Kalimat 59")
        self.root.update()
        self.assertGreater(self.app.text.yview()[1], 0.98)


if __name__ == "__main__":
    unittest.main()
