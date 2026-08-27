"""Extracts the slide-change schedule (time -> document -> page) from recording metadata.

Adobe Connect records share-pod navigation across two streams:
  * ContentManagerId_Mainstream setContentSo events map a numeric ctID to its
    full <documentDescriptor> (the document registry).
  * ftcontent*.xml playEvents carry the live navigation: ctID switches
    (setContentSo / name=ctID) and PDF page turns (setPdfContentSo memento
    tPgNum values, 0-based).

Combining both yields an accurate millisecond timeline of exactly what the
attendees saw on the Share pod.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from utils.logger import logger


@dataclass
class SlideEvent:
    time_ms: int
    ct_id: str
    page_num: int          # 0-based PDF page index (-1 = unknown/static asset)
    doc_index: int = 0     # 1-based index into the parsed descriptor list (0 = unresolved)


def parse_document_descriptors(xml_text: str) -> list[dict]:
    """Extract all <documentDescriptor> blocks from recording metadata (CDATA-aware).

    Handles both plain tags and CDATA-wrapped tags, tolerating whitespace
    between the tag and its CDATA section.
    """
    descriptors: list[dict] = []
    seen = set()

    blocks = re.findall(r"<documentDescriptor>.*?</documentDescriptor>", xml_text, re.DOTALL | re.IGNORECASE)
    for block in blocks:
        def extract_tag(tag: str) -> str:
            m = re.search(rf"<{tag}>\s*<!\[CDATA\[(.*?)\]\]>\s*</{tag}>", block, re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()
            m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL | re.IGNORECASE)
            if m:
                return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.DOTALL).strip()
            return ""

        desc = {
            "id": extract_tag("id"),
            "name": extract_tag("name"),
            "playbackContentOutputPath": extract_tag("playbackContentOutputPath"),
            "contentOutputPath": extract_tag("contentOutputPath"),
            "downloadUrl": extract_tag("downloadUrl"),
            "originatingSco": extract_tag("originatingSco"),
            "scoID": extract_tag("scoID"),
        }

        if not any((desc["playbackContentOutputPath"], desc["contentOutputPath"],
                    desc["downloadUrl"], desc["id"], desc["originatingSco"], desc["scoID"])):
            continue

        key = (desc["id"], desc["contentOutputPath"], desc["playbackContentOutputPath"], desc["originatingSco"])
        if key not in seen:
            seen.add(key)
            descriptors.append(desc)
    return descriptors


def _iter_messages(xml_text: str):
    """Yields (time_ms, body) for every <Message> in the stream."""
    for msg in re.finditer(r'<Message time="(\d+)"[^>]*>(.*?)</Message>', xml_text, re.DOTALL):
        t_msg = int(msg.group(1))
        body = msg.group(2)
        tm = re.search(r"<time>\s*<!\[CDATA\[(\d+)\]\]>\s*</time>", body)
        yield (int(tm.group(1)) if tm else t_msg), body


def _parse_ctid_registry(xml_text: str) -> dict[str, dict]:
    """Builds the ctID -> documentDescriptor registry from ContentManager events."""
    registry: dict[str, dict] = {}
    all_descriptors = parse_document_descriptors(xml_text)

    for _, body in _iter_messages(xml_text):
        if "ContentManagerId" not in body:
            continue
        # <name>N</name> ... <ctID>N</ctID> ... <documentDescriptor>...</documentDescriptor>
        for m in re.finditer(
            r"<name>\s*(?:<!\[CDATA\[)?(\d+)(?:\]\]>)?\s*</name>\s*<newValue>(.*?)</newValue>",
            body, re.DOTALL,
        ):
            ct_id, payload = m.group(1), m.group(2)
            ct_m = re.search(r"<ctID>\s*(?:<!\[CDATA\[)?(-?\d+)(?:\]\]>)?\s*</ctID>", payload)
            if not ct_m or ct_m.group(1) != ct_id:
                continue
            dd = re.search(r"<documentDescriptor>.*?</documentDescriptor>", payload, re.DOTALL | re.IGNORECASE)
            if not dd:
                continue
            block = dd.group(0)

            def tag(t: str) -> str:
                mm = re.search(rf"<{t}>\s*<!\[CDATA\[(.*?)\]\]>\s*</{t}>", block, re.DOTALL | re.IGNORECASE)
                if mm:
                    return mm.group(1).strip()
                mm = re.search(rf"<{t}>(.*?)</{t}>", block, re.DOTALL | re.IGNORECASE)
                return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", mm.group(1), flags=re.DOTALL).strip() if mm else ""

            registry[ct_id] = {
                "contentOutputPath": tag("contentOutputPath"),
                "playbackContentOutputPath": tag("playbackContentOutputPath"),
                "originatingSco": tag("originatingSco"),
                "scoID": tag("scoID"),
                "downloadUrl": tag("downloadUrl"),
            }
    return registry


def _match_doc_index(desc: dict, descriptors: list[dict]) -> int:
    """Finds the 1-based index of a registry descriptor among the fetched ones."""
    for field in ("originatingSco", "scoID"):
        val = desc.get(field)
        if val:
            for i, d in enumerate(descriptors, start=1):
                if d.get(field) == val:
                    return i
    path = desc.get("contentOutputPath")
    if path:
        for i, d in enumerate(descriptors, start=1):
            if d.get("contentOutputPath") == path:
                return i
    return 0


def parse_slide_schedule(unpacked_dir: Path) -> list[SlideEvent]:
    """Reconstructs the chronological Share-pod slide timeline for a recording."""
    meta_names = ("mainstream.xml", "indexstream.xml", "document-metadata.xml")
    registry_text = ""
    for name in meta_names:
        p = unpacked_dir / name
        if p.exists():
            try:
                registry_text += p.read_text(encoding="utf-8", errors="ignore") + "\n"
            except Exception as e:
                logger.warning(f"Could not read {name}: {e}")

    registry = _parse_ctid_registry(registry_text)
    descriptors = parse_document_descriptors(registry_text)

    # Collect navigation events from every content pod stream.
    nav_events: list[tuple[int, str, str]] = []  # (time_ms, kind, value)
    for p in sorted(unpacked_dir.glob("ftcontent*.xml")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"Could not read {p.name}: {e}")
            continue
        for t, body in _iter_messages(text):
            if "setPdfContentSo" in body or "setContentSo" in body:
                for m in re.finditer(
                    r"<name>\s*<!\[CDATA\[ctID\]\]>\s*</name>\s*<newValue>\s*<!\[CDATA\[(-?\d+)\]\]>",
                    body, re.DOTALL,
                ):
                    nav_events.append((t, "CTID", m.group(1)))
                for m in re.finditer(
                    r"<name>\s*<!\[CDATA\[memento\]\]>\s*</name>\s*<newValue>\s*<!\[CDATA\[([^\]]*)\]\]>",
                    body, re.DOTALL,
                ):
                    pg = re.search(r"tPgNum-(\d+)", m.group(1))
                    if pg:
                        nav_events.append((t, "PAGE", pg.group(1)))

    if not nav_events:
        logger.info("No share-pod navigation events found; static backdrop will be used.")
        return []

    nav_events.sort(key=lambda e: e[0])

    # Walk chronologically, emitting an event whenever (ctID, page) changes.
    schedule: list[SlideEvent] = []
    current_ct, current_page = None, -1
    for t, kind, value in nav_events:
        if kind == "CTID":
            if value != current_ct:
                # Document switched: the page is unknown until the next memento
                # arrives, so emit an explicit blank beat instead of carrying
                # over the previous document's page number.
                current_ct = value
                current_page = -1
                schedule.append(SlideEvent(time_ms=t, ct_id=value, page_num=-1))
        else:  # PAGE
            page = int(value)
            if page != current_page:
                current_page = page
                schedule.append(SlideEvent(time_ms=t, ct_id=current_ct or "", page_num=page))

    # Resolve doc indices and drop events without a known document or page.
    resolved: list[SlideEvent] = []
    last_key = None
    for ev in schedule:
        desc = registry.get(ev.ct_id) if ev.ct_id not in (None, "", "0", "-1") else None
        doc_index = _match_doc_index(desc, descriptors) if desc else 0
        key = (doc_index, ev.page_num)
        if doc_index <= 0 or ev.page_num < 0 or key == last_key:
            # Document cleared (ctID<=0) or unknown: represent as a blank beat.
            if key != last_key:
                resolved.append(SlideEvent(time_ms=ev.time_ms, ct_id=ev.ct_id, page_num=-1, doc_index=0))
                last_key = key
            continue
        resolved.append(SlideEvent(time_ms=ev.time_ms, ct_id=ev.ct_id, page_num=ev.page_num, doc_index=doc_index))
        last_key = key

    logger.info(f"Slide schedule reconstructed: {len(resolved)} visual beats "
                f"across {len({e.doc_index for e in resolved if e.doc_index})} document(s).")
    return resolved
