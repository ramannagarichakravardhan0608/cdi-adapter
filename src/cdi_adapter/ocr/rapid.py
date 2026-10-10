"""Printed-text OCR with RapidOCR on ONNX Runtime, CPU (ARCHITECTURE §5 S3).

RapidOCR returns ``[quad, text, score]`` per line; a line is kept if ``score >= CDI_OCR_MIN_CONF``
(0.30); the quad becomes ``bbox [x0, y0, x1, y1]`` and the polygon is retained.
``order_reading`` sorts lines into natural top-to-bottom, left-to-right order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from statistics import median
from typing import Any

from cdi_adapter import _cpu
from cdi_adapter.config import get_settings

ENGINE = "rapidocr"


@dataclass
class OcrLine:
    text: str
    bbox: list[int]
    conf: float
    polygon: list[list[float]] | None = None
    reading_order: int = 0
    block_type: str = "line"
    extra: dict[str, Any] = field(default_factory=dict)


def quad_to_bbox(quad) -> list[int]:
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return [int(min(xs)), int(min(ys)), int(round(max(xs))), int(round(max(ys)))]


def order_reading(lines: list[OcrLine]) -> list[OcrLine]:
    """Sort by horizontal band ``round(y_center / (0.7 * median_height))`` then ``x0``."""
    if not lines:
        return []
    heights = [max(1, ln.bbox[3] - ln.bbox[1]) for ln in lines]
    band_h = 0.7 * median(heights)

    def key(ln: OcrLine) -> tuple[int, int]:
        y_center = (ln.bbox[1] + ln.bbox[3]) / 2
        return (round(y_center / band_h), ln.bbox[0])

    ordered = sorted(lines, key=key)
    for i, ln in enumerate(ordered):
        ln.reading_order = i
    return ordered


@lru_cache
def get_engine():
    from rapidocr_onnxruntime import RapidOCR

    threads = _cpu.per_worker_threads(_cpu.cpu_budget(), get_settings().job_max_workers)
    try:
        return RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
    except TypeError:  # older RapidOCR without the thread kwargs
        return RapidOCR()


def lines_from_result(result, min_conf: float) -> list[OcrLine]:
    """Convert raw RapidOCR output (``[[quad, text, score], ...]`` or None) to kept lines."""
    out: list[OcrLine] = []
    for quad, text, score in result or []:
        score = float(score)
        if score < min_conf or not str(text).strip():
            continue
        out.append(
            OcrLine(
                text=str(text).strip(),
                bbox=quad_to_bbox(quad),
                conf=round(score, 4),
                polygon=[[float(x), float(y)] for x, y in quad],
            )
        )
    return order_reading(out)


def read_here(png: bytes, *, min_conf: float | None = None) -> list[OcrLine]:
    """Read the page in this process (what the OCR host itself does)."""
    result, _elapsed = get_engine()(png)
    return lines_from_result(result, get_settings().ocr_min_conf if min_conf is None else min_conf)


def read_on_host(png: bytes, url: str, *, min_conf: float | None = None,
                 timeout: float = 120.0) -> list[OcrLine]:  # fmt: skip
    """Have the OCR host read the page (``ocrhost/app.py``, POST /ocr/rapid). The lines come
    back as it read them: same text, boxes, confidences and reading order as ``read_here``."""
    import httpx

    params = {} if min_conf is None else {"min_conf": min_conf}
    response = httpx.post(url.rstrip("/") + "/ocr/rapid", content=png, params=params,
                          headers={"content-type": "application/octet-stream"},
                          timeout=timeout)  # fmt: skip
    response.raise_for_status()
    return [OcrLine(text=ln["text"], bbox=[int(v) for v in ln["bbox"]], conf=float(ln["conf"]),
                    polygon=ln.get("polygon"), reading_order=int(ln["reading_order"]))
            for ln in response.json()["lines"]]  # fmt: skip


def run_rapidocr(png: bytes, *, min_conf: float | None = None) -> list[OcrLine]:
    """Every printed line of the page with its position and confidence (RD-S1). Read in this
    process, or by the OCR host when ``CDI_OCR_HOST_URL`` names one."""
    url = get_settings().ocr_host_url
    if url:
        import httpx

        try:
            return read_on_host(png, url, min_conf=min_conf)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # RD-S2: a host that is down must not block the page. A page it refused as
            # unreadable (4xx) is unreadable here too, and says so itself.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            import structlog

            structlog.get_logger(__name__).warning(
                "ocr_host_unreachable", error=type(exc).__name__, action="reading in-process"
            )
    return read_here(png, min_conf=min_conf)
