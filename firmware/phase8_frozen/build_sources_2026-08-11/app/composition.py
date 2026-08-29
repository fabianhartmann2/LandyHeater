"""Explicit, side-effect-free composition helpers for Landy Heater.

Importing this module never opens hardware.  The factory below is deliberately
TX-locked for the current bring-up milestone and opens UART only when called.
"""


def open_tx_locked_protocol_service():
    """Open the current board transport only while protocol TX is locked.

    This production-facing factory accepts no injected configuration, writer,
    transport, or authorization capability.  A future TX-enabled composition
    requires a separate, deliberate implementation change after hardware
    approval; changing the mutable board flag is not enough here.
    """

    import board_config

    if board_config.UART_PROTOCOL_TX_ENABLED is not False:
        raise RuntimeError(
            "safe composition requires UART_PROTOCOL_TX_ENABLED=False"
        )

    from protocol.autoterm_service import AutotermProtocolService
    from protocol.uart_transport import open_from_board_config

    transport = open_from_board_config()
    try:
        if transport.tx_enabled is not False:
            raise RuntimeError("safe composition received TX-enabled transport")
        return AutotermProtocolService(transport)
    except BaseException as primary_error:
        # Cleanup is part of the safety boundary: once UART has been opened,
        # every rejected construction path must close it.  A second bounded
        # attempt handles transports whose cleanup is deliberately retryable.
        cleanup_error = None
        for _ in range(2):
            try:
                deinit = getattr(transport, "deinit", None)
                if not callable(deinit):
                    raise RuntimeError(
                        "rejected transport has no deinit()"
                    )
                deinit()
                cleanup_error = None
                break
            except BaseException as error:
                cleanup_error = error

        if cleanup_error is not None:
            raise RuntimeError(
                "safe composition failed ({0}); transport cleanup also "
                "failed ({1})".format(primary_error, cleanup_error)
            )
        raise
