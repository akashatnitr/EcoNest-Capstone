"""Unified async client for Ollama HTTP API."""

import json
from typing import Optional, Type, TypeVar

import httpx
from pydantic import BaseModel

from orchestrator.config import get_settings

T = TypeVar("T", bound=BaseModel)

settings = get_settings()


class LLMClient:
    """Async client for Ollama with retry, streaming, and structured output."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = base_url or settings.OLLAMA_URL
        self.model = model or settings.OLLAMA_MODEL
        self.fallback_model = settings.OLLAMA_FALLBACK_MODEL
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_retries: int = 3,
        stream: bool = False,
    ) -> str:
        """Generate text with retry and optional fallback model."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": temperature,
            "stream": stream,
        }
        if system:
            payload["system"] = system

        for attempt in range(max_retries):
            try:
                if stream:
                    return await self._stream_generate(payload)
                response = await self.client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()["response"]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404 and attempt == 0:
                    # Try fallback model
                    payload["model"] = self.fallback_model
                    continue
                if attempt == max_retries - 1:
                    raise
            except Exception:
                if attempt == max_retries - 1:
                    raise
        return ""

    async def _stream_generate(self, payload: dict) -> str:
        """Collect a streaming response into a single string."""
        parts: list[str] = []
        async with self.client.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        data = json.loads(line)
                        parts.append(data.get("response", ""))
                    except json.JSONDecodeError:
                        continue
        return "".join(parts)

    async def generate_structured(
        self,
        prompt: str,
        output_model: Type[T],
        system: Optional[str] = None,
        temperature: float = 0.7,
    ) -> T:
        """Generate structured output validated by a Pydantic model."""
        structured_prompt = (
            f"{prompt}\n\n"
            f"Respond with valid JSON that matches this schema:\n"
            f"{output_model.model_json_schema()}\n"
            f"Output ONLY the JSON object, no other text."
        )
        raw = await self.generate(
            structured_prompt,
            system=system,
            temperature=temperature,
        )
        # Clean up potential markdown fences
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        return output_model.model_validate_json(cleaned)

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        stream: bool = False,
    ) -> str:
        """Chat completion using Ollama's /api/chat endpoint."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        response = await self.client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    async def close(self) -> None:
        await self.client.aclose()
