import threading
import unittest

from tistory_browser_guard import TistoryProfileInUse, acquire


class TistoryBrowserGuardTests(unittest.TestCase):
    def test_same_profile_rejected_while_different_profiles_are_independent(self):
        release_first = acquire("first-profile")
        release_second = None
        try:
            with self.assertRaisesRegex(TistoryProfileInUse, "다른 작업"):
                acquire("first-profile")
            release_second = acquire("second-profile")
        finally:
            release_first()
            if release_second:
                release_second()

    def test_delayed_close_callback_cannot_release_a_new_owner(self):
        old_release = acquire("reused-profile")
        old_release()
        new_release = acquire("reused-profile")
        try:
            old_release(object())  # Playwright close-event argument is accepted.
            old_release()
            with self.assertRaises(TistoryProfileInUse):
                acquire("reused-profile")
        finally:
            new_release()
        final_release = acquire("reused-profile")
        final_release()

    def test_concurrent_workers_allow_exactly_one_owner(self):
        count = 12
        barrier = threading.Barrier(count)
        releases = []
        outcomes = []
        result_lock = threading.Lock()

        def contend():
            barrier.wait(timeout=5)
            try:
                release = acquire("contended-profile")
                with result_lock:
                    releases.append(release)
                    outcomes.append("acquired")
            except TistoryProfileInUse:
                with result_lock:
                    outcomes.append("busy")

        workers = [threading.Thread(target=contend) for _ in range(count)]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=5)
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(outcomes.count("acquired"), 1)
            self.assertEqual(outcomes.count("busy"), count - 1)
        finally:
            for release in releases:
                release()

    def test_empty_profile_key_is_not_reserved(self):
        for key in (None, "", "  "):
            with self.assertRaises(ValueError):
                acquire(key)


if __name__ == "__main__":
    unittest.main()
