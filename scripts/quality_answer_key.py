"""The picture check's false-reject rate on an answer-key set (backlog IM-S1 AC3).

    python scripts/quality_answer_key.py --key data/samples/quality_answer_key.sample.json
    python scripts/quality_answer_key.py --key my_photos/answer_key.json --json

The key is a JSON list of {"image": path, "readable": true|false, "doc_type": ...}, with paths
relative to the key file. Reads pictures only; writes nothing. What is counted, and why the
false-accept rate is printed beside the false-reject rate, is in
``cdi_adapter/recognition/quality_eval.py``.
"""

from cdi_adapter.recognition.quality_eval import main

if __name__ == "__main__":
    raise SystemExit(main())
