"""``python -m cdi_adapter.ocrhost`` — the OCR host on localhost:``CDI_OCR_HOST_PORT`` (8079)."""

import uvicorn

from cdi_adapter.config import get_settings
from cdi_adapter.logging import configure


def main() -> None:
    configure()
    uvicorn.run("cdi_adapter.ocrhost.app:app", host="127.0.0.1", port=get_settings().ocr_host_port)


if __name__ == "__main__":
    main()
