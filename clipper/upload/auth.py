"""YouTube OAuth2 authentication for Clipper."""

from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from clipper.config import get_project_root

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TOKEN_FILE = ".clipper_token.json"
CLIENT_SECRETS_FILE = "client_secrets.json"


def get_youtube_service():
    """Return an authenticated YouTube Data API v3 service.

    First run launches a browser-based OAuth2 flow.
    Subsequent runs load saved credentials, refreshing if expired.
    """
    root = get_project_root()
    token_path = root / TOKEN_FILE
    secrets_path = root / CLIENT_SECRETS_FILE

    creds = None

    # Load existing token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    # Refresh or run OAuth flow
    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request

        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not secrets_path.exists():
            raise FileNotFoundError(
                f"Missing {CLIENT_SECRETS_FILE} in {root}. "
                "Download it from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)

    # Save token for next run
    token_path.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds)
