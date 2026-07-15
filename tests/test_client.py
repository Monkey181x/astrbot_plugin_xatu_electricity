from __future__ import annotations

import base64
import json
import time
import unittest
from decimal import Decimal
from urllib.parse import parse_qs

import httpx

from xatu_electricity.client import ClientConfig, XatuElectricityClient
from xatu_electricity.exceptions import AuthenticationError


def make_token(expires_in_seconds: int = 3600) -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return (
        f"{encode({'alg': 'none'})}."
        f"{encode({'exp': int(time.time()) + expires_in_seconds})}.x"
    )


class MemoryTokenStore:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    async def load(self) -> str | None:
        return self.token

    async def save(self, token: str) -> None:
        self.token = token

    async def clear(self) -> None:
        self.token = None


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_uses_saved_expired_token_before_cas_login(self) -> None:
        expired_token = make_token(-3600)
        request_tokens: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            request_tokens.append(request.headers["X-Token"])
            return httpx.Response(
                200, json={"messageCode": "0", "message": "ok", "data": []}
            )

        store = MemoryTokenStore(expired_token)
        client = XatuElectricityClient(
            ClientConfig(),
            store,
            transport=httpx.MockTransport(handler),
        )

        async def unexpected_login() -> str:
            raise AssertionError(
                "CAS login must not run before the API rejects a token"
            )

        client._login_with_cas = unexpected_login
        try:
            await client.get_room_list("3")
        finally:
            await client.aclose()

        self.assertEqual(request_tokens, [expired_token])
        self.assertEqual(store.token, expired_token)

    async def test_get_balance_resolves_room_and_calculates_balance(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("queryRoomList"):
                data = [
                    {
                        "roomid": "12566",
                        "roomname": "3128空调     ",
                    }
                ]
            else:
                data = [
                    {
                        "oddl": "6287.81005859375",
                        "suml": "6497.2099609375",
                    }
                ]
            return httpx.Response(
                200, json={"messageCode": "0", "message": "ok", "data": data}
            )

        store = MemoryTokenStore(make_token())
        client = XatuElectricityClient(
            ClientConfig(), store, transport=httpx.MockTransport(handler)
        )
        try:
            result = await client.get_balance("3", "3128")
        finally:
            await client.aclose()

        self.assertEqual(result.room_id, "12566")
        self.assertEqual(result.balance, Decimal("209.39990234375"))

    async def test_api_saves_rotated_token_and_retries(self) -> None:
        rotated = make_token(7200)
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "messageCode": "-3",
                        "message": "refresh",
                        "data": rotated,
                    },
                )
            return httpx.Response(
                200, json={"messageCode": "0", "message": "ok", "data": []}
            )

        store = MemoryTokenStore(make_token())
        client = XatuElectricityClient(
            ClientConfig(), store, transport=httpx.MockTransport(handler)
        )
        try:
            await client.get_room_list("3")
        finally:
            await client.aclose()

        self.assertEqual(calls, 2)
        self.assertEqual(store.token, rotated)

    async def test_api_relogs_with_config_credentials_after_token_rejection(
        self,
    ) -> None:
        rejection_responses = {
            "http-unauthorized": lambda: httpx.Response(401),
            "api-authentication-failed": lambda: httpx.Response(
                200,
                json={
                    "messageCode": "-1",
                    "message": "authentication failed",
                    "data": None,
                },
            ),
        }

        for rejection_name, rejection_response in rejection_responses.items():
            with self.subTest(rejection=rejection_name):
                old_token = make_token()
                new_token = make_token(7200)
                request_tokens: list[str] = []
                login_credentials: list[tuple[str, str]] = []

                async def handler(request: httpx.Request) -> httpx.Response:
                    request_tokens.append(request.headers["X-Token"])
                    if len(request_tokens) == 1:
                        return rejection_response()
                    return httpx.Response(
                        200,
                        json={"messageCode": "0", "message": "ok", "data": []},
                    )

                store = MemoryTokenStore(old_token)
                client = XatuElectricityClient(
                    ClientConfig(username="json-user", password="json-password"),
                    store,
                    transport=httpx.MockTransport(handler),
                )

                async def login_with_json_credentials() -> str:
                    login_credentials.append(
                        (client.config.username, client.config.password)
                    )
                    return new_token

                client._login_with_cas = login_with_json_credentials
                try:
                    await client.get_room_list("3")
                finally:
                    await client.aclose()

                self.assertEqual(login_credentials, [("json-user", "json-password")])
                self.assertEqual(request_tokens, [old_token, new_token])
                self.assertEqual(store.token, new_token)

    async def test_cas_login_follows_ticket_and_extracts_token(self) -> None:
        issued_token = make_token()
        post_fields: dict[str, list[str]] = {}

        async def cas_handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_fields
            if request.url.path.endswith("/checkNeedCaptcha.htl"):
                return httpx.Response(200, json={"isNeed": False})

            if request.method == "POST":
                post_fields = parse_qs((await request.aread()).decode())
                return httpx.Response(
                    302,
                    headers={
                        "location": ("https://jfpay.xatu.edu.cn/casLogin/?ticket=ST-1")
                    },
                )

            if request.url.path == "/casLogin/":
                return httpx.Response(
                    302,
                    headers={
                        "location": (
                            "https://jfpay.xatu.edu.cn/#/middle_page"
                            f"?token={issued_token}&idserial=1"
                        )
                    },
                )

            return httpx.Response(
                200,
                text="""
                <form id="pwdFromId">
                  <input name="execution" value="e1">
                  <input name="_eventId" value="submit">
                  <input name="dllt" value="generalLogin">
                  <input id="pwdEncryptSalt" value="rjBFAaHsNkKAhpoi">
                </form>
                """,
            )

        store = MemoryTokenStore()
        client = XatuElectricityClient(
            ClientConfig(username="user", password="secret"),
            store,
            transport=httpx.MockTransport(lambda request: httpx.Response(500)),
            cas_transport=httpx.MockTransport(cas_handler),
        )
        try:
            token = await client.ensure_token()
        finally:
            await client.aclose()

        self.assertEqual(token, issued_token)
        self.assertEqual(store.token, issued_token)
        self.assertEqual(post_fields["username"], ["user"])
        self.assertNotEqual(post_fields["password"], ["secret"])
        self.assertEqual(post_fields["execution"], ["e1"])

    async def test_cas_login_extracts_token_from_payment_cookie(self) -> None:
        issued_token = make_token()

        async def cas_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/checkNeedCaptcha.htl"):
                return httpx.Response(200, json={"isNeed": False})
            if request.method == "POST":
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://jfpay.xatu.edu.cn/casLogin/?ticket=ST-1"
                    },
                )
            if request.url.path == "/casLogin/":
                cookie_name = "datalook_" + "reimbursement_token"
                return httpx.Response(
                    200,
                    text="<html><body>payment service</body></html>",
                    headers={"set-cookie": f"{cookie_name}={issued_token}; Path=/"},
                )
            return httpx.Response(
                200,
                text="""
                <form id="pwdFromId">
                  <input name="execution" value="e1">
                  <input name="_eventId" value="submit">
                  <input name="dllt" value="generalLogin">
                  <input id="pwdEncryptSalt" value="rjBFAaHsNkKAhpoi">
                </form>
                """,
            )

        store = MemoryTokenStore()
        client = XatuElectricityClient(
            ClientConfig(username="user", password="secret"),
            store,
            cas_transport=httpx.MockTransport(cas_handler),
        )
        try:
            token = await client.ensure_token()
        finally:
            await client.aclose()

        self.assertEqual(token, issued_token)
        self.assertEqual(store.token, issued_token)

    async def test_cas_login_follows_trusted_javascript_intermediate_page(
        self,
    ) -> None:
        issued_token = make_token()

        async def cas_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/checkNeedCaptcha.htl"):
                return httpx.Response(200, json={"isNeed": False})
            if request.method == "POST":
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://jfpay.xatu.edu.cn/casLogin/?ticket=ST-1"
                    },
                )
            if request.url.path == "/casLogin/" and request.url.params:
                return httpx.Response(
                    302,
                    headers={"location": "https://jfpay.xatu.edu.cn/casLogin/"},
                )
            if request.url.path == "/casLogin/":
                return httpx.Response(
                    200,
                    text="""
                    <script>
                    if (is_mobi) {
                      window.location.href =
                        "https://jfpay.xatu.edu.cn/api/pay/web/jzcas/casLogin/user/1";
                    } else {
                      window.location.href =
                        "https://jfpay.xatu.edu.cn/api/pay/web/jzcas/casLogin/user/2";
                    }
                    </script>
                    """,
                )
            if request.url.path.endswith("/jzcas/casLogin/user/2"):
                return httpx.Response(
                    302,
                    headers={
                        "location": (
                            "https://jfpay.xatu.edu.cn/#/middle_page"
                            f"?token={issued_token}&idserial=1"
                        )
                    },
                )
            return httpx.Response(
                200,
                text="""
                <form id="pwdFromId">
                  <input name="execution" value="e1">
                  <input id="pwdEncryptSalt" value="rjBFAaHsNkKAhpoi">
                </form>
                """,
            )

        store = MemoryTokenStore()
        client = XatuElectricityClient(
            ClientConfig(username="user", password="secret"),
            store,
            cas_transport=httpx.MockTransport(cas_handler),
        )
        try:
            token = await client.ensure_token()
        finally:
            await client.aclose()

        self.assertEqual(token, issued_token)
        self.assertEqual(store.token, issued_token)

    def test_script_redirect_rejects_untrusted_urls(self) -> None:
        html = """
        <script>
        window.location.href = "https://example.com/api/pay/web/jzcas/casLogin/u/2";
        window.location.href = "https://jfpay.xatu.edu.cn/other/path";
        </script>
        """

        redirect = XatuElectricityClient._trusted_script_redirect(
            html, "https://jfpay.xatu.edu.cn/casLogin/"
        )

        self.assertIsNone(redirect)

    async def test_cas_login_reports_page_error(self) -> None:
        async def cas_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/checkNeedCaptcha.htl"):
                return httpx.Response(200, json={"isNeed": False})
            if request.method == "POST":
                return httpx.Response(
                    401,
                    text='<div id="showErrorTip">用户名或密码错误</div>',
                )
            return httpx.Response(
                200,
                text="""
                <form id="pwdFromId">
                  <input name="execution" value="e1">
                  <input id="pwdEncryptSalt" value="rjBFAaHsNkKAhpoi">
                </form>
                """,
            )

        client = XatuElectricityClient(
            ClientConfig(username="user", password="wrong"),
            MemoryTokenStore(),
            cas_transport=httpx.MockTransport(cas_handler),
        )
        try:
            with self.assertRaisesRegex(AuthenticationError, "用户名或密码错误"):
                await client.ensure_token()
        finally:
            await client.aclose()

    async def test_extracts_token_from_response_header(self) -> None:
        issued_token = make_token()
        response = httpx.Response(
            200,
            headers={"X-Token": issued_token},
            request=httpx.Request("GET", "https://jfpay.xatu.edu.cn/casLogin/"),
        )
        async with httpx.AsyncClient() as client:
            token = XatuElectricityClient._token_from_response(response, client)

        self.assertEqual(token, issued_token)

    async def test_extracts_token_from_json_data_and_html_script(self) -> None:
        issued_token = make_token()
        responses = [
            httpx.Response(
                200,
                text=json.dumps({"messageCode": "0", "data": issued_token}),
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", "https://jfpay.xatu.edu.cn/casLogin/"),
            ),
            httpx.Response(
                200,
                text=f'<script>window.auth = {{"token": "{issued_token}"}}</script>',
                request=httpx.Request("GET", "https://jfpay.xatu.edu.cn/casLogin/"),
            ),
        ]

        async with httpx.AsyncClient() as client:
            tokens = [
                XatuElectricityClient._token_from_response(response, client)
                for response in responses
            ]

        self.assertEqual(tokens, [issued_token, issued_token])

    def test_response_diagnostic_excludes_body_and_token(self) -> None:
        issued_token = make_token()
        response = httpx.Response(
            200,
            text=f"<title>Payment callback error</title><body>{issued_token}</body>",
            headers={"content-type": "text/html; charset=UTF-8"},
        )

        diagnostic = XatuElectricityClient._response_diagnostic(response)

        self.assertEqual(
            diagnostic,
            "status=200, type=text/html, title=Payment callback error",
        )
        self.assertNotIn(issued_token, diagnostic)


if __name__ == "__main__":
    unittest.main()
