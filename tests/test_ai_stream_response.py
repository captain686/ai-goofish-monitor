import asyncio

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


class AsyncStream:
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


def test_read_ai_response_content_from_async_stream():
    response = AsyncStream([
        _Event("hello", "response.output_text.delta"),
        _Event(" world", "response.output_text.delta"),
    ])

    content = asyncio.run(extract_ai_response_content_async(response, stream=True))
    assert content == "hello world"
