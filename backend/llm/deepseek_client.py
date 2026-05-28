"""
DeepSeek V4 Flash API client — OpenAI-compatible /chat/completions with streaming.
"""
import json
import logging
import os
from typing import Iterator, Optional

import requests

logger = logging.getLogger("jarvis.llm")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

# Legacy / local model names → V4 Flash
_MODEL_ALIASES = {
    "deepseek-chat": DEFAULT_MODEL,
    "deepseek-reasoner": DEFAULT_MODEL,
}


class DeepSeekClient:
    def __init__(self, config: dict):
        base_url = config.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        if "localhost" in base_url or "127.0.0.1" in base_url:
            base_url = DEFAULT_BASE_URL
        # Normalise: strip trailing /v1 (we append /chat/completions directly)
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        self.base_url = base_url

        model = config.get("model", DEFAULT_MODEL)
        if any(x in model.lower() for x in ("phi3", "ollama", "llama", "mistral")):
            model = DEFAULT_MODEL
        self.model = _MODEL_ALIASES.get(model, model)

        self.temperature: float = config.get("temperature", 0.5)
        self.max_tokens: int = config.get("max_tokens", 300)
        self.thinking_type: str = config.get("thinking", "disabled")
        self.system_prompt: str = config.get(
            "system_prompt",
            "You are Jarvis, a helpful AI assistant. Be concise and clear.",
        )
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self._session = requests.Session()

    def generate_stream(
        self,
        user_message: str,
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        history: Optional[list[dict]] = None,
        **kwargs,
    ) -> Iterator[str]:
        if not self.api_key:
            self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
            if not self.api_key:
                logger.error("DEEPSEEK_API_KEY non trovata nelle variabili d'ambiente.")
                yield "[Errore: DEEPSEEK_API_KEY mancante. Configurala nel file .env]"
                return

        prompt = self._build_prompt(user_message, context)
        messages = [{"role": "system", "content": system_prompt or self.system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "stream": True,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "thinking": {"type": kwargs.get("thinking", self.thinking_type)},
        }

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        logger.debug("POST %s  model=%s", url, payload["model"])

        try:
            with self._session.post(
                url, json=payload, headers=headers, stream=True, timeout=(5, 120)
            ) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line_str = raw_line.decode("utf-8").strip()
                    if not line_str or line_str == "data: [DONE]":
                        break
                    if line_str.startswith("data: "):
                        line_str = line_str[6:]
                    try:
                        data = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue
                    token = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if token:
                        yield token
        except requests.RequestException as exc:
            logger.error("DeepSeek request failed: %s", exc)
            yield f"[Errore di connessione a DeepSeek: {exc}]"

    def generate(self, user_message: str = "", **kwargs) -> str:
        msg = user_message or kwargs.pop("prompt", "")
        return "".join(self.generate_stream(msg, **kwargs))

    def is_alive(self) -> bool:
        if not self.api_key:
            self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        return bool(self.api_key)

    def _build_prompt(self, user_message: str, context: Optional[str]) -> str:
        if context and context.strip():
            return (
                f"[CONTESTO]\n{context.strip()}\n\n"
                f"[DOMANDA UTENTE]\n{user_message}\n\n"
                "Rispondi basandoti sul contesto sopra quando pertinente. "
                "Se il contesto non è rilevante, rispondi in base alle tue conoscenze generali."
            )
        return user_message
