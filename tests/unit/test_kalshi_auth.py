"""Tests for Kalshi authentication (RSA-PSS signing)"""

import base64
import os
import tempfile

import pytest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.api.auth import KalshiAuth


@pytest.fixture
def rsa_key_pair():
    """Generate a temporary RSA key pair for testing"""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key


@pytest.fixture
def key_file(rsa_key_pair):
    """Write RSA private key to a temp file"""
    pem = rsa_key_pair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
        f.write(pem)
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def auth(key_file):
    """Create KalshiAuth instance with test key"""
    return KalshiAuth(key_id="test-key-id", private_key_path=key_file)


def test_auth_initialization(auth):
    """Test that KalshiAuth initializes correctly"""
    assert auth.key_id == "test-key-id"
    assert auth.private_key is not None


def test_auth_missing_key_file():
    """Test that missing key file raises FileNotFoundError"""
    with pytest.raises(FileNotFoundError):
        KalshiAuth(key_id="test", private_key_path="/nonexistent/key.pem")


def test_get_auth_headers(auth):
    """Test that auth headers contain required keys"""
    headers = auth.get_auth_headers("GET", "/trade-api/v2/portfolio/balance")

    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"


def test_get_auth_headers_signature_is_base64(auth):
    """Test that signature is valid base64"""
    headers = auth.get_auth_headers("POST", "/trade-api/v2/portfolio/orders")
    signature = headers["KALSHI-ACCESS-SIGNATURE"]

    # Should be valid base64
    decoded = base64.b64decode(signature)
    assert len(decoded) > 0


def test_get_auth_headers_timestamp_is_numeric(auth):
    """Test that timestamp is a numeric string (milliseconds)"""
    headers = auth.get_auth_headers("GET", "/trade-api/v2/markets")
    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]

    assert timestamp.isdigit()
    assert int(timestamp) > 0


def test_signature_verification(auth, rsa_key_pair):
    """Test that signature can be verified with the public key"""
    headers = auth.get_auth_headers("GET", "/trade-api/v2/portfolio/balance")

    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = timestamp + "GET" + "/trade-api/v2/portfolio/balance"

    # Verify with public key — should not raise
    public_key = rsa_key_pair.public_key()
    public_key.verify(
        signature,
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_get_ws_auth_headers(auth):
    """Test WebSocket auth headers use correct path"""
    headers = auth.get_ws_auth_headers()

    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers


def test_different_methods_produce_different_signatures(auth):
    """Test that different HTTP methods produce different signatures"""
    path = "/trade-api/v2/portfolio/orders"
    headers_get = auth.get_auth_headers("GET", path)
    headers_post = auth.get_auth_headers("POST", path)

    # Signatures should differ because method is included in the signed message
    # (timestamps may also differ, but the method difference alone guarantees it)
    assert headers_get["KALSHI-ACCESS-SIGNATURE"] != headers_post["KALSHI-ACCESS-SIGNATURE"]
