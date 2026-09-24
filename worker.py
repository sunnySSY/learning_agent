"""Development index worker.

Production deployments should run this command as a separate worker role and
replace the polling loop with Redis/PostgreSQL queue delivery.
"""

import time

from app.jobs import IndexWorker
from app.repository import ProductRepository


def main() -> None:
    repository = ProductRepository()
    worker = IndexWorker(repository)
    try:
        while True:
            worker.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        repository.close()


if __name__ == "__main__":
    main()
