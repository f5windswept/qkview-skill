from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from qkview_skill.extract_cookies import browser_cookie_header
from qkview_skill.html_parsers import build_graph_selection_token, parse_graph_download_href, parse_graph_names, parse_recent_row, parse_search_results


class IHealthClientError(RuntimeError):
    pass


class IHealthClient:
    def __init__(self, base_url: Optional[str] = None, browser: str = "firefox") -> None:
        self.base_url = (base_url or os.environ.get("IHEALTH_BASE_URL") or "https://ihealth.f5.com").rstrip("/")
        request_host = urllib.parse.urlparse(self.base_url).hostname or "ihealth.f5.com"
        self.cookie_header = browser_cookie_header(request_host, browser=browser)
        self.browser_user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0"
        self._last_response_headers: List[tuple[str, str]] = []

    def _request_text(self, path: str, kind: str = "html", referer: Optional[str] = None) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{self.base_url}{path}"
        headers = self._headers(kind=kind, referer=referer)
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                self._last_response_headers = response.getheaders()
                payload = response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            raise IHealthClientError(f"Request failed for {url}: {exc}") from exc
        if "<title>F5 Networks - Sign In</title>" in payload or "okta-sign-in" in payload:
            raise IHealthClientError(
                "iHealth returned the sign-in page. Refresh the relevant iHealth pages in Firefox and try again."
            )
        return payload

    def _request_with_referers(self, path: str, referers: Iterable[Optional[str]], kind: str = "html") -> str:
        errors: List[str] = []
        for referer in referers:
            try:
                return self._request_text(path, kind=kind, referer=referer)
            except IHealthClientError as exc:
                errors.append(str(exc))
        raise IHealthClientError(errors[-1] if errors else f"Unable to fetch {path}")

    def _request_bytes(self, path: str, referer: Optional[str] = None) -> bytes:
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, headers=self._headers(kind="download", referer=referer))
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                self._last_response_headers = response.getheaders()
                return response.read()
        except Exception as exc:
            raise IHealthClientError(f"Download failed for {url}: {exc}") from exc

    def _request_json(self, path: str) -> Dict:
        payload = self._request_with_referers(path, [None, f"{self.base_url}/qkview-analyzer/recent"], kind="xhr")
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IHealthClientError(f"Expected JSON for {path} but received non-JSON content") from exc

    def _headers(self, kind: str, referer: Optional[str]) -> Dict[str, str]:
        headers = {
            "Cookie": self.cookie_header,
            "User-Agent": self.browser_user_agent,
        }
        if kind == "xhr":
            headers.update(
                {
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                }
            )
        elif kind == "download":
            headers.update(
                {
                    "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
                }
            )
        else:
            headers.update(
                {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-User": "?1",
                }
            )
        if referer:
            headers["Referer"] = referer
        return headers

    def list_recent_qkviews(self) -> List[Dict[str, str]]:
        payload = self._request_json("/qkview-analyzer/qkviewsRecent?draw=1&start=0&length=100")
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return [parse_recent_row(row) for row in rows if isinstance(row, list)]

    def search_qkviews(self, email: Optional[str] = None, hostname: Optional[str] = None, qkview_id: Optional[str] = None) -> List[Dict[str, str]]:
        if qkview_id and not email and not hostname:
            return [{"qkview_id": qkview_id, "hostname": f"qkview-{qkview_id}", "platform": "unknown-platform", "version": "unknown-version", "serial": "unknown-serial", "generation_date": "unknown", "uploaded_file": "unknown", "uploader_email": "unknown", "uploaded_at": "unknown", "last_viewed": "unknown"}]

        results: List[Dict[str, str]] = []
        if email:
            results = self.search_qkviews_by_query(email)
        if not results:
            rows = self.list_recent_qkviews()
            for row in rows:
                if email and row["uploader_email"].lower() != email.lower():
                    continue
                results.append(row)

        matches: List[Dict[str, str]] = []
        for row in results:
            if qkview_id and row["qkview_id"] != qkview_id:
                continue
            if hostname and row["hostname"].lower() != hostname.lower():
                continue
            matches.append(row)
        if not matches:
            raise IHealthClientError("No matching QKViews were visible in the current iHealth search results")
        return matches

    def search_qkviews_by_query(self, email: str) -> List[Dict[str, str]]:
        warm_candidates: List[Optional[str]] = []
        try:
            for recent in self.list_recent_qkviews():
                qkview_id = recent.get("qkview_id")
                if qkview_id:
                    warm_candidates.extend(
                        [
                            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/diagnostics/?tf1=hide[CVE]",
                            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/overview",
                            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/hardware",
                        ]
                    )
        except Exception:
            warm_candidates = []

        if not warm_candidates:
            warm_candidates = [None, f"{self.base_url}/qkview-analyzer/recent"]
        else:
            warm_candidates.append(f"{self.base_url}/qkview-analyzer/recent")
            warm_candidates.append(None)

        discovered: Dict[str, Dict[str, str]] = {}
        for offset in range(0, 250, 25):
            path = f"/qkview-analyzer/?query={urllib.parse.quote(email)}&showAll=true&offset={offset}"
            page_html = self._request_with_referers(path, warm_candidates, kind="html")
            page_results = parse_search_results(page_html, email)
            if not page_results:
                break
            new_count = 0
            for row in page_results:
                if row["qkview_id"] not in discovered:
                    discovered[row["qkview_id"]] = row
                    new_count += 1
            if new_count == 0:
                break
            if len(page_results) < 25:
                break
        return sorted(discovered.values(), key=lambda item: item["hostname"])

    def fetch_overview_page(self, qkview_id: str) -> str:
        path = f"/qkview-analyzer/qv/{qkview_id}/status/overview"
        referers = [
            f"{self.base_url}{path}",
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/diagnostics/?tf1=hide[CVE]",
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/hardware",
            None,
        ]
        return self._request_with_referers(path, referers, kind="html")

    def fetch_hardware_page(self, qkview_id: str) -> str:
        path = f"/qkview-analyzer/qv/{qkview_id}/status/hardware"
        referers = [None, f"{self.base_url}{path}", f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/overview"]
        return self._request_with_referers(path, referers, kind="html")

    def fetch_diagnostics_page(self, qkview_id: str) -> str:
        path = f"/qkview-analyzer/qv/{qkview_id}/diagnostics/?tf1=hide[CVE]"
        referers = [
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/hardware",
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/overview",
            f"{self.base_url}{path}",
            None,
        ]
        return self._request_with_referers(path, referers, kind="html")

    def fetch_high_availability_page(self, qkview_id: str) -> str:
        candidate_paths = [
            f"/qkview-analyzer/qv/{qkview_id}/status/ha",
            f"/qkview-analyzer/qv/{qkview_id}/status/highavailability",
            f"/qkview-analyzer/qv/{qkview_id}/status/high-availability",
            f"/qkview-analyzer/qv/{qkview_id}/status/failover",
        ]
        errors: List[str] = []
        for path in candidate_paths:
            referers = [
                f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/overview",
                f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/hardware",
                f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/diagnostics/?tf1=hide[CVE]",
                f"{self.base_url}{path}",
                None,
            ]
            try:
                payload = self._request_with_referers(path, referers, kind="html")
            except IHealthClientError as exc:
                errors.append(str(exc))
                continue

            if "high availability" in payload.lower() or path.endswith("/status/ha"):
                return payload
        raise IHealthClientError(errors[-1] if errors else f"Unable to fetch a High Availability page for {qkview_id}")

    def fetch_graphs_index_page(self, qkview_id: str) -> str:
        path = f"/qkview-analyzer/qv/{qkview_id}/graphs/standard"
        referers = [
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/overview",
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/status/hardware",
            f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/diagnostics/?tf1=hide[CVE]",
            None,
            f"{self.base_url}{path}",
        ]
        return self._request_with_referers(path, referers, kind="html")

    def fetch_selected_graphs_page(self, qkview_id: str, selected_graphs: Iterable[str]) -> str:
        base_path = f"/qkview-analyzer/qv/{qkview_id}/graphs/standard"
        base_url = f"{self.base_url}{base_path}"
        index_page = self.fetch_graphs_index_page(qkview_id)
        lb_cookies = [
            value.split(";", 1)[0]
            for key, value in self._last_response_headers
            if key.lower() == "set-cookie" and value.split("=", 1)[0].startswith("BIGip")
        ]
        if lb_cookies:
            self.cookie_header = f"{self.cookie_header}; {'; '.join(lb_cookies)}"
        graph_names = parse_graph_names(index_page)
        if not graph_names:
            raise IHealthClientError("Could not read graph selectors from the iHealth graphs page")
        token = build_graph_selection_token(graph_names, selected_graphs)
        return self._request_text(f"{base_path}?selected={token}", kind="html", referer=base_url)

    def download_graph_bundle(self, qkview_id: str, selected_graphs_page: str, output_dir: str) -> List[str]:
        download_href = parse_graph_download_href(selected_graphs_page, qkview_id)
        if not download_href:
            raise IHealthClientError("Could not find a graph download link on the selected graphs page")
        data = self._request_bytes(download_href, referer=f"{self.base_url}/qkview-analyzer/qv/{qkview_id}/graphs/standard")
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        archive_path = target / f"{qkview_id}-graphs.zip"
        archive_path.write_bytes(data)
        return [str(archive_path)]

    @staticmethod
    def extract_graph_bundle(archive_path: str, output_dir: str) -> List[str]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        extracted: List[str] = []
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                if not member.lower().endswith(".png"):
                    continue
                archive.extract(member, path=output)
                extracted.append(str(output / member))
        return extracted
