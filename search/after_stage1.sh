#!/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=.
while ! grep -q '^done' search/stage1.log; do sleep 60; done
python3 search/pipeline.py > search/pipeline.log 2>&1 && python3 search/finalize.py search/pipeline >> search/pipeline.log 2>&1
echo ALLDONE >> search/pipeline.log
