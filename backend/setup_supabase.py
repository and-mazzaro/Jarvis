import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

def setup():
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError("SUPABASE_URL o SUPABASE_KEY mancanti nel file .env")

    try:
        supabase: Client = create_client(url, key)
        print("--- Configurazione Database Supabase ---")
        print("Connessione stabilita.")
        
        # Nota: La creazione delle tabelle va fatta via SQL Editor su Supabase.
        # Qui verifichiamo se esistono e inizializziamo i dati base.
        
        # Prova a leggere il profilo
        res = supabase.table("user_profile").select("*").execute()
        if not res.data:
            print("Inizializzazione profilo utente di default...")
            supabase.table("user_profile").insert([
                {"key": "name", "value": "Signore"},
                {"key": "language", "value": "italiano"},
                {"key": "preferred_tts_voice", "value": "im_nicola"}
            ]).execute()
            print("Profilo inizializzato.")
        else:
            print("Profilo utente già presente.")
            
        print("Setup completato con successo.")
    except Exception as e:
        print(f"[ERRORE] Setup fallito: {e}")
        print("Assicurati di aver creato le tabelle via SQL Editor su Supabase dashboard.")
        raise e

if __name__ == "__main__":
    try:
        setup()
    except Exception as e:
        print(f"[ERRORE CRITICO] {e}")
        sys.exit(1)
