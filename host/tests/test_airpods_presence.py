from __future__ import annotations

import json
import unittest

from host.airpods_presence import (
    DEFAULT_URL,
    PresenceDebouncer,
    build_parser,
    detect_backend,
    make_probe,
    parse_blueutil,
    parse_bluetoothctl,
    parse_system_profiler,
    post_presence,
    run,
)


class PresenceDebouncerTests(unittest.TestCase):
    def test_requires_two_consecutive_readings_to_flip(self) -> None:
        debounce = PresenceDebouncer()
        self.assertIsNone(debounce.observe(True))  # first reading: not yet
        self.assertTrue(debounce.observe(True))  # second consecutive: confirm
        self.assertIsNone(debounce.observe(True))  # no change

    def test_single_transient_reading_is_ignored(self) -> None:
        debounce = PresenceDebouncer()
        debounce.observe(True)
        debounce.observe(True)  # confirmed True
        self.assertIsNone(debounce.observe(False))  # one stray False
        self.assertIsNone(debounce.observe(True))  # back to True before flipping
        self.assertIs(debounce.confirmed, True)

    def test_unknown_readings_do_not_flip(self) -> None:
        debounce = PresenceDebouncer()
        self.assertIsNone(debounce.observe(None))
        self.assertIsNone(debounce.observe(None))
        self.assertIsNone(debounce.confirmed)

    def test_probe_failure_breaks_the_consecutive_run(self) -> None:
        debounce = PresenceDebouncer()
        self.assertIsNone(debounce.observe(True))  # candidate True
        self.assertIsNone(debounce.observe(None))  # failed probe resets the run
        self.assertIsNone(debounce.observe(True))  # only first consecutive again
        self.assertTrue(debounce.observe(True))  # now two truly consecutive


class RunLoopTests(unittest.TestCase):
    def test_debounces_changes_and_heartbeats_presence(self) -> None:
        readings = [True, True, True, False, True, True, False, False]
        posts: list[bool] = []
        logs: list[str] = []
        index = {"i": 0}

        def probe() -> bool | None:
            value = readings[index["i"]]
            index["i"] += 1
            return value

        run(
            probe,
            posts.append,
            interval_seconds=1.0,
            sleep=lambda _: None,
            log=logs.append,
            max_polls=len(readings),
        )

        # Only the two debounced transitions are logged; the stray False at
        # index 3 never flips the believed state.
        self.assertEqual(logs, ["airpods connected", "airpods disconnected"])
        # Heartbeat: once connected, the confirmed state is re-posted every poll
        # to keep the host's TTL fresh.
        self.assertEqual(posts[0], True)
        self.assertEqual(posts[-1], False)
        self.assertNotIn(None, posts)

    def test_no_post_or_log_before_a_state_is_confirmed(self) -> None:
        posts: list[bool] = []
        logs: list[str] = []
        run(
            lambda: True,
            posts.append,
            sleep=lambda _: None,
            log=logs.append,
            max_polls=1,
        )
        self.assertEqual(posts, [])
        self.assertEqual(logs, [])


class PostPresenceTests(unittest.TestCase):
    def test_builds_minimal_presence_payload(self) -> None:
        captured: dict[str, object] = {}

        class _Resp:
            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return b""

        def fake_opener(req: object, timeout: float | None = None) -> _Resp:
            captured["url"] = req.full_url  # type: ignore[attr-defined]
            captured["method"] = req.method  # type: ignore[attr-defined]
            captured["data"] = req.data  # type: ignore[attr-defined]
            return _Resp()

        post_presence(
            "http://localhost:8787/presence", True, source="airpods", ttl_seconds=30.0,
            opener=fake_opener,
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            json.loads(captured["data"]),  # type: ignore[arg-type]
            {"present": True, "source": "airpods", "ttl_seconds": 30.0},
        )


class ParsingTests(unittest.TestCase):
    BLUEUTIL = (
        'address: aa-bb-cc, not connected, not favourite, paired, '
        'name: "Shantanu\'s AirPods", recent access date: 2026\n'
        'address: dd-ee-ff, connected (master, -50 dBm), paired, name: "Keyboard"\n'
    )
    SYSTEM_PROFILER = json.dumps(
        {
            "SPBluetoothDataType": [
                {
                    "device_connected": [
                        {"Shantanu's AirPods": {"device_address": "AA-BB"}}
                    ],
                    "device_not_connected": [
                        {"Magic Mouse": {"device_address": "CC-DD"}}
                    ],
                }
            ]
        }
    )
    BLUETOOTHCTL = (
        "Device AA:BB:CC:DD:EE:FF Shantanu's AirPods\n"
        "Device 11:22:33:44:55:66 Keyboard\n"
    )

    def test_blueutil_connected_and_disconnected(self) -> None:
        self.assertIs(parse_blueutil(self.BLUEUTIL, "Shantanu's AirPods"), False)
        self.assertIs(parse_blueutil(self.BLUEUTIL, "Keyboard"), True)
        self.assertIsNone(parse_blueutil(self.BLUEUTIL, "Mouse"))

    def test_system_profiler_states(self) -> None:
        self.assertIs(parse_system_profiler(self.SYSTEM_PROFILER, "Shantanu's AirPods"), True)
        self.assertIs(parse_system_profiler(self.SYSTEM_PROFILER, "Magic Mouse"), False)
        self.assertIsNone(parse_system_profiler(self.SYSTEM_PROFILER, "Unknown"))

    def test_system_profiler_malformed_is_unknown(self) -> None:
        self.assertIsNone(parse_system_profiler("not json", "x"))

    def test_bluetoothctl_connected_only(self) -> None:
        self.assertIs(parse_bluetoothctl(self.BLUETOOTHCTL, "Shantanu's AirPods"), True)
        self.assertIs(parse_bluetoothctl(self.BLUETOOTHCTL, "Mouse"), False)

    def test_name_matching_is_exact_not_substring(self) -> None:
        sp = json.dumps(
            {
                "SPBluetoothDataType": [
                    {"device_connected": [{"AirPods Pro": {"device_address": "AA"}}]}
                ]
            }
        )
        # "AirPods" must not false-match the connected "AirPods Pro".
        self.assertIsNone(parse_system_profiler(sp, "AirPods"))
        self.assertIs(parse_system_profiler(sp, "airpods pro"), True)

    def test_blueutil_does_not_misread_connected_keyword(self) -> None:
        line = 'address: aa, not connected, paired, name: "AirPods", connected: 0\n'
        self.assertIs(parse_blueutil(line, "AirPods"), False)


class BackendTests(unittest.TestCase):
    def test_detect_backend_per_platform(self) -> None:
        self.assertEqual(detect_backend(platform="darwin", which=lambda _: "/usr/bin/blueutil"), "blueutil")
        self.assertEqual(detect_backend(platform="darwin", which=lambda _: None), "system_profiler")
        self.assertEqual(detect_backend(platform="linux", which=lambda _: None), "bluetoothctl")

    def test_make_probe_uses_injected_runner(self) -> None:
        probe = make_probe(
            "Shantanu's AirPods", "blueutil", runner=lambda _cmd: ParsingTests.BLUEUTIL
        )
        self.assertIs(probe(), False)

    def test_probe_returns_unknown_when_command_fails(self) -> None:
        probe = make_probe("AirPods", "bluetoothctl", runner=lambda _cmd: None)
        self.assertIsNone(probe())


class CliTests(unittest.TestCase):
    def test_url_defaults_to_localhost(self) -> None:
        args = build_parser().parse_args(["--name", "AirPods"])
        self.assertEqual(args.url, DEFAULT_URL)
        self.assertTrue(args.url.startswith("http://localhost"))

    def test_name_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])


class IntegrationTests(unittest.TestCase):
    def test_helper_post_reaches_control_server_and_wakes_loop(self) -> None:
        from host.control import ControlServer, ControlServerConfig
        from host.inputs import HostInputQueue
        from host.presence import SignalMailbox

        inputs = HostInputQueue()
        mailbox = SignalMailbox()
        readings = [True, True]
        index = {"i": 0}

        def probe() -> bool | None:
            value = readings[min(index["i"], len(readings) - 1)]
            index["i"] += 1
            return value

        with ControlServer(
            ControlServerConfig(port=0), inputs, signal_mailbox=mailbox
        ) as server:
            url = f"http://{server.address[0]}:{server.address[1]}/presence"

            def poster(present: bool) -> None:
                post_presence(url, present, source="airpods", ttl_seconds=30.0, timeout=2.0)

            run(probe, poster, sleep=lambda _: None, max_polls=2)

            signal = inputs.wait(0.5)

        self.assertIs(mailbox.get().present, True)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.source, "presence_signal")


if __name__ == "__main__":
    unittest.main()
