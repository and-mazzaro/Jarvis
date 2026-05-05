"""
Ollama API client — wraps the OpenAI-compatible /api/chat endpoint with streaming.
"""
import json
import logging
import requests
from typing import Iterator, Optional

logger = logging.getLogger("jarvis.llm")


class OllamaClient:
    def __init__(self, config: dict):
        self.base_url: str = config["base_url"].rstrip("/")
        self.model: str = config["model"]
        self.temperature: float = config.get("temperature", 0.7)
        self.max_tokens: int = config.get("max_tokens", 512)
        self.num_ctx: int = config.get("num_ctx", 2048)
        self.top_k: int = config.get("top_k", 40)
        self.top_p: float = config.get("top_p", 0.9)
        self.repeat_penalty: float = config.get("repeat_penalty", 1.1)
        self.system_prompt: str = config.get(
            "system_prompt",
            "You are Jarvis, a helpful local AI assistant. Be concise and clear.",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_stream(
        self,
        user_message: str,
        context: Optional[str] = None,
        system_prompt: Optional[str] = None,
        history: Optional[list[dict]] = None,
        **kwargs,
    ) -> Iterator[str]:
        """
        Yield response tokens one-by-one as they arrive from Ollama.
        `context` is the pre-assembled RAG/Wikipedia text block.
        """
        prompt = self._build_prompt(user_message, context)
        
        messages = []
        # Use provided system_prompt or fallback to default
        sys_p = system_prompt if system_prompt is not None else self.system_prompt
        messages.append({"role": "system", "content": sys_p})
        
        # Add conversation history if provided
        if history:
            messages.extend(history)
            
        # Add current user message
        messages.append({"role": "user", "content": prompt})

        # Options for the generation
        options = {
            "temperature": self.temperature,
            "num_predict": self.max_tokens,
            "num_ctx": self.num_ctx,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
        }
        # Allow overriding options via kwargs
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "num_ctx" in kwargs:
            options["num_ctx"] = kwargs["num_ctx"]

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": options,
        }

        url = f"{self.base_url}/api/chat"
        logger.debug("POST %s  model=%s", url, self.model)

        try:
            with requests.post(url, json=payload, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    try:
                        data = json.loads(raw_line)
                    except json.JSONDecodeError:
                        logger.warning("Malformed JSON line: %s", raw_line)
                        continue

                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token

                    if data.get("done", False):
                        break

        except requests.RequestException as exc:
            logger.error("Ollama request failed: %s", exc)
            yield f"[Error contacting Ollama: {exc}]"

    def generate(self, user_message: str = "", **kwargs) -> str:
        """Blocking version — collect all tokens and return the full string."""
        # Handle cases where 'prompt' might be used instead of 'user_message'
        msg = user_message or kwargs.pop("prompt", "")
        return "".join(self.generate_stream(msg, **kwargs))

    def is_alive(self) -> bool:
        """Return True if Ollama is reachable."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, user_message: str, context: Optional[str]) -> str:
        if context and context.strip():
            return (
                f"[CONTESTO]\n{context.strip()}\n\n"
                f"[DOMANDA UTENTE]\n{user_message}\n\n"
                "Rispondi basandoti sul contesto sopra quando pertinente. "
                "Se il contesto non è rilevante, rispondi in base alle tue conoscenze generali."
            )
        return user_message
