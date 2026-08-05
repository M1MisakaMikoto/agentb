import copy
import os
import unittest
from unittest.mock import patch

from service.settings_service.settings_service import (
    DEFAULT_SETTINGS,
    _apply_env_overrides,
)


class WorkspaceConfigurationTests(unittest.TestCase):
    def test_workspace_directory_can_be_overridden_by_environment(self):
        with patch.dict(
            os.environ,
            {"AGENTB_WORKSPACE_DIR": "/app/workspaces"},
            clear=False,
        ):
            settings = _apply_env_overrides(copy.deepcopy(DEFAULT_SETTINGS))

        self.assertEqual(settings["workspace"]["base_dir"], "/app/workspaces")


if __name__ == "__main__":
    unittest.main()
