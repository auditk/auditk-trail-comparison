"""
Sample 50 sessions from the combined auditk gold set for human annotation.

Selection strategy:
  - Exclude sessions with > 15 steps
  - Prioritise sessions containing at least one drift label per domain
  - Fill remaining slots with faithful-only sessions
  - Seed 42 for reproducibility

Output: data/gold_set_sessions.jsonl  (one record per step)
"""

import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

TAU2_FILE = DATA / "tau2_steps_judged.jsonl"
TRAIL_FILE = DATA / "trail_steps_judged.jsonl"
OUTPUT_FILE = DATA / "gold_set_sessions.jsonl"

DRIFT_LABELS = {"goal_deviation", "instruction_noncompliance", "undeclared_goal"}

TARGETS = {
    "coding": 13,
    "airline": 13,
    "retail": 12,
    "telecom": 12,
}

MAX_STEPS = 15


def load_sessions(path: Path, default_domain: str | None = None) -> dict[str, list[dict]]:
    """Return {trace_id: [steps]} from a JSONL file."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if default_domain:
                record["domain"] = default_domain
            sessions[record["trace_id"]].append(record)
    # sort each session by step_number
    for steps in sessions.values():
        steps.sort(key=lambda r: r.get("step_number", 0))
    return dict(sessions)


def has_drift(steps: list[dict]) -> bool:
    return any(s.get("auditk_label") in DRIFT_LABELS for s in steps)


def sample_domain(
    sessions: dict[str, list[dict]],
    domain: str,
    n: int,
    rng: random.Random,
) -> list[str]:
    """Return up to n trace_ids for the given domain, drift-first."""
    eligible = [
        tid for tid, steps in sessions.items()
        if steps[0].get("domain") == domain and len(steps) <= MAX_STEPS
    ]
    drift_sessions = [tid for tid in eligible if has_drift(sessions[tid])]
    faithful_sessions = [tid for tid in eligible if not has_drift(sessions[tid])]

    rng.shuffle(drift_sessions)
    rng.shuffle(faithful_sessions)

    selected = drift_sessions[:n]
    if len(selected) < n:
        selected += faithful_sessions[: n - len(selected)]

    return selected


def main() -> None:
    rng = random.Random(42)

    tau2 = load_sessions(TAU2_FILE)
    trail = load_sessions(TRAIL_FILE, default_domain="coding")
    all_sessions = {**tau2, **trail}

    selected_ids: list[str] = []
    domain_meta: dict[str, dict] = {}

    for domain, n in TARGETS.items():
        ids = sample_domain(all_sessions, domain, n, rng)
        drift_count = sum(1 for tid in ids if has_drift(all_sessions[tid]))
        faithful_count = len(ids) - drift_count
        total_steps = sum(len(all_sessions[tid]) for tid in ids)
        domain_meta[domain] = {
            "sessions": len(ids),
            "drift_sessions": drift_count,
            "faithful_sessions": faithful_count,
            "total_steps": total_steps,
        }
        selected_ids.extend(ids)

    # assign session_rank (1-indexed, order by domain then within domain)
    label_tally: dict[str, int] = defaultdict(int)
    with OUTPUT_FILE.open("w") as out:
        for rank, tid in enumerate(selected_ids, start=1):
            for step in all_sessions[tid]:
                record = dict(step)
                record["gold_set"] = True
                record["session_rank"] = rank
                out.write(json.dumps(record) + "\n")
                label_tally[record.get("auditk_label", "unknown")] += 1

    # ── stdout report ────────────────────────────────────────────────────────
    total_steps = sum(m["total_steps"] for m in domain_meta.values())
    print(f"\n{'='*55}")
    print(f"  Gold set sample  —  {len(selected_ids)} sessions, {total_steps} steps")
    print(f"{'='*55}")
    for domain in TARGETS:
        m = domain_meta[domain]
        print(
            f"  {domain:<10}  {m['sessions']:>2} sessions  |  "
            f"{m['total_steps']:>3} steps  |  "
            f"drift={m['drift_sessions']}  faithful={m['faithful_sessions']}"
        )
    print(f"{'─'*55}")
    print("  Label distribution (steps):")
    for label, count in sorted(label_tally.items(), key=lambda x: -x[1]):
        print(f"    {label:<30} {count:>4}")
    print(f"{'─'*55}")
    print(f"  Total steps written: {total_steps}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
