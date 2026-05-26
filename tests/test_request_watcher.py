import unittest
from unittest.mock import patch

from request_watcher import process_next_request


class RequestWatcherTests(unittest.TestCase):
    def test_process_next_request_scores_pending_request_and_marks_done(self):
        writes = []
        requests = [
            {
                "request_id": "req1",
                "status": "pending",
                "token_ticker": "NEX",
                "project_name": "Nexus",
                "x_handle": "NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
            }
        ]

        def fake_write(rows, message, sha=None):
            writes.append(([dict(row) for row in rows], message, sha))

        with patch("request_watcher.read_github_requests_with_sha", return_value=(requests, "sha")), patch(
            "request_watcher.write_github_requests",
            side_effect=fake_write,
        ), patch(
            "request_watcher.score_payload",
            return_value={"assessment": {"token_ticker": "NEX", "total_score": 48.02}},
        ) as mocked_score:
            processed = process_next_request()

        self.assertTrue(processed)
        mocked_score.assert_called_once_with(
            {
                "token_ticker": "NEX",
                "project_name": "Nexus",
                "x_handle": "NexusLabs",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Nexus?k=MTE3NDI%3D",
            }
        )
        self.assertEqual(writes[0][0][0]["status"], "processing")
        self.assertEqual(writes[-1][0][0]["status"], "done")
        self.assertEqual(writes[-1][0][0]["token_ticker"], "NEX")
        self.assertEqual(writes[-1][0][0]["project_name"], "Nexus")
        self.assertEqual(writes[-1][0][0]["total_score"], 48.02)

    def test_process_next_request_preserves_manual_identity_over_assessment(self):
        writes = []
        requests = [
            {
                "request_id": "req1",
                "status": "pending",
                "token_ticker": "CTR",
                "project_name": "Citrea",
                "x_handle": "citrea_xyz",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Citrea?k=MTEyNTk%3D",
            }
        ]

        with patch("request_watcher.read_github_requests_with_sha", return_value=(requests, "sha")), patch(
            "request_watcher.write_github_requests",
            side_effect=lambda rows, message, sha=None: writes.append(([dict(row) for row in rows], message, sha)),
        ), patch(
            "request_watcher.score_payload",
            return_value={"assessment": {"token_ticker": "", "project_name": "Citrea", "total_score": 35.88}},
        ):
            processed = process_next_request()

        self.assertTrue(processed)
        self.assertEqual(writes[-1][0][0]["token_ticker"], "CTR")
        self.assertEqual(writes[-1][0][0]["project_name"], "Citrea")

    def test_process_next_request_marks_failed_on_error(self):
        writes = []
        requests = [
            {
                "request_id": "req1",
                "status": "pending",
                "x_handle": "Bad",
                "rootdata_url": "https://cn.rootdata.com/projects/detail/Bad?k=MQ%3D%3D",
            }
        ]

        with patch("request_watcher.read_github_requests_with_sha", return_value=(requests, "sha")), patch(
            "request_watcher.write_github_requests",
            side_effect=lambda rows, message, sha=None: writes.append(([dict(row) for row in rows], message, sha)),
        ), patch("request_watcher.score_payload", side_effect=RuntimeError("RootData failed")):
            processed = process_next_request()

        self.assertTrue(processed)
        self.assertEqual(writes[-1][0][0]["status"], "failed")
        self.assertIn("RootData failed", writes[-1][0][0]["error"])


if __name__ == "__main__":
    unittest.main()
