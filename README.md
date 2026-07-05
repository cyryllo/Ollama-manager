# Ollama Manager

A desktop app (PyQt6) for KDE that started as a simple [Ollama](https://ollama.com) service
manager and grew into a control panel for a whole local AI stack: multi-host model management,
a model aggregator (LiteLLM) that exposes one OpenAI-compatible endpoint for tools like
Continue/VS Code, and a browser chat UI (Open WebUI). No terminal, no Docker, no cloud —
everything runs on your LAN.

*(Polska wersja tego pliku: [README_PL.md](README_PL.md))*

## Requirements

- Python 3 + **PyQt6**, **requests**
- **systemd** + **polkit** (`pkexec`) — standard on KDE/Debian
- Ollama (if you don't have it, the app installs it with one click)
- Optional: **uv** (for installing Open WebUI and LiteLLM — the app installs it itself if needed)
- Optional: `ffmpeg`, `pandoc`, `zstd` (full Open WebUI functionality — voice, document RAG)

## Installation and running

First, clone the repository and enter its directory — the install scripts below
expect to be run from there (they look for `ollama_manager.py` next to themselves):

```
git clone https://github.com/cyryllo/Ollama-manager.git
cd Ollama-manager
```

**As a menu app, no root (recommended)** — installs dependencies via pip, copies the app to
`~/.local/share/ollama-manager` and adds a menu entry (Utilities section). Detects an existing
install and offers to update/reinstall accordingly:

```
./install.sh
```

Uninstall with `./install.sh --uninstall`.

**As a `.deb` package (Debian/Ubuntu)** — dependencies come from `apt`, easy to uninstall:

```
./build-deb.sh
sudo apt install ./ollama-manager_*_all.deb
```

**Manually, for development** — no copying, no menu entry:

```
pip install PyQt6 requests
python3 ollama_manager.py
```

## Features

**Ollama service**
- Start / stop the systemd service, live status detection
- Autostart on system boot
- Detects a missing install + a one-click install button

**Models**
- List of installed models + deletion
- Downloading new models with suggestions of popular ones (Llama, Gemma, Mistral, Phi, DeepSeek, Qwen) and a progress bar
- Preview of models currently loaded into memory (VRAM)

**Open WebUI**
- One-click install of the browser chat panel (no Docker)
- Start / stop, autostart on login
- The "Open WebUI" button opens the panel in the browser (no automatic opening)

**Server switcher**
- Choose the Ollama host (localhost or any host on the LAN, e.g. BC-250) for model operations
- Add/remove servers from the window, remembered between runs

**Model aggregator (LiteLLM)**
- One-click install, start/stop and autostart for LiteLLM (no Docker)
- Exposes a single endpoint (OpenAI-compatible) combining models from ALL
  servers on the switcher list — VS Code/Continue only needs to point at this one address
- Preview of which models and hosts will end up in the config, before starting

**Advanced (Ollama environment variables)**
- `OLLAMA_KEEP_ALIVE` — how long a model stays in memory after the last request
- `OLLAMA_CONTEXT_LENGTH` — context window size (the default 4096 is too small for agentic work)
- `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`
- `OLLAMA_VULKAN` — Vulkan backend instead of ROCm (useful on AMD cards without full ROCm support, e.g. BC-250)
- `OLLAMA_IGPU_ENABLE` — whether Ollama may use the integrated GPU (enabled by default)
- Every change writes a systemd override and restarts the service — from the window, no manual file editing

**Stats bar**
- Ollama and Open WebUI status
- VRAM usage on the currently selected server
- Number of installed models

**Event log** — a log of all operations, always visible at the bottom of the window.

**Interface language** — Polish, English, German, Spanish, French, Portuguese and Italian, switchable from the window, remembered between runs.

## Notes

- Ollama service control always targets the local machine — even if a remote
  server (e.g. BC-250) is selected in the window, start/stop/autostart act locally.
