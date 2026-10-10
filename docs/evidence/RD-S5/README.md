# RD-S5 — ten prescriptions at once, on one GPU

**MEASURED** on 2026-10-10 on one rented GPU: NVIDIA RTX PRO 4500 Blackwell (32 GB), $0.366/hr.
Model: Qwen2.5-VL-7B-Instruct. Every document was a fake single-page prescription (printed, a
made-up patient), sent as its own upload in the same second by `scripts/burst_test.py`, through
the real pipeline on the host. "Seconds" is from the upload being accepted to its result saved.

| Run | File | Saved within 60 s | Median / slowest (s) | Lost | Doubled | Peak GPU memory | Cost per document |
|---|---|---|---|---|---|---|---|
| 10 at once, serial server (as deployed before) | `hf-10` | 4 of 10 | 63.9 / 114.9 | 0 | 0 | 17.8 GB | $0.00117 |
| **10 at once, batching server (AC1)** | `vllm-10` | **10 of 10** | 39.9 / 40.9 | 0 | 0 | 27.6 GB | $0.00042 |
| 20 at once, batching server (AC2) | `vllm-20` | 20 of 20 | 22.0 / 30.5 | 0 | 0 | 27.6 GB | $0.00016 |
| 10 at once, GPU worker killed 6 s in (AC3) | `vllm-kill` | 10 of 10 | 45.2 / 47.2 | 0 | 0 | 27.6 GB | $0.00048 |
| 10 at once, batching server stopped (AC4) | `fallback` | 4 of 10 | 63.7 / 115.8 | 0 | 0 | 17.8 GB | $0.00118 |

Cost per document is the host's price for the run's wall time divided by the documents; it
leaves out the time the host sits idle.

## What each run shows

- **AC1.** With the batching server (vLLM 0.11.0, 10 sequences at once) all 10 were saved within
  41 s. With the serial server only 4 of 10 made it: it reads one document at a time, about 10 s
  each, so the tenth waits for nine others.
- **AC2.** 20 at once: all 20 finished; 10 were shown "waiting" while the first 10 were read. The
  20 finished sooner than the 10 of AC1 because AC1 was the server's first heavy load after
  starting.
- **AC3.** The GPU worker was killed (`kill -9`) 6 s in, holding 7 of the 10 documents, and
  started again 5 s later. Those 7 were read again (2 attempts each). Every document has exactly
  one extraction and one review item.
- **AC4.** The batching server was stopped before the documents were sent. They went to the
  serial server listed as the fallback (`CDI_MLSERVE_FALLBACK_URL`), which loaded the model and
  read them one at a time: all 10 finished, the slowest in 116 s. The `CdiModelServerDown` alert
  fired and reached Alertmanager (severity `page`).

## What the runs found, and what was fixed

1. **Nothing said "waiting".** A document read and waiting for the model showed as "reading".
   It now shows "waiting".
2. **The serial server took half of every burst.** Listed as a second address in
   `CDI_MLSERVE_URL`, it was treated as an equal replica and given every other call - and could
   not load the model while the batching server held the GPU: 5 of 10 documents failed. A
   fallback server now has its own setting and is used only when every replica has refused.
3. **The fallback was ignored for extraction.** Extraction calls go through the rollout's
   routed client, which the first version of the fallback did not cover. Fixed.
4. **The batching server's install was incomplete on this host.** The pinned install left out
   torch's NVIDIA libraries; they were added by hand from the main environment's versions.

## Limits

- Printed fake prescriptions, one page each. Real handwritten ones carry more text and take
  longer; this says nothing about them.
- One GPU model (a 32 GB card). A 24 GB card has less room for sequences in flight.
- `CdiModelServerFailover` (the ticket that says the fallback is in use) did not fire: its
  counter first appears at the first failover, and an increase cannot be worked out from a
  series that has only just appeared. `CdiModelServerDown` did fire.
- The fallback only works when the batching server has gone (its GPU memory is free). A batching
  server that is up but answering with errors leaves the fallback unable to load.
- "GPU memory before: 0 MiB" in the `vllm-10` report is a sampling artifact (the first sample
  was taken while the app tier was restarting); the peak is right.
- An earlier AC3 attempt killed nothing (a bug in the harness's process lookup), and an earlier
  AC4 attempt was spoiled by a stray worker that harness had left behind. Both were fixed and
  the runs repeated; only the repeated runs are kept here.
