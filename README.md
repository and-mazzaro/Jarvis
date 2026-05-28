# Jarvis Windows — Assistente vocale ibrido (DeepSeek V4 Flash)

Assistente vocale per Windows 10/11: **STT, TTS, RAG e Wikipedia restano locali**; le risposte intelligenti passano dall’API cloud **DeepSeek V4 Flash** (veloce ed economica). Niente più Ollama né modelli LLM in locale.

**Flusso:** parli → Whisper trascrive (locale) → contesto da documenti (ChromaDB) / Wikipedia (Kiwix) → **DeepSeek V4 Flash** risponde in streaming → Kokoro sintetizza la voce (locale).

---

## Stack tecnologico

| Layer | Tecnologia | Dove gira |
|-------|-----------|-----------|
| **LLM** | [DeepSeek API](https://api.deepseek.com) — modello `deepseek-v4-flash` | Cloud |
| **STT** | faster-whisper + webrtcvad | Locale (CPU) |
| **TTS** | kokoro-onnx (`im_nicola`) | Locale |
| **RAG** | ChromaDB + `paraphrase-multilingual-MiniLM-L12-v2` | Locale |
| **Wikipedia** | kiwix-serve + file ZIM | Locale |
| **UI** | Electron 28 + Three.js | Locale |
| **Backend** | Python asyncio + websockets + aiohttp | Locale |
| **Memoria** | Supabase (sessioni, profilo) | Cloud |

---

## Novità e ottimizzazioni

- **DeepSeek V4 Flash**: sostituisce Ollama e modelli locali pesanti; latenza molto inferiore, zero VRAM dedicata al LLM.
- **Modalità non-thinking** (`thinking: disabled`): risposte più rapide per l’uso vocale.
- **Client dedicato** (`backend/llm/deepseek_client.py`): streaming SSE, sessione HTTP riutilizzata, pre-warm all’avvio.
- **Nessun download LLM** in `setup.bat`: basta la chiave API in `.env`.
- **Indicatori UI**: pillola “DeepSeek V4” verde se `DEEPSEEK_API_KEY` è configurata.
- **Isolamento processi Electron**: solo `python.exe` e `kiwix-serve.exe` avviati da Jarvis.
- **Upload multipart** `/api/ingest/upload` per indicizzare documenti.
- **Animazione orb in pausa** quando si apre il pannello Knowledge.

---

## Avvio rapido

### Prerequisiti

| Tool | Link |
|------|------|
| Python 3.11 | https://www.python.org/downloads/release/python-3119/ |
| Node.js (Electron) | https://nodejs.org/ |
| Chiave DeepSeek | https://platform.deepseek.com/ |
| Kiwix-serve (opzionale) | https://www.kiwix.org/en/downloads/kiwix-serve |

### 1 — File `.env`

Copia `.env.example` in `.env` e compila:

```ini
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-key
JARVIS_CONTEXT_MESSAGES=20
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

### 2 — Setup una tantum

```bat
setup.bat
```

### 3 — File TTS (root progetto)

- `kokoro-v1.0.onnx`
- `voices-v1.0.json`  
  Da: https://huggingface.co/hexgrad/Kokoro-82M

### 4 — Avvio

```bat
launch.bat
```

Oppure: `npm install` → `npm start` (Electron avvia anche il backend se non usi `JARVIS_CONSOLE`).

### Test backend (senza microfono)

```bat
.venv\Scripts\activate
python test_backend.py
```

---

## Configurazione LLM (`backend/config.json`)

```json
"llm": {
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "temperature": 0.5,
  "max_tokens": 300,
  "thinking": "disabled",
  "stream": true
}
```

Per compiti più complessi (non vocale) puoi usare `deepseek-v4-pro` nello stesso file.

---

## Come ottenere l’API DeepSeek V4 Flash

1. **Registrati** su [DeepSeek Platform](https://platform.deepseek.com/).
2. **Ricarica credito** (Billing → Top up): l’API è a consumo, costi molto bassi per `deepseek-v4-flash`.
3. **Crea una chiave**: menu **API Keys** → **Create new API key**.
4. **Copia subito** la chiave (`sk-...`); non viene mostrata di nuovo.
5. **Incolla in `.env`**:
   ```ini
   DEEPSEEK_API_KEY=sk-...
   ```
6. **Riavvia** `launch.bat`. La pillola **DeepSeek V4** nell’interfaccia diventa verde.

**Endpoint ufficiale:** `https://api.deepseek.com`  
**Nome modello:** `deepseek-v4-flash` (sostituisce il legacy `deepseek-chat`, deprecato dopo luglio 2026).

Documentazione: [DeepSeek API Docs](https://api-docs.deepseek.com/)

---

## Licenza

MIT
