"""Opt-in real Gemma test with the complete chat prompt and synthetic FFS only."""
import asyncio
import json
import os
from pathlib import Path

import httpx
from pydantic_ai import models
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
import pytest

from app.services import ffs
from agents.tools.ffs import ffs_get_entitlement, ffs_find_shops
from tests.test_ffs import setup, login, context

pytestmark = pytest.mark.skipif(os.environ.get('FFS_LIVE_CLOSING_TEST') != '1',
                               reason='Opt-in loopback Gemma proxy; FFS remains synthetic')


@pytest.mark.parametrize('language', ['en', 'hi'])
@pytest.mark.parametrize('waitlisted', [False, True])
def test_confirmed_and_waitlisted_closing_is_neutral(setup, monkeypatch, language, waitlisted):
    import agents.agrinet as module
    monkeypatch.setattr(models, 'ALLOW_MODEL_REQUESTS', True)
    monkeypatch.setattr(module, 'format_vector_schemes_prompt_block', lambda: {
        'vector_scheme_count':1, 'vector_schemes_bullets':'FFS', 'vector_schemes_identifiers':'ffs'})

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.network = httpx.AsyncHTTPTransport()
        async def handle_async_request(self, request):
            if request.url.host == 'ffs.test':
                return await setup.backend.respond(request)
            assert request.url.host == '127.0.0.1' and request.url.port == 18167
            return await self.network.handle_async_request(request)
        async def aclose(self):
            await self.network.aclose()

    class Client(setup.client_class):
        def __init__(self, **kwargs):
            kwargs.setdefault('transport', Transport())
            super().__init__(**kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', Client)

    async def run():
        async with Client(timeout=120, trust_env=False) as client:
            model = OpenAIChatModel('google/gemma-4-31b-it', provider=OpenAIProvider(
                base_url='http://127.0.0.1:18167/v1', api_key='test', http_client=client))
            await login()
            await ffs_get_entitlement(context('crop'), 'village-1', crop_name='maize', crop_code='0101000')
            await ffs_find_shops(context('order'), order=[ffs.OrderItem(product='urea', product_id=1089, bags=1)])
            if waitlisted:
                state = await ffs.get_session('session-1')
                shop = state['shops']['last_result']['data']['shops'][0]['shop_ref']
                retailer = next(s['retailer_id'] for s in state['shops']['pool'].values() if s['shop_ref'] == shop)
                setup.backend.booking_data_override = {
                    'isValid':True, 'totalBookingsRequested':1, 'totalBookingsSuccessful':1, 'totalBookingsFailed':0,
                    'bookingResults':[{'retailerId':retailer, 'isValid':True, 'isWaitlisted':True,
                        'procurementApplicationCode':'WAIT-TEST-001', 'procurementId':123456,
                        'waitlistExpiresOn':'2026-11-16 23:00:00'}]}
            query = ('Book the one bag of urea from the first shop. I confirm.' if language == 'en'
                     else 'पहली दुकान से एक बोरी यूरिया की बुकिंग पक्की कर दो। मेरी सहमति है।')
            deps = context('book', query=query).deps
            deps.lang_code = language
            deps.ffs_context = await ffs.prepare_chat_turn('session-1')
            result = await module.agrinet_agent.run(deps.get_user_message(), deps=deps, model=model)
            output = Path(os.environ.get('FFS_CLOSING_AUDIT_DIR', '/tmp/ffs-chat-closing'))
            output.mkdir(parents=True, exist_ok=True)
            (output/f'closing-{language}-{waitlisted}.json').write_text(json.dumps(
                {'output':result.output}, ensure_ascii=False, indent=2))
            neutral = 'Can I help with anything else?' if language == 'en' else 'क्या मैं आपकी किसी और बात में मदद कर सकती हूँ?'
            assert result.output.endswith(neutral), result.output
            assert result.output.count('?') == 1
            assert len(setup.backend.booking_payloads) == 1
            # The separate suggested-question generator must obey the same rule.
            from agents.suggestions import suggestions_agent
            suggestions = await suggestions_agent.run(
                'Conversation History: User: ' + query + '\nAssistant: ' + result.output +
                '\nGenerate Suggestions In: ' + ('English' if language == 'en' else 'Hindi'), model=model)
            assert 3 <= len(suggestions.output) <= 5
            (output/f'suggestions-{language}-{waitlisted}.json').write_text(json.dumps(
                suggestions.output, ensure_ascii=False, indent=2))
    asyncio.run(run())
