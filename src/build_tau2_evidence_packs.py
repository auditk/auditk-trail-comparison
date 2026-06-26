"""Build signed evidence packs from pre-judged tau2-bench steps.

Reads tau2_steps_judged.jsonl (1,659 records with full auditk labels), groups
by trace_id (278 sessions), constructs a DriftReport directly from existing
labels using severity-weighted scoring (LOW=1, MEDIUM=2, HIGH=3, null=1),
signs each pack with a fresh Ed25519 key, and writes one JSON file per session
to results/tau2_evidence_packs/.

Bypasses compute_drift / pack.build() intentionally — labels already exist
from the judge_tau2.py pipeline run; re-running the scorer would incur API
cost and produce identical results.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "auditk" / "src"))

from auditk.analysis.taxonomy import DRIFT_LABELS, TaxonomyLabel
from auditk.attestation.canonical import canonicalize
from auditk.attestation.signer import LocalEd25519Signer, generate_keypair
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    DriftReport,
    EvidencePack,
    FlowType,
    Issuer,
    RiskTier,
    ScorerFingerprint,
    Step,
    StepDrift,
    Subject,
    Trace,
    TraceSummary,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "tau2_steps_judged.jsonl"
OUT_DIR = ROOT / "results" / "tau2_evidence_packs"
RESULTS_MD = ROOT / "results" / "tau2_pipeline_results.md"
KEY_PATH = ROOT / "results" / "tau2_signer"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_WEIGHTS: dict[str | None, float] = {"LOW": 1.0, "MEDIUM": 2.0, "HIGH": 3.0}
_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)

_SCORER_FINGERPRINT = ScorerFingerprint(
    method="llm-judge",
    method_version="0.3",
    nli_model="cross-encoder/nli-deberta-v3-small",
    nli_revision="fa2804872c3b4bd748f38c0185cc85775361e735",
    judge_model="accounts/fireworks/models/deepseek-v4-pro",
)

_ISSUER = Issuer(name="Matt Dawson", organization="auditk Project")
_SUBJECT = Subject(agent_config_ref="tau2-bench-agent@0.1", agent_version="0.1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity_weight(record: dict) -> float:
    return SEVERITY_WEIGHTS.get(record.get("auditk_severity"), 1.0)


def _build_step(record: dict) -> Step:
    trace_id = record["trace_id"]
    step_num = record["step_number"]
    return Step(
        step_id=f"{trace_id}:{step_num}",
        trace_id=trace_id,
        timestamp=_BASE_TS + timedelta(seconds=step_num),
        actor=Actor.AGENT,
        declared_intent=record["declared_intent"],
        action=Action(
            type=ActionType.TOOL_CALL,
            payload={
                "tool_name": record["tool_name"],
                "tool_arguments": record["tool_arguments"],
                "action_taken": record["action_taken"],
            },
        ),
        metadata={
            "domain": record["domain"],
            "task_id": record["task_id"],
            "auditk_label": record["auditk_label"],
            "auditk_severity": record.get("auditk_severity"),
            "auditk_confidence": record.get("auditk_confidence"),
            "nli_label": record.get("nli_label"),
            "reward": record.get("reward"),
            "action_match": record.get("action_match"),
        },
    )


def _build_trace(trace_id: str, steps: list[Step], domain: str) -> Trace:
    return Trace(
        trace_id=trace_id,
        flow_type=FlowType.GENERIC,
        agent_config_ref="tau2-bench-agent@0.1",
        steps=steps,
        source_adapter="tau2-bench",
        metadata={"domain": domain},
    )


def _build_drift_report(records: list[dict], steps: list[Step]) -> DriftReport:
    trace_id = records[0]["trace_id"]
    per_step: dict[str, StepDrift] = {}
    flagged: list[str] = []
    taxonomy_counts: dict[str, int] = defaultdict(int)
    total_weight = 0.0
    drift_weight = 0.0

    for record, step in zip(records, steps):
        label = TaxonomyLabel(record["auditk_label"])
        taxonomy_counts[label.value] += 1
        w = _severity_weight(record)
        total_weight += w
        if label in DRIFT_LABELS:
            drift_weight += w
            flagged.append(step.step_id)
        per_step[step.step_id] = StepDrift(
            step_id=step.step_id,
            label=label,
            overturned_gate=False,
            reasoning=record.get("auditk_reasoning") or "",
        )

    drift_score = drift_weight / total_weight if total_weight > 0.0 else 0.0

    return DriftReport(
        drift_score=drift_score,
        drift_per_trace={trace_id: drift_score},
        flagged_steps=flagged,
        method="llm-judge",
        method_version="0.3",
        per_step=per_step,
        scorer_fingerprint=_SCORER_FINGERPRINT,
        taxonomy_counts=dict(taxonomy_counts),
    )


def _build_and_sign_pack(
    trace: Trace,
    drift: DriftReport,
    signer: LocalEd25519Signer,
) -> EvidencePack:
    now = datetime.now(UTC)
    ts_list = [s.timestamp for s in trace.steps]
    pack = EvidencePack(
        pack_id=uuid4(),
        issued_at=now,
        issuer=_ISSUER,
        subject=_SUBJECT,
        drift_metrics=drift,
        trace_summary=TraceSummary(
            trace_count=1,
            step_count=len(trace.steps),
            flow_types=[trace.flow_type],
            time_range=(min(ts_list), max(ts_list)),
        ),
    )
    manifest = pack.model_dump(mode="json", exclude={"signatures"})
    pack.signatures.append(signer.sign(canonicalize(manifest)))
    return pack


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _verify_pack(pack: EvidencePack) -> bool:
    from cryptography.exceptions import InvalidSignature
    from auditk.attestation.signer import LocalEd25519Verifier

    manifest = pack.model_dump(mode="json", exclude={"signatures"})
    canonical_bytes = canonicalize(manifest)
    for sig in pack.signatures:
        try:
            LocalEd25519Verifier(sig.public_key).verify(canonical_bytes, sig.signature)
        except InvalidSignature:
            return False
    return True


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def _compute_summary(
    packs: list[EvidencePack],
    domain_map: dict[str, str],
) -> str:
    from collections import defaultdict

    domain_scores: dict[str, list[float]] = defaultdict(list)
    domain_taxonomy: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for pack in packs:
        trace_id = next(iter(pack.drift_metrics.drift_per_trace))
        domain = domain_map[trace_id]
        score = pack.drift_metrics.drift_score
        domain_scores[domain].append(score)
        for label, count in (pack.drift_metrics.taxonomy_counts or {}).items():
            domain_taxonomy[domain][label] += count

    drift_labels = {dl.value for dl in DRIFT_LABELS}

    lines = [
        "# auditk tau2-bench Pipeline Results",
        f"_Generated {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}_",
        "",
        "## Overview",
        "",
        f"- Source: `data/tau2_steps_judged.jsonl`",
        f"- Sessions: {len(packs)}",
        f"- Steps: 1659",
        f"- Scorer: llm-judge@0.3 (labels pre-computed by judge_tau2.py)",
        f"- Judge model: accounts/fireworks/models/deepseek-v4-pro",
        f"- Drift score: severity-weighted (LOW=1, MEDIUM=2, HIGH=3, null=1)",
        f"- Signing: Ed25519, key at results/tau2_signer.ed25519",
        "",
        "## Per-Domain Results",
        "",
        "| Domain | Sessions | Mean drift | Drift > 0 | Top non-faithful label |",
        "|--------|----------|------------|-----------|------------------------|",
    ]

    for domain in sorted(domain_scores):
        scores = domain_scores[domain]
        mean_drift = sum(scores) / len(scores)
        n_drifted = sum(1 for s in scores if s > 0)
        pct = round(100 * n_drifted / len(scores))
        tc = domain_taxonomy[domain]
        top_drift_label = max(
            (k for k in tc if k in drift_labels),
            key=lambda k: tc[k],
            default="—",
        )
        lines.append(
            f"| {domain} | {len(scores)} | {mean_drift:.4f} | {n_drifted} ({pct}%) | {top_drift_label} |"
        )

    lines += [
        "",
        "## Taxonomy Breakdown (all sessions)",
        "",
        "| Label | Count | % |",
        "|-------|-------|---|",
    ]
    total_steps = 1659
    all_tc: dict[str, int] = defaultdict(int)
    for domain in domain_taxonomy:
        for label, count in domain_taxonomy[domain].items():
            all_tc[label] += count
    for label, count in sorted(all_tc.items(), key=lambda x: -x[1]):
        lines.append(f"| {label} | {count} | {round(100 * count / total_steps, 1)}% |")

    lines += [
        "",
        "## Drifted Sessions (drift_score > 0)",
        "",
        "| trace_id | domain | drift_score | flagged_steps | dominant_label |",
        "|----------|--------|-------------|---------------|----------------|",
    ]
    drifted = [
        (p, domain_map[next(iter(p.drift_metrics.drift_per_trace))])
        for p in packs
        if p.drift_metrics.drift_score > 0
    ]
    drifted.sort(key=lambda x: -x[0].drift_metrics.drift_score)
    for pack, domain in drifted:
        tid = next(iter(pack.drift_metrics.drift_per_trace))
        score = pack.drift_metrics.drift_score
        n_flagged = len(pack.drift_metrics.flagged_steps)
        tc = pack.drift_metrics.taxonomy_counts or {}
        top = max(
            (k for k in tc if k in drift_labels),
            key=lambda k: tc[k],
            default="—",
        )
        lines.append(f"| {tid} | {domain} | {score:.4f} | {n_flagged} | {top} |")

    lines += ["", "## Attestation", "", "All evidence packs signed with Ed25519. Signature verification: 100% pass."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Generating signing key...")
    priv_path, pub_path = generate_keypair(KEY_PATH)
    signer = LocalEd25519Signer(priv_path)
    print(f"  Key: {priv_path}")

    print(f"Reading {DATA}...")
    records = [json.loads(line) for line in DATA.read_text().splitlines()]
    print(f"  {len(records)} steps loaded")

    # Group by trace_id preserving step order
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_trace[r["trace_id"]].append(r)
    for steps in by_trace.values():
        steps.sort(key=lambda r: r["step_number"])

    domain_map = {tid: recs[0]["domain"] for tid, recs in by_trace.items()}
    print(f"  {len(by_trace)} sessions across domains: { {d: sum(1 for v in domain_map.values() if v == d) for d in sorted(set(domain_map.values()))} }")

    print("Building and signing evidence packs...")
    packs: list[EvidencePack] = []
    for i, (trace_id, trace_records) in enumerate(by_trace.items(), 1):
        steps = [_build_step(r) for r in trace_records]
        trace = _build_trace(trace_id, steps, trace_records[0]["domain"])
        drift = _build_drift_report(trace_records, steps)
        pack = _build_and_sign_pack(trace, drift, signer)
        packs.append(pack)
        out_path = OUT_DIR / f"{trace_id}.json"
        out_path.write_text(pack.model_dump_json(indent=2))
        if i % 50 == 0 or i == len(by_trace):
            print(f"  {i}/{len(by_trace)}")

    print("Verifying signatures...")
    failures = [p for p in packs if not _verify_pack(p)]
    if failures:
        print(f"  FAILED: {len(failures)} packs did not verify")
        sys.exit(1)
    print(f"  {len(packs)}/{len(packs)} passed")

    print("Computing summary metrics...")
    summary_md = _compute_summary(packs, domain_map)
    RESULTS_MD.write_text(summary_md)
    print(f"  Written to {RESULTS_MD}")

    print("Done.")


if __name__ == "__main__":
    main()
