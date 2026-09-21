from clipboard_agent.clipboard_utils import clipboard_digest, normalize_clipboard_text


def test_clipboard_digest_ignores_windows_line_ending_conversion():
    source = "#Execution\nID: inspect-root\n\nGet-ChildItem -Force\n"
    windows_roundtrip = source.replace("\n", "\r\n")
    assert clipboard_digest(source) == clipboard_digest(windows_roundtrip)


def test_clipboard_digest_ignores_terminal_nul_transport_artifact():
    source = "hello\nworld"
    assert clipboard_digest(source) == clipboard_digest(source + "\x00")


def test_normalization_does_not_strip_meaningful_spaces_or_newlines():
    assert normalize_clipboard_text("a  \n") == "a  \n"


def test_poll_ignores_own_clipboard_after_crlf_roundtrip_and_keeps_polling():
    from clipboard_agent.app import ClipboardAgentApp

    source = "Initial prompt with #Execution and #Show\n"
    observed = source.replace("\n", "\r\n")

    class FakeApp:
        POLL_MS = 250
        auto_enabled = False
        last_seen_clipboard_hash = "different-before-observation"
        last_written_clipboard_hash = clipboard_digest(source)
        last_written_clipboard_normalized = normalize_clipboard_text(source)

        def _read_clipboard(self):
            return observed

        def _hash(self, text):
            return clipboard_digest(text)

        def _handle_agent_text(self, text, *, source_auto):
            raise AssertionError("an application-owned clipboard payload must not be parsed")

        def _poll_clipboard(self):
            pass

        def after(self, delay, callback):
            self.scheduled = (delay, callback)

    fake = FakeApp()
    ClipboardAgentApp._poll_clipboard(fake)
    assert fake.last_seen_clipboard_hash == clipboard_digest(source)
    assert fake.scheduled[0] == fake.POLL_MS


def test_poll_processes_new_external_clipboard_text():
    from clipboard_agent.app import ClipboardAgentApp

    own = "initial prompt"
    external = "#End\nDone"

    class FakeApp:
        POLL_MS = 250
        auto_enabled = False
        last_seen_clipboard_hash = clipboard_digest(own)
        last_written_clipboard_hash = clipboard_digest(own)
        last_written_clipboard_normalized = normalize_clipboard_text(own)

        def _read_clipboard(self):
            return external

        def _hash(self, text):
            return clipboard_digest(text)

        def _handle_agent_text(self, text, *, source_auto):
            self.handled = (text, source_auto)

        def _poll_clipboard(self):
            pass

        def after(self, delay, callback):
            self.scheduled = (delay, callback)

    fake = FakeApp()
    ClipboardAgentApp._poll_clipboard(fake)
    assert fake.handled == (external, False)
    assert fake.scheduled[0] == fake.POLL_MS


def test_auto_copy_accepts_same_text_when_windows_clipboard_sequence_changes():
    from clipboard_agent.app import ClipboardAgentApp
    from clipboard_agent.state_machine import AutoState

    copied = "#End\nDone"
    digest = clipboard_digest(copied)

    class FakeDesktop:
        def clipboard_sequence_number(self):
            return 101

    class FakeApp:
        POLL_MS = 250
        auto_enabled = True
        auto_paused = False
        auto_state = AutoState.WAITING_CLIPBOARD
        auto_copy_baseline_hash = digest
        auto_copy_baseline_sequence = 100
        last_seen_clipboard_hash = digest
        last_written_clipboard_hash = digest
        last_written_clipboard_normalized = normalize_clipboard_text(copied)
        desktop = FakeDesktop()

        def _read_clipboard(self):
            return copied

        def _hash(self, text):
            return clipboard_digest(text)

        def _cancel_auto_jobs(self):
            self.cancelled = True

        def _auto_accept_copied_text(self, text):
            self.accepted = text

        def _poll_clipboard(self):
            pass

        def after(self, delay, callback):
            self.scheduled = (delay, callback)

    fake = FakeApp()
    ClipboardAgentApp._poll_clipboard(fake)
    assert fake.cancelled is True
    assert fake.accepted == copied
    assert fake.scheduled[0] == fake.POLL_MS


def test_auto_copy_same_text_without_new_sequence_waits_for_timeout():
    from clipboard_agent.app import ClipboardAgentApp
    from clipboard_agent.state_machine import AutoState

    copied = "#End\nDone"
    digest = clipboard_digest(copied)

    class FakeDesktop:
        def clipboard_sequence_number(self):
            return 100

    class FakeApp:
        POLL_MS = 250
        auto_enabled = True
        auto_paused = False
        auto_state = AutoState.WAITING_CLIPBOARD
        auto_copy_baseline_hash = digest
        auto_copy_baseline_sequence = 100
        last_seen_clipboard_hash = digest
        last_written_clipboard_hash = digest
        last_written_clipboard_normalized = normalize_clipboard_text(copied)
        desktop = FakeDesktop()

        def _read_clipboard(self):
            return copied

        def _hash(self, text):
            return clipboard_digest(text)

        def _cancel_auto_jobs(self):
            raise AssertionError("no fresh Copy event was observed")

        def _auto_accept_copied_text(self, text):
            raise AssertionError("same old clipboard content must not be accepted")

        def _poll_clipboard(self):
            pass

        def after(self, delay, callback):
            self.scheduled = (delay, callback)

    fake = FakeApp()
    ClipboardAgentApp._poll_clipboard(fake)
    assert fake.scheduled[0] == fake.POLL_MS
