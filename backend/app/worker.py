"""Background worker: outbox dispatch + scheduled workflow jobs.

STUB: loops without doing work. Replaced by the workflows implementation.
"""

import logging
import time

log = logging.getLogger("worker")


def run_once() -> None:
    pass


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("worker started")
    while True:
        run_once()
        time.sleep(2)


if __name__ == "__main__":
    main()
