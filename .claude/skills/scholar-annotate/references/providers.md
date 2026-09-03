# Providers

`assets/providers.py` unifies live + batch calls across three backends. Set via the manifest's
`provider` block; keys from env. temperature=0 everywhere.

## Backends
| provider | live | batch | key_env | notes |
|---|---|---|---|---|
| `openai` | chat.completions (+`response_format=json_object`) | **Batch API** (JSONL upload → poll → download) | `OPENAI_API_KEY` | `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o-mini`, … |
| `anthropic` | messages | **Message Batches** | `ANTHROPIC_API_KEY` | `claude-opus-4-8`, `claude-sonnet-4-6`, … |
| `local` | OpenAI-compatible chat | (async only) | none (`sk-local`) | llama.cpp `llama-server`, `ollama`, `vLLM`; set `api_base` |

## Local server endpoints
- **llama.cpp:** `llama-server -m model.gguf -ngl 999 -sm row -c 65536 --parallel 16 --port 8080` → `api_base=http://127.0.0.1:8080/v1`
- **ollama:** `ollama serve` (`OLLAMA_NUM_PARALLEL=8`) → `http://127.0.0.1:11434/v1`, model = the pulled tag
- **vLLM:** `python -m vllm.entrypoints.openai.api_server --model … --port 8080` → `http://127.0.0.1:8080/v1`

## Cost
`estimate_cost()` uses a small price table (VERIFY current vendor pricing — it drifts). Batch ≈
half list price. Local = $0 (compute only). Always `--dry-run` before a paid full run.

## Verify before a big run
- Confirm the exact **model id** exists for the provider (ids change; the engine can't check).
- Local: hit `GET /v1/models`; run `--limit 500` and eyeball a few JSON outputs (some local
  servers need `--chat-template` or don't honor `response_format` — the parser is defensive but
  check).
