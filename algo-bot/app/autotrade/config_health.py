"""Redis keys shared by execution event and readiness notifications."""

CONFIG_HEALTH_KEY = "auto_trade:config_health"
EXECUTOR_READINESS_KEY = "auto_trade:executor_readiness"

__all__ = ["CONFIG_HEALTH_KEY", "EXECUTOR_READINESS_KEY"]
