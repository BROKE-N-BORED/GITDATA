"""Tests for the STELLA converter (T35).

Covers map_row for every shape the converter accepts or skips,
the three text formats, validate_rows, and peek_shape (T29).
No network, no Qt.
"""

import unittest

from scout import convert


class MapRowTests(unittest.TestCase):
    def test_prompt_completion_passes_through(self):
        row, reason = convert.map_row(
            {"prompt": "What is 2+2?", "completion": "4"})
        self.assertEqual(row, {"prompt": "What is 2+2?",
                               "completion": "4"})
        self.assertEqual(reason, "")

    def test_messages_pass_through(self):
        messages = [{"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello"}]
        row, reason = convert.map_row({"messages": messages})
        self.assertEqual(row, {"messages": messages})
        self.assertEqual(reason, "")

    def test_instruction_input_output_maps_to_prompt(self):
        row, _reason = convert.map_row(
            {"instruction": "Add these.", "input": "2 and 3",
             "output": "5"})
        self.assertEqual(row["prompt"], "Add these.\n\n2 and 3")
        self.assertEqual(row["completion"], "5")

    def test_instruction_without_input(self):
        row, _reason = convert.map_row(
            {"instruction": "Say hi.", "output": "Hi"})
        self.assertEqual(row["prompt"], "Say hi.")
        self.assertEqual(row["completion"], "Hi")

    def test_question_answer_maps(self):
        row, _reason = convert.map_row(
            {"question": "Why?", "answer": "Because."})
        self.assertEqual(row, {"prompt": "Why?",
                               "completion": "Because."})

    def test_instruction_answer_maps(self):
        # Common instruction-tuning layout: instruction + answer,
        # sometimes with a thought_process field (ignored — extra
        # fields never block a row that maps).
        row, _reason = convert.map_row(
            {"instruction": "Solve 2+2.", "answer": "4",
             "thought_process": "2 plus 2 is 4."})
        self.assertEqual(row, {"prompt": "Solve 2+2.",
                               "completion": "4"})

    def test_instruction_response_maps(self):
        row, _reason = convert.map_row(
            {"instruction": "Say hi.", "response": "Hi"})
        self.assertEqual(row, {"prompt": "Say hi.",
                               "completion": "Hi"})

    def test_question_response_maps(self):
        row, _reason = convert.map_row(
            {"question": "Why?", "response": "Because."})
        self.assertEqual(row, {"prompt": "Why?",
                               "completion": "Because."})

    def test_input_output_maps(self):
        row, _reason = convert.map_row(
            {"input": "Translate: cat", "output": "chat"})
        self.assertEqual(row, {"prompt": "Translate: cat",
                               "completion": "chat"})

    def test_prompt_response_maps(self):
        # v0.1.4 T19: prompt + response is a common pair.
        row, _reason = convert.map_row(
            {"prompt": "Why?", "response": "Because."})
        self.assertEqual(row, {"prompt": "Why?",
                               "completion": "Because."})

    def test_prompt_ground_truth_maps(self):
        # v0.1.4 T19: question-answering files pair a prompt
        # with a ground truth answer.
        row, _reason = convert.map_row(
            {"prompt": "2+2?", "ground_truth": "4"})
        self.assertEqual(row, {"prompt": "2+2?",
                               "completion": "4"})

    def test_question_ground_truth_maps(self):
        row, _reason = convert.map_row(
            {"question": "2+2?", "ground_truth": "4"})
        self.assertEqual(row, {"prompt": "2+2?",
                               "completion": "4"})

    def test_instruction_ground_truth_maps(self):
        row, _reason = convert.map_row(
            {"instruction": "Add.", "ground_truth": "4"})
        self.assertEqual(row, {"prompt": "Add.",
                               "completion": "4"})

    def test_prompt_without_completion_says_what_is_missing(self):
        # v0.1.4 T19: the prompt is present, so the reason says
        # the completion is missing instead of calling the
        # prompt empty.
        row, reason = convert.map_row({"prompt": "a"})
        self.assertIsNone(row)
        self.assertIn("prompt has no completion", reason)

    def test_completion_without_prompt_says_what_is_missing(self):
        row, reason = convert.map_row({"completion": "b"})
        self.assertIsNone(row)
        self.assertIn("completion has no prompt", reason)

    def test_text_only_is_skipped_not_faked(self):
        # STELLA cannot train on a row with no prompt, so a bare
        # text row is skipped, never given an empty prompt.
        row, reason = convert.map_row({"text": "Some plain text."})
        self.assertIsNone(row)
        self.assertIn("text only", reason)

    def test_empty_prompt_is_skipped(self):
        row, reason = convert.map_row({"prompt": "  ",
                                       "completion": "x"})
        self.assertIsNone(row)
        self.assertIn("empty", reason)

    def test_empty_completion_is_skipped(self):
        row, reason = convert.map_row({"prompt": "x",
                                       "completion": ""})
        self.assertIsNone(row)
        self.assertIn("empty", reason)

    def test_messages_last_must_be_assistant(self):
        row, reason = convert.map_row(
            {"messages": [{"role": "user", "content": "Hi"},
                          {"role": "user", "content": "Still me"}]})
        self.assertIsNone(row)
        self.assertIn("not a valid conversation", reason)

    def test_messages_need_two_entries(self):
        row, _reason = convert.map_row(
            {"messages": [{"role": "assistant", "content": "Hi"}]})
        self.assertIsNone(row)

    def test_messages_bad_role_is_skipped(self):
        row, _reason = convert.map_row(
            {"messages": [{"role": "narrator", "content": "Hi"},
                          {"role": "assistant", "content": "Yo"}]})
        self.assertIsNone(row)

    def test_unknown_shape_is_skipped(self):
        row, reason = convert.map_row({"foo": 1, "bar": 2})
        self.assertIsNone(row)
        self.assertIn("no mapping", reason)

    def test_non_object_row_is_skipped(self):
        row, _reason = convert.map_row("just a string")
        self.assertIsNone(row)

    def test_empty_prompt_falls_through_to_instruction(self):
        # v0.1.3 T30: a broken prompt field no longer vetoes a row
        # that maps another way.
        row, _reason = convert.map_row(
            {"prompt": "", "completion": "",
             "instruction": "Add", "output": "2"})
        self.assertEqual(row, {"prompt": "Add", "completion": "2"})

    def test_broken_messages_fall_through_to_instruction(self):
        row, _reason = convert.map_row(
            {"messages": "not a list",
             "instruction": "Add", "output": "2"})
        self.assertEqual(row, {"prompt": "Add", "completion": "2"})

    def test_empty_prompt_falls_through_to_question(self):
        row, _reason = convert.map_row(
            {"prompt": " ", "completion": " ",
             "question": "What is 2+2?", "answer": "4"})
        self.assertEqual(row, {"prompt": "What is 2+2?",
                               "completion": "4"})


class ConvertTextTests(unittest.TestCase):
    def test_jsonl_prompt_completion(self):
        text = ('{"prompt": "a", "completion": "b"}\n'
                '{"prompt": "c", "completion": "d"}\n')
        rows, report = convert.convert_jsonl_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["format"], "prompt")
        self.assertEqual(report["kept"], 2)
        self.assertEqual(report["skipped"], {})

    def test_jsonl_skips_bad_lines_and_counts_them(self):
        text = ('{"prompt": "a", "completion": "b"}\n'
                'not json at all\n'
                '{"text": "plain"}\n'
                '{"prompt": "c", "completion": "d"}\n')
        rows, report = convert.convert_jsonl_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(report["skipped"].values()), 2)
        self.assertIn("not valid JSON", report["skipped"])
        self.assertIn("text only, no prompt to pair it with",
                      report["skipped"])

    def test_jsonl_first_valid_row_sets_format(self):
        # A messages row after prompt rows is skipped: one format
        # per file, and the first valid row wins.
        text = ('{"prompt": "a", "completion": "b"}\n'
                '{"messages": [{"role": "user", "content": "x"},'
                '{"role": "assistant", "content": "y"}]}\n'
                '{"prompt": "c", "completion": "d"}\n')
        rows, report = convert.convert_jsonl_text(text)
        self.assertEqual(report["format"], "prompt")
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            report["skipped"].get(
                "different shape from the rest of the file"), 1)

    def test_json_list(self):
        text = ('[{"instruction": "Add", "input": "1+1", "output": "2"},'
                ' {"instruction": "Add", "input": "2+2", "output": "4"}]')
        rows, _report = convert.convert_json_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["prompt"], "Add\n\n1+1")

    def test_json_wrapped_in_data_key(self):
        text = ('{"data": [{"prompt": "a", "completion": "b"},'
                ' {"prompt": "c", "completion": "d"}]}')
        rows, _report = convert.convert_json_text(text)
        self.assertEqual(len(rows), 2)

    def test_json_single_object_without_rows_is_skipped(self):
        rows, report = convert.convert_json_text('{"prompt": "a"}')
        self.assertEqual(rows, [])
        # v0.1.4 T19: the prompt is not empty, so the reason says
        # what is actually missing instead of "prompt or
        # completion is empty".
        self.assertIn("prompt has no completion to pair it with",
                      report["skipped"])

    def test_json_scalar_is_not_an_object(self):
        rows, report = convert.convert_json_text("42")
        self.assertEqual(rows, [])
        self.assertEqual(report["skipped"], {"not an object": 1})

    def test_csv_prompt_completion(self):
        text = "prompt,completion\nWhat?,That.\nWho?,Them.\n"
        rows, _report = convert.convert_csv_text(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["prompt"], "What?")

    def test_csv_tab_delimiter(self):
        text = "question\tanswer\nWhy?\tBecause.\nHow?\tLike this.\n"
        rows, _report = convert.convert_csv_text(text, delimiter="\t")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["prompt"], "Why?")

    def test_csv_headerless_keeps_its_first_row(self):
        # v0.1.3 T33: no recognised field name in the first row
        # means it is data, not a header.
        text = "What is 2+2?,4\nWhat is 3+3?,6\n"
        rows, report = convert.convert_csv_text(text)
        self.assertEqual(report["kept"], 2)
        self.assertEqual(report["seen"], 2)
        self.assertEqual(rows[0],
                         {"prompt": "What is 2+2?", "completion": "4"})

    def test_csv_headerless_tsv_keeps_its_first_row(self):
        text = "What is 2+2?\t4\nWhat is 3+3?\t6\n"
        rows, report = convert.convert_csv_text(text, delimiter="\t")
        self.assertEqual(report["kept"], 2)
        self.assertEqual(rows[1]["completion"], "6")

    def test_csv_capitalised_headers_map(self):
        text = "Prompt,Completion\nWhat?,That.\nWho?,Them.\n"
        rows, report = convert.convert_csv_text(text)
        self.assertEqual(report["kept"], 2)
        self.assertEqual(rows[0]["prompt"], "What?")

    def test_convert_text_dispatches_by_kind(self):
        text = ('{"prompt": "a", "completion": "b"}\n'
                '{"prompt": "c", "completion": "d"}\n')
        rows, _report = convert.convert_text(text, "jsonl")
        self.assertEqual(len(rows), 2)

    def test_convert_text_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            convert.convert_text("a,b\nc,d\n", "xml")

    def test_convert_text_parquet_kind_raises(self):
        # Parquet is a known kind but not a text kind: it is read
        # from a file path by scout.parquet (T62), so convert_text
        # raises for it like any kind it cannot handle.
        with self.assertRaises(ValueError):
            convert.convert_text("a,b\nc,d\n", "parquet")


class ValidateRowsTests(unittest.TestCase):
    def test_needs_two_rows(self):
        ok, message = convert.validate_rows(
            [{"prompt": "a", "completion": "b"}])
        self.assertFalse(ok)
        self.assertIn("2", message)

    def test_two_rows_ok(self):
        ok, _message = convert.validate_rows(
            [{"prompt": "a", "completion": "b"},
             {"prompt": "c", "completion": "d"}])
        self.assertTrue(ok)

    def test_mixed_format_rejected(self):
        ok, message = convert.validate_rows(
            [{"prompt": "a", "completion": "b"},
             {"messages": [{"role": "user", "content": "x"},
                           {"role": "assistant", "content": "y"}]}])
        self.assertFalse(ok)
        self.assertIn("mixed", message)


class RowsToJsonlTests(unittest.TestCase):
    def test_round_trips(self):
        rows = [{"prompt": "a", "completion": "b"},
                {"prompt": "c", "completion": "d"}]
        text = convert.rows_to_jsonl(rows)
        self.assertEqual(text.count("\n"), 2)
        back, _report = convert.convert_jsonl_text(text)
        self.assertEqual(back, rows)


class ColumnsMapTests(unittest.TestCase):
    # v0.1.4 T10: the Parquet peek judges columns by running a
    # probe row through map_row, so the peek and the converter
    # always agree.
    def test_prompt_completion_columns_map(self):
        self.assertTrue(convert.columns_map(["prompt", "completion"]))

    def test_question_ground_truth_columns_map(self):
        self.assertTrue(convert.columns_map(["question", "ground_truth"]))

    def test_messages_column_maps(self):
        self.assertTrue(convert.columns_map(["messages"]))

    def test_text_only_columns_do_not_map(self):
        self.assertFalse(convert.columns_map(["text"]))

    def test_unrelated_columns_do_not_map(self):
        self.assertFalse(convert.columns_map(["id", "url", "score"]))


class PeekShapeTests(unittest.TestCase):
    def test_ready_jsonl(self):
        text = ('{"prompt": "a", "completion": "b"}\n'
                '{"prompt": "c", "completion": "d"}\n')
        status, detail = convert.peek_shape(text)
        self.assertEqual(status, "ready")
        self.assertIn("already in STELLA's shape", detail)

    def test_convert_jsonl(self):
        text = ('{"instruction": "Add", "output": "2"}\n'
                '{"instruction": "Add", "output": "4"}\n')
        status, detail = convert.peek_shape(text)
        self.assertEqual(status, "convert")
        self.assertIn("with conversion", detail)

    def test_unknown_text_only(self):
        # v0.1.4 T12: the file was read and its rows have no
        # usable fields, so the peek says so instead of
        # "unknown" (which means the check itself could not run).
        text = '{"text": "plain"}\n{"text": "plain"}\n'
        status, detail = convert.peek_shape(text)
        self.assertEqual(status, "unmappable")
        self.assertIn("no fields Scout can map", detail)

    def test_empty_text_is_unknown(self):
        status, _detail = convert.peek_shape("")
        self.assertEqual(status, "unknown")

    def test_non_json_text_is_unknown(self):
        # peek_shape reads JSON lines only; a CSV header is not JSON.
        status, detail = convert.peek_shape(
            "prompt,completion\na,b\nc,d\n")
        self.assertEqual(status, "unknown")
        self.assertIn("not JSON", detail)


class DescribeReportTests(unittest.TestCase):
    def test_plain_summary(self):
        text = convert.describe_report(
            {"kept": 10,
             "skipped": {"text only, no prompt to pair it with": 2,
                         "not valid JSON": 1}})
        self.assertIn("10 rows kept", text)
        self.assertIn("3 skipped", text)


if __name__ == "__main__":
    unittest.main()
