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
    ha_state, ha_summary = _summarize_high_availability(high_availability)
    version = _first_value(record.get("version"), overview.get("Version - Edition"), "unknown-version")
    platform = _first_value(record.get("platform"), overview.get("Platform"), "unknown-platform")
    system_summary = [
        f"Hostname: `{hostname}`.",
        f"Time zone: `{time_zone}`.",
        f"Appliance serial: `{appliance_serial}`.",
        f"Blade serial: `{blade_serial}`.",
        f"Status: `{status}`.",
        f"Uptime: `{uptime}`.",
        f"Load average: `{load_1:.2f}` / `{load_5:.2f}` / `{load_15:.2f}`.",
        f"Physical memory: `{physical_memory}`.",
        f"CPU totals: `{cpu_totals}`.",
        f"High availability: {ha_summary}",
    ]

    high_count = diagnostics.get("high", 0)
    medium_count = diagnostics.get("medium", 0)
    low_count = diagnostics.get("low", 0)
    critical_count = diagnostics.get("critical", 0)

    executive = [
        f"Host `{hostname}` runs `{version}` on `{platform}` with QKView `{record['qkview_id']}`.",
        f"iHealth diagnostics show `{critical_count}` critical, `{high_count}` high, `{medium_count}` medium, and `{low_count}` low findings.",
        f"The device reports status `{status}` with uptime `{uptime}`.",
        f"Status -> High Availability indicates {ha_summary}",
    ]

    critical_findings: List[str] = []
    warnings: List[str] = []

    if critical_count:
        critical_findings.append(f"iHealth flagged `{critical_count}` critical findings for this BIG-IP.")
    if high_count:
        critical_findings.append(f"iHealth flagged `{high_count}` high-severity findings that should be reviewed before the next change window.")
    if not critical_findings:
        warnings.append("No critical iHealth findings were visible in the current diagnostics summary.")

    if load_1 >= 4.0:
        warnings.append(f"Load average is elevated at `{load_1:.2f}` / `{load_5:.2f}` / `{load_15:.2f}`.")
    else:
        warnings.append(f"Load average is `{load_1:.2f}` / `{load_5:.2f}` / `{load_15:.2f}`, which does not immediately suggest CPU saturation.")

    performance_notes = [
        f"Load average: `{load_1:.2f}` / `{load_5:.2f}` / `{load_15:.2f}`.",
        f"Physical memory reported by iHealth: `{physical_memory}`.",
        f"CPU inventory reported by iHealth: `{cpu_totals}`.",
    ]

    configuration_notes = [
        f"Platform: `{platform}`.",
        f"Software version: `{version}`.",
        f"Provisioned modules from Status -> Overview / Licensing and Provisioning where Provisioned = Nominal: `{modules}`.",
        f"HA posture from Status -> High Availability: {ha_summary}",
        f"Object counts: virtuals `{virtuals}`, pools `{pools}`, nodes `{nodes}`, monitor instances `{monitors}`.",
        f"Device serial: `{appliance_serial}`.",
    ]

    if any(version.startswith(prefix) for prefix in ("11.", "12.", "13.", "14.")):
        configuration_notes.append("This TMOS train is old enough that lifecycle and hotfix posture should be reviewed carefully.")

    next_actions = [
        "Review the high-severity iHealth findings in the Diagnostics tab and confirm whether any map directly to the current incident or maintenance scope.",
        "Validate software support status and hotfix posture for the running TMOS version before planning the next upgrade or remediation window.",
        "Compare the attached utilization graphs against the incident window to confirm whether CPU, memory, throughput, connection rate, or SSL load spikes align with the reported symptoms.",
    ]

    ve_recommendation = [
        "Evaluate replacing this hardware BIG-IP with BIG-IP Virtual Edition as part of the next refresh cycle.",
        f"Current system data shows platform `{platform}`, CPU inventory `{cpu_totals}`, physical memory `{physical_memory}`, and load average `{load_1:.2f}` / `{load_5:.2f}` / `{load_15:.2f}`, which is useful input for VE sizing.",
        "Size the VE using F5 guidance from K14810 and select vCPU, RAM, and licensed throughput tier based on the 30-day utilization graphs plus growth headroom rather than matching hardware model names directly.",
        "Preserve HA design, module requirements, SSL/TLS load, and peak throughput/connections when choosing the VE entitlement and hypervisor footprint.",
    ]

    if any(term in ha_state.lower() for term in ("standby", "active", "offline", "failover")):
        next_actions.append("Confirm HA state and failover expectations on the peer device before making configuration changes.")

    return {
        "scope": [
            f"Uploader email: `{record['uploader_email']}`",
            f"Uploaded file: `{record['uploaded_file']}` generated `{record['generation_date']}`",
            f"Last viewed in iHealth: `{record['last_viewed']}`",
        ],
        "system_summary": system_summary,
        "executive_assessment": executive,
        "critical_findings": critical_findings,
        "warnings": warnings,
        "performance_notes": performance_notes,
        "configuration_notes": configuration_notes,
        "ve_recommendation": ve_recommendation,
        "recommended_next_actions": _dedupe(next_actions),
    }


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


def _summarize_high_availability(high_availability: Dict[str, str]) -> tuple[str, str]:
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
    has_pair_markers = any(
        _has_meaningful_value(value)
        for key, value in high_availability.items()
        if any(term in key.lower() for term in ("peer", "failover", "redundancy", "device group", "traffic group", "configsync", "config sync"))
    )
    if has_pair_markers:
        peer_text = f" with peer `{peer}`" if peer else ""
        return state, f"this device is configured as an HA pair and is currently `{state}`{peer_text}."
    if high_availability:
        return state, "the iHealth High Availability page did not surface evidence that this device is configured as an HA pair."
    return state, "the iHealth High Availability page was not available during this run."


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
