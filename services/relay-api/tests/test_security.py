import pytest

from app.security import FernetFieldCipher, redact_for_log


def test_redact_for_log_removes_sensitive_values():
    redacted = redact_for_log(
        {"phone_number": "+919999999999", "booking_reference": "ABC123"}
    )

    assert redacted == {"phone_number": "[REDACTED]", "booking_reference": "[REDACTED]"}


def test_redact_for_log_removes_token_and_secret_suffixes():
    redacted = redact_for_log(
        {"authorization": "Bearer token", "nested": {"api_key": "secret"}, "safe": "ok"}
    )

    assert redacted == {"authorization": "[REDACTED]", "nested": {"api_key": "[REDACTED]"}, "safe": "ok"}


def test_field_cipher_encrypts_and_decrypts_ciphertext():
    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())

    encrypted = cipher.encrypt("private value")

    assert encrypted.startswith("enc:v1:")
    assert encrypted != "private value"
    assert cipher.decrypt(encrypted) == "private value"


def test_field_cipher_rejects_tampered_versioned_ciphertext():
    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())

    with pytest.raises(ValueError):
        cipher.decrypt("enc:v1:not-authenticated")
