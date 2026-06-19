"""
NLI gate for auditk × tau2-bench experiment.

Runs cross-encoder/nli-deberta-v3-small over (declared_intent, action_taken) pairs.
Asymmetric entailment: does the declared intent entail the action taken?

Input:  data/tau2_steps.jsonl
Output: data/tau2_steps_nli.jsonl  (same records + nli_label, nli_confidence)
"""

import json
from pathlib import Path

import numpy as np
from sentence_transformers import CrossEncoder
from tqdm import tqdm

INPUT_PATH = Path("data/tau2_steps.jsonl")
OUTPUT_PATH = Path("data/tau2_steps_nli.jsonl")

# labels returned by the cross-encoder in this order
NLI_LABELS = ["contradiction", "entailment", "neutral"]

MIN_LENGTH = 10


def load_model():
    print("Loading DeBERTa NLI model...")
    model = CrossEncoder("cross-encoder/nli-deberta-v3-small", max_length=512)
    print("Model loaded.")
    return model


def classify(model, intent: str, action: str) -> tuple[str, float]:
    """
    Run NLI: does intent (premise) entail action (hypothesis)?
    Returns (label, confidence) where label is one of:
      entailment / neutral / contradiction
    """
    scores = model.predict([(intent, action)])
    probs = np.exp(scores) / np.exp(scores).sum(axis=1, keepdims=True)
    probs = probs[0]
    label_idx = int(probs.argmax())
    return NLI_LABELS[label_idx], float(probs[label_idx])


def main():
    model = load_model()

    records = []
    with open(INPUT_PATH) as f:
        for line in f:
            records.append(json.loads(line))

    print(f"Running NLI gate over {len(records)} steps...")

    results = []
    label_counts: dict[str, int] = {"entailment": 0, "neutral": 0, "contradiction": 0, "skipped": 0}

    for record in tqdm(records, desc="NLI gate"):
        intent = record.get("declared_intent") or ""
        action = record.get("action_taken") or ""

        if len(intent) < MIN_LENGTH or len(action) < MIN_LENGTH:
            record["nli_label"] = "skipped"
            record["nli_confidence"] = None
            label_counts["skipped"] += 1
        else:
            label, confidence = classify(model, intent, action)
            record["nli_label"] = label
            record["nli_confidence"] = round(confidence, 4)
            label_counts[label] += 1

        results.append(record)

    with open(OUTPUT_PATH, "w") as f:
        for record in results:
            f.write(json.dumps(record) + "\n")

    print(f"\nOutput written to {OUTPUT_PATH}")
    print("\nNLI label distribution:")
    total = len(results)
    for label, count in label_counts.items():
        pct = 100 * count / total if total else 0
        print(f"  {label:15s} {count:4d}  ({pct:.1f}%)")


if __name__ == "__main__":
    main()
