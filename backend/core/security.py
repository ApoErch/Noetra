from functools import lru_cache

from cryptography.fernet import Fernet

from core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    """Build (once, cached) the Fernet cipher from the configured key. Fernet = symmetric authenticated encryption."""
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt_token(plaintext: str) -> str:
    """Encrypt a GitHub access token so it can be stored at rest in the DB."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Reverse `encrypt_token`: recover the plaintext access token for use in API calls."""
    return _fernet().decrypt(ciphertext.encode()).decode()
