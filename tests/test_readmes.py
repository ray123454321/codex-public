from __future__ import annotations

import json
from pathlib import Path
import re
import unittest
from urllib.parse import unquote


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def github_heading_slug(heading: str) -> str:
    normalized = heading.strip().lower().replace("`", "")
    normalized = re.sub(r"[^\w\- ]", "", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", "-", normalized)


class RepositoryReadmeTests(unittest.TestCase):
    def test_root_readmes_are_bilingual_and_link_every_published_plugin(self) -> None:
        english = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPOSITORY_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))

        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)

        for plugin in marketplace.get("plugins", []):
            plugin_name = plugin["name"]
            plugin_root = REPOSITORY_ROOT / "plugins" / plugin_name
            english_path = f"plugins/{plugin_name}/README.md"
            chinese_path = f"plugins/{plugin_name}/README.zh-CN.md"

            self.assertTrue((plugin_root / "README.md").is_file())
            self.assertIn(f"({english_path})", english)
            self.assertIn(f"({english_path})", chinese)

            if (plugin_root / "README.zh-CN.md").is_file():
                self.assertIn(f"({chinese_path})", english)
                self.assertIn(f"({chinese_path})", chinese)

    def test_all_root_relative_markdown_links_resolve(self) -> None:
        for readme_name in ("README.md", "README.zh-CN.md"):
            readme = REPOSITORY_ROOT / readme_name
            for raw_target in MARKDOWN_LINK.findall(
                readme.read_text(encoding="utf-8")
            ):
                if "://" in raw_target or raw_target.startswith(("mailto:", "#")):
                    continue

                path_text, _, fragment = raw_target.partition("#")
                target = (readme.parent / unquote(path_text)).resolve()
                self.assertTrue(target.is_file(), f"broken link in {readme_name}: {raw_target}")

                if fragment and target.suffix.lower() == ".md":
                    headings = re.findall(
                        r"^#{1,6}\s+(.+?)\s*$",
                        target.read_text(encoding="utf-8"),
                        flags=re.MULTILINE,
                    )
                    slugs = {github_heading_slug(heading) for heading in headings}
                    self.assertIn(
                        unquote(fragment),
                        slugs,
                        f"broken anchor in {readme_name}: {raw_target}",
                    )


if __name__ == "__main__":
    unittest.main()
