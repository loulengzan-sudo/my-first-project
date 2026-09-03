## MODULE 7: LLM-Powered Analysis Workflows

> ## ROUTER — annotation-as-measurement belongs to `/scholar-annotate`
>
> **If the deliverable is a validated variable over a corpus** — a class, frame, stance, score,
> or relevance flag that will be counted, modelled, or reported — **stop and invoke
> `/scholar-annotate`.** That skill owns LLM-as-measurement end to end: codebook → dev/gold set
> → DSPy optimization → the **hard κ + coverage + construct-match gate (MODE 7)** → Batch/local/
> HPC scale-out → distillation. It ships an execution engine (`assets/annotate_engine.py`), not
> in-context snippets, and it is the only path with a mechanical gate between "we prompted a
> model" and "we have a variable."
>
> | You want | Go to |
> |---|---|
> | a labelled variable at corpus scale, with a reliability estimate | **`/scholar-annotate`** |
> | framing / stance / relevance coding to be analysed downstream | **`/scholar-annotate`** |
> | LLM labels feeding a regression (DSL / predicted-label correction) | **`/scholar-annotate`** MODE 9 |
> | interpretive, small-N, human-led qualitative coding | `/scholar-qual` |
> | LLM as *respondent* (silicon sampling, generative ABM) | `/scholar-simulate` |
> | ad-hoc extraction, RAG, document QA, an inductive discovery loop | **stay here** |
>
> **Why the split is enforced.** In the 2026-08 large-corpus annotation run, a
> measurement produced outside the annotate gate hard-coded a recall rate of `0.62` measured
> under a *broader* construct than the consuming model implemented. The correct value was
> `0.94`. It drove two declared sensitivity arms and a planned manuscript caveat, survived four
> code-review iterations, and was caught only when an estimator refused a degenerate fit. The
> gate that would have caught it lives in `/scholar-annotate` MODE 7. Do not reimplement a
> measurement pipeline here to avoid the gate.
>
> What remains in this module below is the **ad-hoc** half: extraction, RAG/document QA,
> grounded-theory discovery loops, and prompt-optimization technique. Those produce artifacts a
> researcher reads, not variables a model consumes.

### Step 1 — Goal and Risk Assessment

Before designing the LLM pipeline, classify the workflow type and apply corresponding safeguards:

| Workflow type | Goal | Key risk | Safeguard |
|--------------|------|----------|-----------|
| **Structured extraction** | Pull named entities, dates, amounts from documents | Hallucination of non-existent values | Schema validation + spot-check 10% |
| **Multi-step coding** | Assign substantive codes to text (theory-driven) | Validity drift; confidential leakage | κ ≥ 0.70 vs. humans; few-shot anchors |
| **Computational grounded theory** | Inductive category discovery | Premature closure; category proliferation | 3-iteration loop; researcher review each cycle |
| **Document QA / RAG** | Answer questions about a document corpus | Retrieval failures; fabricated citations | Always cite retrieved passage; human audit |

Apply all four Lin & Zhang (2025) epistemic risk checks (from MODULE 1 Step 7) before full deployment: validity, reliability, replicability, transparency. "Full deployment" is the MODULE 0.7 boundary — pilot review of ≤50 documents stays inline; the deployment pipeline consolidates to scripts and passes pre-scale review + `pre-exec-review-check.sh` first.

When a hand-written prompt for **multi-step coding** cannot clear the κ ≥ 0.70 gate, do not keep tweaking by hand — treat prompt construction as a scored search (**Step 4b**: APE, OPRO, DSPy, TextGrad) and report the search space, metric, and held-out κ.

---

### Step 2 — Structured Extraction (Pydantic + Claude)

```python
from anthropic import Anthropic
from pydantic import BaseModel, Field
from typing import Optional
import json

client = Anthropic()

class PolicyEvent(BaseModel):
    date:            Optional[str]   = Field(description="Date of policy event (YYYY-MM-DD)")
    jurisdiction:    Optional[str]   = Field(description="Jurisdiction (state, city, country)")
    policy_type:     Optional[str]   = Field(description="Type of policy (housing, immigration, labor, etc.)")
    direction:       Optional[str]   = Field(description="'restrictive' or 'permissive'")
    affected_group:  Optional[str]   = Field(description="Primary affected group if mentioned")
    confidence:      str             = Field(description="'high', 'medium', or 'low'")

EXTRACT_SYSTEM = f"""Extract structured information about policy events from news articles.
If a field cannot be determined from the text, return null for that field.
Return ONLY a JSON object matching this schema:
{PolicyEvent.schema_json(indent=2)}"""

def extract_event(text: str) -> PolicyEvent:
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        temperature=0,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user",
                   "content": f"Article:\n{text[:3000]}"}]
    )
    raw  = json.loads(msg.content[0].text)
    return PolicyEvent(**raw)

# Batch extraction with schema validation
records = []
errors  = []
for _, row in df.iterrows():
    try:
        event = extract_event(row["text"])
        records.append({"doc_id": row["id"], **event.dict()})
    except Exception as e:
        errors.append({"doc_id": row["id"], "error": str(e)})

import pandas as pd
pd.DataFrame(records).to_csv("${OUTPUT_ROOT}/tables/policy-events-extracted.csv", index=False)
print(f"Extracted: {len(records)} / Errors: {len(errors)}")
```

---

### Step 3 — Chain-of-Thought Multi-Step Coding

For theoretically nuanced coding tasks, use step-by-step reasoning before assigning the final code. This improves validity (Lin & Zhang 2025) by making the inference process auditable.

```python
COT_SYSTEM = """You are a sociologist coding newspaper articles about immigration using schema theory.
Follow these steps before assigning a frame code:
STEP 1: Identify the main subject of the article.
STEP 2: Note the most prominent metaphors or analogies used.
STEP 3: Identify the primary causal attribution (who/what is responsible?).
STEP 4: Based on steps 1-3, assign a frame code:
  - ECONOMIC: immigration framed primarily in terms of labor, economy, costs/benefits
  - SECURITY: immigration framed primarily in terms of crime, border security, national safety
  - HUMANITARIAN: immigration framed primarily in terms of human rights, family, asylum
  - CULTURAL: immigration framed primarily in terms of identity, values, national character
  - OTHER: does not fit cleanly into above categories
Respond with JSON: {"step1":"...", "step2":"...", "step3":"...", "frame":"ECONOMIC|SECURITY|HUMANITARIAN|CULTURAL|OTHER", "confidence":"high|medium|low"}"""

def code_frame_cot(text: str) -> dict:
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        temperature=0,
        system=COT_SYSTEM,
        messages=[{"role": "user", "content": f"Article:\n{text[:3000]}"}]
    )
    return json.loads(msg.content[0].text)
```

---

### Step 4 — Computational Grounded Theory Loop

Nelson (2020, *Sociological Methods & Research*) proposes an iterative cycle: unsupervised induction → manual interpretation → supervised deduction.

```python
# CYCLE 1: Inductive discovery via STM or k-means on embeddings
# (Run Step 3 or Step 6 from MODULE 1 to get initial topics/clusters)

# CYCLE 2: Researcher reviews top documents per cluster;
#           defines theoretically-grounded categories

categories_cycle2 = {
    "displacement_threat": "Articles framing immigrants as displacing native workers",
    "cultural_dilution":   "Articles framing immigration as threatening cultural values",
    "humanitarian_appeal": "Articles emphasizing suffering, rights, and asylum needs",
    "economic_benefit":    "Articles emphasizing immigrants' economic contributions"
}

# CYCLE 3: LLM assigns documents to researcher-defined categories (deductive)
DEDUCTIVE_SYSTEM = (
    "You are coding news articles into one of these categories defined by a sociologist:\n"
    + "\n".join(f"- {k}: {v}" for k, v in categories_cycle2.items())
    + "\nRespond ONLY with JSON: {\"category\": \"category_name\", \"confidence\": \"high|medium|low\"}"
)

def classify_grounded(text: str) -> dict:
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        temperature=0,
        system=DEDUCTIVE_SYSTEM,
        messages=[{"role": "user", "content": f"Article:\n{text[:2000]}"}]
    )
    return json.loads(msg.content[0].text)

# Validate Cycle 3 against human codes (κ ≥ 0.70 required)
```

**Reporting template:**
> "Following Nelson's (2020) computational grounded theory approach, we proceeded in three cycles. In Cycle 1, we applied Structural Topic Modeling (K = [X]) to inductively identify latent themes in the corpus. In Cycle 2, two authors reviewed the top [N] documents per topic and developed a [K]-category coding scheme. In Cycle 3, we used [Claude Sonnet 4.6] to classify all [N] documents into this scheme (agreement with human codes on 200-document sample: κ = [X]). All prompts and a random sample of LLM reasoning chains are reproduced in the Online Appendix."

---

### Step 4b — Prompt Optimization for Annotation: From Art to Search (APE · OPRO · DSPy · TextGrad)

Steps 2–4 hand-wrote the annotation prompt. That is the folklore era — "we tried a few prompts and this one felt best." Once the corpus is large and the codebook is fixed, prompt construction becomes a **model-selection step**: you search a space of instructions (and demonstrations) against a metric on labeled data, exactly as you would tune any other estimator. Lin & Zhang's (2025) four-step annotation procedure — codebook → pilot → metric gate → held-out validation — **is a hand-rolled optimization loop**; these tools automate the search, with **Cohen's κ as the objective function**, not a footnote.

**The four techniques (art → search):**

| Method | Reference | What it searches |
|--------|-----------|------------------|
| **APE** (Automatic Prompt Engineer) | Zhou et al. 2022, [arXiv:2211.01910](https://arxiv.org/abs/2211.01910) | An LLM *proposes* candidate instructions; an LLM *scores* them on a metric; keep the best |
| **OPRO** (Optimization by PROmpting) | Yang et al. 2023, [arXiv:2309.03409](https://arxiv.org/abs/2309.03409) | The LLM rewrites *its own* prompt against a metric each round ("Take a deep breath…" was *discovered* this way, not invented) |
| **DSPy** | Khattab et al. 2023, [arXiv:2310.03714](https://arxiv.org/abs/2310.03714); ICLR 2024 | A **framework**, not one algorithm: write module **signatures** (an I/O contract), then pick one of ~13 optimizers (see roster below) to search instructions and/or demonstrations. "Program, don't prompt." |
| **GEPA** (Genetic-Pareto) | Agrawal et al. 2025, [arXiv:2507.19457](https://arxiv.org/abs/2507.19457); ICLR 2026 (oral) | Reflective prompt **evolution**: keeps a Pareto frontier of prompts and edits them from **natural-language reflection** on your metric's per-item feedback. The NL-feedback idea (cf. TextGrad) built into DSPy (`dspy.GEPA`; also standalone `pip install gepa`); reported to beat MIPROv2 by ~13% with ~35× fewer rollouts |
| **TextGrad** | Yuksekgonul et al. 2024, [arXiv:2406.07496](https://arxiv.org/abs/2406.07496); *Nature* 2025 | "Textual gradients" — natural-language critique **backpropagated** through a compound-AI graph to rewrite the instruction. The standalone NL-gradient engine |

**When to reach for this** (vs. hand-writing a zero-shot prompt): the codebook is fixed, you have ≥ 20–50 gold-labeled items to search against, and a hand-written zero-shot prompt lands *below* the κ ≥ 0.70 gate — or you simply want a defensible, *reported* search instead of folklore. If zero-shot already clears ~0.80, optimization buys little; spend the labels on the validation set instead.

**DSPy — compile an annotation prompt against κ** (reuses the immigration-frame codebook from Step 3):

```python
# pip install -U dspy   — provider-agnostic (litellm backend); illustrative sketch, read the docs
import dspy
from sklearn.metrics import cohen_kappa_score

dspy.configure(lm=dspy.LM("anthropic/claude-sonnet-4-6", temperature=0))   # any provider

# 1. A SIGNATURE is the annotation task as an I/O contract, not a prompt string
class FrameCode(dspy.Signature):
    """Assign the dominant immigration frame to a news article."""
    article = dspy.InputField()
    frame   = dspy.OutputField(desc="one of ECONOMIC, SECURITY, HUMANITARIAN, CULTURAL, OTHER")

coder = dspy.ChainOfThought(FrameCode)        # a program, not a prompt

# 2. The METRIC *is* the objective function — the same κ gate you will report.
#    trainset/devset/testset are lists of dspy.Example(article=..., frame=...) carrying GOLD labels.
def kappa_metric(example, pred, trace=None):
    return example.frame == pred.frame.strip().upper()   # per-item; κ is computed on the set

# 3. COMPILE = search the instruction (MIPROv2) or the demonstrations (BootstrapFewShot)
optimizer = dspy.MIPROv2(metric=kappa_metric, auto="light")       # searches the INSTRUCTION
compiled  = optimizer.compile(coder, trainset=train_set, valset=dev_set)
# alt: dspy.BootstrapFewShot(metric=kappa_metric).compile(coder, trainset=train_set)  # searches DEMOS

# 4. REPORT the HELD-OUT score — never the training/dev score the search optimized
LABELS = ["ECONOMIC", "SECURITY", "HUMANITARIAN", "CULTURAL", "OTHER"]
gold = [ex.frame for ex in test_set]
pred = [compiled(article=ex.article).frame.strip().upper() for ex in test_set]
print("held-out kappa:", cohen_kappa_score(gold, pred, labels=LABELS))

compiled.save("${OUTPUT_ROOT}/scripts/72-compiled-frame-coder.json")   # archive the fitted instrument
```

**DSPy is a menu, not one optimizer — swap only the `optimizer =` line.** The Signature, the κ metric, and the held-out reporting stay identical; you choose the optimizer by *what the bottleneck is* (demonstrations vs. instruction wording vs. model weights) and *how much gold you have*:

| DSPy optimizer | Tunes | Reach for it when |
|----------------|-------|-------------------|
| `LabeledFewShot` | demos | drop in *k* gold examples as demonstrations — no search, ~free |
| `BootstrapFewShot` | demos | small gold set (≈ ≤ 50); bootstraps demos from the model's *own* correct outputs |
| `BootstrapFewShotWithRandomSearch` (`BootstrapRS`) | demos | more gold (≈ 50+); random-searches over bootstrapped demo sets — costs real API calls |
| `KNNFewShot` | demos | choose demonstrations *per input* by nearest-neighbor retrieval |
| `COPRO` | instructions | beam-search rewrite of the instruction text only |
| `MIPROv2` | demos + instructions | **the workhorse** — Bayesian search over instructions *and* demos; budget via `auto="light"/"medium"/"heavy"` |
| `GEPA` | demos + instructions | reflective evolution from NL feedback; needs a metric returning `dspy.Prediction(score=…, feedback=…)`; best when the codebook needs *explained* fixes |
| `SIMBA` | demos + instructions | self-reflects on the hardest training items to refine the prompt |
| `InferRules` | demos + instructions | induces explicit natural-language *rules* and folds them into the instruction |
| `BootstrapFinetune` | demos + **weights** | you control the model (open-weights/local) — distills the prompt into the weights |
| `BetterTogether` | demos + instructions + **weights** | alternate prompt optimization and finetuning |
| `Ensemble` | — | combine several compiled programs into one |

*(`AvatarOptimizer` also exists but targets tool-using `Avatar` agents, not annotation programs — out of scope here.)*

**The docs' own default, and why it fits annotation:** *"Demo-tuning tends to overfit; instruction-tuning tends to generalize… if your evaluation set is small or skewed, lean toward instruction optimization."* Gold-labeled annotation sets are exactly that — small and often class-skewed — which is why the demo below shows instruction optimizers (MIPROv2, TextGrad) clearing the κ gate while demonstration selection (BootstrapFewShot) stalls. **Start with `MIPROv2(auto="light")`;** reach for `GEPA` when you want the optimizer to *explain* its edits from your metric's feedback; use `BootstrapFinetune`/`BetterTogether` only if you can fine-tune the model's weights.

**TextGrad — rewrite the instruction from natural-language feedback:**

```python
# pip install textgrad   — "an autograd engine for textual gradients"; illustrative sketch
import textgrad as tg
tg.set_backward_engine("anthropic/claude-sonnet-4-6")

instruction = tg.Variable(
    "Assign the dominant immigration frame (ECONOMIC/SECURITY/HUMANITARIAN/CULTURAL/OTHER).",
    requires_grad=True, role_description="annotation instruction")

model     = tg.BlackboxLLM("anthropic/claude-sonnet-4-6", system_prompt=instruction)
optimizer = tg.TGD(parameters=[instruction])           # textual gradient descent

for article, gold in train_pairs:                      # a few dozen labeled items
    pred = model(tg.Variable(article, requires_grad=False, role_description="article"))
    loss = tg.TextLoss(f"Gold frame is {gold}. Critique the instruction so this item is labeled correctly.")(pred)
    loss.backward()        # NL feedback backpropagated through the graph
    optimizer.step()       # rewrites the instruction in place
print(instruction.value)   # the improved instruction — now validate on a HELD-OUT set with κ
```

**Which tool for an instrument?** For annotation you have *gold labels*, so κ on those labels is the right objective — that is **DSPy's turf** (MIPROv2 and GEPA search the instruction; BootstrapFewShot selects demonstrations). TextGrad's edge is objectives that live **only in words** — an essay, a plan, a free-text answer judged by a rubric — where there is no κ to compute; optimizing toward an LLM judge's taste is weaker than optimizing toward gold labels. APE/OPRO sit in between: lighter-weight instruction search you can run with a few dozen API calls.

**The empirical lesson** (runnable, self-contained demo bundled at [`references/prompt-optimization-demo/`](prompt-optimization-demo/README.md) — `qwen2.5:7b` local via Ollama, 20 training items, a few steps — a real but modest gain that shows the *mechanism*, not a benchmark). On a codebook that *contradicts* the model's prior:

| Method | What it tuned | κ (held-out) |
|--------|---------------|--------------|
| Hand-written zero-shot (baseline) | — | 0.667 |
| TextGrad | the **instruction** | **0.750** |
| DSPy · MIPROv2 | the **instruction** | **0.750** |
| DSPy · BootstrapFewShot | the **demonstrations** | 0.667 |

**Optimizing the instruction moved the needle; selecting demonstrations did not** — BootstrapFewShot only bootstraps examples the model *already* gets right, so it never surfaces the hard cases where the codebook fights the model's intuition.

**Reporting template:**
> "We treated prompt construction as a model-selection step rather than authorship. Using [DSPy MIPROv2 / TextGrad / OPRO], we searched over [instructions / instructions + few-shot demonstrations] to maximize Cohen's κ on an [N]-item training split, selected the compiled prompt on an [N]-item development split, and report agreement on a **held-out** [N]-item test split (κ = [X]; hand-written zero-shot baseline κ = [Y]). The search space, metric, optimizer, and compiled prompt are archived at [repo DOI]. We report the held-out κ rather than the development κ because a prompt fitted to a metric can overfit."

**Caveat — a compiled prompt is a fitted instrument, not a string.** Because the search optimizes against data, it can overfit; the held-out test split is mandatory and must not be touched during compilation. Archive the *compiled* prompt verbatim (DSPy: `program.save(...)`; TextGrad: the final `instruction.value`) — it, not your source prompt, is what ran. Once a prompt is chosen by a scored search on data, the folklore era is over: you report the *search space*, the *metric*, and the *held-out score*, exactly as you would for any other model-selection step in a paper.

---

### Step 5 — RAG / Document QA with FAISS

For large document collections where you need to answer specific questions or retrieve relevant passages:

```python
from sentence_transformers import SentenceTransformer
import faiss, numpy as np

# Build vector index
embedder = SentenceTransformer("all-mpnet-base-v2")   # 768-dim embeddings

def build_index(texts: list[str], chunk_size: int = 500) -> tuple:
    """Chunk documents and build FAISS index."""
    chunks, chunk_ids = [], []
    for doc_id, text in enumerate(texts):
        words  = text.split()
        for i in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[i:i+chunk_size]))
            chunk_ids.append(doc_id)

    embeddings = embedder.encode(chunks, show_progress_bar=True,
                                 batch_size=64, normalize_embeddings=True)
    index = faiss.IndexFlatIP(embeddings.shape[1])   # inner product = cosine for L2-normalized
    index.add(embeddings.astype("float32"))
    return index, chunks, chunk_ids

def query(question: str, index, chunks, top_k=5) -> list[str]:
    q_emb = embedder.encode([question], normalize_embeddings=True).astype("float32")
    _, ids = index.search(q_emb, top_k)
    return [chunks[i] for i in ids[0]]

def answer_with_rag(question: str, context_chunks: list[str]) -> str:
    context = "\n\n---\n\n".join(context_chunks)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        temperature=0,
        system="You are a research assistant. Answer the question based ONLY on the provided passages. If the answer is not in the passages, say 'Not found in corpus.'",
        messages=[{"role": "user",
                   "content": f"Passages:\n{context}\n\nQuestion: {question}"}]
    )
    return msg.content[0].text

# Example usage
index, chunks, chunk_ids = build_index(df["text"].tolist())
context   = query("What are the main arguments for restrictive immigration policy?", index, chunks)
answer    = answer_with_rag("What are the main arguments for restrictive immigration policy?", context)
print(answer)
```

---

### Step 6 — LLM Analysis Reproducibility

```python
import hashlib, json, datetime

def reproducible_prompt_call(system: str, user_msg: str,
                              model="claude-sonnet-4-6") -> dict:
    """Wrapper that records prompt hash, model, date for replicability."""
    prompt_hash = hashlib.sha256(
        (system + user_msg).encode()).hexdigest()[:12]
    result = client.messages.create(
        model=model,
        max_tokens=500,
        temperature=0,   # REQUIRED for replicability
        system=system,
        messages=[{"role": "user", "content": user_msg}]
    )
    return {
        "response":     result.content[0].text,
        "model":        model,
        "date":         datetime.date.today().isoformat(),
        "prompt_hash":  prompt_hash,
        "input_tokens": result.usage.input_tokens,
        "output_tokens":result.usage.output_tokens
    }
```

**Reproducibility checklist for LLM analysis:**
- temperature = 0 for all production annotation calls
- Exact model ID (including version suffix) archived
- Annotation date recorded (model behavior can change across releases)
- Full system + user prompts archived verbatim
- Raw LLM outputs saved alongside derived codes

---

### Step 7 — LLM Analysis Verification (Subagent)

```
LLM ANALYSIS VERIFICATION REPORT
==================================

RISK ASSESSMENT (Lin & Zhang 2025)
[ ] Validity: LLM coding of intended construct confirmed via pilot + rationale review
[ ] Reliability: run-to-run κ on 50-doc subsample reported; temperature=0 used
[ ] Replicability: model name + version + date + prompts archived
[ ] Transparency: prompts reproduced in supplementary; limitations discussed

STRUCTURED EXTRACTION (if used)
[ ] Pydantic schema documented and reproduced
[ ] Error rate (extraction failures / schema violations) reported
[ ] 10% spot-check against source documents conducted

CHAIN-OF-THOUGHT CODING (if used)
[ ] Reasoning steps documented in prompt
[ ] Sample CoT chains included in supplementary

GROUNDED THEORY (if used)
[ ] All 3 cycles documented (inductive → interpretive → deductive)
[ ] Category definitions reproduced verbatim
[ ] κ from Cycle 3 deductive coding ≥ 0.70

PROMPT OPTIMIZATION (if used — APE / OPRO / DSPy / TextGrad)
[ ] Search space (instructions vs. demonstrations), metric (κ gate), and optimizer named
[ ] Train / dev / held-out split documented; reported κ is the HELD-OUT score, not the dev score
[ ] Compiled prompt archived verbatim (it is a fitted instrument, not a string)
[ ] Hand-written zero-shot baseline κ reported for comparison

RAG / DOCUMENT QA (if used)
[ ] Chunk size and embedding model documented
[ ] Retrieval quality assessed (sample queries spot-checked)
[ ] Answers grounded in retrieved passages (no hallucinated citations)

COST
[ ] Total token usage and estimated cost reported
[ ] Cost included in Methods or budget note

RESULT: [PASS / NEEDS REVISION]
Issues: [list]
```

---

