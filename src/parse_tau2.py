"""
tau2-bench parser for auditk cross-taxonomy experiment.

Extracts per-step (declared_intent, action_taken) pairs from Claude traces
in the tau2-bench dataset across retail, airline, and telecom domains.

Output: data/tau2_steps.jsonl — one record per step per simulation.
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path.home() / "Projects/tau2-bench/data/tau2/results/final"
OUTPUT_PATH = Path("data/tau2_steps.jsonl")

DOMAIN_FILES = {
    "retail": "claude-3-7-sonnet-20250219_retail_default_gpt-4.1-2025-04-14_4trials.json",
    "airline": "claude-3-7-sonnet-20250219_airline_default_gpt-4.1-2025-04-14_4trials.json",
    "telecom": "claude-3-7-sonnet-20250219_telecom_default_gpt-4.1-2025-04-14_4trials.json",
}


def format_action(tool_name: str, arguments: dict) -> str:
    """Format a tool call as a human-readable string."""
    args_str = ", ".join(f"{k}={repr(v)}" for k, v in arguments.items())
    return f"{tool_name}({args_str})"


def build_action_check_lookup(action_checks: list) -> list[dict]:
    """
    Return a list of action_check entries to consume when matching tool calls.
    Keyed by (name, canonical_args) for deduplication-safe matching.
    """
    entries = []
    for ac in action_checks or []:
        action = ac.get("action", {})
        entries.append({
            "name": action.get("name"),
            "arguments": action.get("arguments", {}),
            "action_match": ac.get("action_match"),
            "claimed": False,
        })
    return entries


def find_action_match(lookup: list[dict], tool_name: str, arguments: dict) -> bool | None:
    """Find the first unclaimed action_check matching name + args, mark it claimed."""
    for entry in lookup:
        if entry["claimed"]:
            continue
        if entry["name"] == tool_name and entry["arguments"] == arguments:
            entry["claimed"] = True
            return entry["action_match"]
    return None


def extract_steps(simulation: dict, domain: str) -> list[dict]:
    """
    Extract intent/action pairs from a single simulation's message list.

    Strategy: walk messages in order; when an assistant message has non-null
    content (pending_intent), and the same or the next assistant message has
    tool_calls, emit a step record pairing them.
    """
    trace_id = simulation.get("id", "unknown")
    task_id = simulation.get("task_id")
    reward = simulation.get("reward_info", {}).get("reward")

    action_checks = simulation.get("reward_info", {}).get("action_checks") or []
    ac_lookup = build_action_check_lookup(action_checks)

    messages = simulation.get("messages", [])
    records = []
    step_number = 0
    pending_intent: str | None = None

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")

        if role != "assistant":
            # Tool/user messages don't reset pending_intent
            continue

        # Update pending_intent when the assistant produces text
        if content is not None:
            pending_intent = content

        # Emit a record for each tool call when we have a pending intent
        if tool_calls and pending_intent is not None:
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                arguments = tc.get("arguments", {})

                action_match = find_action_match(ac_lookup, tool_name, arguments)

                step_number += 1
                records.append({
                    "trace_id": trace_id,
                    "task_id": task_id,
                    "domain": domain,
                    "step_number": step_number,
                    "declared_intent": pending_intent,
                    "action_taken": format_action(tool_name, arguments),
                    "tool_name": tool_name,
                    "tool_arguments": json.dumps(arguments),
                    "reward": reward,
                    "action_match": action_match,
                })
            # Reset after consuming: the same intent shouldn't pair with the
            # next tool call unless the assistant produces new content first.
            pending_intent = None

    return records


def load_domain(domain: str, filename: str) -> list[dict]:
    """Load simulations from one domain file, deduplicated by task_id (keep first trial)."""
    path = DATA_DIR / filename
    with open(path) as f:
        data = json.load(f)

    simulations = data.get("simulations", [])

    seen_task_ids: set = set()
    unique_sims = []
    for sim in simulations:
        task_id = sim.get("task_id")
        if task_id not in seen_task_ids:
            seen_task_ids.add(task_id)
            unique_sims.append(sim)

    print(f"  {domain}: {len(simulations)} simulations → {len(unique_sims)} unique task_ids")
    return unique_sims


def main() -> None:
    OUTPUT_PATH.parent.mkdir(exist_ok=True)

    all_records: list[dict] = []
    domain_stats: dict[str, dict] = {}

    for domain, filename in DOMAIN_FILES.items():
        print(f"\nLoading {domain}...")
        simulations = load_domain(domain, filename)

        domain_records: list[dict] = []
        for sim in simulations:
            steps = extract_steps(sim, domain)
            domain_records.extend(steps)

        all_records.extend(domain_records)
        domain_stats[domain] = {
            "total": len(domain_records),
            "action_match_true": sum(1 for r in domain_records if r["action_match"] is True),
            "action_match_false": sum(1 for r in domain_records if r["action_match"] is False),
            "action_match_null": sum(1 for r in domain_records if r["action_match"] is None),
        }

    # Write output
    with open(OUTPUT_PATH, "w") as f:
        for record in all_records:
            f.write(json.dumps(record) + "\n")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"Output: {OUTPUT_PATH}")
    print(f"{'=' * 60}")
    print(f"\n{'Domain':<12} {'Total':>8} {'Match=T':>10} {'Match=F':>10} {'Match=null':>12}")
    print("-" * 56)
    for domain, stats in domain_stats.items():
        print(
            f"{domain:<12} {stats['total']:>8} "
            f"{stats['action_match_true']:>10} "
            f"{stats['action_match_false']:>10} "
            f"{stats['action_match_null']:>12}"
        )
    total = len(all_records)
    t_true = sum(s["action_match_true"] for s in domain_stats.values())
    t_false = sum(s["action_match_false"] for s in domain_stats.values())
    t_null = sum(s["action_match_null"] for s in domain_stats.values())
    print("-" * 56)
    print(f"{'TOTAL':<12} {total:>8} {t_true:>10} {t_false:>10} {t_null:>12}")

    # Quick sanity check
    if all_records:
        r = all_records[0]
        print(f"\n--- First record ---")
        print(f"trace_id: {r['trace_id']}")
        print(f"task_id:  {r['task_id']}")
        print(f"domain:   {r['domain']}")
        print(f"step:     {r['step_number']}")
        print(f"intent:   {r['declared_intent'][:120]!r}")
        print(f"action:   {r['action_taken']}")
        print(f"match:    {r['action_match']}")


if __name__ == "__main__":
    main()
