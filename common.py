#!/usr/bin/env python3
"""
Shared utilities for Google Play price update scripts.
Used by both subscription and one-time product updaters.
"""

import csv
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional
from urllib.parse import quote

import httplib2
import pycountry
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# HTTP timeout in seconds (default is 60, increase for large requests)
HTTP_TIMEOUT = 600  # 10 minutes

# Set global socket timeout as fallback
socket.setdefaulttimeout(HTTP_TIMEOUT)

# Retry settings for timeout errors
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


@dataclass
class RegionalPrice:
    region_iso2: str
    currency_code: str
    units: str
    nanos: int


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
            "enable_availability": False,
        },
    }

    if not os.path.exists(config_path):
        print(f"Configuration file '{config_path}' not found.")
        print("Run 'python setup.py' to create one, or specify all required arguments.")
        return default_config

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

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

                price_str = row.get("Price", "").strip()
                try:
                    price_val = float(price_str)
                    if price_val < 0:
                        print(f"Warning: Negative price in row {row_num}: {price_str}")
                        continue
                except ValueError:
                    print(f"Warning: Invalid price format in row {row_num}: '{price_str}' - skipping")
                    continue

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


def convert_price_to_units_nanos(price_str: str) -> tuple[str, int]:
    price = Decimal(price_str)
    if price < 0:
        raise ValueError("Price cannot be negative")
    units_part = price.to_integral_value(rounding=ROUND_DOWN)
    fractional = (price - units_part).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    nanos = int((fractional * Decimal(10**9)))
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


def authenticate(service_account_path: str):
    credentials = service_account.Credentials.from_service_account_file(
        service_account_path, scopes=[ANDROID_PUBLISHER_SCOPE]
    )
    # Create HTTP client with extended timeout for large requests
    http = httplib2.Http(timeout=HTTP_TIMEOUT)
    authorized_http = AuthorizedHttp(credentials, http=http)
    service = build("androidpublisher", "v3", http=authorized_http, cache_discovery=False)
    return service


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
    """Convert a price in source currency to target region's local currency."""
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


def format_price_display(price_dict: dict, highlight: bool = False, color: str = None) -> str:
    """Format a price dictionary for display."""
    if not price_dict:
        return "N/A"

    currency = price_dict.get("currencyCode", "")
    units = price_dict.get("units", "0")
    nanos = price_dict.get("nanos", 0)

    decimal_part = f"{nanos:09d}".rstrip("0") or "0"
    if decimal_part == "0":
        price_str = f"{units} {currency}"
    else:
        price_str = f"{units}.{decimal_part} {currency}"

    if highlight:
        if color == "green":
            return f"\033[32m→ {price_str} ←\033[0m"
        elif color == "yellow":
            return f"\033[33m→ {price_str} ←\033[0m"
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
        return " 📈"
    elif new_total < old_total:
        return " 📉"
    else:
        return " 🔄"


def execute_with_retry(request, description: str = "API call"):
    """Execute an API request with retry logic for timeout errors."""
    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            return request.execute()
        except (TimeoutError, socket.timeout, OSError) as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                print(f"⚠️  Timeout error (attempt {attempt + 1}/{MAX_RETRIES}). Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"❌ All {MAX_RETRIES} attempts failed due to timeout.")
                raise
    raise last_exception


def filter_and_fix_regional_prices(
    service,
    package_name: str,
    regional_prices: List[RegionalPrice],
    region_currency_map: Dict[str, str],
    fix_currency: bool = False,
    convert_currency: bool = False,
    use_recommended: bool = False,
) -> List[RegionalPrice]:
    """Filter regional prices by billable regions and fix currency mismatches.

    Shared logic used by both subscription and OTP updaters.
    """
    recommended_prices_by_region: Dict[str, dict] = {}
    if use_recommended and region_currency_map:
        try:
            rec_resp = (
                service.monetization()
                .convertRegionPrices(
                    packageName=package_name,
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
        return regional_prices

    billable_regions = set(region_currency_map.keys())
    filtered = [rp for rp in regional_prices if rp.region_iso2 in billable_regions]

    if use_recommended and recommended_prices_by_region:
        for rp in filtered:
            rec = recommended_prices_by_region.get(rp.region_iso2)
            if rec and rec.get("currencyCode") == region_currency_map.get(rp.region_iso2):
                rp.currency_code = rec.get("currencyCode")
                rp.units = str(int(rec.get("units") or 0))
                rp.nanos = int(rec.get("nanos") or 0)

    skipped = [rp for rp in regional_prices if rp.region_iso2 not in billable_regions]
    if skipped:
        skipped_codes = ", ".join(sorted({rp.region_iso2 for rp in skipped}))
        print(f"Skipping {len(skipped)} non-billable regions at this version: {skipped_codes}")

    mismatched_rps = [
        rp
        for rp in filtered
        if region_currency_map.get(rp.region_iso2)
        and region_currency_map.get(rp.region_iso2) != rp.currency_code
    ]
    if mismatched_rps:
        if fix_currency:
            action = "Fixing currency and converting amount" if convert_currency else "Fixing"
            print(f"{action} to match region requirements:")
            for rp in mismatched_rps:
                required = region_currency_map.get(rp.region_iso2)
                old_curr = rp.currency_code
                if convert_currency:
                    converted = convert_amount(
                        service, package_name, rp.units, rp.nanos, old_curr, rp.region_iso2
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
            filtered = [rp for rp in filtered if rp not in mismatched_rps]

    return filtered


def clamp_config_from_error_message(error_message: str, merged_configs: List[dict]) -> bool:
    """Parse error like:
    "Price for CI must be between F CFA 30 and F CFA 627,341, found F CFA 27"
    and clamp the CI config to the minimum in merged_configs. Returns True if adjusted.
    """
    m = re.search(
        r"Price for\s+([A-Z]{2})\s+must be between\s+(.+?)\s+and\s+(.+?),\s+found\s+(.+)$",
        error_message,
    )
    if not m:
        return False
    region = m.group(1)
    min_str = m.group(2)
    max_str = m.group(3)

    normalized_min = min_str.replace("\u202f", " ").replace("\xa0", " ")
    normalized_max = max_str.replace("\u202f", " ").replace("\xa0", " ")
    num_min = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", normalized_min)
    num_max = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", normalized_max)
    if not num_min or not num_max:
        return False
    raw_min = num_min.group(1).replace(",", ".")
    raw_max = num_max.group(1).replace(",", ".")
    try:
        min_value = Decimal(raw_min)
        max_value = Decimal(raw_max)
    except Exception:
        return False

    for cfg in merged_configs:
        price_key = "price" if "price" in cfg else None
        if not price_key:
            continue
        if cfg.get("regionCode") == region and isinstance(cfg.get(price_key), dict):
            current_units = Decimal(cfg[price_key].get("units", "0"))
            current_nanos = Decimal(cfg[price_key].get("nanos", 0)) / Decimal(10**9)
            found_value = current_units + current_nanos
            target = min_value if found_value < min_value else (max_value if found_value > max_value else found_value)
            units_part = int(target.to_integral_value(rounding=ROUND_DOWN))
            fractional = (target - units_part).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
            nanos = int((fractional * Decimal(10**9)))
            cfg[price_key]["units"] = str(units_part)
            cfg[price_key]["nanos"] = nanos
            return True
    return False


def remove_region_from_configs(error_message: str, merged_configs: List[dict]) -> Optional[str]:
    """Parse region code from error and remove it from merged_configs."""
    m = re.search(r"Region code\s+([A-Z]{2})\b", error_message)
    if not m:
        m = re.search(r"Price for\s+([A-Z]{2})\b", error_message)
    if not m:
        return None
    region = m.group(1)
    before = len(merged_configs)
    merged_configs[:] = [cfg for cfg in merged_configs if cfg.get("regionCode") != region]
    return region if len(merged_configs) < before else None
