"""Send only the checked-in migration to the VM through IAP; no local secrets."""
import os
from pathlib import Path
import shlex
import subprocess


def command(env):
    remote = shlex.join(['python3', '-', '--project', env['WORKFLOW_PROJECT'],
                         '--root-version', env['WORKFLOW_ROOT_VERSION'],
                         '--provisioner-version', env['WORKFLOW_PROVISIONER_VERSION']])
    return ['gcloud', 'compute', 'ssh', env['WORKFLOW_VM'],
            '--project=' + env['WORKFLOW_PROJECT'], '--zone=' + env['WORKFLOW_ZONE'],
            '--tunnel-through-iap', '--quiet', '--command=' + remote, '--', '-T']


def main():
    script = Path(__file__).with_name('bootstrap_project_namespace.py').read_bytes()
    subprocess.run(command(os.environ), input=script, check=True, timeout=600)


if __name__ == '__main__':
    main()
