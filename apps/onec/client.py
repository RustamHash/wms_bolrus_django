from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

from apps.onec.exceptions import OnecAuthError, OnecConfigError, OnecNotFound, OnecRequestError

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


class ODataClient:
    """Низкоуровневый клиент стандартного интерфейса OData 1С."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._auth = f"Basic {token}"
        self._cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_settings(cls) -> ODataClient:
        base = getattr(settings, "ONEC_ODATA_URL", "") or ""
        user = getattr(settings, "ONEC_ODATA_USER", "") or ""
        password = getattr(settings, "ONEC_ODATA_PASSWORD", "") or ""
        timeout = float(getattr(settings, "ONEC_ODATA_TIMEOUT", 60) or 60)
        if not base or not user or not password:
            raise OnecConfigError(
                "Задайте ONEC_ODATA_URL, ONEC_ODATA_USER и ONEC_ODATA_PASSWORD в окружении"
            )
        return cls(base, user, password, timeout=timeout)

    def clear_cache(self) -> None:
        self._cache.clear()

    def get(
        self,
        entity: str,
        *,
        filter: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        format: str = "json",
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"$format": format}
        if filter:
            params["$filter"] = filter
        if select:
            params["$select"] = select
        if orderby:
            params["$orderby"] = orderby
        if top is not None:
            params["$top"] = str(top)
        if skip is not None:
            params["$skip"] = str(skip)
        payload = self._request("GET", entity, params=params)
        if isinstance(payload, dict) and "value" in payload:
            return list(payload["value"])
        raise OnecRequestError(f"Ожидался список в {entity}", body=str(payload)[:500])

    def get_one(self, entity: str, guid: str, *, select: str | None = None) -> dict[str, Any]:
        if not guid or guid == EMPTY_GUID:
            raise OnecNotFound(f"Пустой ключ для {entity}")
        path = f"{entity}(guid'{guid}')"
        params: dict[str, str] = {"$format": "json"}
        if select:
            params["$select"] = select
        payload = self._request("GET", path, params=params)
        if not isinstance(payload, dict):
            raise OnecRequestError(f"Ожидался объект {entity}", body=str(payload)[:500])
        return payload

    def get_cached(self, entity: str, guid: str, *, select: str | None = None) -> dict[str, Any] | None:
        if not guid or guid == EMPTY_GUID:
            return None
        key = f"{entity}:{guid}:{select or ''}"
        if key not in self._cache:
            try:
                self._cache[key] = self.get_one(entity, guid, select=select)
            except (OnecNotFound, OnecRequestError):
                self._cache[key] = {}
        row = self._cache[key]
        return row or None

    def _request(self, method: str, path: str, *, params: dict[str, str] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {}, quote_via=urllib.parse.quote)
        encoded_path = urllib.parse.quote(path.lstrip("/"), safe="/()'=,_-$:")
        url = f"{self.base_url}/{encoded_path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "Authorization": self._auth,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise OnecAuthError(
                    "OData: неверный логин или пароль (сквозная авторизация Scloud)"
                ) from exc
            if exc.code == 404:
                raise OnecNotFound(f"OData: не найдено {path}") from exc
            message = _odata_message(body) or f"OData HTTP {exc.code}"
            raise OnecRequestError(message, status=exc.code, body=body[:1000]) from exc
        except urllib.error.URLError as exc:
            raise OnecRequestError(f"OData недоступен: {exc.reason}") from exc
        if not raw:
            return {}
        return json.loads(raw)


def _odata_message(body: str) -> str:
    try:
        data = json.loads(body)
        err = data.get("odata.error") or data.get("error") or {}
        msg = err.get("message")
        if isinstance(msg, dict):
            return str(msg.get("value") or "")
        if isinstance(msg, str):
            return msg
    except (json.JSONDecodeError, AttributeError, TypeError):
        return ""
    return ""
