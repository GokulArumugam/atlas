"""Ollama provider — local, no key needed. Default for Atlas."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from atlas.agent.providers.base import (
    Generator,
    build_user_message,
    strip_fences,
    system_prompt,
)


class OllamaGenerator:
    """Talks to a local (or reachable) Ollama server.

    Uses the `/api/chat` endpoint with `stream=false`. Streaming can be added
    once the analyst path grows a streaming interface — for now the firewall
    needs the full SQL before it can approve execution, so streaming end-to-end
    doesn't save wall time.
    """

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.1:8b") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, user: str, question: str, context: str) -> str:
        del user
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 600},
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": build_user_message(context, question)},
            ],
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload.get("message", {}).get("content", "")
            return strip_fences(str(content))
        except (HTTPError, URLError, KeyError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Ollama at {self.base_url} could not produce SQL for model {self.model}."
            ) from error
