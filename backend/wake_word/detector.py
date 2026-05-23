import os
import threading
import time
import logging
import numpy as np
import pyaudio
import openwakeword
from openwakeword.model import Model

logger = logging.getLogger("jarvis.wake_word")

class WakeWordDetector:
    def __init__(self, config: dict, on_detected: callable):
        """
        Rilevatore di wake word ottimizzato per Jarvis.
        Gestisce il microfono in modo cooperativo per evitare conflitti con Whisper.
        """
        self.config = config
        self.on_detected = on_detected
        self.enabled = config.get("enabled", True)
        self.model_name = config.get("model", "hey_jarvis")
        self.threshold = config.get("threshold", 0.5)
        self.vad_threshold = config.get("vad_threshold", 0.3)
        self.cooldown_seconds = config.get("cooldown_seconds", 2.0)
        self.chunk_size = config.get("chunk_size", 1280)
        
        self.last_detection_time = 0
        self.is_running = False
        self.is_paused = False
        self._listen_thread = None
        self._lock = threading.Lock()
        
        # Inizializza PyAudio
        self.audio = pyaudio.PyAudio()
        self.stream = None

        try:
            # Scarica il modello se non presente
            logger.info("Inizializzazione modello wake word: %s", self.model_name)
            openwakeword.utils.download_models([self.model_name])
            
            # Inizializza il modello ONNX
            self.model = Model(
                wakeword_models=[self.model_name],
                inference_framework="onnx",
                vad_threshold=self.vad_threshold
            )
        except Exception as e:
            logger.error("Errore inizializzazione openwakeword: %s", e)
            self.enabled = False

    def start(self):
        """Avvia il thread di monitoraggio."""
        if not self.enabled:
            return
        with self._lock:
            if self.is_running:
                return
            self.is_running = True
            self.is_paused = False
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()
        logger.info("Rilevatore wake word avviato (threshold: %s)", self.threshold)

    def pause(self):
        """Mette in pausa e rilascia COMPLETAMENTE le risorse audio."""
        with self._lock:
            if self.is_running and not self.is_paused:
                self.is_paused = True
                self._close_stream_unlocked()
                if self.audio:
                    try:
                        self.audio.terminate()
                    except Exception:
                        pass
                    self.audio = None
                logger.info("WakeWord rilascia il microfono per Whisper...")

    def resume(self):
        """Riattiva l'ascolto ricreando l'istanza PyAudio."""
        with self._lock:
            if self.is_running and self.is_paused:
                if not self.audio:
                    try:
                        self.audio = pyaudio.PyAudio()
                    except Exception as e:
                        logger.error("Errore ricreazione PyAudio in resume: %s", e)
                        return
                
                # Reset dello stato accumulato del modello per evitare falsi positivi
                if hasattr(self, 'model') and self.model:
                    try:
                        self.model.reset()
                        logger.info("Stato del modello openwakeword resettato.")
                    except Exception as e:
                        logger.debug("Impossibile resettare il modello: %s", e)
                
                self.is_paused = False
                logger.info("WakeWord riprende il monitoraggio.")

    def stop(self):
        """Ferma tutto e pulisce le risorse."""
        with self._lock:
            self.is_running = False
        if self._listen_thread:
            self._listen_thread.join(timeout=1.0)
        with self._lock:
            self._close_stream_unlocked()
            if self.audio:
                try:
                    self.audio.terminate()
                except Exception:
                    pass
                self.audio = None
        logger.info("Rilevatore wake word fermato.")

    def _close_stream(self):
        with self._lock:
            self._close_stream_unlocked()

    def _close_stream_unlocked(self):
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _open_stream(self) -> bool:
        with self._lock:
            if not self.audio:
                try:
                    self.audio = pyaudio.PyAudio()
                except Exception as e:
                    logger.error("PyAudio non inizializzato e impossibile crearlo: %s", e)
                    return False
            try:
                self.stream = self.audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=self.chunk_size
                )
                return True
            except Exception as e:
                logger.error("Impossibile aprire microfono per WakeWord: %s", e)
                return False

    def _listen_loop(self):
        """Ciclo di ascolto continuo con diagnostica."""
        last_log_time = time.time()
        max_score_seen = 0
        consecutive_errors = 0
        
        while True:
            with self._lock:
                if not self.is_running:
                    break
                paused = self.is_paused
            
            if paused:
                time.sleep(0.5)
                continue

            # Controlla ed eventualmente apri lo stream
            has_stream = False
            with self._lock:
                if self.stream:
                    has_stream = True
            
            if not has_stream:
                if not self._open_stream():
                    consecutive_errors += 1
                    # Aumenta il tempo di attesa se gli errori sono persistenti
                    wait_time = min(2.0 * consecutive_errors, 30.0)
                    if consecutive_errors % 5 == 0:
                        logger.error("Rilevatore wake word bloccato da continui errori di apertura stream (%d errori). Attesa di %.1fs...", consecutive_errors, wait_time)
                    time.sleep(wait_time)
                    continue

            try:
                # Legge audio (80ms chunk = 1280 samples)
                with self._lock:
                    if not self.stream:
                        continue
                    # Usiamo non-blocking read o read con exception_on_overflow
                    stream_to_read = self.stream
                
                data = stream_to_read.read(self.chunk_size, exception_on_overflow=False)
                if not data:
                    continue
                
                # Conversione e controllo volume (RMS)
                audio_chunk = np.frombuffer(data, dtype=np.int16)
                rms = np.sqrt(np.mean(audio_chunk.astype(np.float32)**2))
                
                # Se il volume è sospettosamente basso, avvisa (una volta ogni 30s)
                if rms < 10 and time.time() - last_log_time > 30:
                    logger.warning("[WakeWord] Segnale microfono quasi assente (RMS < 10). Controlla le impostazioni audio di Windows.")

                # Predizione
                prediction = self.model.predict(audio_chunk)
                consecutive_errors = 0  # Resetta contatore su successo
                
                if prediction:
                    score = max(prediction.values())
                    if score > max_score_seen:
                        max_score_seen = score
                    
                    # Log periodico del punteggio massimo visto (ogni 5 secondi)
                    if time.time() - last_log_time > 5:
                        logger.info("[WakeWord] In ascolto... (Punteggio max recente: %.2f, RMS: %.1f)", max_score_seen, rms)
                        max_score_seen = 0
                        last_log_time = time.time()
                    
                    # Rilevamento
                    if score >= self.threshold:
                        current_time = time.time()
                        if current_time - self.last_detection_time >= self.cooldown_seconds:
                            logger.info("!!! JARVIS RILEVATO !!! (Score: %.2f)", score)
                            self.last_detection_time = current_time
                            if self.on_detected:
                                self.on_detected()
                
            except Exception as e:
                consecutive_errors += 1
                logger.error("Errore nel monitoraggio audio openwakeword: %s", e)
                self._close_stream()
                time.sleep(0.5)
