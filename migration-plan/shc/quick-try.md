# Trying the Soil Health Card use cases

Assumes the stack is up — see [../stack-setup.md](../stack-setup.md). For what
changed and why, see [plan.md](plan.md).

SHC-specific setup, on top of the shared steps:

- The adapter image must include the GraphQL token login (network-adapter
  `feat/shc-token-exchange`).
- `make register-provider PROVIDER=shc` — needs `SHC_PARTICIPANT_ID`,
  `SHC_CAPABILITY`, `SHC_BASE_URL`, `SHC_MAPPING_URL`, `SHC_TOKEN_URL` and
  `SHC_REFRESH_TOKEN` in `.env` (see helmcharts `quick-start/.env.example`).
  Then `make configure-adapter`.
- Publish the SHC catalog (`cat-shc`, resource `shc-reports`).
- Extended (L2) schema validation off on the adapter, for now
  ([plan.md](plan.md#open-todos)).
- Set `SHC_BECKN_VERSION: v2` and `DEFAULT_API_READ_TIMEOUT: "75"` in the app's
  environment and recreate the app. Unset, the same questions run on v1.

---

## Two ways to ask

### The tool on its own

No model involved, so a failure here is a network or adapter failure rather than
a prompt problem. Run this first. The reply holds a real farmer's data: print
only what you need.

```sh
docker exec -i sva_app python - <<'PY'
import asyncio
from types import SimpleNamespace
from agents.tools.v2.shc_scheme_status import check_shc_status

out = asyncio.run(check_shc_status(SimpleNamespace(deps=SimpleNamespace()), "<registered phone>", "2024-25"))
print(out.splitlines()[0], "| reports:", out.count("## Report"))
PY
```

Expect `## Provider: <department>` and a report count. Each report takes SHC
15-26 s to produce, so the call takes about 30 s.

### The whole chat path

Ask in the chat UI: "Check my soil health card", then give the phone and the
cycle. The logs show the v2 path:

```sh
docker logs -f sva_app 2>&1 | grep 'SHC v2'
```

Expect: `discover found res-shc-reports from provider shc`, `select -> …`,
`got N reports, N usable`.

## Reading the result

| Log line | Means |
|---|---|
| `discover found no shc-reports resource` | the catalog is not published; the farmer sees "service unavailable" |
| `provider returned no report list: errors=['Not Authorized']` | the adapter's token login failed; check `SHC_REFRESH_TOKEN` |
| `dropped report at index N (…): no rdfValues` | v1 drops the same report |
| `got 0 reports` | "No data available." |
| `adapter returned 500` | the adapter or SHC failed; the adapter log says which |
| a timeout | `DEFAULT_API_READ_TIMEOUT` is below SHC's 15-26 s |

## Switching back to v1

Remove `SHC_BECKN_VERSION` from the app's environment and recreate the app.
