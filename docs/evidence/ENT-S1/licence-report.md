# Licence report

Generated 2026-10-13 by `scripts/licence_audit.py`. **Not legal advice** - it lists the evidence; counsel decides.

- components: 257  |  ALLOWED: 148  |  ALLOWED WITH NOTICES: 3  |  NOT IN POLICY: 9  |  REVIEW: 12  |  UNKNOWN: 85
- unreviewed: 86
- build gate: BLOCKED (94 blockers)
- release gate: BLOCKED (112 blockers)

## Decisions

| Decision | Status | Owner | Date | Found in the repository |
|---|---|---|---|---|
| PyMuPDF (AGPL-3.0 or Artifex commercial): Replace with pypdfium2; ship the PDFium licence files. | APPLIED | - | - | - |
| Qwen2.5-VL-3B-Instruct (Qwen RESEARCH licence, non-commercial): Remove it; no fallback model. Delete vlm_fallback_model_id. | OPEN - NEEDS AN OWNER AND A DATE | - | - | scripts/model_rollout_drill.py:81 |
| IAM handwriting database as training data (non-commercial research only): Never train or fine-tune on IAM for the sold product. | APPLIED | - | - | - |
| MinIO server (AGPL-3.0): Do not make it mandatory: an ObjectStore interface (SW-S6) - cloud storage when hosted; an encrypted filesystem store or the customer's S3-compatible store when installed. | OPEN - NEEDS AN OWNER AND A DATE | - | - | src/cdi_adapter/storage.py: the only store is the S3 client (any S3 service works; there is no filesystem store) |
| Redis server (BSD-3 up to 7.2; RSALv2 / SSPLv1 from 7.4): Pin 7.2 or older, or switch to Valkey. No upgrade to 7.4 or later without a compliance review. | APPLIED | - | - | - |
| Grafana OSS (AGPL-3.0): Do not bundle. Expose Prometheus / OpenTelemetry metrics; customers attach their own. | OPEN - NEEDS AN OWNER AND A DATE | - | - | infra/runpod/versions.env: grafana 13.2.2; infra/monitoring/grafana/ (provisioning shipped with the product) |
| OpenCV wheels (Apache-2.0; bundle FFmpeg LGPL; the non-headless wheel also bundles Qt LGPL): Headless wheel only; ship the third-party notices. | OPEN - NEEDS AN OWNER AND A DATE | - | - | opencv-python in infra/runpod/requirements.lock |
| 1mg catalogue (terms prohibit commercial reuse and scraping): Do not scrape or copy it. If medicines are added later: CDSCO lists plus a licensed drug database. | OPEN - NEEDS AN OWNER AND A DATE | - | - | src/cdi_adapter/catalogue/normalize.py:76; src/cdi_adapter/catalogue/normalize.py:102; src/cdi_adapter/catalogue/service.py:3; src/cdi_adapter/catalogue/service.py:61 |
| ICD-10 (WHO, CC BY-ND 3.0 IGO conditions) and SNOMED CT (affiliate licence): Not bundled, not required: an optional adapter a customer enables (SW-S6). Never alter code descriptions. | OPEN - NEEDS AN OWNER AND A DATE | - | - | src/cdi_adapter/terminology/seed.py:18; src/cdi_adapter/terminology/seed.py:20 |
| NOTICE file and third-party licence texts: Ship a NOTICE file; for the installed version also every third-party licence text. | APPLIED | - | - | - |
| Qwen2.5-VL-7B-Instruct-AWQ (Qwen LICENSE AGREEMENT): Conditional: give recipients the agreement and the notice line 'Qwen is licensed under the Qwen LICENSE AGREEMENT, Copyright (c) Alibaba Cloud. All Rights Reserved.'; show 'Built with Qwen' where required; request a licence above 100 million monthly users. | NOT CHECKED BY THE BUILD - NEEDS AN OWNER AND A DATE | - | - | - |
| LOINC codes: Accepted: free licence, commercial use allowed. Keep the notices and accept the licence terms. | NOT CHECKED BY THE BUILD - NEEDS AN OWNER AND A DATE | - | - | - |
| NVIDIA driver, CUDA runtime and cuDNN: Hosted: not redistributed. Installed: ship ONLY the files on the EULA's redistributable list; read the EULA text, then list the exact files. | NOT CHECKED BY THE BUILD - NEEDS AN OWNER AND A DATE | - | - | - |

## Blocked

- python package agent-detector: not in the register: add an entry with its licence and where it was read
- python package aiohappyeyeballs: not in the register: add an entry with its licence and where it was read
- python package aiohttp: not in the register: add an entry with its licence and where it was read
- python package aiosignal: not in the register: add an entry with its licence and where it was read
- python package astor: not in the register: add an entry with its licence and where it was read
- python package blake3: not in the register: add an entry with its licence and where it was read
- python package cachetools: not in the register: add an entry with its licence and where it was read
- python package cbor2: not in the register: add an entry with its licence and where it was read
- python package cloudpickle: not in the register: add an entry with its licence and where it was read
- python package compressed-tensors: not in the register: add an entry with its licence and where it was read
- python package cuda-pathfinder: not in the register: add an entry with its licence and where it was read
- python package cupy-cuda12x: not in the register: add an entry with its licence and where it was read
- python package datasets: not in the register: add an entry with its licence and where it was read
- python package depyf: not in the register: add an entry with its licence and where it was read
- python package detect-installer: not in the register: add an entry with its licence and where it was read
- python package dill: not in the register: add an entry with its licence and where it was read
- python package diskcache: not in the register: add an entry with its licence and where it was read
- python package dnspython: not in the register: add an entry with its licence and where it was read
- python package email-validator: not in the register: add an entry with its licence and where it was read
- python package fastapi-cli: not in the register: add an entry with its licence and where it was read
- python package fastapi-cloud-cli: not in the register: add an entry with its licence and where it was read
- python package fastar: not in the register: add an entry with its licence and where it was read
- python package frozendict: not in the register: add an entry with its licence and where it was read
- python package frozenlist: not in the register: add an entry with its licence and where it was read
- python package gguf: not in the register: add an entry with its licence and where it was read
- python package httpcore2: not in the register: add an entry with its licence and where it was read
- python package httpx2: not in the register: add an entry with its licence and where it was read
- python package interegular: not in the register: add an entry with its licence and where it was read
- python package jiter: not in the register: add an entry with its licence and where it was read
- python package llguidance: not in the register: add an entry with its licence and where it was read
- python package llmcompressor: not in the register: add an entry with its licence and where it was read
- python package llvmlite: not in the register: add an entry with its licence and where it was read
- python package lm-format-enforcer: not in the register: add an entry with its licence and where it was read
- python package loguru: not in the register: add an entry with its licence and where it was read
- python package mistral-common: not in the register: add an entry with its licence and where it was read
- python package msgspec: not in the register: add an entry with its licence and where it was read
- python package multidict: not in the register: add an entry with its licence and where it was read
- python package multiprocess: not in the register: add an entry with its licence and where it was read
- python package ninja: not in the register: add an entry with its licence and where it was read
- python package numba: not in the register: add an entry with its licence and where it was read
- python package openai: not in the register: add an entry with its licence and where it was read
- python package openai-harmony: not in the register: add an entry with its licence and where it was read
- python package outlines-core: not in the register: add an entry with its licence and where it was read
- python package pandas: not in the register: add an entry with its licence and where it was read
- python package partial-json-parser: not in the register: add an entry with its licence and where it was read
- python package prometheus-fastapi-instrumentator: not in the register: add an entry with its licence and where it was read
- python package propcache: not in the register: add an entry with its licence and where it was read
- python package py-cpuinfo: not in the register: add an entry with its licence and where it was read
- python package pyarrow: not in the register: add an entry with its licence and where it was read
- python package pybase64: not in the register: add an entry with its licence and where it was read
- python package pycountry: not in the register: add an entry with its licence and where it was read
- python package pydantic-extra-types: not in the register: add an entry with its licence and where it was read
- python package pynvml: not in the register: add an entry with its licence and where it was read
- python package ray: not in the register: add an entry with its licence and where it was read
- python package rich-toolkit: not in the register: add an entry with its licence and where it was read
- python package rignore: not in the register: add an entry with its licence and where it was read
- python package scipy: not in the register: add an entry with its licence and where it was read
- python package sentry-sdk: not in the register: add an entry with its licence and where it was read
- python package setproctitle: not in the register: add an entry with its licence and where it was read
- python package soundfile: not in the register: add an entry with its licence and where it was read
- python package soxr: not in the register: add an entry with its licence and where it was read
- python package tiktoken: not in the register: add an entry with its licence and where it was read
- python package truststore: not in the register: add an entry with its licence and where it was read
- python package vllm: not in the register: add an entry with its licence and where it was read
- python package xformers: not in the register: add an entry with its licence and where it was read
- python package xgrammar: not in the register: add an entry with its licence and where it was read
- python package xxhash: not in the register: add an entry with its licence and where it was read
- python package yarl: not in the register: add an entry with its licence and where it was read
- server or binary alertmanager: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary apt:autossh: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary apt:cron: REVIEW: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:curl: REVIEW: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:git: REVIEW: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:libcurl4t64: REVIEW: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:libgl1: REVIEW: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:openjdk-21-jre-headless: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary apt:postgresql-16: NOT IN POLICY: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:postgresql-client-16: NOT IN POLICY: a decision must be recorded (what, who, when) before this may be built
- server or binary apt:python3-venv: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary bitsandbytes: not in the register: add an entry with its licence and where it was read
- server or binary caddy: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary cloudflared: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary fhir-validator: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary go: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary image:postgres: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary llmcompressor: not in the register: add an entry with its licence and where it was read
- server or binary loki: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary prometheus: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary tempo: licence UNKNOWN: read it from the source and record it (never guessed)
- server or binary vllm: not in the register: add an entry with its licence and where it was read
- model en_core_web_lg: not in the register: add an entry with its licence and where it was read
- model microsoft/trocr-base-handwritten: REVIEW: a decision must be recorded (what, who, when) before this may be built
- model obi/deid_roberta_i2b2: not in the register: add an entry with its licence and where it was read
- data set medicine catalogue (1mg-derived): the recorded decision is NOT to use it, and it is in the build

## Components

| Kind | Name | Version | Licence | Verdict | Status |
|---|---|---|---|---|---|
| python package | accelerate | 1.15.0 | Apache / Apache Software License | ALLOWED | ok |
| python package | agent-detector | 2.0.0 | - | UNKNOWN | blocked, unreviewed |
| python package | aiohappyeyeballs | 2.7.1 | - | UNKNOWN | blocked, unreviewed |
| python package | aiohttp | 3.14.3 | - | UNKNOWN | blocked, unreviewed |
| python package | aiosignal | 1.4.0 | - | UNKNOWN | blocked, unreviewed |
| python package | alembic | 1.20.0 | MIT | ALLOWED | ok |
| python package | amqp | 5.4.0 | BSD / BSD License | ALLOWED | ok |
| python package | annotated-doc | 0.0.5 | MIT | ALLOWED | ok |
| python package | annotated-types | 0.8.0 | MIT / MIT License | ALLOWED | ok |
| python package | anyio | 4.15.1 | MIT | ALLOWED | ok |
| python package | astor | 0.8.1 | - | UNKNOWN | blocked, unreviewed |
| python package | attrs | 26.1.0 | MIT | ALLOWED | ok |
| python package | av | 18.1.0 | BSD-3-Clause | ALLOWED | ok |
| python package | billiard | 4.3.0 | BSD / BSD License | ALLOWED | ok |
| python package | blake3 | 1.0.9 | - | UNKNOWN | blocked, unreviewed |
| python package | blis | 1.3.3 | BSD | ALLOWED | ok |
| python package | boto3 | 1.43.99 | Apache-2.0 | ALLOWED | ok |
| python package | botocore | 1.43.99 | Apache-2.0 | ALLOWED | ok |
| python package | cachetools | 7.2.0 | - | UNKNOWN | blocked, unreviewed |
| python package | catalogue | 2.0.10 | MIT | ALLOWED | ok |
| python package | cbor2 | 6.1.4 | - | UNKNOWN | blocked, unreviewed |
| python package | celery | 5.6.3 | BSD-3-Clause | ALLOWED | ok |
| python package | certifi | 2026.7.22 | MPL-2.0 / Mozilla Public License 2.0 (MPL 2.0) | NOT IN POLICY | ok (REG-PY-2026-10-04) |
| python package | cffi | 2.1.1 | MIT | ALLOWED | ok |
| python package | charset-normalizer | 3.5.1 | MIT | ALLOWED | ok |
| python package | click | 8.5.0 | BSD-3-Clause | ALLOWED | ok |
| python package | click-didyoumean | 0.3.1 | MIT / MIT License | ALLOWED | ok |
| python package | click-plugins | 1.1.1.2 | New BSD / BSD License | ALLOWED | ok |
| python package | click-repl | 0.4.0 | MIT | ALLOWED | ok |
| python package | cloudpathlib | 0.25.0 | MIT License | ALLOWED | ok |
| python package | cloudpickle | 3.1.2 | - | UNKNOWN | blocked, unreviewed |
| python package | compressed-tensors | 0.11.0 | - | UNKNOWN | blocked, unreviewed |
| python package | confection | 1.3.3 | MIT | ALLOWED | ok |
| python package | cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause / Apache Software License; BSD License | ALLOWED | ok |
| python package | cuda-pathfinder | 1.8.2 | - | UNKNOWN | blocked, unreviewed |
| python package | cupy-cuda12x | 14.2.0 | - | UNKNOWN | blocked, unreviewed |
| python package | cymem | 2.0.13 | MIT | ALLOWED | ok |
| python package | datasets | 4.0.0 | - | UNKNOWN | blocked, unreviewed |
| python package | depyf | 0.19.0 | - | UNKNOWN | blocked, unreviewed |
| python package | detect-installer | 0.2.1 | - | UNKNOWN | blocked, unreviewed |
| python package | dill | 0.4.1 | - | UNKNOWN | blocked, unreviewed |
| python package | diskcache | 5.6.3 | - | UNKNOWN | blocked, unreviewed |
| python package | dnspython | 2.8.0 | - | UNKNOWN | blocked, unreviewed |
| python package | einops | 0.8.2 | MIT / MIT License | ALLOWED | ok |
| python package | email-validator | 2.3.0 | - | UNKNOWN | blocked, unreviewed |
| python package | fastapi | 0.141.1 | MIT | ALLOWED | ok |
| python package | fastapi-cli | 0.0.32 | - | UNKNOWN | blocked, unreviewed |
| python package | fastapi-cloud-cli | 0.26.0 | - | UNKNOWN | blocked, unreviewed |
| python package | fastar | 0.12.0 | - | UNKNOWN | blocked, unreviewed |
| python package | filelock | 4.0.1 | Unlicense / The Unlicense (Unlicense) | NOT IN POLICY | ok (REG-PY-2026-10-04) |
| python package | flatbuffers | 25.12.19 | Apache 2.0 / Apache Software License | ALLOWED | ok |
| python package | frozendict | 2.4.7 | - | UNKNOWN | blocked, unreviewed |
| python package | frozenlist | 1.8.0 | - | UNKNOWN | blocked, unreviewed |
| python package | fsspec | 2026.9.0 | BSD 3-Clause License / BSD License | ALLOWED | ok |
| python package | gguf | 0.19.0 | - | UNKNOWN | blocked, unreviewed |
| python package | googleapis-common-protos | 1.75.3 | Apache-2.0 | ALLOWED | ok |
| python package | greenlet | 3.5.6 | MIT AND PSF-2.0 | NOT IN POLICY | ok (REG-PY-2026-10-04) |
| python package | h11 | 0.16.0 | MIT / MIT License | ALLOWED | ok |
| python package | hf-xet | 1.6.0 | Apache-2.0 / Apache Software License | ALLOWED | ok |
| python package | httpcore | 1.0.9 | BSD-3-Clause / BSD License | ALLOWED | ok |
| python package | httpcore2 | 2.13.0 | - | UNKNOWN | blocked, unreviewed |
| python package | httptools | 0.8.0 | MIT | ALLOWED | ok |
| python package | httpx | 0.28.1 | BSD-3-Clause / BSD License | ALLOWED | ok |
| python package | httpx2 | 2.13.0 | - | UNKNOWN | blocked, unreviewed |
| python package | huggingface-hub | 1.32.0 | Apache-2.0 / Apache Software License | ALLOWED | ok |
| python package | idna | 3.20 | BSD License | ALLOWED | ok |
| python package | interegular | 0.3.3 | - | UNKNOWN | blocked, unreviewed |
| python package | jinja2 | 3.1.6 | BSD License | ALLOWED | ok |
| python package | jiter | 0.17.0 | - | UNKNOWN | blocked, unreviewed |
| python package | jmespath | 1.1.0 | MIT / MIT License | ALLOWED | ok |
| python package | jsonschema | 4.26.0 | MIT | ALLOWED | ok |
| python package | jsonschema-specifications | 2025.9.1 | MIT | ALLOWED | ok |
| python package | kombu | 5.6.2 | BSD-3-Clause | ALLOWED | ok |
| python package | lark | 1.2.2 | MIT / MIT License | ALLOWED | ok |
| python package | llguidance | 0.7.30 | - | UNKNOWN | blocked, unreviewed |
| python package | llmcompressor | 0.7.1.4 | - | UNKNOWN | blocked, unreviewed |
| python package | llvmlite | 0.44.0 | - | UNKNOWN | blocked, unreviewed |
| python package | lm-format-enforcer | 0.11.3 | - | UNKNOWN | blocked, unreviewed |
| python package | locust | - | MIT | ALLOWED | ok |
| python package | loguru | 0.7.3 | - | UNKNOWN | blocked, unreviewed |
| python package | mako | 1.4.1 | MIT | ALLOWED | ok |
| python package | markdown-it-py | 4.2.0 | MIT License | ALLOWED | ok |
| python package | markupsafe | 3.0.3 | BSD-3-Clause | ALLOWED | ok |
| python package | mdurl | 0.1.2 | MIT License | ALLOWED | ok |
| python package | mistral-common | 1.11.7 | - | UNKNOWN | blocked, unreviewed |
| python package | mpmath | 1.3.0 | BSD / BSD License | ALLOWED | ok |
| python package | msal | - | MIT / MIT License | ALLOWED | ok |
| python package | msgpack | 1.2.2 | Apache-2.0 | ALLOWED | ok |
| python package | msgspec | 0.21.1 | - | UNKNOWN | blocked, unreviewed |
| python package | multidict | 6.9.1 | - | UNKNOWN | blocked, unreviewed |
| python package | multiprocess | 0.70.16 | - | UNKNOWN | blocked, unreviewed |
| python package | murmurhash | 1.0.15 | MIT | ALLOWED | ok |
| python package | mypy | - | MIT | ALLOWED | ok |
| python package | networkx | 3.7 | BSD License | ALLOWED | ok |
| python package | ninja | 1.13.2 | - | UNKNOWN | blocked, unreviewed |
| python package | numba | 0.61.2 | - | UNKNOWN | blocked, unreviewed |
| python package | numpy | 2.4.6 | Copyright (c) 2005-2024, NumPy Developers. / BSD License | ALLOWED | ok |
| python package | onnxruntime | 1.30.0 | MIT License / MIT License | ALLOWED | ok |
| python package | openai | 3.17.0 | - | UNKNOWN | blocked, unreviewed |
| python package | openai-harmony | 0.0.8 | - | UNKNOWN | blocked, unreviewed |
| python package | opencv-python | 5.0.0.93 | Apache 2.0 / Apache Software License | ALLOWED | ok |
| python package | opencv-python-headless | 5.0.0.93 | Apache 2.0 / Apache Software License | ALLOWED | ok |
| python package | opentelemetry-api | 1.44.0 | Apache-2.0 | ALLOWED | ok |
| python package | opentelemetry-exporter-otlp-proto-common | 1.44.0 | Apache-2.0 | ALLOWED | ok |
| python package | opentelemetry-exporter-otlp-proto-http | 1.44.0 | Apache-2.0 | ALLOWED | ok |
| python package | opentelemetry-proto | 1.44.0 | Apache-2.0 | ALLOWED | ok |
| python package | opentelemetry-sdk | 1.44.0 | Apache-2.0 | ALLOWED | ok |
| python package | opentelemetry-semantic-conventions | 0.65b0 | Apache-2.0 | ALLOWED | ok |
| python package | outlines-core | 0.2.11 | - | UNKNOWN | blocked, unreviewed |
| python package | packaging | 26.3 | Apache Software License; BSD License | ALLOWED | ok |
| python package | pandas | 3.0.6 | - | UNKNOWN | blocked, unreviewed |
| python package | partial-json-parser | 0.2.1.1.post7 | - | UNKNOWN | blocked, unreviewed |
| python package | phonenumbers | 9.0.39 | Apache-2.0 | ALLOWED | ok |
| python package | pillow | 12.3.0 | MIT-CMU / CMU License (MIT-CMU) | NOT IN POLICY | ok (REG-PY-2026-10-04) |
| python package | pip | 26.2.1 | MIT | ALLOWED | ok |
| python package | preshed | 3.0.13 | MIT | ALLOWED | ok |
| python package | presidio-analyzer | 2.2.364 | MIT | ALLOWED | ok |
| python package | presidio-anonymizer | 2.2.364 | MIT | ALLOWED | ok |
| python package | prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | ALLOWED | ok |
| python package | prometheus-fastapi-instrumentator | 8.1.0 | - | UNKNOWN | blocked, unreviewed |
| python package | prompt-toolkit | 3.0.53 | BSD License | ALLOWED | ok |
| python package | propcache | 0.5.4 | - | UNKNOWN | blocked, unreviewed |
| python package | protobuf | 7.36.2 | 3-Clause BSD License | ALLOWED | ok |
| python package | psutil | 7.2.2 | BSD-3-Clause | ALLOWED | ok |
| python package | psycopg | 3.3.6 | LGPL-3.0-only | ALLOWED WITH NOTICES | ok |
| python package | psycopg-binary | 3.3.6 | LGPL-3.0-only | ALLOWED WITH NOTICES | ok |
| python package | py-cpuinfo | 9.0.0 | - | UNKNOWN | blocked, unreviewed |
| python package | pyarrow | 25.0.1 | - | UNKNOWN | blocked, unreviewed |
| python package | pybase64 | 1.5.0 | - | UNKNOWN | blocked, unreviewed |
| python package | pyclipper | 1.4.0 | MIT / OSI Approved; MIT License | ALLOWED | ok |
| python package | pycountry | 26.2.16 | - | UNKNOWN | blocked, unreviewed |
| python package | pycparser | 3.0 | BSD-3-Clause / BSD License | ALLOWED | ok |
| python package | pydantic | 2.13.5 | MIT | ALLOWED | ok |
| python package | pydantic-core | 2.46.5 | MIT | ALLOWED | ok |
| python package | pydantic-extra-types | 2.11.1 | - | UNKNOWN | blocked, unreviewed |
| python package | pydantic-settings | 2.15.0 | MIT / MIT License | ALLOWED | ok |
| python package | pygments | 2.21.0 | BSD-2-Clause / BSD License | ALLOWED | ok |
| python package | pyjwt | - | MIT / MIT License | ALLOWED | ok |
| python package | pynvml | 12.0.0 | - | UNKNOWN | blocked, unreviewed |
| python package | pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0, dependency licenses | ALLOWED | ok |
| python package | pytest | - | MIT | ALLOWED | ok |
| python package | pytest-cov | - | MIT / MIT License | ALLOWED | ok |
| python package | python-dateutil | 2.9.0.post0 | Dual License / BSD License; Apache Software License | ALLOWED | ok |
| python package | python-dotenv | 1.2.3 | BSD-3-Clause | ALLOWED | ok |
| python package | python-json-logger | 4.2.0 | BSD-2-Clause License / BSD License | ALLOWED | ok |
| python package | python-multipart | 0.0.32 | Apache-2.0 / Apache Software License | ALLOWED | ok |
| python package | pyyaml | 6.0.3 | MIT / MIT License | ALLOWED | ok |
| python package | pyzmq | 27.2.0 | BSD 3-Clause License / BSD License | ALLOWED | ok |
| python package | qwen-vl-utils | 0.0.14 | Apache-2.0 / Apache Software License | ALLOWED | ok |
| python package | rapidocr-onnxruntime | 1.4.4 | Apache-2.0 | ALLOWED | ok |
| python package | ray | 2.58.0 | - | UNKNOWN | blocked, unreviewed |
| python package | redis | 6.4.0 | MIT / MIT License | ALLOWED | ok |
| python package | referencing | 0.37.0 | MIT | ALLOWED | ok |
| python package | regex | 2026.9.10 | Apache-2.0 AND CNRI-Python | ALLOWED | ok |
| python package | requests | 2.34.2 | Apache-2.0 / Apache Software License | ALLOWED | ok |
| python package | requests-file | 3.0.1 | Apache 2.0 | ALLOWED | ok |
| python package | rich | 15.0.0 | MIT / MIT License | ALLOWED | ok |
| python package | rich-toolkit | 0.20.5 | - | UNKNOWN | blocked, unreviewed |
| python package | rignore | 0.8.1 | - | UNKNOWN | blocked, unreviewed |
| python package | rpds-py | 2026.6.3 | MIT | ALLOWED | ok |
| python package | ruff | - | MIT | ALLOWED | ok |
| python package | s3transfer | 0.19.2 | Apache License 2.0 / Apache Software License | ALLOWED | ok |
| python package | safetensors | 0.8.0 | Apache Software License | ALLOWED | ok |
| python package | scipy | 1.18.1 | - | UNKNOWN | blocked, unreviewed |
| python package | sentencepiece | 0.2.2 | Apache-2.0 | ALLOWED | ok |
| python package | sentry-sdk | 2.70.0 | - | UNKNOWN | blocked, unreviewed |
| python package | setproctitle | 1.3.7 | - | UNKNOWN | blocked, unreviewed |
| python package | setuptools | 84.0.0 | MIT | ALLOWED | ok |
| python package | shapely | 2.1.2 | BSD 3-Clause / BSD License | ALLOWED | ok |
| python package | shellingham | 1.5.4 | ISC License / ISC License (ISCL) | ALLOWED | ok |
| python package | six | 1.17.0 | MIT / MIT License | ALLOWED | ok |
| python package | smart-open | 8.0.1 | MIT License | ALLOWED | ok |
| python package | sniffio | 1.3.1 | MIT OR Apache-2.0 / MIT License; Apache Software License | ALLOWED | ok |
| python package | soundfile | 0.14.0 | - | UNKNOWN | blocked, unreviewed |
| python package | soxr | 1.1.0 | - | UNKNOWN | blocked, unreviewed |
| python package | spacy | 3.8.16 | MIT | ALLOWED | ok |
| python package | spacy-legacy | 3.0.12 | MIT | ALLOWED | ok |
| python package | spacy-loggers | 1.0.5 | MIT | ALLOWED | ok |
| python package | sqlalchemy | 2.0.54 | MIT | ALLOWED | ok |
| python package | srsly | 2.5.3 | MIT | ALLOWED | ok |
| python package | starlette | 1.6.0 | BSD-3-Clause | ALLOWED | ok |
| python package | structlog | 26.1.0 | MIT OR Apache-2.0 / Apache Software License; MIT License | ALLOWED | ok |
| python package | sympy | 1.14.0 | BSD / BSD License | ALLOWED | ok |
| python package | tenacity | 9.1.4 | Apache 2.0 / Apache Software License | ALLOWED | ok |
| python package | thinc | 8.3.13 | MIT | ALLOWED | ok |
| python package | tiktoken | 0.14.0 | - | UNKNOWN | blocked, unreviewed |
| python package | tldextract | 5.3.2 | BSD-3-Clause | ALLOWED | ok |
| python package | tokenizers | 0.23.2 | Apache Software License | ALLOWED | ok |
| python package | tqdm | 4.70.1 | MPL-2.0 AND MIT | NOT IN POLICY | ok (REG-PY-2026-10-04) |
| python package | transformers | 5.16.1 | Apache 2.0 License | ALLOWED | ok |
| python package | truststore | 0.10.4 | - | UNKNOWN | blocked, unreviewed |
| python package | typer | 0.27.2 | MIT | ALLOWED | ok |
| python package | typing-extensions | 4.16.0 | PSF-2.0 | NOT IN POLICY | ok (REG-PY-2026-10-04) |
| python package | typing-inspection | 0.4.4 | MIT | ALLOWED | ok |
| python package | tzdata | 2026.4 | Apache-2.0 | ALLOWED | ok |
| python package | tzlocal | 5.4.4 | MIT | ALLOWED | ok |
| python package | urllib3 | 2.8.0 | MIT | ALLOWED | ok |
| python package | uvicorn | 0.53.0 | BSD-3-Clause | ALLOWED | ok |
| python package | uvloop | 0.22.1 | MIT License / Apache Software License; MIT License | ALLOWED | ok |
| python package | vine | 5.1.0 | BSD / BSD License | ALLOWED | ok |
| python package | vllm | 0.11.0 | - | UNKNOWN | blocked, unreviewed |
| python package | wasabi | 1.1.3 | MIT | ALLOWED | ok |
| python package | watchdog | 6.0.0 | Apache-2.0 / Apache Software License | ALLOWED | ok |
| python package | watchfiles | 1.3.0 | MIT / MIT License | ALLOWED | ok |
| python package | wcwidth | 0.8.4 | MIT / MIT License | ALLOWED | ok |
| python package | weasel | 1.0.0 | MIT | ALLOWED | ok |
| python package | websockets | 17.1 | BSD-3-Clause | ALLOWED | ok |
| python package | wheel | 0.48.0 | MIT License | ALLOWED | ok |
| python package | wrapt | 2.4.1 | BSD-2-Clause | ALLOWED | ok |
| python package | xformers | 0.0.32.post1 | - | UNKNOWN | blocked, unreviewed |
| python package | xgrammar | 0.1.25 | - | UNKNOWN | blocked, unreviewed |
| python package | xxhash | 4.0.1 | - | UNKNOWN | blocked, unreviewed |
| python package | yarl | 1.25.1 | - | UNKNOWN | blocked, unreviewed |
| server or binary | alertmanager | 0.34.1 | - | UNKNOWN | blocked, unreviewed |
| server or binary | apt:autossh | 1.4g-1 | - | UNKNOWN | blocked, unreviewed |
| server or binary | apt:cron | 3.0pl1-184ubuntu2 | Paul-Vixie's-license; Paul-Vixie's-license and GPL-2+ and ISC; GPL-2+; Artistic | REVIEW | blocked |
| server or binary | apt:curl | 8.5.0-2ubuntu10.13 | curl; OLDAP-2.8; ISC; GPL-2+ with Autoconf-data exception | REVIEW | blocked |
| server or binary | apt:git | 1:2.43.0-1ubuntu7.3 | GPL-2; BSD-3-clause; Zlib; LGPL-2.1+ | REVIEW | blocked |
| server or binary | apt:libcurl4t64 | 8.5.0-2ubuntu10.13 | curl; OLDAP-2.8; ISC; GPL-2+ with Autoconf-data exception | REVIEW | blocked |
| server or binary | apt:libgl1 | 1.7.0-1build1 | MIT; Apache-2.0; public-domain; GPL-3+ | REVIEW | blocked |
| server or binary | apt:libglib2.0-0t64 | 2.80.0-6ubuntu3.8 | LGPL-2.1+; old-glib-tests; AFL-2.0 or LGPL-2.1+; Janik-permissive | ALLOWED WITH NOTICES | ok |
| server or binary | apt:openjdk-21-jre-headless | 21.0.12+8-1~24.04 | - | UNKNOWN | blocked, unreviewed |
| server or binary | apt:postgresql-16 | 16.15-0ubuntu0.24.04.1 | PostgreSQL; PostgreSQL and Custom-regex and Tcl; Custom-pg_dump; BSD-3-Clause | NOT IN POLICY | blocked |
| server or binary | apt:postgresql-client-16 | 16.15-0ubuntu0.24.04.1 | PostgreSQL; PostgreSQL and Custom-regex and Tcl; Custom-pg_dump; BSD-3-Clause | NOT IN POLICY | blocked |
| server or binary | apt:python3-venv | 3.12.3-0ubuntu2.1 | - | UNKNOWN | blocked, unreviewed |
| server or binary | apt:redis-server | 5:7.0.15-1ubuntu0.24.04.4 | BSD-3-clause; BSD-2-clause; MIT | ALLOWED | ok |
| server or binary | bitsandbytes | 0.50.2 | - | UNKNOWN | blocked, unreviewed |
| server or binary | caddy | 2.11.4 | - | UNKNOWN | blocked, unreviewed |
| server or binary | cloudflared | 2026.9.3 | - | UNKNOWN | blocked, unreviewed |
| server or binary | fhir-validator | 6.10.4 | - | UNKNOWN | blocked, unreviewed |
| server or binary | go | 1.27.1 | - | UNKNOWN | blocked, unreviewed |
| server or binary | grafana | 13.2.2 | AGPL-3.0 | REVIEW | ok (grafana) |
| server or binary | image:postgres | 16 | - | UNKNOWN | blocked, unreviewed |
| server or binary | image:redis | 7.2-alpine | BSD-3-Clause (Redis 7.2 and earlier); RSALv2 / SSPLv1 from 7.4 | REVIEW | ok (redis) |
| server or binary | llmcompressor | 0.7.1.4 | - | UNKNOWN | blocked, unreviewed |
| server or binary | loki | 3.7.8 | - | UNKNOWN | blocked, unreviewed |
| server or binary | minio | RELEASE.2025-10-15T17-29-55Z | AGPL-3.0 | REVIEW | ok (minio) |
| server or binary | prometheus | 3.14.0 | - | UNKNOWN | blocked, unreviewed |
| server or binary | quant-transformers | 4.55.2 | Apache 2.0 License | ALLOWED | ok |
| server or binary | tempo | 3.0.3 | - | UNKNOWN | blocked, unreviewed |
| server or binary | torch | 2.8.0+cu128 | BSD-3-Clause / BSD License | ALLOWED | ok |
| server or binary | torchaudio | 2.8.0+cu128 | BSD License | ALLOWED | ok |
| server or binary | torchvision | 0.23.0+cu128 | BSD | ALLOWED | ok |
| server or binary | transformers | 5.16.1 | Apache 2.0 License | ALLOWED | ok |
| server or binary | vllm | 0.11.0 | - | UNKNOWN | blocked, unreviewed |
| server or binary | vllm-transformers | 4.57.6 | Apache 2.0 License | ALLOWED | ok |
| model | Qwen/Qwen2.5-VL-7B-Instruct | cc594898137f460bfe9f0759e9844b3ce807cfb5 | Apache-2.0 (Hugging Face metadata; the repository has NO LICENSE file) | ALLOWED | ok |
| model | en_core_web_lg | - | - | UNKNOWN | blocked, unreviewed |
| model | microsoft/trocr-base-handwritten | eaacaf452b06415df8f10bb6fad3a4c11e609406 | MIT (model card); fine-tuned on the IAM dataset, which is non-commercial researc | REVIEW | blocked |
| model | obi/deid_roberta_i2b2 | - | - | UNKNOWN | blocked, unreviewed |
| model | Qwen/Qwen2.5-VL-7B-Instruct-AWQ | - | Qwen LICENSE AGREEMENT (commercial use allowed; licence request above 100 millio | REVIEW | ok (qwen-awq) |
| data set | medicine catalogue (1mg-derived) | - | Proprietary: 1mg terms of use prohibit commercial reuse of the content and autom | REVIEW | blocked (onemg) |
| data set | lab-test master (terminology/data/lab_tests.csv) | - | Own work (engineer-written, not clinician-reviewed); the LOINC codes in it are u | ALLOWED | ok |
| data set | preparation phrases (extract/data/preparation_phrases.json) | - | Own work (engineer-written) | ALLOWED | ok |
| data set | indication groups (extract/data/indication_groups.json) | - | Own work (engineer-written) | ALLOWED | ok |
| code list | LOINC | - | LOINC licence (custom terms: free, commercial use allowed, notices required) | NOT IN POLICY | ok (loinc) |
| other | NVIDIA driver, CUDA runtime and cuDNN | - | Proprietary: NVIDIA EULAs; the CUDA EULA's Attachment A lists the redistributabl | REVIEW | ok (nvidia), unreviewed |
