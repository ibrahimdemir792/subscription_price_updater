#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import pycountry
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"


def load_config(config_path: str = "config.json") -> Dict:
    """Load configuration from JSON file with fallback defaults."""
    default_config = {
        "package_name": None,
        "product_id": "subscription-product",
        "base_plan_id": "monthly-plan", 
        "service_account_path": "service-account.json",
        "default_csv_path": "prices.csv",
        "regions_version": "2025/01",
        "defaults": {
            "fix_currency": False,
            "convert_currency": False,
            "use_recommended": False,
            "batch_size": 0,
            "enable_availability": False
        }
    }
    
    if not os.path.exists(config_path):
        print(f"Configuration file '{config_path}' not found.")
        print("Run 'python setup.py' to create one, or specify all required arguments.")
        return default_config
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Merge with defaults
        for key, value in default_config.items():
            if key not in config:
                config[key] = value
            elif key == "defaults" and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if sub_key not in config[key]:
                        config[key][sub_key] = sub_value
        
        return config
    except Exception as e:
        print(f"Error loading configuration: {e}")
        print("Using default configuration.")
        return default_config


@dataclass
class RegionalPrice:
    region_iso2: str
    currency_code: str
    units: str
    nanos: int


def read_csv_prices(csv_path: str) -> List[Dict[str, str]]:
    """Read and validate CSV price file."""
    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required = {"Countries or Regions", "Currency Code", "Price"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(
                    f"CSV is missing required columns: {', '.join(sorted(missing))}. "
                    f"Present columns: {reader.fieldnames or []}"
                )
            rows: List[Dict[str, str]] = []
            for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
                if not row.get("Countries or Regions", "").strip():
                    continue
                if not row.get("Price", "").strip():
                    continue
                
                # Validate price format
                price_str = row.get("Price", "").strip()
                try:
                    price_val = float(price_str)
                    if price_val < 0:
                        print(f"Warning: Negative price in row {row_num}: {price_str}")
                        continue
                except ValueError:
                    print(f"Warning: Invalid price format in row {row_num}: '{price_str}' - skipping")
                    continue
                
                # Validate currency code
                currency = row.get("Currency Code", "").strip().upper()
                if len(currency) != 3:
                    print(f"Warning: Invalid currency code in row {row_num}: '{currency}' - should be 3 letters")
                
                rows.append(row)
                
            if not rows:
                raise ValueError("No valid data rows found in CSV file")
                
        return rows
    except FileNotFoundError:
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    except Exception as e:
        raise ValueError(f"Error reading CSV file: {e}")


def map_iso3_to_iso2(iso3: str) -> Optional[str]:
    if not iso3:
        return None
    iso3 = iso3.strip().upper()
    overrides = {
        # Kosovo (not officially ISO 3166-1, Play commonly uses XK)
        "XKS": "XK",
    }
    if iso3 in overrides:
        return overrides[iso3]
    try:
        country = pycountry.countries.get(alpha_3=iso3)
        if country and hasattr(country, "alpha_2"):
            return country.alpha_2
    except Exception:
        pass
    return None


def convert_price_to_units_nanos(price_str: str) -> (str, int):
    price = Decimal(price_str)
    if price < 0:
        raise ValueError("Price cannot be negative")
    # Split into integral units and fractional nanos (9 decimal places)
    units_part = price.to_integral_value(rounding=ROUND_DOWN)
    fractional = (price - units_part).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    nanos = int((fractional * Decimal(10 ** 9)))
    return str(int(units_part)), nanos


def build_regional_prices(rows: List[Dict[str, str]]) -> List[RegionalPrice]:
    regional_prices: List[RegionalPrice] = []
    for row in rows:
        iso3 = row.get("Countries or Regions", "").strip()
        iso2 = map_iso3_to_iso2(iso3)
        if not iso2:
            print(f"Skipping row with unknown ISO3 '{iso3}'", file=sys.stderr)
            continue
        currency = row.get("Currency Code", "").strip().upper()
        price_str = row.get("Price", "").strip()
        if not currency or not price_str:
            continue
        units, nanos = convert_price_to_units_nanos(price_str)
        regional_prices.append(RegionalPrice(iso2, currency, units, nanos))
    return regional_prices


def build_regional_price_migrations(new_prices: List[RegionalPrice]) -> List[dict]:
    migrations: List[dict] = []
    for rp in new_prices:
        migrations.append(
            {
                "regionCode": rp.region_iso2,
                "price": {
                    "currencyCode": rp.currency_code,
                    "units": rp.units,
                    "nanos": rp.nanos,
                },
            }
        )
    return migrations


def fetch_regions_version(service, package_name: str) -> Optional[dict]:
    """Fetch current RegionsVersion via convertRegionPrices.

    The endpoint requires a Money input; we use a trivial USD 1.00 request.
    Only the regionsVersion from the response is used.
    """
    try:
        resp = (
            service.monetization()
            .convertRegionPrices(
                packageName=package_name,
                body={
                    "price": {
                        "currencyCode": "USD",
                        "units": "1",
                        "nanos": 0,
                    }
                },
            )
            .execute()
        )
        if isinstance(resp, dict) and resp.get("regionsVersion") is not None:
            return resp.get("regionsVersion")
    except HttpError:
        # If unavailable, caller will proceed without and may hit API validation
        return None
    return None


def fetch_billable_regions_and_currencies(service, package_name: str) -> Dict[str, str]:
    """Return mapping of region_code -> currency_code for billable regions.

    Uses convertRegionPrices as the source of truth.
    """
    mapping: Dict[str, str] = {}
    try:
        resp = (
            service.monetization()
            .convertRegionPrices(
                packageName=package_name,
                body={
                    "price": {
                        "currencyCode": "USD",
                        "units": "1",
                        "nanos": 0,
                    }
                },
            )
            .execute()
        )
        converted = resp.get("convertedRegionPrices") or {}
        for region_code, data in converted.items():
            price = data.get("price") or {}
            currency = price.get("currencyCode")
            if region_code and currency:
                mapping[region_code] = currency
    except HttpError:
        return {}
    return mapping


def convert_amount(
    service,
    package_name: str,
    amount_units: str,
    amount_nanos: int,
    source_currency: str,
    target_region: str,
) -> Optional[dict]:
    """Convert a price in source currency to target region's local currency using convertRegionPrices.

    Returns a Money dict {currencyCode, units, nanos} in the region's currency.
    """
    try:
        resp = (
            service.monetization()
            .convertRegionPrices(
                packageName=package_name,
                body={
                    "price": {
                        "currencyCode": source_currency,
                        "units": amount_units,
                        "nanos": amount_nanos,
                    }
                },
            )
            .execute()
        )
        converted = (resp.get("convertedRegionPrices") or {}).get(target_region)
        if converted and isinstance(converted.get("price"), dict):
            return converted.get("price")
    except HttpError:
        return None
    return None


def authenticate(service_account_path: str):
    credentials = service_account.Credentials.from_service_account_file(
        service_account_path, scopes=[ANDROID_PUBLISHER_SCOPE]
    )
    service = build("androidpublisher", "v3", credentials=credentials, cache_discovery=False)
    return service


def format_price_display(price_dict: dict, highlight: bool = False, color: str = None) -> str:
    """Format a price dictionary for display."""
    if not price_dict:
        return "N/A"
    
    currency = price_dict.get("currencyCode", "")
    units = price_dict.get("units", "0")
    nanos = price_dict.get("nanos", 0)
    
    # Convert nanos to decimal places
    decimal_part = f"{nanos:09d}".rstrip('0') or '0'
    if decimal_part == '0':
        price_str = f"{units} {currency}"
    else:
        price_str = f"{units}.{decimal_part} {currency}"
    
    # Add highlighting for changes
    if highlight:
        if color == "green":
            return f"\033[32m→ {price_str} ←\033[0m"  # Green for new
        elif color == "yellow":
            return f"\033[33m→ {price_str} ←\033[0m"  # Yellow for changes
        else:
            return f"→ {price_str} ←"
    else:
        return price_str


def get_price_change_indicator(old_price: dict, new_price: dict) -> str:
    """Generate a visual indicator for price changes."""
    if not old_price or not new_price:
        return ""
    
    old_units = float(old_price.get("units", "0"))
    old_nanos = old_price.get("nanos", 0)
    old_total = old_units + (old_nanos / 1_000_000_000)
    
    new_units = float(new_price.get("units", "0"))
    new_nanos = new_price.get("nanos", 0)
    new_total = new_units + (new_nanos / 1_000_000_000)
    
    if new_total > old_total:
        return " 📈"  # Price increase
    elif new_total < old_total:
        return " 📉"  # Price decrease
    else:
        return " 🔄"  # Currency change only


def print_price_changes_preview(base_plan: dict, new_configs: List[dict], enable_availability: bool):
    """Print a detailed preview of price changes for dry run mode."""
    existing_configs = {rc.get("regionCode"): rc for rc in base_plan.get("regionalConfigs", []) if rc.get("regionCode")}
    
    # Group changes by type
    new_regions = []
    price_changes = []
    availability_changes = []
    no_changes = []
    
    for config in new_configs:
        region_code = config.get("regionCode")
        new_price = config.get("price", {})
        new_availability = config.get("newSubscriberAvailability")
        
        existing_config = existing_configs.get(region_code)
        
        if not existing_config:
            # New region
            new_regions.append({
                "region": region_code,
                "price": new_price,
                "availability": new_availability
            })
        else:
            existing_price = existing_config.get("price", {})
            existing_availability = existing_config.get("newSubscriberAvailability")
            
            # Check for price changes
            price_changed = (
                existing_price.get("currencyCode") != new_price.get("currencyCode") or
                existing_price.get("units") != new_price.get("units") or
                existing_price.get("nanos") != new_price.get("nanos")
            )
            
            # Check for availability changes
            availability_changed = enable_availability and existing_availability != new_availability
            
            if price_changed:
                price_changes.append({
                    "region": region_code,
                    "old_price": existing_price,
                    "new_price": new_price,
                    "availability_changed": availability_changed,
                    "new_availability": new_availability
                })
            elif availability_changed:
                availability_changes.append({
                    "region": region_code,
                    "price": new_price,
                    "old_availability": existing_availability,
                    "new_availability": new_availability
                })
            else:
                no_changes.append({
                    "region": region_code,
                    "price": new_price
                })
    
    # Print summary
    print(f"\nSUMMARY:")
    print(f"  • New regions: {len(new_regions)}")
    print(f"  • Price changes: {len(price_changes)}")
    print(f"  • Availability changes: {len(availability_changes)}")
    print(f"  • No changes: {len(no_changes)}")
    print(f"  • Total regions: {len(new_configs)}")
    
    # Print new regions
    if new_regions:
        print(f"\n🆕 NEW REGIONS ({len(new_regions)}):")
        print(f"{'Region':<8} {'Price':<30} {'Availability':<25}")
        print("-" * 65)
        for item in sorted(new_regions, key=lambda x: x["region"]):
            price_str = format_price_display(item["price"], highlight=True, color="green")
            availability_str = item["availability"] or "Not set"
            print(f"{item['region']:<8} {price_str:<30} {availability_str:<25}")
    
    # Print price changes
    if price_changes:
        print(f"\n💰 PRICE CHANGES ({len(price_changes)}):")
        print(f"{'Region':<8} {'Old Price':<18} {'New Price':<30} {'Change':<8} {'Availability':<20}")
        print("-" * 90)
        for item in sorted(price_changes, key=lambda x: x["region"]):
            old_price_str = format_price_display(item["old_price"])
            new_price_str = format_price_display(item["new_price"], highlight=True, color="yellow")
            change_indicator = get_price_change_indicator(item["old_price"], item["new_price"])
            
            if item["availability_changed"]:
                availability_str = f"\033[36m→ {item['new_availability'][:15]}\033[0m"  # Cyan for availability change
            else:
                availability_str = "No change"
            
            print(f"{item['region']:<8} {old_price_str:<18} {new_price_str:<30} {change_indicator:<8} {availability_str:<20}")
    
    # Print availability-only changes
    if availability_changes:
        print(f"\n🌍 AVAILABILITY CHANGES ({len(availability_changes)}):")
        print(f"{'Region':<8} {'Price':<20} {'Old Availability':<25} {'New Availability':<25}")
        print("-" * 80)
        for item in sorted(availability_changes, key=lambda x: x["region"]):
            price_str = format_price_display(item["price"])
            old_avail = item["old_availability"] or "Not set"
            new_avail = item["new_availability"] or "Not set"
            print(f"{item['region']:<8} {price_str:<20} {old_avail:<25} {new_avail:<25}")
    
    # Print regions with no changes (only if there are some)
    if no_changes and len(no_changes) <= 10:  # Only show if reasonably small list
        print(f"\n✅ NO CHANGES ({len(no_changes)}):")
        print(f"{'Region':<8} {'Current Price':<20}")
        print("-" * 30)
        for item in sorted(no_changes, key=lambda x: x["region"]):
            price_str = format_price_display(item["price"])
            print(f"{item['region']:<8} {price_str:<20}")
    elif no_changes:
        print(f"\n✅ NO CHANGES: {len(no_changes)} regions will remain unchanged")
    
    # Print highlighted summary of key changes
    if price_changes or new_regions:
        print(f"\n" + "🔍 CHANGE HIGHLIGHTS".center(80, "="))
        
        if new_regions:
            print(f"\n✨ Adding {len(new_regions)} new regions:")
            for item in sorted(new_regions[:5], key=lambda x: x["region"]):  # Show first 5
                price_str = format_price_display(item["price"], highlight=True, color="green")
                print(f"   {item['region']}: {price_str}")
            if len(new_regions) > 5:
                print(f"   ... and {len(new_regions) - 5} more")
        
        if price_changes:
            increases = [item for item in price_changes if get_price_change_indicator(item["old_price"], item["new_price"]) == " 📈"]
            decreases = [item for item in price_changes if get_price_change_indicator(item["old_price"], item["new_price"]) == " 📉"]
            currency_only = [item for item in price_changes if get_price_change_indicator(item["old_price"], item["new_price"]) == " 🔄"]
            
            if increases:
                print(f"\n📈 Price increases ({len(increases)}):")
                for item in sorted(increases[:5], key=lambda x: x["region"]):
                    old_str = format_price_display(item["old_price"])
                    new_str = format_price_display(item["new_price"], highlight=True, color="yellow")
                    print(f"   {item['region']}: {old_str} → {new_str}")
                if len(increases) > 5:
                    print(f"   ... and {len(increases) - 5} more")
            
            if decreases:
                print(f"\n📉 Price decreases ({len(decreases)}):")
                for item in sorted(decreases[:5], key=lambda x: x["region"]):
                    old_str = format_price_display(item["old_price"])
                    new_str = format_price_display(item["new_price"], highlight=True, color="yellow")
                    print(f"   {item['region']}: {old_str} → {new_str}")
                if len(decreases) > 5:
                    print(f"   ... and {len(decreases) - 5} more")
            
            if currency_only:
                print(f"\n🔄 Currency changes ({len(currency_only)}):")
                for item in sorted(currency_only[:5], key=lambda x: x["region"]):
                    old_str = format_price_display(item["old_price"])
                    new_str = format_price_display(item["new_price"], highlight=True, color="yellow")
                    print(f"   {item['region']}: {old_str} → {new_str}")
                if len(currency_only) > 5:
                    print(f"   ... and {len(currency_only) - 5} more")
        
        print("=" * 80)
    
    print(f"\n💡 To apply these changes, run the same command with --apply")


def clamp_config_from_error_message(error_message: str, merged_configs: List[dict]) -> bool:
    """Parse error like:
    "Price for CI must be between F CFA 30 and F CFA 627,341, found F CFA 27"
    and clamp the CI config to the minimum in merged_configs. Returns True if adjusted.
    """
    import re

    # Try to capture region and numeric bounds
    m = re.search(r"Price for\s+([A-Z]{2})\s+must be between\s+(.+?)\s+and\s+(.+?),\s+found\s+(.+)$", error_message)
    if not m:
        return False
    region = m.group(1)
    min_str = m.group(2)
    max_str = m.group(3)
    found_str = m.group(4)

    # Extract first numeric token (handles thousands separators and unicode spaces)
    normalized_min = min_str.replace('\u202f', ' ').replace('\xa0', ' ')
    normalized_max = max_str.replace('\u202f', ' ').replace('\xa0', ' ')
    normalized_found = found_str.replace('\u202f', ' ').replace('\xa0', ' ')
    num_min = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", normalized_min)
    num_max = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", normalized_max)
    num_found = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", normalized_found)
    if not num_min or not num_max or not num_found:
        return False
    raw_min = num_min.group(1).replace(',', '.')
    raw_max = num_max.group(1).replace(',', '.')
    raw_found = num_found.group(1).replace(',', '.')
    try:
        from decimal import Decimal
        min_value = Decimal(raw_min)
        max_value = Decimal(raw_max)
        found_value = Decimal(raw_found)
    except Exception:
        return False

    # Locate the region config to adjust
    for cfg in merged_configs:
        if cfg.get("regionCode") == region and isinstance(cfg.get("price"), dict):
            target = min_value if found_value < min_value else (max_value if found_value > max_value else found_value)
            units_part = int(target.to_integral_value(rounding=ROUND_DOWN))
            fractional = (target - units_part).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
            nanos = int((fractional * Decimal(10 ** 9)))
            cfg["price"]["units"] = str(units_part)
            cfg["price"]["nanos"] = nanos
            return True
    return False


def remove_region_from_configs(error_message: str, merged_configs: List[dict]) -> Optional[str]:
    """Parse region code from error and remove it from merged_configs.
    Returns the removed region code if successful.
    """
    import re
    m = re.search(r"Region code\s+([A-Z]{2})\b", error_message)
    if not m:
        m = re.search(r"Price for\s+([A-Z]{2})\b", error_message)
    if not m:
        return None
    region = m.group(1)
    before = len(merged_configs)
    merged_configs[:] = [cfg for cfg in merged_configs if cfg.get("regionCode") != region]
    return region if len(merged_configs) < before else None


def get_base_plan(service, package_name: str, product_id: str, base_plan_id: str) -> Optional[dict]:
    # Prefer the newer dedicated BasePlan GET if available
    try:
        return (
            service.monetization()
            .subscriptions()
            .basePlans()
            .get(
                packageName=package_name,
                productId=product_id,
                basePlanId=base_plan_id,
            )
            .execute()
        )
    except AttributeError:
        pass
    except HttpError as e:
        if e.resp is not None and e.resp.status == 404:
            # Fall through to subscription-level lookup
            pass
        else:
            raise

    # Fallback: fetch whole subscription and filter client-side
    subscription = (
        service.monetization().subscriptions().get(packageName=package_name, productId=product_id).execute()
    )
    for bp in subscription.get("basePlans", []):
        if bp.get("basePlanId") == base_plan_id:
            return bp
    return None


def merge_regional_configs(
    existing_base_plan: dict,
    new_prices: List[RegionalPrice],
    enable_availability: bool = False,
) -> List[dict]:
    existing = existing_base_plan.get("regionalConfigs", []) or []
    by_region: Dict[str, dict] = {rc.get("regionCode"): rc for rc in existing if rc.get("regionCode")}
    for rp in new_prices:
        # Start from any existing regional config to preserve fields we are not managing here
        preserved = dict(by_region.get(rp.region_iso2, {}))
        preserved["regionCode"] = rp.region_iso2
        preserved["price"] = {
            "currencyCode": rp.currency_code,
            "units": rp.units,
            "nanos": rp.nanos,
        }
        if enable_availability:
            preserved["newSubscriberAvailability"] = "NEW_SUBSCRIBERS_CAN_PURCHASE"
        by_region[rp.region_iso2] = preserved
    # Return sorted by regionCode for deterministic output
    merged = [by_region[k] for k in sorted(by_region.keys())]
    return merged


def patch_base_plan_regional_configs(
    service,
    package_name: str,
    product_id: str,
    base_plan_id: str,
    merged_regional_configs: List[dict],
    regions_version: Optional[dict] = None,
):
    # Subscription-level PATCH. We must send the full subscription with updated basePlans
    subscription = (
        service.monetization().subscriptions().get(packageName=package_name, productId=product_id).execute()
    )
    found = False
    new_base_plans: List[dict] = []
    # Use provided regions_version, or fetch if missing on base plan in the subscription payload
    fallback_regions_version = regions_version or fetch_regions_version(service, package_name)
    for bp in subscription.get("basePlans", []):
        if bp.get("basePlanId") == base_plan_id:
            found = True
            bp = dict(bp)  # shallow copy
            bp["regionalConfigs"] = merged_regional_configs
        new_base_plans.append(bp)
    if not found:
        raise RuntimeError(
            f"Base plan '{base_plan_id}' not found under subscription '{product_id}'."
        )

    subscription_body = {
        **{k: v for k, v in subscription.items() if k != "basePlans"},
        "basePlans": new_base_plans,
    }
    regions_version_str = None
    if isinstance(fallback_regions_version, dict):
        regions_version_str = fallback_regions_version.get("version")
    elif isinstance(fallback_regions_version, str):
        regions_version_str = fallback_regions_version
    req = (
        service.monetization()
        .subscriptions()
        .patch(
            packageName=package_name,
            productId=product_id,
            updateMask="basePlans",
            body=subscription_body,
        )
    )
    if regions_version_str:
        sep = '&' if '?' in req.uri else '?'
        req.uri = f"{req.uri}{sep}regionsVersion.version={quote(regions_version_str)}"
    return req.execute()


# Removed migratePrices flows: those are for migrating legacy cohorts, not setting new prices.


def main():
    # Load configuration first
    parser = argparse.ArgumentParser(description="Update Google Play base plan regional prices from CSV")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    
    # Parse config argument first to load settings
    config_args, remaining_args = parser.parse_known_args()
    config = load_config(config_args.config)
    
    # Now set up the full parser with config defaults
    parser = argparse.ArgumentParser(description="Update Google Play base plan regional prices from CSV")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    parser.add_argument(
        "--package-name", 
        default=config.get("package_name"), 
        required=config.get("package_name") is None,
        help="Android app package name (e.g., com.example.app)"
    )
    parser.add_argument(
        "--product-id", 
        default=config.get("product_id"), 
        help=f"Subscription productId (default: {config.get('product_id')})"
    )
    parser.add_argument(
        "--base-plan-id", 
        default=config.get("base_plan_id"), 
        help=f"Base plan ID (default: {config.get('base_plan_id')})"
    )
    parser.add_argument(
        "--csv", 
        default=config.get("default_csv_path"), 
        help=f"Path to CSV (default: {config.get('default_csv_path')})"
    )
    parser.add_argument(
        "--service-account",
        default=config.get("service_account_path"),
        help=f"Path to service account JSON key (default: {config.get('service_account_path')})",
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes (otherwise dry-run)")
    parser.add_argument(
        "--fix-currency",
        action="store_true" if config.get("defaults", {}).get("fix_currency") else "store_false",
        default=config.get("defaults", {}).get("fix_currency", False),
        help="If a region's required currency differs from CSV, replace with required currency",
    )
    parser.add_argument(
        "--convert-currency",
        action="store_true" if config.get("defaults", {}).get("convert_currency") else "store_false",
        default=config.get("defaults", {}).get("convert_currency", False),
        help="When fixing currency, also convert the numeric price using Google convertRegionPrices",
    )
    parser.add_argument(
        "--use-recommended",
        action="store_true" if config.get("defaults", {}).get("use_recommended") else "store_false",
        default=config.get("defaults", {}).get("use_recommended", False),
        help="Replace CSV prices with Google recommended per-region prices from convertRegionPrices",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=config.get("defaults", {}).get("batch_size", 0),
        help="Apply prices in chunks of this size (0 = single request)",
    )
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="Also migrate legacy cohorts to the new price using batchMigratePrices",
    )
    parser.add_argument(
        "--migrate-cutoff",
        help="ISO8601 timestamp for oldestAllowedPriceVersionTime (e.g., 2025-09-01T00:00:00Z)",
    )
    parser.add_argument(
        "--migrate-increase-type",
        choices=["PRICE_INCREASE_TYPE_OPT_IN", "PRICE_INCREASE_TYPE_OPT_OUT"],
        help="Price increase type for migration",
    )
    parser.add_argument(
        "--regions-version", 
        default=config.get("regions_version"),
        help=f"Explicit regionsVersion.version string required by Google Play when updating prices (default: {config.get('regions_version')})",
    )
    parser.add_argument(
        "--enable-availability",
        action="store_true" if config.get("defaults", {}).get("enable_availability") else "store_false",
        default=config.get("defaults", {}).get("enable_availability", False),
        help="Also set newSubscriberAvailability=NEW_SUBSCRIBERS_CAN_PURCHASE for updated regions",
    )
    args = parser.parse_args()
    
    # Validate required configuration
    if not args.package_name:
        print("Error: package-name is required.")
        print("Either specify it with --package-name or add it to your config.json file.")
        print("Run 'python setup.py' to create a configuration file.")
        sys.exit(1)
    
    # Validate file paths
    csv_path = os.path.abspath(args.csv)
    if not os.path.exists(csv_path):
        print(f"Error: CSV file '{csv_path}' not found.")
        sys.exit(1)
    
    sa_path = os.path.abspath(args.service_account)
    if not os.path.exists(sa_path):
        print(f"Error: Service account file '{sa_path}' not found.")
        sys.exit(1)

    # Read and validate CSV
    try:
        rows = read_csv_prices(csv_path)
        regional_prices = build_regional_prices(rows)
        if not regional_prices:
            print("No valid pricing data found in CSV.")
            sys.exit(1)
    except (ValueError, FileNotFoundError) as e:
        print(f"CSV Error: {e}")
        sys.exit(1)

    # Report summary
    print(f"Read {len(regional_prices)} regional prices from CSV '{csv_path}'.")
    print("Examples:")
    for example in regional_prices[:5]:
        print(
            f"  {example.region_iso2}: {example.units}.{str(example.nanos).zfill(9)} {example.currency_code}"
        )

    # Authenticate and fetch base plan
    try:
        service = authenticate(sa_path)
        print(f"✓ Authenticated with Google Play Console")
    except Exception as e:
        print(f"Authentication Error: {e}")
        print("Please check your service account file and permissions.")
        sys.exit(1)
    
    try:
        base_plan = get_base_plan(service, args.package_name, args.product_id, args.base_plan_id)
        if not base_plan:
            print(f"Error: Base plan '{args.base_plan_id}' not found for product '{args.product_id}'.")
            print("Please check your product ID and base plan ID in the Google Play Console.")
            sys.exit(1)
        print(f"✓ Found base plan '{args.base_plan_id}' for product '{args.product_id}'")
    except Exception as e:
        print(f"API Error: {e}")
        print("Please check your package name, product ID, and base plan ID.")
        sys.exit(1)

    # Filter out regions not billable at the current regions version
    region_currency_map = fetch_billable_regions_and_currencies(service, args.package_name)
    recommended_prices_by_region: Dict[str, dict] = {}
    if args.use_recommended and region_currency_map:
        # Fetch recommended prices using a USD 1.00 anchor, then scale to CSV magnitude per region
        try:
            rec_resp = (
                service.monetization()
                .convertRegionPrices(
                    packageName=args.package_name,
                    body={"price": {"currencyCode": "USD", "units": "1", "nanos": 0}},
                )
                .execute()
            )
            recommended = rec_resp.get("convertedRegionPrices") or {}
            for region_code, data in recommended.items():
                price = data.get("price") or {}
                if price.get("currencyCode"):
                    recommended_prices_by_region[region_code] = price
        except HttpError:
            print("Warning: Could not fetch recommended prices; proceeding with CSV values.")
    if not region_currency_map:
        print("Warning: Could not fetch billable region list; proceeding without filtering.")
        filtered_regional_prices = regional_prices
    else:
        billable_regions = set(region_currency_map.keys())
        filtered_regional_prices = [rp for rp in regional_prices if rp.region_iso2 in billable_regions]
        # If using recommended, override price fields per region
        if args.use_recommended and recommended_prices_by_region:
            for rp in filtered_regional_prices:
                rec = recommended_prices_by_region.get(rp.region_iso2)
                if rec and rec.get("currencyCode") == region_currency_map.get(rp.region_iso2):
                    rp.currency_code = rec.get("currencyCode")
                    rp.units = str(int(rec.get("units") or 0))
                    rp.nanos = int(rec.get("nanos") or 0)
        skipped = [rp for rp in regional_prices if rp.region_iso2 not in billable_regions]
        if skipped:
            skipped_codes = ", ".join(sorted({rp.region_iso2 for rp in skipped}))
            print(f"Skipping {len(skipped)} non-billable regions at this version: {skipped_codes}")
        # Handle currency mismatches
        mismatched_rps = [
            rp for rp in filtered_regional_prices
            if region_currency_map.get(rp.region_iso2) and region_currency_map.get(rp.region_iso2) != rp.currency_code
        ]
        if mismatched_rps:
            if args.fix_currency:
                action = "Fixing"
                if args.convert_currency:
                    action = "Fixing currency and converting amount"
                print(f"{action} to match region requirements:")
                for rp in mismatched_rps:
                    required = region_currency_map.get(rp.region_iso2)
                    old_curr = rp.currency_code
                    if args.convert_currency:
                        converted = convert_amount(
                            service,
                            args.package_name,
                            rp.units,
                            rp.nanos,
                            old_curr,
                            rp.region_iso2,
                        )
                        if converted:
                            rp.currency_code = converted.get("currencyCode", required)
                            rp.units = str(int(converted.get("units") or 0))
                            rp.nanos = int(converted.get("nanos") or 0)
                            print(f"  - {rp.region_iso2}: {old_curr} -> {rp.currency_code} (converted)")
                        else:
                            rp.currency_code = required
                            print(f"  - {rp.region_iso2}: {old_curr} -> {required} (fallback no conversion)")
                    else:
                        rp.currency_code = required
                        print(f"  - {rp.region_iso2}: {old_curr} -> {required}")
            else:
                print("Skipping regions with currency mismatches (use --fix-currency to auto-correct):")
                for rp in mismatched_rps[:20]:
                    required = region_currency_map.get(rp.region_iso2)
                    print(f"  - {rp.region_iso2}: CSV {rp.currency_code} vs required {required}")
                if len(mismatched_rps) > 20:
                    print(f"  ... and {len(mismatched_rps) - 20} more")
                filtered_regional_prices = [rp for rp in filtered_regional_prices if rp not in mismatched_rps]

    if not filtered_regional_prices:
        print("No billable regions left after filtering; aborting.")
        sys.exit(2)

    merged_regional_configs = merge_regional_configs(
        base_plan,
        filtered_regional_prices,
        enable_availability=args.enable_availability,
    )
    print(f"Prepared {len(merged_regional_configs)} merged regional configs.")
    regions_version = base_plan.get("regionsVersion") if isinstance(base_plan, dict) else None
    if args.regions_version:
        regions_version = {"version": args.regions_version}
    if regions_version is None:
        regions_version = fetch_regions_version(service, args.package_name)
    rv_str = regions_version.get("version") if isinstance(regions_version, dict) else regions_version
    print(f"Using regionsVersion: {rv_str if rv_str else 'None'}")

    if not args.apply:
        print("\n" + "="*80)
        print("DRY RUN - PREVIEW OF CHANGES")
        print("="*80)
        print_price_changes_preview(base_plan, merged_regional_configs, args.enable_availability)
        print("="*80)
        print("Dry-run: no changes applied. Use --apply to perform the update.")
        return

    try:
        # Apply via subscriptions.patch with regionsVersion.version query param
        def apply_chunk(configs: List[dict]):
            return patch_base_plan_regional_configs(
                service,
                args.package_name,
                args.product_id,
                args.base_plan_id,
                configs,
                regions_version=regions_version,
            )

        if args.batch_size and args.batch_size > 0:
            total = len(merged_regional_configs)
            print(f"Applying in batches of {args.batch_size} (total {total})...")
            start = 0
            while start < total:
                end = min(start + args.batch_size, total)
                chunk = merged_regional_configs[start:end]
                print(f"Applying {start+1}-{end}...")
                apply_chunk(chunk)
                start = end
            resp = {"basePlanId": args.base_plan_id}
        else:
            resp = apply_chunk(merged_regional_configs)
        # Print minimal confirmation to avoid dumping large response
        if isinstance(resp, dict):
            if "basePlanId" in resp:
                print(f"Updated base plan '{resp['basePlanId']}'.")
            else:
                print("Update applied.")
        else:
            print("Update applied.")
        # Optionally migrate existing cohorts after success
        if args.migrate_existing:
            try:
                from datetime import datetime
                if not args.migrate_cutoff:
                    raise RuntimeError("--migrate-cutoff is required for --migrate-existing")
                cutoff_iso = args.migrate_cutoff
                increase_type = args.migrate_increase_type or "PRICE_INCREASE_TYPE_OPT_IN"
                # Build requests array: one per region we updated
                requests = []
                for cfg in merged_regional_configs:
                    requests.append(
                        {
                            "packageName": args.package_name,
                            "productId": args.product_id,
                            "basePlanId": args.base_plan_id,
                            "regionsVersion": regions_version,
                            "regionalPriceMigrations": [
                                {
                                    "regionCode": cfg.get("regionCode"),
                                    "oldestAllowedPriceVersionTime": cutoff_iso,
                                    "priceIncreaseType": increase_type,
                                }
                            ],
                            "latencyTolerance": "PRODUCT_UPDATE_LATENCY_TOLERANCE_LATENCY_TOLERANT",
                        }
                    )
                if requests:
                    print(f"Migrating existing cohorts for {len(requests)} regions...")
                    service.monetization().subscriptions().basePlans().batchMigratePrices(
                        packageName=args.package_name,
                        productId=args.product_id,
                        body={"requests": requests},
                    ).execute()
                    print("Migration requests submitted.")
            except Exception as me:
                print(f"Warning: migrate-existing failed: {me}")
    except HttpError as e:
        # Attempt to clamp if price too low/high error
        details_text = None
        try:
            details = json.loads(e.content.decode("utf-8"))
            details_text = details.get("error", {}).get("message")
        except Exception:
            details = {"error": str(e)}
            details_text = str(e)
        if details_text and "must be between" in details_text:
            adjusted = clamp_config_from_error_message(details_text, merged_regional_configs)
            if adjusted:
                print("Adjusted one region to minimum allowed price based on API error; retrying once...")
                resp = patch_base_plan_regional_configs(
                    service,
                    args.package_name,
                    args.product_id,
                    args.base_plan_id,
                    merged_regional_configs,
                    regions_version=regions_version,
                )
                if isinstance(resp, dict):
                    if "basePlanId" in resp:
                        print(f"Updated base plan '{resp['basePlanId']}'.")
                    else:
                        print("Update applied.")
                else:
                    print("Update applied.")
                return
        # If still failing, try removing the region and retry once
        removed = remove_region_from_configs(details_text or "", merged_regional_configs)
        if removed:
            print(f"Removed region {removed} due to constraints; retrying once...")
            resp = patch_base_plan_regional_configs(
                service,
                args.package_name,
                args.product_id,
                args.base_plan_id,
                merged_regional_configs,
                regions_version=regions_version,
            )
            if isinstance(resp, dict):
                if "basePlanId" in resp:
                    print(f"Updated base plan '{resp['basePlanId']}'.")
                else:
                    print("Update applied.")
            else:
                print("Update applied.")
            return
        print("API error while applying update:")
        print(json.dumps(details, indent=2))
        sys.exit(2)


if __name__ == "__main__":
    main()


