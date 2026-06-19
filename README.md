# auditk × TRAIL — cross-taxonomy comparison

Reproducibility code and data for the cross-taxonomy comparison in [auditk](https://github.com/auditk/auditk) — the open standard for cryptographically attested intent–enactment drift measurement in agentic AI systems.

This experiment compares the auditk intent–enactment drift taxonomy against the TRAIL error taxonomy (Deshpande et al., 2025) on 31 SWE-bench traces. The goal is to determine whether the two instruments measure the same construct or distinct ones.

---

## Key findings

| Metric | Value |
|--------|-------|
| Traces analysed | 31 (SWE-bench split) |
| Total steps | 460 |
| Steps with TRAIL errors | 166 |
| NLI gate recall on TRAIL errors | **0.60** |
| Additional steps flagged by auditk not in TRAIL | **163** |
| TRAIL-labelled steps scored faithful by NLI gate | 63 |

**Conclusion:** The instruments empirically measure distinct constructs, not the same thing twice. The 0.60 NLI recall means the two taxonomies partially overlap — TRAIL errors are a significant signal for the auditk NLI gate — but 163 additional auditk-flagged steps lie entirely outside TRAIL's coverage window, and 63 TRAIL-labelled steps are correctly scored faithful by auditk (constraint violations and formatting errors, not intent–enactment drift).

---

## What TRAIL is

TRAIL (Deshpande et al., 2025) is a step-level error taxonomy over OpenTelemetry traces from smolagents running SWE-bench tasks. It annotates errors at the span level using categories including Instruction Non-compliance, Task Orchestration, Tool-related, Resource Abuse, and others.

**Key difference from auditk:** TRAIL classifies *errors* (things that went wrong) using human or automated annotation over execution traces. auditk measures *intent–enactment drift* — the gap between what the agent declared it would do and what it did. A TRAIL "Instruction Non-compliance" may or may not be a drift event; a constraint violation that the agent declared it would honour is drift, but a formatting error is not.

The cross-taxonomy comparison uses a **Thought-as-intent approximation**: the smolagents Thought block (the model's explicit reasoning before acting) is treated as the declared intent, and the subsequent Code block is the action.

---

## Taxonomy alignment table (top pairings)

From `data/trail_steps_judged.jsonl`:

| auditk label | TRAIL category | Count |
|---|---|---|
| `faithful` | *(no TRAIL error)* | ~260 |
| `faithful` | Instruction Non-compliance | 44 |
| `faithful` | Task Orchestration | 9 |
| `benign_elaboration` | *(no TRAIL error)* | 22 |
| `goal_deviation` | *(no TRAIL error)* | 15 |
| `goal_deviation` | Task Orchestration | 5 |

The `faithful` / TRAIL-error cells confirm the distinct-constructs claim: TRAIL flags errors auditk correctly scores faithful (constraint violations are not drift).

---

## Pipeline

Three sequential scripts, each reading the previous stage's output:

```
src/parse_trail.py  →  data/trail_steps.jsonl
                          ↓
src/nli_gate.py     →  data/trail_steps_nli.jsonl
                          ↓
src/judge.py        →  data/trail_steps_judged.jsonl
```

### Stage 1 — `parse_trail.py`

Loads `PatronusAI/TRAIL` from HuggingFace (SWE-bench split, 31 traces), walks the OpenTelemetry span trees, and extracts `(thought, code, trail_labels)` triples for each step. Handles the smolagents `Thought: ... Code: ```py...``` ` format.

**Requires:** HuggingFace account with TRAIL dataset access accepted at https://huggingface.co/datasets/PatronusAI/TRAIL

### Stage 2 — `nli_gate.py`

Runs `cross-encoder/nli-deberta-v3-small` over each `(thought, code)` pair as an asymmetric entailment test: does the Thought (declared intent) entail the Code (action)? Steps labelled `entailment` pass through as `faithful`; `neutral` and `contradiction` are forwarded to the judge.

**Requires:** `sentence-transformers`, GPU optional (runs on CPU).

### Stage 3 — `judge.py`

Runs DeepSeek V3 via Fireworks AI over the NLI-flagged steps to assign the full auditk taxonomy label (`faithful` / `benign_elaboration` / `goal_deviation` / `instruction_noncompliance` / `undeclared_goal`) plus severity and evidence quote. Checkpoints as it goes — safe to interrupt and resume.

**Requires:** `FIREWORKS_API_KEY` environment variable.

---

## Reproduction

```bash
# 1. Install dependencies
pip install datasets tqdm sentence-transformers httpx

# 2. Run Stage 1 (requires HF TRAIL access)
python src/parse_trail.py

# 3. Run Stage 2 (NLI gate — ~5 min on CPU)
python src/nli_gate.py

# 4. Run Stage 3 (LLM judge — requires Fireworks key, ~$1 at current pricing)
export FIREWORKS_API_KEY=your_key_here
python src/judge.py
```

Stages 2 and 3 can be skipped: the pre-computed output files in `data/` reproduce the experiment results directly.

---

## Pre-computed data

The `data/` directory contains the pre-computed outputs of all three stages:

| File | Description | Size |
|------|-------------|------|
| `trail_steps.jsonl` | Parsed steps from TRAIL (Stage 1 output) | ~960 KB |
| `trail_steps_nli.jsonl` | Steps with NLI labels (Stage 2 output) | ~960 KB |
| `trail_steps_judged.jsonl` | Steps with full auditk labels (Stage 3 output) | ~1.1 MB |

Each record in the judged file has the shape:
```json
{
  "trace_id": "...",
  "step_number": 3,
  "thought": "I need to read the failing test to understand what's expected...",
  "code": "with open('tests/test_foo.py') as f:\n    print(f.read())",
  "trail_labels": [{"category": "Task Orchestration", "...": "..."}],
  "has_trail_error": true,
  "nli_label": "entailment",
  "nli_confidence": 0.94,
  "auditk_label": "faithful",
  "auditk_confidence": 0.94,
  "auditk_reasoning": "NLI gate: entailment — marked faithful without judge"
}
```

**Data provenance:** `trail_steps.jsonl` contains content extracted from `PatronusAI/TRAIL` (HuggingFace), used here for non-commercial research under the dataset's terms. NLI and judge labels are model-generated outputs from this experiment.

---

## Limitations and methodology notes

**Thought-as-intent approximation.** smolagents does not use an explicit pre-plan mechanism like `TodoWrite`. The Thought block is used as a proxy for declared intent. This is an approximation — Thoughts are reasoning, not formal intent declarations.

**Manual review of judge outputs.** The LLM judge (DeepSeek V3) was run without causal masking (the judge sees the full step context, not just what the agent knew at that point). This is a known limitation (curse-of-knowledge overestimation). Causal masking is listed as future work in the auditk roadmap.

**Peak-end evaluation bias.** Manual review of judge outputs was structured to evaluate each step before seeing subsequent steps, following Kahneman's peak-end effect mitigation protocol.

---

## Citation

If you use this experiment in your research, please cite the auditk software:

```bibtex
@software{dawson2026auditk,
  title={auditk: an open standard for cryptographically attested intent--enactment drift measurement in agentic AI systems},
  author={Dawson, Matt},
  year={2026},
  url={https://github.com/auditk/auditk}
}
```

---

## Related

- [`auditk`](https://github.com/auditk/auditk) — Python reference implementation
- [`auditk-spec`](https://github.com/auditk/auditk-spec) — Protocol specification
- [TRAIL dataset](https://huggingface.co/datasets/PatronusAI/TRAIL) — Deshpande et al., 2025

## Annotation tool

A browser-based annotation tool for the gold set is in `annotation_tool/index.html`.

**Important:** the server must be started from the **project root**, not the `annotation_tool/` subdirectory. The tool fetches data files using paths relative to the project root.

```bash
# Start the server from the project root:
bash annotation_tool/serve.sh

# Then open:
# http://localhost:8765/annotation_tool/index.html
```

---

## License

Apache-2.0. See [LICENSE](LICENSE).

Data in `data/trail_steps.jsonl` is derived from `PatronusAI/TRAIL` and subject to its original terms.
