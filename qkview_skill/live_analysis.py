from __future__ import annotations

import re
from typing import Dict, List

from qkview_skill.html_parsers import parse_load_averages


def build_live_report(
    record: Dict[str, str],
    overview: Dict[str, str],
    hardware: Dict[str, str],
    diagnostics: Dict[str, int],
    high_availability: Dict[str, str] | None = None,
    provisioned_modules: List[str] | None = None,
) -> Dict[str, List[str]]:
    high_availability = high_availability or {}
    provisioned_modules = provisioned_modules or []
    load_1, load_5, load_15 = parse_load_averages(overview)
    hostname = _first_value(overview.get("System.Hostname"), overview.get("Hostname"), record.get("hostname"), "unknown-host")
    physical_memory = _first_value(
        overview.get("System.Physical Memory"),
        hardware.get("Physical Memory"),
        overview.get("Physical Memory"),
        "unknown",
    )
    cpu_totals = _first_value(
        overview.get("System.CPU Totals"),
        hardware.get("CPU Totals"),
        overview.get("CPU Totals"),
        "unknown",
    )
    status = _first_value(overview.get("System.Status"), hardware.get("Status"), overview.get("Status"), "unknown")
    uptime = _first_value(overview.get("System.Uptime"), hardware.get("Uptime"), overview.get("Uptime"), "unknown")
    time_zone = _first_value(overview.get("System.Time Zone"), overview.get("Time Zone"), "unknown")
    appliance_serial = _first_value(
        overview.get("System.Appliance S/N"),
        hardware.get("Appliance S/N"),
        overview.get("Appliance S/N"),
        record.get("serial"),
        "unknown",
    )
    blade_serial = _first_value(overview.get("System.Blade S/N"), overview.get("Blade S/N"), "unknown")
    virtuals = _first_value(overview.get("Configuration Totals.Virtuals"), overview.get("Virtuals"), "unknown")
    pools = _first_value(overview.get("Configuration Totals.Pools"), overview.get("Pools"), "unknown")
    nodes = _first_value(overview.get("Configuration Totals.Nodes"), overview.get("Nodes"), "unknown")
    monitors = _first_value(
        overview.get("Configuration Totals.Monitor Instances"),
        overview.get("Monitor Instances"),
        "unknown",
    )
    modules = ", ".join(provisioned_modules) if provisioned_modules else "none surfaced as nominal"
    ha_state, is_ha_pair, ha_peer = _summarize_high_availability(high_availability)
    ha_short = _ha_short(ha_state, is_ha_pair, ha_peer, bool(high_availability))
    version = _first_value(record.get("version"), overview.get("Version - Edition"), "unknown-version")
    platform = _first_value(record.get("platform"), overview.get("Platform"), "unknown-platform")
    load_text = f"{load_1:.2f} / {load_5:.2f} / {load_15:.2f}"

    # Raw facts live once, in the System Summary table. Other sections interpret rather than repeat.
    system_summary: List[tuple[str, str]] = [
        ("Status", status),
        ("Uptime", uptime),
        ("Load average (1/5/15m)", load_text),
        ("Physical memory", physical_memory),
        ("CPU", cpu_totals),
        ("High availability", ha_short),
        ("Provisioned modules", modules),
        ("Object counts", f"{virtuals} virtuals / {pools} pools / {nodes} nodes / {monitors} monitors"),
        ("Time zone", time_zone),
        ("Appliance serial", appliance_serial),
    ]
    if _has_meaningful_value(blade_serial):
        system_summary.append(("Blade serial", blade_serial))

    critical_count = diagnostics.get("critical", 0)
    high_count = diagnostics.get("high", 0)
    medium_count = diagnostics.get("medium", 0)
    low_count = diagnostics.get("low", 0)
    diagnostics_line = (
        f"**{critical_count} critical** · **{high_count} high** · "
        f"{medium_count} medium · {low_count} low"
    )

    executive = [
        f"`{hostname}` runs `{version}` on `{platform}` (QKView `{record['qkview_id']}`).",
        _overall_health_line(critical_count, high_count, status, ha_short),
    ]

    critical_findings: List[str] = []
    warnings: List[str] = []

    if critical_count:
        critical_findings.append(
            f"iHealth reports **{critical_count} {_plural(critical_count, 'critical finding')}** — "
            "review the Diagnostics tab before the next change window."
        )
    if high_count:
        critical_findings.append(
            f"**{high_count} {_plural(high_count, 'high-severity finding')}** flagged — "
            "triage these against the current incident or maintenance scope."
        )
    if not critical_count and not high_count:
        warnings.append("No critical or high-severity iHealth findings in the current diagnostics summary.")
    if medium_count or low_count:
        warnings.append(
            f"{medium_count} medium and {low_count} low findings remain for routine cleanup."
        )

    if load_1 >= 4.0:
        performance_notes = [
            f"Load average is elevated at `{load_text}` — investigate CPU saturation against the graphs below."
        ]
    else:
        performance_notes = [
            f"Load average `{load_text}` shows headroom; no sustained CPU saturation in this snapshot."
        ]
    performance_notes.append(
        "Compare the 30-day CPU, memory, throughput, connection, and SSL graphs below against the incident window."
    )

    configuration_notes: List[str] = []
    if any(version.startswith(prefix) for prefix in ("11.", "12.", "13.", "14.")):
        configuration_notes.append(
            f"`{version}` is an older TMOS train — review lifecycle and hotfix posture carefully."
        )
    if is_ha_pair:
        configuration_notes.append(f"Configured as an HA pair; current role is {ha_short}.")
    elif high_availability:
        configuration_notes.append("No HA pair detected on the Status -> High Availability page.")
    else:
        configuration_notes.append("Status -> High Availability was not available this run; HA posture unconfirmed.")
    if not provisioned_modules:
        configuration_notes.append("No modules surfaced as Provisioned = Nominal; confirm provisioning expectations.")

    next_actions = [
        "Review the high-severity iHealth findings and confirm whether any map to the current incident or maintenance scope.",
        "Validate software support status and hotfix posture for the running TMOS version before the next upgrade window.",
        "Compare the utilization graphs against the incident window to confirm whether CPU, memory, throughput, connection, or SSL load aligns with the reported symptoms.",
    ]
    if is_ha_pair:
        next_actions.append("Confirm HA state and failover expectations on the peer device before making configuration changes.")

    ve_recommendation = [
        "Consider migrating to BIG-IP Virtual Edition at the next refresh cycle.",
        "Size vCPU, RAM, and the licensed throughput tier per F5 K14810 using the 30-day graphs below plus growth headroom — not the hardware model. Preserve HA design, provisioned modules, SSL/TLS load, and peak throughput/connections when choosing the VE entitlement.",
    ]

    scope = [f"Uploader: `{record['uploader_email']}`"]
    generation_date = _first_value(record.get("generation_date"))
    if generation_date:
        scope.append(f"QKView `{record['qkview_id']}` generated `{generation_date}`")
    else:
        scope.append(f"QKView `{record['qkview_id']}`")
    last_viewed = _first_value(record.get("last_viewed"))
    if last_viewed:
        scope.append(f"Last viewed in iHealth: `{last_viewed}`")

    return {
        "header": {
            "hostname": hostname,
            "subtitle": f"{version} · {platform} · QKView {record['qkview_id']}",
        },
        "scope": scope,
        "system_summary": system_summary,
        "diagnostics_line": diagnostics_line,
        "executive_assessment": executive,
        "critical_findings": critical_findings,
        "warnings": warnings,
        "performance_notes": performance_notes,
        "configuration_notes": configuration_notes,
        "ve_recommendation": ve_recommendation,
        "recommended_next_actions": _dedupe(next_actions),
    }


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else f"{singular}s"


def _overall_health_line(critical: int, high: int, status: str, ha_short: str) -> str:
    if critical:
        health = "needs attention — critical findings present"
    elif high:
        health = "has high-severity findings to triage"
    else:
        health = "looks healthy with no critical or high findings"
    return f"Overall, this device {health}. Status: `{status}`; HA: {ha_short}."


def _ha_short(state: str, is_pair: bool, peer: str, page_available: bool) -> str:
    if is_pair:
        role = state.title() if state and state != "not surfaced" else "unknown role"
        return f"{role} (HA pair{', peer ' + peer if peer else ''})"
    if page_available:
        return "No HA pair detected"
    return "Not available"


def fallback_graph_metrics(record: Dict[str, str], overview: Dict[str, str], hardware: Dict[str, str], diagnostics: Dict[str, int]) -> Dict[str, List[float]]:
    load_1, load_5, load_15 = parse_load_averages(overview)
    return {
        "Diagnostic Counts": [diagnostics.get("critical", 0), diagnostics.get("high", 0), diagnostics.get("medium", 0), diagnostics.get("low", 0)],
        "Load Average": [load_1, load_5, load_15],
    }


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _first_value(*values: str | None) -> str:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized and normalized != "[No data available]":
            return normalized
    return ""


def _summarize_high_availability(high_availability: Dict[str, str]) -> tuple[str, bool, str]:
    """Return (failover_state, is_ha_pair, peer)."""
    state = _first_value(
        _lookup_value(high_availability, "failover", "state"),
        _lookup_value(high_availability, "redundancy", "state"),
        _lookup_value(high_availability, "traffic", "group", "state"),
        _lookup_value(high_availability, "state"),
        "not surfaced",
    )
    peer = _first_value(
        _lookup_value(high_availability, "peer", "hostname"),
        _lookup_value(high_availability, "peer", "name"),
        _lookup_value(high_availability, "peer", "address"),
        _lookup_value(high_availability, "peer", "ip"),
    )
    is_ha_pair = any(
        _has_meaningful_value(value)
        for key, value in high_availability.items()
        if any(term in key.lower() for term in ("peer", "failover", "redundancy", "device group", "traffic group", "configsync", "config sync"))
    )
    return state, is_ha_pair, peer


def _lookup_value(values: Dict[str, str], *terms: str) -> str:
    for key, value in values.items():
        normalized_key = key.lower()
        if all(term in normalized_key for term in terms):
            if _has_meaningful_value(value):
                return str(value).strip()
    return ""


def _has_meaningful_value(value: str | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized not in {"", "-", "[no data available]", "none", "n/a", "not configured", "disabled", "unknown"}
