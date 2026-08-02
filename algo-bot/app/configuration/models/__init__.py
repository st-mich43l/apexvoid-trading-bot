"""Complete frozen configuration schema; inactive in application startup."""

from app.configuration.models.root import ApexVoidConfig


__all__ = ("ApexVoidConfig",)
"""Complete and Python-scoped frozen canonical configuration schemas."""

from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.models.root import ApexVoidConfig

__all__ = ("ApexVoidConfig", "PythonRuntimeConfig")
