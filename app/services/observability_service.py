from helpers.utils import get_logger
from app.tasks.telemetry import send_telemetry as _post_telemetry

telemetry_logger = get_logger("telemetry")


class ObservabilityService:
    async def log_telemetry(self, data: dict):
        telemetry_logger.info(data)

    async def send_telemetry(self, data: dict):
        try:
            result = await _post_telemetry(data)
            telemetry_logger.info(f"Telemetry sent: {result}")
        except Exception as e:
            telemetry_logger.error(f"Failed to send telemetry: {e}")



