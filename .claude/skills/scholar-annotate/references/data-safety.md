# Data Safety (first-class, not an afterthought)

LLM annotation is a data-transfer decision. This skill bakes in the reasoning learned the hard way.

## Classify the corpus first (MODE 0 gate)
`SAFETY_STATUS ∈ {CLEARED, LOCAL_MODE, ANONYMIZED, HALTED}`.
- **CLEARED** — public, non-sensitive → any strategy (disclose cloud transfers).
- **LOCAL_MODE** — restricted / private / PII-bearing → **never `Read` raw rows**; run everything
  through the engine (aggregate-only stdout); **force `strategy=local`** (on-prem server; nothing
  leaves the machine). A cloud batch is allowed ONLY for explicitly-public fields with the user's
  authorization, logged.
- **ANONYMIZED** — de-identify first, then treat as its status warrants.
- **HALTED** — do not process.

## The core rules
1. **Cloud API = external transfer = publication.** OpenAI/Anthropic Batch/async send your text
   off-machine. For anything sensitive, use `local`. Every cloud call over a restricted corpus is
   a conscious, **logged** decision (write an AI-use disclosure to the project's audit log).
2. **Comments / transcripts / PII → anonymize-first** (Presidio or equivalent) before ANY external
   call. Metadata (titles/descriptions) is usually public; comment *text* usually is not.
3. **Local inference resolves the tension.** An on-prem GLM/Qwen/Llama via llama.cpp/vLLM annotates
   restricted data with zero external transfer — the preferred path at scale for private corpora.
4. **Aggregate-only outputs.** Emit counts, distributions, κ, cost — never raw rows or verbatim
   quotes into the conversation/trace.

## Disclosure template (log it)
> "[date] — [N] docs of [field types] sent to [provider/model] via [batch/async] for [task];
> [public/anonymized]; temperature=0; model id + date archived. Full corpus annotated [locally
> on <cluster> / via distillation] — no external transfer at scale."

## Auto-behavior
MODE 0 sets `SAFETY_STATUS`; MODE 8 refuses a cloud strategy under `LOCAL_MODE` unless the user
overrides for public fields (and then logs it). The disclosure is part of the MODE 10 packet.
