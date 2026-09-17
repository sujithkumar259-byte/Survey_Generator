"""Optional authentication helpers for Streamlit deployments.

Authentication is disabled by default for local development. Production operators
can enable either a simple built-in password/token gate or proxy/SSO mode.

Supported environment variables:
- SURVEY_GENERATOR_AUTH_MODE=none|password|token|proxy
- SURVEY_GENERATOR_USERS=alice:plain-password,bob:sha256:<digest>
- SURVEY_GENERATOR_PASSWORD or SURVEY_GENERATOR_PASSWORD_SHA256 for a shared/default user
- SURVEY_GENERATOR_AUTH_TOKEN for a shared token
- SURVEY_GENERATOR_PROXY_USER_ENV=REMOTE_USER for proxy/SSO mode
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class AuthConfig:
    enabled: bool = False
    users: Mapping[str, str] = field(default_factory=dict)  # username -> sha256(password)
    shared_token_hash: str = ""
    mode: str = "none"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _auth_mode() -> str:
    return os.environ.get("SURVEY_GENERATOR_AUTH_MODE", "none").strip().lower()


def _proxy_user_env() -> str:
    return os.environ.get("SURVEY_GENERATOR_PROXY_USER_ENV", "REMOTE_USER")


def load_auth_config() -> AuthConfig:
    mode = _auth_mode()
    enabled = _truthy(os.environ.get("SURVEY_GENERATOR_AUTH_ENABLED")) or mode not in {"", "none", "off", "false", "0"}
    users: dict[str, str] = {}

    raw_users = os.environ.get("SURVEY_GENERATOR_USERS", "")
    for item in raw_users.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        username, secret = item.split(":", 1)
        username = username.strip()
        secret = secret.strip()
        if not username or not secret:
            continue
        users[username] = secret[7:] if secret.startswith("sha256:") else _sha256(secret)

    default_user = os.environ.get("SURVEY_GENERATOR_DEFAULT_USER", "analyst")
    legacy_hash = os.environ.get("SURVEY_GENERATOR_PASSWORD_SHA256", "").strip()
    legacy_plain = os.environ.get("SURVEY_GENERATOR_PASSWORD", "")
    if legacy_hash:
        users.setdefault(default_user, legacy_hash)
    elif legacy_plain:
        users.setdefault(default_user, _sha256(legacy_plain))

    token = os.environ.get("SURVEY_GENERATOR_AUTH_TOKEN", "").strip()
    token_hash = _sha256(token) if token else ""

    if users or token_hash:
        enabled = True
        if mode in {"", "none", "off", "false", "0"}:
            mode = "password"

    return AuthConfig(enabled=enabled, users=users, shared_token_hash=token_hash, mode=mode)


def verify_password(username: str, password: str, config: AuthConfig | None = None) -> bool:
    config = config or load_auth_config()
    if not config.enabled:
        return True
    expected = str((config.users or {}).get((username or "").strip(), ""))
    return bool(expected) and hmac.compare_digest(expected, _sha256(password or ""))


def verify_token(token: str, config: AuthConfig | None = None) -> bool:
    config = config or load_auth_config()
    if not config.enabled:
        return True
    expected = str(config.shared_token_hash or "")
    return bool(expected) and hmac.compare_digest(expected, _sha256(token or ""))


def current_user(session_state: Any) -> str:
    return str(session_state.get("auth_user") or session_state.get("authenticated_user") or "anonymous")


def require_authenticated_user(st) -> str:
    """Render an auth gate if configured and return the current user name."""
    config = load_auth_config()
    mode = config.mode
    if not config.enabled:
        st.session_state.setdefault("auth_user", "anonymous")
        st.session_state.setdefault("authenticated_user", "anonymous")
        return "anonymous"

    if mode == "proxy":
        user = os.environ.get(_proxy_user_env(), "").strip() or "proxy-user"
        st.session_state["auth_user"] = user
        st.session_state["authenticated_user"] = user
        st.session_state["auth_ok"] = True
        return user

    if mode not in {"password", "token", "none", ""} and not config.users and not config.shared_token_hash:
        st.error(f"Unsupported SURVEY_GENERATOR_AUTH_MODE={mode!r}. Use none, password, token, or proxy.")
        st.stop()

    if st.session_state.get("auth_ok") or st.session_state.get("authenticated"):
        return current_user(st.session_state)

    st.markdown("### Sign in")
    st.caption("Authentication is enabled for this deployment.")
    with st.form("login_form"):
        default_user = os.environ.get("SURVEY_GENERATOR_DEFAULT_USER", "analyst")
        username = st.text_input("Username", value=default_user)
        secret = st.text_input("Password or access token", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if verify_password(username, secret, config) or verify_token(secret, config):
            user = username.strip() or "authenticated"
            st.session_state["auth_ok"] = True
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = user
            st.session_state["authenticated_user"] = user
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop()


def require_authentication(st) -> str:
    """Backward-compatible alias."""
    return require_authenticated_user(st)
