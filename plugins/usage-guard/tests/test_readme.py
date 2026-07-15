from __future__ import annotations

from pathlib import Path
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class ReadmeContractTests(unittest.TestCase):
    def test_both_languages_document_all_three_responsibilities(self) -> None:
        english = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (PLUGIN_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)

        for heading in (
            "## 1. Keep the current model under compute pressure",
            "## 2. Automatically use a reset opportunity at 97%",
            "## 3. Prepare handoff after a failed redeem above 98%",
        ):
            self.assertIn(heading, english)

        for heading in (
            "## 1. 算力紧张时保留当前模型",
            "## 2. 使用率达到 97% 时自动使用重置机会",
            "## 3. Redeem 失败且超过 98% 时准备任务交接",
        ):
            self.assertIn(heading, chinese)


if __name__ == "__main__":
    unittest.main()
