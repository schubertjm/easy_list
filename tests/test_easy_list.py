import json
from io import BytesIO
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from easy_list import (
    DEFAULT_CONFIG,
    EasyListError,
    build_listing_plan,
    calculate_target_price,
    choose_search_image,
    encode_image,
    get_access_token,
    load_config,
    resolve_credentials,
    run,
    search_by_image,
)


class EasyListTests(unittest.TestCase):
    def test_load_config_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text("{invalid", encoding="utf-8")

            with self.assertRaises(EasyListError):
                load_config(config_path)

    def test_load_config_requires_object_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text('["not-an-object"]', encoding="utf-8")

            with self.assertRaises(EasyListError):
                load_config(config_path)

    def test_load_config_wraps_missing_file(self):
        with self.assertRaises(EasyListError):
            load_config(Path("missing-config.json"))

    def test_resolve_credentials_prefers_environment_variables(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["ebay"]["client_id"] = "config-id"
        config["ebay"]["client_secret"] = "config-secret"

        with patch.dict(os.environ, {"EBAY_CLIENT_ID": "env-id", "EBAY_CLIENT_SECRET": "env-secret"}, clear=False):
            self.assertEqual(resolve_credentials(config), ("env-id", "env-secret"))

    def test_resolve_credentials_requires_values(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))

        with patch.dict(os.environ, {"EBAY_CLIENT_ID": "", "EBAY_CLIENT_SECRET": ""}, clear=False):
            with self.assertRaises(EasyListError):
                resolve_credentials(config)

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

    def test_search_by_image_rejects_non_positive_limit(self):
        with self.assertRaises(EasyListError):
            search_by_image("token", "encoded-image", "EBAY_US", 0)

    def test_search_by_image_rejects_non_numeric_limit(self):
        with self.assertRaises(EasyListError):
            search_by_image("token", "encoded-image", "EBAY_US", "abc")

    def test_get_access_token_wraps_http_errors(self):
        http_error = error.HTTPError(
            url="https://api.ebay.com/identity/v1/oauth2/token",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"error_description":"bad credentials"}'),
        )

        with patch("easy_list.request.urlopen", side_effect=http_error):
            with self.assertRaises(EasyListError) as ctx:
                get_access_token("client", "secret", "scope")

        self.assertIn("Unable to get an eBay access token: bad credentials", str(ctx.exception))

    def test_get_access_token_wraps_url_errors(self):
        with patch("easy_list.request.urlopen", side_effect=error.URLError("offline")):
            with self.assertRaises(EasyListError) as ctx:
                get_access_token("client", "secret", "scope")

        self.assertIn("Unable to get an eBay access token: offline", str(ctx.exception))

    def test_search_by_image_wraps_http_errors(self):
        http_error = error.HTTPError(
            url="https://api.ebay.com/buy/browse/v1/item_summary/search_by_image",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=BytesIO(b'{"message":"temporary failure"}'),
        )

        with patch("easy_list.request.urlopen", side_effect=http_error):
            with self.assertRaises(EasyListError) as ctx:
                search_by_image("token", "encoded-image", "EBAY_US", 5)

        self.assertIn("eBay image search failed: temporary failure", str(ctx.exception))

    def test_search_by_image_wraps_url_errors(self):
        with patch("easy_list.request.urlopen", side_effect=error.URLError("offline")):
            with self.assertRaises(EasyListError) as ctx:
                search_by_image("token", "encoded-image", "EBAY_US", 5)

        self.assertIn("eBay image search failed: offline", str(ctx.exception))

    def test_encode_image_wraps_read_failures(self):
        image_path = Path("broken.jpg")

        with patch.object(Path, "read_bytes", side_effect=OSError("denied")):
            with self.assertRaises(EasyListError):
                encode_image(image_path)

    def test_calculate_target_price_reduces_price(self):
        self.assertEqual(calculate_target_price("100.00", 15), 85.0)

    def test_calculate_target_price_returns_none_for_invalid_numbers(self):
        self.assertIsNone(calculate_target_price("not-a-number", 15))

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
