"""Safe, worker-thread-only validation of Schluter account credentials.

Never cache, publish, log, or echo login details or a returned session ID.
"""

from __future__ import annotations

from .v_lib.api import Api


def check_login(username: str, password: str) -> str:
    """Return only 'ok', 'invalid_auth' or 'cannot_connect'."""
    api = None
    try:
        api = Api(email=username, password=password)
        response = api.get_session()
        if response is None:
            return "cannot_connect"
        status = getattr(response, "status_code", None)
        if status in (401, 403):
            return "invalid_auth"
        if type(status) is not int or not 200 <= status < 300:
            return "cannot_connect"
        data = response.json()
        if (type(data) is dict
                and data.get("ErrorCode") in (0, "0")
                and type(data.get("SessionId")) is str
                and data["SessionId"].strip()):
            return "ok"
        return "invalid_auth"
    except Exception:
        return "cannot_connect"
    finally:
        if api is not None:
            try:
                api._http_session.close()
            except Exception:
                pass
