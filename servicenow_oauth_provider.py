"""
ServiceNow OAuth proxy provider for the MCP authorization framework.

This module implements the MCP OAuthAuthorizationServerProvider interface
by proxying OAuth flows to ServiceNow. When a user connects via Claude,
the flow is:

1. Claude → MCP server: "Authorize this user"
2. MCP server → ServiceNow: Redirect user to ServiceNow OAuth (which goes to Microsoft Entra ID SSO)
3. User authenticates via Microsoft Entra ID SSO
4. ServiceNow → MCP server callback: Authorization code
5. MCP server exchanges code with ServiceNow for tokens
6. MCP server wraps ServiceNow tokens in MCP-compatible tokens for Claude

This gives per-user authentication: each employee's ServiceNow permissions
are inherited through their own OAuth token.
"""
import os
import secrets
import time
import logging
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs
from dataclasses import dataclass, field

import httpx
from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider,
    AuthorizationParams,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)


@dataclass
class StoredAuthCode:
    """An authorization code stored in memory, mapping to a ServiceNow auth code."""
    servicenow_code: str
    client_id: str
    redirect_uri: str
    code_verifier: str | None
    created_at: float = field(default_factory=time.time)
    expires_in: int = 300  # 5 minutes

    @property
    def is_expired(self) -> bool:
        return time.time() > (self.created_at + self.expires_in)


@dataclass
class StoredToken:
    """A token stored in memory, mapping an MCP token to a ServiceNow token."""
    servicenow_access_token: str
    servicenow_refresh_token: str | None
    client_id: str
    created_at: float = field(default_factory=time.time)
    expires_in: int = 1800  # 30 minutes

    @property
    def is_expired(self) -> bool:
        return time.time() > (self.created_at + self.expires_in)


class ServiceNowOAuthProvider(OAuthAuthorizationServerProvider):
    """MCP OAuth provider that proxies authentication to ServiceNow.

    This provider acts as an OAuth authorization server to Claude while
    delegating actual authentication to ServiceNow (which in turn delegates
    to Microsoft Entra ID SSO for enterprise users).

    Configuration via environment variables:
        SERVICENOW_INSTANCE: ServiceNow instance URL (e.g., https://company.service-now.com)
        SERVICENOW_OAUTH_CLIENT_ID: OAuth client ID registered in ServiceNow
        SERVICENOW_OAUTH_CLIENT_SECRET: OAuth client secret (for confidential proxy)
        MCP_SERVER_BASE_URL: The public URL of this MCP server (for callbacks)
    """

    def __init__(self) -> None:
        self.servicenow_instance = os.environ["SERVICENOW_INSTANCE"]
        self.sn_client_id = os.environ["SERVICENOW_OAUTH_CLIENT_ID"]
        self.sn_client_secret = os.environ.get("SERVICENOW_OAUTH_CLIENT_SECRET", "")
        self.server_base_url = os.environ.get("MCP_SERVER_BASE_URL", "http://localhost:8080")

        # ServiceNow OAuth endpoints
        self.sn_authorize_url = f"{self.servicenow_instance}/oauth_auth.do"
        self.sn_token_url = f"{self.servicenow_instance}/oauth_token.do"

        # In-memory stores (production: replace with Redis or similar)
        self._auth_codes: dict[str, StoredAuthCode] = {}
        self._access_tokens: dict[str, StoredToken] = {}
        self._refresh_tokens: dict[str, StoredToken] = {}
        self._clients: dict[str, OAuthClientInformationFull] = {}

        # Mapping: MCP auth code -> state for the SN callback
        self._pending_authorizations: dict[str, dict[str, Any]] = {}

    # ─── Client Registration (DCR) ───────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Look up a registered OAuth client by ID."""
        return self._clients.get(client_id)

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        """Register a new OAuth client (Claude uses DCR to register)."""
        self._clients[client_info.client_id] = client_info
        logger.info("Registered OAuth client: %s", client_info.client_id)

    # ─── Authorization ───────────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Start the authorization flow by redirecting to ServiceNow.

        Claude calls this to get a URL where the user can authenticate.
        We redirect them to ServiceNow's OAuth endpoint, which will in
        turn redirect to Microsoft Entra ID SSO.

        Returns:
            URL to redirect the user to for ServiceNow/Entra ID authentication.
        """
        # Generate a state token to correlate the callback
        state = secrets.token_urlsafe(32)

        # Store the pending authorization so we can match the callback
        self._pending_authorizations[state] = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "code_challenge_method": getattr(params, "code_challenge_method", "S256"),
            "scopes": params.scopes or [],
            "state": params.state,
        }

        # Build the ServiceNow OAuth URL
        # The MCP server itself acts as the "client" to ServiceNow
        sn_params = {
            "response_type": "code",
            "client_id": self.sn_client_id,
            "redirect_uri": f"{self.server_base_url}/oauth/servicenow/callback",
            "state": state,
        }

        authorize_url = f"{self.sn_authorize_url}?{urlencode(sn_params)}"
        logger.info("Redirecting user to ServiceNow OAuth: %s", authorize_url)
        return authorize_url

    async def handle_servicenow_callback(
        self, code: str, state: str
    ) -> str:
        """Handle the OAuth callback from ServiceNow.

        After the user authenticates via Microsoft Entra ID SSO, ServiceNow redirects
        back to our callback URL with an authorization code. We:
        1. Store the ServiceNow auth code
        2. Generate our own MCP auth code
        3. Redirect the user back to Claude's redirect_uri

        Returns:
            Redirect URL to send the user back to Claude.
        """
        pending = self._pending_authorizations.pop(state, None)
        if not pending:
            raise ValueError("Invalid or expired state parameter")

        # Generate our own authorization code for Claude
        mcp_code = secrets.token_urlsafe(48)

        # Store the mapping: MCP code -> ServiceNow code
        self._auth_codes[mcp_code] = StoredAuthCode(
            servicenow_code=code,
            client_id=pending["client_id"],
            redirect_uri=pending["redirect_uri"],
            code_verifier=None,  # We don't use PKCE with ServiceNow, only with Claude
        )

        # Build redirect URL back to Claude with our MCP auth code
        redirect_params = {
            "code": mcp_code,
        }
        if pending.get("state"):
            redirect_params["state"] = pending["state"]

        redirect_uri = pending["redirect_uri"]
        separator = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{separator}{urlencode(redirect_params)}"

    # ─── Token Exchange ──────────────────────────────────────────────────

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> OAuthToken:
        """Exchange an MCP authorization code for tokens.

        Claude sends us the MCP auth code. We:
        1. Look up the corresponding ServiceNow auth code
        2. Exchange it with ServiceNow for real tokens
        3. Wrap the ServiceNow tokens in MCP tokens
        """
        stored = self._auth_codes.pop(str(authorization_code), None)
        if not stored or stored.is_expired:
            raise ValueError("Invalid or expired authorization code")

        if stored.client_id != client.client_id:
            raise ValueError("Authorization code was not issued to this client")

        # Exchange with ServiceNow
        sn_tokens = await self._exchange_code_with_servicenow(
            stored.servicenow_code
        )

        # Create MCP tokens backed by ServiceNow tokens
        mcp_access_token = secrets.token_urlsafe(48)
        mcp_refresh_token = secrets.token_urlsafe(48)

        expires_in = sn_tokens.get("expires_in", 1800)

        token_entry = StoredToken(
            servicenow_access_token=sn_tokens["access_token"],
            servicenow_refresh_token=sn_tokens.get("refresh_token"),
            client_id=client.client_id,
            expires_in=expires_in,
        )

        self._access_tokens[mcp_access_token] = token_entry
        if sn_tokens.get("refresh_token"):
            self._refresh_tokens[mcp_refresh_token] = token_entry

        return OAuthToken(
            access_token=mcp_access_token,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=mcp_refresh_token if sn_tokens.get("refresh_token") else None,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange a refresh token for new tokens.

        Claude sends us the MCP refresh token. We:
        1. Look up the corresponding ServiceNow refresh token
        2. Exchange it with ServiceNow for new tokens
        3. Return new MCP tokens
        """
        stored = self._refresh_tokens.pop(str(refresh_token), None)
        if not stored:
            raise ValueError("Invalid refresh token")

        if stored.client_id != client.client_id:
            raise ValueError("Refresh token was not issued to this client")

        if not stored.servicenow_refresh_token:
            raise ValueError("No ServiceNow refresh token available")

        # Refresh with ServiceNow
        sn_tokens = await self._refresh_token_with_servicenow(
            stored.servicenow_refresh_token
        )

        # Create new MCP tokens
        new_access_token = secrets.token_urlsafe(48)
        new_refresh_token = secrets.token_urlsafe(48)

        expires_in = sn_tokens.get("expires_in", 1800)

        new_entry = StoredToken(
            servicenow_access_token=sn_tokens["access_token"],
            servicenow_refresh_token=sn_tokens.get("refresh_token"),
            client_id=client.client_id,
            expires_in=expires_in,
        )

        self._access_tokens[new_access_token] = new_entry
        if sn_tokens.get("refresh_token"):
            self._refresh_tokens[new_refresh_token] = new_entry

        return OAuthToken(
            access_token=new_access_token,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=new_refresh_token if sn_tokens.get("refresh_token") else None,
        )

    # ─── Token Validation ────────────────────────────────────────────────

    async def load_access_token(self, token: str) -> StoredToken | None:
        """Validate and load an access token.

        Called on every MCP tool invocation to verify the user's token
        is still valid.
        """
        stored = self._access_tokens.get(token)
        if not stored:
            return None
        if stored.is_expired:
            self._access_tokens.pop(token, None)
            return None
        return stored

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> StoredAuthCode | None:
        """Load a stored authorization code."""
        stored = self._auth_codes.get(authorization_code)
        if not stored:
            return None
        if stored.is_expired:
            self._auth_codes.pop(authorization_code, None)
            return None
        if stored.client_id != client.client_id:
            return None
        return stored

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> StoredToken | None:
        """Load a stored refresh token."""
        stored = self._refresh_tokens.get(refresh_token)
        if not stored:
            return None
        if stored.client_id != client.client_id:
            return None
        return stored

    async def revoke_token(self, token: StoredToken | StoredAuthCode) -> None:
        """Revoke a token (access or refresh)."""
        # Remove from all stores
        keys_to_remove = []
        for key, val in self._access_tokens.items():
            if val is token:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            self._access_tokens.pop(key, None)

        keys_to_remove = []
        for key, val in self._refresh_tokens.items():
            if val is token:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            self._refresh_tokens.pop(key, None)

        logger.info("Revoked token for client: %s", getattr(token, "client_id", "unknown"))

    # ─── ServiceNow API Calls ────────────────────────────────────────────

    async def _exchange_code_with_servicenow(self, code: str) -> dict[str, Any]:
        """Exchange an authorization code with ServiceNow for tokens."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{self.server_base_url}/oauth/servicenow/callback",
            "client_id": self.sn_client_id,
            "client_secret": self.sn_client_secret,
        }

        async with httpx.AsyncClient(verify=True) as client:
            response = await client.post(
                self.sn_token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def _refresh_token_with_servicenow(self, refresh_token: str) -> dict[str, Any]:
        """Refresh tokens with ServiceNow."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.sn_client_id,
            "client_secret": self.sn_client_secret,
        }

        async with httpx.AsyncClient(verify=True) as client:
            response = await client.post(
                self.sn_token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    # ─── Helpers ─────────────────────────────────────────────────────────

    def get_servicenow_token_for_mcp_token(self, mcp_token: str) -> str | None:
        """Get the ServiceNow access token backing an MCP token.

        Called by the tool execution layer to make authenticated
        ServiceNow API calls on behalf of the user.
        """
        stored = self._access_tokens.get(mcp_token)
        if not stored or stored.is_expired:
            return None
        return stored.servicenow_access_token

    def cleanup_expired(self) -> int:
        """Remove expired tokens and codes from all stores.

        Returns the number of items cleaned up. Should be called
        periodically (e.g., every 5 minutes).
        """
        count = 0

        for store in (self._auth_codes, self._access_tokens, self._refresh_tokens):
            expired_keys = [
                k for k, v in store.items()
                if hasattr(v, "is_expired") and v.is_expired
            ]
            for key in expired_keys:
                store.pop(key, None)
                count += 1

        expired_pending = [
            k for k, v in self._pending_authorizations.items()
            # Pending authorizations don't have expiry — clean up after 10 min
        ]
        # We'll add timestamp tracking for pending auths in production

        return count
