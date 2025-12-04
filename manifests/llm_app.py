"""
Ray Serve LLM application with built-in OpenAI API key authentication.
"""

import os
import logging
from typing import Set, Optional, Dict, Any
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from ray import serve

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class APIKeyValidator:
    """Validates API keys against a configured set."""

    def __init__(self):
        # Load API keys from file (mounted from Kubernetes secret)
        keys_file = os.environ.get("OPENAI_API_KEYS_FILE", "/etc/secrets/api-keys")
        try:
            with open(keys_file, 'r') as f:
                content = f.read()
                # Filter out empty lines and comments
                self.valid_keys = set(
                    k.strip() for k in content.split('\n') 
                    if k.strip() and not k.strip().startswith('#')
                )
            logger.info(f"Loaded {len(self.valid_keys)} API keys from {keys_file}")
        except FileNotFoundError:
            logger.error(f"API keys file not found at {keys_file}")
            self.valid_keys = set()

    def validate(self, auth_header: str) -> tuple[bool, Optional[str]]:
        """
        Validate authorization header.

        Returns:
            (is_valid, error_message)
        """
        if not auth_header:
            return False, "Missing Authorization header"

        if not auth_header.startswith("Bearer "):
            return False, "Invalid Authorization header format. Use: Bearer <token>"

        token = auth_header[7:]  # Remove "Bearer " prefix

        if token not in self.valid_keys:
            logger.warning(f"Invalid API key attempt: {token[:10]}...")
            return False, "Invalid API key"

        return True, None


@serve.deployment
class AuthenticatedLLMApp:
    """Ray Serve LLM application with API key authentication."""

    def __init__(self, llm_configs: list[Dict[str, Any]]):
        from ray.serve.llm import build_openai_app

        # Initialize API key validator
        self.validator = APIKeyValidator()

        # Build the underlying OpenAI-compatible LLM app
        logger.info("Building OpenAI-compatible LLM application...")
        self.llm_app = build_openai_app(llm_configs=llm_configs)
        self.app = self.llm_app.app

        # Add authentication middleware
        @self.app.middleware("http")
        async def authenticate_middleware(request: Request, call_next):
            # Skip auth for health check endpoints only
            if request.url.path in ["/health", "/healthz"]:
                return await call_next(request)

            # Validate API key
            auth_header = request.headers.get("Authorization", "")
            is_valid, error_msg = self.validator.validate(auth_header)

            if not is_valid:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "error": {
                            "message": error_msg,
                            "type": "invalid_request_error",
                            "code": "invalid_api_key"
                        }
                    }
                )

            # Authentication successful, proceed with request
            response = await call_next(request)
            return response

        logger.info("Authenticated LLM application ready")

    async def __call__(self, request: Request) -> Response:
        return await self.app(request)


def build_app(llm_configs: list[Dict[str, Any]]):
    """Build the authenticated LLM application."""
    return AuthenticatedLLMApp.bind(llm_configs=llm_configs)
