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
        
        # Inizializza PyAudio
        self.audio = pyaudio.PyAudio()
        self.stream = None

        try:
            # Scarica il modello se non presente
            logger.info(f"Inizializzazione modello wake word: {self.model_name}")
            openwakeword.utils.download_models([self.model_name])
            
            # Inizializza il modello ONNX
            self.model = Model(
                wakeword_models=[self.model_name],
                inference_framework="onnx",
                vad_threshold=self.vad_threshold
            )
        except Exception as e:
            logger.error(f"Errore inizializzazione openwakeword: {e}")
            self.enabled = False

    def start(self):
        """Avvia il thread di monitoraggio."""
        if not self.enabled:
            return
        if self.is_running:
            return

        self.is_running = True
        self.is_paused = False
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()
        logger.info(f"Rilevatore wake word avviato (threshold: {self.threshold})")

    def pause(self):
        """Mette in pausa e rilascia COMPLETAMENTE le risorse audio."""
        if self.is_running and not self.is_paused:
            self.is_paused = True
            self._close_stream()
            if self.audio:
                self.audio.terminate()
                self.audio = None
            logger.info("WakeWord rilascia il microfono per Whisper...")

    def resume(self):
        """Riattiva l'ascolto ricreando l'istanza PyAudio."""
        if self.is_running and self.is_paused:
            if not self.audio:
                self.audio = pyaudio.PyAudio()
            self.is_paused = False
            logger.info("WakeWord riprende il monitoraggio.")

    def stop(self):
        """Ferma tutto e pulisce le risorse."""
        self.is_running = False
        if self._listen_thread:
            self._listen_thread.join(timeout=1.0)
        self._close_stream()
        if self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass
            self.audio = None
        logger.info("Rilevatore wake word fermato.")

    def _close_stream(self):
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
            self.stream = None

    def _open_stream(self):
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
            logger.error(f"Impossibile aprire microfono per WakeWord: {e}")
            return False

    def _listen_loop(self):
        """Ciclo di ascolto continuo con diagnostica."""
        last_log_time = time.time()
        max_score_seen = 0
        
        while self.is_running:
            if self.is_paused:
                time.sleep(0.5)
                continue

            if not self.stream:
                if not self._open_stream():
                    time.sleep(2.0)
                    continue

            try:
                # Legge audio (80ms chunk = 1280 samples)
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
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
                
                if prediction:
                    score = max(prediction.values())
                    if score > max_score_seen:
                        max_score_seen = score
                    
                    # Log periodico del punteggio massimo visto (ogni 5 secondi)
                    if time.time() - last_log_time > 5:
                        logger.info(f"[WakeWord] In ascolto... (Punteggio max recente: {max_score_seen:.2f}, RMS: {rms:.1f})")
                        max_score_seen = 0
                        last_log_time = time.time()
                    
                    # Rilevamento
                    if score >= self.threshold:
                        current_time = time.time()
                        if current_time - self.last_detection_time >= self.cooldown_seconds:
                            logger.info(f"!!! JARVIS RILEVATO !!! (Score: {score:.2f})")
                            self.last_detection_time = current_time
                            if self.on_detected:
                                self.on_detected()
                
            except Exception as e:
                logger.error(f"Errore nel monitoraggio audio: {e}")
                self._close_stream()
                time.sleep(0.5)
