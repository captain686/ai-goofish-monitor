import asyncio
from types import SimpleNamespace

from src.services.ai_response_parser import extract_ai_response_content_async


class _Delta:
    def __init__(self, content=None):
        self.content = content


class _Choice:
    def __init__(self, content=None):
        self.delta = _Delta(content)


class _Event:
    def __init__(self, content=None, event_type=""):
        self.choices = [_Choice(content)]
        self.type = event_type
        self.delta = content
        self.text = content


class _AsyncStream:
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        self._iter = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


async def _read(stream):
    return await extract_ai_response_content_async(stream, stream=True)


def test_extract_ai_response_content_async_reads_async_stream_chunks():
    response = _AsyncStream(
        [
            _Event("hello", "response.output_text.delta"),
            _Event(" world", "response.output_text.delta"),
        ]
    )

    assert asyncio.run(_read(response)) == "hello world"


def test_extract_ai_response_content_async_falls_back_for_non_stream_response():
    response = SimpleNamespace(output_text="plain text")

    assert asyncio.run(_read(response)) == "plain text"
