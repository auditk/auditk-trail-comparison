#!/usr/bin/env python3
"""
auditk TRAIL cross-taxonomy metrics computation
Run from: ~/Projects/auditk-trail-experiment/
Output: results/trail_metrics.md

Computes precision, recall, F1 and confusion matrix for:
1. NLI gate alone vs TRAIL ground truth
2. Full pipeline (NLI + judge) vs TRAIL ground truth
"""

import json
import sys
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────
NLI_FILE    = Path("data/trail_steps_nli.jsonl")
JUDGED_FILE = Path("data/trail_steps_judged.jsonl")
OUT_DIR     = Path("results")
OUT_FILE    = OUT_DIR / "trail_metrics.md"

# ── label mappings ───────────────────────────────────────────────────────────
# NLI gate: "entailment" = faithful (PASS), anything else = flagged (FAIL)
NLI_FAITHFUL     = {"entailment"}
# Judge: these labels = non-drift (faithful)
JUDGE_FAITHFUL   = {"faithful", "benign_elaboration"}
# Judge: these labels = drift detected
JUDGE_DRIFT      = {"goal_deviation", "instruction_noncompliance", "undeclared_goal"}

def load_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]

def confusion(tp, fp, fn, tn):
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    return precision, recall, f1

def main():
    OUT_DIR.mkdir(exist_ok=True)

    nli_steps    = load_jsonl(NLI_FILE)
    judged_steps = load_jsonl(JUDGED_FILE)

    # Index judged steps by (trace_id, step_number) for alignment
    judged_index = {
        (s["trace_id"], s["step_number"]): s
        for s in judged_steps
    }

    # ── NLI gate metrics ─────────────────────────────────────────────────────
    nli_tp = nli_fp = nli_fn = nli_tn = 0
    nli_null = 0  # steps where NLI label is missing

    for step in nli_steps:
        has_error = step.get("has_trail_error", False)
        nli_label = step.get("nli_label")

        if nli_label is None:
            nli_null += 1
            continue

        nli_pass = nli_label in NLI_FAITHFUL  # entailment = pass = not flagged

        if not nli_pass and has_error:     # flagged + error = TP
            nli_tp += 1
        elif not nli_pass and not has_error:  # flagged + no error = FP
            nli_fp += 1
        elif nli_pass and has_error:       # not flagged + error = FN
            nli_fn += 1
        else:                              # not flagged + no error = TN
            nli_tn += 1

    nli_prec, nli_rec, nli_f1 = confusion(nli_tp, nli_fp, nli_fn, nli_tn)
    nli_total = nli_tp + nli_fp + nli_fn + nli_tn

    # ── Full pipeline metrics ────────────────────────────────────────────────
    # A step is "drift detected" if:
    #   NLI flags it (not entailment) AND judge classifies as drift label
    # A step is "not detected" if:
    #   NLI passes it (entailment) OR judge classifies as faithful/benign
    #   OR judge label is null (treat as not detected — conservative)

    pipe_tp = pipe_fp = pipe_fn = pipe_tn = 0
    pipe_no_judge = 0

    for step in nli_steps:
        has_error = step.get("has_trail_error", False)
        nli_label = step.get("nli_label")

        if nli_label is None:
            continue

        nli_flagged = nli_label not in NLI_FAITHFUL

        # Get judge label from judged file
        key = (step["trace_id"], step["step_number"])
        judged = judged_index.get(key, {})
        auditk_label = judged.get("auditk_label")

        if nli_flagged:
            if auditk_label is None:
                # NLI flagged but no judge output — conservative: treat as not detected
                pipe_no_judge += 1
                detected = False
            else:
                detected = auditk_label in JUDGE_DRIFT
        else:
            detected = False  # NLI passed = not detected by pipeline

        if detected and has_error:
            pipe_tp += 1
        elif detected and not has_error:
            pipe_fp += 1
        elif not detected and has_error:
            pipe_fn += 1
        else:
            pipe_tn += 1

    pipe_prec, pipe_rec, pipe_f1 = confusion(pipe_tp, pipe_fp, pipe_fn, pipe_tn)
    pipe_total = pipe_tp + pipe_fp + pipe_fn + pipe_tn

    # ── Judge label distribution ─────────────────────────────────────────────
    label_dist: dict[str, int] = {}
    for step in judged_steps:
        label = step.get("auditk_label")
        if label:
            label_dist[label] = label_dist.get(label, 0) + 1

    # ── Sanity checks ────────────────────────────────────────────────────────
    print(f"\n── Sanity checks ──────────────────────────────")
    print(f"NLI total steps counted : {nli_total} (expect ~460, null excluded: {nli_null})")
    print(f"NLI recall              : {nli_rec:.4f} (paper cites 0.60 — verify)")
    if abs(nli_rec - 0.60) > 0.05:
        print(f"  ⚠️  WARNING: recall {nli_rec:.4f} diverges from cited 0.60 by >{abs(nli_rec-0.60):.2f}")
        print(f"     Investigate before submitting paper.")
    else:
        print(f"  ✓  Recall within acceptable range of cited 0.60")

    # ── Output ───────────────────────────────────────────────────────────────
    out = []
    out.append("# auditk TRAIL Cross-Taxonomy Metrics\n")
    out.append("_Generated by compute_trail_metrics.py_\n")

    out.append("\n## NLI Gate vs TRAIL Ground Truth\n")
    out.append("Label mapping: `entailment` → faithful (not flagged); `neutral` → flagged\n")
    out.append(f"Steps evaluated: {nli_total} (null NLI labels excluded: {nli_null})\n")
    out.append("\n| Metric | Value |\n|--------|-------|\n")
    out.append(f"| True Positives (TP) | {nli_tp} |\n")
    out.append(f"| False Positives (FP) | {nli_fp} |\n")
    out.append(f"| False Negatives (FN) | {nli_fn} |\n")
    out.append(f"| True Negatives (TN) | {nli_tn} |\n")
    out.append(f"| Precision | {nli_prec:.4f} |\n")
    out.append(f"| Recall | {nli_rec:.4f} |\n")
    out.append(f"| F1 | {nli_f1:.4f} |\n")

    out.append("\n### NLI Gate Confusion Matrix\n")
    out.append("```\n")
    out.append(f"                   TRAIL ERROR    TRAIL CORRECT\n")
    out.append(f"NLI flagged        {nli_tp:<14} {nli_fp}\n")
    out.append(f"NLI passed         {nli_fn:<14} {nli_tn}\n")
    out.append("```\n")

    out.append("\n## Full Pipeline vs TRAIL Ground Truth\n")
    out.append("Drift detected = NLI flagged AND judge label in "
               "{goal_deviation, instruction_noncompliance, undeclared_goal}\n")
    out.append(f"Steps with NLI flagged but no judge output (treated as not detected): "
               f"{pipe_no_judge}\n")
    out.append(f"Steps evaluated: {pipe_total}\n")
    out.append("\n| Metric | Value |\n|--------|-------|\n")
    out.append(f"| True Positives (TP) | {pipe_tp} |\n")
    out.append(f"| False Positives (FP) | {pipe_fp} |\n")
    out.append(f"| False Negatives (FN) | {pipe_fn} |\n")
    out.append(f"| True Negatives (TN) | {pipe_tn} |\n")
    out.append(f"| Precision | {pipe_prec:.4f} |\n")
    out.append(f"| Recall | {pipe_rec:.4f} |\n")
    out.append(f"| F1 | {pipe_f1:.4f} |\n")

    out.append("\n### Full Pipeline Confusion Matrix\n")
    out.append("```\n")
    out.append(f"                   TRAIL ERROR    TRAIL CORRECT\n")
    out.append(f"Drift detected     {pipe_tp:<14} {pipe_fp}\n")
    out.append(f"Not detected       {pipe_fn:<14} {pipe_tn}\n")
    out.append("```\n")

    out.append("\n## Judge Label Distribution\n")
    out.append("_(Steps that reached the judge stage)_\n\n")
    out.append("| Label | Count |\n|-------|-------|\n")
    for label, count in sorted(label_dist.items(), key=lambda x: -x[1]):
        out.append(f"| {label} | {count} |\n")

    out.append("\n## Combined Metrics Table (for §4.3)\n")
    out.append("\n| Metric | NLI Gate | Full Pipeline |\n")
    out.append("|--------|----------|---------------|\n")
    out.append(f"| TP | {nli_tp} | {pipe_tp} |\n")
    out.append(f"| FP | {nli_fp} | {pipe_fp} |\n")
    out.append(f"| FN | {nli_fn} | {pipe_fn} |\n")
    out.append(f"| TN | {nli_tn} | {pipe_tn} |\n")
    out.append(f"| Precision | {nli_prec:.4f} | {pipe_prec:.4f} |\n")
    out.append(f"| Recall | {nli_rec:.4f} | {pipe_rec:.4f} |\n")
    out.append(f"| F1 | {nli_f1:.4f} | {pipe_f1:.4f} |\n")

    OUT_FILE.write_text("".join(out))
    print(f"\n── Results written to {OUT_FILE} ──────────────")
    print("".join(out))

if __name__ == "__main__":
    main()
