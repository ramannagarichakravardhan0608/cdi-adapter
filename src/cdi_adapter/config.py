"""Typed settings for every CDI-Adapter process (ARCHITECTURE §9).

Every value is overridable per environment with the ``CDI_`` prefix (env var or ``.env``).
The code default for the model backend is ``stub`` so tests and CI need no GPU; the pod sets
``CDI_MLSERVE_BACKEND=hf`` in its ``.env``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CDI_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # adapter DB / broker. Credentials (these four) come from the secrets store in any shared
    # deployment (backlog E1-S5); the defaults are local-dev only and `secretstore check` rejects
    # them. repr=False keeps them out of logs and tracebacks.
    database_url: str = Field(default="postgresql+psycopg://cdi:cdi@127.0.0.1:5432/cdi", repr=False)
    # E10-S6: the primary and its standby, e.g. "127.0.0.1:5432,127.0.0.1:5433". When set, the app
    # connects to whichever of them is writable (libpq target_session_attrs=read-write), so a
    # failover needs no change here. Empty = the host in the database URL only.
    database_hosts: str = ""
    # E10-S11: where snapshot.sh keeps the dumps + object copies, and the restore drill reports
    backup_dir: str = "/workspace/backup"
    dr_report_dir: str = "/workspace/logs/dr"
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", repr=False)
    # E9-S8: the Celery broker. Empty = redis_url, which is what every deployment uses today; an
    # amqp:// URL moves the broker to RabbitMQ with no code change (the queues, routing, lanes and
    # tasks are the same), which is what makes the broker-durability comparison a configuration
    # swap. The result backend stays on Redis either way, so the drill compares brokers only.
    broker_url: str = Field(default="", repr=False)

    # object store (MinIO)
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_access_key: str = Field(default="minioadmin", repr=False)
    s3_secret_key: str = Field(default="minioadmin", repr=False)
    s3_bucket: str = "cdi-documents"
    # OUT-S2: every result is also written as a JSON file - one per prescription, the same
    # whether it was uploaded or dropped in the watched folder. In the document store under
    # this key prefix ("" = not there), and, when a folder is named, in that folder too.
    result_output_prefix: str = "results"
    result_output_dir: str = ""

    # secrets store for the four credentials above (backlog E1-S5, docs/secrets.md)
    secrets_dir: str | None = None

    # folder-watch intake
    inbox_dir: str = "/workspace/data/inbox"
    processed_dir: str = "/workspace/data/processed"
    failed_dir: str = "/workspace/data/failed"

    # LS-S1: the file listener's drive, chosen here and nowhere else. "local", "onedrive",
    # "sharepoint", "gdrive", or a class of your own as package.module:Class. One at a time.
    # See listener/connectors.py and docs/LISTENER.md.
    listener_connector: str = "local"
    listener_local_root: str = "/workspace/data/listener"  # local: the folder to watch
    listener_root: str = ""  # onedrive / sharepoint: the folder's path on the drive ("" = root)
    # the six folders, by what they are called on the drive. An empty inbox means the root
    # folder itself is where files are dropped.
    listener_inbox: str = "inbox"
    listener_processing: str = "processing"
    listener_success: str = "success"
    listener_error: str = "error"
    listener_quarantine: str = "quarantine"
    listener_log: str = "log"
    # how long one call may wait, in all, on a drive that says "too many requests"
    listener_max_wait_seconds: float = Field(default=300.0, ge=0)
    # LS-S9 AC4: where the web app asks which alerts are firing, for the banner on every page
    # (webapp/alerts.py). Loopback: start_monitoring.sh binds Alertmanager there. Empty = no banner.
    alertmanager_url: str = "http://127.0.0.1:9093"
    # the listener's loop (listener/service.py): how often it looks at the folder, and how long
    # it waits for one file to be read before filing it under error with a "timeout" note
    listener_poll_seconds: float = Field(default=10.0, gt=0)
    listener_job_timeout_s: float = Field(default=900.0, gt=0)
    # LS-S3: pick up only finished, new, allowed files. A file is taken when its size and
    # version tag have been the same for this many looks in a row; only names matching the
    # patterns are read (anything else is listed as ignored); a file over the size limit is
    # refused as a data error. The lease is how long one worker may hold a file before another
    # may take it over; the worker's name defaults to this machine's.
    listener_stable_polls: int = Field(default=2, ge=1)
    listener_patterns: str = "*.pdf,*.png,*.jpg,*.jpeg,*.tif,*.tiff"
    listener_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    listener_lease_seconds: float = Field(default=900.0, gt=0)
    # LS-S9: the listener is "unwell" when no look at its folder has succeeded for this long.
    # The health view says so; the alert rule (CdiListenerNotPolling) uses the same five
    # minutes and is changed there.
    listener_stale_seconds: float = Field(default=300.0, gt=0)
    listener_worker: str = ""
    # LS-S5: files are run in batches. Up to this many finished files are taken together,
    # oldest first, and run at the same time; the next batch starts only when the whole batch
    # has finished. When fewer are waiting they are started once the oldest of them has waited
    # this long, so a lone file is not stuck (0 = do not wait: start whatever is there).
    # LS-S7: a failed file is tried again by itself when trying again can help (a timeout, a
    # server that was down), up to this many times, after waits of base x 2^n seconds (60, 120,
    # 240). So a file runs at most max_attempts + 1 times; when the last retry fails it is moved
    # to quarantine (LS-S6). A data error (empty, corrupt, too blurred, too big ...) is never
    # retried and stays in error.
    listener_max_attempts: int = Field(default=3, ge=0)
    listener_retry_base_seconds: float = Field(default=60.0, ge=0)
    listener_batch_size: int = Field(default=3, ge=1)
    listener_batch_wait_seconds: float = Field(default=60.0, ge=0)
    # Microsoft Graph (onedrive, sharepoint). The drive is /me/drive unless one of these says
    # otherwise: a drive id, a user (app-only sign-in), or - for sharepoint - a site id.
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_drive_id: str = ""
    graph_site_id: str = ""
    graph_user: str = ""
    # Google Drive: the id of the folder (the last part of its web address)
    gdrive_base_url: str = "https://www.googleapis.com"
    gdrive_folder_id: str = ""
    # LS-S2: signing in without handling a password (listener/auth.py). Microsoft: the Entra
    # app registration (client id, tenant id) and how to sign in - "device" (a person, once, by
    # typing a code on Microsoft's page) or "app" (a service identity an administrator approved,
    # with a certificate or a secret). The renewable token of a device sign-in is kept in the
    # MSAL token cache file, on the persistent disk so a restart does not ask again.
    graph_auth: str = "device"
    graph_client_id: str = ""
    graph_tenant_id: str = ""
    graph_authority_host: str = "https://login.microsoftonline.com"
    graph_token_cache: str = "/workspace/.cdi/graph_token_cache.json"
    # app-only: a certificate (private key PEM + thumbprint) or a secret. Files outside git.
    graph_client_cert_file: str = ""
    graph_client_cert_thumbprint: str = ""
    graph_client_secret_file: str = ""
    graph_client_secret: str = Field(default="", repr=False)
    # Google: the service account's JSON key, e.g. /workspace/secrets/gdrive_sa.json. Never in git.
    gdrive_service_account_file: str = ""
    # where the listener leaves its alert when it stops because the sign-in expired ("" = none)
    listener_alert_file: str = "/workspace/logs/listener_alert.json"
    # A bearer token for a test run only; these stay empty in every committed settings file.
    graph_access_token: str = Field(default="", repr=False)
    gdrive_access_token: str = Field(default="", repr=False)

    # S1 render resolution
    page_dpi: int = Field(default=200, ge=50, le=600)
    # LS-S3: every dropped or uploaded file is untrusted. A document with more pages than this
    # is REFUSED, never cut short (a prescription is a page or two; ASSUMPTION: 10 is ample).
    # A picture or PDF page that would open to more pixels than this is refused unopened - a
    # few kilobytes can claim to be a picture of billions of pixels (a decompression bomb).
    max_pages: int = Field(default=10, ge=1)
    max_image_pixels: int = Field(default=150_000_000, gt=0)

    # UP-S2 / IM-S1: is the photo good enough to read? ON by default since 2026-10-08 (IM-S1
    # AC2, owner's decision): a page that is blurred, too dark, too small, blank, glared or
    # photographed from too far away is held at the desk with a plain reason and a retake button,
    # instead of being read wrongly. Set CDI_QUALITY_CHECK=false to let every page through. The
    # limits below are starting values measured on rendered pages and sample scans, not yet tuned
    # on the answer-key set (scripts/quality_answer_key.py reports the false-reject rate when
    # that set exists) - so watch the weekly retake report (scripts/quality_report.py).
    # See recognition/quality.py.
    quality_check: bool = True
    quality_min_side_px: int = Field(default=700, ge=100)  # the shorter side of a page
    # variance of the Laplacian: a rendered prescription measures 73 (sparse) to 2153 (dense),
    # the same page blurred by 9 px measures 1.6 and blank paper 0.0
    quality_min_sharpness: float = Field(default=20.0, ge=0)
    # share of pixels darker than mid-grey: nothing to read ~0, a page in the dark ~1
    quality_min_ink_fraction: float = Field(default=0.0005, ge=0, le=1)
    quality_max_ink_fraction: float = Field(default=0.90, gt=0, le=1)
    # IM-S1 v3: how tall the writing is. A page photographed from far away is sharp but its
    # letters are tiny, which sharpness cannot see. Since 2026-10-09 this is the upper quartile
    # of the heights of the marks darker than their own paper (the letters, not the specks):
    # 23 px on a normal render, 17 to 21 px on readable phone photos of handwriting. That
    # measure reads about a fifth higher than the old median did, so the limit moved from 8 to
    # 10 to hold the same pictures. 0 = do not judge text height
    quality_min_text_px: float = Field(default=10.0, ge=0)
    # share of the page burnt out to white. Judged on photographs only: where the paper tone is
    # already at or above quality_paper_white (a render, a clean scan) there is no glare to find
    quality_max_glare_fraction: float = Field(default=0.05, gt=0, le=1)
    quality_paper_white: float = Field(default=250.0, ge=0, le=255)
    # IM-S1 AC4: a photo taken from far away is sharp *where the writing is* even though the
    # frame as a whole measures blank or blurred. At or above this (sharp writing measured
    # 700 - 26,000; blurred 4 - 330) tiny text is called "move closer", not "blurry".
    quality_min_writing_sharpness: float = Field(default=500.0, ge=0)
    # A readable page must not be held (2026-10-09). How crisp the strokes must be - edge
    # strength at the ink relative to the ink's darkness - for a page under the whole-frame
    # sharpness limit to be accepted anyway (0 = never). And the size below which a picture is
    # too small whatever it holds; between this and quality_min_side_px a picture is accepted
    # when its writing is tall enough (quality_min_text_px) and crisp.
    quality_min_stroke_sharpness: float = Field(default=0.4, ge=0)
    quality_floor_side_px: int = Field(default=400, ge=0)

    # IM-S2: turn a sideways or upside-down page upright and flatten an angled phone photo.
    # OFF by default - it changes the picture every later stage reads, and its limits are
    # proven on rendered pages and simulated photographs, not yet on real ones (ENT-S3). With
    # it off S1 does what it always did (the small-tilt deskew); the transform is recorded
    # either way. With it on, a page that cannot be made upright is held for a retake. See
    # recognition/geometry.py.
    straighten: bool = False

    # web app (the RunPod-proxied port)
    webapp_port: int = 8888
    # E10-S9: with several instances each binds 127.0.0.1:<own port> and the balancer
    # (infra/runpod/Caddyfile.lb) holds the public port; a single instance keeps 0.0.0.0
    webapp_host: str = "0.0.0.0"  # noqa: S104 - the pod proxy / balancer reaches it
    # names this process in /readyz, logs and web_job.accepted_by; empty = <hostname>:<port>
    instance_name: str = ""

    # durable jobs (backlog E10-S9, E9-S1 core; docs/ha.md "Application tier"): an upload is staged
    # in the object store and written to web_job before it is acknowledged. "queue" = the job runs
    # on a Celery worker (queue cdi-web, infra/runpod/start_worker.sh); "inprocess" = on the web app
    # instance that took the upload (dev / tests). Either way a job whose runner dies (its lease
    # runs out) is claimed and resumed by another runner.
    job_execution: Literal["inprocess", "queue"] = "inprocess"
    job_lease_s: float = Field(default=30.0, ge=3)
    # a job never claimed this long after its upload (its queue message lost) is claimed anyway
    job_unclaimed_s: float = Field(default=120.0, ge=5)
    job_max_attempts: int = Field(default=3, ge=1)
    # E9-S1 schema changes under load (cdi_adapter.migration): how long a migration waits for the
    # jobs in flight to end before it gives up (nothing applied), and the longest it may hold the
    # queue - a migrator that dies never gates new claims for more than this
    migration_drain_s: float = Field(default=120.0, ge=0)
    migration_hold_s: float = Field(default=900.0, ge=10)
    # a pipeline_run still "running" after this long is a worker that died, not one in flight,
    # and no longer holds a migration back
    migration_stage_stale_s: float = Field(default=600.0, ge=1)
    # E9-S2 pool split: which side of the pipeline this worker process serves. "cpu" runs the
    # ingest/OCR/classify stages and hands extraction to the GPU pool; "gpu" runs extraction;
    # "all" runs both in one worker (dev, a single-worker pod). "" = derive it from the queues the
    # worker consumes (cdi/cdi-web -> cpu, cdi-gpu -> gpu, both -> all), which is what the start
    # scripts rely on - so set this per worker process, never in a shared .env.
    worker_role: Literal["", "all", "cpu", "gpu"] = ""
    # how many documents one worker of each pool runs at once (Celery --concurrency). 0 = auto:
    # the CPU pool takes job_max_workers (Phase 1 is I/O- and OCR-bound), the GPU pool takes
    # extract_slots() (E2-S2, what the model gateway itself allows). Raising the CPU number buys
    # Phase-1 throughput and never touches GPU spend; that independence is the point of E9-S2.
    worker_cpu_concurrency: int = Field(default=0, ge=0)
    worker_gpu_concurrency: int = Field(default=0, ge=0)
    # Prometheus /metrics of a Celery web-job worker (127.0.0.1 only; 0 = off)
    worker_metrics_port: int = Field(default=9470, ge=0, le=65535)
    # E10-S9: a model gateway replica that refused a connection is skipped this long
    mlserve_down_s: float = Field(default=15.0, ge=0)
    # Prometheus /metrics of the web app (backlog E10-S1): its own port, bound to 127.0.0.1 only,
    # so the staff-facing app and the ingress never serve it; 0 = off
    metrics_port: int = Field(default=9464, ge=0, le=65535)
    # OpenTelemetry export (backlog E10-S3): empty = off (spans still exist, so logs keep their
    # trace ids). Pod: Tempo's OTLP receiver and Loki's OTLP ingestion, both on 127.0.0.1
    otel_traces_endpoint: str = ""  # e.g. http://127.0.0.1:4318/v1/traces
    otel_logs_endpoint: str = ""  # e.g. http://127.0.0.1:3100/otlp/v1/logs

    # model gateway. E10-S9: several replicas of the same model, comma separated; a call goes to
    # the next replica when one refuses the connection or answers 5xx
    mlserve_url: str = "http://127.0.0.1:8077"
    # RD-S5: a model server used only when every one in mlserve_url refuses - the serial (hf)
    # server behind a batching (vllm) one. Never shares the load: the replicas above do that.
    # Empty = none.
    mlserve_fallback_url: str = ""
    mlserve_port: int = 8077
    mlserve_backend: Literal["stub", "hf", "vllm"] = "stub"
    vlm_model_id: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    # RD-S4 (decided): no fallback model. The 3B that loaded on out-of-memory is under the Qwen
    # research licence (non-commercial) and was removed; a CDI_VLM_FALLBACK_MODEL_ID left in an
    # old .env is ignored.
    # model registry (backlog E10-S5): a gateway started for a registered version loads the model
    # at its pinned revision and reports the version on /healthz; the web app routes documents to
    # the primary or the canary gateway from the rollout log, re-read every model_route_ttl_s
    vlm_model_revision: str | None = None
    model_version: str | None = None
    model_route_ttl_s: float = Field(default=5.0, ge=0)
    vlm_max_pixels_ocr: int = 2_000_000
    # hf backend weights: "" = bf16 (ARCHITECTURE §3.4, the pod). "8bit" = bitsandbytes
    # LLM.int8, only for a test host below the 24 GB bar (docs/fallback-host.md): the 7B's bf16
    # weights (~16 GB) do not fit a 15 GB T4. start_mlserve.sh installs the pinned bitsandbytes.
    vlm_quantize: Literal["", "8bit"] = ""
    # vllm backend (backlog E2-S1, §3.4): 32 GB = ~15 GB weights + ~12-14 GB KV cache
    # -> 3-4 concurrent page extractions
    vllm_gpu_memory_utilization: float = Field(default=0.88, gt=0, le=0.98)
    vllm_max_model_len: int = Field(default=16384, ge=2048)
    vllm_max_num_seqs: int = Field(default=4, ge=1)
    # grammar-locked JSON decoding with XGrammar (backlog E2-S3, §14 #3)
    vllm_guided: bool = True
    vllm_request_timeout_s: float = Field(default=240.0, gt=0, allow_inf_nan=False)

    # pipeline behaviour
    fast_classify: bool = True
    # the rule-based fast path is for cleanly printed pages only: a page whose OCR is less sure than
    # this on average (handwriting read by a print OCR) goes to the VLM, which can say "handwritten"
    # and so send it through the VLM handwriting pass (2026-09-26: a printed letterhead with a
    # handwritten Rx, mean OCR 0.80, was taken as printed and its drugs extracted as gibberish)
    fast_classify_min_ocr_conf: float = Field(default=0.88, ge=0, le=1)
    # medicine catalogue layer (owner request 2026-09-26; docs/medicine-catalogue.md): after S5,
    # each medication fact is identified against the imported catalogue (Tata 1mg). A fuzzy match is
    # accepted only at >= min_score AND a lead of >= min_margin over every other brand; else
    # UNKNOWN_MEDICATION. Off, or an empty catalogue, changes nothing.
    catalogue_enabled: bool = True
    catalogue_min_score: float = Field(default=0.85, ge=0, le=1)
    catalogue_min_margin: float = Field(default=0.05, ge=0, le=1)
    catalogue_strength_must_agree: bool = True  # a fuzzy name with another strength: unknown
    # the medication agent (backlog E12-S4, ADR-0003): after validation it re-reads each medicine
    # line with the prescription's diagnoses and the catalogue and SUGGESTS the product written
    # there; advisory only, a reviewer applies it
    agent_enabled: bool = True
    agent_max_options: int = Field(default=10, ge=2, le=20)  # catalogue products per line
    agent_min_grounding: float = Field(default=0.45, ge=0, le=1)  # evidence text vs product name
    # the recall step (owner, 2026-09-28): the model is given each medicine line's extracted text
    # and the prescription's diagnoses and names, from its own knowledge, the brands and generics
    # the line most likely is; they widen the catalogue shortlist (false: the agent as before)
    agent_recall: bool = True
    extract_retries: int = Field(default=1, ge=0)
    job_max_workers: int = Field(default=6, ge=1)
    # Phase-2 extractions in flight at once (backlog E2-S2, §7.2): 0 = auto, i.e.
    # vllm_max_num_seqs with the vllm backend and 1 otherwise; hf is always 1
    extract_concurrency: int = Field(default=0, ge=0)
    # batch short documents of one doc_type into one gateway call (backlog E2-S8, §7.4): one
    # batched forward pass in which every document is its own sequence with its own prompt, so
    # evidence can never cross documents. "Short" = one page and at most extract_batch_max_blocks
    # OCR blocks. 1 = off, the default until the E1-S7 A/B on the GPU shows a gain.
    extract_batch_max: int = Field(default=1, ge=1, le=8)
    extract_batch_max_blocks: int = Field(default=40, ge=1)
    ocr_engine: Literal["rapidocr", "vlm"] = "rapidocr"
    handwritten_uses_vlm: bool = True
    ocr_min_conf: float = Field(default=0.30, ge=0, le=1)  # §5 S3: RapidOCR line kept if >= this
    # RD-S1: the OCR host (ocrhost/app.py, POST /ocr/rapid), a CPU-only process of its own. The
    # port it listens on (loopback), and where the pipeline finds it: empty = read in-process.
    ocr_host_port: int = 8079
    ocr_host_url: str = ""
    ocr_host_max_bytes: int = Field(default=40_000_000, gt=0)
    # RD-S2: two handwriting readers on every handwritten line crop, each on its own
    # (recognition/engines.py). The TrOCR checkpoint is English, trained on IAM: unmeasured on
    # prescription handwriting, and its licence is a review item (ENT-S1) - so this is off
    # unless set. It needs line_regions and line_readers. TrOCR reads on the OCR host when
    # ocr_host_url is set, else in-process on the CPU. A reader that has not answered within
    # two_readers_timeout_s is given up on for that line: the line keeps its one reading and
    # goes to review.
    trocr_model_id: str = "microsoft/trocr-base-handwritten"
    two_readers: bool = False
    two_readers_timeout_s: float = Field(default=20.0, gt=0)
    # RD-S3: deciding each handwritten line (recognition/hierarchy.py). The readers' agreement,
    # the picture check and the word lists always run when two_readers is on. Two more checks
    # cost a model call per line each, so they are off unless set: self_consistency reads the
    # crop again with other padding; second_opinion asks Qwen which reading the picture shows
    # when the readers disagree (recorded only - it never decides).
    self_consistency: bool = False
    second_opinion: bool = False
    # IM-S3: S3 finds the lines of every page and labels them printed / handwritten / mixed /
    # uncertain (recognition/regions.py), and records the counts with the OCR run - always. This
    # switch is whether the labels are *acted on*: each observation is then stored with its
    # line's label instead of the reader's ("printed" for RapidOCR), and a page with at least
    # line_regions_min_handwritten handwritten lines gets the handwriting pass even when the
    # classifier called it printed. OFF by default: the labels are measured on two real pages,
    # not on the answer-key set, and the story's own rule is that routing must prove its worth.
    line_regions: bool = False
    line_regions_min_handwritten: int = Field(default=3, ge=1)
    # IM-S3 AC2 / AC4: read line by line. With this on (it needs line_regions too) every line
    # that is not plainly printed is cut from the page to the crop standard and read by the
    # handwriting reader: a handwritten line is read by it INSTEAD of the print reader, an
    # uncertain or mixed line by BOTH. OFF by default: it is one model call per such line - 18
    # on a neat real prescription, 38 on a hard one - and what it gains over reading the whole
    # page once has not been measured (the story's own ablation). line_readers_max_per_page
    # stops a page that is all "uncertain" from making hundreds of calls; lines past the cap
    # keep their print reading.
    line_readers: bool = False
    line_readers_max_per_page: int = Field(default=60, ge=1)

    # S6 governance gate (§5 S6, §14 "The gate"); per-class fitted thresholds are backlog E4-S3
    gate_auto_accept_conf: float = Field(default=0.985, ge=0, le=1)
    gate_audit_conf: float = Field(default=0.95, ge=0, le=1)
    gate_review_floor: float = Field(default=0.85, ge=0, le=1)
    gate_partial_penalty: float = Field(default=0.30, ge=0, le=1)
    audit_sample_rate: float = Field(default=0.05, ge=0, le=1)
    # E4-S2: the fitted calibrator the gate decides with (a calibration_model version). Unset:
    # the fixed-penalty _calibrate score, as before. Only set it to a calibrator fitted on real
    # clinician-adjudicated data (E14-S1).
    calibrator_version: int | None = Field(default=None, ge=1)
    # E4-S3: the derived per-class gate thresholds (a checked-in gate_thresholds.v1 JSON file, on
    # the scale of CDI_CALIBRATOR_VERSION). Unset: the global constants above, as before.
    gate_thresholds: str | None = None

    # jurisdiction packages (§2)
    # the NRCeS ABDM IG as published in the FHIR package registry: id "ndhm.in" (canonical
    # https://nrces.in/ndhm/fhir/r4); "nrces.fhir.r4.ndhm" is not a registry id (backlog E6-S1)
    ig_package: str = "ndhm.in#6.5.0"
    terminology_package: str = "in-snomed-loinc-icd10"

    # HL7 FHIR validator jar (TECH_STACK §10): used when java + this jar + the pinned package
    # cache exist (backlog E6-S1). The cache holds the IG tarball and every package it pulls in,
    # frozen and checked against infra/fhir/packages.lock; validation never goes online (§12).
    fhir_validator_jar: str = "/workspace/tools/validator_cli.jar"
    fhir_java: str = "java"
    fhir_package_home: str = "/workspace/tools/fhir-home"

    # web-app sign-in (backlog E11-S1, ARCHITECTURE §12): server-side sessions + account lockout.
    # The cookie is Secure by default (staff reach the app over TLS: E1-S9 ingress / RunPod proxy);
    # set CDI_SESSION_COOKIE_SECURE=false only for plain-http local development.
    session_idle_minutes: int = Field(default=30, ge=1)
    session_max_hours: int = Field(default=12, ge=1)
    session_cookie_secure: bool = True
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    # Every unsafe request must be same-origin: Origin/Referer host == Host (webapp.auth). The
    # E1-S9 ingress passes Host through, so staff traffic matches and this stays empty. A proxy
    # that REWRITES Host (Colab's port proxy, RunPod's <POD_ID>-8888.proxy.runpod.net) makes the
    # two disagree and every POST is refused; name those hostnames here, comma separated, to
    # accept them as well. An explicit list only - no wildcards, never a public deployment.
    public_origins: str = ""

    # de-identification before any training / evaluation use (backlog E11-S4, ARCHITECTURE §12):
    # Presidio + spaCy + a clinical de-id NER, loaded from local files at a pinned revision only
    deid_spacy_model: str = "en_core_web_lg"
    deid_ner_model: str = "obi/deid_roberta_i2b2"
    deid_ner_revision: str = "78f2152eb93ddd817290ce8dbe46f1a6685e09fc"
    deid_min_score: float = Field(default=0.35, ge=0, le=1)


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, read once at start-up.

    Credentials come from the secrets store (backlog E1-S5, ``cdi_adapter.secretstore``) when one
    is configured; a store readable by group/other refuses to load.
    """
    from cdi_adapter import secretstore

    base = Settings()  # env + .env only: tells us where the store is
    store = secretstore.active_dir(base.secrets_dir)
    if store is None:
        return base
    if not store.is_dir():
        raise FileNotFoundError(f"{secretstore.ENV_VAR}={store} does not exist")
    secretstore.assert_safe(store)
    problems = secretstore.check(store, names=tuple(f"cdi_{f}" for f in secretstore.FIELDS))
    if problems:
        raise ValueError("invalid secrets store: " + "; ".join(problems))
    credentials = {field: secretstore.read(store, f"cdi_{field}") for field in secretstore.FIELDS}
    # A configured vault/store is authoritative for credentials; env still configures other fields.
    return Settings(**credentials, _secrets_dir=store)  # type: ignore[arg-type,call-arg]
