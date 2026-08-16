"""Azure OpenAI plugin for enhanced conversion with image description and OCR.

Strategy:
- For each embedded image, perform BOTH:
  1. OCR extraction (extract any text in the image)
  2. AI description (describe the visual content, charts, diagrams)
- Combine both into a structured image metadata block placed inline
- This gives the LLM maximum context about what was in the image
"""
from __future__ import annotations

import base64
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_VERIFY_SSL,
    AZURE_CHAT_DEPLOYMENT,
    MARKITDOWN_SOURCE_DIR,
)

#: Azure OpenAI data-plane API version used by every call in this app.
AZURE_API_VERSION = "2024-12-01-preview"

if MARKITDOWN_SOURCE_DIR.exists() and str(MARKITDOWN_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(MARKITDOWN_SOURCE_DIR))


# ---------------------------------------------------------------------------
# Azure OpenAI pricing table (per 1 million tokens, Global Standard, USD)
# Source: https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/
# Updated: 2025-06
# ---------------------------------------------------------------------------
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # deployment-name-pattern → (input_per_1m_usd, output_per_1m_usd)
    "gpt-chat-latest": (5.00, 30.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4-turbo": (11.00, 33.00),
    "gpt-4": (30.00, 60.00),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    "o1-mini": (1.10, 4.40),
    "o1": (15.00, 60.00),
}

_DEFAULT_PRICING = (5.00, 30.00)  # gpt-chat-latest if deployment name not recognised


def _get_pricing(deployment_name: str) -> tuple[float, float]:
    """Return (input_per_1m, output_per_1m) USD for a given deployment name."""
    name_lower = (deployment_name or "").lower()
    # Match longest prefix first so "gpt-4o-mini" beats "gpt-4o"
    for key in sorted(_MODEL_PRICING, key=len, reverse=True):
        if key in name_lower:
            return _MODEL_PRICING[key]
    return _DEFAULT_PRICING


# ---------------------------------------------------------------------------
# Token-tracking client wrapper
# ---------------------------------------------------------------------------

class _CompletionsProxy:
    """Proxies client.chat.completions.create() and records token usage."""

    def __init__(self, tracker: "_TokenTracker") -> None:
        self._tracker = tracker

    def create(self, *args: Any, **kwargs: Any) -> Any:
        real_completions = self._tracker._real.chat.completions
        response = real_completions.create(*args, **kwargs)
        if hasattr(response, "usage") and response.usage:
            inp = getattr(response.usage, "prompt_tokens", 0) or 0
            out = getattr(response.usage, "completion_tokens", 0) or 0
            self._tracker._calls.append((inp, out))
        return response


class _ChatProxy:
    def __init__(self, tracker: "_TokenTracker") -> None:
        self._tracker = tracker

    @property
    def completions(self) -> _CompletionsProxy:
        return _CompletionsProxy(self._tracker)


class _TokenTracker:
    """Wraps an AzureOpenAI client to capture cumulative token usage."""

    def __init__(self, real_client: Any, deployment: str) -> None:
        self._real = real_client
        self.deployment = deployment
        self._calls: list[tuple[int, int]] = []  # (prompt_tokens, completion_tokens)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    @property
    def chat(self) -> _ChatProxy:
        return _ChatProxy(self)

    def build_cost_estimate(
        self, locations: list[str] | None = None
    ) -> "AICostEstimate | None":
        """Build an AICostEstimate from accumulated call data."""
        if not self._calls:
            return None

        from app.models import AICostEstimate, AICostCall  # avoid circular at module level

        input_per_1m, output_per_1m = _get_pricing(self.deployment)
        cost_calls: list[AICostCall] = []
        total_input = 0
        total_output = 0

        for i, (inp, out) in enumerate(self._calls):
            loc = (locations[i] if locations and i < len(locations) else f"AI call {i + 1}")
            call_type = "image_analysis" if any(
                kw in loc for kw in ("Sheet", "Page", "image")
            ) else "document_plugin"
            call_cost = (inp / 1_000_000 * input_per_1m) + (out / 1_000_000 * output_per_1m)
            cost_calls.append(
                AICostCall(
                    call_type=call_type,
                    location=loc,
                    model=self.deployment,
                    input_tokens=inp,
                    output_tokens=out,
                    total_cost_usd=round(call_cost, 8),
                )
            )
            total_input += inp
            total_output += out

        total_cost = (
            total_input / 1_000_000 * input_per_1m
            + total_output / 1_000_000 * output_per_1m
        )
        return AICostEstimate(
            model=self.deployment,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=round(total_cost, 6),
            cost_per_call=cost_calls,
        )


def estimate_cost_for_images(image_count: int, deployment: str | None = None) -> float:
    """Rough pre-conversion cost estimate based on image count.

    Uses conservative per-image estimate:
    ~1 500 input tokens (prompt + image) + ~600 output tokens per image.
    """
    dep = deployment or AZURE_CHAT_DEPLOYMENT or "gpt-4o"
    inp_per_1m, out_per_1m = _get_pricing(dep)
    tokens_in_per_image = 1500
    tokens_out_per_image = 600
    cost_per_image = (
        tokens_in_per_image / 1_000_000 * inp_per_1m
        + tokens_out_per_image / 1_000_000 * out_per_1m
    )
    return round(cost_per_image * image_count, 6)


@dataclass
class ImageAnalysis:
    """Result of AI image analysis combining OCR + description."""
    location: str  # e.g., "Sheet1/B5" or "Page 2"
    ocr_text: str  # Raw text extracted from image
    description: str  # AI description of what the image shows
    image_type: str  # "chart", "table", "diagram", "photo", "logo", "unknown"
    confidence: float  # 0-1 how confident the AI is
    errors: list[str] = field(default_factory=list)


def is_plugin_available() -> bool:
    """Check if Azure OpenAI credentials are configured."""
    return bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)


def _get_azure_client():
    """Create and return the Azure OpenAI client used by every AI call here.

    Certificate verification stays on unless ``AZURE_OPENAI_VERIFY_SSL`` is
    explicitly turned off, because these requests carry the API key.
    """
    from openai import AzureOpenAI

    kwargs: dict[str, Any] = {
        "azure_endpoint": AZURE_OPENAI_ENDPOINT,
        "api_key": AZURE_OPENAI_API_KEY,
        "api_version": AZURE_API_VERSION,
    }
    if not AZURE_OPENAI_VERIFY_SSL:
        import httpx

        kwargs["http_client"] = httpx.Client(verify=False)
    return AzureOpenAI(**kwargs)


def _analyze_image(client: Any, image_bytes: bytes, context: str = "") -> ImageAnalysis:
    """Analyze a single image using AI: extract text AND describe content.

    Uses a single GPT-4o call with a structured prompt that asks for both
    OCR and description in one shot (more efficient than two calls).
    """
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_uri = f"data:image/png;base64,{base64_image}"

    prompt = f"""Analyze this image from a document. Provide a structured analysis:

1. **IMAGE_TYPE**: Classify as one of: chart, table, diagram, photo, logo, form, text_block, unknown
2. **OCR_TEXT**: Extract ALL text visible in the image exactly as written. Maintain layout/order. If no text, write "NONE".
3. **DESCRIPTION**: Describe what this image shows in detail for a knowledge base. Include:
   - What type of visual it is (bar chart, flow diagram, table, etc.)
   - Key data points, values, labels, legends
   - Relationships between elements
   - Any conclusions that can be drawn from it
4. **CONFIDENCE**: Rate 0.0-1.0 how well you could extract the content

{f"Context: This image is from {context}" if context else ""}

Respond in EXACTLY this format:
IMAGE_TYPE: <type>
OCR_TEXT:
<extracted text or NONE>
END_OCR
DESCRIPTION:
<detailed description>
END_DESCRIPTION
CONFIDENCE: <0.0-1.0>"""

    try:
        response = client.chat.completions.create(
            model=AZURE_CHAT_DEPLOYMENT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            max_completion_tokens=2000,
            temperature=1,
        )

        text = response.choices[0].message.content or ""
        return _parse_image_analysis(text)
    except Exception as e:
        return ImageAnalysis(
            location="",
            ocr_text="",
            description=f"[Image analysis failed: {e}]",
            image_type="unknown",
            confidence=0.0,
            errors=[str(e)],
        )


def _parse_image_analysis(response_text: str) -> ImageAnalysis:
    """Parse the structured AI response into an ImageAnalysis object."""
    image_type = "unknown"
    ocr_text = ""
    description = ""
    confidence = 0.5

    lines = response_text.strip().split("\n")
    section = None
    buffer: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("IMAGE_TYPE:"):
            image_type = stripped.replace("IMAGE_TYPE:", "").strip().lower()
        elif stripped.startswith("CONFIDENCE:"):
            try:
                confidence = float(stripped.replace("CONFIDENCE:", "").strip())
            except ValueError:
                confidence = 0.5
        elif stripped == "OCR_TEXT:" or stripped.startswith("OCR_TEXT:"):
            section = "ocr"
            remainder = stripped.replace("OCR_TEXT:", "").strip()
            if remainder:
                buffer.append(remainder)
        elif stripped == "END_OCR":
            ocr_text = "\n".join(buffer).strip()
            if ocr_text.upper() == "NONE":
                ocr_text = ""
            buffer = []
            section = None
        elif stripped == "DESCRIPTION:" or stripped.startswith("DESCRIPTION:"):
            section = "desc"
            remainder = stripped.replace("DESCRIPTION:", "").strip()
            if remainder:
                buffer.append(remainder)
        elif stripped == "END_DESCRIPTION":
            description = "\n".join(buffer).strip()
            buffer = []
            section = None
        elif section == "ocr":
            buffer.append(line)
        elif section == "desc":
            buffer.append(line)

    # If parsing didn't find END markers, use remaining buffer
    if section == "ocr" and buffer:
        ocr_text = "\n".join(buffer).strip()
    elif section == "desc" and buffer:
        description = "\n".join(buffer).strip()

    return ImageAnalysis(
        location="",
        ocr_text=ocr_text,
        description=description,
        image_type=image_type,
        confidence=confidence,
    )


def format_image_block(analysis: ImageAnalysis) -> str:
    """Format an image analysis into a structured markdown block.

    This produces a block that LLMs can easily parse and understand,
    clearly marking where an image was and what it contained.
    """
    parts = [f"<!-- IMAGE_START: {analysis.location} -->"]
    parts.append(f"> **[Embedded Image]** _{analysis.image_type}_")
    parts.append(f"> **Location:** {analysis.location}")

    if analysis.ocr_text:
        parts.append(">")
        parts.append("> **Text extracted from image (OCR):**")
        for line in analysis.ocr_text.split("\n"):
            parts.append(f"> {line}")

    if analysis.description:
        parts.append(">")
        parts.append("> **Image description:**")
        for line in analysis.description.split("\n"):
            parts.append(f"> {line}")

    parts.append(f">")
    parts.append(f"> _AI confidence: {analysis.confidence:.0%}_")
    parts.append(f"<!-- IMAGE_END: {analysis.location} -->")

    return "\n".join(parts)


# --- Excel Image Extraction ---

def extract_excel_images(source_path: Path) -> list[tuple[str, bytes]]:
    """Extract all embedded images from an Excel workbook.

    Returns list of (location_reference, image_bytes) tuples.
    """
    from openpyxl import load_workbook

    results: list[tuple[str, bytes]] = []
    wb = load_workbook(source_path, data_only=True)

    for ws in wb.worksheets:
        if not hasattr(ws, "_images") or not ws._images:
            continue
        for idx, img in enumerate(ws._images):
            try:
                # Get image data
                if hasattr(img, "_data"):
                    image_data = img._data()
                elif hasattr(img, "image") and hasattr(img.image, "getvalue"):
                    image_data = img.image.getvalue()
                else:
                    continue

                # Get cell reference
                cell_ref = f"image_{idx + 1}"
                if hasattr(img, "anchor") and hasattr(img.anchor, "_from"):
                    from_cell = img.anchor._from
                    if hasattr(from_cell, "col") and hasattr(from_cell, "row"):
                        col_letter = chr(65 + min(from_cell.col, 25))
                        cell_ref = f"{col_letter}{from_cell.row + 1}"

                location = f"{ws.title}/{cell_ref}"
                results.append((location, image_data))
            except Exception:
                continue

    wb.close()
    return results


# --- PDF Image Extraction ---

def extract_pdf_images(source_path: Path) -> list[tuple[str, bytes]]:
    """Extract embedded images from a PDF file.

    Returns list of (location_reference, image_bytes) tuples.
    """
    results: list[tuple[str, bytes]] = []

    try:
        import pdfplumber
        from PIL import Image as PILImage

        with pdfplumber.open(str(source_path)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                if not hasattr(page, "images") or not page.images:
                    continue

                for img_idx, img_dict in enumerate(page.images):
                    try:
                        # Try to extract image from page
                        if "stream" in img_dict and hasattr(img_dict["stream"], "get_data"):
                            img_bytes = img_dict["stream"].get_data()
                            try:
                                pil_img = PILImage.open(io.BytesIO(img_bytes))
                                if pil_img.mode not in ("RGB", "L"):
                                    pil_img = pil_img.convert("RGB")
                                buf = io.BytesIO()
                                pil_img.save(buf, format="PNG")
                                results.append((f"Page {page_num}/image {img_idx + 1}", buf.getvalue()))
                            except Exception:
                                pass
                        else:
                            # Fallback: crop the region from page
                            x0 = img_dict.get("x0", 0)
                            y0 = img_dict.get("top", 0)
                            x1 = img_dict.get("x1", 0)
                            y1 = img_dict.get("bottom", 0)

                            if x1 > x0 and y1 > y0:
                                cropped = page.within_bbox((x0, y0, x1, y1))
                                page_img = cropped.to_image(resolution=150)
                                buf = io.BytesIO()
                                page_img.original.save(buf, format="PNG")
                                results.append((f"Page {page_num}/image {img_idx + 1}", buf.getvalue()))
                    except Exception:
                        continue
    except ImportError:
        pass  # pdfplumber not available
    except Exception:
        pass

    return results


# --- Main Plugin Functions ---

def analyze_document_images(
    source_path: Path, extension: str
) -> "tuple[list[ImageAnalysis], AICostEstimate | None]":
    """Extract and analyze all images in a document.

    Returns ``(analyses, cost_estimate)``.  ``cost_estimate`` is ``None`` when
    no AI calls were made (no images found or plugin not configured).
    """
    if not is_plugin_available():
        return [], None

    real_client = _get_azure_client()
    tracker = _TokenTracker(real_client, AZURE_CHAT_DEPLOYMENT or "gpt-4o")
    analyses: list[ImageAnalysis] = []

    # Extract images based on file type
    images: list[tuple[str, bytes]] = []
    if extension in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        images = extract_excel_images(source_path)
    elif extension == ".pdf":
        images = extract_pdf_images(source_path)

    # Analyze each image
    for location, image_bytes in images:
        analysis = _analyze_image(tracker, image_bytes, context=f"{source_path.name} at {location}")
        analysis.location = location
        analyses.append(analysis)

    locations = [a.location for a in analyses]
    cost = tracker.build_cost_estimate(locations)
    return analyses, cost


def convert_with_plugin(source_path: Path) -> "tuple[str, AICostEstimate | None]":
    """Convert a file using MarkItDown with Azure OpenAI LLM for image descriptions.

    Returns ``(markdown_text, cost_estimate)``.

    This enables:
    - Image description in DOCX/PPTX files
    - OCR-like capability for scanned PDFs (if markitdown-ocr plugin installed)
    - Better handling of embedded charts/diagrams
    """
    if not is_plugin_available():
        raise RuntimeError(
            "Azure OpenAI plugin is not configured. "
            "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env"
        )

    try:
        from markitdown import MarkItDown
    except ImportError:
        raise RuntimeError("MarkItDown is not available.")

    tracker = _TokenTracker(_get_azure_client(), AZURE_CHAT_DEPLOYMENT or "gpt-4o")

    md = MarkItDown(
        llm_client=tracker,
        llm_model=AZURE_CHAT_DEPLOYMENT,
        llm_prompt=(
            "Analyze this image for a knowledge base. Provide:\n"
            "1. ALL text visible in the image (OCR)\n"
            "2. A detailed description of visual elements (charts, diagrams, tables)\n"
            "3. Key data points and relationships shown\n"
            "Format: First the extracted text, then '---', then the description."
        ),
    )

    result = md.convert(str(source_path))
    locations = [f"MarkItDown LLM call {i + 1}" for i in range(len(tracker._calls))]
    cost = tracker.build_cost_estimate(locations)
    return result.text_content.strip(), cost

