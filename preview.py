#!/usr/bin/env python3
"""
Price-change preview / dry-run display logic.
Used by both subscription and one-time product updaters.
"""

from typing import Dict, List

from common import format_price_display, get_price_change_indicator


def print_price_changes_preview_generic(
    existing_configs_by_region: Dict[str, dict],
    new_configs: List[dict],
    enable_availability: bool,
    availability_key: str = "newSubscriberAvailability",
):
    """Print a detailed preview of price changes for dry run mode.

    Args:
        existing_configs_by_region: {regionCode: config_dict} from the current product.
        new_configs: list of merged configs to be applied.
        enable_availability: whether availability changes should be tracked.
        availability_key: the key name for availability in the config dicts.
    """
    new_regions = []
    price_changes = []
    availability_changes = []
    no_changes = []

    for config in new_configs:
        region_code = config.get("regionCode")
        new_price = config.get("price", {})
        new_avail = config.get(availability_key)

        existing = existing_configs_by_region.get(region_code)

        if not existing:
            new_regions.append({"region": region_code, "price": new_price, "availability": new_avail})
        else:
            old_price = existing.get("price", {})
            old_avail = existing.get(availability_key)

            price_changed = (
                old_price.get("currencyCode") != new_price.get("currencyCode")
                or old_price.get("units") != new_price.get("units")
                or old_price.get("nanos") != new_price.get("nanos")
            )
            avail_changed = enable_availability and old_avail != new_avail

            if price_changed:
                price_changes.append({
                    "region": region_code,
                    "old_price": old_price,
                    "new_price": new_price,
                    "availability_changed": avail_changed,
                    "new_availability": new_avail,
                })
            elif avail_changed:
                availability_changes.append({
                    "region": region_code,
                    "price": new_price,
                    "old_availability": old_avail,
                    "new_availability": new_avail,
                })
            else:
                no_changes.append({"region": region_code, "price": new_price})

    _print_change_summary(new_regions, price_changes, availability_changes, no_changes, len(new_configs))


def _print_change_summary(
    new_regions: list,
    price_changes: list,
    availability_changes: list,
    no_changes: list,
    total: int,
):
    print(f"\nSUMMARY:")
    print(f"  • New regions: {len(new_regions)}")
    print(f"  • Price changes: {len(price_changes)}")
    print(f"  • Availability changes: {len(availability_changes)}")
    print(f"  • No changes: {len(no_changes)}")
    print(f"  • Total regions: {total}")

    if new_regions:
        print(f"\n🆕 NEW REGIONS ({len(new_regions)}):")
        print(f"{'Region':<8} {'Price':<30} {'Availability':<25}")
        print("-" * 65)
        for item in sorted(new_regions, key=lambda x: x["region"]):
            price_str = format_price_display(item["price"], highlight=True, color="green")
            avail_str = item["availability"] or "Not set"
            print(f"{item['region']:<8} {price_str:<30} {avail_str:<25}")

    if price_changes:
        print(f"\n💰 PRICE CHANGES ({len(price_changes)}):")
        print(f"{'Region':<8} {'Old Price':<18} {'New Price':<30} {'Change':<8} {'Availability':<20}")
        print("-" * 90)
        for item in sorted(price_changes, key=lambda x: x["region"]):
            old_str = format_price_display(item["old_price"])
            new_str = format_price_display(item["new_price"], highlight=True, color="yellow")
            indicator = get_price_change_indicator(item["old_price"], item["new_price"])
            if item["availability_changed"]:
                avail_str = f"\033[36m→ {(item['new_availability'] or '')[:15]}\033[0m"
            else:
                avail_str = "No change"
            print(f"{item['region']:<8} {old_str:<18} {new_str:<30} {indicator:<8} {avail_str:<20}")

    if availability_changes:
        print(f"\n🌍 AVAILABILITY CHANGES ({len(availability_changes)}):")
        print(f"{'Region':<8} {'Price':<20} {'Old Availability':<25} {'New Availability':<25}")
        print("-" * 80)
        for item in sorted(availability_changes, key=lambda x: x["region"]):
            price_str = format_price_display(item["price"])
            old_a = item["old_availability"] or "Not set"
            new_a = item["new_availability"] or "Not set"
            print(f"{item['region']:<8} {price_str:<20} {old_a:<25} {new_a:<25}")

    if no_changes and len(no_changes) <= 10:
        print(f"\n✅ NO CHANGES ({len(no_changes)}):")
        print(f"{'Region':<8} {'Current Price':<20}")
        print("-" * 30)
        for item in sorted(no_changes, key=lambda x: x["region"]):
            print(f"{item['region']:<8} {format_price_display(item['price']):<20}")
    elif no_changes:
        print(f"\n✅ NO CHANGES: {len(no_changes)} regions will remain unchanged")

    if price_changes or new_regions:
        print(f"\n" + "🔍 CHANGE HIGHLIGHTS".center(80, "="))
        if new_regions:
            print(f"\n✨ Adding {len(new_regions)} new regions:")
            for item in sorted(new_regions[:5], key=lambda x: x["region"]):
                ps = format_price_display(item["price"], highlight=True, color="green")
                print(f"   {item['region']}: {ps}")
            if len(new_regions) > 5:
                print(f"   ... and {len(new_regions) - 5} more")
        if price_changes:
            increases = [i for i in price_changes if get_price_change_indicator(i["old_price"], i["new_price"]) == " 📈"]
            decreases = [i for i in price_changes if get_price_change_indicator(i["old_price"], i["new_price"]) == " 📉"]
            currency_only = [i for i in price_changes if get_price_change_indicator(i["old_price"], i["new_price"]) == " 🔄"]
            for label, emoji, items in [
                ("Price increases", "📈", increases),
                ("Price decreases", "📉", decreases),
                ("Currency changes", "🔄", currency_only),
            ]:
                if items:
                    print(f"\n{emoji} {label} ({len(items)}):")
                    for item in sorted(items[:5], key=lambda x: x["region"]):
                        o = format_price_display(item["old_price"])
                        n = format_price_display(item["new_price"], highlight=True, color="yellow")
                        print(f"   {item['region']}: {o} → {n}")
                    if len(items) > 5:
                        print(f"   ... and {len(items) - 5} more")
        print("=" * 80)

    print(f"\n💡 To apply these changes, run the same command with --apply")
