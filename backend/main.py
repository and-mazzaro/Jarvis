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
from wake_word.detector import WakeWordDetector  # AGGIUNTO FASE 3

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
from knowledge.web_searcher import WebSearcher # AGGIUNTO
import ws_server


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_current_state = "idle"
_loop: asyncio.AbstractEventLoop | None = None
_wake_word_detected = threading.Event()  # AGGIUNTO FASE 3


def _set_state(state: str) -> None:
    global _current_state
    _current_state = state
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(ws_server.broadcast(state), _loop)
    logger.info("State → %s", state)
    
    # Reset event if entering waiting state
    if state == "waiting":
        _wake_word_detected.clear()


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
    wake_detector: WakeWordDetector = None,  # AGGIUNTO FASE 3
    web_searcher: WebSearcher = None,         # AGGIUNTO
) -> None:
    logger.info("Voice loop started.")
    while True:
        try:
            # AGGIUNTO FASE 3: Gestione Wake Word
            if CONFIG.get("wake_word", {}).get("enabled", False):
                _set_state("waiting")
                # Blocca finché non viene rilevata la wake word
                _wake_word_detected.wait()
                _wake_word_detected.clear() # Reset immediato
                
                # Mette in pausa il detector per liberare il microfono (AGGIUNTO)
                if wake_detector:
                    wake_detector.pause()
                
                _set_state("listening")
            else:
                _set_state("listening")

            # 1. Listen
            logger.info("[VoiceLoop] Whisper in ascolto...")
            query = transcriber.listen_and_transcribe()
            
            if not query.strip():
                logger.info("[VoiceLoop] Nessun comando rilevato (silenzio o trascrizione vuota).")
                continue

            # 2. Transcribed — check if we need retrieval
            _set_state("transcribing")
            logger.info(f"[VoiceLoop] Query trascritta: {query}")

            # ANALISI QUERY (Novità per velocità e ricerca)
            is_short_query = len(query.split()) <= 3
            is_web_search = "cerca online" in query.lower()
            
            if is_web_search and web_searcher:
                _set_state("retrieving")
                logger.info("[VoiceLoop] Richiesta ricerca online rilevata.")
                web_results = web_searcher.search(query)
                if web_results:
                    wiki_text = f"[Risultati dal Web]\n{web_results}"
                else:
                    wiki_text = "[Sistema] Nessun risultato trovato online."
                rag_context, used_rag = "", False
                _set_state("generating")
            # SALTO RETRIEVAL PER FRASI BREVI
            elif is_short_query or any(word in query.lower() for word in ["ciao", "grazie", "chi sei", "scusa"]):
                logger.info("[VoiceLoop] Salto retrieval per query semplice/breve.")
                rag_context, used_rag = "", False
                wiki_text = ""
                _set_state("generating")
            else:
                _set_state("retrieving")
                # AGGIUNTO FASE 2: RAG asincrono in parallelo (OTTIMIZZATO)
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future_rag = executor.submit(retriever.build_context, query)
                    future_wiki = executor.submit(kiwix.search, query) if kiwix.is_alive() else None
                    
                    try:
                        # Timeout ridotto ulteriormente a 1.0s
                        rag_context, used_rag = future_rag.result(timeout=1.0)
                    except Exception:
                        rag_context, used_rag = "", False
                    
                    wiki_text = ""
                    if future_wiki:
                        try:
                            # Timeout ridotto ulteriormente a 1.0s
                            wiki_text = future_wiki.result(timeout=1.0)
                        except Exception:
                            pass
                _set_state("generating")

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
            
            # Riattiva il monitoraggio della wake word (AGGIUNTO)
            if wake_detector:
                wake_detector.resume()


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
    web_searcher = WebSearcher() # AGGIUNTO

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

    # AGGIUNTO FASE 3: Callback Wake Word
    def on_wake_word_detected():
        if _current_state == "waiting":
            _wake_word_detected.set()

    # AGGIUNTO FASE 3: Inizializzazione Wake Word
    wake_detector = None
    if CONFIG.get("wake_word", {}).get("enabled", False):
        wake_detector = WakeWordDetector(
            config=CONFIG["wake_word"],
            on_detected=on_wake_word_detected
        )
        wake_detector.start()

    # AGGIUNTO FASE 2: Signal Handling (Aggiornato FASE 3)
    def on_shutdown(sig, frame):
        logger.info("Jarvis: chiusura in corso, salvo la sessione...")
        if wake_detector:
            wake_detector.stop()
        memory.end_session(llm)
        sys.exit(0)

    signal.signal(signal.SIGINT, on_shutdown)
    signal.signal(signal.SIGTERM, on_shutdown)

    # Start voice loop in background thread
    vl_thread = threading.Thread(
        target=voice_loop,
        args=(transcriber, retriever, kiwix, llm, synthesizer, memory, wake_detector, web_searcher),
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
