"""设备白名单 + 密码门禁（按 device_id，不按 IP）。"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, Response

COOKIE_NAME = "patchouli_console"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
DEVICE_HEADER = "X-Console-Device"
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def normalize_device_id(raw: str | None) -> str:
    did = str(raw or "").strip()
    if not _DEVICE_RE.fullmatch(did):
        raise ValueError("无效的 device_id")
    return did


class DeviceGate:
    def __init__(self, *, data_dir: Path, password: str, session_secret: str) -> None:
        self.path = Path(data_dir) / "allowed_devices.json"
        self.password = password
        self.session_secret = session_secret.encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self._data: dict[str, Any] = {"devices": {}}
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {"devices": {}}
        if not isinstance(self._data.get("devices"), dict):
            self._data["devices"] = {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    @staticmethod
    def device_id_from_request(request: Request) -> str | None:
        header = request.headers.get(DEVICE_HEADER) or ""
        if header.strip():
            try:
                return normalize_device_id(header)
            except ValueError:
                return None
        raw = request.cookies.get(COOKIE_NAME, "")
        if "|" not in raw:
            return None
        cookie_did, _sig = raw.split("|", 1)
        try:
            return normalize_device_id(cookie_did)
        except ValueError:
            return None

    def is_allowed_device(self, device_id: str) -> bool:
        return device_id in self._data.get("devices", {})

    def remember_device(self, device_id: str, *, note: str = "") -> None:
        devices = self._data.setdefault("devices", {})
        devices[device_id] = {
            "first_seen": devices.get(device_id, {}).get("first_seen") or time.time(),
            "last_seen": time.time(),
            "note": note or devices.get(device_id, {}).get("note") or "",
        }
        self._save()

    def touch_device(self, device_id: str) -> None:
        if device_id in self._data.get("devices", {}):
            self.remember_device(device_id)

    def verify_password(self, password: str) -> bool:
        return secrets.compare_digest(password, self.password)

    def _sign(self, device_id: str) -> str:
        return hmac.new(
            self.session_secret,
            device_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def issue_cookie(self, response: Response, device_id: str) -> None:
        token = f"{device_id}|{self._sign(device_id)}"
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def cookie_ok(self, request: Request, device_id: str) -> bool:
        raw = request.cookies.get(COOKIE_NAME, "")
        if "|" not in raw:
            return False
        cookie_did, sig = raw.split("|", 1)
        if cookie_did != device_id:
            return False
        return hmac.compare_digest(sig, self._sign(device_id))

    def is_authenticated(self, request: Request) -> bool:
        did = self.device_id_from_request(request)
        if not did:
            return False
        if self.is_allowed_device(did):
            return True
        return self.cookie_ok(request, did)

    def require(self, request: Request) -> str:
        did = self.device_id_from_request(request)
        if not did:
            raise HTTPException(status_code=401, detail="auth_required")
        if self.is_allowed_device(did):
            self.touch_device(did)
            return did
        if self.cookie_ok(request, did):
            self.remember_device(did, note="cookie")
            return did
        raise HTTPException(status_code=401, detail="auth_required")


# 兼容旧冒烟 / import 名
IpGate = DeviceGate
