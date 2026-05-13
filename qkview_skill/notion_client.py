from __future__ import annotations

import json
import mimetypes
import os
import uuid
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"


class NotionClientError(RuntimeError):
    pass


class NotionClient:
    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token or os.environ.get("NOTION_API_TOKEN") or _load_export_from_bashrc("NOTION_API_TOKEN")
        if not self.token:
            raise NotionClientError("NOTION_API_TOKEN is not set")

    def _json_request(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict:
        request = urllib.request.Request(
            f"{NOTION_API}{path}",
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise NotionClientError(f"Notion request failed for {path}: {exc}") from exc

    def append_children(self, block_id: str, children: List[Dict]) -> None:
        for start in range(0, len(children), 100):
            batch = children[start : start + 100]
            self._json_request("PATCH", f"/blocks/{block_id}/children", {"children": batch})

    def upload_file(self, file_path: str, content_type: Optional[str] = None) -> str:
        path = Path(file_path)
        mime_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        created = self._json_request("POST", "/file_uploads", {"filename": path.name, "content_type": mime_type})
        upload_id = created["id"]

        boundary = f"----NotionBoundary{uuid.uuid4().hex}"
        body = _multipart_body(path, boundary, mime_type)
        request = urllib.request.Request(
            f"{NOTION_API}/file_uploads/{upload_id}/send",
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            data=body,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise NotionClientError(f"Failed to upload {file_path} to Notion: {exc}") from exc

        if payload.get("status") != "uploaded":
            raise NotionClientError(f"Notion did not finish uploading {file_path}: {payload}")
        return upload_id


def _load_export_from_bashrc(name: str) -> Optional[str]:
    bashrc = Path("~/.bashrc").expanduser()
    if not bashrc.exists():
        return None
    for line in bashrc.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[len("export ") :].split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return None


def _multipart_body(file_path: Path, boundary: str, mime_type: str) -> bytes:
    content = file_path.read_bytes()
    lines = [
        f"--{boundary}\r\n".encode("utf-8"),
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode("utf-8"),
        f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return b"".join(lines)


def blocks_from_report(
    hostname: str,
    report: Dict[str, List[str]],
    uploaded_file_ids: Iterable[str],
    graph_groups: Optional[Sequence[Tuple[str, Sequence[Tuple[str, str]]]]] = None,
) -> List[Dict]:
    children: List[Dict] = [
        heading_2(hostname),
        bulleted_section("Scope", report["scope"]),
        bulleted_section("System Summary", report.get("system_summary", [])),
        bulleted_section("Executive Assessment", report["executive_assessment"]),
        bulleted_section("Critical Findings", report["critical_findings"] or ["None noted."]),
        bulleted_section("Warnings", report["warnings"] or ["None noted."]),
        bulleted_section("Performance Notes", report["performance_notes"]),
        bulleted_section("Configuration Notes", report["configuration_notes"]),
        bulleted_section("Virtual Edition Recommendation", report.get("ve_recommendation", [])),
        numbered_section("Recommended Next Actions", report["recommended_next_actions"]),
    ]
    if graph_groups:
        children.append(heading_3("Utilization Graphs"))
        for group_title, items in graph_groups:
            children.append(heading_3(group_title))
            for upload_id, caption in items:
                children.append(image_block(upload_id, caption))
    else:
        for upload_id in uploaded_file_ids:
            children.append(image_block(upload_id))
    return children


def paragraph(text: str) -> Dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def heading_1(text: str) -> Dict:
    return {"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def heading_2(text: str) -> Dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def heading_3(text: str) -> Dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def bulleted_section(title: str, items: List[str]) -> Dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "children": [
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": item}}]},
                }
                for item in items
            ],
        },
    }


def numbered_section(title: str, items: List[str]) -> Dict:
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "children": [
                {
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {"rich_text": [{"type": "text", "text": {"content": item}}]},
                }
                for item in items
            ],
        },
    }


def image_block(file_upload_id: str, caption: Optional[str] = None) -> Dict:
    return {
        "object": "block",
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {"id": file_upload_id},
            "caption": ([{"type": "text", "text": {"content": caption}}] if caption else []),
        },
    }
