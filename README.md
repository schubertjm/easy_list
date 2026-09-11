# easy_list

Python helper to search eBay by image for user-provided item photos, then prepare pricing and shipping guidance for a later "sell one like this" workflow.

## Files
- `easy_list.py` - CLI script for image-searching eBay and writing a JSON report.
- `config.json` - editable config for eBay settings, shipping, weight, and percentage-less pricing.
- `pictures/` - add one sub-folder per item with the listing photos.

## Usage
1. Add item photos under `pictures/<item-name>/`.
2. Set `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`, or fill them into `config.json`.
3. Run a dry run first:
   ```bash
   python easy_list.py --dry-run
   ```
4. Run the live search:
   ```bash
   python easy_list.py --output search_results.json
   ```

The generated report includes the closest returned listing, a suggested lower price based on `percentage_less`, and the configured shipping instructions.

Each item entry in `search_results.json` includes:
- `item_folder` - the item sub-folder that was processed
- `search_image` - the image used for the eBay image search
- `listing_images` - all images found for the listing
- `closest_match` - the top returned eBay match
- `matches` - the returned similar listings
- `recommended_listing` - the lower target price plus shipping settings and the selected match URL
