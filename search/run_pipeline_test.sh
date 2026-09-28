#!/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=.
PIPE_OUT=search/pipeline_test PIPE_MAX=1 python3 search/pipeline.py && python3 search/finalize.py search/pipeline_test
