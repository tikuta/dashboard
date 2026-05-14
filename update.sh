#!/bin/bash
set -eu

for d in /home/*/ /em/*data/ /em/analysis/*/; do echo -n "$d "; df -BT --output=used,avail,size,pcent "$d" | tail -1; done | column -t > quota.txt

squeue --json > squeue.json

set +eu
