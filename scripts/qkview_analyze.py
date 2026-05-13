#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qkview_skill.analyzer import analyze_bundle, render_report
from qkview_skill.ihealth_client import IHealthClient, IHealthClientError
from qkview_skill.models import QKViewBundle


def load_fixture(path: str) -> QKViewBundle:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return QKViewBundle(
        qkview_id=str(payload.get("qkview_id") or payload.get("id") or "fixture-qkview"),
        summary=payload.get("summary", {}),
        diagnostics=payload.get("diagnostics", []),
        metrics=payload.get("metrics", {}),
        metadata=payload.get("metadata", {}),
    )


def choose_qkview(matches: list[dict], requested_hostname: str | None) -> str:
    if len(matches) == 1:
        return str(matches[0].get("id") or matches[0].get("qkview_id"))
    if requested_hostname:
        for match in matches:
            hostname = str(match.get("hostname") or match.get("device_name") or "")
            if hostname.lower() == requested_hostname.lower():
                return str(match.get("id") or match.get("qkview_id"))
    newest = sorted(
        matches,
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or item.get("id") or ""),
        reverse=True,
    )[0]
    return str(newest.get("id") or newest.get("qkview_id"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze BIG-IP QKViews from iHealth")
    parser.add_argument("--fixture", help="Path to a saved bundle JSON file for offline testing")
    parser.add_argument("--qkview-id", help="Exact iHealth QKView ID")
    parser.add_argument("--customer-email", help="Uploader email to search by")
    parser.add_argument("--hostname", help="Hostname to narrow search results")
    parser.add_argument("--dump-dir", help="Directory to save live bundle JSON for debugging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.fixture:
            bundle = load_fixture(args.fixture)
        else:
            client = IHealthClient()
            matches = client.search_qkviews(email=args.customer_email, hostname=args.hostname, qkview_id=args.qkview_id)
            qkview_id = args.qkview_id or choose_qkview(matches, args.hostname)
            bundle = client.fetch_bundle(qkview_id)
            if args.dump_dir:
                client.dump_bundle(bundle, args.dump_dir)
        report = analyze_bundle(bundle)
        print(render_report(report))
        return 0
    except (IHealthClientError, FileNotFoundError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
