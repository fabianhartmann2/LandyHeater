"""Failure-only, bounded diagnostics for the Phase-8 product smoke."""

import gc as _gc


FULL_REST_PHONE_FAILURE_STAGE_TOKEN = (
    "PHASE8_FULL_REST_PHONE_FAILURE_STAGE_V1"
)

_ALLOWED_STAGES = (
    "stage1_preflight",
    "stage1_ap_startup",
    "stage1_observe_deadline",
    "stage1_observe_network_step",
    "stage1_observe_network_truth",
    "stage1_confirm_association",
    "preflight_product",
    "preflight_storage",
    "preflight_wifi",
    "rest_composition",
    "http_bind",
    "confirm_association",
    "observe_timeout",
    "observe_network",
    "observe_http_step",
    "observe_http_transport",
    "observe_security",
    "observe_route_binding",
    "observe_post_response",
    "cleanup_http",
    "cleanup_rest",
    "cleanup_wifi",
    "cleanup_storage",
    "cleanup_state",
    "postflight_safe_state",
)

_ALLOWED_HTTP_ERRORS = (
    "accept_failed",
    "accept_contract_failed",
    "accepted_socket_rejected",
    "application_handle_failed",
    "client_close_contract_failed",
    "client_close_failed",
    "client_recv_budget_exceeded",
    "client_recv_contract_failed",
    "client_recv_failed",
    "client_send_closed",
    "client_send_contract_failed",
    "client_send_failed",
    "listener_close_contract_failed",
    "listener_close_failed",
    "listener_start_failed",
    "rejected_socket_cleanup_failed",
    "rejected_socket_close_contract_failed",
    "rejected_socket_close_failed",
    "request_parser_contract_failed",
    "response_contract_failed",
    "server_lifecycle_reentrancy_detected",
    "server_reentrancy_detected",
    "truncated_request",
    "write_timeout",
)

_HEAP_KEYS = (
    "memory_before",
    "memory_after_wifi_factory",
    "memory_after_ap_ready",
    "memory_after_client_association",
    "memory_after_product_imports",
    "memory_after_configuration_adoption",
    "memory_before_http_start",
    "memory_after_proof_before_listen",
    "memory_after_http_bind",
    "memory_after_response",
    "memory_after_cleanup",
    "memory_after_failure_cleanup",
)


def memory_free_no_collect():
    reader = getattr(_gc, "mem_free", None)
    if not callable(reader):
        return -1
    try:
        value = reader()
    except BaseException:
        return -1
    return value if type(value) is int and value >= 0 else -1


def _counter(value):
    if type(value) is int and 0 <= value <= 1000000:
        return value
    return -1


def _boolean(value):
    if value is True:
        return 1
    if value is False:
        return 0
    return -1


def _http_last_error(value):
    if value is None or value == "none":
        return "none"
    if value in _ALLOWED_HTTP_ERRORS:
        return value
    return "other"


def capture(
    stage,
    server_snapshot,
    socket_factory,
    gateway,
    ap_client_confirmed,
    post_bind_peer_confirmed,
    response_completed,
    heap_values=None,
    stage1_values=None,
    cleanup_confirmed=False,
):
    """Copy only fixed enums, booleans and bounded integers."""

    values = {
        "stage": stage if stage in _ALLOWED_STAGES else "other",
        "ap_client_associated": _boolean(ap_client_confirmed),
        "association_after_bind": _boolean(post_bind_peer_confirmed),
        "response_completed": _boolean(response_completed),
        "cleanup_success": _boolean(cleanup_confirmed),
        "stage1_client_seen": -1,
        "stage1_ap_clients": -1,
        "stage1_action": "none",
        "stage1_association_confirmed": -1,
        "http_started": -1,
        "http_closed": -1,
        "http_faulted": -1,
        "http_clients": -1,
        "http_accept_actions": -1,
        "http_accepted": -1,
        "http_recv_actions": -1,
        "http_send_actions": -1,
        "http_completed": -1,
        "http_parse_errors": -1,
        "http_timeouts": -1,
        "http_socket_errors": -1,
        "http_reentries": -1,
        "http_last_error": "none",
        "listener_factory_returned": -1,
        "listener_setblocking_returned": -1,
        "listener_bind_returned": -1,
        "listener_listen_returned": -1,
        "listener_errno": -1,
        "parsed_requests": -1,
        "rest_application_entered": -1,
        "rest_application_returned": -1,
        "status_data_completed": -1,
        "status_validator_result": "not_run",
        "status_successful": -1,
        "status_rejected": -1,
        "response_encoding_completed": -1,
        "response_body_length": -1,
        "expected_response_wire_length": -1,
        "target_send_attempts": -1,
        "target_successful_send_calls": -1,
        "target_bytes_written": -1,
        "target_send_would_blocks": -1,
        "peer_eof_events": -1,
        "target_zero_send_events": -1,
        "client_disconnect_observed": -1,
        "target_headers": -1,
        "target_wires": -1,
        "target_completions": -1,
        "target_failures": -1,
        "target_socket_closed": -1,
        "observer_accepted": -1,
        "observer_closed": -1,
        "observer_open_clients": -1,
        "observer_faulted": -1,
        "write_timeout": -1,
    }
    for key in _HEAP_KEYS:
        values[key] = -1

    if type(stage1_values) is tuple and len(stage1_values) == 4:
        client_seen, clients, action, association = stage1_values
        values["stage1_client_seen"] = _boolean(client_seen)
        values["stage1_ap_clients"] = _counter(clients)
        values["stage1_action"] = (
            action if action in ("none", "ap_checked", "other") else "other"
        )
        values["stage1_association_confirmed"] = _boolean(association)

    if type(server_snapshot) is dict:
        for target, source in (
            ("http_started", "started"),
            ("http_closed", "closed"),
            ("http_faulted", "faulted"),
        ):
            values[target] = _boolean(server_snapshot.get(source))
        for target, source in (
            ("http_clients", "client_count"),
            ("http_accept_actions", "accept_actions"),
            ("http_accepted", "accepted"),
            ("http_recv_actions", "recv_actions"),
            ("http_send_actions", "send_actions"),
            ("http_completed", "completed"),
            ("http_parse_errors", "parse_errors"),
            ("http_timeouts", "timeouts"),
            ("http_socket_errors", "socket_errors"),
            ("http_reentries", "reentries"),
        ):
            values[target] = _counter(server_snapshot.get(source))
        last_error = _http_last_error(server_snapshot.get("last_error"))
        values["http_last_error"] = last_error
        values["write_timeout"] = 1 if last_error == "write_timeout" else 0

    observer = None
    if socket_factory is not None:
        for target, source in (
            ("listener_factory_returned", "factory_returned"),
            ("listener_setblocking_returned", "setblocking_returned"),
            ("listener_bind_returned", "bind_returned"),
            ("listener_listen_returned", "listen_returned"),
            ("listener_errno", "listener_errno"),
        ):
            values[target] = _counter(getattr(socket_factory, source, None))
        observer = getattr(socket_factory, "observer", None)

    if observer is not None:
        for target, source in (
            ("target_send_attempts", "send_attempts"),
            ("target_successful_send_calls", "successful_send_calls"),
            ("target_bytes_written", "bytes_written"),
            ("target_send_would_blocks", "send_would_blocks"),
            ("peer_eof_events", "peer_eof_events"),
            ("target_zero_send_events", "target_zero_send_events"),
            ("response_body_length", "response_body_length"),
            ("expected_response_wire_length", "expected_response_wire_length"),
            ("target_headers", "target_headers"),
            ("target_wires", "target_wires"),
            ("target_completions", "target_completions"),
            ("target_failures", "target_failures"),
            ("observer_accepted", "accepted"),
            ("observer_closed", "closed"),
        ):
            values[target] = _counter(getattr(observer, source, None))
        values["observer_faulted"] = _boolean(
            getattr(observer, "faulted", None)
        )
        try:
            values["observer_open_clients"] = _counter(
                observer.open_clients()
            )
        except BaseException:
            pass
        values["target_socket_closed"] = _boolean(
            getattr(observer, "target_socket_closed", None)
        )
        peer_eof = values["peer_eof_events"]
        zero_send = values["target_zero_send_events"]
        if peer_eof >= 0 and zero_send >= 0:
            values["client_disconnect_observed"] = _boolean(
                bool(peer_eof or zero_send)
            )
        encoding = getattr(observer, "response_encoding_observed", None)
        values["response_encoding_completed"] = _boolean(encoding)

    if (
        values["response_encoding_completed"] != 1
        and values["http_last_error"] == "response_contract_failed"
    ):
        values["response_encoding_completed"] = 0

    if gateway is not None:
        values["parsed_requests"] = _counter(
            getattr(gateway, "routed_requests", None)
        )
        values["rest_application_entered"] = _counter(
            getattr(gateway, "rest_application_entered", None)
        )
        values["rest_application_returned"] = _counter(
            getattr(gateway, "rest_application_returned", None)
        )
        values["status_data_completed"] = _counter(
            getattr(gateway, "status_data_completed", None)
        )
        accepted = _counter(getattr(gateway, "validator_accepted", None))
        rejected = _counter(getattr(gateway, "validator_rejected", None))
        if accepted > 0:
            values["status_validator_result"] = "accepted"
        elif rejected > 0:
            values["status_validator_result"] = "rejected"
        values["status_successful"] = _counter(
            getattr(gateway, "successful_status_responses", None)
        )
        values["status_rejected"] = _counter(
            getattr(gateway, "rejected_requests", None)
        )

    if type(heap_values) is tuple and len(heap_values) == len(_HEAP_KEYS):
        for key, value in zip(_HEAP_KEYS, heap_values):
            values[key] = _counter(value)
    return values


def emit(values):
    print(FULL_REST_PHONE_FAILURE_STAGE_TOKEN)
    ordered = (
        "stage",
        "ap_client_associated",
        "association_after_bind",
        "response_completed",
        "cleanup_success",
        "stage1_client_seen",
        "stage1_ap_clients",
        "stage1_action",
        "stage1_association_confirmed",
        "http_started",
        "http_closed",
        "http_faulted",
        "http_clients",
        "http_accept_actions",
        "http_accepted",
        "http_recv_actions",
        "http_send_actions",
        "http_completed",
        "http_parse_errors",
        "http_timeouts",
        "http_socket_errors",
        "http_reentries",
        "http_last_error",
        "listener_factory_returned",
        "listener_setblocking_returned",
        "listener_bind_returned",
        "listener_listen_returned",
        "listener_errno",
        "parsed_requests",
        "rest_application_entered",
        "rest_application_returned",
        "status_data_completed",
        "status_validator_result",
        "status_successful",
        "status_rejected",
        "response_encoding_completed",
        "response_body_length",
        "expected_response_wire_length",
        "target_send_attempts",
        "target_successful_send_calls",
        "target_bytes_written",
        "target_send_would_blocks",
        "peer_eof_events",
        "target_zero_send_events",
        "client_disconnect_observed",
        "target_headers",
        "target_wires",
        "target_completions",
        "target_failures",
        "target_socket_closed",
        "observer_accepted",
        "observer_closed",
        "observer_open_clients",
        "observer_faulted",
        "write_timeout",
    ) + _HEAP_KEYS
    for key in ordered:
        print("{}={}".format(key, values.get(key, -1)))
    return None
