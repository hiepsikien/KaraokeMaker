import unittest
from unittest import mock

from karaoke import server


def job_record(**overrides):
    record = {
        "id": "job-start-time",
        "status": "editing",
        "created": 100.0,
        "started": 100.0,
        "log": [],
        "error": "",
        "outputs": None,
        "lines": [],
        "duration": 0.0,
        "prepared": {
            "vocal_audio": "/tmp/vocal.wav",
            "project_path": "/tmp/project.karaoke.json",
        },
        "project": None,
        "background": {},
    }
    record.update(overrides)
    return record


class JobStartTimeTests(unittest.TestCase):
    def test_public_job_falls_back_to_created(self):
        public = server._public_job(job_record(started=None))
        self.assertEqual(public["started"], 100.0)

    def test_public_job_exposes_started(self):
        public = server._public_job(job_record(started=250.0))
        self.assertEqual(public["started"], 250.0)

    def test_realign_records_a_new_started_time(self):
        record = job_record(started=100.0)
        with server._lock:
            server._jobs[record["id"]] = record
        try:
            with mock.patch.object(server.threading, "Thread") as thread_class, mock.patch.object(
                server.time, "time", return_value=999.0
            ):
                server.realign_job(
                    record["id"],
                    server.AlignRequest(model_name="small", device="cpu"),
                )
                # The worker must not run inside this test.
                thread_class.return_value.start.assert_called_once()
            self.assertEqual(record["started"], 999.0)
            self.assertEqual(server._public_job(record)["started"], 999.0)
        finally:
            with server._lock:
                server._jobs.pop(record["id"], None)


if __name__ == "__main__":
    unittest.main()
