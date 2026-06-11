"""
NLI gate for auditk × TRAIL experiment.

Runs cross-encoder/nli-deberta-v3-small over (thought, code) pairs.
Asymmetric entailment: does the Thought entail the Code action?

Input:  data/trail_steps.jsonl
Output: data/trail_steps_nli.jsonl  (same records + nli_label, nli_confidence)
"""

import json
from pathlib import Path

from sentence_transformers import CrossEncoder
from tqdm import tqdm

INPUT_PATH = Path("data/trail_steps.jsonl")
OUTPUT_PATH = Path("data/trail_steps_nli.jsonl")

# labels returned by the cross-encoder in order
NLI_LABELS = ["contradiction", "entailment", "neutral"]

# minimum thought/code length to bother running NLI
MIN_LENGTH = 10


def load_model():
    print("Loading DeBERTa NLI model...")
    model = CrossEncoder(
        "cross-encoder/nli-deberta-v3-small",
        max_length=512,
    )
    print("Model loaded.")
    return model


def classify(model, thought: str, code: str) -> tuple[str, float]:
    """
    Run NLI: does thought (premise) entail code (hypothesis)?
    Returns (label, confidence) where label is one of:
      entailment / neutral / contradiction
    """
    if len(thought) < MIN_LENGTH or len(code) < MIN_LENGTH:
        return "neutral", 0.0

    # cross-encoder expects (premise, hypothesis)
    scores = model.predict([(thought, code)])

    # scores shape: (1, 3) — contradiction, entailment, neutral
    import numpy as np
    probs = np.exp(scores) / np.exp(scores).sum(axis=1, keepdims=True)
    probs = probs[0]

    label_idx = int(probs.argmax())
    label = NLI_LABELS[label_idx]
    confidence = float(probs[label_idx])

    return label, confidence


def main():
    model = load_model()

    records = []
    with open(INPUT_PATH) as f:
        for line in f:
            records.append(json.loads(line))

    print(f"Running NLI gate over {len(records)} steps...")

    results = []
    label_counts = {"entailment": 0, "neutral": 0, "contradiction": 0, "skipped": 0}

    for record in tqdm(records, desc="NLI gate"):
        thought = record.get("thought", "")
        code = record.get("code", "")

        if len(thought) < MIN_LENGTH or len(code) < MIN_LENGTH:
            record["nli_label"] = "skipped"
            record["nli_confidence"] = None
            label_counts["skipped"] += 1
        else:
            label, confidence = classify(model, thought, code)
            record["nli_label"] = label
            record["nli_confidence"] = round(confidence, 4)
            label_counts[label] += 1

        results.append(record)

    # write output
    with open(OUTPUT_PATH, "w") as f:
        for record in results:
            f.write(json.dumps(record) + "\n")

    print(f"\nOutput written to {OUTPUT_PATH}")
    print("\nNLI label distribution:")
    total = len(results)
    for label, count in label_counts.items():
        pct = 100 * count / total if total else 0
        print(f"  {label:15s} {count:4d}  ({pct:.1f}%)")

    # spot check: show a few non-entailment steps with their TRAIL labels
    print("\n--- Sample non-entailment steps ---")
    shown = 0
    for r in results:
        if r["nli_label"] in ("neutral", "contradiction") and shown < 3:
            print(f"\nStep {r['step_number']} ({r['nli_label']}, conf={r['nli_confidence']})")
            print(f"  Thought: {r['thought'][:150]}")
            print(f"  Code:    {r['code'][:150]}")
            print(f"  TRAIL:   {[l['category'] for l in r['trail_labels']]}")
            shown += 1


if __name__ == "__main__":
    main()