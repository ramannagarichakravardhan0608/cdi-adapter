# The swap points and their interfaces (backlog SW-S1)

Every part of the product that may have to be replaced - for a licence, a price or a quality
reason - sits behind a small written interface and is chosen by **one setting**. To replace one:

1. write an adapter that implements the interface;
2. run it through the interface's **contract test**;
3. register it under a name;
4. set the setting to that name.

No other code changes. The list is `cdi_adapter.components.POINTS`; the service shows it at
`GET /api/components` (choice made, version, choices there are), and **refuses to start** when
a setting names something unknown or something whose library is not installed - the message
names the setting.

| Swap point | Setting | Choices today |
|---|---|---|
| PDF renderer | `CDI_PDF_RENDERER` | `pypdfium2` |
| Object store | `CDI_OBJECT_STORE` | `s3`, `fs` |
| Printed-text reader | `CDI_OCR_ENGINE` | `rapidocr`, `vlm` |
| Handwriting line reader | `CDI_TWO_READERS` | `trocr`, `none` |
| Vision-language model | `CDI_MLSERVE_BACKEND` | `stub`, `hf`, `vllm` |
| Job queue | `CDI_JOB_EXECUTION` | `inprocess`, `queue` (server: `CDI_QUEUE_SERVER` = `redis` 7.2 or `valkey`) |
| Terminology provider | `CDI_TERMINOLOGY_PACKAGE` | internal canonical vocabulary; SNOMED CT / ICD-10 only with `CDI_LICENSED_CODE_SYSTEMS` |
| Drive connector | `CDI_LISTENER_CONNECTOR` | `local`, `onedrive`, `sharepoint`, `gdrive`, or a class of your own |
| Sign-in provider | `CDI_AUTH_PROVIDER` | `local`, `oidc` |
| Metrics sink | (fixed) | `prometheus` |

Two rules hold for every adapter: it **never edits saved evidence** (a reading stays as it was
read), and **its library is named nowhere outside its own module**.

The name and version of what actually ran are saved by each stage when it runs, and every
result lists them under `engines`.

---

## PDF renderer

- **Interface:** `cdi_adapter.ingest.pdf.PdfRenderer`. **Setting:** `CDI_PDF_RENDERER`.
- **In:** the bytes of a PDF; for `render`, a resolution in dots per inch.
- **Out:** `page_count(raw)` → number of pages. `page_sizes(raw)` → each page's width and
  height in points (1/72 inch), without drawing it. `render(raw, dpi)` → one RGB PNG per page,
  in page order, at that resolution. Also `name` and `version`.
- **Errors:** bytes that are not a PDF that can be opened → `NotAPdf`. Nothing else.
- **Rules:** a PDF is read as pictures; a text layer is not used. Must be safe to call from
  several threads.
- **Add one:** `pdf.register("name", Factory, libraries=("module",), package="dist-name")`.
- **Contract test:** `tests/contracts.py::pdf_renderer_contract`.

## Object store

- **Interface:** `cdi_adapter.storage.Store`. **Setting:** `CDI_OBJECT_STORE`.
- **In / out:** `put_bytes(key, data, content_type)` replaces; `get_bytes(key)` returns exactly
  what went in; `exists(key)`; `delete(key)`; `listing()` → every `(key, size)`;
  `content_type(key)`; `ensure_bucket()`; `healthy()` → true / false. Also `name`, `version`.
- **Errors:** `get_bytes` of a key that is not there raises. `delete` of one is not an error.
  `healthy` and `exists` never raise for an ordinary "no".
- **Rules:** a key is a `/`-separated path; bytes are never re-encoded.
- **Add one:** `storage.register("name", lambda settings: MyStore(settings), libraries=(...))`.
- **Contract test:** `tests/contracts.py::object_store_contract`.
- **Today:** two adapters. `s3` (MinIO or any service that speaks the S3 API) and `fs`
  (SW-S6): the scans as files under `CDI_FILE_STORE_ROOT`, which must be on an encrypted
  volume. `fs` refuses a key with `..`, an absolute path or a backslash, writes through a
  temporary file and a move (never half a file), and keeps content types in `.content-types/`.
  Both pass `object_store_contract`; the ingest code names neither.

## Printed-text reader

- **Interface:** `cdi_adapter.ocr.rapid` - a page picture in; lines out, each with its text,
  its box and a confidence. **Setting:** `CDI_OCR_ENGINE`.
- **Errors:** a page it cannot read gives no lines, not an exception.
- **Rules:** every line is saved as read; a later reading is added beside it, never over it.
- **Contract test:** `tests/test_rd_s1_acceptance_unit.py` (the one reader there is).

## Handwriting line reader

- **Interface:** `cdi_adapter.recognition.engines` - one line crop in, one reading with a
  confidence out. **Setting:** `CDI_TWO_READERS` (the model: `CDI_TROCR_MODEL_ID`).
- **Rules:** a reader never sees another reader's answer. A reader that is down gives no
  reading; the line then goes to a person.
- **Contract test:** `tests/test_rd_s2_acceptance_unit.py`.

## Vision-language model

- **Interface:** `cdi_adapter.mlserve.backends.Backend` (built by `make_backend`).
  **Setting:** `CDI_MLSERVE_BACKEND` (the model: `CDI_VLM_MODEL_ID`).
- **In:** a prompt, optionally a page picture and a JSON schema. **Out:** text; with a schema,
  JSON that fits it.
- **Errors:** an answer cut off by the length limit is marked so, never passed off as whole.
- **Contract test:** `tests/test_ml_unit.py`.

## Job queue

- **Interface:** `cdi_adapter.webapp.jobs.JobStore` (claim, save, finish, release, heartbeat,
  orphans). **Setting:** `CDI_JOB_EXECUTION` (the broker: `CDI_BROKER_URL`).
- **Rules:** an upload is stored before it is acknowledged; one runner holds a job at a time; a
  job whose runner stopped is taken up by another.
- **The server (SW-S6):** Redis 7.2 or Valkey, the same protocol. `CDI_QUEUE_SERVER` names
  which; a worker asks the server (`INFO server`) when it starts and refuses the wrong one, and
  any Redis after 7.2 (no longer open-source). `cdi_adapter.queueserver`.
- **Contract test:** `tests/test_apptier_unit.py`; the 20-job drill: `cdi_adapter.swapdrill`.

## Terminology provider

- **Interface:** `cdi_adapter.terminology` - a written test or diagnosis in; its standard name
  and code out, or "not matched" with candidates. **Setting:** `CDI_TERMINOLOGY_PACKAGE`.
- **Rules:** what was written is always kept; a doubtful match goes to a person; a code is
  never invented.
- **Codes (SW-S6):** the internal canonical vocabulary is the default
  (`terminology/data/canonical_vocabulary.csv`, `cdi_adapter.terminology.canonical`): a term is
  saved as the page's text plus its canonical name and the vocabulary's own id. SNOMED CT and
  ICD-10 are added only for a customer who holds the licence (`CDI_LICENSED_CODE_SYSTEMS`).
  LOINC (free to use) stays on lab tests and vital signs.
- **Contract test:** `tests/test_terminology_unit.py`, `tests/test_sw_s6_acceptance_unit.py`.

## Drive connector

- **Interface:** `cdi_adapter.listener.connectors.Connector`. **Setting:**
  `CDI_LISTENER_CONNECTOR` - a built-in name, or a class of your own as `package.module:Class`.
- **In / out:** list the files of a folder; read one; move one to a folder; make the folders.
- **Errors:** "too many requests" with a wait time → the listener waits and tries again.
- **Contract test:** `tests/test_ls_s1_acceptance_unit.py`.

## Sign-in provider

- **Interface:** `cdi_adapter.webapp.auth` (local accounts) and `cdi_adapter.webapp.oidc`
  (any OpenID Connect provider, by settings alone). **Setting:** `CDI_AUTH_PROVIDER`.
- **Out:** a signed-in person with one of three roles. Whoever signs a person in, the
  per-route rules are the same.
- **Contract test:** `tests/test_ent_s2_acceptance_unit.py`.

## Metrics sink

- **Interface:** `cdi_adapter.metrics` - every process serves `GET /metrics` in the Prometheus
  text format; traces can also be sent over OTLP. **Setting:** none (fixed).
- **Rules:** names and counts only; never a patient's detail.
- **Contract test:** `tests/test_monitoring_unit.py`.

---

## What is and is not done

- **Shared contract tests** - a test that takes *any* implementation - exist for the first two:
  the PDF renderer and the object store. The other eight have the tests of the one
  implementation there is; turning those into shared contracts is still to do.
- **Second implementations (SW-S6):** the filesystem store, the Valkey queue server and the
  internal vocabulary are built. There is one PDF renderer by decision (2026-10-04, no PyMuPDF).
  The swap drill (`python -m cdi_adapter.swapdrill`) runs the golden set on each default and
  alternative and explains every difference; what it cannot reach where it runs (MinIO, the
  queue servers, real drive accounts, a real identity provider) it reports as not run.
