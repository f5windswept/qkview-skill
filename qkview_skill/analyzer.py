from __future__ import annotations

from typing import Any, Dict, Iterable, List

from qkview_skill.models import AnalysisReport, QKViewBundle, optional_float


SEVERITY_ORDER = {"critical": 0, "high": 1, "warning": 2, "medium": 2, "low": 3, "info": 4}


def _lookup(data: Dict[str, Any], *keys: str) -> Any:
    lowered = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _metric_value(metrics: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metrics:
            return metrics[key]
    lowered = {str(key).lower(): value for key, value in metrics.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def analyze_bundle(bundle: QKViewBundle) -> AnalysisReport:
    summary = bundle.summary or {}
    metrics = bundle.metrics or {}
    diagnostics = sorted(bundle.diagnostics or [], key=lambda item: SEVERITY_ORDER.get(str(item.get("severity", "info")).lower(), 99))

    hostname = _lookup(summary, "hostname", "host_name", "device_name") or bundle.metadata.get("hostname") or "unknown-host"
    version = _lookup(summary, "tmos_version", "version", "software_version") or "unknown-version"
    platform = _lookup(summary, "platform", "appliance", "model") or "unknown-platform"
    sync_status = _lookup(summary, "sync_status", "config_sync_status", "ha_sync")
    failover_status = _lookup(summary, "failover_status", "ha_status")
    uptime = _lookup(summary, "uptime", "system_uptime")

    scope = [
        f"QKView `{bundle.qkview_id}` for `{hostname}` on `{platform}`",
        f"TMOS `{version}`" + (f", uptime `{uptime}`" if uptime else ""),
    ]

    executive: List[str] = []
    critical_findings: List[str] = []
    warnings: List[str] = []
    performance_notes: List[str] = []
    configuration_notes: List[str] = []
    actions: List[str] = []

    critical_diags = [item for item in diagnostics if str(item.get("severity", "")).lower() in {"critical", "high"}]
    warning_diags = [item for item in diagnostics if str(item.get("severity", "")).lower() in {"warning", "medium"}]

    if critical_diags:
        executive.append(f"iHealth reports `{len(critical_diags)}` critical or high-severity findings that need prompt review.")
    elif warning_diags:
        executive.append(f"No critical iHealth findings are present, but `{len(warning_diags)}` warnings merit follow-up.")
    else:
        executive.append("No major iHealth findings were present in the supplied data.")

    for item in critical_diags[:5]:
        title = item.get("title") or item.get("name") or "Untitled finding"
        detail = item.get("detail") or item.get("summary") or item.get("description") or ""
        critical_findings.append(f"{title}: {detail}".strip())
        recommendation = item.get("recommendation") or item.get("action")
        if recommendation:
            actions.append(str(recommendation))

    for item in warning_diags[:5]:
        title = item.get("title") or item.get("name") or "Untitled finding"
        detail = item.get("detail") or item.get("summary") or item.get("description") or ""
        warnings.append(f"{title}: {detail}".strip())

    cpu = optional_float(_metric_value(metrics, "cpu_utilization", "system_cpu_usage", "cpu"))
    memory = optional_float(_metric_value(metrics, "memory_used", "memory_utilization", "memory"))
    swap = optional_float(_metric_value(metrics, "swap_usage", "swap"))
    throughput = optional_float(_metric_value(metrics, "throughput_bits", "throughput_bps"))
    ssl_tps = optional_float(_metric_value(metrics, "ssl_tps", "ssl_transactions"))
    connections = optional_float(_metric_value(metrics, "active_connections", "connections"))
    http_rps = optional_float(_metric_value(metrics, "http_requests", "http_rps"))

    if cpu is not None:
        if cpu >= 90:
            performance_notes.append(f"CPU utilization is critically high at `{cpu:.1f}%`.")
            actions.append("Investigate the busiest virtual servers, pools, and TMM workers behind the high CPU load.")
        elif cpu >= 75:
            performance_notes.append(f"CPU utilization is elevated at `{cpu:.1f}%`.")
        else:
            performance_notes.append(f"CPU utilization is `{cpu:.1f}%`, which is not currently alarming.")

    if memory is not None:
        if memory >= 90:
            performance_notes.append(f"Memory utilization is critically high at `{memory:.1f}%`.")
            actions.append("Review TMM memory consumers and provisioning levels before memory pressure worsens.")
        elif memory >= 80:
            performance_notes.append(f"Memory utilization is elevated at `{memory:.1f}%`.")
        else:
            performance_notes.append(f"Memory utilization is `{memory:.1f}%`.")

    if swap is not None and swap > 0:
        performance_notes.append(f"Swap usage is present at `{swap:.1f}%`, which can indicate memory pressure.")

    if throughput is not None:
        performance_notes.append(f"Observed throughput is approximately `{throughput:.0f}` bits/sec.")
    if ssl_tps is not None:
        performance_notes.append(f"Observed SSL transaction rate is `{ssl_tps:.0f}` TPS.")
    if connections is not None:
        performance_notes.append(f"Active connections are around `{connections:.0f}`.")
    if http_rps is not None:
        performance_notes.append(f"HTTP request rate is around `{http_rps:.0f}` requests/sec.")

    version_text = str(version)
    if any(token in version_text for token in ("11.", "12.", "13.", "14.")):
        configuration_notes.append(f"TMOS version `{version_text}` may be old enough to require lifecycle and hotfix review.")
        actions.append("Confirm support status and hotfix level for the running TMOS version.")

    if sync_status:
        configuration_notes.append(f"Config-sync status is `{sync_status}`.")
        if str(sync_status).lower() not in {"in sync", "insync", "green", "synchronized"}:
            actions.append("Validate config-sync health and resolve HA drift before the next failover event.")

    if failover_status:
        configuration_notes.append(f"Failover status is `{failover_status}`.")

    certificates = _as_list(_lookup(summary, "expired_certificates", "certificate_alerts"))
    for certificate in certificates[:3]:
        configuration_notes.append(f"Certificate issue noted: `{certificate}`.")
        actions.append("Review certificate expiration and replacement timelines for the affected objects.")

    disks = optional_float(_lookup(summary, "disk_usage", "disk_percent_used"))
    if disks is not None:
        if disks >= 90:
            critical_findings.append(f"Disk usage is critically high at `{disks:.1f}%`.")
            actions.append("Clean up old logs, UCS files, and core files to recover disk space.")
        elif disks >= 80:
            warnings.append(f"Disk usage is elevated at `{disks:.1f}%`.")

    if not critical_findings and critical_diags:
        critical_findings = [str(item.get("title") or item.get("name") or "Critical iHealth finding") for item in critical_diags[:3]]

    if not warnings and warning_diags:
        warnings = [str(item.get("title") or item.get("name") or "iHealth warning") for item in warning_diags[:3]]

    if not actions:
        actions.append("Validate the major iHealth findings with the customer and compare them against the reported incident window.")
    actions.append("Preserve the QKView ID and timestamp used for this analysis so follow-up comparisons use the same baseline.")

    if cpu is not None or memory is not None:
        executive.append("Performance data was available and included in the assessment.")
    else:
        executive.append("This report is based mostly on summary and diagnostic data; performance telemetry was limited.")

    if sync_status or failover_status:
        executive.append("HA state was reviewed as part of the analysis.")

    return AnalysisReport(
        scope=scope,
        executive_assessment=executive[:4],
        critical_findings=critical_findings[:6],
        warnings=warnings[:6],
        performance_notes=performance_notes[:8],
        configuration_notes=configuration_notes[:8],
        recommended_next_actions=_dedupe(actions)[:6],
    )


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def render_report(report: AnalysisReport) -> str:
    sections = [
        ("Scope", report.scope),
        ("Executive Assessment", report.executive_assessment),
        ("Critical Findings", report.critical_findings),
        ("Warnings", report.warnings),
        ("Performance Notes", report.performance_notes),
        ("Configuration Notes", report.configuration_notes),
    ]
    lines = ["BIG-IP QKView Analysis", ""]
    for title, items in sections:
        lines.append(title)
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("- None noted.")
        lines.append("")
    lines.append("Recommended Next Actions")
    for index, item in enumerate(report.recommended_next_actions, start=1):
        lines.append(f"{index}. {item}")
    return "\n".join(lines)
