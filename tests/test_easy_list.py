import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easy_list import (
    DEFAULT_CONFIG,
    build_listing_plan,
    calculate_target_price,
    choose_search_image,
    run,
    search_by_image,
)


class EasyListTests(unittest.TestCase):
    def test_search_by_image_normalizes_and_encodes_limit(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"itemSummaries": []}'

        captured = {}

        def fake_urlopen(req):
            captured["url"] = req.full_url
            return FakeResponse()

        with patch("easy_list.request.urlopen", side_effect=fake_urlopen):
            result = search_by_image("token", "encoded-image", "EBAY_US", "5")

        self.assertEqual(result["itemSummaries"], [])
        self.assertTrue(captured["url"].endswith("?limit=5"))

    def test_calculate_target_price_reduces_price(self):
        self.assertEqual(calculate_target_price("100.00", 15), 85.0)

    def test_choose_search_image_prefers_named_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "a.jpg"
            second = folder / "b.jpg"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            self.assertEqual(choose_search_image([first, second], "b.jpg"), second)

    def test_run_dry_run_discovers_item_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            pictures_dir = root / "pictures" / "item-1"
            pictures_dir.mkdir(parents=True)
            (pictures_dir / "photo1.jpg").write_bytes(b"photo")
            config_path.write_text(
                json.dumps(
                    {
                        "pictures": {"directory": "pictures"},
                        "listing": {"percentage_less": 20},
                    }
                ),
                encoding="utf-8",
            )

            report = run(config_path, dry_run=True)

            self.assertTrue(report["dry_run"])
            self.assertEqual(report["items"][0]["item_folder"], "item-1")
            self.assertEqual(len(report["items"][0]["listing_images"]), 1)

    def test_build_listing_plan_uses_first_match_for_price_guidance(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        plan = build_listing_plan(
            item_folder=Path("camera"),
            listing_images=[Path("camera/front.jpg")],
            search_image=Path("camera/front.jpg"),
            matches=[
                {
                    "item_id": "123",
                    "title": "Camera",
                    "price": "50.00",
                    "currency": "USD",
                    "item_web_url": "https://www.ebay.com/itm/123",
                }
            ],
            config=config,
        )

        self.assertEqual(plan["recommended_listing"]["suggested_price"], 45.0)
        self.assertEqual(plan["recommended_listing"]["sell_one_like_this_hint"], "https://www.ebay.com/itm/123")


if __name__ == "__main__":
    unittest.main()
