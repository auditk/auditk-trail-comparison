"""
Build conversation_context field for each step in gold_set_sessions.jsonl.

For each tau2 step (airline/retail/telecom), extracts the user and assistant
messages that occurred between the previous step's tool result and the current
step's tool call.  Coding (TRAIL) steps get conversation_context=[].

Overwrites data/gold_set_sessions.jsonl in place.
"""

import json
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


def _step_tool_call_positions(messages: list) -> list[int]:
    """
    Return the message index for each step, in step order.
    One entry per tool call (parallel calls in one message each get their own entry
    pointing at the same message index).
    """
    positions: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        for _ in tool_calls:
            positions.append(i)
    return positions


def extract_context(messages: list, step_number: int) -> list[dict]:
    """
    Conversation context for step_number (1-indexed).

    Returns [{role, content}, ...] for the user and assistant-text messages
    that appear between the previous step's last tool result and the current
    step's tool-call message.  Step 1 always returns [].
    """
    if step_number <= 1:
        return []

    positions = _step_tool_call_positions(messages)
    if step_number > len(positions):
        return []

    current_msg_idx = positions[step_number - 1]
    prev_msg_idx    = positions[step_number - 2]

    # Skip all tool-result messages that immediately follow the previous tool call.
    start_idx = prev_msg_idx + 1
    while start_idx < len(messages) and messages[start_idx].get("role") == "tool":
        start_idx += 1

    # Collect conversational turns up to (but not including) the current tool call.
    context: list[dict] = []
    for msg in messages[start_idx:current_msg_idx]:
        role    = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            context.append({"role": "user", "content": content})
        elif role == "assistant" and not msg.get("tool_calls"):
            # Plain assistant text between tool calls (clarification, acknowledgement, etc.)
            context.append({"role": "assistant", "content": content})

    return context


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

    with_context = 0
    empty_context = 0
    missing_sim   = set()

    print("Building context ...")
    for rec in records:
        domain      = rec.get("domain", "")
        trace_id    = rec.get("trace_id", "")
        step_number = rec.get("step_number", 1)

        if domain == "coding":
            rec["conversation_context"] = []
            empty_context += 1
            continue

        messages = sim_index.get(trace_id)
        if messages is None:
            rec["conversation_context"] = []
            missing_sim.add(trace_id)
            empty_context += 1
            continue

        ctx = extract_context(messages, step_number)
        rec["conversation_context"] = ctx
        if ctx:
            with_context += 1
        else:
            empty_context += 1

    print(f"  Steps with context:    {with_context}")
    print(f"  Steps without context: {empty_context}")
    if missing_sim:
        print(f"  WARNING — {len(missing_sim)} trace_ids not found in simulation files:")
        for tid in sorted(missing_sim):
            print(f"    {tid}")

    print("\nWriting updated gold_set_sessions.jsonl ...")
    with open(GOLD_SET_PATH, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print("Done.\n")

    # ── Sample output ──────────────────────────────────────────────
    print("── Sample: first 3 non-coding steps that have non-empty context ──")
    shown = 0
    for rec in records:
        ctx = rec.get("conversation_context") or []
        if ctx:
            print(f"\ntrace_id={rec['trace_id']}  domain={rec['domain']}  step={rec['step_number']}")
            print(f"declared_intent (truncated): {rec.get('declared_intent','')[:120]!r}")
            print(f"context ({len(ctx)} turn{'s' if len(ctx)!=1 else ''}):")
            for turn in ctx:
                preview = turn["content"][:200]
                print(f"  [{turn['role']:>9}]: {preview!r}")
            shown += 1
            if shown >= 3:
                break

    if shown == 0:
        print("  (no non-empty context found — check simulation file alignment)")


if __name__ == "__main__":
    main()
