"""
Speech-to-Text pipeline:
  PyAudio  →  webrtcvad (silence detection)  →  faster-whisper (transcription)
"""
import collections
import logging
import time
from typing import Optional

import numpy as np
import pyaudio
import webrtcvad
from faster_whisper import WhisperModel

logger = logging.getLogger("jarvis.stt")

# Audio constants — these must match what webrtcvad expects
SAMPLE_RATE = 16_000          # Hz
CHANNELS = 1
SAMPLE_WIDTH = 2              # 16-bit PCM
FRAME_DURATION_MS = 30        # 10 / 20 / 30 ms allowed by webrtcvad
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # samples per frame


class Transcriber:
    def __init__(self, config: dict):
        self.model_size: str = config.get("model", "small")
        self.language: Optional[str] = config.get("language", "en") or None
        self.device: str = config.get("device", "cpu")
        self.compute_type: str = config.get("compute_type", "int8")
        self.vad_aggressiveness: int = config.get("vad_aggressiveness", 2)
        # silence_threshold_ms: quanto silenzio (ms) dopo l'ultima parola prima di chiudere
        # Valori bassi (300-500ms) tagliano le frasi, usare almeno 800ms per conversazioni naturali
        self.silence_threshold_ms: int = config.get("silence_threshold_ms", 1000)
        self.beam_size: int = config.get("beam_size", 1)
        self.best_of: int = config.get("best_of", 1)
        # initial_timeout_s: quanto attendere prima di sentire la prima parola
        self.initial_timeout_s: float = config.get("initial_timeout_s", 8.0)

        logger.info(
            "Loading Whisper model '%s' on %s (%s) …",
            self.model_size,
            self.device,
            self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        self._vad = webrtcvad.Vad(self.vad_aggressiveness)
        logger.info("STT ready.")

        # PyAudio e stream inizializzati ma NON aperti — gestione cooperativa
        # con il wake word detector (su Windows solo uno alla volta può usare il mic)
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._mic_open = False
        logger.info("Microfono pronto (verrà aperto on-demand).")

    # ------------------------------------------------------------------
    # Gestione cooperativa del microfono
    # ------------------------------------------------------------------

    def open_mic(self) -> None:
        """Apre il microfono. Chiamare prima di listen_and_transcribe()."""
        if self._mic_open:
            return
        if self._pa is None:
            self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=FRAME_SIZE,
        )
        self._mic_open = True
        logger.debug("Microfono STT aperto.")

    def close_mic(self) -> None:
        """Rilascia completamente il microfono per il wake word detector."""
        if not self._mic_open:
            return
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        self._mic_open = False
        logger.debug("Microfono STT rilasciato.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def listen_and_transcribe(self) -> str:
        """
        Block until the user speaks and then falls silent, then return the
        transcribed text.  Raises RuntimeError if no audio device is found.
        Apre il microfono se non è già aperto.
        """
        if not self._mic_open:
            self.open_mic()
        audio_data = self._capture_speech()
        if not audio_data:
            return ""
        return self._transcribe(audio_data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capture_speech(self) -> bytes:
        """
        Usa il microfono aperto, rileva il parlato via VAD
        e restituisce i byte PCM grezzi di una singola utterance.
        """
        logger.info("Whisper sta ascoltando...")
        num_silence_frames = max(1, int(self.silence_threshold_ms / FRAME_DURATION_MS))
        ring_buffer: collections.deque = collections.deque(maxlen=num_silence_frames)
        triggered = False
        voiced_frames: list[bytes] = []

        start_time = time.time()
        timeout = self.initial_timeout_s  # Timeout configurabile per la prima parola

        while True:
            frame = self._stream.read(FRAME_SIZE, exception_on_overflow=False)
            is_speech = self._vad.is_speech(frame, SAMPLE_RATE)

            if not triggered:
                ring_buffer.append((frame, is_speech))
                num_voiced = sum(1 for _, s in ring_buffer if s)

                # Più sensibile: basta il 60% di frame parlati per attivare (era 90%)
                if num_voiced > 0.6 * ring_buffer.maxlen:
                    triggered = True
                    logger.debug("Voce rilevata — registrazione...")
                    voiced_frames.extend(f for f, _ in ring_buffer)
                    ring_buffer.clear()

                # Timeout se non parla nessuno
                if time.time() - start_time > timeout:
                    logger.info("Timeout: nessuna voce rilevata dopo %.1fs.", timeout)
                    break
            else:
                voiced_frames.append(frame)
                ring_buffer.append((frame, is_speech))
                num_unvoiced = sum(1 for _, s in ring_buffer if not s)
                if num_unvoiced > 0.9 * ring_buffer.maxlen:
                    logger.debug("Silenzio rilevato — fine registrazione.")
                    break

        if not voiced_frames:
            return b""
        return b"".join(voiced_frames)

    def _transcribe(self, pcm_bytes: bytes) -> str:
        """Run faster-whisper on raw PCM bytes and return the text."""
        # Convert bytes → float32 array in [-1, 1]
        audio_array = (
            np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )

        segments, info = self._model.transcribe(
            audio_array,
            language=self.language,
            beam_size=self.beam_size,
            best_of=self.best_of,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info("Transcribed: %r", text)
        return text
