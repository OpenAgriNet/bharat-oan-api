# Trying the SMAM use cases

Assumes the stack is up — see [../stack-setup.md](../stack-setup.md). For what changed and
why, see [plan.md](plan.md).

SMAM-specific setup, on top of the shared steps:

- `make register-provider PROVIDER=smam` — needs `SMAM_PARTICIPANT_ID` (`smam`),
  `SMAM_BASE_URL`, `SMAM_TOKEN` and `SMAM_MAPPING_BASE_URL` in `.env` (see helmcharts
  `quick-start/.env.example`). Then `make configure-adapter`.
- Publish the SMAM catalog: one `SchemeApplication` resource, `smam-benefits`, with its offer.
  Its `@context` must be the official
  `https://openagrinet.github.io/network-specs/schema/SchemeApplication/v0.2/context.jsonld`.
- Extended (L2) schema validation off on the provider module, for now ([plan.md](plan.md#open-todos)).
- Set `SMAM_BECKN_VERSION: v2` in the app's environment and recreate the app. Unset, the same
  questions run on v1.

---

## Two ways to ask

### The tool on its own

No model involved, so a failure here is a network or adapter failure rather than a prompt
problem. The answer is a farmer's data: print only what you need.

```sh
docker exec -i sva_app python - <<'PY'
import asyncio
from types import SimpleNamespace
from agents.tools.v2 import smam_scheme_status as smam

ctx = SimpleNamespace(deps=SimpleNamespace(session_id="s", question_id="q"))
out = asyncio.run(smam.check_smam_scheme_status(ctx, "mobile", "<registered mobile>"))
print(out.count("## Application"), "applications |", out.splitlines()[0][:40])
PY
```

### The whole chat path

Ask in the chat UI: "What is the status of my SMAM application?", then give the mobile or
application number. The logs show the v2 path:

```sh
docker logs -f sva_app 2>&1 | grep 'SMAM v2'
```

Expect: `discover -> …`, `status -> …`, `N applications, success=True`.

## Reading the result

| Log line | Means |
|---|---|
| `res-smam-benefits is in no published offer` | the catalog was published without its offer |
| `discover failed` | the adapter refused discover; its log says why |
| `status refused: HTTP 4xx/5xx` | "SMAM application status service is unavailable." |
| `0 applications, success=False` | "Status: Failed. No application found" |

## Switching back to v1

Remove `SMAM_BECKN_VERSION` from the app's environment and recreate the app.
