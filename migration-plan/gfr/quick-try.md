# Trying the GFR use cases

Assumes the stack is up — see [../stack-setup.md](../stack-setup.md). For what changed and
why, see [plan.md](plan.md).

GFR-specific setup, on top of the shared steps:

- The adapter image must include the GraphQL token login (network-adapter #146).
- `make register-provider PROVIDER=gfr` — needs `GFR_PARTICIPANT_ID` (`gfr`), `GFR_BASE_URL`,
  `GFR_TOKEN_URL`, `GFR_REFRESH_TOKEN` and `GFR_MAPPING_BASE_URL` in `.env` (see helmcharts
  `quick-start/.env.example`). Then `make configure-adapter`.
- Publish the GFR catalog: the state `gfr-advice` resources and the `gfr-soil-report` resource.
- Extended (L2) schema validation off on the adapter, for now ([plan.md](plan.md#open-todos)).
- Set `GFR_BECKN_VERSION: v2` in the app's environment and recreate the app. Unset, the same
  questions run on v1.

---

## Two ways to ask

### The tools on their own

No model involved, so a failure here is a network or adapter failure rather than a prompt
problem. Run this first. The soil test is a real farmer's data: print only what you need.

```sh
docker exec -i sva_app python - <<'PY'
import asyncio
from types import SimpleNamespace
from agents.tools.v2 import gfr

ctx = SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"))
print(asyncio.run(gfr.gfr_get_crop_registries(ctx, 22.085, 79.55, crop_name_contains="barley")))
out = asyncio.run(gfr.gfr_get_recommendations(ctx, "<state id>", ["<crop id>"], "<registered phone>", "2024-25"))
print(out.splitlines()[:8])
PY
```

The first prints crop lines with their crop id and state id; the second starts with
"Fertilizer recommendation:".

### The whole chat path

Ask in the chat UI, for example "How much fertilizer should I apply for barley in Seoni,
Madhya Pradesh?", then give the phone, the cycle and "inorganic". The logs show the v2 path:

```sh
docker logs -f sva_app 2>&1 | grep 'GFR v2'
```

Expect: `discover matched [...]`, `select -> …` twice, `selecting res-gfr-advice:<lgd> for crops [...]`.

## Reading the result

| Log line | Means |
|---|---|
| `discover matched []` | the point is outside every published state, or the catalog is not published |
| `skipped res-…: gfr-advice but not openagrinet:AgricultureAdvisory` | a stale resource from an older publish; harmless |
| `advice found False` / `soil report found False` | one of the two resources is missing from the catalog |
| `no soil test for cycle …` | "No recommendation data found." |
| `soil test refused: ['Not Authorized']` | the adapter's login failed; check `GFR_REFRESH_TOKEN` |
| `adapter returned 4xx/5xx` | the farmer is asked to try again; the adapter log says why |

## Switching back to v1

Remove `GFR_BECKN_VERSION` from the app's environment and recreate the app.
