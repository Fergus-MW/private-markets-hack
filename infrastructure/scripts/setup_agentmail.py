"""Create/reuse the agent inbox and webhook; store secrets without printing them.

Run with services/mail_agent/requirements.txt installed and gcloud authenticated.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
from urllib.parse import urlparse

from agentmail import AgentMail
from agentmail.inboxes.types.create_inbox_request import CreateInboxRequest


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ")
        if name == "AGENTMAIL_API_KEY":
            parts = shlex.split(value, comments=True)
            if len(parts) != 1:
                raise ValueError("Invalid AgentMail key entry")
            os.environ.setdefault(name, parts[0])


def secret(project, name, value=None):
    command = ["gcloud", "secrets", "versions"]
    command += ["access", "latest", "--secret=" + name] if value is None else ["add", name, "--data-file=-"]
    result = subprocess.run(command + ["--project=" + project, "--quiet"], input=value,
                            text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("Secret Manager operation failed for " + name)
    return result.stdout.strip()


def pages(list_method, attribute):
    token = None
    while True:
        page = list_method(limit=100, page_token=token)
        yield from getattr(page, attribute)
        token = page.next_page_token
        if not token:
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--inbox-id", help="Reuse an existing inbox, including inbox-scoped API keys")
    parser.add_argument("--output", type=Path, default=Path("infrastructure/agentmail.auto.tfvars.json"))
    args = parser.parse_args()
    origin = urlparse(args.origin)
    if origin.scheme != "https" or origin.path not in {"", "/"} or origin.query or origin.fragment:
        raise ValueError("Expected the public frontend HTTPS origin")
    load_env(args.env_file)
    api_key = os.environ.get("AGENTMAIL_API_KEY") or secret(args.project, "agentmail-api-key")
    client = AgentMail(api_key=api_key)
    inbox_key = args.project + "-email-agent-v1"
    inbox = client.inboxes.get(args.inbox_id) if args.inbox_id else next((i for i in pages(client.inboxes.list, "inboxes") if i.client_id == inbox_key), None)
    if inbox is None:
        inbox = client.inboxes.create(request=CreateInboxRequest(display_name="Private Markets Agent", client_id=inbox_key))
    url = args.origin.rstrip("/") + "/api/agentmail/webhook"
    webhook_key = args.project + "-email-agent-webhook-v1"
    list_webhooks = lambda **kwargs: client.inboxes.webhooks.list(inbox.inbox_id, **kwargs)
    webhook = next((w for w in pages(list_webhooks, "webhooks") if w.client_id == webhook_key), None)
    if webhook is None:
        webhook = client.inboxes.webhooks.create(inbox.inbox_id, url=url, event_types=["message.received"], client_id=webhook_key)
    elif webhook.url != url:
        raise ValueError("Existing webhook has a different URL; explicitly migrate it before rerunning setup")
    webhook = client.inboxes.webhooks.get(inbox.inbox_id, webhook.webhook_id)
    if webhook.inbox_ids != [inbox.inbox_id] or webhook.event_types != ["message.received"]:
        raise ValueError("Existing webhook subscription differs from the expected single inbox/received event")
    secret(args.project, "agentmail-api-key", api_key)
    secret(args.project, "agentmail-webhook-secret", webhook.secret)
    configuration = json.loads(args.output.read_text()) if args.output.exists() else {}
    configuration.update(mail_enabled=True, agentmail_inbox_id=inbox.inbox_id)
    args.output.write_text(json.dumps(configuration, indent=2) + "\n")
    print(json.dumps({"inbox_id": inbox.inbox_id, "webhook_id": webhook.webhook_id, "webhook_url": url,
                      "terraform_configuration": str(args.output), "secrets": "stored in Secret Manager"}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SDK/HTTP exceptions can include request headers. Never print them.
        raise SystemExit("AgentMail setup failed (" + type(error).__name__ + "). Check configuration, credentials and service access.") from None
