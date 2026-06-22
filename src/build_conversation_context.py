"""
Build conversation_context field for each step in gold_set_sessions.jsonl.

For each tau2 step (airline/retail/telecom), extracts the user and assistant
messages that form the conversational context leading up to the step's tool
call.  Specifically, for step N the context spans from the last pure-text
assistant response before step N's key tool call up to (but not including)
that tool call, collecting only user messages and non-tool-call assistant
messages.  Step 1 gets everything before the session's first tool call.
Coding (TRAIL) steps get conversation_context=[].

Overwrites data/gold_set_sessions.jsonl in place.
"""

import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TAU2_ROOT = Path.home() / "Projects/tau2-bench/data/tau2/results/final"

DOMAIN_FILES = {
    "airline": "claude-3-7-sonnet-20250219_airline_default_gpt-4.1-2025-04-14_4trials.json",
    "retail":  "claude-3-7-sonnet-20250219_retail_default_gpt-4.1-2025-04-14_4trials.json",
    "telecom": "claude-3-7-sonnet-20250219_telecom_default_gpt-4.1-2025-04-14_4trials.json",
}

GOLD_SET_PATH = DATA_DIR / "gold_set_sessions.jsonl"


def load_simulations() -> dict[str, list]:
    """Return mapping sim_id -> messages for all tau2 simulations."""
    index: dict[str, list] = {}
    for domain, filename in DOMAIN_FILES.items():
        path = TAU2_ROOT / filename
        print(f"  Loading {domain} ...")
        with open(path) as f:
            data = json.load(f)
        for sim in data.get("simulations", []):
            index[sim["id"]] = sim.get("messages", [])
    print(f"  {len(index)} simulations indexed\n")
    return index


def _find_key_call_pos(
    messages: list,
    tool_name: str,
    tool_arguments: str,
    search_from: int = 0,
) -> int:
    """
    Return the index of the first message at or after search_from whose
    tool call matches tool_name and tool_arguments.  Returns -1 if not found.
    """
    try:
        target_args = json.loads(tool_arguments) if tool_arguments else None
    except (json.JSONDecodeError, TypeError):
        target_args = None

    for i in range(search_from, len(messages)):
        msg = messages[i]
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if tc.get("name") != tool_name:
                continue
            if target_args is None or tc.get("arguments") == target_args:
                return i
    return -1


def _collect_conversational(messages: list, start: int, end: int) -> list[dict]:
    """
    Collect user and plain-assistant messages in messages[start:end].
    Skips tool-result messages and assistant messages that contain tool calls.
    """
    result: list[dict] = []
    for msg in messages[start:end]:
        role = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            result.append({"role": "user", "content": content})
        elif role == "assistant" and not msg.get("tool_calls"):
            result.append({"role": "assistant", "content": content})
    return result


def build_session_contexts(messages: list, steps: list[dict]) -> list[list[dict]]:
    """
    Build conversation_context for each step in a single session.

    steps must be sorted by step_number (ascending), all from the same trace_id.

    Algorithm:
    - For step 1: collect all conversational messages before the session's
      first tool call.
    - For step N > 1: find the step's key tool call by matching tool_name and
      tool_arguments (greedy, starting just after the previous step's position).
      Then find the last pure-text assistant message before that call and
      collect conversational messages from there up to the call.
    """
    # Locate key call position for each step sequentially so that duplicate
    # (tool_name, tool_arguments) pairs resolve to the correct occurrence.
    key_positions: list[int] = []
    search_from = 0
    for step in steps:
        pos = _find_key_call_pos(
            messages,
            step.get("tool_name", ""),
            step.get("tool_arguments", ""),
            search_from=search_from,
        )
        key_positions.append(pos)
        if pos >= 0:
            search_from = pos + 1

    contexts: list[list[dict]] = []
    for i, step in enumerate(steps):
        current_pos = key_positions[i]
        if current_pos < 0:
            contexts.append([])
            continue

        if step.get("step_number", 1) == 1:
            # Step 1: everything before the first tool call in the session.
            first_tc = next(
                (j for j, m in enumerate(messages) if m.get("tool_calls")),
                current_pos,
            )
            contexts.append(_collect_conversational(messages, 0, first_tc))
        else:
            # Find the last pure-text assistant message before current_pos.
            last_text = -1
            for j in range(current_pos - 1, -1, -1):
                m = messages[j]
                if m.get("role") == "assistant" and not m.get("tool_calls"):
                    last_text = j
                    break

            start = last_text if last_text >= 0 else 0
            contexts.append(_collect_conversational(messages, start, current_pos))

    return contexts


def main() -> None:
    print("Loading tau2 simulations ...")
    sim_index = load_simulations()

    print("Reading gold_set_sessions.jsonl ...")
    records: list[dict] = []
    with open(GOLD_SET_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"  {len(records)} records loaded\n")

    # Group non-coding records by trace_id, preserving list order.
    by_trace: dict[str, list[int]] = defaultdict(list)
    for idx, rec in enumerate(records):
        if rec.get("domain") != "coding":
            by_trace[rec["trace_id"]].append(idx)

    changed = 0
    unchanged = 0
    missing_sim: set[str] = set()

    print("Building context ...")
    for trace_id, indices in by_trace.items():
        messages = sim_index.get(trace_id)
        if messages is None:
            missing_sim.add(trace_id)
            for idx in indices:
                if records[idx].get("conversation_context") != []:
                    changed += 1
                else:
                    unchanged += 1
                records[idx]["conversation_context"] = []
            continue

        steps = [records[idx] for idx in indices]
        steps_sorted = sorted(steps, key=lambda r: r.get("step_number", 0))
        contexts = build_session_contexts(messages, steps_sorted)

        # Write contexts back; detect changes.
        for step, ctx in zip(steps_sorted, contexts):
            # Find original index for this step record.
            orig_idx = next(
                i for i in indices
                if records[i].get("step_number") == step.get("step_number")
            )
            old_ctx = records[orig_idx].get("conversation_context", [])
            if old_ctx != ctx:
                changed += 1
            else:
                unchanged += 1
            records[orig_idx]["conversation_context"] = ctx

    # Set coding steps.
    for rec in records:
        if rec.get("domain") == "coding":
            old = rec.get("conversation_context", [])
            if old != []:
                changed += 1
            else:
                unchanged += 1
            rec["conversation_context"] = []

    print(f"  Steps where context changed:   {changed}")
    print(f"  Steps where context unchanged: {unchanged}")
    if missing_sim:
        print(f"  WARNING — {len(missing_sim)} trace_ids not found in simulation files:")
        for tid in sorted(missing_sim):
            print(f"    {tid}")

    print("\nWriting updated gold_set_sessions.jsonl ...")
    with open(GOLD_SET_PATH, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print("Done.\n")

    # ── Verification: session 14 (trace_id 2ff34e77-...) ──────────────────
    TARGET_TRACE = "2ff34e77-4f85-4606-8d63-53a082be41c1"
    print(f"── Verification: session trace_id={TARGET_TRACE} ──")
    target_recs = [r for r in records if r.get("trace_id") == TARGET_TRACE]
    target_recs.sort(key=lambda r: r.get("step_number", 0))
    for rec in target_recs:
        ctx = rec.get("conversation_context") or []
        print(f"\nstep={rec['step_number']}  tool={rec.get('tool_name','')}  context ({len(ctx)} turns):")
        for turn in ctx:
            preview = turn["content"][:200]
            print(f"  [{turn['role']:>9}]: {preview!r}")
        if not ctx:
            print("  (empty)")


if __name__ == "__main__":
    main()
