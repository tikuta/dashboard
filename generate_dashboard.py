#!/usr/bin/env python3
"""Generate a dashboard HTML from quota.txt, squeue.json, leadm.txt, and sinfo.json."""

import json
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
QUOTA_FILE = SCRIPT_DIR / "quota.txt"
SQUEUE_FILE = SCRIPT_DIR / "squeue.json"
LEADM_FILE = SCRIPT_DIR / "leadm.txt"
SINFO_FILE = SCRIPT_DIR / "sinfo.json"
OUTPUT_FILE = SCRIPT_DIR / "dashboard.html"


def parse_quota(path: Path) -> list[dict]:
    """Parse quota.txt into a list of dicts."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        mount = parts[0]
        used = parts[1]
        free = parts[2]
        total = parts[3]
        pct_str = parts[4].rstrip("%")
        try:
            pct = int(pct_str)
        except ValueError:
            pct = 0
        entries.append(
            {
                "mount": mount,
                "used": used,
                "free": free,
                "total": total,
                "pct": pct,
            }
        )
    return entries


def parse_squeue(path: Path) -> tuple[list[dict], dict]:
    """Parse squeue.json and return (jobs, meta)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs = data.get("jobs", [])
    slurm = data.get("meta", {}).get("slurm", {})
    last_update_epoch = data.get("last_update", {}).get("number")
    meta = {
        "cluster": slurm.get("cluster", "—"),
        "slurm_ver": slurm.get("release", "—"),
        "last_update": (
            datetime.fromtimestamp(last_update_epoch, tz=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M")
            if last_update_epoch
            else "—"
        ),
    }
    return jobs, meta


def parse_leadm(path: Path) -> list[dict]:
    """Parse leadm.txt into a list of tape dicts."""
    entries = []
    for index, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if index == 0 and line.startswith("Barcode"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue

        barcode, location, used, avail, use_pct = parts[:5]
        severity = " ".join(parts[5:])
        try:
            pct = int(use_pct.rstrip("%"))
        except ValueError:
            pct = 0

        entries.append(
            {
                "barcode": barcode,
                "location": location,
                "used": used,
                "avail": avail,
                "use_pct": use_pct,
                "pct": pct,
                "severity": severity,
            }
        )
    return entries


def parse_sinfo(path: Path) -> dict:
  """Parse sinfo.json and return one record per unique node."""
  data = json.loads(path.read_text(encoding="utf-8"))
  sinfo_list = data.get("sinfo", [])
  if not sinfo_list:
    return {"nodes": []}

  nodes_by_name: dict[str, dict] = {}
  for info in sinfo_list:
    nodes = info.get("nodes", {})
    cpus = info.get("cpus", {})
    memory = info.get("memory", {})
    gres = info.get("gres", {})
    node_names = nodes.get("nodes", [])
    if not node_names:
      continue

    node_name = node_names[0]
    memory_total = memory.get("maximum", 0)
    free_min = memory.get("free", {}).get("minimum", {}).get("number", 0)
    memory_allocated = memory.get("allocated", 0)
    gres_total = gres.get("total", "")
    gres_used = gres.get("used", "")

    gpu_total = 0
    gpu_used = 0
    if isinstance(gres_total, str) and "gpu:" in gres_total:
      try:
        gpu_total = int(gres_total.split("gpu:", 1)[1].split(",", 1)[0])
      except ValueError:
        gpu_total = 0
    if isinstance(gres_used, str) and "gpu:" in gres_used:
      try:
        gpu_used = int(gres_used.split("gpu:", 1)[1].split(",", 1)[0])
      except ValueError:
        gpu_used = 0

    state = info.get("node", {}).get("state", ["—"])
    if isinstance(state, list):
      state = state[0] if state else "—"

    nodes_by_name[node_name] = {
      "name": node_name,
      "state": str(state),
      "cpus_total": cpus.get("total", 0),
      "cpus_allocated": cpus.get("allocated", 0),
      "memory_total": memory_total,
      "memory_allocated": memory_allocated,
      "memory_free": free_min,
      "gpu_total": gpu_total,
      "gpu_used": gpu_used,
    }

  return {"nodes": [nodes_by_name[name] for name in sorted(nodes_by_name)]}


def pct_color(pct: int) -> str:
    if pct >= 90:
        return "#ef4444"
    if pct >= 70:
        return "#f97316"
    if pct >= 40:
        return "#eab308"
    return "#22c55e"


def node_state_badge(state: str) -> str:
  colours = {
    "IDLE": ("#ecfdf5", "#16a34a"),
    "MIXED": ("#fff7ed", "#ea580c"),
    "ALLOCATED": ("#eff6ff", "#2563eb"),
    "DOWN": ("#fef2f2", "#dc2626"),
    "DRAIN": ("#fef2f2", "#b91c1c"),
  }
  bg, fg = colours.get(state.upper(), ("#f1f5f9", "#475569"))
  return (
    f'<span style="background:{bg};color:{fg};padding:2px 8px;'
    f'border-radius:9999px;font-size:0.7rem;font-weight:700;">{state}</span>'
  )


def job_state_badge(state: str) -> str:
    colours = {
        "RUNNING": ("#dcfce7", "#16a34a"),
        "PENDING": ("#fef9c3", "#ca8a04"),
        "FAILED": ("#fee2e2", "#dc2626"),
        "COMPLETED": ("#f0fdf4", "#15803d"),
        "CANCELLED": ("#f3f4f6", "#6b7280"),
    }
    bg, fg = colours.get(state.upper(), ("#e0e7ff", "#4338ca"))
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:9999px;font-size:0.75rem;font-weight:600;">{state}</span>'
    )


def build_quota_rows(entries: list[dict]) -> str:
    rows = []
    sorted_entries = sorted(entries, key=lambda item: item["pct"], reverse=True)
    for entry in sorted_entries:
        color = pct_color(entry["pct"])
        bar = (
            f'<div style="background:#e5e7eb;border-radius:4px;height:10px;min-width:120px;">'
            f'<div style="background:{color};width:{entry["pct"]}%;height:10px;border-radius:4px;'
            f'transition:width .3s;"></div></div>'
        )
        rows.append(
            f"<tr>"
            f'<td class="mono">{entry["mount"]}</td>'
            f"<td>{entry['used']}</td>"
            f"<td>{entry['free']}</td>"
            f"<td>{entry['total']}</td>"
            f'<td style="white-space:nowrap;">{bar} <span style="color:{color};font-weight:600;">{entry["pct"]}%</span></td>'
            f"</tr>"
        )
    return "\n".join(rows)


def build_job_rows(jobs: list[dict]) -> str:
    if not jobs:
        return '<tr><td colspan="8" style="text-align:center;color:#9ca3af;padding:2rem;">No jobs in queue</td></tr>'

    rows = []
    for job in jobs:
        job_id = job.get("job_id", "—")
        name = job.get("name", "—")
        user = job.get("user_name", job.get("user", "—"))
        if isinstance(job.get("job_state"), list):
            state = job.get("job_state", ["—"])[0]
        else:
            state = job.get("job_state", "—")
        partition = job.get("partition", "—")
        if isinstance(job.get("node_count"), dict):
            nodes = job.get("node_count", {}).get("number", "—")
        else:
            nodes = job.get("node_count", "—")
        if isinstance(job.get("cpus"), dict):
            cpus = job.get("cpus", {}).get("number", "—")
        else:
            cpus = job.get("cpus", "—")

        time_limit = job.get("time_limit", {})
        if isinstance(time_limit, dict):
            time_limit = time_limit.get("number", "—")
        if isinstance(time_limit, int):
            hours, minutes = divmod(time_limit, 60)
            time_limit = f"{hours}h{minutes:02d}m"

        rows.append(
            f"<tr>"
            f"<td>{job_id}</td>"
            f"<td>{name}</td>"
            f"<td>{user}</td>"
            f"<td>{job_state_badge(str(state))}</td>"
            f"<td>{partition}</td>"
            f"<td>{nodes}</td>"
            f"<td>{cpus}</td>"
            f"<td>{time_limit}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def build_tape_rows(entries: list[dict]) -> str:
    if not entries:
        return '<tr><td colspan="6" style="text-align:center;color:#9ca3af;padding:2rem;">No tape data</td></tr>'

    rows = []
    for entry in sorted(entries, key=lambda item: (item["location"], item["barcode"])):
        color = pct_color(entry["pct"])
        rows.append(
            f"<tr>"
            f'<td class="mono">{entry["barcode"]}</td>'
            f"<td>{entry['location']}</td>"
            f"<td>{entry['used']}</td>"
            f"<td>{entry['avail']}</td>"
            f'<td style="color:{color};font-weight:600;">{entry["use_pct"]}</td>'
            f"<td>{entry['severity']}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def build_sinfo_charts(sinfo: dict) -> str:
    """Build resource usage charts HTML for each node."""
    nodes_data = sinfo.get("nodes", [])
    if not nodes_data:
        return '<div style="text-align:center;color:#9ca3af;padding:2rem;">No resource data available</div>'
    
    def node_card(node: dict) -> str:
        name = node.get("name", "—")
        cpus_total = node.get("cpus_total", 0)
        cpus_allocated = node.get("cpus_allocated", 0)
        gpu_total = node.get("gpu_total", 0)
        gpu_used = node.get("gpu_used", 0)
        
        cpu_pct = int((cpus_allocated / max(cpus_total, 1)) * 100) if cpus_total else 0
        
        cpu_color = pct_color(cpu_pct)
        gpu_pct = int((gpu_used / max(gpu_total, 1)) * 100) if gpu_total else 0
        gpu_color = pct_color(gpu_pct)
        gpu_section = ""
        if gpu_total > 0:
            gpu_section = f'''
            <div style="margin-top: 1rem;">
              <div style="font-size: .7rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; margin-bottom: .5rem;">
                GRES / GPU
              </div>
              <div style="display: flex; align-items: center; gap: .75rem;">
                <div style="flex: 1;">
                  <div style="background: #e5e7eb; border-radius: 4px; height: 6px; overflow: hidden;">
                    <div style="background: {gpu_color}; width: {gpu_pct}%; height: 100%; transition: width .3s;"></div>
                  </div>
                </div>
                <div style="font-size: .8rem; font-weight: 600; color: {gpu_color}; min-width: 40px; text-align: right;">
                  {gpu_pct}%
                </div>
              </div>
              <div style="font-size: .7rem; color: #64748b; margin-top: .3rem;">
                {gpu_used} / {gpu_total} GPU used
              </div>
            </div>
            '''
        
        return f'''
        <div style="flex: 1; min-width: 280px; margin-bottom: 1rem;">
          <div style="background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 1.5rem;">
            <div style="display:flex; align-items:center; justify-content:space-between; gap:.75rem; margin-bottom: 1rem;">
              <div style="font-size: .85rem; color: #1e293b; font-weight: 700; font-family: monospace;">
                {name}
              </div>
              <div>
                {node_state_badge(node.get("state", "—"))}
              </div>
            </div>
            
            <!-- CPU -->
            <div style="margin-bottom: 1.2rem;">
              <div style="font-size: .7rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; margin-bottom: .5rem;">
                CPUs
              </div>
              <div style="display: flex; align-items: center; gap: .75rem;">
                <div style="flex: 1;">
                  <div style="background: #e5e7eb; border-radius: 4px; height: 6px; overflow: hidden;">
                    <div style="background: {cpu_color}; width: {cpu_pct}%; height: 100%; transition: width .3s;"></div>
                  </div>
                </div>
                <div style="font-size: .8rem; font-weight: 600; color: {cpu_color}; min-width: 40px; text-align: right;">
                  {cpu_pct}%
                </div>
              </div>
              <div style="font-size: .7rem; color: #64748b; margin-top: .3rem;">
                {cpus_allocated} / {cpus_total}
              </div>
            </div>
            {gpu_section}
          </div>
        </div>
        '''
    
    cards_html = "\n".join(node_card(node) for node in nodes_data)
    
    return f'''
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem;">
      {cards_html}
    </div>
    '''


def generate_html(quota: list[dict], jobs: list[dict], tapes: list[dict], meta: dict, sinfo: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    quota_rows = build_quota_rows(quota)
    job_rows = build_job_rows(jobs)
    tape_rows = build_tape_rows(tapes)
    sinfo_charts = build_sinfo_charts(sinfo)
    job_count = len(jobs)
    tape_count = len(tapes)

    total_mounts = len(quota)
    critical = sum(1 for entry in quota if entry["pct"] >= 90)
    warning = sum(1 for entry in quota if 70 <= entry["pct"] < 90)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cluster Dashboard</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f1f5f9; color: #1e293b;
    overflow-y: scroll;
  }}
  html {{ scrollbar-gutter: stable; }}
  header {{
    background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
    color: #fff; padding: 1.25rem 2rem;
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 1.5rem; flex-wrap: wrap;
  }}
  .header-main {{ flex: 1 1 720px; min-width: 0; }}
  .header-meta {{ flex: 0 0 auto; text-align: right; white-space: nowrap; }}
  header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 700; letter-spacing: .5px; }}
  .subtitle {{ font-size: .8rem; opacity: .75; margin-top: .2rem; }}
  .timestamp {{ font-size: .8rem; opacity: .75; text-align: right; }}
  .quick-links {{
    margin-top: .7rem; display: flex; flex-wrap: wrap; gap: .5rem;
    max-width: 100%;
  }}
  .quick-link {{
    color: #dbeafe; text-decoration: none; font-size: .8rem; font-weight: 600;
    border: 1px solid rgba(219, 234, 254, .45);
    padding: .35rem .65rem; border-radius: 9999px;
    background: rgba(255, 255, 255, .08);
    transition: background .2s ease;
    flex: 0 1 auto;
    line-height: 1.2;
  }}
  .quick-link:hover {{ background: rgba(255, 255, 255, .2); }}

  main {{ padding: 1.5rem 2rem 5rem; max-width: 1400px; margin: 0 auto; }}
  
  footer {{
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 1000;
    background: #f8fafc; border-top: 1px solid #e2e8f0;
    padding: 1rem 2rem; text-align: right; font-size: .75rem; color: #64748b;
  }}
  footer a {{
    color: #2563eb; text-decoration: none; margin-left: 1.5rem;
  }}
  footer a:hover {{ text-decoration: underline; }}

  .tabs {{ display: flex; gap: .5rem; margin-bottom: 1rem; flex-wrap: wrap; }}
  .tab-button {{
    border: 1px solid #cbd5e1; background: #fff; color: #334155;
    border-radius: 9999px; padding: .45rem .85rem; font-size: .88rem; font-weight: 700;
    cursor: pointer;
  }}
  .tab-button.active {{ background: #2563eb; border-color: #2563eb; color: #fff; }}
  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}

  .cards {{ display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .card {{
    background: #fff; border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    padding: 1rem 1.5rem; flex: 1; min-width: 160px;
  }}
  .card .label {{ font-size: .75rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; }}
  .card .value {{ font-size: 2rem; font-weight: 700; margin-top: .25rem; }}
  .card.red .value {{ color: #ef4444; }}
  .card.orange .value {{ color: #f97316; }}
  .card.blue .value {{ color: #2563eb; }}
  .card.gray .value {{ color: #64748b; }}

  .filter-bar {{
    padding: .75rem 1.25rem;
    border-bottom: 1px solid #e2e8f0;
    display: flex; align-items: center; gap: .5rem;
  }}
  .filter-bar input {{
    border: 1px solid #cbd5e1; border-radius: 6px;
    padding: .35rem .75rem; font-size: .85rem; width: 100%; max-width: 320px;
    outline: none;
  }}
  .filter-bar input:focus {{ border-color: #2563eb; box-shadow: 0 0 0 2px #bfdbfe; }}

  section {{
    background: #fff; border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    margin-bottom: 1.5rem; overflow: hidden;
  }}
  .section-header {{
    padding: .875rem 1.25rem;
    border-bottom: 1px solid #e2e8f0;
    font-weight: 700; font-size: 1rem;
    display: flex; align-items: center; gap: .5rem;
  }}
  .badge {{
    display: inline-flex; align-items: center; justify-content: center;
    background: #2563eb; color: #fff;
    border-radius: 9999px; font-size: .7rem; font-weight: 700;
    min-width: 1.4rem; height: 1.4rem; padding: 0 .35rem;
  }}

  table {{ width: 100%; border-collapse: collapse; font-size: .875rem; }}
  th {{
    background: #f8fafc; color: #64748b;
    font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
    padding: .6rem 1rem; text-align: left; border-bottom: 1px solid #e2e8f0;
  }}
  td {{ padding: .65rem 1rem; border-bottom: 1px solid #f1f5f9; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fafc; }}
  .mono {{ font-family: "SF Mono", "Fira Code", "Consolas", monospace; font-size: .8rem; }}

  @media (max-width: 640px) {{
    main {{ padding: 1rem; }}
    header {{ padding: 1rem; }}
    .header-main, .header-meta {{ flex: 1 1 100%; text-align: left; }}
    .header-meta {{ white-space: normal; }}
  }}
</style>
</head>
<body>
<header>
  <div class="header-main">
    <h1>&#x1F5A5; Cluster Dashboard</h1>
    <div class="subtitle">Cluster: {meta['cluster']} &nbsp;|&nbsp; Slurm {meta['slurm_ver']}</div>
    <div class="quick-links">
      <a class="quick-link" href="http://ssp.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">Change Password</a>
      <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSeSHGU1krKfc1X1_0vqTbbZ7HW2y9K4fCqF-sItrdWY8mzzPg/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Request Password Reset</a>
      <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSe5Vke-08O1U66QRV9c4Hc1biuZ2Riu3GVsS_Hm3Gcq2kKcDA/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Request New Account</a>
      <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSd4LazR45hWELow9vSFOf2cOKo3Jqc-x3L5-B_-JhKF02KBgg/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Request VPN Access</a>
      <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLScyuiOM5egfCpADC-nt7jiwf7QWKahLq0pfhlPsjZEIuz81dQ/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Request Disk Quota Increase</a>
      <a class="quick-link" href="http://cryosparc.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">CryoSPARC</a>
    </div>
  </div>
  <div class="timestamp header-meta">
    Generated: {now}<br>
    Queue updated: {meta['last_update']}
  </div>
</header>

<main>
  <div class="cards">
    <div class="card gray">
      <div class="label">Jobs in Queue</div>
      <div class="value">{job_count}</div>
    </div>
    <div class="card red">
      <div class="label">Critical (&ge;90%)</div>
      <div class="value">{critical}</div>
    </div>
    <div class="card orange">
      <div class="label">Warning (&ge;70%)</div>
      <div class="value">{warning}</div>
    </div>
    <div class="card blue">
      <div class="label">Tape Media</div>
      <div class="value">{tape_count}</div>
    </div>
  </div>

  <div class="tabs" role="tablist" aria-label="Dashboard sections">
    <button class="tab-button active" type="button" data-tab="resource" role="tab" aria-selected="true">Resources</button>
    <button class="tab-button" type="button" data-tab="jobs" role="tab" aria-selected="false">Job Queue</button>
    <button class="tab-button" type="button" data-tab="quota" role="tab" aria-selected="false">Disk Quota</button>
    <button class="tab-button" type="button" data-tab="tape" role="tab" aria-selected="false">Tape</button>
  </div>

  <div class="tab-panel active" id="tab-resource" role="tabpanel">
    <section>
      <div class="section-header">
        &#x1F4CA; Cluster Resources
      </div>
      {sinfo_charts}
    </section>
  </div>

  <div class="tab-panel" id="tab-jobs" role="tabpanel">
    <section>
      <div class="section-header">
        &#x23F3; Job Queue (squeue)
        <span class="badge">{job_count}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Job ID</th>
            <th>Name</th>
            <th>User</th>
            <th>State</th>
            <th>Partition</th>
            <th>Nodes</th>
            <th>CPUs</th>
            <th>Time Limit</th>
          </tr>
        </thead>
        <tbody>
          {job_rows}
        </tbody>
      </table>
    </section>
  </div>

  <div class="tab-panel" id="tab-quota" role="tabpanel">
    <section>
      <div class="section-header">
        &#x1F4BE; Storage Quota
        <span class="badge">{total_mounts}</span>
      </div>
      <div class="filter-bar">
        <input type="search" id="quota-filter" placeholder="&#128269; マウントパスで絞り込み..." oninput="filterQuota(this.value)">
      </div>
      <table>
        <thead>
          <tr>
            <th>Mount</th>
            <th>Used</th>
            <th>Free</th>
            <th>Total</th>
            <th>Usage</th>
          </tr>
        </thead>
        <tbody id="quota-tbody">
          {quota_rows}
        </tbody>
      </table>
    </section>
  </div>

  <div class="tab-panel" id="tab-tape" role="tabpanel">
    <section>
      <div class="section-header">
        &#x1F4FC; Tape
        <span class="badge">{tape_count}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Barcode</th>
            <th>Location</th>
            <th>Used</th>
            <th>Avail</th>
            <th>Use%</th>
            <th>Severity</th>
          </tr>
        </thead>
        <tbody>
          {tape_rows}
        </tbody>
      </table>
    </section>
  </div>
</main>

<footer>
  Admin: <a href="http://lam.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">LAM</a>
</footer>

<script>
function filterQuota(query) {{
  const rows = document.querySelectorAll('#quota-tbody tr');
  const lowercaseQuery = query.toLowerCase();
  rows.forEach((row) => {{
    const mount = row.cells[0] ? row.cells[0].textContent.toLowerCase() : '';
    row.style.display = mount.includes(lowercaseQuery) ? '' : 'none';
  }});
}}

document.querySelectorAll('.tab-button').forEach((button) => {{
  button.addEventListener('click', () => {{
    const tabName = button.dataset.tab;
    document.querySelectorAll('.tab-button').forEach((item) => {{
      item.classList.toggle('active', item === button);
      item.setAttribute('aria-selected', item === button ? 'true' : 'false');
    }});
    document.querySelectorAll('.tab-panel').forEach((panel) => {{
      panel.classList.toggle('active', panel.id === `tab-${{tabName}}`);
    }});
  }});
}});
</script>
</body>
</html>
"""


def main() -> None:
    quota = parse_quota(QUOTA_FILE)
    jobs, meta = parse_squeue(SQUEUE_FILE)
    tapes = parse_leadm(LEADM_FILE)
    sinfo = parse_sinfo(SINFO_FILE)
    html = generate_html(quota, jobs, tapes, meta, sinfo)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Dashboard written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
