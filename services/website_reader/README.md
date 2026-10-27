# Agriwelfare website reader

Companion service for `research_agriwelfare(question, urls=None)`. It searches the
bundled JSON link catalogue, fetches up to three starting pages and two relevant
linked pages, and returns source passages to the conversational agent. HTML uses
Chromium; PDFs use pypdf with page numbers. No vector database or LLM runs here.
Only HTTPS URLs on agriwelfare.gov.in and www.agriwelfare.gov.in are accepted.

## Run

From the repository root, use Python 3.11 or newer in a separate environment:

```bash
python3 -m venv .venv/website-reader
.venv/website-reader/bin/pip install -r services/website_reader/requirements.txt
.venv/website-reader/bin/python -m playwright install --with-deps chromium
mkdir -p .cache/website-reader
cp services/website_reader/catalogue.json .cache/website-reader/catalogue.json
export WEBSITE_CATALOGUE_PATH="$PWD/.cache/website-reader/catalogue.json"
.venv/website-reader/bin/python services/website_reader/reader.py
```

The service binds to `127.0.0.1:18128`. The API tool uses that address by default;
set `WEBSITE_READER_URL` in the API environment if the reader has a different
private address. In Docker, loopback refers to the API container, so arrange
private networking explicitly. This reader has no authentication: keep it on
loopback or a trusted private network; do not expose it publicly.

`CHROMIUM_PATH` optionally selects an existing browser executable. Browser/PDF
dependencies are separate from the main API requirements and container image.
The catalogue path must be writable; newly discovered links are merged into it.
Refresh its metadata from the website with the same environment:

```bash
.venv/website-reader/bin/python services/website_reader/reader.py --seed
```

The bundled catalogue is a starting snapshot, not current answer evidence.
Only fetched passages are evidence. Extracted pages are cached in memory for
five minutes; link metadata persists in the JSON file.

## Agent integration

The tool is registered in the `website` capability. Existing capability allowlists
still apply. For a dedicated website-only assistant, configure the API with:

```text
CAPABILITY_ALLOWED_TOOLS=research_agriwelfare
CAPABILITY_PRELOADED_TOOLS=all
```

This selects the evidence-only website prompt, including source citations and
instructions to decline personal status checks and transactions. With a broader
allowlist the ordinary assistant prompt remains active. The tool description
provides evidence-handling rules; a lazy agent can load the `website` capability.
No request-level tool filtering or demo frontend is included here.

## Reader API and limits

`POST /research` accepts `question` (1–1500 characters), optional `urls` (up to
three same-site URLs) and optional `request_id`. It returns `status`, `evidence`,
`visited`, `failed_urls`, `stop_reason` and `next_links`. Each excerpt carries its
URL, title, fetch timestamp and PDF page number when applicable. `GET /health`
reports catalogue size; `GET /activity/{request_id}` reports recent visited/failed
URLs for a caller-supplied request ID.

Research has a 45-second deadline, three concurrent reads and a 24,000-character
returned-evidence budget. PDFs are limited to 12 MB and 120 pages. Scanned PDFs
require OCR, which is not included. Source dates are not inferred from fetch dates;
results do not establish that a document is current or that site coverage is complete.
Question matching uses English keywords and aliases; the agent translates queries
before calling the tool. Chromium extraction expects this site's `main` element.

## Offline checks

```bash
.venv/website-reader/bin/python -m unittest discover -s services/website_reader -p 'test_*.py'
python -m pytest tests/test_website_tool.py tests/test_capabilities.py tests/test_prompt_structure.py
```

These checks do not call the website, an LLM or business services. The published
Vercel demo and its running services are independent of this extracted branch.
