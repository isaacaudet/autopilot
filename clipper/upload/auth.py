"""OAuth2 authentication for Clipper (YouTube, TikTok, Meta/Instagram/Facebook)."""

import json
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from clipper.config import get_project_root, require_env

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

TOKEN_FILE = ".clipper_token.json"
CLIENT_SECRETS_FILE = "client_secrets.json"


def _load_or_refresh_creds(token_path: Path, secrets_path: Path, *, interactive: bool = True) -> Credentials:
    """Load OAuth credentials from file, refresh if expired, or run OAuth flow if missing.

    When interactive=False, raises RuntimeError instead of launching a browser
    OAuth flow (safe for server contexts where blocking would hang).
    """
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not interactive:
            raise RuntimeError(
                f"YouTube OAuth token is missing or expired ({token_path.name}). "
                "Run `clipper auth` from the terminal to re-authenticate."
            )
        if not secrets_path.exists():
            raise FileNotFoundError(
                f"Missing {secrets_path.name} in {secrets_path.parent}. "
                "Download it from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    return creds


def get_youtube_service(*, interactive: bool = False):
    """Return an authenticated YouTube Data API v3 service.

    When interactive=True (CLI), may launch a browser-based OAuth2 flow.
    When interactive=False (server), raises RuntimeError if token is missing.
    """
    root = get_project_root()
    creds = _load_or_refresh_creds(root / TOKEN_FILE, root / CLIENT_SECRETS_FILE, interactive=interactive)
    return build("youtube", "v3", credentials=creds)


def get_youtube_service_for_channel(channel_name: str, config: dict, *, interactive: bool = False):
    """Return an authenticated YouTube service using a channel-specific token file."""
    channels = config.get("channels", {})
    ch_config = channels.get(channel_name, {})
    token_file = ch_config.get("token_file", TOKEN_FILE)

    root = get_project_root()
    creds = _load_or_refresh_creds(root / token_file, root / CLIENT_SECRETS_FILE, interactive=interactive)
    return build("youtube", "v3", credentials=creds)


def setup_channel_auth(channel_name: str, config: dict):
    """Run OAuth flow for a specific channel and save its token file."""
    from rich.console import Console
    console = Console()

    channels = config.get("channels", {})
    ch_config = channels.get(channel_name, {})
    token_file = ch_config.get("token_file", f".clipper_token_{channel_name}.json")

    root = get_project_root()
    secrets_path = root / CLIENT_SECRETS_FILE
    token_path = root / token_file

    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Missing {CLIENT_SECRETS_FILE} in {root}. "
            "Download it from Google Cloud Console."
        )

    console.print(f"[bold]Setting up OAuth for channel '{channel_name}'[/bold]")
    console.print(f"Token will be saved to: {token_path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())

    console.print(f"[green]Auth complete![/green] Token saved to {token_path}")


def get_youtube_analytics_service(*, interactive: bool = False):
    """Return an authenticated YouTube Analytics API v2 service.

    Reuses the same OAuth credentials as get_youtube_service().
    Requires yt-analytics.readonly scope (added to SCOPES).
    """
    root = get_project_root()
    creds = _load_or_refresh_creds(root / TOKEN_FILE, root / CLIENT_SECRETS_FILE, interactive=interactive)
    return build("youtubeAnalytics", "v2", credentials=creds)


def get_youtube_analytics_service_for_channel(channel_name: str, config: dict, *, interactive: bool = False):
    """Return an authenticated YouTube Analytics API v2 service for a specific channel token."""
    channels = config.get("channels", {})
    ch_config = channels.get(channel_name, {})
    token_file = ch_config.get("token_file", TOKEN_FILE)

    root = get_project_root()
    creds = _load_or_refresh_creds(root / token_file, root / CLIENT_SECRETS_FILE, interactive=interactive)
    return build("youtubeAnalytics", "v2", credentials=creds)


# ---------------------------------------------------------------------------
#  Local callback server for OAuth redirects
# ---------------------------------------------------------------------------

_REDIRECT_PORT = 8421


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Captures the OAuth redirect code from the browser."""

    auth_code: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]

        if code:
            _OAuthCallbackHandler.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Auth complete! You can close this tab.</h2>")
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>Auth failed: {error}</h2>".encode())

    def log_message(self, format, *args):
        pass  # silence HTTP logs


def _wait_for_auth_code() -> str:
    """Start a local HTTP server and wait for the OAuth redirect."""
    _OAuthCallbackHandler.auth_code = None
    server = HTTPServer(("localhost", _REDIRECT_PORT), _OAuthCallbackHandler)
    server.timeout = 120
    while _OAuthCallbackHandler.auth_code is None:
        server.handle_request()
    server.server_close()
    code = _OAuthCallbackHandler.auth_code
    if not code:
        raise RuntimeError("No auth code received")
    return code


# ---------------------------------------------------------------------------
#  TikTok Auth
# ---------------------------------------------------------------------------


def setup_tiktok_auth(channel_name: str, config: dict):
    """Run TikTok OAuth flow — opens browser, saves token file."""
    from rich.console import Console
    console = Console()

    channels = config.get("channels", {})
    ch_config = channels.get(channel_name, {})
    token_file = ch_config.get("token_file", f".clipper_tiktok_{channel_name}.json")

    root = get_project_root()
    token_path = root / token_file
    client_key = require_env("TIKTOK_CLIENT_KEY")
    client_secret = require_env("TIKTOK_CLIENT_SECRET")

    redirect_uri = f"http://localhost:{_REDIRECT_PORT}/"
    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": client_key,
        "response_type": "code",
        "scope": "video.publish",
        "redirect_uri": redirect_uri,
    })

    console.print(f"[bold]Setting up TikTok auth for channel '{channel_name}'[/bold]")
    console.print(f"Opening browser for authorization...")
    webbrowser.open(auth_url)

    code = _wait_for_auth_code()

    # Exchange code for tokens
    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        json={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    token_data = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + data.get("expires_in", 86400),
        "open_id": data.get("open_id", ""),
    }
    token_path.write_text(json.dumps(token_data, indent=2))
    console.print(f"[green]TikTok auth complete![/green] Token saved to {token_path}")


# ---------------------------------------------------------------------------
#  Meta (Instagram / Facebook) Auth
# ---------------------------------------------------------------------------

_META_SCOPES = {
    "instagram": "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement",
    "facebook": "pages_manage_posts,publish_video,pages_show_list",
}


def setup_meta_auth(channel_name: str, config: dict, platform: str = "instagram"):
    """Run Facebook/Instagram OAuth flow — opens browser, saves token file."""
    from rich.console import Console
    console = Console()

    channels = config.get("channels", {})
    ch_config = channels.get(channel_name, {})
    token_file = ch_config.get("token_file", f".clipper_{platform}_{channel_name}.json")

    root = get_project_root()
    token_path = root / token_file
    app_id = require_env("META_APP_ID")
    app_secret = require_env("META_APP_SECRET")

    redirect_uri = f"http://localhost:{_REDIRECT_PORT}/"
    scopes = _META_SCOPES.get(platform, _META_SCOPES["instagram"])

    auth_url = f"https://www.facebook.com/v21.0/dialog/oauth?" + urlencode({
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "response_type": "code",
    })

    console.print(f"[bold]Setting up {platform.title()} auth for channel '{channel_name}'[/bold]")
    console.print("Opening browser for authorization...")
    webbrowser.open(auth_url)

    code = _wait_for_auth_code()

    # Exchange code for short-lived token
    resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=15,
    )
    resp.raise_for_status()
    short_token = resp.json()["access_token"]

    # Exchange short-lived for long-lived token (60 days)
    resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    long_data = resp.json()
    long_token = long_data["access_token"]
    expires_at = time.time() + long_data.get("expires_in", 5184000)

    token_data = {
        "access_token": long_token,
        "expires_at": expires_at,
        "platform": platform,
    }

    # Fetch Pages
    pages_resp = requests.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={"access_token": long_token},
        timeout=15,
    )
    pages_resp.raise_for_status()
    pages = pages_resp.json().get("data", [])

    if not pages:
        raise RuntimeError("No Facebook Pages found. Link a Page to your account first.")

    page = pages[0]  # Use first page
    console.print(f"  Using page: {page.get('name')} (id: {page['id']})")
    token_data["page_id"] = page["id"]
    token_data["page_access_token"] = page["access_token"]
    token_data["page_name"] = page.get("name", "")

    if platform == "instagram":
        # Find linked Instagram Business Account
        ig_resp = requests.get(
            f"https://graph.facebook.com/v21.0/{page['id']}",
            params={"fields": "instagram_business_account", "access_token": page["access_token"]},
            timeout=15,
        )
        ig_resp.raise_for_status()
        ig_account = ig_resp.json().get("instagram_business_account")

        if not ig_account:
            raise RuntimeError(
                f"Page '{page.get('name')}' has no linked Instagram Business Account. "
                "Link an IG Business/Creator account to the Facebook Page first."
            )

        token_data["ig_user_id"] = ig_account["id"]
        console.print(f"  Instagram Business Account ID: {ig_account['id']}")

    token_path.write_text(json.dumps(token_data, indent=2))
    console.print(f"[green]{platform.title()} auth complete![/green] Token saved to {token_path}")
