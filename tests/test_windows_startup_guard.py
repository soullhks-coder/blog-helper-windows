import unittest

import startup_guard


class _FakeFunction:
    def __init__(self, return_value=None) -> None:
        self.return_value = return_value
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.return_value


class _FakeKernel32:
    def __init__(self, *, handle=1234, last_error=0) -> None:
        self.CreateMutexW = _FakeFunction(handle)
        self.GetLastError = _FakeFunction(last_error)
        self.CloseHandle = _FakeFunction(True)


class _FakeUser32:
    def __init__(self) -> None:
        self.MessageBoxW = _FakeFunction(1)


class WindowsStartupGuardTests(unittest.TestCase):
    def test_guard_is_disabled_outside_frozen_windows_app(self) -> None:
        kernel32 = _FakeKernel32()

        self.assertIsNone(
            startup_guard.acquire_windows_single_instance(
                os_name="posix",
                frozen=True,
                kernel32=kernel32,
            )
        )
        self.assertEqual(kernel32.CreateMutexW.calls, [])

    def test_first_windows_instance_keeps_mutex_handle(self) -> None:
        kernel32 = _FakeKernel32(handle=9876, last_error=0)

        handle = startup_guard.acquire_windows_single_instance(
            os_name="nt",
            frozen=True,
            executable_path="C:/BlogHelper/BlogHelper.exe",
            kernel32=kernel32,
            user32=_FakeUser32(),
        )

        self.assertEqual(handle, 9876)
        self.assertEqual(len(kernel32.CreateMutexW.calls), 1)
        self.assertEqual(kernel32.CloseHandle.calls, [])

    def test_second_windows_instance_shows_message_and_exits(self) -> None:
        kernel32 = _FakeKernel32(
            handle=4321,
            last_error=startup_guard.ERROR_ALREADY_EXISTS,
        )
        user32 = _FakeUser32()

        with self.assertRaisesRegex(SystemExit, "0"):
            startup_guard.acquire_windows_single_instance(
                os_name="nt",
                frozen=True,
                executable_path="C:/BlogHelper/BlogHelper.exe",
                kernel32=kernel32,
                user32=user32,
            )

        self.assertEqual(kernel32.CloseHandle.calls, [(4321,)])
        self.assertEqual(len(user32.MessageBoxW.calls), 1)
        self.assertIn("이미 실행 중입니다", user32.MessageBoxW.calls[0][1])

    def test_mutex_name_is_stable_per_executable_path(self) -> None:
        first = startup_guard.windows_single_instance_mutex_name(
            "C:/BlogHelper/BlogHelper.exe"
        )
        same = startup_guard.windows_single_instance_mutex_name(
            "C:/BlogHelper/BlogHelper.exe"
        )
        other = startup_guard.windows_single_instance_mutex_name(
            "C:/BlogHelper-Archive/BlogHelper.exe"
        )

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("Local\\BlogHelper-"))

    def test_mutex_handle_can_be_released_for_internal_restart(self) -> None:
        kernel32 = _FakeKernel32()

        startup_guard.release_windows_single_instance(321, kernel32=kernel32)

        self.assertEqual(kernel32.CloseHandle.calls, [(321,)])


if __name__ == "__main__":
    unittest.main()
