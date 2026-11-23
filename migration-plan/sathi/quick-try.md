# Trying the SATHI use cases

Assumes the stack is up — see [../stack-setup.md](../stack-setup.md). For what
changed and why, see [plan.md](plan.md).

SATHI-specific setup, on top of the shared steps:

- The adapter image must include the `AgricultureResource` step (network-adapter
  `feat/agriculture-resource-step`).
- `make register-provider PROVIDER=sathi` — needs `SATHI_PARTICIPANT_ID`,
  `SATHI_CAPABILITY`, `SATHI_BASE_URL`, `SATHI_MAPPING_URL` and `SATHI_API_KEY`
  in `.env` (see helmcharts `quick-start/.env.example`).
- Publish a SATHI catalog: the sample in helmcharts
  `quick-start/capability-examples/AgricultureResource/publish/`, or the full
  36-district catalog.
- Extended (L2) schema validation off on the adapter, for now
  ([plan.md](plan.md#open-todos)).
- Set `SATHI_BECKN_VERSION: v2` in the app's environment and recreate the app.
  Unset, the same questions run on v1.

---

## Two ways to ask

### The tool on its own

No model involved, so a failure here is a network or adapter failure rather than
a prompt problem. Run this first.

```sh
docker exec -i -e PYTHONPATH=/app sva_app python - <<'PY'
import asyncio, logging
logging.basicConfig(level=logging.INFO)
from agents.tools.v2.sathi_seed import list_sathi_crops, search_sathi_seed_availability

async def main():
    print(await list_sathi_crops(None, 19.9975, 73.7898))          # Nashik city
    print(await search_sathi_seed_availability(
        None, {"cropCode": "A0102", "stateCode": "27", "districtCode": "487"}, 19.9975, 73.7898))

asyncio.run(main())
PY
```

`list_sathi_crops` ends with a "Request fields" section; the search returns
"**Dealers with stock in Nashik, Maharashtra**" and ends with "**Source: SATHI**".

### The whole chat path

Ask in the chat UI. The logs show the v2 path:

```sh
docker logs -f sva_app 2>&1 | grep 'SATHI seed v2'
```

Expect, per question: `discover at … matched [...]`, `request fields from …`,
`selecting res-sathi-stock:27:<district> with fields [...]`.

## The cases worth running

| Ask | Expect |
|---|---|
| "Where can I buy certified paddy seed in Nashik district?" | dealers in Nashik, Maharashtra |
| "Where can I get paddy seed in Pune?" | Pune's dealers; the log shows `chose res-sathi-stock:27:490 … from ['Ahilyanagar', 'Pune']`, or the bot asks which district |
| "Barley seed availability in Nashik" | "not available"; the log shows `statusCode=409` |
| "Paddy seed dealers in Hyderabad" | the Maharashtra-only sentence, with no SATHI call |

## Reading the result

| Log line | Means |
|---|---|
| `matched []` | the point is outside every published district |
| `matched [two ids]` with no `chose` | the bot will ask which district |
| `corrected stateCode 'Maharashtra' to code 27` | the model passed a name; the tool fixed it |
| `skipped N dealer rows with no bags in stock` | normal; v1 drops them too |
| `fetching OpenAPI …` failing | `operationRef` in the catalog is unreachable; the farmer sees the service-error text |

## Switching back to v1

Remove `SATHI_BECKN_VERSION` from the app's environment and recreate the app.
v1 needs the old BAP and provider-backend, as before.
