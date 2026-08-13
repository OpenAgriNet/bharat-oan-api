# Model configuration and execution

All runtime LLM policy lives in `config/models.yaml`.

The `models` section defines how to construct a provider client. Environment
variables supply deployment-specific endpoints and credentials; Python code does
not choose providers from environment flags.

The `use_cases` section defines execution policy for each feature:

- `default_alias`: model used when the caller does not supply a routed alias.
- `aliases` and `proportions`: eligible primary models and their routing split.
- `timeout_seconds`: per-attempt timeout.
- `fallbacks`: ordered model aliases to try after any execution error or timeout.
- `routing_ttl_seconds`: session stickiness for routed use cases such as Agrinet.

`agents.model_registry.ModelRegistry` loads the YAML, resolves `${ENV_VAR}`
references, validates aliases and policies, and lazily constructs model clients.
`agents.model_service.ModelService` is the only execution-policy layer: callers
give it a use-case name and an async function, and it supplies each configured
model in order until one succeeds.

## Agrinet text flow

1. `app.services.agrinet_routing` selects or restores the sticky primary alias.
2. `app.services.chat` asks `ModelService` to execute the `agrinet` use case.
3. `ModelService` obtains the model and timeout from `ModelRegistry`.
4. On an error or timeout, it follows `use_cases.agrinet.fallbacks`.
5. If fallback succeeds, the session route is updated to the successful alias.
6. Streaming retries are allowed only before the first response chunk is emitted.

## Moderation flow

1. `app.services.chat._run_moderation` asks `ModelService` to execute the
   `moderation` use case using its configured default alias.
2. The service overrides the moderation agent's model for that attempt.
3. On an error or timeout, it follows `use_cases.moderation.fallbacks` exactly as
   it does for Agrinet.
4. Langfuse records the alias that actually succeeded and whether fallback ran.

Fallback cycles are permitted in configuration. The service de-duplicates the
chain and attempts each alias at most once per execution.
