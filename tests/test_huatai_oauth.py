import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastmcp import Client

from huatai_oauth.server import MetaAPI, MetaVerifier, Settings, build_server


@pytest.fixture
def settings():
    return Settings("https://meta-ads-mcp-bao6.onrender.com", "28622598490723764",
                    "test-app-secret", "test-signing-key-" * 4, "981676801551252")


def responder(settings, permissions=True, account=True):
    def respond(request):
        assert request.url.host == "graph.facebook.com"
        assert request.headers["authorization"] == "Bearer provider-secret"
        assert "access_token" not in request.url.params
        assert len(request.url.params["appsecret_proof"]) == 64
        if request.url.path.endswith("/permissions"):
            payload = {"data": [{"permission": "ads_read", "status": "granted" if permissions else "declined"}]}
        elif request.url.path.endswith("/me"):
            payload = {"id": "test-user", "name": "Test"}
        elif request.url.path.endswith(("/campaigns", "/insights")):
            payload = {"data": [{"id": "c1", "name": "Test campaign"}], "paging": {
                "next": "https://attacker.invalid/?access_token=must-not-return",
                "cursors": {"after": "cursor-1"},
            }}
        else:
            payload = {"account_id": settings.account_id if account else "different-account"}
        return httpx.Response(200, json=payload)
    return httpx.MockTransport(respond)


@pytest.mark.asyncio
@pytest.mark.parametrize("permissions,account,expected", [(True, True, True), (False, True, False), (True, False, False)])
async def test_meta_permission_and_account_boundary(settings, permissions, account, expected):
    api = MetaAPI(settings, responder(settings, permissions, account))
    result = await MetaVerifier(settings, api).verify_token("provider-secret")
    assert bool(result) == expected


@pytest.mark.asyncio
async def test_discovery_and_fail_closed_auth(settings):
    mcp = build_server(settings, responder(settings))
    app = mcp.http_app(stateless_http=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=settings.base_url) as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        resource = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert resource.status_code == 200
        assert resource.json()["resource"] == settings.base_url + "/mcp"
        metadata = await client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        assert "S256" in metadata.json()["code_challenge_methods_supported"]
        for headers in ({}, {"Authorization": "Bearer provider-secret"}):
            result = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
            assert result.status_code == 401
            assert "resource_metadata" in result.headers["www-authenticate"]
        registration = await client.post("/register", json={
            "redirect_uris": ["https://attacker.invalid/callback"],
            "token_endpoint_auth_method": "none",
        })
        assert registration.status_code == 400
        assert "test-app-secret" not in registration.text
        valid = await client.post("/register", json={
            "redirect_uris": ["https://chatgpt.com/connector_platform/oauth_redirect"],
            "token_endpoint_auth_method": "none", "scope": "ads_read",
        })
        assert valid.status_code == 201
        no_pkce = await client.get("/authorize", params={
            "client_id": valid.json()["client_id"], "response_type": "code",
            "redirect_uri": "https://chatgpt.com/connector_platform/oauth_redirect",
            "scope": "ads_read", "resource": settings.base_url + "/mcp",
        })
        assert no_pkce.status_code == 302
        error = parse_qs(urlsplit(no_pkce.headers["location"]).query)
        assert error["error"] == ["invalid_request"]
        assert "code_challenge" in error["error_description"][0]
        assert "code" not in error


@pytest.mark.asyncio
async def test_tools_read_only_and_no_pagination_url_leak(settings, monkeypatch):
    api = MetaAPI(settings, responder(settings))
    access = await MetaVerifier(settings, api).verify_token("provider-secret")
    monkeypatch.setattr("huatai_oauth.server.get_access_token", lambda: access)
    mcp = build_server(settings, responder(settings))
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == {"get_ad_account", "list_campaigns", "get_insights"}
        assert all(t.annotations.readOnlyHint for t in tools)
        result = await client.call_tool("list_campaigns", {})
        output = json.loads(result.content[0].text)
        assert output["next_cursor"] == "cursor-1"
        assert "attacker" not in str(result)
        assert "provider-secret" not in str(result)
        insights = await client.call_tool("get_insights", {})
        assert "attacker" not in str(insights)
        assert "must-not-return" not in str(insights)
        with pytest.raises(Exception, match="Invalid pagination"):
            await client.call_tool("list_campaigns", {"limit": 1000})


def test_missing_secrets_fail_startup(monkeypatch):
    for name in ("PUBLIC_BASE_URL", "META_APP_ID", "META_APP_SECRET", "OAUTH_SIGNING_KEY", "META_AD_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="Missing settings"):
        Settings.from_env()
