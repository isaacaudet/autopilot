"""YouTube OAuth2 authentication for Clipper."""

from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from clipper.config import get_project_root

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
