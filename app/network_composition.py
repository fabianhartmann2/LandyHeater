"""Credential-gated construction of the Phase-7 NetworkManager.

This module is hardware free.  It binds one already-open injected WLAN port to
one exact, readback-confirmed ConfigManager generation.  Importing or building
the object performs no WLAN operation; callers still invoke ``start()`` and
``step()`` explicitly.
"""

from app.network_manager import NetworkManager
from services.configuration_errors import ConfigurationStateError


class ConfiguredNetworkRuntime:
    __slots__ = ("_manager", "_configuration_generation")

    def __init__(self, manager, configuration_generation):
        self._manager = manager
        self._configuration_generation = configuration_generation

    @property
    def manager(self):
        return self._manager

    @property
    def configuration_generation(self):
        return self._configuration_generation

    def restart_required(self, config_manager):
        generation = getattr(config_manager, "generation", None)
        network_allowed = getattr(
            config_manager, "network_start_allowed", None
        )
        if type(generation) is not int or type(network_allowed) is not bool:
            raise ConfigurationStateError(
                "network configuration gate is unavailable"
            )
        return (
            generation != self._configuration_generation
            or not network_allowed
        )

    def snapshot(self):
        return {
            "configuration_generation": self._configuration_generation,
            "restart_required": False,
            "network": self._manager.snapshot(),
        }


def build_configured_network(
    config_manager,
    port,
    ticks_diff=None,
    ticks_add=None,
):
    """Build a cold NetworkManager from one trusted config generation."""

    if (ticks_diff is None) != (ticks_add is None):
        raise ValueError("ticks_diff and ticks_add must be provided together")
    getter = getattr(config_manager, "network_configuration_for_runtime", None)
    if not callable(getter):
        raise ValueError(
            "config_manager must provide network_configuration_for_runtime()"
        )
    generation = getattr(config_manager, "generation", None)
    allowed = getattr(config_manager, "network_start_allowed", None)
    if type(generation) is not int or type(allowed) is not bool:
        raise ConfigurationStateError("network configuration gate is invalid")
    if not allowed:
        raise ConfigurationStateError(
            "network configuration is not trusted or provisioned"
        )
    snapshot = getter()
    if (
        type(snapshot) is not dict
        or frozenset(snapshot) != frozenset(("generation", "network"))
        or snapshot["generation"] != generation
    ):
        raise ConfigurationStateError(
            "network configuration changed while staging"
        )
    if ticks_diff is None:
        manager = NetworkManager(port, snapshot["network"])
    else:
        manager = NetworkManager(
            port,
            snapshot["network"],
            ticks_diff=ticks_diff,
            ticks_add=ticks_add,
        )
    if (
        getattr(config_manager, "generation", None) != generation
        or getattr(config_manager, "network_start_allowed", None) is not True
    ):
        raise ConfigurationStateError(
            "network configuration changed during construction"
        )
    return ConfiguredNetworkRuntime(manager, generation)
