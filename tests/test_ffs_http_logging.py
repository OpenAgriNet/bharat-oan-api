"""Full received response logging uses isolated HTTP transport, never live FFS."""
import asyncio
import json

import httpx
import pytest

from app.services import ffs
from helpers import ffs_tracing
from tests.test_ffs import setup


@pytest.mark.parametrize('status,body,failed', [
    (200, '{ "code": 200, "data": {"userToken":"returned-token"} }\n', False),
    (200, '{"code":500,"message":"booking rejected","data":null}', True),
    (503, '<html>Provider unavailable\ntry later</html>', True),
    (200, 'invalid JSON\n', True),
])
def test_full_response_logged_before_validation(setup, caplog, status, body, failed):
    service = ffs.FFSService('response-session', 'owner', 'response-question')
    service.state['token'] = 'outbound-token'
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, content=body.encode())

    async def run():
        async with setup.client_class(transport=httpx.MockTransport(respond)) as client:
            service.client = client
            if failed:
                with pytest.raises(ffs.FFSError):
                    await service.call('POST', ffs.APPROVAL, '/bharatVistar/createMultipleBookingV2', {})
            else:
                result = await service.call('POST', ffs.APPROVAL, '/bharatVistar/createMultipleBookingV2', {})
                assert result == {'userToken': 'returned-token'}

    asyncio.run(run())
    records = [r for r in caplog.records if r.name == 'ffs.http']
    assert len(records) == 1 and len(calls) == 1
    line = records[0].getMessage()
    assert len(line.splitlines()) == 1
    event = json.loads(line.removeprefix('FFS_HTTP_RESPONSE '))
    assert event['response_body'] == body
    assert event['http_status'] == status
    assert event['session_id'] == 'response-session'
    assert event['question_id'] == 'response-question'
    assert event['url'] == str(calls[0].url)
    assert event['method'] == 'POST'
    assert 'outbound-token' not in line


def test_log_handler_failure_does_not_change_response(setup, monkeypatch):
    service = ffs.FFSService('response-session', 'owner', 'response-question')
    service.state['token'] = 'outbound-token'
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 200, 'data': {'accepted': True}})

    def broken_handler(*args, **kwargs):
        raise OSError('log sink unavailable')

    monkeypatch.setattr(ffs_tracing.http_logger, 'info', broken_handler)

    async def run():
        async with setup.client_class(transport=httpx.MockTransport(respond)) as client:
            service.client = client
            assert await service.call('POST', ffs.APPROVAL, '/bharatVistar/createMultipleBookingV2', {}) == {'accepted': True}

    asyncio.run(run())
    assert len(calls) == 1
