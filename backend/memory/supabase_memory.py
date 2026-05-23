import os
import uuid
import threading
import queue
import logging
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

logger = logging.getLogger("jarvis.memory")

class JarvisMemory:
    def __init__(self):
        load_dotenv()
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_KEY")
        self.context_limit = int(os.getenv("JARVIS_CONTEXT_MESSAGES", 20))
        
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL e SUPABASE_KEY devono essere definiti nel file .env")
            
        self.supabase: Client = create_client(self.url, self.key)
        self.session_id = f"{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        
        self.profile = {}
        self.recent_messages = []
        self._lock = threading.Lock()
        self._data_ready = threading.Event()

        # Carica dati in background — non blocca l'avvio
        threading.Thread(target=self._load_initial_data_async, daemon=True).start()

        # Coda per scrittura asincrona unificata (messaggi + profilo)
        self._write_queue = queue.Queue()
        self._writer_thread = threading.Thread(target=self._async_writer, daemon=True)
        self._writer_thread.start()

    def _load_initial_data_async(self):
        """Carica profilo e messaggi in background senza bloccare l'avvio."""
        self._load_initial_data()
        self._data_ready.set()
        logger.info("Dati caricati in background.")

    def wait_ready(self, timeout: float = 5.0):
        """
        Attende che i dati siano caricati. Chiamare prima di build_system_prompt()
        se si vuole avere il profilo completo. Timeout di sicurezza: 5 secondi.
        """
        self._data_ready.wait(timeout=timeout)

    def _load_initial_data(self):
        try:
            # Carica profilo
            res_p = self.supabase.table("user_profile").select("*").execute()
            with self._lock:
                self.profile = {item['key']: item['value'] for item in res_p.data}
            
            # Carica messaggi recenti
            res_m = self.supabase.table("conversations")\
                .select("role, content")\
                .order("timestamp", desc=True)\
                .limit(self.context_limit)\
                .execute()
            # L'ordine è desc, ribaltiamo per la storia
            with self._lock:
                self.recent_messages = list(reversed(res_m.data))
            logger.info("Caricati %d messaggi di contesto.", len(self.recent_messages))
        except Exception as e:
            logger.error("Errore caricamento iniziale: %s", e)

    def add_message(self, role, content):
        # Aggiornamento locale immediato con thread lock
        msg = {"role": role, "content": content}
        with self._lock:
            self.recent_messages.append(msg)
            if len(self.recent_messages) > self.context_limit:
                self.recent_messages.pop(0)
            
        # In coda per salvataggio asincrono
        self._write_queue.put({
            "action": "insert_message",
            "data": {
                "session_id": self.session_id,
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "tokens_estimated": len(content) // 4
            }
        })

    def _async_writer(self):
        while True:
            item = self._write_queue.get()
            if item is None:
                break
            try:
                action = item.get("action")
                data = item.get("data")
                if action == "insert_message":
                    self.supabase.table("conversations").insert(data).execute()
                elif action == "upsert_profile":
                    self.supabase.table("user_profile").upsert(data).execute()
            except Exception as e:
                logger.error("Errore scrittura Supabase: %s", e)
            finally:
                self._write_queue.task_done()

    def get_context_messages(self):
        with self._lock:
            return list(self.recent_messages)

    def get_profile_summary(self):
        summary = ""
        with self._lock:
            for k, v in self.profile.items():
                summary += f"{k.capitalize()}: {v}\n"
        return summary or "Nessuna informazione profilo."

    def update_profile(self, key, value):
        with self._lock:
            self.profile[key] = value
        
        # Invia alla coda unificata in background
        self._write_queue.put({
            "action": "upsert_profile",
            "data": {
                "key": key,
                "value": value,
                "updated_at": datetime.now().isoformat()
            }
        })

    def end_session(self, llm_client=None):
        logger.info("Fine sessione %s. Salvataggio messaggi pendenti...", self.session_id)
        # Flush queue and stop writer thread gracefully
        self._write_queue.put(None)
        self._writer_thread.join(timeout=5.0)
        self._rotate_old_conversations()

    def _rotate_old_conversations(self):
        # Implementazione base: logica di archiviazione
        pass
