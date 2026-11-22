"""
Encryption for secrets we must store (connector OAuth tokens, PKCE verifiers).

Uses Fernet (AES-128-CBC + HMAC-SHA256, authenticated) from the `cryptography`
package. Keys come from the CONNECTOR_ENCRYPTION_KEYS environment variable,
never from the database, so a database leak alone does not reveal tokens.

Key rotation: put the new key FIRST in CONNECTOR_ENCRYPTION_KEYS and keep the
old ones after it. New data is encrypted with the first key; old data still
decrypts. `needs_rotation()` tells callers when to re-encrypt.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class DecryptionError(Exception):
    """The ciphertext was tampered with, or no configured key can open it."""


class FernetTokenCipher:

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("At least one encryption key is required.")
        self._current = Fernet(keys[0].encode())
        self._all = MultiFernet([Fernet(key.encode()) for key in keys])

    def encrypt(self, plaintext: str) -> bytes:
        return self._current.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._all.decrypt(ciphertext).decode()
        except InvalidToken as error:
            # Never include the ciphertext in the message.
            raise DecryptionError("Stored secret could not be decrypted.") from error

    def needs_rotation(self, ciphertext: bytes) -> bool:
        """True when the data was encrypted with an older key."""
        try:
            self._current.decrypt(ciphertext)
            return False
        except InvalidToken:
            return True
