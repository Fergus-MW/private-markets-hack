"""Release-script checks run locally with no production access."""
import unittest
from test_frontend_release import run_release

SERVICES = [('cloudbuild.yaml', 'document-ingestion', 'ingestion'),
            ('cloudbuild-mail.yaml', 'agent-mail', 'agent-mail'),
            ('cloudbuild-model-gateway.yaml', 'model-gateway', 'model-gateway')]


class ServiceReleaseTests(unittest.TestCase):
    def test_services_deploy_the_built_digest_and_propagate_failure(self):
        for config, service, image in SERVICES:
            with self.subTest(service=service):
                result, calls = run_release(config_name=config)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 2)
                self.assertIn('run services update ' + service, calls[1])
                self.assertIn('/services/' + image + '@sha256:tested-image', calls[1])
                result, calls = run_release(config_name=config, fail_update=True)
                self.assertNotEqual(result.returncode, 0)

    def test_build_only_is_explicit_and_invalid_flags_fail(self):
        for config in [row[0] for row in SERVICES] + ['cloudbuild-connectors.yaml']:
            for deploy in ('false', '', 'yes'):
                with self.subTest(config=config, deploy=deploy):
                    result, calls = run_release(config_name=config, deploy=deploy)
                    self.assertEqual(result.returncode == 0, deploy == 'false')
                    self.assertEqual(calls, [])
                    if deploy == 'false':
                        self.assertIn('Build-only', result.stdout)

    def test_no_service_updates_when_digest_is_missing(self):
        for config in [row[0] for row in SERVICES] + ['cloudbuild-connectors.yaml']:
            with self.subTest(config=config):
                result, calls = run_release(config_name=config, digest='', jobs='connector-drive')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)

    def test_connectors_update_every_target_without_executing_jobs(self):
        result, calls = run_release(config_name='cloudbuild-connectors.yaml', jobs='connector-drive connector-gmail')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)
        for call, job in zip(calls[1:], ('connector-drive', 'connector-gmail')):
            self.assertIn('run jobs update ' + job, call)
            self.assertIn('/services/connectors@sha256:tested-image', call)
            self.assertNotIn('execute', call)

    def test_connectors_reject_empty_and_invalid_targets_before_any_updates(self):
        for jobs in ('', '   ', 'frontend', 'connector-drive --help', 'connector-*'):
            with self.subTest(jobs=jobs):
                result, calls = run_release(config_name='cloudbuild-connectors.yaml', jobs=jobs)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_connector_update_failure_stops_release(self):
        result, calls = run_release(config_name='cloudbuild-connectors.yaml',
                                   jobs='connector-drive connector-gmail', fail_update=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)
