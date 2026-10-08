"""Handwriting transcription through the model gateway (ARCHITECTURE §5 S3).

Used for pages the classifier calls handwritten (``CDI_HANDWRITTEN_USES_VLM``). The VLM gives
text but no geometry, so every line carries the **page-level bbox** and a fixed confidence of
0.55 — low on purpose, so S6 never auto-trusts a fact that rests only on a transcription.
"""

from __future__ import annotations

from cdi_adapter.ml.client import MLClient
from cdi_adapter.ocr.rapid import OcrLine

ENGINE = "vlm"
VLM_OCR_CONF = 0.55

TRANSCRIBE_PROMPT = (
    "Transcribe every line of text on this scanned clinical page exactly as written, top to "
    "bottom, one line of output per line on the page. Do not summarise, correct or translate. "
    "Output plain text only."
)


# IM-S3 AC2: one line of writing, cut from the page to the crop standard. The prompt asks for
# that line and nothing else: a model shown a sliver of a page will otherwise explain it.
LINE_PROMPT = (
    "This picture is one line of writing cut from a scanned clinical page. Transcribe exactly "
    "what is written on it, on one line. Do not summarise, correct, translate or explain. If "
    "nothing can be read, output nothing. Output plain text only."
)


def read_line_crop(crop_png: bytes, client: MLClient, *, max_tokens: int = 96) -> str:
    """The handwriting reader on one line crop: the text of that line (one line, no trailing
    space), or an empty string when the model read nothing."""
    text = client.generate(LINE_PROMPT, crop_png, max_tokens=max_tokens)
    return " ".join(str(text or "").split())


def run_vlm_transcription(
    png: bytes, width: int, height: int, client: MLClient, *, max_tokens: int = 2048
) -> list[OcrLine]:
    text = client.generate(TRANSCRIBE_PROMPT, png, max_tokens=max_tokens)
    page_bbox = [0, 0, int(width), int(height)]
    lines = [t.strip() for t in text.splitlines() if t.strip()]
    return [
        OcrLine(text=t, bbox=list(page_bbox), conf=VLM_OCR_CONF, reading_order=i, block_type="page")
        for i, t in enumerate(lines)
    ]
