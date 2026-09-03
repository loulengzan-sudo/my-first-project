# MODE 2 — Corpus Profile (know it before you code it)

Purpose: discover the **class prior**, **languages**, **dominant topics**, and — critically —
**off-topic contamination** before writing the codebook. Skipping this is how a scheme gets
silently poisoned (e.g. an ambiguous keyword pulling in a whole unrelated genre).

## What to produce (aggregates only — LOCAL_MODE safe)
- Row/unit counts; % missing per text field; text-length distribution.
- Language mix (metadata language tags or a fast detector).
- Distinctive keywords per stratum (TF-IDF, jieba/latin tokenized) → what each slice is about.
- Cross-cutting topics (NMF/LDA over titles) → recurring themes vs. noise.
- A rough **class prior** using a cheap lexicon (feeds MODE 4 over-sampling).

## How
Either run a small profiling script that emits the tables above, or invoke
`/scholar-compute` MODULE 1 (STM/BERTopic) for a full topic model. Emit only term lists,
counts, and topic tables — never raw documents.

## What it changes downstream
- **Codebook (MODE 3):** contamination you find becomes explicit OFFTOPIC exclusion rules; genres you find (news/entertainment merely *in* the language vs. discourse *about* it) become distinct classes.
- **Sampling (MODE 4):** the prior tells you which classes are rare and must be over-sampled.

> Lesson from practice: "content *in* language X" ≠ "discourse *about* X." If the corpus is
> keyword-retrieved, expect a large fraction to be generic content the keywords happened to
> catch. Name that as its own class so the target class stays clean.
