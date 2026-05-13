#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qkview_skill.html_parsers import parse_graph_names
from qkview_skill.ihealth_client import IHealthClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render local sample PNGs for all available iHealth graph names")
    parser.add_argument("--qkview-id", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def series_for_graph(graph_name: str, points: int = 30) -> tuple[list[str], list[float], str]:
    seed = int(hashlib.sha256(graph_name.encode("utf-8")).hexdigest()[:8], 16)
    labels = [(dt.date.today() - dt.timedelta(days=points - idx - 1)).strftime("%b %d") for idx in range(points)]
    values: list[float] = []

    lower = graph_name.lower()
    if graph_name in {"Access Requests"}:
        return labels, [], "requests"
    if any(token in lower for token in ["cpu", "memory", "cache", "utilization"]):
        baseline = 35 + (seed % 30)
        amplitude = 10 + (seed % 15)
        unit = "%"
    elif "throughput" in lower:
        baseline = 4e8 + (seed % 12) * 7.5e7
        amplitude = 1.5e8 + (seed % 8) * 3.5e7
        unit = "mbps"
    elif any(token in lower for token in ["requests", "transactions", "connections", "sessions"]):
        baseline = 800 + (seed % 20) * 250
        amplitude = 250 + (seed % 9) * 90
        unit = "requests"
    else:
        baseline = 100 + (seed % 15) * 25
        amplitude = 20 + (seed % 11) * 12
        unit = "value"

    trend = ((seed % 11) - 5) / 18.0
    for idx in range(points):
        wave = math.sin((idx / max(points - 1, 1)) * math.pi * 2.2 + (seed % 7))
        subwave = math.cos((idx / max(points - 1, 1)) * math.pi * 4.6 + (seed % 5))
        value = baseline + amplitude * wave + (amplitude * 0.35) * subwave + idx * trend * amplitude
        if unit == "mbps":
            values.append(max(value / 1_000_000, 0.0))
        else:
            values.append(max(value, 0.0))
    return labels, values, unit


def y_label(unit: str) -> str:
    return {
        "%": "Utilization (%)",
        "mbps": "Megabits/sec",
        "requests": "Requests/sec",
    }.get(unit, "Value")


def main() -> int:
    args = parse_args()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    client = IHealthClient(browser="firefox")
    html = client.fetch_graphs_index_page(args.qkview_id)
    graph_names = parse_graph_names(html)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []
    for graph_name in graph_names:
        labels, values, unit = series_for_graph(graph_name)
        plt.figure(figsize=(10, 4.8))
        if values:
            plt.plot(labels, values, color="#005f73", linewidth=2.25)
            plt.fill_between(labels, values, color="#94d2bd", alpha=0.32)
            plt.scatter(labels, values, color="#0a9396", s=18, zorder=3)
        else:
            plt.text(0.5, 0.5, "No data available", ha="center", va="center", transform=plt.gca().transAxes, fontsize=16)
        plt.title(graph_name)
        plt.suptitle("Sample PNG styling preview - not live iHealth graph data", fontsize=9, y=0.98)
        plt.ylabel(y_label(unit))
        plt.xticks(rotation=35, ha="right")
        plt.grid(axis="y", alpha=0.22)
        plt.tight_layout()
        file_name = graph_name.lower().replace("/", "-").replace(" ", "-")
        file_name = "".join(ch for ch in file_name if ch.isalnum() or ch in {"-", "_", ".", "(" , ")"})
        target = output_dir / f"{file_name}.png"
        plt.savefig(target, dpi=160)
        plt.close()
        generated.append(target)

    contact_sheet = output_dir / "contact-sheet.txt"
    contact_sheet.write_text("\n".join(path.name for path in generated), encoding="utf-8")
    print(f"Rendered {len(generated)} sample PNGs to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
