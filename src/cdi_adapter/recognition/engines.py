"""The two handwriting readers, each on its own (backlog RD-S2; ARCHITECTURE §15.3).

A handwritten line is cut from the page as one crop (IM-S3, ``regions.py``) and that same crop
is given to two readers that have nothing in common:

* :class:`TrOCREngine` - TrOCR, a handwriting line reader (``CDI_TROCR_MODEL_ID``,
  ``microsoft/trocr-base-handwritten``). It runs on the CPU, in this process or on the OCR host
  (``ocrhost/app.py``, POST /ocr/trocr).
* :class:`QwenLineEngine` - Qwen, the picture-reading model behind the model gateway (GPU).

**Neither sees the other's answer.** A reader is handed the crop and nothing else: ``read`` has
no parameter that could carry another reading, and Qwen's prompt is one fixed sentence
(:data:`QWEN_PROMPT`) that is never formatted with anything. That is what lets one reader's
mistake be caught by the other.

**Not yet trusted.** The TrOCR checkpoint is an English one trained on the IAM set - general
handwriting, not prescriptions - and no reader here has been measured on real prescription
handwriting (``recognition/htr_bench.py`` is the harness for that). Its licence is a review
item too: the weights are marked MIT but were tuned on IAM, which is non-commercial research
data (ENT-S1). Reading with two readers is therefore off unless ``CDI_TWO_READERS`` is set.
"""

from __future__ import annotations

import hashlib
import io
import math
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from cdi_adapter.logging import get_logger
from cdi_adapter.ml.client import MLClient
from cdi_adapter.ocr import vlm_ocr

log = get_logger(__name__)

TROCR = "trocr"
QWEN = "vlm"  # the name Qwen's readings have carried in ``ocr_observation`` since E0-S5

# The whole of what Qwen is told. A constant: nothing is ever added to it.
QWEN_PROMPT = vlm_ocr.LINE_PROMPT


class EngineDown(RuntimeError):
    """A reader could not be reached, failed, or took too long. The line is then read by the
    other reader alone and goes to review; the page is never held up by it."""


@dataclass(frozen=True)
class Reading:
    """One reader's reading of one crop."""

    engine: str  # TROCR or QWEN
    text: str
    confidence: float | None  # the reader's own, 0..1; None when it gives none
    engine_version: str  # the checkpoint or model that read it
    seconds: float
    prompt_hash: str | None = None  # Qwen only: the fingerprint of the prompt it was given


class LineEngine(Protocol):
    name: str

    def read(self, crop_png: bytes) -> Reading: ...


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- TrOCR


@lru_cache(maxsize=2)
def load_trocr(model_id: str, revision: str | None = None) -> tuple[Any, Any, Any]:
    """What turns the crop into pixels, what turns tokens into text, and the model - on the
    CPU. Loaded once: it is several hundred megabytes."""
    from transformers import (
        AutoImageProcessor,
        AutoTokenizer,
        RobertaTokenizer,
        VisionEncoderDecoderModel,
    )

    # SW-S2: the pinned revision only, and no code that comes with the model
    pin: dict[str, Any] = {"revision": revision, "trust_remote_code": False} if revision else {
        "trust_remote_code": False}  # fmt: skip
    pixels = AutoImageProcessor.from_pretrained(model_id, **pin)
    try:
        words = AutoTokenizer.from_pretrained(model_id, **pin)
    except ValueError:
        # the handwritten checkpoints ship a vocabulary and merges but no tokenizer file, which
        # this version of transformers will not build on its own (seen on the pod, 2026-10-09)
        words = RobertaTokenizer.from_pretrained(model_id, **pin)
    model = VisionEncoderDecoderModel.from_pretrained(model_id, **pin)
    # SW-S7 AC4: the files that were loaded are the files the registry checked; a mismatch
    # (ModelMismatch) stops the reader before it reads a single line
    from cdi_adapter.modelops import catalog

    catalog.verify_loaded(model_id, revision)
    model.to("cpu")  # type: ignore[arg-type]
    model.eval()
    return pixels, words, model


_trocr_lock = threading.Lock()  # one line at a time: the model is not shared between threads


def pinned_revision(model_id: str) -> str | None:
    """SW-S2: the exact revision the settings pin for this checkpoint (the champion's, when
    one is set - modelops.catalog.apply). Another checkpoint named directly is not pinned."""
    from cdi_adapter.config import get_settings

    s = get_settings()
    return s.trocr_model_revision or None if model_id == s.trocr_model_id else None


def trocr_read(
    crop_png: bytes, model_id: str, *, max_new_tokens: int = 64
) -> tuple[str, float | None]:
    """Read one line crop with TrOCR here, on the CPU: the text, and how sure the model was -
    the geometric mean of the probability of each token it chose."""
    import torch
    from PIL import Image

    to_pixels, words, model = load_trocr(model_id, pinned_revision(model_id))
    with Image.open(io.BytesIO(crop_png)) as im:
        pixels = to_pixels(images=im.convert("RGB"), return_tensors="pt").pixel_values
    with _trocr_lock, torch.no_grad():
        out = model.generate(pixels, max_new_tokens=max_new_tokens, num_beams=1, do_sample=False,
                             output_scores=True, return_dict_in_generate=True)  # fmt: skip
    text = _one_line(words.batch_decode(out.sequences, skip_special_tokens=True)[0])
    confidence: float | None = None
    if out.scores:
        chosen = out.sequences[0, -len(out.scores) :]
        logs = [float(torch.log_softmax(step[0], dim=-1)[token])
                for step, token in zip(out.scores, chosen, strict=True)]  # fmt: skip
        confidence = round(min(1.0, max(0.0, math.exp(sum(logs) / len(logs)))), 4)
    return text, confidence


class TrOCREngine:
    """TrOCR on one line crop. With ``url`` it asks the OCR host (the CPU host); without, it
    reads in this process. Either way it is given the crop and nothing else."""

    name = TROCR

    def __init__(self, model_id: str, *, url: str = "", timeout: float = 20.0) -> None:
        self.model_id, self.url, self.timeout = model_id, url.rstrip("/"), timeout

    def read(self, crop_png: bytes) -> Reading:
        started = time.perf_counter()
        try:
            if self.url:
                text, confidence, version = self._ask_host(crop_png)
            else:
                text, confidence = trocr_read(crop_png, self.model_id)
                version = self.model_id
        except EngineDown:
            raise
        except Exception as exc:
            raise EngineDown(f"TrOCR could not read the line ({type(exc).__name__})") from exc
        return Reading(TROCR, text, confidence, version, round(time.perf_counter() - started, 3))

    def _ask_host(self, crop_png: bytes) -> tuple[str, float | None, str]:
        import httpx

        try:
            response = httpx.post(self.url + "/ocr/trocr", content=crop_png, timeout=self.timeout,
                                  headers={"content-type": "application/octet-stream"})  # fmt: skip
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise EngineDown(f"the TrOCR host did not answer ({type(exc).__name__})") from exc
        return _one_line(body["text"]), body.get("confidence"), str(body["engine_version"])


def repad(crop_png: bytes, extra_px: int = 24) -> bytes:
    """The same crop with more paper round it (RD-S3, self-consistency): the line is unchanged,
    only its margin is not, so a reader that reads it differently was not sure of it."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(crop_png)) as im:
        rgb = im.convert("RGB")
    out = io.BytesIO()
    ImageOps.expand(rgb, border=extra_px, fill=(255, 255, 255)).save(out, format="PNG")
    return out.getvalue()


# ---------------------------------------------------------------- Qwen


class QwenLineEngine:
    """Qwen on one line crop, through the model gateway. The prompt is :data:`QWEN_PROMPT`,
    whole and unchanged, for every line."""

    name = QWEN

    def __init__(self, client: MLClient, *, max_tokens: int = 96) -> None:
        self.client, self.max_tokens = client, max_tokens

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(QWEN_PROMPT.encode()).hexdigest()

    def read(self, crop_png: bytes) -> Reading:
        started = time.perf_counter()
        try:
            text = _one_line(
                self.client.generate(QWEN_PROMPT, crop_png, max_tokens=self.max_tokens)
            )
        except Exception as exc:
            raise EngineDown(f"Qwen could not read the line ({type(exc).__name__})") from exc
        # the gateway returns no token probabilities: every Qwen reading carries the one fixed
        # value the pipeline has always given it (vlm_ocr.VLM_OCR_CONF), which is not a measure
        return Reading(QWEN, text, vlm_ocr.VLM_OCR_CONF, str(self.client.model),
                       round(time.perf_counter() - started, 3), self.prompt_hash)  # fmt: skip
