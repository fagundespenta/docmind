"""LLM provider abstraction: fake (offline), OpenAI, Anthropic Claude, Google Gemini.

Every provider exposes the same async streaming interface, so the RAG pipeline and
the SSE endpoint never need to know which vendor is answering.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, cast

from app.config import Effort, Settings
from app.embeddings import tokenize
from app.vectorstore import SearchHit

if TYPE_CHECKING:
    from anthropic.types import MessageParam
    from openai.types.chat import ChatCompletionMessageParam


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    def stream(
        self, system: str, messages: list[ChatMessage], sources: list[SearchHit]
    ) -> AsyncIterator[str]:
        """Yield the answer as text deltas."""
        ...


# --------------------------------------------------------------------------- fake


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


class FakeLLM:
    """Deterministic, offline "LLM": an extractive answer built from the sources.

    It picks the sentences that best overlap the question, cites them with [n]
    markers exactly like a real model would, and streams the result word by word.
    """

    def __init__(self, delay: float = 0.0, max_sentences: int = 3) -> None:
        self.delay = delay
        self.max_sentences = max_sentences

    @property
    def name(self) -> str:
        return "fake"

    def compose(self, question: str, sources: list[SearchHit]) -> str:
        q_tokens = set(tokenize(question))
        candidates: list[tuple[float, int, str]] = []
        for n, hit in enumerate(sources, start=1):
            for sentence in _SENTENCE_RE.split(hit.text):
                sentence = sentence.strip()
                if len(sentence) < 20:
                    continue
                s_tokens = set(tokenize(sentence))
                overlap = len(q_tokens & s_tokens)
                if overlap:
                    candidates.append((overlap + hit.score, n, sentence))
        if not candidates:
            return (
                "I couldn't find a passage that answers this directly. "
                "Try rephrasing the question or uploading a more relevant document."
            )
        candidates.sort(key=lambda c: c[0], reverse=True)
        picked = candidates[: self.max_sentences]
        lines = [f"- {sentence} [{n}]" for _, n, sentence in picked]
        return "Here is what the documents say:\n\n" + "\n".join(lines)

    async def stream(
        self, system: str, messages: list[ChatMessage], sources: list[SearchHit]
    ) -> AsyncIterator[str]:
        question = next((m.content for m in reversed(messages) if m.role == "user"), "")
        answer = self.compose(question, sources)
        for token in re.findall(r"\S+\s*", answer):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield token


# ------------------------------------------------------------------------- openai


class OpenAILLM:
    def __init__(self, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self.model = model

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    async def stream(
        self, system: str, messages: list[ChatMessage], sources: list[SearchHit]
    ) -> AsyncIterator[str]:
        api_messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": system}]
        api_messages += [
            cast("ChatCompletionMessageParam", {"role": m.role, "content": m.content})
            for m in messages
        ]
        stream = await self._client.chat.completions.create(
            model=self.model, stream=True, messages=api_messages
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# ---------------------------------------------------------------------- anthropic


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, effort: Effort) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.effort = effort

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    async def stream(
        self, system: str, messages: list[ChatMessage], sources: list[SearchHit]
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[
                cast("MessageParam", {"role": m.role, "content": m.content}) for m in messages
            ],
            output_config={"effort": self.effort},
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield "\n\n_The model declined to answer this request._"


# ------------------------------------------------------------------------- gemini


class GeminiLLM:
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model = model

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    async def stream(
        self, system: str, messages: list[ChatMessage], sources: list[SearchHit]
    ) -> AsyncIterator[str]:
        from google.genai import types

        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in messages
        ]
        response = await self._client.aio.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system),
        )
        async for chunk in response:
            if chunk.text:
                yield chunk.text


# ------------------------------------------------------------------------ factory


def build_llm(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider
    if provider == "openai":
        _require(settings.openai_api_key, "OPENAI_API_KEY", provider)
        return OpenAILLM(settings.openai_api_key, settings.openai_model)
    if provider == "anthropic":
        _require(settings.anthropic_api_key, "ANTHROPIC_API_KEY", provider)
        return AnthropicLLM(
            settings.anthropic_api_key, settings.anthropic_model, settings.anthropic_effort
        )
    if provider == "gemini":
        _require(settings.gemini_api_key, "GEMINI_API_KEY", provider)
        return GeminiLLM(settings.gemini_api_key, settings.gemini_model)
    return FakeLLM(delay=settings.fake_llm_delay)


def _require(value: str, env_name: str, provider: str) -> None:
    if not value:
        raise RuntimeError(f"{env_name} must be set when LLM_PROVIDER={provider}")
