import unittest

from builddiet import tomlmini
from builddiet.config import Config, ConfigError, from_dict, normalize_rel, render_config
from builddiet.cost import reuse_probability
from builddiet.units import format_duration, format_size, parse_size


class UnitsTest(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(parse_size("20GB"), 20 * 10**9)
        self.assertEqual(parse_size("1.5 kb"), 1500)
        self.assertEqual(parse_size("2MiB"), 2 * 2**20)
        self.assertEqual(parse_size(42), 42)
        with self.assertRaises(ValueError):
            parse_size("lots")

    def test_format(self):
        self.assertEqual(format_size(12_400_000_000), "12.4 GB")
        self.assertEqual(format_duration(43), "43s")
        self.assertEqual(format_duration(137), "2m17s")
        self.assertEqual(format_duration(8 * 3600 + 12 * 60), "8h12m")


class TomlTest(unittest.TestCase):
    TEXT = """
# comment
[commands]
regenerate = 'C:\\tools\\build.bat --all'   # literal string
verify = "python -m unittest \\"x\\""
timeout = 1_200

[candidates]
auto = false
include = [
  "experiments/cache",  # trailing comment
  'arena',
]
min_size = "1MB"

[reuse]
"old-results" = 0.05
a.b = 1
"""

    def test_minimal_parser(self):
        data = tomlmini.loads_minimal(self.TEXT)
        self.assertEqual(data["commands"]["regenerate"], "C:\\tools\\build.bat --all")
        self.assertEqual(data["commands"]["verify"], 'python -m unittest "x"')
        self.assertEqual(data["commands"]["timeout"], 1200)
        self.assertIs(data["candidates"]["auto"], False)
        self.assertEqual(data["candidates"]["include"], ["experiments/cache", "arena"])
        self.assertEqual(data["reuse"]["old-results"], 0.05)
        self.assertEqual(data["reuse"]["a"]["b"], 1)

    def test_rendered_config_roundtrips(self):
        cfg = Config(
            regenerate="cmake --build build",
            verify="it's \"quoted\"",
            include=["x/y"],
            exclude=["family_photos"],
            reuse={"old": 0.1},
        )
        text = render_config(cfg)
        for parse in (tomlmini.loads, tomlmini.loads_minimal):
            back = from_dict(parse(text))
            self.assertEqual(back.regenerate, cfg.regenerate)
            self.assertEqual(back.verify, cfg.verify)
            self.assertEqual(back.include, ["x/y"])
            self.assertEqual(back.exclude, ["family_photos"])
            self.assertEqual(back.min_size, cfg.min_size)
            self.assertEqual(back.reuse, {"old": 0.1})
            self.assertEqual(back.digest(), cfg.digest())


class ConfigTest(unittest.TestCase):
    def test_normalize_rel(self):
        self.assertEqual(normalize_rel(".\\a\\b\\"), "a/b")
        for bad in ("../x", "/etc", "C:\\x", ".", "a/../../b"):
            with self.assertRaises(ConfigError):
                normalize_rel(bad)

    def test_requires_a_workflow(self):
        with self.assertRaises(ConfigError):
            Config().validate()

    def test_reuse_probability_most_specific_wins(self):
        reuse = {"exp": 0.5, "exp/cache": 0.1}
        self.assertEqual(reuse_probability(reuse, "exp/cache"), 0.1)
        self.assertEqual(reuse_probability(reuse, "exp/other"), 0.5)
        self.assertEqual(reuse_probability(reuse, "build"), 1.0)


if __name__ == "__main__":
    unittest.main()
