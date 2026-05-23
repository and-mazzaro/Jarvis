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
import time
from pathlib import Path

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
from memory.supabase_memory import JarvisMemory
from prompt_builder import build_system_prompt
from wake_word.detector import WakeWordDetector
from llm.ollama_client import OllamaClient
from stt.transcriber import Transcriber
from tts.synthesizer import Synthesizer
from rag.ingestor import Ingestor
from rag.retriever import Retriever
from knowledge.kiwix_client import KiwixClient
from knowledge.web_searcher import WebSearcher
import ws_server

# ---------------------------------------------------------------------------
# Global state and ThreadPool
# ---------------------------------------------------------------------------
_current_state = "idle"
_state_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_wake_word_detected = threading.Event()
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)


def _get_state() -> str:
    with _state_lock:
        return _current_state


def _set_state(state: str) -> None:
    global _current_state
    with _state_lock:
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
# Configurazione conversazione multi-turno
# ---------------------------------------------------------------------------
FOLLOW_UP_WINDOW_S = 8.0     # Secondi di attesa per un follow-up dopo la risposta
POST_TTS_PAUSE_S   = 1.0     # Pausa dopo che Jarvis finisce di parlare (evita eco TTS)


def _retrieve_context(query, retriever, kiwix, web_searcher):
    """Esegue il retrieval RAG/Wikipedia/Web e ritorna (context_str | None)."""
    is_short_query = len(query.split()) <= 3
    is_web_search = "cerca online" in query.lower()

    if is_web_search and web_searcher:
        logger.info("[VoiceLoop] Richiesta ricerca online rilevata.")
        web_results = web_searcher.search(query)
        if web_results:
            return f"[Risultati dal Web]\n{web_results}"
        return "[Sistema] Nessun risultato trovato online."

    if is_short_query or any(w in query.lower() for w in ["ciao", "grazie", "chi sei", "scusa", "buongiorno", "buonasera"]):
        logger.info("[VoiceLoop] Salto retrieval per query semplice/breve.")
        return None

    rag_context, used_rag = "", False
    wiki_text = ""
    future_rag = _executor.submit(retriever.build_context, query)
    future_wiki = _executor.submit(kiwix.search, query) if kiwix.is_alive() else None
    try:
        rag_context, used_rag = future_rag.result(timeout=1.5)
    except Exception:
        rag_context, used_rag = "", False
    if future_wiki:
        try:
            wiki_text = future_wiki.result(timeout=1.5)
        except Exception:
            pass

    parts = []
    if used_rag:
        parts.append(rag_context)
    if wiki_text:
        parts.append(f"[Wikipedia excerpt]\n{wiki_text}")
    return "\n\n".join(parts) if parts else None


def _generate_and_speak(query, context, llm, synthesizer, memory):
    """
    Genera la risposta in streaming, la manda al TTS frase per frase,
    e aspetta che tutta la riproduzione audio sia terminata.
    Usa una logica di split frase migliorata che non tronca mai a metà.
    """
    system_prompt = build_system_prompt(memory)
    history = memory.get_context_messages()

    response_tokens: list[str] = []
    sentence_buffer = ""
    first_sentence = True
    last_broadcast_time = 0.0

    for token in llm.generate_stream(query, context, system_prompt=system_prompt, history=history):
        response_tokens.append(token)
        sentence_buffer += token

        # Split su fine frase COMPLETA: punto/interrogativo/esclamativo
        # seguito da spazio/newline. Assicura che non tronchi mai a metà parola.
        while True:
            match = re.search(r'[.?!:;]+(?:\s|$)', sentence_buffer)
            if not match:
                break
            end_pos = match.end()
            sentence = sentence_buffer[:end_pos].strip()
            sentence_buffer = sentence_buffer[end_pos:]

            if sentence and len(sentence) > 5:  # Ignora frammenti troppo corti
                if first_sentence:
                    synthesizer.enqueue(sentence, on_start=lambda: _set_state("speaking"))
                    first_sentence = False
                else:
                    synthesizer.enqueue(sentence)

        # Stream testo parziale alla UI con throttling a 100ms
        current_time = time.time()
        if current_time - last_broadcast_time > 0.1 or token in ".?!:;\n":
            last_broadcast_time = current_time
            partial = "".join(response_tokens)
            if _loop and not _loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    ws_server.broadcast("generating", {"partial": partial}),
                    _loop,
                )

    # Accoda il testo rimanente (ultima frase, anche senza punto finale)
    if sentence_buffer.strip():
        if first_sentence:
            synthesizer.enqueue(sentence_buffer.strip(), on_start=lambda: _set_state("speaking"))
        else:
            synthesizer.enqueue(sentence_buffer.strip())

    # Assicura broadcast finale alla fine della generazione
    partial = "".join(response_tokens)
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            ws_server.broadcast("generating", {"partial": partial}),
            _loop,
        )

    response_text = "".join(response_tokens).strip()
    memory.add_message("assistant", response_text)

    logger.info("Response complete. Waiting for TTS playback ...")
    synthesizer.wait_until_done()
    logger.info("TTS playback done.")

    return response_text


def _extract_user_profile(query, response, memory):
    """
    Analizza la query dell'utente per estrarre informazioni di profilo.
    Aggiorna il profilo utente in modo automatico e non intrusivo.
    """
    q_lower = query.lower()

    # Estrazione età tramite regex
    age_match = re.search(r'\bho\s+(\d+)\s+anni\b', q_lower)
    if age_match:
        age_val = age_match.group(1)
        memory.update_profile("età", age_val)
        logger.info("[Profile] età → %s", age_val)

    # Pattern di profilazione: coppie (pattern_keywords, profile_key)
    # Rimossa la parola "sono" troppo generica per evitare falsi positivi
    profile_patterns = [
        (["mi chiamo", "il mio nome è", "chiamami"], "nome"),
        (["abito a", "vivo a", "sono di", "la mia città"], "città"),
        (["lavoro come", "faccio il", "sono un", "sono una", "di professione"], "professione"),
        (["mi piace", "mi piacciono", "adoro", "amo"], "interessi"),
        (["studio", "frequento", "la mia scuola", "università"], "studi"),
    ]

    for keywords, profile_key in profile_patterns:
        for kw in keywords:
            if kw in q_lower:
                # Estrai il valore dopo la keyword
                idx = q_lower.find(kw)
                value_part = query[idx + len(kw):].strip().rstrip(".,!?")
                if value_part and len(value_part) > 1:
                    # Per "interessi" appendi invece di sovrascrivere
                    if profile_key == "interessi":
                        existing = memory.profile.get("interessi", "")
                        if value_part.lower() not in existing.lower():
                            new_val = f"{existing}, {value_part}" if existing else value_part
                            memory.update_profile("interessi", new_val)
                            logger.info("[Profile] Aggiunto interesse: %s", value_part)
                    else:
                        memory.update_profile(profile_key, value_part)
                        logger.info("[Profile] %s → %s", profile_key, value_part)
                break  # Un solo match per pattern


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
    wake_detector: WakeWordDetector = None,
    web_searcher: WebSearcher = None,
) -> None:
    logger.info("Voice loop started.")
    use_wake_word = CONFIG.get("wake_word", {}).get("enabled", False)

    while True:
        try:
            # ── Fase 1: Attendi wake word (se abilitata) ──
            if use_wake_word:
                # Assicurati che il mic STT sia chiuso (wake word lo usa)
                transcriber.close_mic()
                _set_state("waiting")
                _wake_word_detected.wait()
                _wake_word_detected.clear()
                # Pausa il wake word detector → rilascia il suo mic
                if wake_detector:
                    wake_detector.pause()
                # Piccola pausa per dare tempo a Windows di rilasciare il device audio
                time.sleep(0.3)

            # ── Fase 2: Apri mic per il transcriber ──
            transcriber.open_mic()

            # ── Fase 3: Ciclo di conversazione multi-turno ──
            in_conversation = True
            while in_conversation:
                _set_state("listening")
                logger.info("[VoiceLoop] Whisper in ascolto...")
                query = transcriber.listen_and_transcribe()

                if not query.strip():
                    logger.info("[VoiceLoop] Silenzio — fine conversazione multi-turno.")
                    in_conversation = False
                    break

                _set_state("transcribing")
                logger.info("[VoiceLoop] Query trascritta: %s", query)

                # Salva messaggio utente
                memory.add_message("user", query)

                # Retrieval
                _set_state("retrieving")
                context = _retrieve_context(query, retriever, kiwix, web_searcher)

                # Generazione + riproduzione
                _set_state("generating")
                response_text = _generate_and_speak(query, context, llm, synthesizer, memory)

                # Profilazione automatica dell'utente
                _extract_user_profile(query, response_text, memory)

                # ── Pausa post-TTS ──
                time.sleep(POST_TTS_PAUSE_S)

                # ── Finestra di follow-up ──
                if use_wake_word:
                    logger.info("[VoiceLoop] Finestra follow-up: %.1fs per continuare...", FOLLOW_UP_WINDOW_S)
                    _set_state("listening")
                    query_follow = transcriber.listen_and_transcribe()
                    if query_follow.strip():
                        logger.info("[VoiceLoop] Follow-up rilevato: %s", query_follow)
                        memory.add_message("user", query_follow)
                        _set_state("retrieving")
                        context = _retrieve_context(query_follow, retriever, kiwix, web_searcher)
                        _set_state("generating")
                        response_text = _generate_and_speak(query_follow, context, llm, synthesizer, memory)
                        _extract_user_profile(query_follow, response_text, memory)
                        time.sleep(POST_TTS_PAUSE_S)
                        continue
                    else:
                        logger.info("[VoiceLoop] Nessun follow-up — fine conversazione.")
                        in_conversation = False

            # ── Fase 4: Rilascia mic e riattiva wake word ──
            transcriber.close_mic()
            time.sleep(0.3)  # Attendi rilascio device Windows
            if wake_detector:
                wake_detector.resume()

        except KeyboardInterrupt:
            logger.info("Voice loop interrupted.")
            break
        except Exception as exc:
            logger.error("Voice loop error: %s", exc, exc_info=True)
            # In caso di errore, assicurati di rilasciare il mic
            try:
                transcriber.close_mic()
            except Exception:
                pass
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

    logger.info("Caricamento modello di embedding SentenceTransformer e client ChromaDB...")
    import chromadb
    from chromadb.config import Settings
    from sentence_transformers import SentenceTransformer
    
    shared_embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    shared_chroma_client = chromadb.PersistentClient(
        path=str(CHROMA_PATH),
        settings=Settings(anonymized_telemetry=False),
    )

    ingestor = Ingestor(CONFIG["rag"], CHROMA_PATH, embedder=shared_embedder, chroma_client=shared_chroma_client)
    retriever = Retriever(CONFIG["rag"], CHROMA_PATH, embedder=shared_embedder, chroma_client=shared_chroma_client)
    kiwix = KiwixClient(CONFIG["kiwix"])
    web_searcher = WebSearcher()
    
    synthesizer = Synthesizer(CONFIG["tts"], PROJECT_ROOT)
    transcriber = Transcriber(CONFIG["stt"])

    # AGGIUNTO FASE 2: Inizializzazione Memoria
    memory = JarvisMemory()
    # Aspetta max 3s che Supabase carichi — poi parte comunque
    memory.wait_ready(timeout=3.0)
    
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
        if _get_state() == "waiting":
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
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_shutdown)
    signal.signal(signal.SIGTERM, on_shutdown)

    # Start voice loop in background thread
    vl_thread = threading.Thread(
        target=voice_loop,
        args=(transcriber, retriever, kiwix, llm, synthesizer, memory, wake_detector, web_searcher),
        daemon=True,
    )
    vl_thread.start()

    try:
        # Run servers (blocks forever)
        await ws_server.run_servers(
            ws_port=CONFIG["server"]["ws_port"],
            http_port=CONFIG["server"]["http_port"],
            ingestor=ingestor,
            retriever=retriever,
            kiwix_client=kiwix,
            frontend_dist=PROJECT_ROOT / "frontend" / "dist",
        )
    finally:
        logger.info("Jarvis: chiusura in corso, salvo la sessione...")
        if wake_detector:
            try:
                wake_detector.stop()
            except Exception:
                pass
        try:
            memory.end_session(llm)
        except Exception:
            pass
        try:
            _executor.shutdown(wait=False)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Jarvis stopped.")
