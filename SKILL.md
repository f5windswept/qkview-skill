name: bigip-qkview-analysis
description: Analyzes BIG-IP QKViews already uploaded to F5 iHealth and produces an operator-ready health summary. Use when asked to review a BIG-IP QKView, summarize iHealth findings, identify performance or configuration risks, or turn uploaded QKView data into a concise report with recommendations.

# BIG-IP QKView Analysis
Reviews BIG-IP QKViews in F5 iHealth and turns the findings into a practical health report.

## When To Use
Use this skill when the user asks to:
- analyze a BIG-IP QKView already uploaded to iHealth
- summarize system health, performance, or configuration issues
- identify notable alerts, risks, or tuning opportunities
- produce a customer-ready or operator-ready report from iHealth findings

## Prerequisites
1. **Active iHealth session** - the user must be logged in to iHealth in Chrome. If not, have them log in at `https://account.f5.com/ihealth2` first.
2. **Uploaded QKView** - the target BIG-IP QKView must already exist in iHealth.
3. **Target identifier** - get at least one of: customer email, QKView ID, hostname, or case/reference details so the correct upload can be found.

## Inputs To Collect
- **Customer email** - best default for locating uploaded QKViews
- **QKView ID** - use if the user already has the exact upload
- **Hostname or device name** - helps choose the right QKView when multiple exist
- **Time window or incident context** - what problem period or symptom the user cares about
- **Focus area** - optional; examples: CPU, memory, HA, SSL, ASM, throughput, crashes, provisioning, networking

## Workflow
1. Confirm the user is logged in to iHealth in Chrome.
2. Locate the correct QKView in iHealth using the best identifier available.
3. Review the main system summary first: platform, TMOS version, uptime, provisioning, sync/failover state, and active alerts.
4. Confirm whether the device is configured as an HA pair and capture the current state from `Status -> High Availability`.
5. Review diagnostic findings from iHealth with emphasis on severity, recurrence, and operational impact.
6. Check performance data that is available in iHealth: CPU, memory, connections, throughput, SSL TPS, HTTP rate, and unusual spikes or sustained saturation.
7. Note configuration and lifecycle risks such as old TMOS versions, hotfix gaps, expired certs, disk pressure, failover issues, and which modules are actually provisioned by checking for `Nominal` in `Status -> Overview / Licensing and Provisioning`.
8. Produce a concise report with findings grouped into critical issues, warnings, observations, and next actions.

## Analysis Checklist
Review these areas when data is available:

### System Overview
- hostname, management IP, platform, and serial where shown
- TMOS version, hotfix level, and software age
- licensed modules and provisioning levels
- device trust, sync, and failover status for HA pairs

### Health Findings
- iHealth diagnostics by severity
- daemon restarts, core files, crash indicators, and failed services
- disk usage, inode usage, and log growth concerns
- NTP, DNS, and management-plane issues

### Performance
- sustained or spiking CPU usage
- memory pressure, swap usage, or TMM memory concerns
- active connections and connection churn
- throughput by bits and packets
- SSL TPS and HTTP request rates
- per-core imbalance if visible

### Configuration Risk
- unsupported or old TMOS release train
- mismatched HA peers or config-sync problems
- oversized or undersized provisioning
- certificate expiration or crypto hygiene issues
- virtual server, pool, VLAN, route, or SNAT anomalies called out by iHealth

## Output Format
Return the result as a short report with these sections:

```text
BIG-IP QKView Analysis

Scope
- device / QKView reviewed
- time period or incident context

Executive Assessment
- 2-4 bullets on overall health and biggest risks

Critical Findings
- only issues needing urgent action

Warnings
- meaningful issues that are not yet critical

Performance Notes
- CPU, memory, throughput, connections, SSL, HTTP trends

Configuration Notes
- versioning, HA, provisioning, certificates, or architecture concerns

Recommended Next Actions
1. most important action
2. second action
3. optional follow-up validation
```

## Reporting Guidance
- Lead with the operational impact, not raw counters.
- Distinguish clearly between confirmed issues and weaker signals.
- If iHealth shows a finding but the impact is unclear, say so.
- Prefer specific recommendations such as upgrade, add hotfix, rebalance provisioning, clean disk, investigate failover state, or inspect a busy virtual server.
- If the user asked about one symptom, answer that first before giving broader observations.

## Troubleshooting
### Cannot find the QKView
- Ask for a different identifier such as exact uploader email, hostname, or QKView ID.
- Check whether the upload may be under a different user or account.

### iHealth session expired
- Ask the user to refresh their iHealth login in Chrome and try again.

### Multiple similar QKViews exist
- Choose the newest one unless the user gave a specific incident date.
- Confirm hostname, upload time, and TMOS version before reporting.

### Data is incomplete
- State which sections were unavailable in iHealth.
- Still provide a partial report and list the missing evidence.

## Guardrails
- Do not claim packet-level certainty from iHealth summary data alone.
- Do not recommend disruptive remediation without calling out risk and validation steps.
- If the findings imply a production-impacting defect, recommend confirming software version, hotfix level, and change window requirements.
