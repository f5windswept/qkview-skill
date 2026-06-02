from __future__ import annotations

import json
import mimetypes
import os
import re
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
    report: Dict,
    uploaded_file_ids: Iterable[str],
    graph_groups: Optional[Sequence[Tuple[str, Sequence[Tuple[str, str]]]]] = None,
) -> List[Dict]:
    header = report.get("header") or {}
    children: List[Dict] = [heading_2(header.get("hostname") or hostname)]

    subtitle = header.get("subtitle")
    if subtitle:
        children.append(caption_paragraph(subtitle))

    children.extend(_bullet_section("Scope", report.get("scope", [])))

    system_summary = report.get("system_summary") or []
    if system_summary:
        children.append(heading_3("System Summary"))
        children.append(key_value_table(system_summary))

    diagnostics_line = report.get("diagnostics_line")
    if diagnostics_line:
        children.append(heading_3("Diagnostics"))
        children.append(callout(diagnostics_line))

    children.extend(_bullet_section("Executive Assessment", report.get("executive_assessment", [])))
    children.extend(_bullet_section("Critical Findings", report.get("critical_findings") or ["None noted."]))
    children.extend(_bullet_section("Warnings", report.get("warnings") or ["None noted."]))
    children.extend(_bullet_section("Performance Notes", report.get("performance_notes", [])))
    children.extend(_bullet_section("Configuration Notes", report.get("configuration_notes", [])))
    children.extend(_bullet_section("Virtual Edition", report.get("ve_recommendation", [])))

    actions = report.get("recommended_next_actions", [])
    if actions:
        children.append(heading_3("Recommended Next Actions"))
        children.extend(numbered(item) for item in actions)

    if graph_groups:
        children.append(heading_3("Utilization Graphs"))
        for group_title, items in graph_groups:
            children.append(bold_paragraph(group_title))
            for upload_id, caption in items:
                children.append(image_block(upload_id, caption))
    else:
        for upload_id in uploaded_file_ids:
            children.append(image_block(upload_id))
    return children


def _bullet_section(title: str, items: Sequence[str]) -> List[Dict]:
    if not items:
        return []
    blocks: List[Dict] = [heading_3(title)]
    blocks.extend(bullet(item) for item in items)
    return blocks


_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def rich_text(text: str) -> List[Dict]:
    """Convert lightweight markup (**bold**, `code`) into Notion rich_text segments."""
    segments: List[Dict] = []
    pos = 0
    for match in _MARKUP_RE.finditer(text):
        if match.start() > pos:
            segments.append(_segment(text[pos:match.start()]))
        if match.group(1) is not None:
            segments.append(_segment(match.group(1), bold=True))
        else:
            segments.append(_segment(match.group(2), code=True))
        pos = match.end()
    if pos < len(text):
        segments.append(_segment(text[pos:]))
    return segments or [_segment("")]


def _segment(content: str, bold: bool = False, code: bool = False, italic: bool = False, color: Optional[str] = None) -> Dict:
    segment: Dict = {"type": "text", "text": {"content": content}}
    annotations: Dict = {}
    if bold:
        annotations["bold"] = True
    if code:
        annotations["code"] = True
    if italic:
        annotations["italic"] = True
    if color:
        annotations["color"] = color
    if annotations:
        segment["annotations"] = annotations
    return segment


def paragraph(text: str) -> Dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def bold_paragraph(text: str) -> Dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_segment(text, bold=True)]}}


def caption_paragraph(text: str) -> Dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_segment(text, italic=True, color="gray")]}}


def heading_1(text: str) -> Dict:
    return {"object": "block", "type": "heading_1", "heading_1": {"rich_text": rich_text(text)}}


def heading_2(text: str) -> Dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich_text(text)}}


def heading_3(text: str) -> Dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text(text)}}


def bullet(text: str) -> Dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(text)}}


def numbered(text: str) -> Dict:
    return {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": rich_text(text)}}


def callout(text: str) -> Dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {"rich_text": rich_text(text), "color": "gray_background"},
    }


def key_value_table(rows: Sequence[Tuple[str, str]]) -> Dict:
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": 2,
            "has_column_header": False,
            "has_row_header": True,
            "children": [
                {
                    "object": "block",
                    "type": "table_row",
                    "table_row": {"cells": [[_segment(str(label), bold=True)], rich_text(str(value))]},
                }
                for label, value in rows
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
