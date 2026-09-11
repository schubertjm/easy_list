#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
DEFAULT_SCOPE = "https://api.ebay.com/oauth/api_scope"
DEFAULT_CONFIG: dict[str, Any] = {
    "ebay": {
        "client_id": "",
        "client_secret": "",
        "scope": DEFAULT_SCOPE,
        "marketplace_id": "EBAY_US",
        "result_limit": 5,
    },
    "listing": {
        "percentage_less": 10,
        "shipping_type": "calculated",
        "weight_lbs": 1.0,
        "shipping_instructions": "Set shipping details before publishing the listing.",
        "currency": "USD",
    },
    "pictures": {
        "directory": "pictures",
        "search_image_name": "",
    },
}


class EasyListError(RuntimeError):
    pass


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Path) -> dict[str, Any]:
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except json.JSONDecodeError as exc:
        raise EasyListError(f"Invalid JSON config file: {config_path}") from exc
    return deep_merge(DEFAULT_CONFIG, loaded)


def list_picture_sets(pictures_dir: Path) -> list[Path]:
    if not pictures_dir.exists():
        raise EasyListError(f"Pictures directory does not exist: {pictures_dir}")

    picture_sets = [path for path in sorted(pictures_dir.iterdir()) if path.is_dir()]
    if not picture_sets:
        raise EasyListError(
            f"No picture sub-folders were found in {pictures_dir}. Add one folder per item to search."
        )
    return picture_sets


def list_images(folder: Path) -> list[Path]:
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ]


def choose_search_image(images: list[Path], preferred_name: str = "") -> Path:
    if not images:
        raise EasyListError("No supported images were found in the item folder.")
    if preferred_name:
        for image in images:
            if image.name == preferred_name:
                return image
        raise EasyListError(f"Configured search image '{preferred_name}' was not found.")
    return images[0]


def encode_image(image_path: Path) -> str:
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def resolve_credentials(config: dict[str, Any]) -> tuple[str, str]:
    ebay_config = config["ebay"]
    client_id = os.environ.get("EBAY_CLIENT_ID", ebay_config.get("client_id", "")).strip()
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", ebay_config.get("client_secret", "")).strip()
    if not client_id or not client_secret:
        raise EasyListError(
            "Missing eBay API credentials. Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET or update config.json."
        )
    return client_id, client_secret


def _read_json_response(response: Any) -> dict[str, Any]:
    payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def _safe_error_summary(exc: error.HTTPError, fallback_message: str) -> str:
    details = exc.read().decode("utf-8", errors="replace")
    if details:
        try:
            payload = json.loads(details)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            summary = payload.get("error_description") or payload.get("message") or payload.get("error")
            if summary:
                return f"{fallback_message}: {summary}"
    return fallback_message


def _normalize_limit(limit: int | str) -> int:
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise EasyListError("eBay result_limit must be a positive integer.") from exc
    if normalized_limit < 1:
        raise EasyListError("eBay result_limit must be a positive integer.")
    return normalized_limit


def get_access_token(client_id: str, client_secret: str, scope: str) -> str:
    token_url = "https://api.ebay.com/identity/v1/oauth2/token"
    credentials = f"{client_id}:{client_secret}".encode("utf-8")
    auth_header = base64.b64encode(credentials).decode("ascii")
    body = parse.urlencode({"grant_type": "client_credentials", "scope": scope}).encode("utf-8")
    token_request = request.Request(
        token_url,
        data=body,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with request.urlopen(token_request) as response:
            token_response = _read_json_response(response)
    except error.HTTPError as exc:
        raise EasyListError(_safe_error_summary(exc, "Unable to get an eBay access token")) from exc
    except error.URLError as exc:
        raise EasyListError(f"Unable to get an eBay access token: {exc.reason}") from exc

    access_token = token_response.get("access_token", "")
    if not access_token:
        raise EasyListError("eBay token response did not include an access_token.")
    return access_token


def search_by_image(access_token: str, image_base64: str, marketplace_id: str, limit: int) -> dict[str, Any]:
    normalized_limit = _normalize_limit(limit)
    query_string = parse.urlencode({"limit": normalized_limit})
    search_url = f"https://api.ebay.com/buy/browse/v1/item_summary/search_by_image?{query_string}"
    search_request = request.Request(
        search_url,
        data=json.dumps({"image": image_base64}).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + access_token,
            "Content-Type": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        },
        method="POST",
    )
    try:
        with request.urlopen(search_request) as response:
            return _read_json_response(response)
    except error.HTTPError as exc:
        raise EasyListError(_safe_error_summary(exc, "eBay image search failed")) from exc
    except error.URLError as exc:
        raise EasyListError(f"eBay image search failed: {exc.reason}") from exc


def simplify_items(search_results: dict[str, Any]) -> list[dict[str, Any]]:
    items = search_results.get("itemSummaries") or []
    simplified = []
    for item in items:
        price = item.get("price") or {}
        simplified.append(
            {
                "item_id": item.get("itemId"),
                "title": item.get("title"),
                "price": price.get("value"),
                "currency": price.get("currency"),
                "item_web_url": item.get("itemWebUrl"),
                "condition": item.get("condition"),
                "image_url": (item.get("image") or {}).get("imageUrl"),
            }
        )
    return simplified


def calculate_target_price(price_value: str | float | int | None, percentage_less: float) -> float | None:
    if price_value in (None, ""):
        return None
    return round(float(price_value) * (1 - (percentage_less / 100.0)), 2)


def build_listing_plan(
    item_folder: Path,
    listing_images: list[Path],
    search_image: Path,
    matches: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    listing_config = config["listing"]
    percentage_less = float(listing_config["percentage_less"])
    closest_match = matches[0] if matches else None
    suggested_price = None
    if closest_match:
        suggested_price = calculate_target_price(closest_match.get("price"), percentage_less)

    return {
        "item_folder": item_folder.name,
        "search_image": str(search_image),
        "listing_images": [str(path) for path in listing_images],
        "closest_match": closest_match,
        "matches": matches,
        "recommended_listing": {
            "percentage_less": percentage_less,
            "suggested_price": suggested_price,
            "currency": (
                (closest_match or {}).get("currency") or listing_config.get("currency")
            ),
            "shipping_type": listing_config["shipping_type"],
            "weight_lbs": listing_config["weight_lbs"],
            "shipping_instructions": listing_config["shipping_instructions"],
            "sell_one_like_this_hint": (closest_match or {}).get("item_web_url"),
        },
    }


def run(config_path: Path, dry_run: bool = False) -> dict[str, Any]:
    config = load_config(config_path)
    pictures_dir = (config_path.parent / config["pictures"]["directory"]).resolve()
    preferred_name = config["pictures"].get("search_image_name", "")
    picture_sets = list_picture_sets(pictures_dir)

    if dry_run:
        dry_run_items = []
        for folder in picture_sets:
            images = list_images(folder)
            dry_run_items.append(
                {
                    "item_folder": folder.name,
                    "search_image": str(choose_search_image(images, preferred_name)),
                    "listing_images": [str(path) for path in images],
                }
            )
        return {
            "dry_run": True,
            "items": dry_run_items,
        }

    client_id, client_secret = resolve_credentials(config)
    access_token = get_access_token(client_id, client_secret, config["ebay"]["scope"])
    results = []
    for folder in picture_sets:
        images = list_images(folder)
        search_image = choose_search_image(images, preferred_name)
        search_results = search_by_image(
            access_token=access_token,
            image_base64=encode_image(search_image),
            marketplace_id=config["ebay"]["marketplace_id"],
            limit=config["ebay"]["result_limit"],
        )
        results.append(build_listing_plan(folder, images, search_image, simplify_items(search_results), config))

    return {"dry_run": False, "items": results}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search eBay by image for each item sub-folder and prepare pricing/shipping guidance."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON config file (default: config.json)",
    )
    parser.add_argument(
        "--output",
        default="search_results.json",
        help="Path to write the JSON output report (default: search_results.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and picture folders without calling the eBay API.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    output_path = Path(args.output).resolve()

    try:
        report = run(config_path, dry_run=args.dry_run)
    except EasyListError as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 1

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {len(report['items'])} item plan(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
