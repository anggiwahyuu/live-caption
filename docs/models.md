# Models and GPUs

Which Whisper model to use, and how the app uses each kind of GPU. Back to the [README](../README.md).

## Choosing a model for your VRAM

On an NVIDIA GPU (GTX 1050 as the example):

| VRAM | Recommended model | Notes |
|---|---|---|
| 2 GB | `small` (default) | Fast, good enough accuracy for Indonesian/English |
| 3–4 GB | `medium` | More accurate, slightly slower |
| 4 GB | `large-v3-turbo` | Worth a try; if you see "N sentences behind", drop to `medium` |

```bash
./run.sh --model medium --lang id
```

`--model` is used for the final text. Drafts still use a small model (`--draft-model base`) so captions stay live. Watch VRAM usage with `watch -n1 nvidia-smi` while the app is running. To make it permanent, put `model = "medium"` in [`config.toml`](../README.md#saving-your-settings).

## GPUs other than NVIDIA (AMD, Intel, Apple)

faster-whisper, the engine used on NVIDIA GPUs, can't use other GPUs. On every other computer the app uses **whisper.cpp** instead, which runs on:

- **AMD and Intel GPUs** through **Vulkan** (Linux and Windows): Radeon RX cards, Radeon graphics in Ryzen laptops, Intel Arc and Iris Xe;
- the **Apple Silicon GPU** through **Metal** (M1 and newer);
- the **CPU**, where it's still about twice as fast as faster-whisper for a short sentence.

`./setup.sh` / `setup.ps1` set it up when there's no working NVIDIA GPU: they download whisper.cpp (built from the official source for this project, see [Building whisper.cpp](development.md#building-whispercpp)) into `~/.local/share/live-caption/whisper.cpp` (Windows: `%LOCALAPPDATA%\live-caption\whisper.cpp`), download the model (`small` is a 260 MB file), and then **measure** each GPU and the CPU with a test sentence. The fastest is saved in `config.toml`, because built-in graphics can be slower than the CPU. Measured on one laptop with the `small` model, per 3-second sentence:

| Device | Time |
|---|---|
| NVIDIA GTX 1050, faster-whisper (CUDA) | 0.37 s |
| NVIDIA GTX 1050, whisper.cpp (Vulkan) | 0.27 s |
| Intel Core i7-9750H CPU, whisper.cpp | 0.79 s |
| Intel Core i7-9750H CPU, faster-whisper | 1.48 s |
| Intel UHD 630 (built-in graphics), whisper.cpp (Vulkan) | 1.40 s: slower than the CPU, so setup picks the CPU there |

A separate AMD card is much faster than a GTX 1050, so `small` or `medium` should keep up easily. On Linux, setup also installs the Vulkan driver if it's missing (Mesa's `mesa-vulkan-drivers`; on Arch `vulkan-radeon` / `vulkan-intel`). Without one, whisper.cpp uses the CPU.

| Option | Meaning |
|---|---|
| `--engine auto` | Default: faster-whisper on a working NVIDIA GPU, else whisper.cpp if setup installed it, else faster-whisper on the CPU |
| `--engine whisper.cpp` | whisper.cpp even with an NVIDIA GPU (it can be the faster one, see the table) |
| `--whispercpp-device gpu:1` | Use GPU number 1 (`cpu` for the CPU, `auto` for the first separate GPU). Setup saves the fastest one |

The status line shows where it runs, e.g. `Ready — small + draft base @ Vulkan (AMD Radeon RX 6600)`. To measure again after changing the GPU, delete the `whispercpp-device` line from `config.toml` and run setup again. If whisper-server stops during a meeting (e.g. the GPU driver resets), the app starts it again with the next sentence.

Differences from faster-whisper: `--vocab` words are given to whisper.cpp as an initial prompt (it has no hotwords), which helps less with spelling; and the models are separate files (`--model` takes the same names: `tiny`, `base`, `small`, `medium`, `large-v3-turbo`; `distil-*` models don't exist for whisper.cpp).

## Without a usable GPU

Everything works on the CPU, only slower: on a 9th-gen Core i7 the `small` model takes about 0.8 s per sentence with whisper.cpp and 1.5 s with faster-whisper. If captions fall behind ("N sentences behind"), use `--model base`, `--draft-model tiny`, or `--draft-interval 0.5`. `--device cpu` forces the CPU even when a GPU is there.
