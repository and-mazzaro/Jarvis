"""
Jarvis — main orchestrator.

State machine:  idle → listening → transcribing → retrieving → generating → speaking → idle

Runs two parallel tasks:
  1. Voice loop (synchronous, in a thread executor)
  2. WebSocket + HTTP servers (asyncio)
"""
import asyncio
import json
import logging
import os
import sys
import threading
import signal
import concurrent.futures
import re
from pathlib import Path
from memory.supabase_memory import JarvisMemory
from prompt_builder import build_system_prompt

# ---------------------------------------------------------------------------
# Resolve project root and add it to sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BACKEND_DIR = PROJECT_ROOT / "backend"
CHROMA_PATH = PROJECT_ROOT / "chroma_db"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("jarvis.main")

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
CONFIG_PATH = BACKEND_DIR / "config.json"
with open(CONFIG_PATH, encoding="utf-8") as _f:
    CONFIG = json.load(_f)

# ---------------------------------------------------------------------------
# Imports (after sys.path setup)
# ---------------------------------------------------------------------------
from llm.ollama_client import OllamaClient
from stt.transcriber import Transcriber
from tts.synthesizer import Synthesizer
from rag.ingestor import Ingestor
from rag.retriever import Retriever
from knowledge.kiwix_client import KiwixClient
import ws_server


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_current_state = "idle"
_loop: asyncio.AbstractEventLoop | None = None


def _set_state(state: str) -> None:
    global _current_state
    _current_state = state
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(ws_server.broadcast(state), _loop)
    logger.info("State → %s", state)


# ---------------------------------------------------------------------------
# Voice loop (runs in a background thread)
# ---------------------------------------------------------------------------

def voice_loop(
    transcriber: Transcriber,
    retriever: Retriever,
    kiwix: KiwixClient,
    llm: OllamaClient,
    synthesizer: Synthesizer,
    memory: JarvisMemory,
) -> None:
    logger.info("Voice loop started.")
    while True:
        try:
            _set_state("idle")

            # 1. Listen
            _set_state("listening")
            query = transcriber.listen_and_transcribe()
            if not query.strip():
                continue

            # 2. Transcribed — retrieve context
            _set_state("transcribing")
            logger.info("Query: %r", query)

            _set_state("retrieving")
            # AGGIUNTO FASE 2: RAG asincrono in parallelo
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_rag = executor.submit(retriever.build_context, query)
                future_wiki = executor.submit(kiwix.search, query) if kiwix.is_alive() else None
                
                try:
                    rag_context, used_rag = future_rag.result(timeout=2.0)
                except Exception as e:
                    logger.warning("RAG timeout o errore: %s", e)
                    rag_context, used_rag = "", False
                
                wiki_text = ""
                if future_wiki:
                    try:
                        wiki_text = future_wiki.result(timeout=2.0)
                    except Exception as e:
                        logger.warning("Kiwix timeout o errore: %s", e)

            context_parts: list[str] = []
            if used_rag:
                context_parts.append(rag_context)
            if wiki_text:
                context_parts.append(f"[Wikipedia excerpt]\n{wiki_text}")

            context = "\n\n".join(context_parts) if context_parts else None

            # 3. Generate
            _set_state("generating")
            
            # AGGIUNTO FASE 2: Memoria e Prompt Dinamico
            memory.add_message("user", query)
            system_prompt = build_system_prompt(memory)
            history = memory.get_context_messages()
            
            response_tokens: list[str] = []
            sentence_buffer = ""
            first_sentence = True
            
            
            # Passiamo history e system_prompt (MODIFICATO FASE 2)
            for token in llm.generate_stream(query, context, system_prompt=system_prompt, history=history):
                response_tokens.append(token)
                sentence_buffer += token
                
                # Check for sentence endings (. ? ! followed by space or newline)
                # We use a regex to find the first occurrence and split
                match = re.search(r'([.?!]+)(\s+)', sentence_buffer)
                if match:
                    end_pos = match.end()
                    sentence = sentence_buffer[:end_pos].strip()
                    sentence_buffer = sentence_buffer[end_pos:]
                    
                    if sentence:
                        if first_sentence:
                            # The first sentence triggers the "speaking" state in UI
                            synthesizer.enqueue(sentence, on_start=lambda: _set_state("speaking"))
                            first_sentence = False
                        else:
                            synthesizer.enqueue(sentence)

                # Stream partial text to UI
                partial = "".join(response_tokens)
                if _loop and not _loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        ws_server.broadcast("generating", {"partial": partial}),
                        _loop,
                    )

            # Enqueue any remaining text
            if sentence_buffer.strip():
                if first_sentence:
                    synthesizer.enqueue(sentence_buffer.strip(), on_start=lambda: _set_state("speaking"))
                else:
                    synthesizer.enqueue(sentence_buffer.strip())

            response_text = "".join(response_tokens).strip()
            # AGGIUNTO FASE 2: Salva risposta assistente
            memory.add_message("assistant", response_text)
            
            logger.info("Response complete. Waiting for TTS playback ...")

            # 4. Wait for all sentences to be spoken before going back to idle/listening
            synthesizer.wait_until_done()
            logger.info("TTS playback done.")


        except KeyboardInterrupt:
            logger.info("Voice loop interrupted.")
            break
        except Exception as exc:
            logger.error("Voice loop error: %s", exc, exc_info=True)
            _set_state("idle")


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------

async def main() -> None:
    global _loop
    _loop = asyncio.get_running_loop()

    logger.info("Initialising Jarvis …")

    # Instantiate components
    llm = OllamaClient(CONFIG["llm"])
    if not llm.is_alive():
        logger.warning("Ollama not reachable at %s — LLM will fail!", CONFIG["llm"]["base_url"])

    ingestor = Ingestor(CONFIG["rag"], CHROMA_PATH)
    retriever = Retriever(CONFIG["rag"], CHROMA_PATH)
    kiwix = KiwixClient(CONFIG["kiwix"])

    synthesizer = Synthesizer(CONFIG["tts"], PROJECT_ROOT)
    transcriber = Transcriber(CONFIG["stt"])

    # AGGIUNTO FASE 2: Inizializzazione Memoria
    memory = JarvisMemory()
    
    # AGGIUNTO FASE 2: Pre-warming LLM
    def prewarm_llm(ollama_client):
        try:
            ollama_client.generate(user_message="Ciao", max_tokens=1)
            logger.info("Modello pre-caricato in RAM.")
        except Exception as e:
            logger.debug("Errore pre-warm LLM: %s", e)
    threading.Thread(target=prewarm_llm, args=(llm,), daemon=True).start()

    # AGGIUNTO FASE 2: Signal Handling
    def on_shutdown(sig, frame):
        logger.info("Jarvis: chiusura in corso, salvo la sessione...")
        memory.end_session(llm)
        sys.exit(0)

    signal.signal(signal.SIGINT, on_shutdown)
    signal.signal(signal.SIGTERM, on_shutdown)

    # Start voice loop in background thread
    vl_thread = threading.Thread(
        target=voice_loop,
        args=(transcriber, retriever, kiwix, llm, synthesizer, memory),
        daemon=True,
    )
    vl_thread.start()

    # Run servers (blocks forever)
    await ws_server.run_servers(
        ws_port=CONFIG["server"]["ws_port"],
        http_port=CONFIG["server"]["http_port"],
        ingestor=ingestor,
        retriever=retriever,
        kiwix_client=kiwix,
        frontend_dist=PROJECT_ROOT / "frontend" / "dist",
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Jarvis stopped.")
