# Model swap drill (backlog SW-S5)

A model change is never a gamble: a candidate is tried on real documents without anyone seeing
its answers, made champion in one step, and taken back in one step. This page is how, and the
drill that proves it works. **Do the drill once a quarter and keep the evidence** (story NFR3).

## The three moves

| Move | Command | What happens |
|---|---|---|
| **Shadow** | `CDI_SHADOW_EXTRACTION=<registry id>` and `CDI_SHADOW_URL=<the candidate's gateway>` (optionally `CDI_SHADOW_PERCENT`) | After each real result is saved, the candidate answers the same page from its own gateway. Its answer goes to the `shadow_result` table only: never into a result, a fact, a review screen or an export. A candidate that fails or is down costs a row with its error, nothing else. `python -m cdi_adapter.modelops shadow-report` shows how far it agreed with the champion, and how fast it was. |
| **Switch** | `python -m cdi_adapter.modelops switch <version> --url <gateway>` | The candidate becomes the champion for every new document within `CDI_MODEL_ROUTE_TTL_S` seconds (5 by default), with no restart. A document already being read finishes on the model it started on; the command waits until none is left (`drain`). Refused unless the model is in `models/registry.json` with its licence text saved, its gateway serves it, no canary is running, and it won a binding contest (SW-S4). `--force --reason` overrides only the contest, and is recorded. |
| **Roll back** | `python -m cdi_adapter.modelops rollback` | The previous champion takes every new document again, the same way. Its files are never deleted, and its gateway is started again first when it was stopped. The switch records "keep the previous model ready until" 30 days on (an assumption in the story; the owner decides). |

Which model made a result is always in the result (`engines`, SW-S1/SW-S2) and in the
`extraction` and `pipeline_run` rows.

## For installed customers: model packs

```bash
python -m cdi_adapter.modelops.pack make <model files> --model <registry id> --key pack-key.pem --out model.tar
python -m cdi_adapter.modelops.pack install model.tar --into <model cache> --public-key pack-key.pub.pem
```

`install` refuses, and installs nothing, when the signature does not check, when any file is
missing, added or changed, when the checksum is not the registry's, or when the registry has no
licence text for the model. It never writes over a different copy and never removes another
revision. The customer then switches by setting. Open question (story): may installed customers
switch models themselves, or only through our packs?

## The drill

On the GPU host, with both models in `models/registry.json` with their licence texts:

```bash
python scripts/model_swap_drill.py --model-id <A> --revision <sha> --candidate-model-id <B> --candidate-revision <sha> --running 5
```

It runs the same steps as the integration test (`cdi_adapter.modelops.drill.run`):

1. shadow: B answers 3 documents in shadow; every real result is A's, B's answers are saved apart;
2. switch while 5 documents are being read by A: each finishes once, on A, and names A; the next
   document goes to B;
3. roll back: new documents are A's again; the seconds it took are recorded.

The record goes to `docs/evidence/SW-S5/drill-gpu-<date>.json`. The same drill with stub models
(PostgreSQL, real gateways over HTTP, no GPU) is `tests/test_sw_s5_acceptance_integration.py`;
its record is `docs/evidence/SW-S5/drill-local-stub-models.json`.

## Not done yet

* The GPU drill has not been run: no model in `models/registry.json` has its licence text saved
  yet, so the switch and shadow mode refuse every real model - the rule working.
* A share of traffic first (hosted service) is the E10-S5 canary: `canary`, `set-percent`,
  `promote` (gated by the live comparison).
