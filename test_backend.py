"""
test_backend.py — Smoke test for the Jarvis backend (no microphone needed).

Run from the project root with the venv active:
    python test_backend.py

Checks:
  1. Config file loads correctly
  2. Ollama is reachable and the target model exists
  3. LLM can generate a short response
  4. RAG ingestor + retriever initialise without errors
  5. Kiwix connectivity (optional — skipped if offline)
  6. TTS model files are present
"""

import json
import sys
import traceback
from pathlib import Path

# -- resolve paths ----------------------------------------------------------

ROOT    = Path(__file__).parent.resolve()
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

CHROMA  = ROOT / "chroma_db"
CONFIG_PATH = BACKEND / "config.json"

PASS  = "  [PASS]"
FAIL  = "  [FAIL]"
SKIP  = "  [SKIP]"

errors = 0


def check(name: str):
    """Decorator-style context manager for a named test."""
    class _CM:
        def __enter__(self):
            print(f"[{name}]", end=" ", flush=True)
            return self
        def __exit__(self, exc_type, exc_val, tb):
            global errors
            if exc_type is None:
                print(PASS)
            else:
                print(f"{FAIL}\n       {exc_val}")
                errors += 1
            return True   # suppress exception
    return _CM()


# -- 1. Config --------------------------------------------------------------

with check("Config loads"):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    assert "llm" in cfg and "stt" in cfg and "rag" in cfg

# -- 2. Ollama reachable ----------------------------------------------------

with check("Ollama reachable"):
    from llm.ollama_client import OllamaClient
    llm = OllamaClient(cfg["llm"])
    assert llm.is_alive(), "Ollama not running - start Ollama and retry."

# -- 3. LLM generates ------------------------------------------------------

with check("LLM generates text"):
    response = llm.generate("Say the word HELLO and nothing else.")
    assert "HELLO" in response.upper(), f"Unexpected: {response!r}"
    print(f"        -> {response[:80]!r}", end="")

# -- 4. RAG ingestor + retriever --------------------------------------------

with check("RAG ingestor init"):
    from rag.ingestor import Ingestor
    ingestor = Ingestor(cfg["rag"], CHROMA)

with check("RAG retriever init"):
    from rag.retriever import Retriever
    retriever = Retriever(cfg["rag"], CHROMA)
    # query against (possibly empty) DB should not crash
    results = retriever.retrieve("test query")
    assert isinstance(results, list)

# -- 5. Kiwix (optional) ----------------------------------------------------

with check("Kiwix-serve reachable (optional)"):
    from knowledge.kiwix_client import KiwixClient
    kiwix = KiwixClient(cfg["kiwix"])
    if not kiwix.is_alive():
        print(f"{SKIP} - kiwix-serve offline (that's OK)", end="")
        # force skip without counting as error
        errors -= 0  # no error was raised, just informational

# -- 6. TTS model files ----------------------------------------------------

with check("TTS model files present"):
    onnx   = ROOT / cfg["tts"]["model"]
    voices = ROOT / cfg["tts"]["voices"]
    if not onnx.exists() or not voices.exists():
        missing = [p for p in [onnx, voices] if not p.exists()]
        raise FileNotFoundError(
            f"Missing: {[m.name for m in missing]}\n"
            "       Download from https://huggingface.co/fastrtc/kokoro-onnx"
        )

# -- Summary ---------------------------------------------------------------

print()
print("-" * 50)
if errors == 0:
    print("All checks passed - Jarvis backend is ready!")
else:
    print(f"{errors} check(s) failed - fix the issues above before running launch.bat.")
print("-" * 50)
sys.exit(errors)
