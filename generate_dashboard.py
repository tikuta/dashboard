#!/usr/bin/env python3
"""Generate a dashboard HTML from quota.txt, squeue.json, leadm.txt, sinfo.json, and arcconf.*."""

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).parent
QUOTA_FILE = SCRIPT_DIR / "quota.txt"
SQUEUE_FILE = SCRIPT_DIR / "squeue.json"
LEADM_FILE = SCRIPT_DIR / "leadm.txt"
SINFO_FILE = SCRIPT_DIR / "sinfo.json"
LOG_FILE = SCRIPT_DIR / "log.txt"
TIPS_FILE = SCRIPT_DIR / "tips.txt"
OUTPUT_FILE = SCRIPT_DIR / "dashboard.html"
ARCCONF_FILES = sorted(SCRIPT_DIR.glob("arcconf.*"))
RESOURCE_SNAPSHOT_FILES = [
  "kernel.all.cpu.user.bio2q001",
  "nvidia.gpuactive.bio2q001",
  "kernel.all.cpu.user.bio2q003",
  "nvidia.gpuactive.bio2q003",
]


def format_local_mtime(path: Path) -> str:
  return datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M")


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
      "gres_total": gres_total if isinstance(gres_total, str) else "",
      "gres_used": gres_used if isinstance(gres_used, str) else "",
    }

  def _node_sort_key(name: str) -> tuple:
    """Sort argon after gpu/cpu nodes."""
    if name == "argon":
      return (1, name)
    return (0, name)

  return {"nodes": [nodes_by_name[name] for name in sorted(nodes_by_name, key=_node_sort_key)]}


def parse_log_notice(path: Path) -> tuple[str, list[str]]:
  if not path.exists():
    return "", []

  lines: list[str] = []
  for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw_line.rstrip()
    if line.strip():
      lines.append(line)

  if not lines:
    return "", []
  return lines[0], lines[1:]


def parse_tips(path: Path) -> list[str]:
  if not path.exists():
    return []
  tips: list[str] = []
  for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw_line.strip()
    if line:
      tips.append(line)
  return tips


def find_optional_data_file(name: str) -> Optional[Path]:
    for candidate in (SCRIPT_DIR / name, Path.home() / "Downloads" / name):
        if candidate.exists():
            return candidate
    return None


def parse_pcp_snapshot(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    metric = "—"
    host = "—"
    semantics = "—"
    units = "—"
    samples = "—"
    data_lines: list[str] = []
    in_data = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_data:
                data_lines.append("")
            continue

        if line.startswith("metric:"):
            metric = line.split(":", 1)[1].strip()
            continue
        if line.startswith("host:"):
            host = line.split(":", 1)[1].strip()
            continue
        if line.startswith("semantics:"):
            semantics = line.split(":", 1)[1].strip()
            continue
        if line.startswith("units:"):
            units = line.split(":", 1)[1].strip()
            continue
        if line.startswith("samples:"):
            samples = line.split(":", 1)[1].strip()
            in_data = True
            continue

        if in_data:
            data_lines.append(line)

    nonblank_lines = [line for line in data_lines if line]
    series: list[dict] = []

    if len(nonblank_lines) == 1 and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", nonblank_lines[0]):
        value_text = nonblank_lines[0]
        try:
            value = float(value_text)
        except ValueError:
            value = 0.0
        series.append({"label": metric, "value": value, "raw": value_text})
    elif len(nonblank_lines) >= 2:
        labels = nonblank_lines[-2].split()
        values = nonblank_lines[-1].split()
        for index, value_text in enumerate(values):
            label = labels[index] if index < len(labels) else f"value{index + 1}"
            try:
                value = float(value_text)
            except ValueError:
                value = 0.0
            series.append({"label": label, "value": value, "raw": value_text})
    elif nonblank_lines:
        series.append({"label": metric, "value": 0.0, "raw": nonblank_lines[-1]})

    if series:
        if len(series) == 1:
            summary = f"{series[0]['raw']} {units}".strip()
        else:
            summary = f"{max(item['value'] for item in series):g} max"
    else:
        summary = "No data"

    return {
        "name": path.name,
        "metric": metric,
        "host": host,
        "semantics": semantics,
        "units": units,
        "samples": samples,
        "summary": summary,
        "series": series,
        "updated": format_local_mtime(path),
    }


def build_resource_snapshot_cards(snapshots: list[dict]) -> str:
    if not snapshots:
        return ""

    snapshots_by_host: dict[str, dict] = {}
    for snapshot in snapshots:
        host = snapshot.get("host", "—")
        entry = snapshots_by_host.setdefault(
            host,
            {
                "host": host,
                "updated": snapshot.get("updated", "—"),
                "snapshots": {},
            },
        )
        if snapshot.get("updated", "—") > entry["updated"]:
            entry["updated"] = snapshot.get("updated", "—")
        entry["snapshots"][snapshot.get("metric", "—")] = snapshot

    cards = []
    for host in sorted(snapshots_by_host):
        node = snapshots_by_host[host]
        cpu_snapshot = node["snapshots"].get("kernel.all.cpu.user")
        gpu_snapshot = node["snapshots"].get("nvidia.gpuactive")

        metric_sections = []
        for label, snapshot, color in (
            ("CPU User", cpu_snapshot, "#2563eb"),
            ("GPU Active", gpu_snapshot, "#7c3aed"),
        ):
            if not snapshot:
                continue

            series = snapshot.get("series", [])
            graph_rows = []
            if len(series) == 1:
                value = series[0].get("value", 0.0)
                bar_width = max(0.0, min(100.0, value))
                graph_rows.append(
                    "<div style='display:flex;align-items:center;gap:.75rem;'>"
                    "<div style='flex:1;'>"
                    "<div style='background:#e5e7eb;border-radius:9999px;height:8px;overflow:hidden;'>"
                    f"<div style='background:{color};width:{bar_width}%;height:100%;transition:width .3s;'></div>"
                    "</div>"
                    "</div>"
                    f"<div style='font-size:.8rem;font-weight:700;color:{color};min-width:52px;text-align:right;'>{html.escape(str(series[0].get('raw', '0')))}%</div>"
                    "</div>"
                )
            else:
                scale = max(100.0, max((item.get("value", 0.0) for item in series), default=0.0))
                for item in series:
                    value = item.get("value", 0.0)
                    bar_width = max(0.0, min(100.0, (value / scale) * 100 if scale else 0.0))
                    graph_rows.append(
                        "<div style='display:grid;grid-template-columns:48px 1fr 44px;align-items:center;gap:.5rem;'>"
                        f"<div style='font-size:.75rem;color:#64748b;font-weight:600;'>{html.escape(str(item.get('label', '')))}</div>"
                        "<div style='background:#e5e7eb;border-radius:9999px;height:8px;overflow:hidden;'>"
                        f"<div style='background:{color};width:{bar_width}%;height:100%;transition:width .3s;'></div>"
                        "</div>"
                        f"<div style='font-size:.78rem;font-weight:700;color:{color};text-align:right;'>{html.escape(str(item.get('raw', '0')))}%</div>"
                        "</div>"
                    )

            metric_sections.append(
                "<div style='margin-top:1rem;'>"
                f"<div style='font-size:.7rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.5rem;'>{label}</div>"
                f"<div style='display:grid;gap:.45rem;'>{''.join(graph_rows)}</div>"
                
                "</div>"
            )

        cards.append(
            "<div style='flex:1;min-width:320px;margin-bottom:1rem;'>"
            "<div style='background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:1.5rem;'>"
            f"<div style='display:flex;align-items:center;justify-content:space-between;gap:.75rem;margin-bottom:1rem;'>"
            f"<div style='font-size:.85rem;color:#1e293b;font-weight:700;font-family:monospace;'>{html.escape(host)}</div>"
            f"<div style='font-size:.72rem;color:#64748b;'>Updated: {html.escape(node['updated'])}</div>"
            "</div>"
            f"{''.join(metric_sections)}"
            "</div>"
            "</div>"
        )

    return (
        "<div style='padding:1rem 1.25rem 0;'>"
        "<div style='display:flex;flex-wrap:wrap;gap:1rem;'>"
        f"{''.join(cards)}"
        "</div></div>"
    )


def parse_arcconf(path: Path) -> dict:
    """Parse arcconf output and collect RAID health warnings."""
    report = {
        "name": path.name,
    "updated": format_local_mtime(path),
        "controllers": None,
        "logical_devices": [],
        "issues": [],
    }

    text = path.read_text(encoding="utf-8", errors="replace")
    controller_match = re.search(r"Controllers found:\s*(\d+)", text)
    if controller_match:
        report["controllers"] = int(controller_match.group(1))

    current_device = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        logical_match = re.match(r"Logical Device number\s+(\d+)", line)
        if logical_match:
            if current_device is not None:
                report["logical_devices"].append(current_device)
            current_device = {
                "number": int(logical_match.group(1)),
                "name": "—",
                "raid_level": "—",
                "status": "—",
                "consistency_check": "—",
            }
            continue

        if current_device is None:
            continue

        if line.startswith("Logical Device name"):
            current_device["name"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("RAID level"):
            current_device["raid_level"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Status of Logical Device"):
            current_device["status"] = line.split(":", 1)[1].strip()
            if current_device["status"].upper() != "OPTIMAL":
                report["issues"].append(
                    f"{path.name} logical device {current_device['number']} ({current_device['name']}): status is {current_device['status']}"
                )
            continue
        if line.startswith("Consistency Check Status"):
            current_device["consistency_check"] = line.split(":", 1)[1].strip()
            continue

        device_match = re.match(r"Device\s+(\d+)\s+:+\s*(.+)", line)
        if device_match:
            availability = device_match.group(2)
            availability_upper = availability.upper()
            if not (
                availability_upper.startswith("PRESENT")
                or availability_upper.startswith("DEDICATED HOT-SPARE")
            ):
                report["issues"].append(
                    f"{path.name} device {device_match.group(1)}: {availability}"
                )
            for keyword in ("FAILED", "DEGRADED", "OFFLINE", "MISSING", "ERROR", "PREDICTIVE FAILURE"):
                if keyword in availability_upper:
                    report["issues"].append(
                        f"{path.name} device {device_match.group(1)}: {availability}"
                    )
                    break

    if current_device is not None:
        report["logical_devices"].append(current_device)

    return report


def build_arcconf_section(reports: list[dict]) -> str:
  if not reports:
    return '<div style="padding:1rem 1.25rem;color:#64748b;">No arcconf files found.</div>'

  report_cards = []
  for report in reports:
    issue_count = len(report["issues"])
    badge_bg = "#fef2f2" if issue_count else "#ecfdf5"
    badge_fg = "#dc2626" if issue_count else "#16a34a"
    logical_rows = []
    for device in report["logical_devices"]:
      logical_rows.append(
        "<tr>"
        f"<td style='padding:.45rem .6rem;border-bottom:1px solid #eef2f7;'>{html.escape(str(device['number']))}</td>"
        f"<td style='padding:.45rem .6rem;border-bottom:1px solid #eef2f7;'>{html.escape(device['name'])}</td>"
        f"<td style='padding:.45rem .6rem;border-bottom:1px solid #eef2f7;'>{html.escape(device['raid_level'])}</td>"
        f"<td style='padding:.45rem .6rem;border-bottom:1px solid #eef2f7;'>{html.escape(device['status'])}</td>"
        f"<td style='padding:.45rem .6rem;border-bottom:1px solid #eef2f7;'>{html.escape(device['consistency_check'])}</td>"
        "</tr>"
      )
    logical_body = "".join(logical_rows)
    if not logical_body:
      logical_body = (
        '<tr><td colspan="5" style="padding:.6rem;color:#9ca3af;text-align:center;">'
        'No logical devices found'
        '</td></tr>'
      )

    logical_table = (
      '<table style="width:100%;border-collapse:collapse;font-size:.82rem;">'
      '<thead><tr>'
      '<th style="background:#f8fafc;color:#64748b;font-size:.7rem;text-transform:uppercase;text-align:left;padding:.45rem .6rem;">#</th>'
      '<th style="background:#f8fafc;color:#64748b;font-size:.7rem;text-transform:uppercase;text-align:left;padding:.45rem .6rem;">Logical Device</th>'
      '<th style="background:#f8fafc;color:#64748b;font-size:.7rem;text-transform:uppercase;text-align:left;padding:.45rem .6rem;">RAID</th>'
      '<th style="background:#f8fafc;color:#64748b;font-size:.7rem;text-transform:uppercase;text-align:left;padding:.45rem .6rem;">Status</th>'
      '<th style="background:#f8fafc;color:#64748b;font-size:.7rem;text-transform:uppercase;text-align:left;padding:.45rem .6rem;">Consistency</th>'
      '</tr></thead>'
      f"<tbody>{logical_body}</tbody>"
      '</table>'
    )
    issue_html = ""
    if report["issues"]:
      issue_items = "".join(f"<li>{html.escape(issue)}</li>" for issue in report["issues"])
      issue_html = (
        '<div style="margin-top:.9rem;background:#fef2f2;color:#991b1b;border:1px solid #fecaca;border-radius:10px;padding:.75rem .9rem;">'
        '<div style="font-weight:700;margin-bottom:.35rem;">Warnings</div>'
        f"<ul style='margin:0;padding-left:1.1rem;'>{issue_items}</ul>"
        '</div>'
      )

    report_name = html.escape(report["name"])
    controllers = report["controllers"] if report["controllers"] is not None else "—"
    report_cards.append(
      f'<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:1rem;box-shadow:0 1px 4px rgba(0,0,0,.06);">'
      f'<div style="display:flex;align-items:center;justify-content:space-between;gap:.75rem;margin-bottom:.75rem;">'
      f'<div style="font-weight:700;">{report_name}</div>'
      f'<div style="background:{badge_bg};color:{badge_fg};border-radius:9999px;font-size:.72rem;font-weight:700;padding:2px 8px;">{issue_count} warning(s)</div>'
      '</div>'
      f'<div style="font-size:.75rem;font-weight:600;color:#64748b;margin-bottom:.75rem;">Updated: {report["updated"]}</div>'
      f'<div style="font-size:.85rem;color:#475569;margin-bottom:.75rem;">Controllers: {controllers} | Logical devices: {len(report["logical_devices"])}'
      '</div>'
      f'{logical_table}'
      f'{issue_html}'
      '</div>'
    )

  return (
    '<div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));gap:1rem;padding:1rem 1.25rem 1.25rem;">'
    f"{''.join(report_cards)}"
    '</div>'
  )


def pct_color(pct: int) -> str:
    if pct >= 90:
        return "#ef4444"
    if pct >= 70:
        return "#f97316"
    if pct >= 40:
        return "#eab308"
    return "#22c55e"


def parse_size_value(value: str) -> float:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)?\s*$", value)
    if not match:
        return 0.0

    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    scale = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
        "tib": 1024**4,
        "p": 1024**5,
        "pb": 1024**5,
        "pib": 1024**5,
    }
    return number * scale.get(unit, 1.0)


def parse_tape_percent(value: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else 0.0


def tape_assigned_owner(barcode: str) -> str:
  match = re.search(r"(\d+)(?=L\d+$)", barcode)
  if not match:
    return "—"

  slot = int(match.group(1)) % 100
  if 20 <= slot <= 29:
    return "cryoem (auto-backup)"
  if 30 <= slot <= 39:
    return "genome & quantum"
  return "—"


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
          f'<td class="mono" data-sort-value="{entry["mount"]}">{entry["mount"]}</td>'
          f'<td data-sort-value="{parse_size_value(entry["used"])}">{entry["used"]}</td>'
          f'<td data-sort-value="{parse_size_value(entry["free"])}">{entry["free"]}</td>'
          f'<td data-sort-value="{parse_size_value(entry["total"])}">{entry["total"]}</td>'
          f'<td data-sort-value="{entry["pct"]}" style="white-space:nowrap;">{bar} <span style="color:{color};font-weight:600;">{entry["pct"]}%</span></td>'
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
        return '<tr><td colspan="7" style="text-align:center;color:#9ca3af;padding:2rem;">No tape data</td></tr>'

    rows = []
    for entry in sorted(entries, key=lambda item: (item["location"], item["barcode"])):
        color = pct_color(entry["pct"])
        owner = tape_assigned_owner(entry["barcode"])
        rows.append(
            f"<tr>"
          f'<td class="mono" data-sort-value="{entry["barcode"]}">{entry["barcode"]}</td>'
          f'<td data-sort-value="{entry["location"]}">{entry["location"]}</td>'
          f'<td data-sort-value="{owner}">{html.escape(owner)}</td>'
          f'<td data-sort-value="{parse_size_value(entry["used"])}">{entry["used"]}</td>'
          f'<td data-sort-value="{parse_size_value(entry["avail"])}">{entry["avail"]}</td>'
          f'<td data-sort-value="{parse_tape_percent(entry["use_pct"])}" style="color:{color};font-weight:600;">{entry["use_pct"]}</td>'
          f'<td data-sort-value="{entry["severity"]}">{entry["severity"]}</td>'
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
        gres_total = node.get("gres_total", "")
        gres_used = node.get("gres_used", "")
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
        elif gres_total:
            # Parse "type:count[,...]" pairs for each resource
            def _parse_gres_pairs(s: str) -> dict[str, int]:
                out: dict[str, int] = {}
                for part in s.split(","):
                    part = part.strip()
                    if ":" in part:
                        k, _, v = part.partition(":")
                        try:
                            out[k.strip()] = int(v.strip())
                        except ValueError:
                            pass
                return out

            gres_total_map = _parse_gres_pairs(gres_total)
            gres_used_map = _parse_gres_pairs(gres_used)

            gres_bars = ""
            for res_type, res_total in gres_total_map.items():
                res_used = gres_used_map.get(res_type, 0)
                res_pct = int((res_used / max(res_total, 1)) * 100)
                res_color = pct_color(res_pct)
                gres_bars += f'''
              <div style="display: flex; align-items: center; gap: .75rem;">
                <div style="flex: 1;">
                  <div style="background: #e5e7eb; border-radius: 4px; height: 6px; overflow: hidden;">
                    <div style="background: {res_color}; width: {res_pct}%; height: 100%; transition: width .3s;"></div>
                  </div>
                </div>
                <div style="font-size: .8rem; font-weight: 600; color: {res_color}; min-width: 40px; text-align: right;">
                  {res_pct}%
                </div>
              </div>
              <div style="font-size: .7rem; color: #64748b; margin-top: .3rem;">
                {res_used} / {res_total} {html.escape(res_type.upper())} used
              </div>'''

            gpu_section = f'''
            <div style="margin-top: 1rem;">
              <div style="font-size: .7rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; margin-bottom: .5rem;">
                GRES / TAPEDRIVE
              </div>
              {gres_bars}
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


def generate_html(
  quota: list[dict],
  jobs: list[dict],
  tapes: list[dict],
  meta: dict,
  sinfo: dict,
  resource_snapshots: list[dict],
  arcconf_reports: list[dict],
  log_first_line: str,
  log_other_lines: list[str],
  tips: list[str],
  quota_updated: str,
  tape_updated: str,
  resource_updated: str,
  raid_updated: str,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    quota_rows = build_quota_rows(quota)
    job_rows = build_job_rows(jobs)
    tape_rows = build_tape_rows(tapes)
    sinfo_charts = build_sinfo_charts(sinfo)
    resource_snapshot_cards = build_resource_snapshot_cards(resource_snapshots)
    arcconf_section = build_arcconf_section(arcconf_reports)
    job_count = len(jobs)
    tape_count = len(tapes)
    raid_warnings = sum(len(report["issues"]) for report in arcconf_reports)
    log_notice = html.escape(log_first_line)
    log_other_escaped = [html.escape(line) for line in log_other_lines]
    log_other_text = "\n".join(log_other_escaped)
    log_notice_details = (
      '<details class="notice-details"><summary>Show more</summary>'
      f'<pre>{log_other_text}</pre></details>'
      if log_other_escaped
      else ""
    )
    tips_json = json.dumps(tips, ensure_ascii=False)

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
  .notice {{ font-size: .8rem; opacity: .9; margin-top: .35rem; color: #fde68a; font-weight: 600; }}
  .notice-details {{ margin-top: .25rem; font-size: .76rem; color: #dbeafe; }}
  .notice-details summary {{ cursor: pointer; user-select: none; font-weight: 600; }}
  .notice-details pre {{ margin: .4rem 0 0; white-space: pre-wrap; font-family: "SF Mono", "Fira Code", "Consolas", monospace; }}
  .tip-boundary-wrap {{
    max-width: 1400px;
    margin: .28rem auto 0;
    padding: 0 2rem;
    position: relative;
    z-index: 2;
  }}
  .tip {{
    display: block;
    margin: 0;
    padding: .2rem .45rem;
    font-size: .8rem;
    font-weight: 600;
    color: #1e40af;
    background: #e0f2fe;
  }}
  .tip-label {{ color: #2563eb; margin-right: .2rem; }}
  .timestamp {{ font-size: .8rem; opacity: .75; text-align: right; }}
  .quick-links {{
    margin-top: .55rem;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: .65rem;
    max-width: 100%;
  }}
  .link-group {{
    display: inline-flex;
    align-items: center;
    gap: .35rem;
    flex: 0 0 auto;
    flex-wrap: wrap;
  }}
  .link-group-title {{
    flex: 0 0 auto;
    color: #bfdbfe;
    font-size: .68rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
    padding: .18rem .45rem;
    background: rgba(30, 58, 95, .18);
  }}
  .quick-link {{
    color: #dbeafe; text-decoration: none; font-size: .76rem; font-weight: 600;
    border: 1px solid rgba(219, 234, 254, .45);
    padding: .26rem .55rem; border-radius: 9999px;
    background: rgba(255, 255, 255, .08);
    transition: background .2s ease;
    flex: 0 1 auto;
    line-height: 1.2;
  }}
  .quick-link:hover {{ background: rgba(255, 255, 255, .2); }}

  main {{ padding: 2rem 2rem 5rem; max-width: 1400px; margin: 0 auto; }}
  
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
  th.sortable {{
    cursor: pointer;
    user-select: none;
  }}
  th.sortable::after {{
    content: "↕";
    margin-left: .35rem;
    font-size: .7rem;
    color: #cbd5e1;
  }}
  th.sortable[data-sort-direction="asc"]::after {{ content: "↑"; color: #2563eb; }}
  th.sortable[data-sort-direction="desc"]::after {{ content: "↓"; color: #2563eb; }}

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

  @keyframes stale-blink {{
    0%, 49% {{ opacity: 1; }}
    50%, 100% {{ opacity: 0; }}
  }}
  .stale-date {{
    color: #ef4444 !important;
    animation: stale-blink 1s step-start infinite;
  }}

  @media (max-width: 640px) {{
    main {{ padding: 1rem; }}
    header {{ padding: 1rem; }}
    .header-main, .header-meta {{ flex: 1 1 100%; text-align: left; }}
    .header-meta {{ white-space: normal; }}
    .tip-boundary-wrap {{ padding: 0 1rem; margin-top: .25rem; }}
    .tip {{ max-width: 100%; }}
  }}
</style>
</head>
<body>
<header>
  <div class="header-main">
    <h1>&#x1F5A5; Cluster Dashboard</h1>
    <div class="subtitle">Cluster: {meta['cluster']} &nbsp;|&nbsp; Slurm {meta['slurm_ver']}</div>
    {f'<div class="notice">Notice: {log_notice}</div>' if log_notice else ''}
    {log_notice_details}
    <div class="quick-links">
      <div class="link-group">
        <div class="link-group-title">Guide</div>
        <a class="quick-link" href="https://bio2q.kibe.la/" target="_blank" rel="noopener noreferrer">Wiki</a>
      </div>
      <div class="link-group">
        <div class="link-group-title">Account &amp; Access</div>
        <a class="quick-link" href="http://ssp.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">Change Password</a>
        <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSeSHGU1krKfc1X1_0vqTbbZ7HW2y9K4fCqF-sItrdWY8mzzPg/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Reset Password</a>
        <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSe5Vke-08O1U66QRV9c4Hc1biuZ2Riu3GVsS_Hm3Gcq2kKcDA/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">New Account</a>
        <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLScf2_SWZfao5emiJ2fZK6IMBKAUre4bHZGgzYO7_GZfM-cLTQ/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">New Group</a>
        <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSd4LazR45hWELow9vSFOf2cOKo3Jqc-x3L5-B_-JhKF02KBgg/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">VPN Access for non-Keio users</a>
        <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLScyuiOM5egfCpADC-nt7jiwf7QWKahLq0pfhlPsjZEIuz81dQ/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Increase Disk Quota</a>
      </div>
      <div class="link-group">
        <div class="link-group-title">Software</div>
        <a class="quick-link" href="http://cryosparc.vpn.bio2q.org/" target="_blank" rel="noopener noreferrer">CryoSPARC</a>
        <a class="quick-link" href="https://docs.google.com/forms/d/e/1FAIpQLSd56ZJ-NsfndG1xuJ_Gh-Ze1RBvBP9xN74kYCGrBbdaZd9u1A/viewform?usp=dialog" target="_blank" rel="noopener noreferrer">Software Installation/Update</a>
      </div>
    </div>
  </div>
  <div class="timestamp header-meta">
    Generated: {now}<br>
  </div>
</header>

<div class="tip-boundary-wrap">
  <div class="tip" id="tip-line"><span class="tip-label">Tip:</span><span id="tip-text">Loading...</span></div>
</div>

<main>
  <div class="cards">
    <div class="card gray">
      <div class="label">Jobs in Queue</div>
      <div class="value">{job_count}</div>
    </div>
    <div class="card">
      <div class="label">Quota</div>
      <div style="display:grid;gap:.45rem;margin-top:.6rem;">
        <div style="display:flex;align-items:baseline;justify-content:space-between;gap:1rem;">
          <span style="font-size:.78rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;">Critical</span>
          <span style="font-size:1.9rem;font-weight:700;line-height:1;color:#64748b;">{critical}</span>
        </div>
        <div style="display:flex;align-items:baseline;justify-content:space-between;gap:1rem;">
          <span style="font-size:.78rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;">Warning</span>
          <span style="font-size:1.9rem;font-weight:700;line-height:1;color:#64748b;">{warning}</span>
        </div>
      </div>
    </div>
    <div class="card {'red' if raid_warnings else 'gray'}">
      <div class="label">RAID Health</div>
      <div class="value">{raid_warnings}</div>
    </div>
  </div>

  <div class="tabs" role="tablist" aria-label="Dashboard sections">
    <button class="tab-button active" type="button" data-tab="resource" role="tab" aria-selected="true">Resources</button>
    <button class="tab-button" type="button" data-tab="jobs" role="tab" aria-selected="false">Job Queue</button>
    <button class="tab-button" type="button" data-tab="quota" role="tab" aria-selected="false">Disk Quota</button>
    <button class="tab-button" type="button" data-tab="tape" role="tab" aria-selected="false">Tape</button>
    <button class="tab-button" type="button" data-tab="raid" role="tab" aria-selected="false">RAID Health</button>
  </div>

  <div class="tab-panel active" id="tab-resource" role="tabpanel">
    <section>
      <div class="section-header" style="justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;">
        <span>&#x1F4CA; Cluster Resources</span>
        <span style="font-size:.75rem;font-weight:600;color:#64748b;">Updated: {resource_updated}</span>
      </div>
      {sinfo_charts}
    </section>

    <section>
      <div class="section-header" style="justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;">
        <span>&#x1F5A5; Cryo-EM Workstation Resources</span>
      </div>
      {resource_snapshot_cards}
    </section>
  </div>

  <div class="tab-panel" id="tab-jobs" role="tabpanel">
    <section>
      <div class="section-header" style="justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;">
        <span>&#x23F3; Job Queue (squeue) <span class="badge">{job_count}</span></span>
        <span style="font-size:.75rem;font-weight:600;color:#64748b;">Updated: {meta['last_update']}</span>
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
      <div class="section-header" style="justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;">
        <span>&#x1F4BE; Storage Quota <span class="badge">{total_mounts}</span></span>
        <span style="font-size:.75rem;font-weight:600;color:#64748b;">Updated: {quota_updated}</span>
      </div>
      <div class="filter-bar">
        <input type="search" id="quota-filter" placeholder="&#128269; Filter with mount paths..." oninput="filterQuota(this.value)">
      </div>
      <table>
        <thead>
          <tr>
            <th class="sortable" data-sort-table="quota" data-sort-column="0" data-sort-type="text">Mount</th>
            <th class="sortable" data-sort-table="quota" data-sort-column="1" data-sort-type="number">Used</th>
            <th class="sortable" data-sort-table="quota" data-sort-column="2" data-sort-type="number">Free</th>
            <th class="sortable" data-sort-table="quota" data-sort-column="3" data-sort-type="number">Total</th>
            <th class="sortable" data-sort-table="quota" data-sort-column="4" data-sort-type="number">Usage</th>
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
      <div class="section-header" style="justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;">
        <span>&#x1F4FC; Tape <span class="badge">{tape_count}</span></span>
        <span style="font-size:.75rem;font-weight:600;color:#64748b;">Updated: {tape_updated}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th class="sortable" data-sort-table="tape" data-sort-column="0" data-sort-type="text">Barcode</th>
            <th class="sortable" data-sort-table="tape" data-sort-column="1" data-sort-type="text">Location</th>
            <th class="sortable" data-sort-table="tape" data-sort-column="2" data-sort-type="text">Assigned Owner</th>
            <th class="sortable" data-sort-table="tape" data-sort-column="3" data-sort-type="number">Total</th>
            <th class="sortable" data-sort-table="tape" data-sort-column="4" data-sort-type="number">Avail</th>
            <th class="sortable" data-sort-table="tape" data-sort-column="5" data-sort-type="number">Use%</th>
            <th class="sortable" data-sort-table="tape" data-sort-column="6" data-sort-type="text">Severity</th>
          </tr>
        </thead>
        <tbody id="tape-tbody">
          {tape_rows}
        </tbody>
      </table>
    </section>
  </div>

  <div class="tab-panel" id="tab-raid" role="tabpanel">
    <section>
      <div class="section-header" style="justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;">
        <span>&#x1F6E0; RAID Health <span class="badge">{raid_warnings}</span></span>
        <span style="font-size:.75rem;font-weight:600;color:#64748b;">Updated: {", ".join(f'{report["name"]}: {report["updated"]}' for report in arcconf_reports)}</span>
      </div>
      {arcconf_section}
    </section>
  </div>
</main>

<footer>
  Admin: 
  <a href="http://grafana.vpn.bio2q.org" target="_blank" rel="noopener noreferrer">Grafana</a>
  <a href="https://neon.vpn.bio2q.org:8443/maxview/manager/login.xhtml" target="_blank" rel="noopener noreferrer">maxView (neon)</a>
  <a href="https://argon.vpn.bio2q.org:8443/maxview/manager/login.xhtml" target="_blank" rel="noopener noreferrer">maxView (argon)</a>
  <a href="https://10.181.64.36/login.php" target="_blank" rel="noopener noreferrer">TS4300</a>
  <a href="http://gold.vpn.bio2q.org:8080" target="_blank" rel="noopener noreferrer">LAM</a>
</footer>

<script>
const dashboardTips = {tips_json};

function pickRandomTip() {{
  if (!dashboardTips.length) {{
    return '';
  }}
  const index = Math.floor(Math.random() * dashboardTips.length);
  return dashboardTips[index];
}}

const tipText = document.getElementById('tip-text');
if (tipText) {{
  const selectedTip = pickRandomTip();
  tipText.textContent = selectedTip || 'No tips available';
}}

function filterQuota(query) {{
  const rows = document.querySelectorAll('#quota-tbody tr');
  const lowercaseQuery = query.toLowerCase();
  rows.forEach((row) => {{
    const mount = row.cells[0] ? row.cells[0].textContent.toLowerCase() : '';
    row.style.display = mount.includes(lowercaseQuery) ? '' : 'none';
  }});
}}

function parseSortableValue(cell, type) {{
  if (!cell) {{
    return '';
  }}
  const rawValue = cell.dataset.sortValue ?? cell.textContent.trim();
  if (type === 'number') {{
    const numeric = Number(rawValue);
    return Number.isNaN(numeric) ? 0 : numeric;
  }}
  return rawValue.toLowerCase();
}}

function sortTable(tableName, columnIndex, type, header) {{
  const tbody = document.querySelector(`#${{tableName}}-tbody`);
  if (!tbody) {{
    return;
  }}

  const currentDirection = header.dataset.sortDirection === 'asc' ? 'asc' : 'desc';
  const nextDirection = currentDirection === 'asc' ? 'desc' : 'asc';
  const rows = Array.from(tbody.querySelectorAll('tr'));

  rows.sort((leftRow, rightRow) => {{
    const leftCell = leftRow.cells[columnIndex];
    const rightCell = rightRow.cells[columnIndex];
    const leftValue = parseSortableValue(leftCell, type);
    const rightValue = parseSortableValue(rightCell, type);

    if (leftValue < rightValue) {{
      return nextDirection === 'asc' ? -1 : 1;
    }}
    if (leftValue > rightValue) {{
      return nextDirection === 'asc' ? 1 : -1;
    }}
    return 0;
  }});

  rows.forEach((row) => tbody.appendChild(row));

  document.querySelectorAll(`th.sortable[data-sort-table="${{tableName}}"]`).forEach((item) => {{
    item.dataset.sortDirection = '';
  }});
  header.dataset.sortDirection = nextDirection;
}}

document.querySelectorAll('th.sortable').forEach((header) => {{
  header.addEventListener('click', () => {{
    sortTable(
      header.dataset.sortTable,
      Number(header.dataset.sortColumn),
      header.dataset.sortType || 'text',
      header,
    );
  }});
}});

function setActiveTab(tabName) {{
  const buttons = Array.from(document.querySelectorAll('.tab-button'));
  const validTabNames = new Set(buttons.map((button) => button.dataset.tab));
  const targetTab = validTabNames.has(tabName) ? tabName : 'resource';

  buttons.forEach((button) => {{
    const isActive = button.dataset.tab === targetTab;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', isActive ? 'true' : 'false');
  }});

  document.querySelectorAll('.tab-panel').forEach((panel) => {{
    panel.classList.toggle('active', panel.id === `tab-${{targetTab}}`);
  }});
}}

const currentParams = new URLSearchParams(window.location.search);
const initialTab = (currentParams.get('tab') || 'resource').toLowerCase();
setActiveTab(initialTab);

document.querySelectorAll('.tab-button').forEach((button) => {{
  button.addEventListener('click', () => {{
    const tabName = button.dataset.tab || 'resource';
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set('tab', tabName);
    window.location.href = nextUrl.toString();
  }});
}});

(function checkStaleDates() {{
  const now = new Date();
  const THRESHOLD_MS = 5 * 60 * 1000;
  const datePattern = /(\\d{{4}}-\\d{{2}}-\\d{{2}} \\d{{2}}:\\d{{2}})/g;

  const walker = document.createTreeWalker(
    document.body,
    NodeFilter.SHOW_TEXT,
    {{
      acceptNode(node) {{
        return /Updated:|Generated:/.test(node.nodeValue)
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_SKIP;
      }},
    }},
  );

  let node;
  while ((node = walker.nextNode())) {{
    const matches = [...node.nodeValue.matchAll(datePattern)];
    if (!matches.length) continue;

    const isStale = matches.some(([, dateStr]) => {{
      const d = new Date(dateStr.replace(' ', 'T'));
      return !Number.isNaN(d.getTime()) && now - d > THRESHOLD_MS;
    }});

    if (isStale) {{
      const parent = node.parentElement;
      if (parent) parent.classList.add('stale-date');
    }}
  }}
}}());\n</script>
</body>
</html>
"""


def main() -> None:
    quota = parse_quota(QUOTA_FILE)
    jobs, meta = parse_squeue(SQUEUE_FILE)
    tapes = parse_leadm(LEADM_FILE)
    sinfo = parse_sinfo(SINFO_FILE)
    log_first_line, log_other_lines = parse_log_notice(LOG_FILE)
    tips = parse_tips(TIPS_FILE)
    resource_snapshots = []
    for snapshot_name in RESOURCE_SNAPSHOT_FILES:
        snapshot_path = find_optional_data_file(snapshot_name)
        if snapshot_path is not None:
            resource_snapshots.append(parse_pcp_snapshot(snapshot_path))
    arcconf_reports = [parse_arcconf(path) for path in ARCCONF_FILES]
    quota_updated = format_local_mtime(QUOTA_FILE)
    tape_updated = format_local_mtime(LEADM_FILE)
    resource_updated = format_local_mtime(SINFO_FILE)
    raid_source_files = ARCCONF_FILES or [SCRIPT_DIR / "arcconf.*"]
    raid_updated = max((format_local_mtime(path) for path in raid_source_files if path.exists()), default="—")
    for report in arcconf_reports:
        for issue in report["issues"]:
            print(f"WARNING: {issue}")
    html = generate_html(
        quota,
        jobs,
        tapes,
        meta,
        sinfo,
        resource_snapshots,
        arcconf_reports,
        log_first_line,
        log_other_lines,
        tips,
        quota_updated,
        tape_updated,
        resource_updated,
        raid_updated,
    )
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Dashboard written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
