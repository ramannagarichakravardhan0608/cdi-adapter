"""One adapter per model family (SW-S3; TR3: model_adapters).

An adapter knows the three things that differ between base models:

* ``render(example)`` - the example written the way THIS family is trained: its chat format
  for a vision-language model, pixels and label text for an encoder-decoder line reader.
  Built at training time from the model-neutral example; the data itself never changes;
* ``prepare(cfg)`` - load the base model at its pinned revision, safely, and put LoRA (or
  QLoRA) layers on it;
* ``step(batch)`` / ``peak_memory()`` / ``save(folder)``.

A base model of a family no adapter knows fails the smoke test with the reason ``format``.
Adding a family is one class and one ``register``; nothing else changes.

``prepare`` and ``step`` need torch, transformers and peft, and a GPU for anything but the
smallest models; they are imported only there.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from cdi_adapter.finetune.dataset import Example

LINE_PROMPT = (
    "Read the handwritten text in this picture exactly as written. Answer with the text only."
)


class FormatError(ValueError):
    """An example cannot be written in the base model's format, or no adapter knows the model."""


class ModelAdapter(Protocol):
    family: str
    roles: tuple[str, ...]

    def handles(self, model_id: str) -> bool: ...
    def render(self, example: Example) -> dict[str, Any]: ...
    def prepare(self, cfg: Any) -> None: ...
    def step(self, batch: list[Example]) -> float: ...
    def peak_memory(self) -> int | None: ...
    def save(self, folder: Any) -> None: ...


def _extraction_prompt() -> str:
    """The instruction an extraction example is trained with: the production wording, with no
    page lines in it (the model reads the picture itself). Trained with the same words it is
    asked with, so the prompt version in the run report is the one served."""
    from cdi_adapter.extract.prompt import build_extraction_prompt

    wording, _ = build_extraction_prompt("prescription", [])
    return str(wording)


def _peak_cuda() -> int | None:
    try:
        import torch

        return int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001 - no torch, no GPU: not measured
        return None


class _Torch:
    """What the two real adapters share: the optimizer, seeding and saving."""

    model: Any = None
    optimizer: Any = None

    def peak_memory(self) -> int | None:
        return _peak_cuda()

    def _seed(self, seed: int) -> None:  # pragma: no cover - needs torch
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
            torch.cuda.reset_peak_memory_stats()

    def _lora(self, cfg: Any, targets: list[str]) -> None:  # pragma: no cover - needs peft
        import torch
        from peft import LoraConfig, get_peft_model

        config = LoraConfig(
            r=cfg.rank,
            lora_alpha=cfg.alpha,
            lora_dropout=cfg.dropout,
            target_modules=targets,
            bias="none",
        )
        self.model = get_peft_model(self.model, config)
        params = [p for p in self.model.parameters() if p.requires_grad]
        self.optimizer = torch.optim.AdamW(params, lr=cfg.lr)
        self.model.train()

    def save(self, folder: Any) -> None:  # pragma: no cover - needs peft
        self.model.save_pretrained(str(folder))  # the LoRA layers only, not the base


class QwenVLAdapter(_Torch):
    """Qwen2-VL / Qwen2.5-VL: the page (or line) as an image in a chat, the answer as the
    assistant's turn. LoRA on the attention projections of the language model; the picture
    encoder stays frozen."""

    family = "qwen2-vl"
    roles = ("extraction", "line_reader")
    _pattern = re.compile(r"qwen2(\.5)?-vl", re.I)

    def handles(self, model_id: str) -> bool:
        return bool(self._pattern.search(model_id))

    def render(self, example: Example) -> dict[str, Any]:
        if example.role == "extraction":
            if not isinstance(example.target, dict):
                raise FormatError(f"{example.id}: an extraction target must be a JSON object")
            question, answer = (
                _extraction_prompt(),
                json.dumps(example.target, sort_keys=True, ensure_ascii=False),
            )
        else:
            question, answer = LINE_PROMPT, str(example.target)
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": str(example.image)},
                        {"type": "text", "text": question},
                    ],
                },
                {"role": "assistant", "content": [{"type": "text", "text": answer}]},
            ]
        }

    def prepare(self, cfg: Any) -> None:  # pragma: no cover - needs a GPU
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        from cdi_adapter.modelops import catalog

        self._seed(cfg.seed)
        kwargs: dict[str, Any] = {
            **catalog.load_kwargs(cfg.revision),
            "torch_dtype": torch.bfloat16,
            "device_map": "cuda",
        }
        if cfg.qlora:
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16
            )
        self.model = AutoModelForImageTextToText.from_pretrained(cfg.base, **kwargs)
        self.processor = AutoProcessor.from_pretrained(
            cfg.base, revision=cfg.revision, trust_remote_code=False
        )
        for name, p in self.model.named_parameters():  # the picture encoder stays as it is
            if "visual" in name:
                p.requires_grad_(False)
        self._lora(cfg, ["q_proj", "k_proj", "v_proj", "o_proj"])

    def step(self, batch: list[Example]) -> float:  # pragma: no cover - needs a GPU
        from PIL import Image

        rendered = [self.render(e) for e in batch]
        texts = [
            self.processor.apply_chat_template(r["messages"], tokenize=False) for r in rendered
        ]
        prompts = [
            self.processor.apply_chat_template(
                r["messages"][:1], tokenize=False, add_generation_prompt=True
            )
            for r in rendered
        ]
        images = [Image.open(e.image).convert("RGB") for e in batch]
        enc = self.processor(text=texts, images=images, return_tensors="pt", padding=True)
        labels = enc["input_ids"].clone()
        for i, prompt in enumerate(prompts):  # learn the answer only, not the question
            asked = len(self.processor(text=[prompt], images=[images[i]])["input_ids"][0])
            labels[i, :asked] = -100
        labels[enc["attention_mask"] == 0] = -100
        enc = {k: v.to(self.model.device) for k, v in enc.items()}
        loss = self.model(**enc, labels=labels.to(self.model.device)).loss
        loss.backward()
        self.optimizer.step()
        self.optimizer.zero_grad()
        return float(loss.item())


class TrOCRAdapter(_Torch):
    """TrOCR (a vision encoder and a text decoder): the line crop as pixels, its text as the
    labels. LoRA on the decoder's attention."""

    family = "trocr"
    roles = ("line_reader",)

    def handles(self, model_id: str) -> bool:
        return "trocr" in model_id.lower()

    def render(self, example: Example) -> dict[str, Any]:
        if example.role != "line_reader":
            raise FormatError(
                f"{example.id}: TrOCR reads lines; it cannot be trained on a {example.role} example"
            )
        return {"image": str(example.image), "text": str(example.target)}

    def prepare(self, cfg: Any) -> None:  # pragma: no cover - needs torch + peft
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        from cdi_adapter.modelops import catalog

        self._seed(cfg.seed)
        pin = catalog.load_kwargs(cfg.revision)
        self.processor = TrOCRProcessor.from_pretrained(cfg.base, revision=cfg.revision)
        self.model = VisionEncoderDecoderModel.from_pretrained(cfg.base, **pin)
        self.model.config.decoder_start_token_id = self.processor.tokenizer.cls_token_id
        self.model.config.pad_token_id = self.processor.tokenizer.pad_token_id
        self._lora(cfg, ["query", "value"])

    def step(self, batch: list[Example]) -> float:  # pragma: no cover - needs torch
        from PIL import Image

        pixels = self.processor(
            images=[Image.open(e.image).convert("RGB") for e in batch], return_tensors="pt"
        ).pixel_values
        labels = self.processor.tokenizer(
            [str(e.target) for e in batch], padding=True, return_tensors="pt"
        ).input_ids
        labels[labels == self.processor.tokenizer.pad_token_id] = -100
        loss = self.model(
            pixel_values=pixels.to(self.model.device), labels=labels.to(self.model.device)
        ).loss
        loss.backward()
        self.optimizer.step()
        self.optimizer.zero_grad()
        return float(loss.item())


_REGISTRY: list[Any] = []


def register(factory: Any) -> None:
    """Add a family: ``factory()`` makes an adapter. Tried before the earlier ones."""
    _REGISTRY.insert(0, factory)


def unregister(factory: Any) -> None:
    if factory in _REGISTRY:
        _REGISTRY.remove(factory)


register(TrOCRAdapter)
register(QwenVLAdapter)


def for_model(model_id: str, role: str) -> ModelAdapter:
    """The adapter that knows this base model, for this role. Raises :class:`FormatError`."""
    for factory in _REGISTRY:
        adapter = factory()
        if adapter.handles(model_id):
            if role not in adapter.roles:
                raise FormatError(
                    f"the {adapter.family} adapter trains {', '.join(adapter.roles)}, not {role}"
                )
            return adapter  # type: ignore[no-any-return]
    families = ", ".join(sorted({f().family for f in _REGISTRY}))
    raise FormatError(
        f"no adapter knows the model family of {model_id!r} (known: {families}); "
        "add one in cdi_adapter/finetune/adapters.py"
    )
