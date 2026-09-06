"""Exercise the actual release shell with a fake CLI; never contact production."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]


def run_release(services='frontend', digest='sha256:tested-image', config_name='cloudbuild-frontend.yaml',
                deploy='true', jobs='', fail_update=False):
    config = (ROOT / config_name).read_text()
    script = config.split('  - id: deploy\n', 1)[1].split('      - |\n', 1)[1].split('\nsubstitutions:', 1)[0]
    script = textwrap.dedent(script)
    for key, value in {'${_SERVICES}': services, '${_DEPLOY}': deploy, '${_JOBS}': jobs, '${_REGION}': 'test-region',
                       '$PROJECT_ID': 'test-project', '$BUILD_ID': 'test-build'}.items():
        script = script.replace(key, value)
    script = script.replace('$$', '$')  # Cloud Build unescapes shell variables.
    with tempfile.TemporaryDirectory() as folder:
        cli = Path(folder) / 'gcloud'
        log = Path(folder) / 'calls'
        cli.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RELEASE_TEST_LOG"\n'
                       'if [ "$1" = artifacts ]; then printf "%s\\n" "$RELEASE_TEST_DIGEST"; fi\n'
                       'if [ "$1" = run ] && [ "$RELEASE_TEST_FAIL_UPDATE" = true ]; then exit 1; fi\n')
        cli.chmod(0o755)
        result = subprocess.run(['bash', '-ceu', script], capture_output=True, text=True,
            env={**os.environ, 'PATH': folder + os.pathsep + os.environ['PATH'],
                 'RELEASE_TEST_LOG': str(log), 'RELEASE_TEST_DIGEST': digest,
                 'RELEASE_TEST_FAIL_UPDATE': str(fail_update).lower()})
        return result, log.read_text().splitlines() if log.exists() else []


class FrontendReleaseTests(unittest.TestCase):
    def test_empty_or_unexpected_targets_fail_without_a_release(self):
        for target in ('', 'another-service', 'frontend another-service'):
            with self.subTest(target=target):
                result, calls = run_release(target)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_release_updates_frontend_with_resolved_immutable_digest(self):
        result, calls = run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('frontend:test-build', calls[0])
        self.assertIn('run services update frontend', calls[1])
        self.assertIn('--image=test-region-docker.pkg.dev/test-project/services/frontend@sha256:tested-image', calls[1])

    def test_missing_digest_fails_before_updating_service(self):
        result, calls = run_release(digest='')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
