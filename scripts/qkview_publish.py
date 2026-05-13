#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qkview_skill.html_parsers import flatten_sections, parse_diagnostic_counts, parse_graph_names, parse_high_availability_summary, parse_provisioned_modules, parse_sections
from qkview_skill.ihealth_client import IHealthClient, IHealthClientError
from qkview_skill.live_analysis import build_live_report, fallback_graph_metrics
from qkview_skill.notion_client import NotionClient, blocks_from_report, bulleted_section, heading_1, heading_2, paragraph


GRAPH_GROUPS_REDUCED = [
    ("System Health", [
        ("CPU_2592000.png", "System CPU Usage"),
        ("memory_2592000.png", "Memory Used"),
        ("memorybreakdown_2592000.png", "Memory Breakdown"),
        ("blade0cpucores_2592000.png", "Blade 0 CPU Usage By Core"),
        ("ramcache_2592000.png", "RAM Cache Utilization"),
    ]),
    ("Traffic And Connections", [
        ("activecons_2592000.png", "Active Connections"),
        ("newcons_2592000.png", "Total New Connections"),
        ("throughput_2592000.png", "Throughput (bits)"),
        ("throughputpkts_2592000.png", "Throughput (packets)"),
        ("detailthroughput1_2592000.png", "TMM Client-side Throughput"),
        ("detailthroughput2_2592000.png", "TMM Server-side Throughput"),
    ]),
    ("Application Layer", [
        ("httprequests_2592000.png", "HTTP Requests"),
        ("SSLTPSGraph_2592000.png", "SSL Transactions"),
    ]),
]


class HostSummary(TypedDict):
    hostname: str
    qkview_id: str
    diagnostic_counts: dict[str, int]
    load_average: str
    load_1: float
    ioc_flag: bool
    certificate_flag: bool


REQUIRED_SYSTEM_KEYS = [
    "System.Hostname",
    "System.Time Zone",
    "System.Appliance S/N",
    "System.Status",
    "System.Uptime",
    "System.Load Average",
    "System.Physical Memory",
    "System.CPU Totals",
]

SHARED_LB_COOKIES: list[str] = []

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze iHealth QKViews and append report content to a Notion page")
    parser.add_argument("--customer-email", required=True)
    parser.add_argument("--notion-page", required=True, help="Notion page ID or full Notion page URL")
    parser.add_argument("--qkview-id", action="append", default=[], help="Explicit QKView ID to analyze; repeat for multiple IDs")
    parser.add_argument("--graph-set", choices=["full", "reduced"], default="full")
    parser.add_argument("--use-cached-graphs-only", action="store_true", help="Skip live graph retrieval and upload cached native iHealth PNGs only")
    parser.add_argument("--skip-notion", action="store_true", help="Generate local artifacts only and do not upload anything to Notion")
    parser.add_argument("--artifacts-dir", default=str(PROJECT_ROOT / "artifacts"))
    return parser.parse_args()


def notion_page_id(value: str) -> str:
    match = re.search(r"([0-9a-fA-F]{32})", value)
    return match.group(1) if match else value


def render_fallback_charts(output_dir: Path, hostname: str, metrics: dict) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    diagnostic_path = output_dir / f"{hostname}-diagnostic-counts.png"
    plt.figure(figsize=(8, 4.5))
    labels = ["Critical", "High", "Medium", "Low"]
    values = metrics.get("Diagnostic Counts", [0, 0, 0, 0])
    plt.bar(labels, values, color=["#a11d33", "#d8572a", "#f0ad4e", "#5b8def"])
    plt.title(f"{hostname} - Diagnostic Counts")
    plt.ylabel("Findings")
    plt.tight_layout()
    plt.savefig(diagnostic_path, dpi=160)
    plt.close()
    generated.append(str(diagnostic_path))

    load_path = output_dir / f"{hostname}-load-average.png"
    plt.figure(figsize=(8, 4.5))
    labels = ["1 min", "5 min", "15 min"]
    values = metrics.get("Load Average", [0.0, 0.0, 0.0])
    plt.plot(labels, values, marker="o", linewidth=2, color="#005f73")
    plt.fill_between(labels, values, color="#94d2bd", alpha=0.35)
    plt.title(f"{hostname} - Load Average Snapshot")
    plt.ylabel("Load")
    plt.tight_layout()
    plt.savefig(load_path, dpi=160)
    plt.close()
    generated.append(str(load_path))
    return generated


def cached_ihealth_pngs(qkview_id: str, artifacts_dir: Path) -> list[str]:
    cache_dir = artifacts_dir / "ihealth-pngs" / qkview_id
    if not cache_dir.exists():
        return []
    return [str(path) for path in sorted(cache_dir.glob("*.png"))]


def graph_groups_for_paths(image_paths: list[str], graph_set: str) -> list[tuple[str, list[tuple[str, str]]]]:
    by_name = {Path(path).name: path for path in image_paths}
    if graph_set == "reduced":
        groups = []
        for title, items in GRAPH_GROUPS_REDUCED:
            group_items = []
            for file_name, caption in items:
                path = by_name.get(file_name)
                if path:
                    group_items.append((path, caption))
            if group_items:
                groups.append((title, group_items))
        return groups

    full_items = [(path, pretty_graph_caption(Path(path).name)) for path in image_paths]
    return [("All Available Graphs", full_items)]


def pretty_graph_caption(file_name: str) -> str:
    mapping = {
        "CPU_2592000.png": "System CPU Usage",
        "memory_2592000.png": "Memory Used",
        "memorybreakdown_2592000.png": "Memory Breakdown",
        "blade0cpucores_2592000.png": "Blade 0 CPU Usage By Core",
        "ramcache_2592000.png": "RAM Cache Utilization",
        "activecons_2592000.png": "Active Connections",
        "newcons_2592000.png": "Total New Connections",
        "throughput_2592000.png": "Throughput (bits)",
        "throughputpkts_2592000.png": "Throughput (packets)",
        "detailthroughput1_2592000.png": "TMM Client-side Throughput",
        "detailthroughput2_2592000.png": "TMM Server-side Throughput",
        "httprequests_2592000.png": "HTTP Requests",
        "SSLTPSGraph_2592000.png": "SSL Transactions",
        "access_requests_2592000.png": "Access Requests",
    }
    if file_name in mapping:
        return mapping[file_name]
    stem = file_name.rsplit("_2592000", 1)[0].replace("_", " ")
    return stem.strip()


def extract_status_value(page_html: str, label: str) -> str:
    match = re.search(
        rf'<td class="header">\s*{re.escape(label)}\s*</td>\s*<td class="value">(.*?)</td>',
        page_html,
        re.I | re.S,
    )
    return _strip_html(match.group(1)) if match else ""


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _first_value(*values: str | None) -> str:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized and normalized != "[No data available]":
            return normalized
    return ""


def render_markdown_report(report: dict[str, list[str]]) -> str:
    sections = [
        ("Scope", report["scope"]),
        ("System Summary", report.get("system_summary", [])),
        ("Executive Assessment", report["executive_assessment"]),
        ("Critical Findings", report["critical_findings"] or ["None noted."]),
        ("Warnings", report["warnings"] or ["None noted."]),
        ("Performance Notes", report["performance_notes"]),
        ("Configuration Notes", report["configuration_notes"]),
        ("Virtual Edition Recommendation", report.get("ve_recommendation", [])),
    ]
    lines = ["BIG-IP QKView Analysis", ""]
    for title, items in sections:
        lines.append(title)
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("Recommended Next Actions")
    for index, item in enumerate(report["recommended_next_actions"], start=1):
        lines.append(f"{index}. {item}")
    return "\n".join(lines) + "\n"


def build_estate_summary(records: list[HostSummary]) -> list[str]:
    total_hosts = len(records)
    total_high = sum(int(record["diagnostic_counts"].get("high", 0)) for record in records)
    total_medium = sum(int(record["diagnostic_counts"].get("medium", 0)) for record in records)
    total_low = sum(int(record["diagnostic_counts"].get("low", 0)) for record in records)
    highest_load = max(records, key=lambda record: float(record["load_1"])) if records else None
    summary = [
        f"Reviewed `{total_hosts}` BIG-IP hosts with `{total_high}` high, `{total_medium}` medium, and `{total_low}` low iHealth findings in aggregate.",
        "All per-host reports were generated only after required Status->Overview System fields, diagnostics counts, and provisioned modules were validated.",
    ]
    if highest_load is not None:
        summary.append(
            f"The highest snapshot load average in this set was on `{highest_load['hostname']}` at `{highest_load['load_average']}`.")
    return summary


def upload_file_with_retry(notion: NotionClient, file_path: str, attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return notion.upload_file(file_path)
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(attempt)
    raise RuntimeError(f"Unable to upload {file_path} to Notion after {attempts} attempts: {last_error}")


def validate_required_host_data(record: dict[str, str], overview_sections: dict[str, str], diagnostic_counts: dict[str, int]) -> None:
    missing = [key for key in REQUIRED_SYSTEM_KEYS if not _first_value(overview_sections.get(key))]
    if missing:
        raise RuntimeError(f"{record['qkview_id']} is missing required Status->Overview System fields: {', '.join(missing)}")
    if not _first_value(overview_sections.get("Licensing and Provisioning.Provisioned Modules")):
        raise RuntimeError(f"{record['qkview_id']} is missing Licensing and Provisioning provisioned modules")
    if not _first_value(record.get("hostname")) or str(record.get("hostname", "")).startswith("qkview-"):
        raise RuntimeError(f"{record['qkview_id']} did not resolve a valid BIG-IP hostname")
    if not _first_value(record.get("platform")) or record.get("platform") == "unknown-platform":
        raise RuntimeError(f"{record['qkview_id']} did not resolve a valid platform")
    if not _first_value(record.get("version")) or record.get("version") == "unknown-version":
        raise RuntimeError(f"{record['qkview_id']} did not resolve a valid software version")
    if not diagnostic_counts:
        raise RuntimeError(f"{record['qkview_id']} diagnostics were not parsed successfully")


def build_ihealth_client() -> IHealthClient:
    client = IHealthClient(browser="firefox")
    if SHARED_LB_COOKIES:
        client.cookie_header = f"{client.cookie_header}; {'; '.join(SHARED_LB_COOKIES)}"
    return client


def add_lb_cookies(client: IHealthClient) -> None:
    for key, value in getattr(client, "_last_response_headers", []):
        if key.lower() != "set-cookie":
            continue
        cookie = value.split(";", 1)[0]
        if cookie.startswith("BIGip") and cookie not in SHARED_LB_COOKIES:
            SHARED_LB_COOKIES.append(cookie)


def warm_ihealth_client(client: IHealthClient, email: str) -> None:
    warmers = [
        lambda: client._request_with_referers(
            f"/qkview-analyzer/?query={urllib.parse.quote(email)}&showAll=true&offset=0",
            [None, f"{client.base_url}/qkview-analyzer/recent"],
            kind="html",
        ),
        client.list_recent_qkviews,
    ]
    for warmer in warmers:
        try:
            warmer()
            add_lb_cookies(client)
            if SHARED_LB_COOKIES:
                client.cookie_header = f"{client.cookie_header}; {'; '.join(SHARED_LB_COOKIES)}"
            return
        except Exception:
            continue


def fetch_host_pages(email: str, qkview_id: str) -> tuple[str, str, str, str, list[str]]:
    last_error: Exception | None = None
    for attempt in range(1, 7):
        client = build_ihealth_client()
        warm_ihealth_client(client, email)
        try:
            overview_html = client.fetch_overview_page(qkview_id)
            add_lb_cookies(client)
            if SHARED_LB_COOKIES:
                client.cookie_header = f"{client.cookie_header}; {'; '.join(SHARED_LB_COOKIES)}"
            hardware_html = client.fetch_hardware_page(qkview_id)
            add_lb_cookies(client)
            if SHARED_LB_COOKIES:
                client.cookie_header = f"{client.cookie_header}; {'; '.join(SHARED_LB_COOKIES)}"
            diagnostics_html = client.fetch_diagnostics_page(qkview_id)
            add_lb_cookies(client)
            high_availability_warning: list[str] = []
            try:
                if SHARED_LB_COOKIES:
                    client.cookie_header = f"{client.cookie_header}; {'; '.join(SHARED_LB_COOKIES)}"
                high_availability_html = client.fetch_high_availability_page(qkview_id)
                add_lb_cookies(client)
            except Exception as exc:
                high_availability_html = ""
                high_availability_warning.append(f"Status -> High Availability was not retrievable during this run: {exc}")
            return overview_html, hardware_html, diagnostics_html, high_availability_html, high_availability_warning
        except Exception as exc:
            last_error = exc
            time.sleep(attempt)
    raise RuntimeError(f"Unable to fetch required iHealth pages for {qkview_id}: {last_error}")


def main() -> int:
    args = parse_args()
    page_id = notion_page_id(args.notion_page)
    artifacts_dir = Path(args.artifacts_dir)
    run_dir = artifacts_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    client = build_ihealth_client()
    warm_ihealth_client(client, args.customer_email)
    notion = NotionClient() if not args.skip_notion else None
    search_records = {}
    try:
        for item in client.search_qkviews(email=args.customer_email):
            search_records[item["qkview_id"]] = item
    except Exception:
        search_records = {}

    if args.qkview_id:
        records = []
        for qkview_id in args.qkview_id:
            record = search_records.get(qkview_id, {}).copy()
            if not record:
                record = {
                    "qkview_id": qkview_id,
                    "hostname": f"qkview-{qkview_id}",
                    "platform": "unknown-platform",
                    "version": "unknown-version",
                    "serial": "unknown-serial",
                    "generation_date": "unknown",
                    "uploaded_file": "unknown",
                    "uploader_email": args.customer_email,
                    "uploaded_at": "unknown",
                    "last_viewed": "unknown",
                }
            records.append(record)
    else:
        records = list(search_records.values())

    if not records:
        raise SystemExit(
            f"No QKViews were discovered for {args.customer_email}. Refresh the iHealth search results page in Firefox and try again, or pass explicit --qkview-id values."
        )

    children = [
        heading_1(f"BIG-IP QKView Analysis - {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}") ,
        paragraph(f"Uploader email: {args.customer_email}. This report was appended by automation and preserves all existing Notion content above."),
    ]
    host_summaries: list[HostSummary] = []
    report_index_rows: list[str] = []

    for record in records:
        qkview_id = record["qkview_id"]
        host_dir = run_dir / record["hostname"].replace("/", "-")
        host_dir.mkdir(parents=True, exist_ok=True)
        print(f"Processing {qkview_id}...", flush=True)

        fetch_warnings: list[str] = []
        provisioned_modules: list[str] = []

        try:
            overview_html, hardware_html, diagnostics_html, high_availability_html, high_availability_warnings = fetch_host_pages(args.customer_email, qkview_id)
            overview_sections = flatten_sections(parse_sections(overview_html))
            provisioned_modules = parse_provisioned_modules(overview_html)
            if provisioned_modules:
                overview_sections["Provisioned Modules"] = ", ".join(provisioned_modules)
                overview_sections["Licensing and Provisioning.Provisioned Modules"] = ", ".join(provisioned_modules)
            hardware_sections = flatten_sections(parse_sections(hardware_html))
            high_availability_sections = parse_high_availability_summary(high_availability_html) if high_availability_html else {}
            diagnostic_counts = parse_diagnostic_counts(diagnostics_html)
            fetch_warnings.extend(high_availability_warnings)
        except Exception as exc:
            overview_html = ""
            overview_sections = {}
            hardware_sections = {}
            high_availability_sections = {}
            diagnostics_html = ""
            diagnostic_counts = {}
            fetch_warnings.append(f"Required iHealth pages were not retrievable during this run: {exc}")

        record["hostname"] = _first_value(
            overview_sections.get("System.Hostname"),
            overview_sections.get("Hostname"),
            record["hostname"],
        )
        record["version"] = _first_value(
            extract_status_value(overview_html, "Version - Edition"),
            record["version"],
        )
        if not record["version"] or record["version"] == "unknown-version":
            software_product = overview_sections.get("Software.Product", "BIG-IP")
            software_build = overview_sections.get("Software.Build")
            if software_build:
                record["version"] = f"{software_product} build {software_build}"
        record["platform"] = _first_value(
            extract_status_value(overview_html, "Platform"),
            record["platform"],
        )
        record["serial"] = _first_value(
            overview_sections.get("System.Appliance S/N"),
            hardware_sections.get("Appliance S/N"),
            record["serial"],
        )
        validate_required_host_data(record, overview_sections, diagnostic_counts)

        report = build_live_report(
            record,
            overview_sections,
            hardware_sections,
            diagnostic_counts,
            high_availability=high_availability_sections,
            provisioned_modules=provisioned_modules,
        )
        report["warnings"].extend(fetch_warnings)
        host_dir = run_dir / str(record["hostname"]).replace("/", "-")
        host_dir.mkdir(parents=True, exist_ok=True)
        image_paths: list[str] = []

        if args.use_cached_graphs_only:
            image_paths = cached_ihealth_pngs(qkview_id, artifacts_dir)
            if image_paths:
                report["warnings"].append("This publish run used cached native iHealth PNGs instead of retrieving fresh graph bundles from iHealth.")
            else:
                raise RuntimeError(f"{qkview_id} has no cached native iHealth PNGs available")
        else:
            try:
                graph_names = parse_graph_names(client.fetch_graphs_index_page(qkview_id))
                selected_graphs_page = client.fetch_selected_graphs_page(qkview_id, graph_names)
                archives = client.download_graph_bundle(qkview_id, selected_graphs_page, str(host_dir))
                for archive_path in archives:
                    image_paths.extend(client.extract_graph_bundle(archive_path, str(host_dir / "graphs")))
                if not image_paths:
                    raise IHealthClientError("No PNG graphs were extracted from the downloaded graph archive")
            except Exception:
                image_paths = cached_ihealth_pngs(qkview_id, artifacts_dir)
                if image_paths:
                    report["warnings"].append("Live iHealth graph retrieval was flaky during publish, so the report used cached native iHealth PNGs downloaded earlier in this session.")
                else:
                    fallback_images = render_fallback_charts(host_dir / "fallback-graphs", record["hostname"], fallback_graph_metrics(record, overview_sections, hardware_sections, diagnostic_counts))
                    image_paths.extend(fallback_images)
                    report["warnings"].append("Time-series iHealth utilization graphs were not retrievable in the current session, so fallback summary charts were generated instead.")

        graph_groups = graph_groups_for_paths(image_paths, args.graph_set)
        grouped_uploads = []
        if notion is not None:
            upload_items = []
            for _, items in graph_groups:
                for path, caption in items:
                    upload_items.append((upload_file_with_retry(notion, path), caption))
            upload_lookup = {caption: upload_id for upload_id, caption in upload_items}
            for group_title, items in graph_groups:
                grouped_uploads.append((group_title, [(upload_lookup[caption], caption) for _, caption in items]))
        children.extend(blocks_from_report(record["hostname"], report, [], grouped_uploads))

        report_path = host_dir / "report.md"
        report_path.write_text(render_markdown_report(report), encoding="utf-8")
        metadata = {
            "qkview_id": qkview_id,
            "hostname": record["hostname"],
            "platform": record["platform"],
            "version": record["version"],
            "serial": record["serial"],
            "provisioned_modules": provisioned_modules,
            "high_availability": high_availability_sections,
            "diagnostic_counts": diagnostic_counts,
            "graph_count": len(image_paths),
            "graph_source": "cached" if args.use_cached_graphs_only else "live_or_cached_fallback",
        }
        (host_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        host_summary: HostSummary = {
            "hostname": str(record["hostname"]),
            "qkview_id": str(qkview_id),
            "diagnostic_counts": diagnostic_counts,
            "load_average": overview_sections.get("System.Load Average") or overview_sections.get("Load Average") or "unknown",
            "load_1": float((overview_sections.get("System.Load Average") or overview_sections.get("Load Average") or "0").split(",", 1)[0].strip() or 0),
            "ioc_flag": any("Indicators of Compromise" in item for item in report["critical_findings"] + report["warnings"]),
            "certificate_flag": any("certificate" in item.lower() for item in report["warnings"] + report["configuration_notes"]),
        }
        host_summaries.append(host_summary)
        report_index_rows.append(f"- `{record['hostname']}` / QKView `{qkview_id}` -> `{report_path}`")
        print(f"Completed {record['hostname']} ({qkview_id}) with {len(image_paths)} graph(s)", flush=True)

    if host_summaries:
        children[2:2] = [
            heading_2("Executive Summary"),
            bulleted_section("Estate Summary", build_estate_summary(host_summaries)),
        ]

    summary_path = run_dir / "index.md"
    summary_lines = [
        "# BIG-IP QKView Publish Run",
        "",
        f"- Generated: `{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Uploader email: `{args.customer_email}`",
        f"- Notion page: `{page_id}`",
        f"- Graph mode: `{args.graph_set}`",
        f"- Cached graphs only: `{str(args.use_cached_graphs_only).lower()}`",
        "",
        "## Reports",
        *report_index_rows,
    ]
    if host_summaries:
        summary_lines.extend(["", "## Executive Summary"])
        summary_lines.extend(f"- {item}" for item in build_estate_summary(host_summaries))
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    if notion is not None:
        notion.append_children(page_id, children)
        print(f"Appended QKView analysis for {len(records)} host(s) to Notion page {page_id}")
    else:
        print(f"Generated QKView analysis for {len(records)} host(s) without uploading to Notion")
    print(f"Run artifacts saved to {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
