import unittest

from wcp.transcript_quality import assess_transcript_quality, normalize_transcript_text


class TestTranscriptQuality(unittest.TestCase):
    def test_detects_repetition_loops(self):
        bad = ("Nao e? " * 220).strip()
        q = assess_transcript_quality(bad)
        self.assertFalse(q.ok)
        self.assertTrue(any(issue in q.issues for issue in ("repeated_phrase_loop", "repeated_sentence_loop", "low_unique_token_ratio")))

    def test_accepts_normal_text(self):
        good = (
            "Fizemos uma chamada curta. Falamos sobre o horario de amanha, "
            "confirmamos os detalhes e depois encerramos sem problemas."
        )
        q = assess_transcript_quality(good)
        self.assertTrue(q.ok)

    def test_normalization_removes_control_noise(self):
        raw = "abc\u0000\u0001 def \ufeff\n\tghi"
        cleaned = normalize_transcript_text(raw)
        self.assertNotIn("\u0000", cleaned)
        self.assertNotIn("\u0001", cleaned)
        self.assertIn("abc", cleaned)
        self.assertIn("ghi", cleaned)

    def test_detects_trailing_replacement_char(self):
        text = (
            "Esta parte da transcricao esta normal e tem conteudo suficiente para validacao. "
            "Depois aparecem caracteres quebrados no final "
            + ("\ufffd" * 20)
        )
        q = assess_transcript_quality(text)
        self.assertFalse(q.ok)
        self.assertIn("trailing_replacement_char", q.issues)

    def test_detects_trailing_symbol_noise(self):
        text = (
            "Transcricao com texto inicial coerente e no final vira ruido de simbolos "
            + ("§¤¶¦‡†•‣※⁂◊◇◆○●△▽" * 4)
        )
        q = assess_transcript_quality(text)
        self.assertFalse(q.ok)
        self.assertIn("trailing_symbol_noise", q.issues)


if __name__ == "__main__":
    unittest.main()
