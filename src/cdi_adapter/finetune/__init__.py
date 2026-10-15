"""Fine-tune any base model on the same data, with one command (backlog SW-S3).

* ``dataset``  - the training data, model-neutral: a picture and its correct answer;
* ``adapters`` - one adapter per model family: how that family wants an example written
  (its chat or prompt format), built at training time, and how it is loaded and stepped;
* ``run``      - the licence check, the smoke test, the training loop, the report;
* ``python -m cdi_adapter.finetune`` - the command.

Heavy libraries (torch, transformers, peft) are imported only inside an adapter's ``prepare``
and ``step``, so the harness, its checks and its report work - and are tested - without a GPU.
"""
