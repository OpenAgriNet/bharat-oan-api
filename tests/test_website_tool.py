"""Reader transport checks without a website, model or business-service call."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from agents.tools.agriwelfare import research_agriwelfare


@pytest.mark.parametrize('status,payload', [
    (200, {'status': 'evidence_found', 'evidence': [{'text': 'Source passage', 'page': 4}]}),
    (503, {'error': 'Unavailable'}),
    (200, None),
])
def test_reader_transport_and_failures(monkeypatch, status, payload):
    monkeypatch.setenv('WEBSITE_READER_URL', 'http://reader.test:18128/')
    requests = []

    def handler(request):
        requests.append(request)
        if payload is None:
            return httpx.Response(status, text='not JSON')
        return httpx.Response(status, json=payload)

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('agents.tools.agriwelfare.httpx.AsyncClient', return_value=client):
            return await research_agriwelfare(
                SimpleNamespace(deps=SimpleNamespace(question_id='offline')),
                'FPO eligibility', ['https://agriwelfare.gov.in/schemes/fpo'])

    result = asyncio.run(run())
    assert str(requests[0].url) == 'http://reader.test:18128/research'
    assert json.loads(requests[0].content) == {
        'question': 'FPO eligibility', 'urls': ['https://agriwelfare.gov.in/schemes/fpo'],
        'request_id': 'offline',
    }
    if status == 200 and payload is not None:
        assert result == payload
    else:
        assert result['status'] == 'unavailable'
        assert result['evidence'] == []
