"""Kalshi API authentication using RSA-PSS signing"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from src.utils.logging import get_logger


class KalshiAuth:
    """Handle Kalshi API authentication with RSA-PSS request signing"""

    def __init__(
        self,
        key_id: str,
        private_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
    ):
        """
        Initialize authentication.

        Provide either ``private_key`` (inline PEM string) **or**
        ``private_key_path`` (path to a PEM file).  If both are given,
        ``private_key`` takes precedence.

        Args:
            key_id: Kalshi API key ID
            private_key: RSA private key as a PEM string
            private_key_path: Path to RSA private key PEM file
        """
        self.logger = get_logger(__name__)
        self.key_id = key_id

        # Load RSA private key from inline string or file
        if private_key:
            pem_bytes = private_key.encode("utf-8")
        elif private_key_path:
            key_path = Path(private_key_path)
            if not key_path.exists():
                raise FileNotFoundError(f"Private key not found: {private_key_path}")
            with open(key_path, "rb") as f:
                pem_bytes = f.read()
        else:
            raise ValueError("Must provide either private_key or private_key_path")

        self.private_key = serialization.load_pem_private_key(pem_bytes, password=None)

        self.logger.info("Initialized Kalshi auth", key_id=self.key_id)

    def get_auth_headers(self, method: str, path: str) -> dict[str, str]:
        """
        Generate authentication headers for a Kalshi API request.

        Signs ``timestamp_ms + METHOD + path`` with RSA-PSS (SHA-256).

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path: API path (e.g., "/trade-api/v2/portfolio/balance")

        Returns:
            Dict with KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE, KALSHI-ACCESS-TIMESTAMP
        """
        timestamp_ms = str(int(time.time() * 1000))
        message = timestamp_ms + method.upper() + path

        signature = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def get_ws_auth_headers(self) -> dict[str, str]:
        """
        Generate authentication headers for Kalshi WebSocket handshake.

        Returns:
            Auth headers for WS connection
        """
        return self.get_auth_headers("GET", "/trade-api/ws/v2")
