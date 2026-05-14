#!/usr/bin/env python3
"""Generate a dashboard HTML from quota.txt and squeue.json."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
QUOTA_FILE  = SCRIPT_DIR / "quota.txt"
SQUEUE_FILE = SCRIPT_DIR / "squeue.json"
OUTPUT_FILE = SCRIPT_DIR / "dashboard.html"


# ── parsers ────────────────────────────────────────────────────────────────

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
        mount   = parts[0]
        used    = parts[1]
        free    = parts[2]
        total   = parts[3]
        pct_str = parts[4].rstrip("%")
        try:
            pct = int(pct_str)
        except ValueError:
            pct = 0
        entries.append({
            "mount": mount,
            "used":  used,
            "free":  free,
            "total": total,
            "pct":   pct,
        })
    return entries


def parse_squeue(path: Path) -> tuple[list[dict], dict]:
    """Parse squeue.json and return (jobs, meta)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs = data.get("jobs", [])
    slurm = data.get("meta", {}).get("slurm", {})
    last_update_epoch = data.get("last_update", {}).get("number")
    meta = {
      "cluster":     slurm.get("cluster", "—"),
      "slurm_ver":   slurm.get("release", "—"),
      "last_update": (
        datetime.fromtimestamp(last_update_epoch, tz=timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M")
        if last_update_epoch else "—"
      ),
    }
    return jobs, meta


# ── colour helpers ─────────────────────────────────────────────────────────

def pct_color(pct: int) -> str:
    if pct >= 90:
        return "#ef4444"   # red
    if pct >= 70:
        return "#f97316"   # orange
    if pct >= 40:
        return "#eab308"   # yellow
    return "#22c55e"       # green


def job_state_badge(state: str) -> str:
    colours = {
        "RUNNING":  ("#dcfce7", "#16a34a"),
        "PENDING":  ("#fef9c3", "#ca8a04"),
        "FAILED":   ("#fee2e2", "#dc2626"),
        "COMPLETED":("#f0fdf4", "#15803d"),
        "CANCELLED":("#f3f4f6", "#6b7280"),
    }
    bg, fg = colours.get(state.upper(), ("#e0e7ff", "#4338ca"))
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:9999px;font-size:0.75rem;font-weight:600;">{state}</span>'
    )


# ── HTML builder ───────────────────────────────────────────────────────────

def build_quota_rows(entries: list[dict]) -> str:
  rows = []
  sorted_entries = sorted(entries, key=lambda item: item["pct"], reverse=True)
  for e in sorted_entries:
    color = pct_color(e["pct"])
    bar = (
      f'<div style="background:#e5e7eb;border-radius:4px;height:10px;min-width:120px;">'
      f'<div style="background:{color};width:{e["pct"]}%;height:10px;border-radius:4px;'
      f'transition:width .3s;"></div></div>'
    )
    rows.append(
      f"<tr>"
      f'<td class="mono">{e["mount"]}</td>'
      f"<td>{e['used']}</td>"
      f"<td>{e['free']}</td>"
      f"<td>{e['total']}</td>"
      f'<td style="white-space:nowrap;">{bar} <span style="color:{color};font-weight:600;">{e["pct"]}%</span></td>'
      f"</tr>"
    )
  return "\n".join(rows)


def build_job_rows(jobs: list[dict]) -> str:
    if not jobs:
        return '<tr><td colspan="8" style="text-align:center;color:#9ca3af;padding:2rem;">No jobs in queue</td></tr>'

    rows = []
    for j in jobs:
        # Field names follow Slurm REST API v0.0.44
        job_id    = j.get("job_id", "—")
        name      = j.get("name", "—")
        user      = j.get("user_name", j.get("user", "—"))
        state     = j.get("job_state", ["—"])[0] if isinstance(j.get("job_state"), list) else j.get("job_state", "—")
        partition = j.get("partition", "—")
        nodes     = j.get("node_count", {}).get("number", "—") if isinstance(j.get("node_count"), dict) else j.get("node_count", "—")
        cpus      = j.get("cpus", {}).get("number", "—") if isinstance(j.get("cpus"), dict) else j.get("cpus", "—")
        # time_limit
        tl = j.get("time_limit", {})
        time_limit = tl.get("number", "—") if isinstance(tl, dict) else tl
        if isinstance(time_limit, int):
            h, m = divmod(time_limit, 60)
            time_limit = f"{h}h{m:02d}m"

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


def generate_html(quota: list[dict], jobs: list[dict], meta: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    quota_rows = build_quota_rows(quota)
    job_rows   = build_job_rows(jobs)
    job_count  = len(jobs)

    # summary cards
    total_mounts  = len(quota)
    critical      = sum(1 for e in quota if e["pct"] >= 90)
    warning       = sum(1 for e in quota if 70 <= e["pct"] < 90)

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
  }}
  header {{
    background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
    color: #fff; padding: 1.25rem 2rem;
    display: flex; align-items: center; justify-content: space-between;
  }}
  header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 700; letter-spacing: .5px; }}
  .subtitle {{ font-size: .8rem; opacity: .75; margin-top: .2rem; }}
  .timestamp {{ font-size: .8rem; opacity: .75; text-align: right; }}
  .quick-links {{ margin-top: .6rem; display: flex; gap: .5rem; flex-wrap: wrap; }}
  .quick-link {{
    color: #dbeafe; text-decoration: none; font-size: .8rem; font-weight: 600;
    border: 1px solid rgba(219, 234, 254, .45);
    padding: .25rem .55rem; border-radius: 9999px;
    background: rgba(255, 255, 255, .08);
    transition: background .2s ease;
  }}
  .quick-link:hover {{ background: rgba(255, 255, 255, .2); }}

  main {{ padding: 1.5rem 2rem; max-width: 1400px; margin: 0 auto; }}

  /* summary cards */
  .cards {{ display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .card {{
    background: #fff; border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    padding: 1rem 1.5rem; flex: 1; min-width: 160px;
  }}
  .card .label {{ font-size: .75rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; }}
  .card .value {{ font-size: 2rem; font-weight: 700; margin-top: .25rem; }}
  .card.red .value   {{ color: #ef4444; }}
  .card.orange .value{{ color: #f97316; }}
  .card.blue .value  {{ color: #2563eb; }}
  .card.gray .value  {{ color: #64748b; }}

  /* quota filter */
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

  /* sections */
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

  /* tables */
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
    header {{ flex-direction: column; align-items: flex-start; gap: .5rem; }}
  }}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#x1F5A5; Cluster Dashboard</h1>
    <div class="subtitle">Cluster: {meta['cluster']} &nbsp;|&nbsp; Slurm {meta['slurm_ver']}</div>
    <div class="quick-links">
      <a class="quick-link" href="http://ssp.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">Change Password</a>
      <a class="quick-link" href="http://cryosparc.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">CryoSPARC</a>
      <a class="quick-link" href="http://lam.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">LAM</a>
    </div>
  </div>
  <div class="timestamp">
    Generated: {now}<br>
      Queue updated: {meta['last_update']}
  </div>
</header>

<main>

  <!-- Summary cards -->
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
  </div>

  <!-- Job queue -->
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

  <!-- Storage quota -->
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

</main>
<script>
function filterQuota(q) {{
  const rows = document.querySelectorAll('#quota-tbody tr');
  const lq = q.toLowerCase();
  rows.forEach(r => {{
    const mount = r.cells[0] ? r.cells[0].textContent.toLowerCase() : '';
    r.style.display = mount.includes(lq) ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""


# ── main ───────────────────────────────────────────────────────────────────

def main():
    quota = parse_quota(QUOTA_FILE)
    jobs, meta = parse_squeue(SQUEUE_FILE)
    html = generate_html(quota, jobs, meta)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Dashboard written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
