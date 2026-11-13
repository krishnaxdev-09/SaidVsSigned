import time
import uuid

import pytest

from app.core.config import StartupCheckError, check_features
from app.core.errors import AppError
from app.core.idempotency import canonical_hash
from app.core.ids import uuid7
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    token_hash,
    verify_password,
)

SECRET = "s" * 48


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery") and not verify_password(h, "wrong")


def test_access_token_valid_and_expired():
    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(uid, SECRET, 900), SECRET) == uid
    expired = create_access_token(uid, SECRET, 10, now=time.time() - 100)
    with pytest.raises(AppError) as ei:
        decode_access_token(expired, SECRET)
    assert ei.value.code == "unauthorized"
    with pytest.raises(AppError):
        decode_access_token(create_access_token(uid, "other" * 10, 900), SECRET)


def test_token_hash_is_sha256_hex():
    assert len(token_hash("abc")) == 64


def test_uuid7_version_variant_and_order():
    ids = [uuid7() for _ in range(200)]
    assert all(i.version == 7 and i.variant == uuid.RFC_4122 for i in ids)
    time.sleep(0.002)
    assert uuid7() > ids[0]


def test_idempotency_hash_canonicalises_json():
    a = canonical_hash("post", "/x", b'{"a":1,"b":2}')
    assert a == canonical_hash("POST", "/x", b'{ "b": 2, "a": 1 }')
    assert a != canonical_hash("POST", "/y", b'{"a":1,"b":2}')
    assert a != canonical_hash("POST", "/x", b'{"a":1,"b":3}')


SAFE = {"finance": {"preview_internal": False, "mvp_formulas_user_visible": False, "gated_formulas_enabled": False,
                    "hc5_approval": {"approved_by": None}}, "rules": {"user_visible": False}}


def test_shipped_features_file_passes():
    from app.core.config import BACKEND_DIR, load_features
    check_features(load_features(BACKEND_DIR / "config" / "features.yaml"), real_users=False)
    check_features(load_features(BACKEND_DIR / "config" / "features.yaml"), real_users=True)


@pytest.mark.parametrize("patch,real_users", [
    ({"finance": {**SAFE["finance"], "mvp_formulas_user_visible": True}}, False),  # no HC-5 record
    ({"finance": {**SAFE["finance"], "gated_formulas_enabled": True}}, False),
    ({"finance": {**SAFE["finance"], "preview_internal": True}}, True),
    ({"rules": {"user_visible": True}}, False),
])
def test_unsafe_flags_refused(patch, real_users):
    with pytest.raises(StartupCheckError):
        check_features({**SAFE, **patch}, real_users=real_users)
