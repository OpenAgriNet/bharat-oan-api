"""Offline boundary and retrieval tests; no website or LLM requests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import reader as module


class ReaderTests(unittest.TestCase):
    def test_external_and_private_urls_are_rejected(self):
        for url in ['http://agriwelfare.gov.in/', 'https://127.0.0.1/',
                    'https://agriwelfare.gov.in.evil.test/', 'https://agriwelfare.gov.in:8443/',
                    'https://user:pass@agriwelfare.gov.in/', '//localhost/file']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.safe_url(url)
        self.assertEqual(module.safe_url('/schemes/fpo#benefits'), module.SITE+'/schemes/fpo')

    def test_redirect_cannot_leave_site(self):
        async def check():
            transport = httpx.MockTransport(lambda request: httpx.Response(
                302, headers={'Location': 'http://169.254.169.254/latest/meta-data/'}))
            client = httpx.AsyncClient(transport=transport)
            with patch.object(module, 'public_host', AsyncMock()), patch.object(module.httpx, 'AsyncClient', return_value=client):
                with self.assertRaises(ValueError):
                    await module.download(module.SITE+'/document.pdf')
        asyncio.run(check())

    def test_scheme_ranking_beats_unrelated_documents(self):
        items = [{'url': '/schemes/pm-kisan', 'title': 'Pradhan Mantri Kisan Samman Nidhi PM-KISAN'},
                 {'url': '/jobs', 'title': 'Recruitment of stenographers'},
                 {'url': '/schemes/fpo', 'title': 'Farmer Producer Organisations'}]
        self.assertEqual(module.rank('PM KISAN benefits', items, 1)[0]['url'], '/schemes/pm-kisan')
        self.assertEqual(module.rank('FPO', items, 1)[0]['url'], '/schemes/fpo')

    def test_failure_is_not_answer_evidence_and_followed_pdf_keeps_page(self):
        async def check():
            reader = module.Reader()
            reader.entries = [{'url': module.SITE+'/schemes/fpo', 'title': 'FPO'}]
            page = {'url': module.SITE+'/schemes/fpo', 'title': 'FPO', 'fetched_at': 'now',
                    'cached': False, 'truncated': False,
                    'pages': [{'page': None, 'text': 'Read the FPO financial guidelines.'}],
                    'links': [{'url': module.SITE+'/guidelines.pdf', 'title': 'FPO guidelines'}]}
            pdf = {**page, 'url': module.SITE+'/guidelines.pdf', 'links': [],
                   'pages': [{'page': 7, 'text': 'FPO assistance requires the stated eligibility conditions.'}]}
            reader.read = AsyncMock(side_effect=[page, pdf])
            with tempfile.TemporaryDirectory() as tmp, patch.object(module, 'CATALOGUE', Path(tmp)/'catalogue.json'):
                result = await reader.research('FPO assistance', [])
                self.assertEqual(len(result['visited']), 2)
                self.assertTrue(any(e['page'] == 7 for e in result['evidence']))
                reader.read = AsyncMock(side_effect=ValueError('HTTP 403'))
                failed = await reader.research('FPO assistance', [])
                self.assertEqual(failed['status'], 'unavailable')
                self.assertFalse(failed['evidence'])
                self.assertIn('403', failed['failed_urls'][0]['reason'])
        asyncio.run(check())

    def test_catalogue_accepts_null_descriptions_and_excludes_external_links(self):
        found = module.Reader.valid_links([{'url': '/schemes/fpo', 'title': 'FPO', 'description': None},
                                          {'url': 'https://other.example/', 'title': 'Other'}])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['description'], '')


if __name__ == '__main__':
    unittest.main()
