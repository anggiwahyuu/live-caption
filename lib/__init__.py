"""
Live Caption — real-time speech-to-text for online meetings on Linux, Windows and macOS.

Pipeline (module in brackets):
  AudioCapture (system audio and/or mic)                                                    [audio]
      Linux: parec, falling back to pw-record · Windows: PyAudioWPatch (WASAPI loopback)
      macOS: sounddevice (system audio through the BlackHole virtual device)
    -> Segmenter (draft every ~0.25 s while speaking + final on pause)                     [audio]
    -> AudioHub (finals are always processed, stale drafts are dropped so captions never fall behind)  [audio]
    -> faster-whisper (STT, GPU via CUDA or CPU fallback; small model for drafts, main model for finals)  [transcriber]
    -> TranslationWorker (optional, can be toggled from the UI)                            [translation]
    -> Tkinter overlay + transcript auto-saved to a .md file                               [ui]
    -> Summarizer (on demand: summary, decisions and action items via Ollama or Claude)    [summary]

Also: subtitles (SRT/VTT export), config (command line, config.toml, window state),
common (shared constants), main (wires it all together).
"""
