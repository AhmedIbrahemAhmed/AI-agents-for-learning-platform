import importlib
import re
import unicodedata
from typing import List, Dict, Any, Optional

import httpx
import io
from bs4 import BeautifulSoup
from langchain_core.tools import tool
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp


def normalize_pdf_url(url: str) -> str:
    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url

def extract_video_id(url: str) -> Optional[str]:
    for p in [r"(?:v=|\/)([0-9A-Za-z_-]{11})", r"youtu\.be\/([0-9A-Za-z_-]{11})"]:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def fetch_metadata(url: str) -> dict:
    ydl_opts: Any = {"quiet": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title", ""),
        "channel": info.get("uploader", ""),
        "duration_s": info.get("duration", 0),
        "description": (info.get("description", "") or "")[:1000],
    }


def fetch_transcript(video_id: str) -> str:
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)

        text_parts: List[str] = []
        for item in transcript:
            if hasattr(item, "text"):
                text_parts.append(item.text)
            elif isinstance(item, dict):
                text_parts.append(item.get("text", ""))
            else:
                text_parts.append(str(item))

        return " ".join(text_parts)

    except Exception as e:
        raise RuntimeError(f"Transcript extraction failed: {e}")


def fetch_blog_content(url: str) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"}
        resp = httpx.get(url, timeout=20.0, headers=headers)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Blog fetch failed: {e}")

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    description = ""
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if desc_tag and desc_tag.get("content"):
        desc_content = desc_tag.get("content", "")
        if isinstance(desc_content, list):
            description = " ".join(desc_content).strip()
        else:
            description = str(desc_content).strip()
    # Prefer specialised extractors if available (trafilatura/readability), otherwise fall back to structured element collection.
    content_text = ""
    try:
        # Trafilatura does a good job for many sites if installed
        trafilatura = importlib.import_module("trafilatura")

        extracted = trafilatura.extract(html)
        if extracted:
            content_text = extracted.strip()
    except Exception:
        # Not available or failed - try readability as a secondary option
        try:
            # Dynamically import readability to avoid static analysis import errors in some environments
            readability = importlib.import_module("readability")
            Document = getattr(readability, "Document", None)
            if Document is None:
                raise ImportError("readability.Document not found")

            doc = Document(html)
            summary_html = doc.summary()
            summary_soup = BeautifulSoup(summary_html, "html.parser")
            elems = summary_soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "code"])
            parts = [e.get_text(separator=" ", strip=True) for e in elems if e.get_text(strip=True)]
            content_text = "\n\n".join(parts).strip()
        except Exception:
            content_text = ""

    if not content_text:
        # Fallback: attempt to extract from common article/main containers first
        article = soup.find("article") or soup.find(id=lambda v: bool(v and "article" in v.lower())) or soup.find("main")
        container = article or soup
        elems = container.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "code"])
        parts = [e.get_text(separator=" ", strip=True) for e in elems if e.get_text(strip=True)]
        content_text = "\n\n".join(parts)
        if not content_text:
            content_text = soup.get_text(separator=" ", strip=True)

    chunks = split_text_into_chunks(content_text, max_chars=9000)
    # Helpful debug: if extraction produced very little text, include a hint
    debug = None
    if len("".join(chunks)) < 200:
        debug = {"length": len(content_text), "sample": content_text[:500]}

    result = {
        "title": title,
        "description": description,
        "content_text": content_text,
        "content_chunks": chunks,
    }
    if debug:
        result["debug"] = debug
    return result


def split_text_into_chunks(text: str, max_chars: int = 9000, overlap: int = 200) -> List[str]:
    if not text:
        return []

    # Split into lines first to preserve code block structure and words
    lines = text.split("\n")
    chunks: List[str] = []
    current_chunk: List[str] = []
    current_length = 0

    for line in lines:
        # If a single line is absurdly long, fallback to character splitting for just that line
        if len(line) > max_chars:
            if current_chunk:
                chunks.append("\n".join(current_chunk).strip())
                current_chunk = []
                current_length = 0
            
            # Slice the oversized line
            start = 0
            while start < len(line):
                end = start + max_chars
                chunks.append(line[start:end].strip())
                start = max(end - overlap, start + 1)
            continue

        if current_length + len(line) + 1 > max_chars:
            chunks.append("\n".join(current_chunk).strip())
            # Retain a bit of overlap history from the end of the previous chunk lines if desired
            current_chunk = [line]
            current_length = len(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk).strip())

    # Sanitize chunks for safe JSON transport
    return [sanitize_for_json(chunk) for chunk in chunks if chunk]


def sanitize_for_json(s: str) -> str:
    """Normalize text and secure it for strict JSON transmission and parsing.
    
    - Normalizes Unicode characters to NFC format.
    - Purges dangerous null bytes completely.
    - Replaces hidden C0 control characters with standard spaces.
    - Explicitly escapes backslashes and double quotes to protect structural code text blocks.
    - Enforces valid UTF-8, replacing broken structural sequences safely.
    """
    if s is None:
        return ""
        
    # 1. Unicode Normalization (NFC)
    try:
        s = unicodedata.normalize("NFC", s)
    except Exception:
        pass

    # 2. Remove explicit null bytes which break many C-based string parsers
    s = s.replace("\x00", "")

    # 3. Clean out non-printable control characters, but keep tabs and newlines
    s = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", s)

    # 4. Escape Backslashes first (so we don't accidentally double-escape later modifications)
    s = s.replace("\\", "\\\\")

    # 5. Escape Double Quotes so internal code strings (e.g., printf("%d")) won't break JSON wrappers
    s = s.replace('"', '\\"')

    # 6. Fallback step to lock down strict valid UTF-8 encoding
    try:
        s = s.encode("utf-8", "replace").decode("utf-8")
    except Exception:
        # Hard fallback: Strip everything outside printable ASCII boundaries and common white spaces
        s = "".join(ch if (ord(ch) >= 32 or ch in "\t\n\r") else " " for ch in s)

    return s


@tool
def fetch_source_content(source_type: str, url: str) -> dict:
    """Fetch content for a source (YouTube or blog) and return metadata and chunks."""
    source_type = source_type.lower()
    # PDF support: fetch binary and extract text
    if source_type == "pdf" or (url and url.lower().endswith(".pdf")):
        try:
            if "drive.google.com" in url:
                url = normalize_pdf_url(url)
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()

            content = resp.content or b""
            # Quick sanity: PDF files should start with %PDF
            if not content.startswith(b"%PDF"):
                snippet = None
                try:
                    snippet = content[:512].decode("utf-8", errors="replace")
                except Exception:
                    snippet = str(content[:512])
                return {"error": "PDF fetch/extract failed: response is not a PDF (missing %PDF header)", "debug_snippet": snippet, "headers": dict(resp.headers)}

            try:
                # Use the modern 'pypdf' package instead of the deprecated 'PyPDF2'
                from pypdf import PdfReader
            except Exception as e:
                return {"error": f"PDF extractor library unavailable: {e}"}

            pages = []
            try:
                reader = PdfReader(io.BytesIO(content))
                for idx, p in enumerate(reader.pages):
                    try:
                        # Extract text and run structural sanitation immediately per page
                        page_text = p.extract_text() or ""
                        
                        # Strip common vertical form feeds or structural markers specific to PDFs
                        page_text = page_text.replace("\x0c", "\n").replace("\xa0", " ")
                        
                        pages.append(page_text)
                    except Exception as page_err:
                        # Fallback placeholder to preserve overall indexing structure if a page fails
                        pages.append("")
            except Exception as primary_err:
                # Lenient fallback attempt
                try:
                    reader = PdfReader(io.BytesIO(content), strict=False)
                    for p in reader.pages:
                        try:
                            page_text = p.extract_text() or ""
                            page_text = page_text.replace("\x0c", "\n").replace("\xa0", " ")
                            pages.append(page_text)
                        except Exception:
                            pages.append("")
                except Exception as fallback_err:
                    snippet = None
                    try:
                        snippet = content[:512].decode("utf-8", errors="replace")
                    except Exception:
                        snippet = str(content[:512])
                    return {"error": f"PDF extraction completely failed. Primary: {primary_err}; Fallback: {fallback_err}", "debug_snippet": snippet, "headers": dict(resp.headers)}

            # Combine page strings and run your full deep sanitation script
            raw_combined_text = "\n\n".join([p for p in pages if p.strip()])
            sanitized_text = sanitize_for_json(raw_combined_text)

            if not sanitized_text.strip():
                return {"error": "PDF extraction succeeded but yielded zero readable text characters. The PDF might be scanned images."}

            # Split text safely using your updated newline/sentence chunker
            chunks = split_text_into_chunks(sanitized_text, max_chars=9000)
            title = url.split("/")[-1]
            
            return {
                "source_type": "pdf",
                "source_url": url,
                "video_id": None,
                "title": title,
                "channel": None,
                "duration_minutes": 0.0,
                "description": "",
                "content_text": sanitized_text,
                "content_chunks": chunks,
            }
        except Exception as e:
            return {"error": f"PDF fetch/extract failed: {e}"}
    if source_type == "youtube":
        video_id = extract_video_id(url)
        if not video_id:
            return {"error": f"Cannot extract video ID from: {url}"}

        try:
            transcript = fetch_transcript(video_id)
            transcript_chunks = split_text_into_chunks(transcript, max_chars=9000)
        except Exception as e:
            return {"error": f"Transcript extraction failed completely: {e}"}

        try:
            meta = fetch_metadata(url)
            title = meta["title"]
            channel = meta["channel"]
            duration_min = round(meta["duration_s"] / 60, 1)
            description = meta["description"]
        except Exception as e:
            title = f"YouTube Video (ID: {video_id})"
            channel = "Unknown Channel"
            duration_min = 0.0
            description = "Transcript successfully parsed dynamically."

        return {
            "source_type": "youtube",
            "source_url": url,
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "duration_minutes": duration_min,
            "description": description,
            "content_text": transcript,
            "content_chunks": transcript_chunks,
        }

    if source_type in {"blog", "article", "webpage"}:
        if "drive.google.com" in url:
            url = normalize_pdf_url(url)
        result = fetch_blog_content(url)
        result["source_type"] = "blog"
        result["source_url"] = url
        result["duration_minutes"] = 0.0
        return result

    return {"error": f"Unsupported source_type: {source_type}. Use youtube or blog."}
