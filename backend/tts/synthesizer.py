"""
Jarvis — TTS module using Kokoro-ONNX for high-quality multi-language speech.
"""
import logging
import numpy as np
import sounddevice as sd
from pathlib import Path
from typing import Callable, Optional
import queue
import threading
from kokoro_onnx import Kokoro

logger = logging.getLogger("jarvis.tts")

class Synthesizer:
    def __init__(self, config: dict, project_root: Path):
        model_name = config.get("model", "kokoro-v1.0.onnx")
        voices_bin = config.get("voices", "voices-v1.0.bin")
        self.voice_name = config.get("voice", "im_nicola")
        self.lang = config.get("lang", "it")
        self.speed = config.get("speed", 1.0)
        
        model_path = project_root / model_name
        voices_path = project_root / voices_bin

        if not model_path.exists():
            raise FileNotFoundError(f"Kokoro model not found: {model_path}")
        if not voices_path.exists():
            raise FileNotFoundError(f"Kokoro voices not found: {voices_path}")

        logger.info("Loading Kokoro TTS model: %s", model_name)
        self._kokoro = Kokoro(str(model_path), str(voices_path))
        
        # Kokoro usually works at 24000Hz
        self.sample_rate = 24000
        logger.info("TTS ready (Kokoro). Voice: %s, Lang: %s, Sample Rate: %d Hz", 
                    self.voice_name, self.lang, self.sample_rate)

        # Background playback queue
        self.audio_queue = queue.Queue()
        self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.playback_thread.start()

    def _playback_loop(self):
        while True:
            item = self.audio_queue.get()
            if item is None:
                break
            text, on_start, on_done = item
            self.speak(text, on_start, on_done)
            self.audio_queue.task_done()

    def enqueue(self, text: str, on_start: Optional[Callable] = None, on_done: Optional[Callable] = None):
        """Add text to the playback queue."""
        if text.strip():
            self.audio_queue.put((text, on_start, on_done))

    def wait_until_done(self):
        """Wait until all items in the queue have been played."""
        self.audio_queue.join()

    def speak(
        self,
        text: str,
        on_start: Optional[Callable] = None,
        on_done: Optional[Callable] = None,
    ) -> None:
        """Synthesize and play audio synchronously using sounddevice."""
        if not text.strip():
            return

        if on_start:
            on_start()

        logger.info("🔊 Sintesi vocale in corso (Kokoro: %s)...", self.voice_name)
        
        try:
            # Kokoro.create returns (samples, sample_rate)
            samples, sample_rate = self._kokoro.create(
                text, 
                voice=self.voice_name, 
                speed=self.speed, 
                lang=self.lang
            )
            
            if samples is None or len(samples) == 0:
                logger.warning("Nessun audio generato per il testo: %s", text)
                return

            # Update sample rate if it differs from expected (unlikely for Kokoro)
            if sample_rate != self.sample_rate:
                self.sample_rate = sample_rate

            # sounddevice plays float32 numpy arrays directly
            # Kokoro returns float32 by default
            sd.play(samples, samplerate=self.sample_rate)
            sd.wait()
            logger.info("✅ Riproduzione completata.")
            
        except Exception as e:
            logger.error("❌ Errore durante la sintesi Kokoro: %s", e, exc_info=True)

        if on_done:
            on_done()

    def speak_async(
        self,
        text: str,
        on_start: Optional[Callable] = None,
        on_done: Optional[Callable] = None,
    ) -> None:
        """Non-blocking version — runs speak() in a daemon thread."""
        t = threading.Thread(
            target=self.speak,
            args=(text,),
            kwargs={"on_start": on_start, "on_done": on_done},
            daemon=True,
        )
        t.start()
