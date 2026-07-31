"""Anthropic Messages API provider."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from atlas.agent.providers.base import (
    build_user_message,
    strip_fences,
    system_prompt,
)


class AnthropicGenerator:
    name = "anthropic"
    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-latest") -> None:
        if not api_key:
            raise ValueError("api_key is required for Anthropic.")
        self.api_key = api_key
        self.model = model

    def generate(self, user: str, question: str, context: str) -> str:
        del user
        body = json.dumps({
            "model": self.model,
            "max_tokens": 600,
            "system": system_prompt(),
            "messages": [
                {"role": "user", "content": build_user_message(context, question)},
            ],
        }).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return strip_fences(str(payload["content"][0]["text"]))
        except (HTTPError, URLError, KeyError, IndexError, json.JSONDecodeError) as error:
            raise RuntimeError("The Anthropic provider could not produce SQL.") from error
