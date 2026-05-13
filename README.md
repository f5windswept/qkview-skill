# QKView Skill

Local tooling for analyzing BIG-IP QKViews that have already been uploaded to F5 iHealth.

## What works now
- local CLI for loading fixture data and producing an analysis report
- iHealth session extraction from Chrome cookies on macOS
- pluggable iHealth client with configurable endpoint paths and response capture
- rule-based analyzer that turns raw findings into critical issues, warnings, and recommendations
- live publish flow that reports HA pair posture from `Status -> High Availability`
- module reporting that lists only entries marked `Nominal` in `Status -> Overview / Licensing and Provisioning`

## Quick start

```bash
cd ~/qkview-skill
python3 scripts/qkview_analyze.py --fixture fixtures/sample_qkview.json
```

## Live testing
For live iHealth validation, you will need:
- an active iHealth login in Chrome
- either a QKView ID or an uploader email/hostname to search by
- possibly endpoint overrides if your iHealth account uses different JSON paths

Example:

```bash
cd ~/qkview-skill
python3 scripts/qkview_analyze.py --qkview-id 123456 --dump-dir fixtures/live-capture
```

## Environment overrides
- `IHEALTH_BASE_URL` - defaults to `https://ihealth2.f5.com`
- `IHEALTH_SEARCH_PATH` - exact search path override
- `IHEALTH_SUMMARY_PATH` - exact summary path template, use `{qkview_id}`
- `IHEALTH_DIAGNOSTICS_PATH` - exact diagnostics path template, use `{qkview_id}`
- `IHEALTH_METRICS_PATH` - exact metrics path template, use `{qkview_id}`
