"""Autostart entry point for the accepted DFR0975-U product image."""

import sys


def _prefer_frozen_modules():
    if ".frozen" in sys.path:
        sys.path.remove(".frozen")
    sys.path.insert(0, ".frozen")


def main():
    _prefer_frozen_modules()
    from app.product_runtime import build_product_runtime
    from time import sleep_ms

    runtime = None
    try:
        runtime = build_product_runtime()
        print("LANDY_HEATER_PRODUCT_AUTOSTART_V1")
        runtime.run_forever()
    except KeyboardInterrupt:
        print("LANDY_HEATER_PRODUCT_STOP_REQUESTED_V1")
    except BaseException as error:
        print("LANDY_HEATER_PRODUCT_FAULT_V1", type(error).__name__)
    if runtime is not None:
        while not runtime.shutdown_step():
            sleep_ms(100)
        print("LANDY_HEATER_PRODUCT_STOPPED_SAFE_V1")


if __name__ == "__main__":
    main()
