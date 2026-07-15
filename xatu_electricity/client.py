from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlparse

import httpx

from .crypto import encrypt_cas_password
from .exceptions import (
    ApiResponseError,
    AuthenticationError,
    CaptchaRequiredError,
    CredentialsMissingError,
    RoomNotFoundError,
)
from .html import extract_element_text, parse_login_form
from .models import ElectricityBalance, Room


class TokenStore(Protocol):
    async def load(self) -> str | None: ...

    async def save(self, token: str) -> None: ...

    async def clear(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ClientConfig:
    username: str = ""
    password: str = ""
    project_id: str = "88827410e9214c81a886f6e1dcb20dcc"
    area_id: str = "1"
    cas_login_url: str = (
        "https://authserver.xatu.edu.cn/authserver/login"
        "?service=https%3A%2F%2Fjfpay.xatu.edu.cn%2FcasLogin%2F"
    )
    api_base_url: str = "https://jfpay.xatu.edu.cn/api/"
    request_timeout_seconds: float = 20.0
    verify_tls: bool = True


class XatuElectricityClient:
    """Async, low-overhead electricity query client with automatic auth."""

    def __init__(
        self,
        config: ClientConfig,
        token_store: TokenStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cas_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._token_store = token_store
        self._auth_lock = asyncio.Lock()
        self._cas_transport = cas_transport
        self._api_client = httpx.AsyncClient(
            base_url=config.api_base_url,
            follow_redirects=False,
            timeout=config.request_timeout_seconds,
            verify=config.verify_tls,
            transport=transport,
            headers={"User-Agent": "astrbot-plugin-xatu-electricity/0.1.0"},
        )

    async def aclose(self) -> None:
        await self._api_client.aclose()

    async def ensure_token(self, *, force_login: bool = False) -> str:
        if not force_login:
            token = await self._token_store.load()
            if token:
                return token

        async with self._auth_lock:
            if not force_login:
                token = await self._token_store.load()
                if token:
                    return token

            if not self.config.username or not self.config.password:
                raise CredentialsMissingError(
                    "CAS login is required, but username/password are missing"
                )

            token = await self._login_with_cas()
            await self._token_store.save(token)
            return token

    async def get_room_list(self, building_id: str) -> list[Room]:
        data = await self._api_post(
            "pay/web/payEleCostController/queryRoomList",
            {
                "projectId": self.config.project_id,
                "areaid": self.config.area_id,
                "buildid": str(building_id),
            },
        )
        if not isinstance(data, list):
            raise ApiResponseError(
                "invalid-data", "queryRoomList did not return a list"
            )
        return [Room.from_api(value) for value in data if isinstance(value, dict)]

    async def resolve_room(self, building_id: str, room_name: str) -> Room:
        rooms = await self.get_room_list(building_id)
        target = room_name.strip()
        accepted_names = {target}
        if not target.endswith("空调"):
            accepted_names.add(f"{target}空调")

        matches = [room for room in rooms if room.room_name in accepted_names]
        if len(matches) != 1:
            raise RoomNotFoundError(str(building_id), room_name, len(matches))
        return matches[0]

    async def get_balance(self, building_id: str, room_name: str) -> ElectricityBalance:
        room = await self.resolve_room(str(building_id), room_name)
        data = await self._api_post(
            "pay/web/payEleCostController/querySydl",
            {
                "projectId": self.config.project_id,
                "areaid": self.config.area_id,
                "buildid": str(building_id),
                "roomid": room.room_id,
            },
        )
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ApiResponseError("invalid-data", "querySydl returned no data")
        return ElectricityBalance.from_api(
            building_id=str(building_id), room=room, value=data[0]
        )

    async def _api_post(self, path: str, payload: dict[str, str]) -> Any:
        force_login = False
        token = await self.ensure_token()

        for _ in range(3):
            response = await self._api_client.post(
                path,
                json=payload,
                headers={"X-Token": token},
            )
            if response.status_code in {401, 403} and not force_login:
                await self._token_store.clear()
                token = await self.ensure_token(force_login=True)
                force_login = True
                continue
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ApiResponseError(
                    "invalid-json", "API response was not a JSON object"
                )
            code = str(result.get("messageCode", ""))

            if code == "0":
                return result.get("data")

            if code == "-3":
                refreshed_token = result.get("data")
                if not isinstance(refreshed_token, str) or not refreshed_token:
                    raise AuthenticationError(
                        "API requested token refresh without returning a token"
                    )
                token = refreshed_token
                await self._token_store.save(token)
                continue

            authentication_failed = code in {"-1", "-2", "-4"} or (
                result.get("code") == 50014
            )
            if authentication_failed and not force_login:
                await self._token_store.clear()
                token = await self.ensure_token(force_login=True)
                force_login = True
                continue

            raise ApiResponseError(code, str(result.get("message", "unknown")))

        raise AuthenticationError("API authentication retries exhausted")

    async def _login_with_cas(self) -> str:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self.config.request_timeout_seconds,
            verify=self.config.verify_tls,
            transport=self._cas_transport,
            headers={"User-Agent": "astrbot-plugin-xatu-electricity/0.1.0"},
        ) as client:
            login_response = await client.get(self.config.cas_login_url)
            login_response.raise_for_status()
            form = parse_login_form(login_response.text)

            execution = form.fields.get("execution", "")
            salt = form.values_by_id.get("pwdEncryptSalt", "")
            if not execution or not salt:
                raise AuthenticationError(
                    "CAS login page did not contain execution and pwdEncryptSalt"
                )

            captcha_url = urljoin(self.config.cas_login_url, "checkNeedCaptcha.htl")
            captcha_response = await client.get(
                captcha_url, params={"username": self.config.username}
            )
            if captcha_response.is_success:
                try:
                    if captcha_response.json().get("isNeed") is True:
                        raise CaptchaRequiredError(
                            "CAS requires an interactive CAPTCHA"
                        )
                except json.JSONDecodeError:
                    pass

            payload = {
                "username": self.config.username,
                "password": encrypt_cas_password(self.config.password, salt),
                "captcha": "",
                "rememberMe": "true",
                "_eventId": form.fields.get("_eventId", "submit"),
                "cllt": "userNameLogin",
                "dllt": form.fields.get("dllt", "generalLogin"),
                "lt": form.fields.get("lt", ""),
                "execution": execution,
            }
            response = await client.post(self.config.cas_login_url, data=payload)
            current_url = str(response.url)

            for _ in range(10):
                token = self._token_from_response(response, client)
                if token:
                    return token

                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise AuthenticationError(
                            "CAS redirect did not include a Location header"
                        )

                    next_url = urljoin(current_url, location)
                    current_url = urldefrag(next_url).url
                    response = await client.get(current_url)
                    continue

                error = self._authentication_error_from_html(response.text)
                if error:
                    raise AuthenticationError(f"CAS login failed: {error}")

                if response.is_success:
                    script_redirect = self._trusted_script_redirect(
                        response.text, current_url
                    )
                    if script_redirect:
                        current_url = script_redirect
                        response = await client.get(current_url)
                        continue

                    returned_form = parse_login_form(response.text)
                    if returned_form.fields.get("execution"):
                        raise AuthenticationError(
                            "CAS returned to the login page without an error "
                            "message; interactive verification may be required"
                        )
                    final_url = urlparse(str(response.url))
                    diagnostic = self._response_diagnostic(response)
                    raise AuthenticationError(
                        "CAS callback returned no X-Token at "
                        f"{final_url.netloc}{final_url.path} ({diagnostic})"
                    )

                response.raise_for_status()

        raise AuthenticationError("CAS redirect limit exceeded")

    @staticmethod
    def _trusted_script_redirect(html: str, current_url: str) -> str | None:
        """Extract the payment site's desktop redirect without executing JS."""

        current = urlparse(current_url)
        candidates: list[str] = []
        for match in re.finditer(
            r"""window\.location\.href\s*=\s*["']([^"']+)["']""",
            html,
            re.IGNORECASE,
        ):
            candidate = urljoin(current_url, match.group(1))
            target = urlparse(candidate)
            if (
                target.scheme == "https"
                and target.netloc == current.netloc
                and target.path.startswith("/api/pay/web/jzcas/casLogin/")
            ):
                candidates.append(candidate)

        return next(
            (
                candidate
                for candidate in candidates
                if urlparse(candidate).path.endswith("/2")
            ),
            candidates[0] if candidates else None,
        )

    @staticmethod
    def _token_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        queries = [parsed.query]
        if "?" in parsed.fragment:
            queries.append(parsed.fragment.split("?", 1)[1])
        elif "=" in parsed.fragment:
            queries.append(parsed.fragment)

        for query in queries:
            values = parse_qs(query).get("token", [])
            if values and XatuElectricityClient._looks_like_token(values[0]):
                return values[0]
        return None

    @classmethod
    def _token_from_response(
        cls, response: httpx.Response, client: httpx.AsyncClient
    ) -> str | None:
        for url in (str(response.url), response.headers.get("location", "")):
            if token := cls._token_from_url(url):
                return token

        for header_name in ("X-Token", "Authorization"):
            token = response.headers.get(header_name, "")
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            if cls._looks_like_token(token):
                return token

        token_cookie_names = {"datalook_reimbursement_token", "token", "X-Token"}
        for cookies in (response.cookies, client.cookies):
            for cookie in cookies.jar:
                if cookie.name in token_cookie_names and cls._looks_like_token(
                    cookie.value
                ):
                    return cookie.value

        try:
            value = response.json()
        except json.JSONDecodeError:
            value = None
        if token := cls._token_from_json(value):
            return token

        for match in re.finditer(
            r"(?:[?&#]|\b)token=([^&\"'<>\s]+)", response.text, re.IGNORECASE
        ):
            token = unquote(match.group(1))
            if cls._looks_like_token(token):
                return token
        for match in re.finditer(
            r"""["'](?:token|x-token)["']\s*[:=]\s*["']([^"']+)["']""",
            response.text,
            re.IGNORECASE,
        ):
            token = unquote(match.group(1))
            if cls._looks_like_token(token):
                return token
        return None

    @classmethod
    def _token_from_json(cls, value: Any) -> str | None:
        if isinstance(value, dict):
            token = value.get("token")
            if isinstance(token, str) and cls._looks_like_token(token):
                return token
            for child in value.values():
                if token := cls._token_from_json(child):
                    return token
        elif isinstance(value, list):
            for child in value:
                if token := cls._token_from_json(child):
                    return token
        elif isinstance(value, str) and cls._looks_like_token(value):
            return value
        return None

    @staticmethod
    def _looks_like_token(value: str) -> bool:
        return len(value) >= 32 and value.count(".") == 2

    @staticmethod
    def _authentication_error_from_html(html: str) -> str:
        messages: list[str] = []
        for element_id in (
            "showErrorTip",
            "formErrorTip",
            "nameErrorTip",
            "pwdErrorTip",
            "captchaErrorTip",
            "showWarnTip",
            "showFidoErrorTipSpan",
        ):
            message = extract_element_text(html, element_id)
            words = message.split()
            if words and len(set(words)) == 1:
                message = words[0]
            if message and message not in messages:
                messages.append(message)
        return "; ".join(messages)

    @staticmethod
    def _response_diagnostic(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "unknown").split(";", 1)[0]
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL
        )
        title = ""
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:80]
        details = [f"status={response.status_code}", f"type={content_type}"]
        if title:
            details.append(f"title={title}")
        return ", ".join(details)
