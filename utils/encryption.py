import os
import base64
import json
from typing import Optional, Dict, Any
import logging
import hashlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger('backend.encryption')


TENANT_ENV = "AZURE_TENANT_ID"
CLIENT_ENV = "AZURE_CLIENT_ID"
SECRET_ENV = "AZURE_CLIENT_SECRET"
VAULT_URL_ENV = "KEYVAULT_URL"
SECRET_NAME_ENV = "SECRET_NAME"

_cached_key: bytes = None
_key_loaded = False


def _load_key_from_keyvault() -> bytes:
    """Load AES256 key from Azure Key Vault using credentials from .env"""

    tenant_id = os.getenv(TENANT_ENV)
    client_id = os.getenv(CLIENT_ENV)
    client_secret = os.getenv(SECRET_ENV)
    vault_url = os.getenv(VAULT_URL_ENV)
    secret_name = os.getenv(SECRET_NAME_ENV)

    missing = [
        v for v, name in [
            (tenant_id, TENANT_ENV),
            (client_id, CLIENT_ENV),
            (client_secret, SECRET_ENV),
            (vault_url, VAULT_URL_ENV),
            (secret_name, SECRET_NAME_ENV),
        ] if v is None
    ]

    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    logger.info(f"[KEYVAULT] Connecting to: {vault_url}")
    logger.info(f"[KEYVAULT] Secret name: {secret_name}")

    try:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

        client = SecretClient(
            vault_url=vault_url,
            credential=credential
        )

        logger.info(f"[KEYVAULT] Fetching secret '{secret_name}'...")
        secret_bundle = client.get_secret(secret_name)
        key_value = secret_bundle.value

        logger.info("[KEYVAULT] Secret retrieved successfully.")

    except Exception as e:
        logger.error(f"[KEYVAULT] Failed to retrieve secret: {e}")
        raise

    
    try:
        key = base64.b64decode(key_value)
        logger.info("[KEYVAULT] Key was base64-encoded, decoded successfully.")
    except Exception:
        logger.info("[KEYVAULT] Key is not base64, using raw value.")
        key = key_value.encode('utf-8')

    
    if len(key) != 32:
        logger.info(f"[KEYVAULT] Key is {len(key)} bytes, deriving 32-byte key using SHA-256.")
        key = hashlib.sha256(key).digest()

    logger.info(f"[KEYVAULT] Final key is {len(key)} bytes.")
    return key

def _get_key() -> bytes:
    """Return cached AES key (loaded once)."""
    global _cached_key, _key_loaded

    if _key_loaded:
        return _cached_key

    logger.info("[CRYPTO] Loading AES key from Azure Key Vault...")
    _cached_key = _load_key_from_keyvault()
    _key_loaded = True
    logger.info("[CRYPTO] AES key loaded and cached.")
    return _cached_key


def encrypt_bytes(data: bytes) -> str:
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, data, None)

    digest = hashlib.sha256(data).hexdigest()
    logger.info(f"Encrypted {len(data)} bytes sha256={digest}")

    return base64.b64encode(nonce + ct).decode()


def decrypt_bytes(b64: str) -> bytes:
    key = _get_key()
    raw = base64.b64decode(b64)
    nonce = raw[:12]
    ct = raw[12:]

    aesgcm = AESGCM(key)
    data = aesgcm.decrypt(nonce, ct, None)

    digest = hashlib.sha256(data).hexdigest()
    logger.info(f"Decrypted {len(data)} bytes sha256={digest}")

    return data


def encrypt_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return encrypt_bytes(text.encode())


def decrypt_text(b64: Optional[str]) -> Optional[str]:
    if b64 is None:
        return None
    return decrypt_bytes(b64).decode()


def encrypt_json(obj: Optional[Dict[str, Any]]) -> Optional[str]:
    if obj is None:
        return None
    return encrypt_text(json.dumps(obj))


def decrypt_json(b64: Optional[str]) -> Optional[Dict[str, Any]]:
    if b64 is None:
        return None
    try:
        return json.loads(decrypt_text(b64))
    except Exception:
        return None
