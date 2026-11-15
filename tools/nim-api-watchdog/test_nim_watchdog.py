import contextlib
import io
import json
import os
import runpy
import tempfile
import unittest
from unittest import mock


class WatchdogConfigTests(unittest.TestCase):
    def test_environment_paths_and_timeout_are_used(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = os.path.join(directory, "state.json")
            task_file = os.path.join(directory, "task.txt")
            with open(task_file, "w") as task:
                task.write("resume deployment")

            response = mock.MagicMock()
            response.__enter__.return_value = response
            output = io.StringIO()
            environment = {
                "NVIDIA_API_KEY": "test-key",
                "STATE_FILE": state_file,
                "TASK_FILE": task_file,
                "TIMEOUT": "7",
                "HOME": directory,
            }
            with mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch("urllib.request.urlopen", return_value=response) as urlopen, \
                    contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as exit_error:
                    runpy.run_path(os.path.join(os.path.dirname(__file__), "nim_watchdog.py"))

            self.assertEqual(exit_error.exception.code, 0)
            with open(state_file) as state:
                self.assertEqual(json.load(state), {"last_status": "up"})
            self.assertEqual(output.getvalue(), "NIM_API_RECOVERED|HAS_TASK|resume deployment\n")
            self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
