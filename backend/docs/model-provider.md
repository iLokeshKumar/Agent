# Provider-neutral model runtime

Yexis agents must not import a vendor SDK or make model HTTP calls directly.
Use `app.services.model_provider.ModelClient` and the existing system boundary:
agents produce typed proposals, while application services enforce policy,
approval, validation, audit, and writes to Zoho, Tally, or messaging systems.

## Configure a provider

The optional client uses `httpx`, already included in the backend dependencies.
Model calls are disabled by default. Configure these environment variables in
the runtime secret/configuration store:

| Variable | Purpose |
| --- | --- |
| `MODEL_PROVIDER` | `openai_compatible`, `anthropic`, or `disabled` |
| `MODEL_BASE_URL` | API base URL; for OpenAI-compatible APIs include `/v1` |
| `MODEL_NAME` | Model identifier understood by that endpoint |
| `MODEL_API_KEY` | API key; required by Anthropic, optional for local endpoints |
| `MODEL_TIMEOUT_SECONDS` | Per-request timeout (default 60 seconds) |

Examples (use a secret manager for keys; do not put real keys in source control):

```dotenv
# OpenAI
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=<model-enabled-for-your-account>
MODEL_API_KEY=<secret>

# Mistral: same adapter, different endpoint and model
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.mistral.ai/v1
MODEL_NAME=<model-enabled-for-your-account>
MODEL_API_KEY=<secret>

# Ollama on the same private network (usually no key)
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=http://ollama:11434/v1
MODEL_NAME=<installed-model-name>

# Anthropic native Messages API
MODEL_PROVIDER=anthropic
MODEL_BASE_URL=https://api.anthropic.com
MODEL_NAME=<model-enabled-for-your-account>
MODEL_API_KEY=<secret>
```

The first adapter targets the OpenAI chat-completions request/response shape,
which is also implemented by Mistral and local servers such as Ollama and vLLM.
Providers with a different API shape get a small adapter implementing the same
`ModelClient` interface. Do not rely on a provider's proprietary tool-calling
behavior for permission enforcement.

Example call:

```python
from app.services.model_provider import ChatMessage, get_model_client

draft = get_model_client().complete(
    system="Draft a response. Do not send it or claim an action was completed.",
    messages=[ChatMessage(role="user", content="Summarize this approved brief.")],
    max_tokens=500,
)
```

## Safe implementation sequence

1. Use an approved provider sandbox or authorized business records. Confirm
   the provider's data-retention, training, processing-region, and contractual
   terms before sending business data.
2. Use the model for drafting, classification, or extraction into a typed
   schema. Validate fields, ranges, source references, and allowed values in
   deterministic code; reject malformed or unsupported output.
3. Keep model-generated text and plans non-executable. The application
   re-fetches authoritative records, applies policy, creates an approval where
   required, and only then invokes an allowlisted integration.
4. Add golden, adversarial, and integration tests for each agent call site.
   Record provider/model/version, latency, token usage, validation outcome,
   and correlation ID; do not log secrets or raw sensitive prompts by default.
5. Run shadow mode first, compare against human-approved outcomes, and promote
   one lane at a time using its existing gate, action cap, and kill switch.

Changing `MODEL_PROVIDER` changes the configured backend only. It does not
automatically make all agents use a model: each agent needs a deliberate
call-site, output contract, policy boundary, and acceptance tests.

## First call site: K1 campaign draft shadow

K1 has an optional, draft-only call site, disabled by default. After enabling
`K1_MODEL_SHADOW_ENABLED`, a campaign-create request may receive a model draft
alongside its ordinary deterministic result as `output.model_shadow`. The draft
is not persisted, does not update the campaign, and cannot invoke K2/K3 or send
messages. An unavailable provider is reported under `model_shadow.status` and
does not silently replace the existing deterministic output.

Only the campaign type, channel, and optional objective/target-segment text are
sent to the selected provider; campaign names, entities, budgets, SKUs, contacts,
and database identifiers are excluded. Objective and segment text is still
business data, so review provider retention/region terms first. Prefer a
private local OpenAI-compatible endpoint during initial shadow evaluation.
Enable this setting only for an explicitly authorized environment, compare
draft quality with a human-approved baseline, and leave it disabled until
Marketing and data owners approve any next phase.
