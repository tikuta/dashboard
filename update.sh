#!/bin/bash
set -eu

BASEDIR=$(dirname $(realpath $0))
for d in /home/*/ /em/*data/ /em/analysis/*/; do echo -n "$d "; df -BT --output=used,avail,size,pcent "$d" | tail -1; done | column -t > $BASEDIR/quota.txt && \
/opt/slurm/bin/squeue --json > $BASEDIR/squeue.json && \
/opt/slurm/bin/sinfo -N -l --json > $BASEDIR/sinfo.json && \
ssh argon leadm tape list > $BASEDIR/leadm.txt && \
/usr/bin/python3 $BASEDIR/generate_dashboard.py && \
cp $BASEDIR/dashboard.html /usr/share/caddy/index.html

set +eu
