"""Session auth + WS one-time ticket management."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

SESSION_COOKIE_NAME = "agm_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "43200"))  # 12h
WS_TICKET_TTL_SECONDS = int(os.getenv("WS_TICKET_TTL_SECONDS", "30"))


def _now() -> int:
    return int(time.time())


def _secret_key() -> str:
    key = os.getenv("APP_SESSION_SECRET", "").strip()
    if key:
        return key
    # fallback for dev; in production should set APP_SESSION_SECRET
    return "dev-session-secret-change-me"


def _sign(value: str) -> str:
    return hmac.new(_secret_key().encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def build_session_cookie(session_id: str) -> str:
    sig = _sign(session_id)
    return f"{session_id}.{sig}"


def parse_session_cookie(raw_value: str | None) -> Optional[str]:
    if not raw_value or "." not in raw_value:
        return None
    session_id, sig = raw_value.rsplit(".", 1)
    if not session_id or not sig:
        return None
    expected = _sign(session_id)
    if not hmac.compare_digest(sig, expected):
        return None
    return session_id


@dataclass
class SessionRecord:
    username: str
    expires_at: int


@dataclass
class WsTicketRecord:
    session_id: str
    expires_at: int


class SessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._ws_tickets: dict[str, WsTicketRecord] = {}

    def _gc(self) -> None:
        ts = _now()
        self._sessions = {k: v for k, v in self._sessions.items() if v.expires_at > ts}
        self._ws_tickets = {k: v for k, v in self._ws_tickets.items() if v.expires_at > ts}

    def create_session(self, username: str) -> str:
        session_id = secrets.token_urlsafe(48)
        with self._lock:
            self._gc()
            self._sessions[session_id] = SessionRecord(
                username=username,
                expires_at=_now() + SESSION_TTL_SECONDS,
            )
        return session_id

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            self._gc()
            record = self._sessions.get(session_id)
            if not record:
                return None
            # sliding expiration
            record.expires_at = _now() + SESSION_TTL_SECONDS
            self._sessions[session_id] = record
            return record

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._ws_tickets = {k: v for k, v in self._ws_tickets.items() if v.session_id != session_id}

    def create_ws_ticket(self, session_id: str) -> str:
        ticket = secrets.token_urlsafe(48)
        with self._lock:
            self._gc()
            self._ws_tickets[ticket] = WsTicketRecord(
                session_id=session_id,
                expires_at=_now() + WS_TICKET_TTL_SECONDS,
            )
        return ticket

    def consume_ws_ticket(self, ticket: str) -> Optional[str]:
        with self._lock:
            self._gc()
            record = self._ws_tickets.pop(ticket, None)
            if not record:
                return None
            return record.session_id


session_store = SessionStore()


def build_ws_exchange_key() -> str:
    # high entropy, single-connection key material
    return secrets.token_urlsafe(64)
