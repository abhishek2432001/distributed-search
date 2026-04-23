import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class ServiceUnavailableError(Exception):
    pass


class TenantNotFoundError(Exception):
    pass


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        data = response.data
        if isinstance(data, dict) and "detail" not in data and "error" not in data:
            response.data = {"error": "Validation failed.", "code": "VALIDATION_ERROR", "detail": data}
        elif isinstance(data, dict) and "detail" in data:
            response.data = {
                "error": str(data["detail"]),
                "code": getattr(data["detail"], "code", "API_ERROR").upper(),
            }
        return response

    if isinstance(exc, ServiceUnavailableError):
        logger.error("Service unavailable: %s", exc)
        return Response({"error": str(exc), "code": "SERVICE_UNAVAILABLE"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    logger.exception("Unhandled exception: %s", exc)
    return Response({"error": "An internal server error occurred.", "code": "INTERNAL_ERROR"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
