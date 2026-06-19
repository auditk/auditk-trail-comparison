#!/usr/bin/env bash
cd "$(dirname "$0")/.." && source .venv/bin/activate && python annotation_tool/server.py
