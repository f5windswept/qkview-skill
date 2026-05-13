#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qkview_skill.html_parsers import parse_graph_names
from qkview_skill.ihealth_client import IHealthClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download all available iHealth graph PNGs for a QKView")
    parser.add_argument("--qkview-id", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = IHealthClient(browser="firefox")
    index_html = client.fetch_graphs_index_page(args.qkview_id)
    graph_names = parse_graph_names(index_html)
    selected_page = client.fetch_selected_graphs_page(args.qkview_id, graph_names)
    archives = client.download_graph_bundle(args.qkview_id, selected_page, args.output_dir)

    extracted = []
    for archive in archives:
        extracted.extend(client.extract_graph_bundle(archive, args.output_dir))

    print(f"Downloaded {len(extracted)} PNGs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
