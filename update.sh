#!/bin/bash
BASEDIR=$(dirname $(realpath $0))

for d in /home/*/ /em/*data/ /em/analysis/*/; do
    echo -n "$d "; df -BT --output=used,avail,size,pcent "$d" | tail -1
done | column -t > $BASEDIR/quota.txt
/opt/slurm/bin/squeue --json > $BASEDIR/squeue.json
/opt/slurm/bin/sinfo -N -l --json > $BASEDIR/sinfo.json
ssh argon leadm tape list > $BASEDIR/leadm.txt
ssh argon  sudo /usr/Arcconf/arcconf getconfig 1 LD > $BASEDIR/arcconf.argon
ssh neon  sudo /usr/Arcconf/arcconf getconfig 1 LD > $BASEDIR/arcconf.neon
pmval -f 1  -s 1 -h bio2q003 nvidia.gpuactive > $BASEDIR/nvidia.gpuactive.bio2q003
pmval -f 1  -s 1 -h bio2q001 nvidia.gpuactive > $BASEDIR/nvidia.gpuactive.bio2q001
pmval -f 1  -s 1 -h bio2q003 kernel.all.cpu.user > $BASEDIR/kernel.all.cpu.user.bio2q003
pmval -f 1  -s 1 -h bio2q001 kernel.all.cpu.user > $BASEDIR/kernel.all.cpu.user.bio2q001
/usr/bin/python3 $BASEDIR/generate_dashboard.py
cp $BASEDIR/dashboard.html /usr/share/caddy/index.html