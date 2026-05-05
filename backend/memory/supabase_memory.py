import os
import uuid
import threading
import queue
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

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
        self._load_initial_data()
        
        # Coda per scrittura asincrona
        self._write_queue = queue.Queue()
        self._writer_thread = threading.Thread(target=self._async_writer, daemon=True)
        self._writer_thread.start()

    def _load_initial_data(self):
        try:
            # Carica profilo
            res_p = self.supabase.table("user_profile").select("*").execute()
            self.profile = {item['key']: item['value'] for item in res_p.data}
            
            # Carica messaggi recenti
            res_m = self.supabase.table("conversations")\
                .select("role, content")\
                .order("timestamp", desc=True)\
                .limit(self.context_limit)\
                .execute()
            # L'ordine è desc, ribaltiamo per la storia
            self.recent_messages = list(reversed(res_m.data))
            print(f"[Memory] Caricati {len(self.recent_messages)} messaggi di contesto.")
        except Exception as e:
            print(f"[Memory] Errore caricamento iniziale: {e}")

    def add_message(self, role, content):
        # Aggiornamento locale immediato
        msg = {"role": role, "content": content}
        self.recent_messages.append(msg)
        if len(self.recent_messages) > self.context_limit:
            self.recent_messages.pop(0)
            
        # In coda per salvataggio asincrono
        self._write_queue.put({
            "session_id": self.session_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "tokens_estimated": len(content) // 4
        })

    def _async_writer(self):
        while True:
            item = self._write_queue.get()
            if item is None: break
            try:
                self.supabase.table("conversations").insert(item).execute()
            except Exception as e:
                print(f"[Memory] Errore scrittura Supabase: {e}")
            finally:
                self._write_queue.task_done()

    def get_context_messages(self):
        return self.recent_messages

    def get_profile_summary(self):
        summary = ""
        for k, v in self.profile.items():
            summary += f"{k.capitalize()}: {v}\n"
        return summary or "Nessuna informazione profilo."

    def update_profile(self, key, value):
        self.profile[key] = value
        # Upsert asincrono "leggero"
        threading.Thread(target=self._do_upsert_profile, args=(key, value), daemon=True).start()

    def _do_upsert_profile(self, key, value):
        try:
            self.supabase.table("user_profile").upsert({
                "key": key, "value": value, "updated_at": datetime.now().isoformat()
            }).execute()
        except Exception as e:
            print(f"[Memory] Errore update profilo: {e}")

    def end_session(self, llm_client):
        # Generazione riepilogo (opzionale se richiesto esplicitamente)
        print(f"[Memory] Fine sessione {self.session_id}.")
        self._rotate_old_conversations()

    def _rotate_old_conversations(self):
        # Implementazione base: logica di archiviazione
        pass
