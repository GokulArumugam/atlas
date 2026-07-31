"""Ollama provider — local, no key needed. Default for Atlas."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from atlas.agent.generator import GeneratorUnavailable
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

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        dialect: str = "duckdb",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dialect = dialect

    def generate(self, user: str, question: str, context: str) -> str:
        del user
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 600},
            "messages": [
                {"role": "system", "content": system_prompt(self.dialect)},
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
            if not content:
                # Ollama returns 404 as JSON like {"error":"model 'x' not found"}
                err = payload.get("error") or "empty response"
                raise GeneratorUnavailable(
                    f"Ollama returned no SQL: {err}. Check the model name and try `ollama pull {self.model}`."
                )
            return strip_fences(str(content))
        except HTTPError as error:
            # Try to extract Ollama's JSON error payload for a specific message.
            try:
                body_text = error.read().decode("utf-8")
                data = json.loads(body_text)
                msg = data.get("error") or body_text
            except Exception:
                msg = str(error)
            raise GeneratorUnavailable(
                f"Ollama at {self.base_url} rejected the request: {msg}. "
                f"If this is a missing model, run `ollama pull {self.model}`."
            ) from error
        except URLError as error:
            raise GeneratorUnavailable(
                f"Can't reach Ollama at {self.base_url}. "
                "Make sure Ollama is running (`ollama serve`) and reachable from the Atlas process. "
                "If Atlas runs in a container, set ATLAS_OLLAMA_BASE_URL to a host-reachable URL "
                "(e.g. http://host.containers.internal:11434 for Podman on macOS/Windows)."
            ) from error
        except (KeyError, json.JSONDecodeError) as error:
            raise GeneratorUnavailable(
                f"Ollama at {self.base_url} returned an unexpected response."
            ) from error
