"""The training run (SW-S3): the checks, the smoke test, the loop, the report.

Every run, smoke test or real, goes the same way:

1. **licence** - the base model must be in the model registry (SW-S2) with its licence text
   saved. Otherwise training is refused before anything is loaded (AC4);
2. **data** - the dataset may be trained on (``dataset.load``) and has examples of the role;
3. **format** - an adapter knows the base model's family, and every chosen example can be
   written in its format - checked on ALL of them before the model is loaded;
4. **train** - LoRA (or QLoRA) at the pinned revision, a fixed number of steps;
5. **report** - seconds per step and the GPU memory it took (AC3), the losses, and every
   setting, saved beside the result so two runs can be compared (AC2).

A failure at any step ends the run with the reason, one of ``licence``, ``data``, ``format``,
``memory`` or ``other``, and its detail (AC1). The smoke test is the same run, short: 50
examples and 20 steps, to find those problems before the GPU is rented for hours.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import platform
import re
import statistics
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cdi_adapter.finetune import adapters, dataset

SMOKE = {"examples": 50, "steps": 20}
REASONS = ("licence", "data", "format", "memory", "other")


@dataclasses.dataclass(frozen=True)
class RunConfig:
    base: str
    role: str
    data: str
    seed: int = 7
    revision: str | None = None  # None: the revision the model registry pins
    steps: int = 200
    examples: int | None = None  # None: every example of the role
    batch: int = 1
    lr: float = 1e-4
    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    qlora: bool = False
    smoke: bool = False

    def as_smoke(self) -> RunConfig:
        return dataclasses.replace(
            self, smoke=True, steps=SMOKE["steps"], examples=SMOKE["examples"]
        )

    def settings(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class Refused(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def licence_check(base: str) -> Any:
    """The base model's registry entry; :class:`Refused` (licence) when it has no licence entry
    or no licence text saved. SW-S7 asks the same before any swap."""
    from cdi_adapter.modelops import catalog

    entry = catalog.find(base)
    if entry is None:
        raise Refused(
            "licence",
            f"{base} has no licence entry: it is not in the model registry "
            "(models/registry.json), so it may not be trained",
        )
    path = entry.licence_text
    if not (path and path.is_file() and path.read_text(encoding="utf-8").strip()):
        raise Refused(
            "licence",
            f"{base} has no licence text saved in the model registry "
            f"({entry.licence.get('name') or 'no licence named'}); it may "
            "not be trained until it is",
        )
    why = catalog.licence_problem(entry)  # SW-S7: a licence the policy blocks is not trained
    if why:
        raise Refused("licence", f"{base}: {why}; it may not be trained")
    return entry


def reason_of(exc: BaseException) -> str:
    if isinstance(exc, Refused):
        return exc.reason
    if isinstance(exc, (dataset.DataFormatError, adapters.FormatError)):
        return "format"
    if isinstance(exc, dataset.DataRefused):
        return "data"
    said = f"{type(exc).__name__} {exc}".lower()
    if "outofmemory" in said.replace(" ", "") or "out of memory" in said:
        return "memory"
    return "other"


def _git() -> str | None:
    try:
        root = Path(__file__).resolve().parents[3]
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _environment() -> dict[str, Any]:
    from cdi_adapter.components import version_of

    return {
        "python": platform.python_version(),
        "code": _git(),
        **{p: version_of(p) for p in ("torch", "transformers", "peft", "bitsandbytes")},
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def run(
    cfg: RunConfig,
    out: Path,
    *,
    adapter: Any = None,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> dict[str, Any]:
    """One training run (or smoke test). Never raises for a training problem: the report says
    what happened. The report and the settings are written to ``out/<run id>/``."""
    started = now()
    report: dict[str, Any] = {
        "kind": "smoke test" if cfg.smoke else "training",
        "base": cfg.base,
        "role": cfg.role,
        "revision": cfg.revision,
        "seed": cfg.seed,
        "settings": cfg.settings(),
        "environment": _environment(),
        "started": started.isoformat(timespec="seconds"),
        "data_version": None,
        "examples": 0,
        "steps_done": 0,
        "losses": [],
        "seconds_per_step": None,
        "gpu_memory": None,
        "outcome": "failed",
        "reason": None,
        "detail": None,
    }
    timings: list[float] = []
    model = adapter
    try:
        entry = licence_check(cfg.base)
        revision = cfg.revision or entry.revision
        report["revision"] = revision
        data = dataset.load(Path(cfg.data))
        report["data_version"] = data.version
        report["data_class"] = data.data_class
        if cfg.role == "extraction":  # trained with the words it will be asked with
            from cdi_adapter.extract.prompt import prompt_version

            report["prompt_version"] = prompt_version("prescription")
            report["answer_schema"] = data.schema
        chosen = dataset.select(data, cfg.role, cfg.examples, cfg.seed)
        report["examples"] = len(chosen)
        model = model or adapters.for_model(cfg.base, cfg.role)
        report["adapter"] = model.family
        for example in chosen:  # every example in the base model's own format, before loading
            model.render(example)
        model.prepare(dataclasses.replace(cfg, revision=revision))
        for i in range(cfg.steps):
            batch = [chosen[(i * cfg.batch + j) % len(chosen)] for j in range(cfg.batch)]
            t0 = clock()
            loss = model.step(batch)
            timings.append(clock() - t0)
            report["losses"].append(round(float(loss), 6))
            report["steps_done"] = i + 1
        report["outcome"] = "finished"
        if not cfg.smoke:  # the LoRA layers, beside the report that says how they were made
            folder = _folder(out, cfg, report, started) / "adapter"
            folder.mkdir(parents=True, exist_ok=True)
            model.save(folder)
            report["adapter_files"] = str(folder)
    except Exception as exc:  # noqa: BLE001 - every failure is reported with its reason
        report["reason"] = reason_of(exc)
        report["detail"] = f"{type(exc).__name__}: {exc}"[:500]
    if timings:
        warm = timings[1:] or timings  # the first step also builds kernels: shown apart
        report["seconds_per_step"] = {
            "mean": round(statistics.fmean(warm), 4),
            "median": round(statistics.median(warm), 4),
            "max": round(max(warm), 4),
            "first_step": round(timings[0], 4),
            "steps": len(timings),
        }
    peak = model.peak_memory() if model is not None and hasattr(model, "peak_memory") else None
    report["gpu_memory"] = (
        {"peak_bytes": peak, "peak_gib": round(peak / 2**30, 2)}
        if peak is not None
        else {"peak_bytes": None, "peak_gib": None, "note": "not measured: no GPU"}
    )
    report["finished"] = now().isoformat(timespec="seconds")
    folder = _folder(out, cfg, report, started)
    (folder / "settings.json").write_text(
        json.dumps(cfg.settings(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (folder / "report.json").write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    (folder / "report.md").write_text(as_markdown(report), encoding="utf-8")
    return report


def _folder(out: Path, cfg: RunConfig, report: dict[str, Any], started: dt.datetime) -> Path:
    """The run's own folder, made once: a second run started in the same second gets ``-2``,
    so no run ever writes over another's report."""
    if not report.get("run_id"):
        base = _run_id(cfg, report, started)
        name, n = base, 1
        while (out / name).exists():
            n += 1
            name = f"{base}-{n}"
        report["run_id"] = name
    folder = out / str(report["run_id"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _run_id(cfg: RunConfig, report: dict[str, Any], started: dt.datetime) -> str:
    return "-".join(
        p
        for p in (
            started.strftime("%Y%m%dT%H%M%S"),
            "smoke" if cfg.smoke else "train",
            cfg.role,
            _slug(cfg.base),
            (report.get("data_version") or "nodata")[:8],
            f"s{cfg.seed}",
        )
        if p
    )


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two run reports side by side. ``comparable``: the same base, revision, data version,
    seed and settings - then any difference in the results is the runs' own."""
    keys = ("base", "revision", "role", "data_version", "seed")
    differences = [f"{k}: {a.get(k)!r} / {b.get(k)!r}" for k in keys if a.get(k) != b.get(k)]
    sa, sb = a.get("settings") or {}, b.get("settings") or {}
    differences += [
        f"settings.{k}: {sa.get(k)!r} / {sb.get(k)!r}"
        for k in sorted(set(sa) | set(sb))
        if sa.get(k) != sb.get(k)
    ]
    la, lb = a.get("losses") or [], b.get("losses") or []
    spa, spb = (a.get("seconds_per_step") or {}), (b.get("seconds_per_step") or {})
    return {
        "comparable": not differences,
        "differences": differences,
        "same_losses": la == lb,
        "final_loss": [la[-1] if la else None, lb[-1] if lb else None],
        "seconds_per_step": [spa.get("mean"), spb.get("mean")],
        "gpu_memory_gib": [
            (a.get("gpu_memory") or {}).get("peak_gib"),
            (b.get("gpu_memory") or {}).get("peak_gib"),
        ],
    }


def as_markdown(report: dict[str, Any]) -> str:
    sps = report.get("seconds_per_step") or {}
    mem = report.get("gpu_memory") or {}
    out = [
        f"# {report['kind'].capitalize()}: {report['base']} ({report['role']})",
        "",
        f"- **Outcome:** {report['outcome']}"
        + (f" - reason **{report['reason']}**: {report['detail']}" if report["reason"] else ""),
        f"- **Base model:** {report['base']} at revision {report.get('revision') or '-'}",
        f"- **Data version:** {report.get('data_version') or '-'}"
        f" ({report.get('examples', 0)} examples), seed {report['seed']}",
        f"- **Steps:** {report['steps_done']} of {report['settings']['steps']}",
        f"- **Seconds per step:** {sps.get('mean', '-')} on average, {sps.get('median', '-')} "
        f"median, {sps.get('max', '-')} at most (first step {sps.get('first_step', '-')})",
        f"- **GPU memory:** {mem.get('peak_gib') if mem.get('peak_gib') is not None else '-'} "
        f"GiB at peak" + (f" ({mem['note']})" if mem.get("note") else ""),
        f"- **Final loss:** {report['losses'][-1] if report['losses'] else '-'}",
        "",
        "Settings:",
        "",
        "```json",
        json.dumps(report["settings"], indent=1, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(out)
