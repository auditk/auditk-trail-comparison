#!/usr/bin/env bash
# Run from the project root so all relative paths resolve correctly.
# Tool is then at: http://localhost:8765/annotation_tool/index.html
cd "$(dirname "$0")/.." && python3 -m http.server 8765
