"""Explicit construction of Phase-6 persistent configuration services.

Importing this module performs no filesystem or hardware I/O.  The factory
only constructs two independent A/B stores: one for rarely changed static
configuration and one for the small Scheduler exactly-once ledger.  Callers
must still invoke ConfigManager.load() explicitly.
"""

from adapters.config_file_store import AtomicJSONConfigStore
from services.config_manager import ConfigManager


STATIC_CONFIG_BASE_PATH = "/landy_heater_config"
SCHEDULER_LEDGER_BASE_PATH = "/landy_heater_scheduler"
STATIC_CONFIG_MAX_RECORD_BYTES = 12 * 1024


def create_default_config_manager():
    """Return the production-path manager without reading or writing files."""

    # Twelve KiB accommodates the proven 8-KiB canonical application limit
    # plus the storage envelope without inviting an unbounded JSON allocation.
    # The compact Scheduler safety ledger retains the adapter default.
    config_store = AtomicJSONConfigStore(
        STATIC_CONFIG_BASE_PATH,
        max_record_bytes=STATIC_CONFIG_MAX_RECORD_BYTES,
    )
    ledger_store = AtomicJSONConfigStore(SCHEDULER_LEDGER_BASE_PATH)
    return ConfigManager(config_store, ledger_store)
