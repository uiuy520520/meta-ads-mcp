"""OAuth-protected, account-scoped Meta Ads discovery and reporting.

FastMCP owns PKCE, consent, callback binding, JWT issuance, and encrypted token
storage. Meta credentials remain server-side and are never tool arguments.
"""

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, OAuthProxy, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Settings:
    base_url: str
    app_id: str
    app_secret: str
    signing_key: str
    account_id: str
    graph_version: str = "v24.0"
    login_config_id: str | None = None

    @classmethod
    def from_env(cls):
        values = {name: os.environ.get(name, "").strip() for name in (
            "PUBLIC_BASE_URL", "META_APP_ID", "META_APP_SECRET",
            "OAUTH_SIGNING_KEY", "META_AD_ACCOUNT_ID",
        )}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError("Missing settings: " + ", ".join(missing))
        result = cls(
            base_url=values["PUBLIC_BASE_URL"].rstrip("/"),
            app_id=values["META_APP_ID"], app_secret=values["META_APP_SECRET"],
            signing_key=values["OAUTH_SIGNING_KEY"],
            account_id=values["META_AD_ACCOUNT_ID"].removeprefix("act_"),
            graph_version=os.environ.get("META_GRAPH_VERSION", "v24.0"),
            login_config_id=os.environ.get("META_LOGIN_CONFIG_ID") or None,
        )
        result.validate()
        return result

    def validate(self):
        url = urlparse(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username or
                url.password or url.path or url.query or url.fragment):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin")
        if not self.app_id.isdigit() or not self.account_id.isdigit():
            raise ValueError("Meta app and account IDs must contain only digits")
        if not re.fullmatch(r"v\d+\.0", self.graph_version):
            raise ValueError("Invalid Graph API version")
        if len(self.signing_key) < 32:
            raise ValueError("OAUTH_SIGNING_KEY must have at least 32 characters")
        if self.login_config_id and not self.login_config_id.isdigit():
            raise ValueError("META_LOGIN_CONFIG_ID must contain only digits")


class MetaAPI:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def get(self, path: str, token: str, params=None):
        # No absolute URLs, arbitrary Graph paths, or provider pagination URLs.
        allowed = {"me", "me/permissions", f"act_{self.settings.account_id}",
                   f"act_{self.settings.account_id}/campaigns",
                   f"act_{self.settings.account_id}/insights"}
        if path not in allowed:
            raise ToolError("This account or endpoint is not enabled.")
        proof = hmac.new(self.settings.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"https://graph.facebook.com/{self.settings.graph_version}/{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={**(params or {}), "appsecret_proof": proof},
                )
                payload = response.json()
                if response.status_code != 200 or "error" in payload:
                    raise ToolError("Meta rejected the request. Reconnect or check ad-account access.")
                return payload
        except (httpx.HTTPError, ValueError):
            raise ToolError("Meta is temporarily unavailable. Please retry.") from None


class MetaVerifier(TokenVerifier):
    def __init__(self, settings: Settings, api: MetaAPI):
        super().__init__(required_scopes=["ads_read"])
        self.settings, self.api = settings, api

    async def verify_token(self, token: str):
        try:
            profile = await self.api.get("me", token, {"fields": "id,name"})
            permissions = await self.api.get("me/permissions", token)
            granted = {p.get("permission") for p in permissions.get("data", [])
                       if p.get("status") == "granted"}
            if "ads_read" not in granted or not profile.get("id"):
                return None
            account = await self.api.get(f"act_{self.settings.account_id}", token, {"fields": "account_id"})
            if account.get("account_id") != self.settings.account_id:
                return None
            return AccessToken(
                token=token, client_id=self.settings.app_id, scopes=["ads_read"],
                subject=profile["id"], claims={"sub": profile["id"], "name": profile.get("name", "")},
            )
        except ToolError:
            return None


def build_server(settings: Settings, transport=None):
    settings.validate()
    api = MetaAPI(settings, transport)
    extra_authorize = {"scope": "ads_read"}
    if settings.login_config_id:
        extra_authorize["config_id"] = settings.login_config_id
    auth = OAuthProxy(
        upstream_authorization_endpoint=f"https://www.facebook.com/{settings.graph_version}/dialog/oauth",
        upstream_token_endpoint=f"https://graph.facebook.com/{settings.graph_version}/oauth/access_token",
        upstream_client_id=settings.app_id, upstream_client_secret=settings.app_secret,
        token_verifier=MetaVerifier(settings, api), base_url=settings.base_url,
        redirect_path="/auth/callback", jwt_signing_key=settings.signing_key,
        token_endpoint_auth_method="client_secret_post",
        forward_pkce=True, forward_resource=False,
        valid_scopes=["ads_read"], extra_authorize_params=extra_authorize,
        require_authorization_consent=True,
        allowed_client_redirect_uris=["https://chatgpt.com/*"],
        fallback_access_token_expiry_seconds=3600,
    )
    mcp = FastMCP("華泰 Meta Ads", auth=auth)

    def token():
        access = get_access_token()
        if access is None or "ads_read" not in access.scopes:
            raise ToolError("Reconnect the Meta Ads account to continue.")
        return access.token

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    async def get_ad_account() -> dict:
        """Read the connected Huatai ad account's name, currency and status."""
        return await api.get(f"act_{settings.account_id}", token(), {
            "fields": "id,account_id,name,currency,timezone_name,account_status",
        })

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    async def list_campaigns(limit: int = 25, after: str | None = None) -> dict:
        """List campaigns in the connected Huatai account; does not change ads.

        Use next_cursor for the next page. limit must be between 1 and 100.
        """
        if not 1 <= limit <= 100 or (after and len(after) > 4096):
            raise ToolError("Invalid pagination parameters.")
        params = {"fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget", "limit": limit}
        if after:
            params["after"] = after
        payload = await api.get(f"act_{settings.account_id}/campaigns", token(), params)
        paging = payload.get("paging", {})
        return {"data": payload.get("data", []), "next_cursor":
                paging.get("cursors", {}).get("after") if paging.get("next") else None}

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    async def get_insights(
        date_preset: Literal["today", "yesterday", "last_7d", "last_30d", "this_month"] = "last_7d",
        level: Literal["account", "campaign"] = "account",
        after: str | None = None,
    ) -> dict:
        """Read Meta spend, impressions, reach, clicks and conversions for Huatai.

        Monetary values use the ad account's currency. Results are Meta attribution data.
        """
        if after and len(after) > 4096:
            raise ToolError("Invalid pagination parameters.")
        params = {
            "date_preset": date_preset, "level": level, "limit": 100,
            "fields": "account_id,campaign_id,campaign_name,spend,impressions,reach,clicks,cpc,cpm,ctr,actions,date_start,date_stop",
        }
        if after:
            params["after"] = after
        payload = await api.get(f"act_{settings.account_id}/insights", token(), params)
        paging = payload.get("paging", {})
        return {"data": payload.get("data", []), "next_cursor":
                paging.get("cursors", {}).get("after") if paging.get("next") else None}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return JSONResponse({"status": "ok", "service": "huatai-meta-ads"})

    return mcp


def main():
    # Keep provider request URLs and authorization data out of routine logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = build_server(Settings.from_env())
    server.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
               stateless_http=True)


if __name__ == "__main__":
    main()
