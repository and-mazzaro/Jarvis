# Jarvis Windows — Local AI Voice Assistant

A fully **offline**, local AI voice assistant for Windows 10/11.  
Speak → Jarvis transcribes → retrieves context from your documents / Wikipedia → LLM answers → you hear it.  
No API keys. No cloud. Runs entirely on your machine.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM   | [Ollama](https://ollama.com) + `mistral:7b-instruct` |
| STT   | faster-whisper (`small` model, int8, CPU) + webrtcvad |
| TTS   | kokoro-onnx (`am_fenrir` voice) + sounddevice |
| RAG   | ChromaDB + sentence-transformers `all-MiniLM-L6-v2` |
| Wikipedia | kiwix-serve + local ZIM file |
| UI    | Electron 28 + Three.js particle orb |
| Backend bridge | Python asyncio + websockets + aiohttp |

---

## Quick Start

### Prerequisites

| Tool | Download |
|------|----------|
| Python 3.11 | https://www.python.org/downloads/release/python-3119/ |
| Ollama | https://ollama.com |
| Node.js 20+ (optional, only for Electron dev) | https://nodejs.org |
| Kiwix-serve (optional) | https://www.kiwix.org/en/downloads/kiwix-serve |

### 1 — One-time setup

```bat
setup.bat
```

This will:
- Create a `.venv` (Python 3.11)
- `pip install -r backend/requirements.txt`
- `ollama pull mistral:7b-instruct`
- Create `chroma_db/` and `documents/` directories

### 2 — Download TTS model files

Place these two files in the **project root** (next to `launch.bat`):

| File | Source |
|------|--------|
| `kokoro-v1.0.onnx` | https://huggingface.co/hexgrad/Kokoro-82M |
| `voices-v1.0.bin`  | same repo |

### 3 — (Optional) Wikipedia offline

1. Download a ZIM file from https://wiki.kiwix.org/wiki/Content_in_all_languages  
   (`wikipedia_en_all_nopic` recommended — ~85 GB; use `_mini` for testing)
2. Download [kiwix-serve](https://www.kiwix.org/en/downloads/kiwix-serve) and add it to PATH
3. Set the environment variable before launching:
   ```bat
   set KIWIX_ZIM_PATH=C:\kiwix\wikipedia_en_all_nopic.zim
   ```

### 4 — Launch

```bat
launch.bat
```

Or, with the Electron UI:

```bat
npm install
npm start
```

---

## Project Structure

```
jarvis-windows/
├── backend/
│   ├── main.py                  ← main orchestrator (voice loop)
│   ├── ws_server.py             ← WebSocket + aiohttp REST server
│   ├── config.json              ← all tuneable settings
│   ├── requirements.txt
│   ├── llm/
│   │   └── ollama_client.py     ← streaming Ollama API client
│   ├── stt/
│   │   └── transcriber.py       ← faster-whisper + webrtcvad pipeline
│   ├── tts/
│   │   └── synthesizer.py       ← kokoro-onnx + sounddevice
│   ├── rag/
│   │   ├── ingestor.py          ← document loader + ChromaDB indexer
│   │   └── retriever.py         ← semantic search
│   └── knowledge/
│       └── kiwix_client.py      ← Wikipedia offline search
├── frontend/
│   ├── src/                     ← source files (edit these)
│   │   ├── index.html
│   │   ├── main.js              ← Three.js orb + WebSocket handler
│   │   └── knowledge-panel.js   ← Knowledge Manager UI
│   └── dist/                    ← served by aiohttp (copy of src/)
├── electron/
│   ├── main.js                  ← Electron main process
│   ├── preload.js               ← context bridge
│   ├── loading.html             ← startup loading screen
│   ├── log-window.html          ← log viewer
│   └── electron-builder.yml     ← build config → .exe NSIS installer
├── chroma_db/                   ← ChromaDB storage (auto-created, gitignored)
├── documents/                   ← drop files here to ingest (gitignored)
├── kokoro-v1.0.onnx             ← TTS model (you provide)
├── voices-v1.0.bin              ← TTS voices (you provide)
├── launch.bat                   ← start everything
├── setup.bat                    ← one-time setup
└── package.json                 ← Node / Electron project
```

---

## Configuration (`backend/config.json`)

All parameters are hot-editable — restart `launch.bat` to apply changes.

```jsonc
{
  "llm": {
    "model": "mistral:7b-instruct",   // any model in Ollama
    "temperature": 0.7,
    "max_tokens": 512,
    "system_prompt": "You are Jarvis…"
  },
  "stt": {
    "model": "small",                 // tiny / base / small / medium / large
    "language": "en",                  // null = auto-detect
    "vad_aggressiveness": 2,           // 0-3
    "silence_threshold_ms": 800
  },
  "tts": {
    "voice": "am_fenrir",              // see kokoro-onnx docs for options
    "speed": 1.0
  },
  "rag": {
    "chunk_size": 500,
    "chunk_overlap": 50,
    "top_k": 3,
    "similarity_threshold": 0.4       // lower = more lenient
  },
  "kiwix": {
    "port": 8888,
    "zim_path": "C:/kiwix/…",
    "max_article_chars": 3000
  }
}
```

---

## Knowledge Manager

Open the **Knowledge Manager** panel from the sidebar (book icon):

- **Documents tab** — drag & drop PDF, TXT, or DOCX files to index them.  
  Files are chunked (500 tokens, 50 overlap) and embedded with `all-MiniLM-L6-v2`.  
  Delete a file from the index with the ✕ button.

- **Wikipedia tab** — test Kiwix article retrieval directly; type any topic and see the extracted excerpt.

---

## Voice Loop — State Machine

```
idle → listening → transcribing → retrieving → generating → speaking → idle
```

Each state fires a WebSocket event to the Electron UI, which updates the Three.js orb:

| State | Orb behaviour | Colour |
|-------|--------------|--------|
| idle | slow breathing | dim blue `#1a3a5c` |
| listening | pulse outward | cyan `#00d4ff` |
| thinking (transcribing / retrieving / generating) | fast orbital swirl | amber `#ffaa00` |
| speaking | ripple wave | white `#ffffff` |

---

## Knowledge Retrieval Logic

1. Transcribe speech → text query  
2. Query ChromaDB → top-3 chunks above similarity threshold (0.4)  
3. If RAG score is too low → query kiwix-serve → extract Wikipedia excerpt  
4. Merge context and build prompt:

```
[CONTEXT]
[Source: document.pdf | score: 0.72]
…chunk text…

[Wikipedia excerpt]
…article text…

[USER QUESTION]
What is quantum entanglement?

Answer based on the context above when relevant...
```

5. Stream LLM tokens → partial text sent to UI in real-time  
6. When complete → TTS synthesis → audio playback

---

## Building the Installer

```bat
npm install
npm run build
```

Output: `dist-electron/Jarvis Setup 1.0.0.exe`

---

## Files You Must Provide (not committed)

| File | Where to get it |
|------|----------------|
| `.venv/` | Created by `setup.bat` |
| `kokoro-v1.0.onnx` | https://huggingface.co/hexgrad/Kokoro-82M |
| `voices-v1.0.bin` | same repo |
| Wikipedia ZIM | https://wiki.kiwix.org/wiki/Content_in_all_languages |
| `~/.ollama/` | Auto-created by Ollama on first `pull` |
| `chroma_db/` | Auto-created on first document ingest |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `PyAudio` install fails | Install Microsoft C++ Build Tools or use `pip install pipwin && pipwin install pyaudio` |
| `webrtcvad` install fails | Same — needs C++ compiler; or `pip install webrtcvad-wheels` |
| Ollama not found | Make sure `ollama.exe` is in PATH after installation |
| No audio output | Check `sounddevice` default device: `python -c "import sounddevice; print(sounddevice.query_devices())"` |
| Kiwix offline | Set `KIWIX_ZIM_PATH` env var and make sure `kiwix-serve` is in PATH |
| ChromaDB cold — no chunks | Ingest at least one document via the Knowledge Manager panel |

---

## License

MIT
