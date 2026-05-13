import unittest

from qkview_skill.analyzer import analyze_bundle
from qkview_skill.html_parsers import parse_high_availability_summary, parse_provisioned_modules
from qkview_skill.live_analysis import build_live_report
from qkview_skill.models import QKViewBundle


class AnalyzerTests(unittest.TestCase):
    def test_analyze_bundle_surfaces_major_risks(self) -> None:
        bundle = QKViewBundle(
            qkview_id="12345",
            summary={
                "hostname": "bigip1.example.com",
                "tmos_version": "14.1.5",
                "platform": "i5800",
                "sync_status": "Changes Pending",
                "failover_status": "Active",
                "disk_usage": 92,
                "expired_certificates": ["/Common/example.com"],
            },
            diagnostics=[
                {"severity": "critical", "title": "TMM restart detected", "detail": "TMM restarted twice in 24h", "recommendation": "Review crash logs and core files."},
                {"severity": "warning", "title": "NTP drift", "detail": "Clock drift exceeds threshold"},
            ],
            metrics={
                "cpu_utilization": 93,
                "memory_used": 88,
                "active_connections": 42000,
                "ssl_tps": 1800,
            },
        )

        report = analyze_bundle(bundle)

        self.assertTrue(report.critical_findings)
        self.assertTrue(any("Disk usage" in item for item in report.critical_findings))
        self.assertTrue(any("CPU utilization" in item for item in report.performance_notes))
        self.assertTrue(any("config-sync" in item.lower() for item in report.recommended_next_actions))

    def test_parse_provisioned_modules_only_returns_nominal_rows(self) -> None:
        html = """
        <div class="section">
          <div class="hd"><h3>Licensing and Provisioning</h3></div>
          <div class="bd">
            <table>
              <tr><th>Module / Feature</th><th>Licensed</th><th>Provisioned</th></tr>
              <tr><td>LTM</td><td>Nominal</td><td>Nominal</td></tr>
              <tr><td>ASM</td><td>Nominal</td><td>Nominal</td></tr>
              <tr><td>AFM</td><td>Nominal</td><td>-</td></tr>
            </table>
          </div>
        </div>
        """

        self.assertEqual(parse_provisioned_modules(html), ["LTM", "ASM"])

    def test_build_live_report_surfaces_ha_and_nominal_modules(self) -> None:
        report = build_live_report(
            {
                "hostname": "bigip1.example.com",
                "qkview_id": "12345",
                "uploader_email": "user@example.com",
                "uploaded_file": "example.qkview",
                "generation_date": "2026-05-13",
                "last_viewed": "2026-05-13",
                "version": "17.1.3 - Final",
                "platform": "BIG-IP i4600 (C115)",
                "serial": "F5-TEST-1234",
            },
            {
                "System.Hostname": "bigip1.example.com",
                "System.Physical Memory": "64 GB",
                "System.CPU Totals": "16 cores",
                "System.Status": "ACTIVE",
                "System.Uptime": "10 days",
                "System.Time Zone": "UTC",
                "System.Appliance S/N": "F5-TEST-1234",
                "System.Load Average": "1.00, 0.80, 0.60",
                "Configuration Totals.Virtuals": "12",
                "Configuration Totals.Pools": "9",
                "Configuration Totals.Nodes": "18",
                "Configuration Totals.Monitor Instances": "24",
            },
            {},
            {"high": 2, "medium": 1, "low": 0, "critical": 0},
            high_availability={
                "Failover State": "ACTIVE",
                "Peer Address": "10.0.0.2",
                "Config Sync": "In Sync",
            },
            provisioned_modules=["LTM", "ASM"],
        )

        self.assertTrue(any("configured as an HA pair" in item for item in report["system_summary"]))
        self.assertTrue(any("Provisioned = Nominal: `LTM, ASM`" in item for item in report["configuration_notes"]))

    def test_parse_high_availability_summary_reads_sync_failover_pair(self) -> None:
        html = """
        <div class="section wide">
          <div class="hd"><h3>Traffic Group Summary</h3></div>
          <div class="bd">
            <table class="status fullwidth">
              <tr><th>Traffic Group</th><th>State</th><th>Times Active</th><th>Status</th></tr>
              <tr><td>/Common/traffic-group-1</td><td>active</td><td>2</td><td>active for 10 days</td></tr>
            </table>
          </div>
        </div>
        <div class="section wide">
          <div class="hd"><h3>Device Groups Summary</h3></div>
          <div class="bd">
            <table class="status fullwidth">
              <tr><th>Name</th><th>Number of Devices</th><th>Device Group Type</th></tr>
              <tr><td>/Common/example-dg</td><td>2</td><td>sync-failover</td></tr>
            </table>
          </div>
        </div>
        """

        parsed = parse_high_availability_summary(html)

        self.assertEqual(parsed["Traffic Group State"], "active")
        self.assertEqual(parsed["Device Group Type"], "sync-failover")
        self.assertEqual(parsed["Device Count"], "2")


if __name__ == "__main__":
    unittest.main()
