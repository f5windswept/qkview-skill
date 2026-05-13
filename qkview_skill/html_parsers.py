from __future__ import annotations

import html
import re
from typing import Any, Dict, Iterable, List, Tuple


SECTION_RE = re.compile(
    r'<div class="section">.*?<div class="hd">\s*<h3>(.*?)</h3>\s*</div>.*?<div class="bd">(.*?)</div>\s*</div>',
    re.S,
)
DT_DD_RE = re.compile(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
SEARCH_RESULT_RE = re.compile(
    r'qvStatuses\["(?P<qkview_id>\d+)"\]\s*=\s*\{.*?"name":"(?P<hostname>[^"]+)".*?"genDate":"(?P<gen_date>[^"]+)"\};',
    re.S,
)


def strip_html(value: str) -> str:
    cleaned = TAG_RE.sub(" ", value)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def parse_recent_row(row: List[Any]) -> Dict[str, str]:
    hostname_info = row[4] if len(row) > 4 and isinstance(row[4], dict) else {}
    return {
        "qkview_id": str(row[0]),
        "platform": str(row[3]),
        "hostname": str(hostname_info.get("hostname") or hostname_info.get("linkTitle") or "unknown-host"),
        "version": str(row[5]),
        "serial": str(row[6]).strip(),
        "generation_date": str(row[7]),
        "uploaded_file": strip_html(str(row[9])),
        "uploader_email": str(row[10]),
        "uploaded_at": str(row[11]),
        "last_viewed": str(row[12]),
    }


def parse_search_results(page_html: str, uploader_email: str) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for match in SEARCH_RESULT_RE.finditer(page_html):
        results.append(
            {
                "qkview_id": match.group("qkview_id"),
                "platform": "unknown-platform",
                "hostname": html.unescape(match.group("hostname")),
                "version": "unknown-version",
                "serial": "unknown-serial",
                "generation_date": match.group("gen_date"),
                "uploaded_file": "unknown",
                "uploader_email": uploader_email,
                "uploaded_at": "unknown",
                "last_viewed": "unknown",
            }
        )
    return results


def parse_sections(page_html: str) -> Dict[str, Dict[str, str]]:
    sections: Dict[str, Dict[str, str]] = {}
    for title, body in SECTION_RE.findall(page_html):
        rows: Dict[str, str] = {}
        for key, value in DT_DD_RE.findall(body):
            rows[strip_html(key)] = strip_html(value)
        if rows:
            sections[strip_html(title)] = rows
    return sections


def flatten_sections(sections: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    flattened: Dict[str, str] = {}
    for section_name, rows in sections.items():
        for key, value in rows.items():
            flattened[key] = value
            flattened[f"{section_name}.{key}"] = value
    return flattened


def parse_generation_date(page_html: str) -> str:
    match = re.search(r"<td class=\"header\">Generation Date</td>\s*<td class=\"value\">.*?</td>", page_html, re.S)
    return strip_html(match.group(0).split("</td>", 1)[-1]) if match else ""


def parse_impersonation_user(page_html: str) -> str:
    match = re.search(r"Impersonating\s+([^<]+)</span>", page_html)
    return strip_html(match.group(1)) if match else ""


def parse_diagnostic_counts(page_html: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for severity, count in re.findall(r"<label for=\"filter-item-[^\"]+\">\s*(Critical|High|Medium|Low)\s*\((\d+)\)", page_html, re.I):
        counts[severity.lower()] = int(count)
    return counts


def parse_graph_names(page_html: str) -> List[str]:
    return [html.unescape(name).strip() for name in re.findall(r'<input id="([^"]+)" name="[^"]+" type="checkbox"', page_html)]


def build_graph_selection_token(all_graphs: Iterable[str], selected_graphs: Iterable[str]) -> str:
    selected = {graph.strip() for graph in selected_graphs}
    return "".join("1" if graph in selected else "0" for graph in all_graphs)


def parse_graph_download_href(page_html: str, qkview_id: str) -> str:
    match = re.search(
        rf"href=['\"](/qkview-analyzer/qv/{re.escape(qkview_id)}/graphs/download/[^'\"]+)['\"][^>]*>Download All Graphs",
        page_html,
    )
    return html.unescape(match.group(1)) if match else ""


def parse_provisioning_rows(page_html: str) -> List[Dict[str, str]]:
    section_match = re.search(
        r"<h3>Licensing and Provisioning.*?</h3>\s*</div>\s*<div class=\"bd\">\s*<table.*?>(.*?)</table>",
        page_html,
        re.S,
    )
    if not section_match:
        return []

    table_html = section_match.group(1)
    row_htmls = re.findall(r"<tr>(.*?)</tr>", table_html, re.S)
    if not row_htmls:
        return []

    headers = [strip_html(cell).lower() for cell in re.findall(r"<t[hd].*?>(.*?)</t[hd]>", row_htmls[0], re.S)]
    if not headers:
        return []

    module_index = next((index for index, header in enumerate(headers) if "module" in header), None)
    provisioned_index = next((index for index, header in enumerate(headers) if "provisioned" in header), None)
    if module_index is None or provisioned_index is None:
        return []

    rows: List[Dict[str, str]] = []
    for row_html in row_htmls[1:]:
        cells = [strip_html(cell) for cell in re.findall(r"<td.*?>(.*?)</td>", row_html, re.S)]
        if len(cells) <= max(module_index, provisioned_index):
            continue
        module_name = cells[module_index]
        provisioned = cells[provisioned_index]
        if not module_name or module_name.lower().startswith("module / feature"):
            continue
        rows.append({"module": module_name, "provisioned": provisioned})
    return rows


def parse_provisioned_modules(page_html: str) -> List[str]:
    modules: List[str] = []
    for row in parse_provisioning_rows(page_html):
        provisioned = row.get("provisioned", "")
        if provisioned.lower() == "nominal":
            modules.append(row["module"])
    return modules


def parse_high_availability_summary(page_html: str) -> Dict[str, str]:
    summary: Dict[str, str] = {}
    traffic_groups = _parse_section_table(page_html, "Traffic Group Summary")
    if traffic_groups:
        first_group = traffic_groups[0]
        summary["Traffic Group"] = first_group.get("Traffic Group", "")
        summary["Traffic Group State"] = first_group.get("State", "")
        summary["Traffic Group Status"] = first_group.get("Status", "")
        summary["Traffic Group Times Active"] = first_group.get("Times Active", "")

    device_groups = _parse_section_table(page_html, "Device Groups Summary")
    for row in device_groups:
        if row.get("Device Group Type", "").lower() != "sync-failover":
            continue
        summary["Device Group Name"] = row.get("Name", "")
        summary["Device Group Type"] = row.get("Device Group Type", "")
        summary["Device Count"] = row.get("Number of Devices", "")
        break
    return summary


def parse_title(page_html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", page_html, re.I | re.S)
    return strip_html(match.group(1)) if match else ""


def parse_load_averages(flattened: Dict[str, str]) -> Tuple[float, float, float]:
    raw = flattened.get("Load Average", "")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    values: List[float] = []
    for part in parts[:3]:
        try:
            values.append(float(part))
        except ValueError:
            values.append(0.0)
    while len(values) < 3:
        values.append(0.0)
    return values[0], values[1], values[2]


def _parse_section_table(page_html: str, title: str) -> List[Dict[str, str]]:
    section_match = re.search(
        rf"<h3>{re.escape(title)}.*?</h3>\s*</div>\s*<div class=\"bd\">\s*<table.*?>(.*?)</table>",
        page_html,
        re.I | re.S,
    )
    if not section_match:
        return []

    table_html = section_match.group(1)
    row_htmls = re.findall(r"<tr.*?>(.*?)</tr>", table_html, re.S)
    if len(row_htmls) < 2:
        return []

    headers = [strip_html(cell) for cell in re.findall(r"<th.*?>(.*?)</th>", row_htmls[0], re.S)]
    if not headers:
        return []

    rows: List[Dict[str, str]] = []
    for row_html in row_htmls[1:]:
        cells = [strip_html(cell) for cell in re.findall(r"<td.*?>(.*?)</td>", row_html, re.S)]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows
