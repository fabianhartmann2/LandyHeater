"""Lazy, secret-free failure diagnostics for the Phase-8 full REST smoke."""

import gc as _gc


FULL_REST_PHONE_FAILURE_STAGE_TOKEN = (
    "PHASE8_FULL_REST_PHONE_FAILURE_STAGE_V1"
)
_FAILURE_STAGES = (
    "stage1_preflight", "stage1_ap_startup", "stage1_http_bind",
    "stage1_observe_deadline", "stage1_observe_network_step",
    "stage1_observe_network_truth", "stage1_observe_http_step",
    "stage1_observe_http_transport", "stage1_post_response_heap",
    "stage1_cleanup_http", "stage1_post_cleanup_heap",
    "stage1_confirm_association",
    "preflight_product", "preflight_storage", "preflight_wifi",
    "ap_startup", "observe_association", "rest_composition", "http_bind",
    "confirm_association", "observe_network", "observe_http_step",
    "observe_http_transport", "observe_security", "observe_route_binding",
    "observe_post_response", "observe_timeout", "cleanup_http",
    "cleanup_rest", "cleanup_wifi", "cleanup_storage", "cleanup_state",
    "postflight_safe_state", "internal",
)
_COUNTER_LIMIT = 1000000
_HTTP_LAST_ERRORS = ("accept_failed", "accept_contract_failed")
_HEAP_NAMES = (
    "memory_before", "memory_after_product_imports",
    "memory_after_configuration_adoption", "memory_after_wifi_factory",
    "memory_after_ap_ready", "memory_after_ip_bind",
    "memory_after_ip_response", "memory_after_ip_cleanup",
    "memory_before_http_start", "memory_after_http_bind",
    "memory_after_response", "memory_after_cleanup",
    "memory_after_failure_cleanup",
)


def memory_free_no_collect():
    try:
        reader = getattr(_gc, "mem_free", None)
        if not callable(reader):
            return -1
        value = reader()
        if type(value) is int and 0 <= value <= _COUNTER_LIMIT:
            return value
    except BaseException:
        pass
    return -1


def _counter(value):
    if type(value) is int and 0 <= value <= _COUNTER_LIMIT:
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
    if type(value) is str and value in _HTTP_LAST_ERRORS:
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
):
    values = {
        "stage": stage if stage in _FAILURE_STAGES else "internal",
        "http_faulted": -1,
        "http_clients": -1,
        "http_accepted": -1,
        "http_completed": -1,
        "http_parse_errors": -1,
        "http_timeouts": -1,
        "http_socket_errors": -1,
        "http_last_error": "none",
        "listener_factory_returned": -1,
        "listener_setblocking_returned": -1,
        "listener_bind_returned": -1,
        "listener_listen_returned": -1,
        "listener_errno": -1,
        "observer_faulted": -1,
        "observer_open_clients": -1,
        "target_headers": -1,
        "target_wires": -1,
        "target_completions": -1,
        "target_failures": -1,
        "status_valid": -1,
        "status_success": -1,
        "status_marked": -1,
        "status_rejected": -1,
        "status_responses": -1,
        "candidate_active": _boolean(
            gateway is not None
            and gateway.successful_status_responses > 0
        ),
        "ap_client_confirmed": _boolean(ap_client_confirmed),
        "post_bind_peer_confirmed": _boolean(post_bind_peer_confirmed),
        "response_completed": _boolean(response_completed),
        "stage1_client_seen": -1,
        "stage1_ap_clients": -1,
        "stage1_action": "none",
        "stage1_http_started": -1,
        "stage1_http_closed": -1,
        "stage1_http_faulted": -1,
        "stage1_http_clients": -1,
        "stage1_http_accepted": -1,
        "stage1_http_completed": -1,
        "stage1_http_parse_errors": -1,
        "stage1_http_timeouts": -1,
        "stage1_http_socket_errors": -1,
        "stage1_http_last_error": "none",
        "stage1_http_reentries": -1,
        "stage1_accept_errno": -1,
        "stage1_valid": -1,
        "stage1_rejected": -1,
        "stage1_responses": -1,
        "stage1_cleanup_confirmed": -1,
    }
    for name in _HEAP_NAMES:
        values[name] = -1
    try:
        if type(server_snapshot) is dict:
            values["http_faulted"] = _boolean(server_snapshot.get("faulted"))
            values["http_last_error"] = _http_last_error(
                server_snapshot.get("last_error")
            )
            for output_name, snapshot_name in (
                ("http_clients", "client_count"),
                ("http_accepted", "accepted"),
                ("http_completed", "completed"),
                ("http_parse_errors", "parse_errors"),
                ("http_timeouts", "timeouts"),
                ("http_socket_errors", "socket_errors"),
            ):
                values[output_name] = _counter(
                    server_snapshot.get(snapshot_name)
                )
        if socket_factory is not None:
            observer = socket_factory.observer
            for output_name, attribute_name in (
                ("listener_factory_returned", "factory_returned"),
                ("listener_setblocking_returned", "setblocking_returned"),
                ("listener_bind_returned", "bind_returned"),
                ("listener_listen_returned", "listen_returned"),
                ("listener_errno", "listener_errno"),
            ):
                values[output_name] = _counter(
                    getattr(socket_factory, attribute_name, None)
                )
            if observer is not None:
                values["observer_faulted"] = _boolean(observer.faulted)
                for output_name, attribute_name in (
                    ("observer_open_clients", "open_clients"),
                    ("target_headers", "target_headers"),
                    ("target_wires", "target_wires"),
                    ("target_completions", "target_completions"),
                    ("target_failures", "target_failures"),
                ):
                    value = (
                        observer.open_clients()
                        if attribute_name == "open_clients"
                        else getattr(observer, attribute_name, None)
                    )
                    values[output_name] = _counter(value)
        if gateway is not None:
            values["status_valid"] = _counter(gateway.valid_status_requests)
            values["status_success"] = _counter(
                gateway.successful_status_responses
            )
            values["status_marked"] = _counter(gateway.marked_status_responses)
            values["status_rejected"] = _counter(gateway.rejected_requests)
            values["status_responses"] = _counter(gateway.responses_returned)
        if type(heap_values) is tuple and len(heap_values) == len(_HEAP_NAMES):
            for index, name in enumerate(_HEAP_NAMES):
                values[name] = _counter(heap_values[index])
        if type(stage1_values) is tuple and len(stage1_values) == 9:
            (
                stage1_snapshot,
                stage1_client_seen,
                stage1_ap_clients,
                stage1_action,
                stage1_accept_errno,
                stage1_valid,
                stage1_rejected,
                stage1_responses,
                stage1_cleanup_confirmed,
            ) = stage1_values
            values["stage1_client_seen"] = _boolean(stage1_client_seen)
            values["stage1_ap_clients"] = _counter(stage1_ap_clients)
            values["stage1_action"] = (
                stage1_action
                if stage1_action in ("none", "ap_checked", "other")
                else "other"
            )
            values["stage1_accept_errno"] = _counter(stage1_accept_errno)
            values["stage1_valid"] = _counter(stage1_valid)
            values["stage1_rejected"] = _counter(stage1_rejected)
            values["stage1_responses"] = _counter(stage1_responses)
            values["stage1_cleanup_confirmed"] = _boolean(
                stage1_cleanup_confirmed
            )
            if type(stage1_snapshot) is tuple and len(stage1_snapshot) == 11:
                (
                    started,
                    closed,
                    faulted,
                    clients,
                    accepted,
                    completed,
                    parse_errors,
                    timeouts,
                    socket_errors,
                    last_error,
                    reentries,
                ) = stage1_snapshot
                values["stage1_http_started"] = _boolean(started)
                values["stage1_http_closed"] = _boolean(closed)
                values["stage1_http_faulted"] = _boolean(faulted)
                values["stage1_http_clients"] = _counter(clients)
                values["stage1_http_accepted"] = _counter(accepted)
                values["stage1_http_completed"] = _counter(completed)
                values["stage1_http_parse_errors"] = _counter(parse_errors)
                values["stage1_http_timeouts"] = _counter(timeouts)
                values["stage1_http_socket_errors"] = _counter(socket_errors)
                values["stage1_http_last_error"] = _http_last_error(last_error)
                values["stage1_http_reentries"] = _counter(reentries)
            elif type(stage1_snapshot) is dict:
                values["stage1_http_started"] = _boolean(
                    stage1_snapshot.get("started")
                )
                values["stage1_http_closed"] = _boolean(
                    stage1_snapshot.get("closed")
                )
                values["stage1_http_faulted"] = _boolean(
                    stage1_snapshot.get("faulted")
                )
                values["stage1_http_last_error"] = _http_last_error(
                    stage1_snapshot.get("last_error")
                )
                for output_name, snapshot_name in (
                    ("stage1_http_clients", "client_count"),
                    ("stage1_http_accepted", "accepted"),
                    ("stage1_http_completed", "completed"),
                    ("stage1_http_parse_errors", "parse_errors"),
                    ("stage1_http_timeouts", "timeouts"),
                    ("stage1_http_socket_errors", "socket_errors"),
                    ("stage1_http_reentries", "reentries"),
                ):
                    values[output_name] = _counter(
                        stage1_snapshot.get(snapshot_name)
                    )
    except BaseException:
        pass
    return values


def emit(values):
    try:
        print(FULL_REST_PHONE_FAILURE_STAGE_TOKEN)
        if type(values) is not dict:
            return None
        for name in (
            "stage", "http_faulted", "http_clients", "http_accepted",
            "http_completed", "http_parse_errors", "http_timeouts",
            "http_socket_errors", "http_last_error",
            "listener_factory_returned",
            "listener_setblocking_returned", "listener_bind_returned",
            "listener_listen_returned", "listener_errno", "observer_faulted",
            "observer_open_clients", "target_headers", "target_wires",
            "target_completions", "target_failures", "status_valid",
            "status_success", "status_marked", "status_rejected",
            "status_responses", "candidate_active", "ap_client_confirmed",
            "post_bind_peer_confirmed", "response_completed",
            "stage1_client_seen", "stage1_ap_clients", "stage1_action",
            "stage1_http_started", "stage1_http_closed",
            "stage1_http_faulted", "stage1_http_clients",
            "stage1_http_accepted", "stage1_http_completed",
            "stage1_http_parse_errors", "stage1_http_timeouts",
            "stage1_http_socket_errors", "stage1_http_last_error",
            "stage1_http_reentries", "stage1_accept_errno",
            "stage1_valid", "stage1_rejected", "stage1_responses",
            "stage1_cleanup_confirmed",
        ) + _HEAP_NAMES:
            print("{}={}".format(name, values[name]))
    except BaseException:
        pass
    return None
