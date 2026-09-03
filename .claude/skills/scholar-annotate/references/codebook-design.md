# MODE 3 — Codebook Design (the measurement instrument)

The codebook is the single source of truth: `codebook_schema.py` compiles it into BOTH the
LLM system prompt AND the output JSON schema/validator. Author it once, in markdown, with an
embedded schema block.

## Required structure of `codebook.md`
1. **Prose (becomes the system prompt):** for each class, a one-line definition, include
   examples, exclude/boundary rules, and a **decision order for ties**. Add 1–2 short boundary
   exemplars. State what makes the *target* class distinct from look-alikes.
2. **One fenced `json` schema block** declaring the machine contract:
```json
{"out_fields": ["relevance","discourse_frame","confidence"],
 "labels": {
   "relevance": ["ON_TOPIC","ADJACENT","BACKGROUND","PROMOTIONAL","OFFTOPIC","UNCLEAR"],
   "discourse_frame": ["frame_a","frame_b","...","none"],
   "confidence": ["high","medium","low"]},
 "conditional": {"discourse_frame": {"only_if": {"relevance": "ON_TOPIC"}, "else": "none"}}}
```
`rationale` (free text, one sentence) is always appended — it makes each label auditable
(Lin & Zhang validity) and is required for spot-checking.

## Principles
- **Measure the act, not the surface.** "Is *about* X" vs. "is merely *in* X" is usually the
  hardest, most important distinction — spell out the test.
- **Decision order** resolves multi-label overlap deterministically (e.g. OFFTOPIC first if
  clearly out-of-scope, then the rare target class, then the common catch-alls).
- **Conditional fields** (sub-codes only under a parent class) keep the schema honest — the
  validator coerces them to the default otherwise.
- **Reliability targets:** state them (human α ≥ 0.70; LLM-vs-gold κ ≥ 0.70) so MODE 7 has a bar.

## Validate the codebook compiles
```bash
python3 -c "import assets.codebook_schema as c; cb=c.load('codebook.md'); print(cb['out_fields']); print(cb['system'][:400])"
```
If a class is ambiguous to *you*, it will be to the model — tighten the definition before annotating.
