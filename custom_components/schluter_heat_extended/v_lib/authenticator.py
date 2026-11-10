# Modified from Adam Michaleski's MIT-licensed py-schluter authenticator.
# Changes: session-cache validity and safe persistence for the extended integration.
# Original: https://github.com/prairieapps/py-schluter

import os
import json
import logging
import tempfile
from enum import Enum
from datetime import datetime, timedelta, UTC

_LOGGER = logging.getLogger(__name__)
AUTH_APPLICATION = 7


def to_authentication_json(authentication):
    if authentication is None:
        return json.dumps({})
    return json.dumps({
        "application": AUTH_APPLICATION,
        "session_id": authentication.session_id,
        "state": authentication.state.value,
        "expires": authentication.expires.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
    })


def from_authentication_json(data):
    if not isinstance(data, dict) or data.get("application") != AUTH_APPLICATION:
        return None
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if data.get("state") != AuthenticationState.AUTHENTICATED.value:
        return None
    expires = datetime.strptime(data["expires"], '%Y-%m-%dT%H:%M:%S.%fZ')
    if expires <= datetime.now(UTC).replace(tzinfo=None):
        return None
    return Authentication(AuthenticationState.AUTHENTICATED, session_id, expires)


class AuthenticationState(Enum):
    REQUIRES_AUTHENTICATION = "requires_authentication"
    AUTHENTICATED = "authenticated"
    BAD_EMAIL = "bad_email"
    BAD_PASSWORD = "bad_password"


class Authentication:
    def __init__(self, state, session_id=None, expires=None):
        self._state = state
        self._session_id = session_id
        self._expires = expires

    @property
    def session_id(self):
        return self._session_id

    @property
    def expires(self):
        return self._expires

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value


class Authenticator:
    def __init__(self, api, email, password, session_id_cache_file=None):
        self._api = api
        self._email = email
        self._password = password
        self._session_id_cache_file = session_id_cache_file
        self._authentication = Authentication(AuthenticationState.REQUIRES_AUTHENTICATION)
        # Initial login and every API refresh share this publishing/cache path.
        self._api._on_authenticated = self._accept_session
        if session_id_cache_file is not None:
            try:
                with open(session_id_cache_file, encoding="utf-8") as handle:
                    cached = from_authentication_json(json.load(handle))
                if cached is not None:
                    self._authentication = cached
            except FileNotFoundError:
                pass
            except Exception:
                _LOGGER.warning("Schluter authentication cache could not be reused")

    def _accept_session(self, session_id, expires=None, persist=True):
        """Publish one selected session; cache failures never discard it."""
        with self._api._session_lock:
            authentication = Authentication(
                AuthenticationState.AUTHENTICATED,
                session_id,
                expires if expires is not None else datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )
            self._authentication = authentication
            self._api._session_id = session_id
            if persist:
                try:
                    self._cache_authentication(authentication)
                except Exception:
                    _LOGGER.warning(
                        "Schluter authentication cache could not be written; "
                        "the authenticated session remains active in memory"
                    )

    def authenticate(self):
        with self._api._session_lock:
            authentication = self._authentication
            if (
                authentication.state == AuthenticationState.AUTHENTICATED
                and authentication.session_id
                and authentication.expires > datetime.now(UTC).replace(tzinfo=None)
            ):
                self._accept_session(
                    authentication.session_id, authentication.expires, persist=False
                )
                return self._authentication

            self._authentication = Authentication(AuthenticationState.REQUIRES_AUTHENTICATION)
            response = self._api.get_session(self._email, self._password)
            if response is not None and 200 <= response.status_code < 300:
                try:
                    data = response.json()
                    code = data.get("ErrorCode") if isinstance(data, dict) else None
                    if type(code) in (int, str) and code in (1, "1", 2, "2"):
                        state = (
                            AuthenticationState.BAD_EMAIL if code in (1, "1")
                            else AuthenticationState.BAD_PASSWORD
                        )
                        self._authentication = Authentication(state)
                except Exception:
                    pass
            return self._authentication

    def _cache_authentication(self, authentication):
        if self._session_id_cache_file is None:
            return
        directory = os.path.dirname(os.path.abspath(self._session_id_cache_file))
        fd, temporary = tempfile.mkstemp(prefix=".schluter-auth-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(to_authentication_json(authentication))
            os.replace(temporary, self._session_id_cache_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
