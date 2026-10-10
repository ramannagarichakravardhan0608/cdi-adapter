"""The OCR host: the CPU readers behind one small HTTP service (backlog epic RD).

POST /ocr/rapid   body = the page image (PNG or JPEG bytes)
                  -> { engine, engine_version, lines: [{ text, bbox, polygon, conf,
                       reading_order }], seconds }
POST /ocr/trocr   body = ONE handwritten line, cut from the page (RD-S2)
                  -> { engine, engine_version, text, confidence, seconds }
GET  /healthz     -> { status, engine, engine_version, loaded, device, trocr }

TrOCR (``recognition/engines.py``) is given the crop and nothing else: the route takes no other
input, so no other reader's answer can reach it.

RapidOCR (``ocr/rapid.py``) reads printed text on ordinary CPUs and uses no GPU memory, so it has
its own process on localhost:``CDI_OCR_HOST_PORT`` (8079) instead of a place in the model gateway
(``mlserve``, port 8077), which holds the GPU. The pipeline reads in-process unless
``CDI_OCR_HOST_URL`` points it here.

Loopback only (``__main__``): the body is a page of a patient's document, and nothing outside
the host has a reason to call this. A page is never logged - only how many lines were read.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from cdi_adapter.config import get_settings
from cdi_adapter.logging import get_logger
from cdi_adapter.ocr import rapid
from cdi_adapter.recognition import engines

log = get_logger(__name__)


class Line(BaseModel):
    text: str
    bbox: list[int]  # [x0, y0, x1, y1] in the image's pixels
    polygon: list[list[float]] | None
    conf: float
    reading_order: int


class TrocrResponse(BaseModel):
    engine: str
    engine_version: str  # the checkpoint that read it
    text: str
    confidence: float | None
    seconds: float


class RapidResponse(BaseModel):
    engine: str
    engine_version: str
    lines: list[Line]
    seconds: float


def engine_version() -> str:
    from importlib import metadata

    try:
        return "rapidocr-onnxruntime " + metadata.version("rapidocr-onnxruntime")
    except metadata.PackageNotFoundError:
        return "rapidocr-onnxruntime"


def create_app() -> FastAPI:
    app = FastAPI(title="cdi-ocrhost")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "engine": rapid.ENGINE, "engine_version": engine_version(),
                "loaded": rapid.get_engine.cache_info().currsize > 0, "device": "cpu",
                "trocr": {"model": get_settings().trocr_model_id,
                          "loaded": engines.load_trocr.cache_info().currsize > 0}}  # fmt: skip

    @app.post("/ocr/trocr", response_model=TrocrResponse)
    async def ocr_trocr(request: Request) -> TrocrResponse:
        settings = get_settings()
        crop = await request.body()
        if not crop:
            raise HTTPException(400, "send the line crop as the request body")
        if len(crop) > settings.ocr_host_max_bytes:
            raise HTTPException(
                413, f"the image is larger than {settings.ocr_host_max_bytes} bytes"
            )
        started = time.perf_counter()
        try:
            text, confidence = await run_in_threadpool(
                engines.trocr_read, crop, settings.trocr_model_id
            )
        except Exception as exc:
            log.error("ocr_trocr_failed", error=type(exc).__name__)
            raise HTTPException(422, "the line could not be read") from exc
        seconds = round(time.perf_counter() - started, 3)
        log.info("ocr_trocr", seconds=seconds, bytes=len(crop), characters=len(text))
        return TrocrResponse(engine=engines.TROCR, engine_version=settings.trocr_model_id,
                             text=text, confidence=confidence, seconds=seconds)  # fmt: skip

    @app.post("/ocr/rapid", response_model=RapidResponse)
    async def ocr_rapid(request: Request, min_conf: float | None = None) -> RapidResponse:
        settings = get_settings()
        image = await request.body()
        if not image:
            raise HTTPException(400, "send the page image as the request body")
        if len(image) > settings.ocr_host_max_bytes:
            raise HTTPException(
                413, f"the image is larger than {settings.ocr_host_max_bytes} bytes"
            )
        if min_conf is not None and not 0 <= min_conf <= 1:
            raise HTTPException(422, "min_conf is between 0 and 1")
        started = time.perf_counter()
        try:
            # reading is CPU work: off the event loop, so /healthz still answers
            lines = await run_in_threadpool(rapid.read_here, image, min_conf=min_conf)
        except Exception as exc:
            log.error("ocr_rapid_failed", error=type(exc).__name__)
            raise HTTPException(422, "the image could not be read") from exc
        seconds = round(time.perf_counter() - started, 3)
        log.info("ocr_rapid", lines=len(lines), seconds=seconds, bytes=len(image))
        return RapidResponse(
            engine=rapid.ENGINE, engine_version=engine_version(), seconds=seconds,
            lines=[Line(text=ln.text, bbox=ln.bbox, polygon=ln.polygon, conf=ln.conf,
                        reading_order=ln.reading_order) for ln in lines])  # fmt: skip

    return app


app = create_app()
