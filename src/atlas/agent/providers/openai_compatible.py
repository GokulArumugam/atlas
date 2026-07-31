"""OpenAI-compatible provider — covers OpenAI, Groq, Together, Fireworks,
Anyscale, and any endpoint that speaks the OpenAI Chat Completions API.

Requires a user-supplied API key. Optional `base_url` lets callers point at
non-OpenAI endpoints while keeping the same request shape.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from atlas.agent.providers.base import (
    build_user_message,
    strip_fences,
    system_prompt,
)


class OpenAICompatibleGenerator:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        dialect: str = "duckdb",
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for OpenAI-compatible providers.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dialect = dialect

    def generate(self, user: str, question: str, context: str) -> str:
        del user
        body = json.dumps({
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": system_prompt(self.dialect)},
                {"role": "user", "content": build_user_message(context, question)},
            ],
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            return strip_fences(str(content))
        except (HTTPError, URLError, KeyError, IndexError, json.JSONDecodeError) as error:
            raise RuntimeError("The OpenAI-compatible provider could not produce SQL.") from error
