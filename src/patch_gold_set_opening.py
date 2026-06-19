"""
Patch gold_set_sessions.jsonl with user_opening_message.

tau2 (retail/airline/telecom): first user-role message from the simulation.
coding (TRAIL): 'question' field from the SWE-bench item embedded in the span log.
Fallback: "Task: {trace_id}" if extraction fails.
"""

import json
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
DATA      = ROOT / "data"
GOLD_FILE = DATA / "gold_set_sessions.jsonl"

TAU2_FILES = {
    "retail":  "/home/matt/Projects/tau2-bench/data/tau2/results/final/"
               "claude-3-7-sonnet-20250219_retail_default_gpt-4.1-2025-04-14_4trials.json",
    "airline": "/home/matt/Projects/tau2-bench/data/tau2/results/final/"
               "claude-3-7-sonnet-20250219_airline_default_gpt-4.1-2025-04-14_4trials.json",
    "telecom": "/home/matt/Projects/tau2-bench/data/tau2/results/final/"
               "claude-3-7-sonnet-20250219_telecom_default_gpt-4.1-2025-04-14_4trials.json",
}


# ── tau2: build {trace_id: first_user_message} ────────────────────────────────

def build_tau2_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for domain, path in TAU2_FILES.items():
        data = json.loads(Path(path).read_text())
        for sim in data["simulations"]:
            tid = sim["id"]
            if tid in index:
                continue
            first_user = next(
                (m["content"] for m in sim.get("messages", [])
                 if m.get("role") == "user" and m.get("content")),
                None,
            )
            if first_user:
                index[tid] = first_user
    return index


# ── TRAIL: build {trace_id: question} ────────────────────────────────────────

def build_trail_index() -> dict[str, str]:
    from datasets import load_dataset

    print("Loading TRAIL dataset (swe_bench split)…")
    ds = load_dataset("PatronusAI/TRAIL", split="swe_bench")

    index: dict[str, str] = {}
    for row in ds:
        trace  = json.loads(row["trace"])
        tid    = trace.get("trace_id", "")
        # question is in the process_item span log's function.arguments.item
        try:
            item     = trace["spans"][0]["logs"][0]["body"]["function.arguments"]["item"]
            question = item.get("question") or item.get("problem_statement") or ""
        except (KeyError, IndexError, TypeError):
            question = ""
        if question:
            index[tid] = question
    print(f"  Indexed {len(index)} TRAIL traces.")
    return index


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Building tau2 index…")
    tau2_index  = build_tau2_index()
    print(f"  Indexed {len(tau2_index)} tau2 simulations.")

    trail_index = build_trail_index()

    records = [json.loads(l) for l in GOLD_FILE.open() if l.strip()]

    found    = 0
    fallback = 0

    for rec in records:
        domain   = rec.get("domain", "")
        trace_id = rec["trace_id"]

        if domain == "coding":
            msg = trail_index.get(trace_id)
        else:
            msg = tau2_index.get(trace_id)

        if msg:
            rec["user_opening_message"] = msg
            found += 1
        else:
            rec["user_opening_message"] = f"Task: {trace_id}"
            fallback += 1

    GOLD_FILE.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    # ── Report ────────────────────────────────────────────────────────────────
    total = len(records)
    print(f"\n{'='*55}")
    print(f"  Patched gold_set_sessions.jsonl")
    print(f"{'='*55}")
    print(f"  Total records : {total}")
    print(f"  Found         : {found}  ({found/total*100:.1f}%)")
    print(f"  Fallback used : {fallback}  ({fallback/total*100:.1f}%)")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
