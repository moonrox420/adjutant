"""Autonomous ad runner daemon executing hourly ticks for all active brands."""

import argparse
import logging
import signal
import time
from pathlib import Path
from typing import Any

from adjutant.config import Settings
from adjutant.db import Database
from adjutant.events import EventRegistry
from adjutant.runner import run_tick

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("runner_daemon")


class RunnerDaemon:
    def __init__(self, settings: Settings | None = None, interval_seconds: int = 60) -> None:
        self.config = settings or Settings()
        self.interval = interval_seconds
        self.running = True
        self.db = Database(self.config.database_url.get_secret_value())
        registry_path = (
            Path(__file__).resolve().parents[1] / "src" / "adjutant" / "event_registry.json"
        )
        self.events = EventRegistry(registry_path)

    def stop(self, *args: Any) -> None:
        logger.info("Shutdown signal received. Stopping autonomous runner daemon...")
        self.running = False

    def run_brand_tick(self, brand_id: Any) -> None:
        try:
            with self.db.transaction(extra_brand=brand_id) as conn:
                result = run_tick(conn, self.config, self.events, brand_id)
                logger.info(
                    "Completed autonomous tick for brand %s: status=%s, actions_executed=%s",
                    brand_id,
                    result.get("status"),
                    result.get("actions_executed", 0),
                )
        except Exception as exc:
            logger.error("Error executing tick for brand %s: %s", brand_id, exc, exc_info=True)

    def run_sweep(self) -> None:
        try:
            with self.db.transaction() as conn:
                active_brands = conn.execute(
                    """SELECT id, display_name FROM brand 
                    WHERE status = 'active' AND campaigns_enabled = true 
                    ORDER BY id"""
                ).fetchall()
        except Exception as exc:
            logger.error("Failed to query active brands: %s", exc)
            return

        if not active_brands:
            logger.debug("No active brands with campaigns enabled found.")
            return

        from concurrent.futures import ThreadPoolExecutor

        max_workers = min(4, len(active_brands))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.run_brand_tick, brand["id"]) for brand in active_brands]
            for f in futures:
                if not self.running:
                    break
                try:
                    f.result(timeout=120)
                except Exception as exc:
                    logger.error("Brand tick worker error: %s", exc)

    def start(self) -> None:
        self.db.open()
        logger.info(
            "Starting Adjutant autonomous ad runner daemon (interval=%ds)...", self.interval
        )
        try:
            while self.running:
                self.run_sweep()
                for _ in range(self.interval):
                    if not self.running:
                        break
                    time.sleep(1)
        finally:
            logger.info("Closing database connection pool.")
            self.db.pool.close()
            logger.info("Runner daemon terminated.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Adjutant Autonomous Runner Daemon")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds between active brand sweeps (default: 60)",
    )
    args = parser.parse_args()

    daemon = RunnerDaemon(interval_seconds=args.interval)
    signal.signal(signal.SIGINT, daemon.stop)
    signal.signal(signal.SIGTERM, daemon.stop)
    daemon.start()


if __name__ == "__main__":
    main()
