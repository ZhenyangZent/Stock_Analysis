"""非計分研究收件匣：登錄 yt-downloader 輸出，或擷取公開 RSS／文章。

本模組的所有寫入都被限定在 05_研究收件匣，且 JSONL 只能追加。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INBOX_ROOT = ROOT / "05_研究收件匣"
USER_AGENT = "StockResearchInbox/1.0 (+personal research; respects robots.txt)"
SOURCE_TYPES = {"youtube", "podcast", "rss", "article"}
QUESTION_STATUSES = {"OPEN", "VERIFIED_OFFICIAL", "CONTRADICTED", "UNVERIFIABLE"}
FORMAL_FIELDS = {"C5", "C6", "I2", "I2Q", "I3", "I4"}
TRANSCRIPT_EXTENSIONS = (".vtt", ".srt", ".txt", ".json3")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(row: dict) -> bytes:
    return (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def ensure_inside(root: Path, target: Path) -> Path:
    root = root.resolve()
    target = target.resolve()
    if target != root and root not in target.parents:
        raise ValueError("RESEARCH_INBOX_PATH_OUTSIDE_ROOT")
    return target


def reject_formal_field_payload(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).upper() in FORMAL_FIELDS:
                raise ValueError(f"FORMAL_FIELD_WRITE_FORBIDDEN:{key}")
            if str(key).lower() == "field" and str(item).upper() in FORMAL_FIELDS:
                raise ValueError(f"FORMAL_FIELD_WRITE_FORBIDDEN:{item}")
            reject_formal_field_payload(item)
    elif isinstance(value, list):
        for item in value:
            reject_formal_field_payload(item)


def append_jsonl(root: Path, relative: str, row: dict) -> None:
    reject_formal_field_payload(row)
    target = ensure_inside(root, root / relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(row)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(target, flags, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL_NOT_OBJECT:{path.name}:{number}")
            rows.append(value)
    return rows


def load_sources(root: Path) -> dict[str, dict]:
    data = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != "RESEARCH_INBOX_SOURCES_1" or not isinstance(data.get("sources"), list):
        raise ValueError("SOURCES_SCHEMA_INVALID")
    result = {}
    required = {"source_id", "name", "type", "url", "language", "member_only", "enabled"}
    for index, source in enumerate(data["sources"]):
        if not isinstance(source, dict) or set(source) != required:
            raise ValueError(f"SOURCE_FIELDS_INVALID:{index}")
        if source["type"] not in SOURCE_TYPES:
            raise ValueError(f"SOURCE_TYPE_INVALID:{source['type']}")
        if not isinstance(source["member_only"], bool) or not isinstance(source["enabled"], bool):
            raise ValueError(f"SOURCE_BOOLEAN_INVALID:{source['source_id']}")
        if not source["source_id"] or source["source_id"] in result:
            raise ValueError(f"SOURCE_ID_INVALID_OR_DUPLICATE:{source['source_id']}")
        result[source["source_id"]] = source
    return result


def summary(command: str) -> dict:
    return {"schema_version": "RESEARCH_INBOX_RUN_SUMMARY_1", "command": command,
            "added": 0, "duplicates": 0, "failed": 0, "manual_action_required": 0,
            "items": [], "completed_at": None}


def existing_keys(root: Path) -> tuple[set[tuple[str, str]], set[str], set[str]]:
    rows = read_jsonl(root / "inbox.jsonl")
    return ({(str(r.get("source_id")), str(r.get("original_url"))) for r in rows},
            {str(r.get("content_sha256")) for r in rows},
            {str(r.get("record_id")) for r in rows})


def normalize_transcript(data: bytes, suffix: str) -> str:
    text = data.decode("utf-8-sig", errors="replace")
    if suffix.lower() == ".json3":
        parsed = json.loads(text)
        parts = []
        for event in parsed.get("events", []):
            value = "".join(seg.get("utf8", "") for seg in event.get("segs", []))
            if value.strip():
                parts.append(value)
        text = "\n".join(parts)
    lines, output = text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), []
    cue = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{3}\s+-->\s+")
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "WEBVTT" or stripped.isdigit() or cue.match(stripped):
            continue
        stripped = re.sub(r"<[^>]+>", "", stripped)
        stripped = html.unescape(stripped).strip()
        if stripped and (not output or stripped != output[-1]):
            output.append(stripped)
    return "\n".join(output).strip() + ("\n" if output else "")


def choose_transcript(info_path: Path) -> Path | None:
    stem = info_path.name.removesuffix(".info.json")
    candidates = []
    for path in info_path.parent.glob(stem + "*"):
        if path.is_file() and path.suffix.lower() in TRANSCRIPT_EXTENSIONS and not path.name.endswith(".info.json"):
            candidates.append(path)
    return sorted(candidates, key=lambda p: (TRANSCRIPT_EXTENSIONS.index(p.suffix.lower()), len(p.name)))[0] if candidates else None


def add_record(root: Path, source: dict, *, title: str, original_url: str,
               published_at: str | None, content_bytes: bytes, transcript_path: Path | None,
               external_media_path: Path | None = None) -> str:
    pair_keys, hashes, _ = existing_keys(root)
    content_hash = digest(content_bytes)
    if (source["source_id"], original_url) in pair_keys or content_hash in hashes:
        return "duplicate"
    fetched_at = utc_now()
    record_id = "RINBOX_" + digest(f"{source['source_id']}\0{original_url}\0{content_hash}".encode())[:24]
    transcript = None
    external = None
    if source["member_only"]:
        if transcript_path:
            raw = transcript_path.read_bytes()
            external = {"path": str(transcript_path.resolve()), "sha256": digest(raw)}
        elif external_media_path:
            raw = external_media_path.read_bytes()
            external = {"path": str(external_media_path.resolve()), "sha256": digest(raw)}
    elif transcript_path:
        normalized = normalize_transcript(transcript_path.read_bytes(), transcript_path.suffix)
        normalized_bytes = normalized.encode("utf-8")
        destination = ensure_inside(root, root / "transcripts" / f"{record_id}.txt")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(normalized_bytes)
        transcript = {"path": destination.relative_to(root).as_posix(), "sha256": digest(normalized_bytes),
                      "format": "text/plain; charset=utf-8"}
    row = {"schema_version": "RESEARCH_INBOX_RECORD_1", "record_id": record_id,
           "source_id": source["source_id"], "title": title, "original_url": original_url,
           "published_at": published_at, "fetched_at": fetched_at, "content_sha256": content_hash,
           "transcript": transcript, "external_content": external, "language": source["language"],
           "member_only": source["member_only"],
           "usage_mark": "MEMBER_ONLY_PERSONAL_USE" if source["member_only"] else "PUBLIC_CONTENT",
           "verification_questions": []}
    append_jsonl(root, "inbox.jsonl", row)
    return "added"


def ingest_yt(root: Path, source: dict, input_dir: Path, result: dict) -> None:
    if source["type"] != "youtube":
        raise ValueError("SOURCE_NOT_YOUTUBE")
    if not input_dir.is_dir():
        raise ValueError("YT_INPUT_DIR_NOT_FOUND")
    found = False
    for info_path in sorted(input_dir.rglob("*.info.json")):
        found = True
        try:
            raw = info_path.read_bytes()
            info = json.loads(raw)
            original_url = info.get("webpage_url") or info.get("original_url") or info.get("url")
            if not original_url:
                raise ValueError("YT_ORIGINAL_URL_MISSING")
            transcript_path = choose_transcript(info_path)
            status = add_record(root, source, title=info.get("title") or info_path.stem,
                                original_url=original_url, published_at=info.get("upload_date") or info.get("timestamp"),
                                content_bytes=raw, transcript_path=transcript_path, external_media_path=info_path)
            result["added" if status == "added" else "duplicates"] += 1
        except Exception as exc:
            result["failed"] += 1
            result["items"].append({"item": str(info_path), "error": str(exc)})
    if not found:
        result["manual_action_required"] += 1
        result["items"].append({"item": str(input_dir), "action": "NO_YT_INFO_JSON_FOUND"})


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.title = ""; self._in_title = False
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript"}: self.parts.append(" ")
        if tag.lower() == "title": self._in_title = True
    def handle_endtag(self, tag):
        if tag.lower() == "title": self._in_title = False
        if tag.lower() in {"p", "div", "article", "section", "br", "li", "h1", "h2", "h3"}: self.parts.append("\n")
    def handle_data(self, data):
        if self._in_title: self.title += data
        self.parts.append(data)
    def text(self):
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip() + "\n"


def allowed_by_robots(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    robots = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    parser = urllib.robotparser.RobotFileParser(); parser.set_url(robots)
    try:
        parser.read()
    except Exception:
        return False
    return parser.can_fetch(USER_AGENT, url)


def fetch_bytes(url: str) -> bytes:
    if not allowed_by_robots(url):
        raise PermissionError("ROBOTS_DISALLOWED_OR_UNAVAILABLE")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(10 * 1024 * 1024 + 1)


def xml_text(element, names: tuple[str, ...]) -> str | None:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].lower() in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def fetch_source(root: Path, source: dict, result: dict) -> None:
    if source["member_only"]:
        result["manual_action_required"] += 1
        result["items"].append({"source_id": source["source_id"], "action": "MEMBER_SOURCE_REQUIRES_EXTERNAL_INGEST"})
        return
    raw = fetch_bytes(source["url"])
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("DOWNLOAD_TOO_LARGE")
    if source["type"] == "article":
        parser = TextExtractor(); parser.feed(raw.decode("utf-8", errors="replace"))
        text = parser.text(); temp = root / ".article.normalized.tmp"
        try:
            temp.write_text(text, encoding="utf-8")
            status = add_record(root, source, title=parser.title.strip() or source["name"], original_url=source["url"],
                                published_at=None, content_bytes=raw, transcript_path=temp)
        finally:
            try: temp.unlink()
            except FileNotFoundError: pass
        result["added" if status == "added" else "duplicates"] += 1
        return
    root_xml = ET.fromstring(raw)
    entries = [e for e in root_xml.iter() if e.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    for entry in entries:
        title = xml_text(entry, ("title",)) or "(untitled)"
        link = xml_text(entry, ("link", "guid", "id"))
        if not link:
            for child in entry.iter():
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]; break
        if not link:
            result["failed"] += 1; result["items"].append({"source_id": source["source_id"], "error": "RSS_LINK_MISSING"}); continue
        body = xml_text(entry, ("content", "encoded", "description", "summary")) or title
        body_bytes = body.encode("utf-8")
        temp = root / ".feed.normalized.tmp"
        try:
            temp.write_text(TextOnly.from_html(body), encoding="utf-8")
            status = add_record(root, source, title=title, original_url=link,
                                published_at=xml_text(entry, ("published", "updated", "pubdate")),
                                content_bytes=body_bytes, transcript_path=temp)
        finally:
            try: temp.unlink()
            except FileNotFoundError: pass
        result["added" if status == "added" else "duplicates"] += 1


class TextOnly:
    @staticmethod
    def from_html(value: str) -> str:
        parser = TextExtractor(); parser.feed(value)
        return parser.text()


def add_question(root: Path, record_id: str, question: str) -> str:
    _, _, record_ids = existing_keys(root)
    if record_id not in record_ids:
        raise ValueError("INBOX_RECORD_NOT_FOUND")
    question_id = "RQ_" + digest(f"{record_id}\0{question}".encode())[:24]
    rows = read_jsonl(root / "verification_questions.jsonl")
    if any(row.get("question_id") == question_id for row in rows):
        return "duplicate"
    append_jsonl(root, "verification_questions.jsonl",
                 {"schema_version": "RESEARCH_QUESTION_EVENT_1", "event": "CREATED", "question_id": question_id,
                  "record_id": record_id, "question": question, "status": "OPEN", "official_doc_ids": [], "created_at": utc_now()})
    return "added"


def update_question(root: Path, question_id: str, status: str, doc_ids: list[str]) -> None:
    if status not in QUESTION_STATUSES - {"OPEN"}:
        raise ValueError("QUESTION_STATUS_INVALID")
    rows = read_jsonl(root / "verification_questions.jsonl")
    if not any(row.get("question_id") == question_id and row.get("event") == "CREATED" for row in rows):
        raise ValueError("QUESTION_NOT_FOUND")
    if status == "VERIFIED_OFFICIAL" and not doc_ids:
        raise ValueError("OFFICIAL_DOC_ID_REQUIRED")
    append_jsonl(root, "verification_questions.jsonl",
                 {"schema_version": "RESEARCH_QUESTION_EVENT_1", "event": "STATUS_CHANGED", "question_id": question_id,
                  "status": status, "official_doc_ids": doc_ids, "created_at": utc_now()})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox-root", type=Path, default=DEFAULT_INBOX_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-sources")
    sub.add_parser("fetch")
    yt = sub.add_parser("ingest-yt"); yt.add_argument("--source-id", required=True); yt.add_argument("--input-dir", type=Path, required=True)
    add = sub.add_parser("add-question"); add.add_argument("--record-id", required=True); add.add_argument("--question", required=True)
    update = sub.add_parser("update-question"); update.add_argument("--question-id", required=True); update.add_argument("--status", required=True, choices=sorted(QUESTION_STATUSES - {"OPEN"})); update.add_argument("--official-doc-id", action="append", default=[])
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    result = summary(args.command)
    try:
        root = args.inbox_root.resolve()
        sources = load_sources(root)
        if args.command == "fetch":
            for source in sources.values():
                if not source["enabled"] or source["type"] == "youtube": continue
                try: fetch_source(root, source, result)
                except Exception as exc:
                    result["failed"] += 1; result["items"].append({"source_id": source["source_id"], "error": str(exc)})
        elif args.command == "ingest-yt":
            if args.source_id not in sources: raise ValueError("SOURCE_ID_NOT_FOUND")
            ingest_yt(root, sources[args.source_id], args.input_dir.resolve(), result)
        elif args.command == "add-question":
            state = add_question(root, args.record_id, args.question)
            result["added" if state == "added" else "duplicates"] += 1
        elif args.command == "update-question":
            update_question(root, args.question_id, args.status, args.official_doc_id); result["added"] += 1
    except Exception as exc:
        result["failed"] += 1; result["items"].append({"error": str(exc)})
    result["completed_at"] = utc_now()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
