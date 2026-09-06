"""Terraform project namespace migration; runs on the VM using its identity."""
import argparse
import base64
import json
import urllib.request
import urllib.error
import time


def fetch(request, timeout=30):
    # Fresh VMs and newly granted secret access can take a little time to settle.
    for attempt in range(24):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code not in {403, 404, 429, 500, 502, 503, 504} or attempt == 23:
                raise
        except urllib.error.URLError:
            if attempt == 23:
                raise
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--root-version', required=True)
    parser.add_argument('--provisioner-version', required=True)
    args = parser.parse_args()
    request = urllib.request.Request(
        'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
        headers={'Metadata-Flavor': 'Google'})
    with fetch(request, timeout=30) as response:
        token = json.load(response)['access_token']

    def secret(name, version):
        request = urllib.request.Request(
            'https://secretmanager.googleapis.com/v1/projects/' + args.project + '/secrets/' + name + '/versions/' + version + ':access',
            headers={'Authorization': 'Bearer ' + token})
        with fetch(request, timeout=30) as response:
            return base64.b64decode(json.load(response)['payload']['data']).decode()

    root = secret('surrealdb-root-password', args.root_version)
    password = secret('surrealdb-project-provisioner-password', args.provisioner_version)
    if not (password.isascii() and password.isalnum() and len(password) >= 16):
        raise ValueError('Invalid provisioner credential format')
    query = "DEFINE NAMESPACE IF NOT EXISTS projects; USE NS projects; "
    query += "DEFINE USER OVERWRITE workflow_provisioner ON NAMESPACE PASSWORD '" + password + "' ROLES OWNER;"
    request = urllib.request.Request('http://127.0.0.1:8000/sql', data=query.encode(), headers={
        'Authorization': 'Basic ' + base64.b64encode(('root:' + root).encode()).decode(),
        'Accept': 'application/json'})
    with fetch(request, timeout=60) as response:
        results = json.load(response)
    if not results or any(row['status'] != 'OK' for row in results):
        raise RuntimeError('Project namespace bootstrap failed')
    print('Project namespace provisioner ready; canonical database unchanged.')


if __name__ == '__main__':
    main()
