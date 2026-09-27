"""Persistent RSA key rings shared by every worker, protected by a process lock."""
import json
import os
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from filelock import FileLock


class KeyStoreUnavailable(RuntimeError):
    pass


class KeyRing:
    ACCESS_WARMUP = 300
    RETENTION = {"access": 930, "refresh": 7 * 86400 + 30}

    def __init__(self, path):
        self.path = Path(path)

    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return FileLock(str(self.path) + ".lock", timeout=10)

    def _read(self):
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("version") != 1 or set(state["active"]) != {"access", "refresh"}:
                raise ValueError("Invalid key ring")
            for purpose, kid in state['active'].items():
                record = state['keys'][kid]
                if record['purpose'] != purpose or not record['private'] or record['retired_at'] is not None:
                    raise ValueError('Invalid active key')
            return state
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise KeyStoreUnavailable("JWT key ring is unavailable") from exc

    def _write(self, state):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".keys-", delete=False) as out:
                temporary = Path(out.name)
                os.chmod(temporary, 0o600)
                json.dump(state, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _new(purpose, now):
        if purpose not in KeyRing.RETENTION:
            raise ValueError("purpose must be access or refresh")
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        kid = str(uuid4())
        public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
        public = {k: public[k] for k in ('kty', 'n', 'e')}
        public.update(kid=kid, use="sig", alg="RS256")
        return dict(kid=kid, purpose=purpose, created_at=now, activated_at=None, retired_at=None,
                    public=public, private=private.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii"))

    def initialize(self):
        with self._lock():
            if self.path.exists():
                self._read()
                return
            now = int(time.time())
            records = [self._new(p, now) for p in ("access", "refresh")]
            for record in records:
                record["activated_at"] = now
            self._write(dict(version=1, active={r["purpose"]: r["kid"] for r in records},
                             keys={r["kid"]: r for r in records}))

    def prepare(self, purpose):
        with self._lock():
            state = self._read()
            record = self._new(purpose, int(time.time()))
            state["keys"][record["kid"]] = record
            self._write(state)
            return record["kid"]

    def activate(self, kid):
        with self._lock():
            state = self._read()
            record = state["keys"].get(kid)
            if not record:
                raise ValueError("Unknown kid")
            if state["active"][record["purpose"]] == kid:
                return
            if record["activated_at"] is not None:
                raise ValueError("A retired key cannot be reactivated")
            now = int(time.time())
            if record["purpose"] == "access" and now < record["created_at"] + self.ACCESS_WARMUP:
                raise ValueError("Publish the access key for at least 300 seconds before activation")
            old = state["keys"][state["active"][record["purpose"]]]
            old["retired_at"] = now
            old.pop("private", None)
            record["activated_at"] = now
            state["active"][record["purpose"]] = kid
            self._write(state)

    def prune(self):
        with self._lock():
            state = self._read()
            now = int(time.time())
            expired = [kid for kid, r in state["keys"].items() if r["retired_at"] is not None
                       and now > r["retired_at"] + self.RETENTION[r["purpose"]]]
            for kid in expired:
                del state["keys"][kid]
            self._write(state)
            return expired

    def status(self):
        with self._lock():
            return [{k: r[k] for k in ("kid", "purpose", "created_at", "activated_at", "retired_at")}
                    for r in self._read()["keys"].values()]

    def sign(self, payload, purpose):
        # Activation shares this lock: old-key signing cannot race retirement.
        with self._lock():
            state = self._read()
            record = state["keys"][state["active"][purpose]]
            return jwt.encode(payload, record["private"], algorithm="RS256",
                              headers={"kid": record["kid"], "typ": "JWT"})

    def verification_key(self, kid, purpose):
        with self._lock():
            record = self._read()["keys"].get(kid)
            if not record or record["purpose"] != purpose:
                raise jwt.InvalidTokenError("Unknown signing key")
            return jwt.PyJWK.from_dict(record["public"]).key

    def jwks(self):
        with self._lock():
            return {"keys": [r["public"] for r in self._read()["keys"].values() if r["purpose"] == "access"]}
