"""Lightweight shared configuration error types.

Keeping these classes separate lets the REST router classify ConfigManager
failures without importing the complete schema, scheduler, time and sensor
validation graph into the resident HTTP path.  ``services.config_manager``
re-exports the same class objects for backward-compatible imports.
"""


class ConfigurationValidationError(ValueError):
    pass


class ConfigurationConflictError(RuntimeError):
    pass


class ConfigurationStateError(RuntimeError):
    pass
