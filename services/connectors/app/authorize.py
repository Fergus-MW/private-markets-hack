"""Run locally to consent once; upload the output to Secret Manager."""
import argparse
import os

SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "drive": "https://www.googleapis.com/auth/drive.readonly",
}


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=SCOPES)
    parser.add_argument("--client", required=True, help="Desktop OAuth client JSON")
    parser.add_argument("--output", required=True, help="New private credentials file")
    args = parser.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(args.client, [SCOPES[args.provider]])
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise RuntimeError("Google did not issue a refresh token; repeat consent")
    # Exclusive creation prevents overwriting existing credentials; never print tokens.
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
        output.write(credentials.to_json())


if __name__ == "__main__":
    main()
