# ENT-S1 — Commercial licence audit: what was built and where it stands

Written 2026-10-13. Story: *Commercial licence audit of every software, model, data set and
code list* (epic ENT, Enterprise readiness).

> **This is not legal advice.** The tool lists the evidence and blocks what nobody has decided.
> Counsel decides. Every licence in the register was copied from the owner's *Licence Register*
> and *SBOM* sheets (evidence dated 2026-10-03 / 2026-10-04) or read from a package's own
> metadata. Nothing was filled in from memory; where there is no evidence the licence is
> recorded as unknown and the build is blocked.

---

## 1. The short version

| | |
|---|---|
| **What exists now** | A licence gate and report that run from the build files, a written policy, a register, a `NOTICE` file, a checker for an installed-version package, and a CI job. |
| **Result on today's repository** | **Build gate: BLOCKED — 94 items.** **Release gate: BLOCKED — 112 items.** |
| **Why it is blocked** | Not because the tool is broken — because 94 components have no recorded licence, no evidence, or no decision. Each one needs a person. |
| **Tests** | `tests/test_ent_s1_acceptance_unit.py`: 75 passed, 3 expected-fails (one for each criterion the real repository does not meet yet). |
| **Committed?** | No. Nothing is committed or pushed. The Excel workbook was only read, from a temporary copy. |

### Acceptance criteria

| AC | What it asks | Status | Where it stands |
|---|---|---|---|
| AC1 | Every component has a verdict and none is unreviewed | **PARTIAL** | All 257 components get a verdict. 85 of them are `UNKNOWN` and 86 are unreviewed. |
| AC2 | A review-list licence fails the build until a decision is recorded | **PASS** | Proven on made-up repositories, and it is blocking the real one today. |
| AC3 | Each register decision is applied, or has an owner and a date | **PARTIAL** | Of the 10 decisions the build can check, 4 are applied and 6 are open. None of the open ones has an owner or a date — nobody has been named. |
| AC4 | A model or package added to a build needs a register entry, or the build fails | **PASS** | Also catches a package added only to a lock file, a model named only in an env file, and a server added to the version pins. |
| AC5 | The installed package has the NOTICE file, the third-party licence texts and only redistributable NVIDIA files | **PARTIAL** | The checker and the bundle work. No installed-version package is built by this repository yet, and the NVIDIA list is not established. |

---

## 2. What was built

### 2.1 Files

| File | What it is |
|---|---|
| `scripts/licence_audit.py` | The audit, the gate and the helper commands (section 2.3). |
| `licences/policy.json` | The written licence policy (section 2.2). |
| `licences/packages.json` | The licence of each Python package, with where and when it was read. 245 entries: 215 from the SBOM sheets, 30 read from packages installed on this machine. |
| `licences/register.json` | Everything that is not a Python package — servers, models, data sets, code lists — plus the decisions, the sign-off and the IP-file placeholder. |
| `licences/nvidia_redistributable.json` | The NVIDIA files an installed package may carry. Empty today (section 5). |
| `NOTICE` | The notice file, generated from the report. Now included in every built package through `pyproject.toml`. |
| `.github/workflows/ci.yml` | New job `licence-gate`: runs the gate and keeps the report with the run. |
| `Makefile` | New target `make licences`. |
| `docs/evidence/ENT-S1/licence-report.md` and `.json` | Today's full report: every component, its licence, its verdict. |
| `tests/test_ent_s1_acceptance_unit.py` | The acceptance tests. |

### 2.2 The policy

| Verdict | Licences | What happens |
|---|---|---|
| **ALLOWED** | MIT, BSD, Apache-2.0, ISC, PostgreSQL, OFL (fonts), our own work | Passes. Ship the licence text and any notice. |
| **ALLOWED WITH NOTICES** | LGPL, used unmodified | Passes. Ship its licence text and notices; never fold modified LGPL code into our own files. |
| **NOT IN POLICY** | Anything the policy does not name — MPL, PSF, HPND, Unlicense, custom terms | **Blocked** until a decision is recorded. |
| **REVIEW** | GPL, AGPL, SSPL, RSAL, non-commercial, research-only, proprietary or gated | **Blocked** until a decision is recorded. AGPL tools are never bundled. |
| **UNKNOWN** | No evidence | **Blocked**. Never guessed. |

A *decision* counts only when it says **what** was decided, **who** decided and **when**. A
decision *not to use* something keeps blocking for as long as that thing is still in the build.

When a licence field offers a choice ("MIT or GPL"), the allowed side is taken. When it lists
several that all apply ("Apache-2.0 and GPL"), all must be acceptable.

### 2.3 The commands

```
python scripts/licence_audit.py check              # the gate: exit 1 when blocked
python scripts/licence_audit.py check --release    # stricter: nothing unreviewed, every decision
                                                   # applied or owned and dated, signed off
python scripts/licence_audit.py check --report D   # also writes D/licence-report.md and .json
python scripts/licence_audit.py record             # add the licences of packages installed HERE
                                                   # to licences/packages.json (run it on the pod)
python scripts/licence_audit.py notice             # rewrite the NOTICE file
python scripts/licence_audit.py bundle --out DIR   # NOTICE + third-party licence texts + SBOM
python scripts/licence_audit.py inspect PATH       # is an installed-version package complete?
python scripts/licence_audit.py sbom --out FILE    # what is installed on this machine
```

### 2.4 What the gate reads

It reads the **build files**, not the machine it runs on, so the answer is the same in CI, on a
laptop and on the pod:

- **Python packages** — `pyproject.toml` (dependencies and every extra) and the three pod lock
  files (`requirements.lock`, `requirements-vllm.lock`, `requirements-quant.lock`).
- **Servers and binaries** — the `*_VERSION` / `*_TAG` keys and the apt pins of
  `infra/runpod/versions.env` and `infra/ingress/versions.env`, and the images in the compose file.
- **Models** — every model id in `src/cdi_adapter/config.py`, and any `CDI_*MODEL*=` line in an
  env or shell file under `infra/`.
- **Data sets and code lists** — the entries in `licences/register.json`.

---

## 3. Today's result in numbers

257 components: 213 Python packages, 33 servers and binaries, 5 models, 4 data sets,
1 code list, 1 other (the NVIDIA libraries).

| Verdict | Count |
|---|---|
| ALLOWED | 148 |
| ALLOWED WITH NOTICES | 3 (`psycopg`, `psycopg-binary`, `libglib2.0`) |
| NOT IN POLICY | 9 |
| REVIEW | 12 |
| UNKNOWN | 85 |

Six Python packages with a licence outside the policy (`certifi`, `filelock`, `greenlet`,
`pillow`, `tqdm`, `typing-extensions`) pass on the Licence Register's own row "Other Python
packages — GREEN", dated 2026-10-04.

---

## 4. What is blocked, and what each item needs

### 4.1 Found by the gate — not in the Licence Register at all

| Item | What was found | What it needs |
|---|---|---|
| Model `obi/deid_roberta_i2b2` | Named in `config.py` for de-identification. No register entry. Its name says it was trained on i2b2 data. | Read its licence and its training-data terms; add an entry. |
| Model `en_core_web_lg` | The spaCy model used by de-identification. No register entry. | Read its licence; add an entry. |
| Loki and Tempo | Pinned in `versions.env` beside Grafana, which the register records as AGPL. Their own licence was never read. | Read their licences. If AGPL, the Grafana decision ("do not bundle") applies to them too. |

### 4.2 In the register, but the build contradicts the decision

| Decision in the register | What the build still has | Where |
|---|---|---|
| **1mg catalogue — do not use** | The medicine catalogue is built on 1mg-derived data. | `src/cdi_adapter/catalogue/` (`normalize.py`, `service.py`), `config.py` line 288 |
| **OpenCV — headless wheel only** | The non-headless `opencv-python` is in the pod lock next to the headless one. | `infra/runpod/requirements.lock` |
| **Grafana — do not bundle** | Grafana 13.2.2 is pinned and its provisioning is shipped. | `infra/runpod/versions.env`, `infra/monitoring/grafana/` |
| **Qwen 3B — remove** | Still the default model of a drill script. | `scripts/model_rollout_drill.py` line 81 |
| **MinIO — not mandatory** | The only store is the S3 client. Any S3 service works, but there is no filesystem store. | `src/cdi_adapter/storage.py` |
| **ICD-10 / SNOMED — optional adapter only** | The terminology seed carries these code systems. | `src/cdi_adapter/terminology/seed.py` lines 18 and 20 |

Each of these needs either the change made, or an owner and a date recorded in
`licences/register.json` (`owner`, `due`).

### 4.3 In the register, but no decision recorded

| Item | Why it blocks |
|---|---|
| `microsoft/trocr-base-handwritten` | Marked MIT, but fine-tuned on IAM, which is non-commercial research data. The register says "AMBER: review — counsel to decide". No decision is recorded. |

### 4.4 Python packages with no licence recorded — 68

All are in the vLLM or quantisation lock files. They were not on the pod when the SBOM sheet
was made and are not installed on this Windows machine, so their licence has never been read.

**How to clear them:** run `python scripts/licence_audit.py record` inside each pod environment
(`.venv-vllm`, `.venv-quant`). It reads each package's own metadata and writes it to
`licences/packages.json`. Anything it records as GPL, MPL and so on will then ask for a decision.

<details><summary>The 68 packages</summary>

agent-detector, aiohappyeyeballs, aiohttp, aiosignal, astor, blake3, cachetools, cbor2,
cloudpickle, compressed-tensors, cuda-pathfinder, cupy-cuda12x, datasets, depyf,
detect-installer, dill, diskcache, dnspython, email-validator, fastapi-cli, fastapi-cloud-cli,
fastar, frozendict, frozenlist, gguf, httpcore2, httpx2, interegular, jiter, llguidance,
llmcompressor, llvmlite, lm-format-enforcer, loguru, mistral-common, msgspec, multidict,
multiprocess, ninja, numba, openai, openai-harmony, outlines-core, pandas, partial-json-parser,
prometheus-fastapi-instrumentator, propcache, py-cpuinfo, pyarrow, pybase64, pycountry,
pydantic-extra-types, pynvml, ray, rich-toolkit, rignore, scipy, sentry-sdk, setproctitle,
soundfile, soxr, tiktoken, truststore, vllm, xformers, xgrammar, xxhash, yarl

</details>

Three more (`vllm`, `bitsandbytes`, `llmcompressor`) are also pinned by version in
`versions.env` and show up a second time there.

### 4.5 Servers and system packages — licence never read (12)

`alertmanager`, `caddy`, `cloudflared`, `fhir-validator`, `go`, `loki`, `prometheus`, `tempo`,
the `postgres` Docker image, and the apt packages `autossh`, `openjdk-21-jre-headless`,
`python3-venv`.

Most of these are widely known projects, but nobody has read the licence from its source and
recorded it. The story's rule is explicit: do not guess, mark unknown and block.

### 4.6 System packages that need a recorded decision (7)

| Package | Licence as read on the pod | Verdict |
|---|---|---|
| `git` | GPL-2; BSD-3-clause; Zlib; LGPL-2.1+ | REVIEW |
| `curl`, `libcurl4t64` | curl; OLDAP-2.8; ISC; GPL-2+ with Autoconf exception | REVIEW |
| `cron` | Paul Vixie's licence and others | REVIEW |
| `libgl1` | MIT; Apache-2.0; public-domain; GPL-3+ | REVIEW |
| `postgresql-16`, `postgresql-client-16` | PostgreSQL and custom terms (regex, Tcl) | NOT IN POLICY |

These are separate operating-system programs, not code linked into ours, so the likely decision
is "accepted as a separate process" — but that decision is counsel's to record, not the tool's.

---

## 5. The register's decisions, one by one

| Decision | Status today | How it is checked |
|---|---|---|
| Replace PyMuPDF with pypdfium2 | **APPLIED** | Not in `pyproject.toml`, not in any lock, not imported anywhere. |
| Never train on IAM | **APPLIED** | No training code refers to the IAM data set. (TrOCR's own history is a separate open item.) |
| Pin Redis to 7.2 or older | **APPLIED today** | The compose file used the floating tag `redis:7-alpine`, which moves on to the RSAL / SSPL series. Changed to `redis:7.2-alpine`. The pod's apt pin was already 7.0.15. |
| Ship a NOTICE file | **APPLIED today** | `NOTICE` written and added to the package build. |
| Remove the Qwen 3B fallback | **OPEN** | The `CDI_VLM_FALLBACK_MODEL_ID` line was removed from `.env.example` and `.env.runpod` today. One reference remains: the drill script's default model. |
| Object store pluggable, MinIO not mandatory | **OPEN** | Needs a second store. Left alone: the project rule is never to substitute MinIO without the owner's say. |
| Do not bundle Grafana | **OPEN** | Still pinned and provisioned. |
| OpenCV headless only | **OPEN** | Non-headless wheel in the pod lock. The lock is generated, so the fix is in what pulls it in, not a hand edit. |
| Do not use the 1mg catalogue | **OPEN** | The catalogue code uses it. |
| ICD-10 / SNOMED optional only | **OPEN** | Depends on SW-S6, which is not built. |
| Qwen AWQ build — conditions | Not checkable by the build | Needs an owner and a date. |
| LOINC — keep notices | Not checkable by the build | Needs an owner and a date. |
| NVIDIA — only redistributable files | Not checkable by the build | Needs the EULA read and the file list written. |

---

## 6. The installed-version package (AC5)

`inspect` reports a package as incomplete when any of these is true:

1. there is no `NOTICE` file, or it is empty;
2. a Python package the SBOM says is shipped has no licence text under `THIRD_PARTY_LICENSES/`;
3. it contains an NVIDIA file that is not on the redistributable list.

It accepts a folder, a zip, a wheel or a tar file.

**The NVIDIA list is empty.** The register records that the CUDA EULA was not read in full and
the exact files were never listed. Until someone reads it and writes the file names into
`licences/nvidia_redistributable.json`, the rule is the safe one: an installed package may
contain **no** NVIDIA file at all, and the customer's machine provides the driver and runtime.

**`bundle` on this machine** collected licence texts for 122 of the 208 shipped packages —
including PDFium's licence files (from `pypdfium2`) and the LGPL text (from `psycopg`). The other
86 are packages not installed here; the bundle must be made on the pod to be complete.

**What is missing for AC5:** this repository does not build an installed-version package yet.
There is nothing real to inspect.

---

## 7. Other changes made along the way

| Change | Why |
|---|---|
| `infra/compose/docker-compose.yml`: `redis:7-alpine` → `redis:7.2-alpine` | The Redis decision. |
| `.env.example`, `.env.runpod`: removed `CDI_VLM_FALLBACK_MODEL_ID` | The Qwen 3B decision. This also fixes `tests/test_ops_unit.py::test_env_runpod_keys_are_all_real_settings`, which was failing before today. |
| `pyproject.toml`: `license-files = ["NOTICE"]` | NOTICE goes into every wheel and source package. |
| `tests/test_ops_unit.py`, `docs/TECH_STACK.md` | Updated to the new Redis tag. |

Your local `.env` still has the fallback-model line. It is your own file and was not touched.

---

## 8. Not done

| Requirement | State |
|---|---|
| FR6 / FR9 — model source, checksum, saved licence copy | Revisions are recorded for Qwen 7B and TrOCR (from the pod SBOM sheet). No checksum and no saved licence text for any model. The release gate lists each. |
| FR10 — SBOM from the real deployed image | The `sbom` command is written (Python packages, system packages, NVIDIA libraries with checksums). It has **not** been run on a pod; no instance was up. |
| FR11 — IP file | A placeholder only. Signed assignments, the prior-art search and the no-disclosure rule are the owner's and counsel's. |
| Release sign-off | Blank. Needs a person with legal authority. |
| Copyright line in `NOTICE` | Blank. The legal name of the owner is not recorded anywhere in the repository. |
| Source offers for copyleft components | Stated in the policy; nothing generates them yet. |

---

## 9. What to do next, in order

1. **On the pod:** run `python scripts/licence_audit.py record` in each environment, then
   `sbom --out` and `bundle --out`. This clears most of the 68 packages and gives the real SBOM.
2. **Read and record** the licences of the 12 servers in section 4.5 and the two models in 4.1.
3. **Decide** (owner and counsel): TrOCR, the 1mg catalogue, the 7 system packages.
4. **Name an owner and a date** for each open decision in section 5, or make the change.
5. **Read the NVIDIA EULA** and list the redistributable files.
6. **Sign off** in `licences/register.json` before a release.

Until steps 1–3 are done the `licence-gate` CI job will be red on every push. That is the
story's intent ("block a release if anything is unreviewed"); it is a separate job and does not
change the result of the unit-test job.

---

## 10. How this was tested

`tests/test_ent_s1_acceptance_unit.py` — 75 passed, 3 expected-fails.

- **The policy:** 26 licence strings, each checked against the verdict it must get — including
  that LGPL is not mistaken for GPL, and that a choice ("MIT or GPL") takes the allowed side.
- **AC1–AC4:** each rule shown alone on a small made-up repository — a clean build passes; an
  unknown licence blocks; a review-list licence blocks until a decision with what/who/when is
  recorded; a decision not to use something keeps blocking; a new package, lock-file entry,
  model or server with no register entry blocks.
- **AC3:** each register decision has a check that reads the repository; an unapplied decision
  is reported with what was found, and passes the release gate only once it has an owner and a date.
- **AC5:** a complete package passes; a missing notice, a missing licence text and six kinds of
  NVIDIA file are each caught; ordinary files such as `libcurl.so` are not mistaken for NVIDIA ones.
- **This repository:** the report covers all six kinds of component; the four applied decisions
  are reported as applied; the gate finds the two unregistered models, TrOCR and the 1mg catalogue.
- **The three expected-fails** state plainly that AC1, AC3 and AC5 are not met on the real
  repository, and why.

Also run: the ops, infrastructure and OUT story suites — 397 passed, 0 failed. Lint and type
check are clean on every file changed.
