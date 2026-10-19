from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from chat_stream import delta_text, sse, stream_chat_events

SESSION_ID = "3f6c1b8e-6a1d-4c8e-9a4f-2b7d0c5e9f12"


def chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def parse(stream):
    """Turn SSE text into [(event, data), ...]."""
    events = []

    for block in "".join(stream).split("\n\n"):
        if not block:
            continue

        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))

    return events


class StreamChatEventsTests(unittest.TestCase):

    def run_stream(self, chunks, on_complete=None):
        saved = []

        def open_stream():
            for item in chunks:
                if isinstance(item, Exception):
                    raise item
                yield item

        events = parse(stream_chat_events(
            session_id=SESSION_ID,
            open_stream=open_stream,
            on_complete=on_complete or saved.append,
        ))

        return events, saved

    def test_happy_path_streams_deltas_then_saves_once(self):
        events, saved = self.run_stream([chunk("Hello"), chunk(" there"), chunk("!")])

        self.assertEqual(events[0], ("meta", {"session_id": SESSION_ID}))
        self.assertEqual(
            [data["text"] for name, data in events if name == "delta"],
            ["Hello", " there", "!"],
        )
        self.assertEqual(events[-1], ("done", {"saved": True}))
        self.assertEqual(saved, ["Hello there!"])

    def test_empty_and_role_only_chunks_are_skipped(self):
        role_only = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])
        no_choices = SimpleNamespace(choices=[])

        events, saved = self.run_stream([role_only, chunk("Hi"), no_choices])

        self.assertEqual([n for n, _ in events], ["meta", "delta", "done"])
        self.assertEqual(saved, ["Hi"])

    def test_model_failure_mid_stream_reports_error_and_saves_nothing(self):
        events, saved = self.run_stream([chunk("Partial"), RuntimeError("socket closed")])

        self.assertEqual(events[-1][0], "error")
        self.assertNotIn("socket", events[-1][1]["detail"])  # no internals leaked
        self.assertEqual(saved, [])

    def test_empty_reply_is_an_error(self):
        events, saved = self.run_stream([chunk("   ")])

        self.assertEqual(events[-1][0], "error")
        self.assertEqual(saved, [])

    def test_save_failure_still_completes_the_reply(self):
        def failing_save(reply):
            raise RuntimeError("version conflict")

        events, _ = self.run_stream([chunk("Answer")], on_complete=failing_save)

        self.assertEqual(events[-1], ("done", {"saved": False}))
        self.assertIn(("delta", {"text": "Answer"}), events)

    def test_newlines_in_text_do_not_break_sse_framing(self):
        events, saved = self.run_stream([chunk("line one\n\nline two")])

        self.assertEqual(events[1], ("delta", {"text": "line one\n\nline two"}))
        self.assertEqual(saved, ["line one\n\nline two"])

    def test_helpers(self):
        self.assertEqual(sse("x", {"a": 1}), 'event: x\ndata: {"a": 1}\n\n')
        self.assertEqual(delta_text(object()), "")


if __name__ == "__main__":
    unittest.main()
