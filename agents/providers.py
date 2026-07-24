"""
Provider registry: generic, role-agnostic functions that know *how* to build a
pydantic-ai model for each provider kind. No env-var reading, no knowledge of
"agrinet"/"moderation" roles -- that decision lives in agents/models.py, which
calls into this registry with already-resolved config values.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic_ai.messages import ModelMessage, ModelResponse, ModelResponseStreamEvent, TextPart
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings


@dataclass
class _OneShotStreamedResponse(StreamedResponse):
    """Presents an already-complete (non-streamed) ModelResponse as a
    one-event "stream", for backends whose streaming responses aren't usable
    (e.g. a gateway that returns `{"raw": "<escaped SSE text>"}` instead of a
    real text/event-stream). The whole answer arrives as a single chunk
    instead of incrementally -- no live streaming, but the pipeline works."""

    _response: ModelResponse = field(default=None)

    def __post_init__(self) -> None:
        self._usage = self._response.usage
        self.finish_reason = self._response.finish_reason
        self.provider_response_id = self._response.provider_response_id
        self.provider_details = self._response.provider_details

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        for i, part in enumerate(self._response.parts):
            if isinstance(part, TextPart):
                # Split into two deltas, not one: the *first* handle_text_delta
                # call for a part creates it (PartStartEvent, whose content
                # never reaches callers that only forward PartDeltaEvent, e.g.
                # the chat endpoint's SSE loop); only later calls for the same
                # part produce real PartDeltaEvents. An empty-string primer
                # creates the part with no content lost, then the *entire*
                # actual text goes out as the one real delta.
                event = self._parts_manager.handle_text_delta(vendor_part_id=i, content="")
                if event is not None:
                    yield event
                if part.content:
                    event = self._parts_manager.handle_text_delta(vendor_part_id=i, content=part.content)
                    if event is not None:
                        yield event
            else:
                yield self._parts_manager.handle_part(vendor_part_id=i, part=part)

    @property
    def model_name(self) -> str:
        return self._response.model_name or ""

    @property
    def provider_name(self) -> str | None:
        return self._response.provider_name

    @property
    def timestamp(self) -> datetime:
        return self._response.timestamp or datetime.now(timezone.utc)


class NonStreamingOpenAIChatModel(OpenAIChatModel):
    """Same as OpenAIChatModel, but `request_stream()` makes a plain
    non-streaming request under the hood and presents the complete result as
    a single-chunk stream. Use this when the backend doesn't support (or
    mishandles) real SSE streaming."""

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context=None,
    ) -> AsyncIterator[StreamedResponse]:
        response = await self.request(messages, model_settings, model_request_parameters)
        yield _OneShotStreamedResponse(model_request_parameters=model_request_parameters, _response=response)


def openai_compatible_model(
    model_name: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    extra_headers: dict | None = None,
    extra_body: dict | None = None,
    disable_streaming: bool = False,
) -> tuple[OpenAIChatModel, dict]:
    """Covers plain OpenAI, a self-hosted vLLM server, and any external
    OpenAI-compatible gateway -- the only difference between those is which
    of these parameters are populated. `extra_headers` is a client/transport
    concern (baked into the AsyncOpenAI client); `extra_body` is a per-request
    concern (only expressible via ModelSettings, returned for the caller to
    pass through to Agent/run model_settings). `disable_streaming` makes
    real-time streaming calls (e.g. the live chat endpoint) fall back to a
    single non-streaming request under the hood -- for gateways whose SSE
    streaming responses aren't spec-compliant."""
    if extra_headers:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", default_headers=extra_headers)
        provider = OpenAIProvider(openai_client=client)
    else:
        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    settings = {"extra_body": extra_body} if extra_body else {}
    model_cls = NonStreamingOpenAIChatModel if disable_streaming else OpenAIChatModel
    return model_cls(model_name, provider=provider), settings


def azure_openai_model(
    deployment_name: str,
    *,
    endpoint: str,
    api_key: str,
    api_version: str,
) -> tuple[OpenAIChatModel, dict]:
    client = AsyncAzureOpenAI(
        azure_endpoint=endpoint.rstrip("/"),
        api_version=api_version,
        api_key=api_key,
    )
    return OpenAIChatModel(deployment_name, provider=OpenAIProvider(openai_client=client)), {}
