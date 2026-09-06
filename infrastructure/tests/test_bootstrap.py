import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path=Path(__file__).parents[1]/'scripts'/'run_project_bootstrap.py'
spec=importlib.util.spec_from_file_location('bootstrap_runner',path)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class BootstrapTests(unittest.TestCase):
    def test_command_uses_iap_and_only_non_secret_versions(self):
        env={'WORKFLOW_PROJECT':'demo-project','WORKFLOW_ZONE':'europe-west2-a','WORKFLOW_VM':'surrealdb',
             'WORKFLOW_ROOT_VERSION':'1','WORKFLOW_PROVISIONER_VERSION':'2'}
        command=runner.command(env)
        self.assertIn('--tunnel-through-iap',command)
        self.assertIn('--command=python3 - --project demo-project --root-version 1 --provisioner-version 2',command)
        with patch.dict('os.environ',env),patch.object(runner.subprocess,'run') as run:
            runner.main()
        self.assertEqual(run.call_args.args[0],command)
        self.assertIn(b'DEFINE NAMESPACE IF NOT EXISTS projects',run.call_args.kwargs['input'])
        self.assertTrue(run.call_args.kwargs['check'])
