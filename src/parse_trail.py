"""
TRAIL swe_bench parser for auditk cross-taxonomy experiment.

Extracts per-step (thought, code, trail_labels) triples from the
deeply nested opentelemetry span trees in the TRAIL dataset.

Output: data/trail_steps.jsonl — one record per step per trace.
"""

import json
import re
import sys
from pathlib import Path

from datasets import load_dataset
from tqdm import tqdm

OUTPUT_PATH = Path("data/trail_steps.jsonl")
OUTPUT_PATH.parent.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Span tree walking
# ---------------------------------------------------------------------------

def find_spans_by_name(spans: list, name: str) -> list:
    """Recursively find all spans with a given name."""
    results = []
    for span in spans:
        if span.get("span_name") == name:
            results.append(span)
        children = span.get("child_spans", [])
        if children:
            results.extend(find_spans_by_name(children, name))
    return results


def find_step_spans(spans: list) -> list:
    """Find all Step N spans in order."""
    step_pattern = re.compile(r"^Step \d+$")
    results = []
    for span in spans:
        if step_pattern.match(span.get("span_name", "")):
            results.append(span)
        children = span.get("child_spans", [])
        if children:
            results.extend(find_step_spans(children))
    # sort by step number
    results.sort(key=lambda s: int(s["span_name"].split()[1]))
    return results


def get_llm_call_from_step(step_span: dict) -> dict | None:
    """Recursively find the LiteLLMModel.__call__ span in a step."""
    for child in step_span.get("child_spans", []):
        if child.get("span_name") == "LiteLLMModel.__call__":
            return child
        # recurse
        result = get_llm_call_from_step(child)
        if result:
            return result
    return None


def extract_thought_and_code(llm_span: dict) -> tuple[str, str]:
    """
    Extract Thought and Code from the LLM output in a LiteLLMModel.__call__ span.
    The output is in span_attributes['output.value'] as a JSON string.
    """
    attrs = llm_span.get("span_attributes", {})
    raw_output = attrs.get("output.value", "")

    if not raw_output:
        return "", ""

    # output.value is a JSON string containing the model's response
    try:
        output_data = json.loads(raw_output)
        # The model output is in choices[0].message.content or similar
        # For smolagents it's typically a string directly
        if isinstance(output_data, str):
            text = output_data
        elif isinstance(output_data, dict):
            # try common paths
            text = (
                output_data.get("content")
                or output_data.get("text")
                or output_data.get("message", {}).get("content", "")
                or str(output_data)
            )
        else:
            text = str(output_data)
    except (json.JSONDecodeError, TypeError):
        text = str(raw_output)

    return split_thought_code(text)


def split_thought_code(text: str) -> tuple[str, str]:
    """
    Split model output into Thought and Code sections.
    smolagents format: "Thought: ...\nCode:\n```py\n...\n```"
    """
    thought = ""
    code = ""

    # extract Thought
    thought_match = re.search(
        r"Thought:\s*(.*?)(?=\nCode:|```|\Z)", text, re.DOTALL | re.IGNORECASE
    )
    if thought_match:
        thought = thought_match.group(1).strip()

    # extract Code block
    code_match = re.search(
        r"```(?:py|python)?\s*(.*?)```", text, re.DOTALL
    )
    if code_match:
        code = code_match.group(1).strip()
    else:
        # fallback: everything after "Code:"
        code_section = re.search(
            r"Code:\s*(.*)", text, re.DOTALL | re.IGNORECASE
        )
        if code_section:
            code = code_section.group(1).strip()

    return thought, code


# ---------------------------------------------------------------------------
# Label parsing
# ---------------------------------------------------------------------------

def parse_labels(labels_str: str) -> list[dict]:
    """Parse the TRAIL labels JSON string."""
    if not labels_str:
        return []
    try:
        data = json.loads(labels_str)
        # labels column is {trace_id, errors, scores}
        # errors is the list we want
        if isinstance(data, dict):
            return data.get("errors", [])
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, TypeError):
        return []

def normalise_category(cat: str) -> str:
    mapping = {
        "instruction non-compliance": "Instruction Non-compliance",
        "instruction non complience": "Instruction Non-compliance",
        "formatting errors": "Formatting Errors",
        "formatting error": "Formatting Errors",
        "language-only": "Language-only",
        "poor information retrieval": "Poor Information Retrieval",
        "task orchestration": "Task Orchestration",
        "task orchestration error": "Task Orchestration",
        "resource abuse": "Resource Abuse",
        "resource exhaustion": "Resource Abuse",
        "incorrect problem identification": "Incorrect Problem Identification",
        "tool-related": "Tool-related",
        "tool output misinterpretation": "Tool-related",
        "incorrect memory usage": "Incorrect Memory Usage",
        "context handling failures": "Context Handling Failures",
    }
    return mapping.get(cat.strip().lower(), cat.strip().title())

def map_labels_to_spans(labels: list[dict]) -> dict[str, list[dict]]:
    """Build a map of span_id -> list of labels."""
    span_map = {}
    for label in labels:
        span_id = label.get("location", "")
        if span_id:
            span_map.setdefault(span_id, []).append(label)
    return span_map


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_trace(row: dict) -> list[dict]:
    """
    Process a single TRAIL trace row.
    Returns a list of step records.
    """
    trace_raw = row.get("trace", "{}")
    labels_raw = row.get("labels", "[]")

    try:
        trace = json.loads(trace_raw) if isinstance(trace_raw, str) else trace_raw
    except (json.JSONDecodeError, TypeError):
        return []

    trace_id = trace.get("trace_id", "unknown")
    spans = trace.get("spans", [])

    # parse labels and build span_id -> labels map
    labels = parse_labels(labels_raw)
    label_map = map_labels_to_spans(labels)

    # find all step spans
    step_spans = find_step_spans(spans)

    records = []
    for step_span in step_spans:
        step_name = step_span.get("span_name", "")
        step_num = int(step_name.split()[1]) if step_name else 0
        step_span_id = step_span.get("span_id", "")

        # get the LLM call for this step
        llm_span = get_llm_call_from_step(step_span)
        if not llm_span:
            continue

        llm_span_id = llm_span.get("span_id", "")
        thought, code = extract_thought_and_code(llm_span)

        # get TRAIL labels for this step's spans
        trail_labels = [
        {**label, "category": normalise_category(label["category"])}
        for label in label_map.get(llm_span_id, [])
        ]

        if not thought and not code:
            continue

        record = {
            "trace_id": trace_id,
            "step_number": step_num,
            "step_span_id": step_span_id,
            "llm_span_id": llm_span_id,
            "thought": thought,
            "code": code,
            "trail_labels": trail_labels,
            "has_trail_error": len(trail_labels) > 0,
            # placeholders for later stages
            "nli_label": None,
            "nli_confidence": None,
            "auditk_label": None,
            "auditk_confidence": None,
            "agreement": None,
        }
        records.append(record)

    return records


def main():
    print("Loading TRAIL swe_bench split...")
    try:
        dataset = load_dataset(
            "PatronusAI/TRAIL",
            split="swe_bench",
            trust_remote_code=True,
        )
    except Exception as e:
        print(f"Failed to load dataset: {e}")
        print("Make sure you've accepted the dataset terms at:")
        print("https://huggingface.co/datasets/PatronusAI/TRAIL")
        sys.exit(1)

    print(f"Loaded {len(dataset)} traces. Parsing...")

    all_records = []
    failed_traces = 0

    for row in tqdm(dataset, desc="Parsing traces"):
        try:
            records = process_trace(row)
            all_records.extend(records)
        except Exception as e:
            failed_traces += 1
            print(f"\nFailed trace: {e}", file=sys.stderr)

    print(f"\nParsed {len(all_records)} steps from {len(dataset)} traces")
    print(f"Failed traces: {failed_traces}")
    print(f"Steps with TRAIL errors: {sum(1 for r in all_records if r['has_trail_error'])}")

    # write output
    with open(OUTPUT_PATH, "w") as f:
        for record in all_records:
            f.write(json.dumps(record) + "\n")

    print(f"\nOutput written to {OUTPUT_PATH}")

    # quick sanity check — print first record
    if all_records:
        print("\n--- First record (thought/code preview) ---")
        r = all_records[0]
        print(f"Trace: {r['trace_id']}")
        print(f"Step:  {r['step_number']}")
        print(f"Thought (first 200 chars): {r['thought'][:200]}")
        print(f"Code (first 200 chars):    {r['code'][:200]}")
        print(f"TRAIL labels: {r['trail_labels']}")


if __name__ == "__main__":
    main()