#!/usr/bin/env python3
"""
Update Google Play one-time product (in-app product) prices from CSV.

Uses the monetization.onetimeproducts API (v3) to update regional pricing
on purchase options for one-time products.
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional
from urllib.parse import quote

from googleapiclient.errors import HttpError

from common import (
    RegionalPrice,
    authenticate,
    build_regional_prices,
    clamp_config_from_error_message,
    execute_with_retry,
    fetch_billable_regions_and_currencies,
    fetch_regions_version,
    filter_and_fix_regional_prices,
    load_config,
    read_csv_prices,
    remove_region_from_configs,
)
from preview import print_price_changes_preview_generic


# ---------------------------------------------------------------------------
# OTP-specific API helpers
# ---------------------------------------------------------------------------

def get_otp_product(service, package_name: str, product_id: str) -> Optional[dict]:
    """Fetch a single one-time product via monetization.onetimeproducts.get."""
    try:
        return (
            service.monetization()
            .onetimeproducts()
            .get(packageName=package_name, productId=product_id)
            .execute()
        )
    except HttpError as e:
        if e.resp is not None and e.resp.status == 404:
            return None
        raise


def list_otp_products(service, package_name: str) -> List[dict]:
    """List all one-time products for the given package."""
    products: List[dict] = []
    page_token = None
    while True:
        kwargs = {"packageName": package_name, "pageSize": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.monetization().onetimeproducts().list(**kwargs).execute()
        products.extend(resp.get("oneTimeProducts", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return products


def find_purchase_option(product: dict, purchase_option_id: Optional[str] = None) -> Optional[dict]:
    """Find a purchase option by ID, or return the first one if no ID given."""
    options = product.get("purchaseOptions", [])
    if not options:
        return None
    if purchase_option_id:
        for opt in options:
            if opt.get("purchaseOptionId") == purchase_option_id:
                return opt
        return None
    return options[0]


def merge_otp_regional_configs(
    existing_option: dict,
    new_prices: List[RegionalPrice],
    enable_availability: bool = False,
) -> List[dict]:
    """Merge new prices into the purchase option's regional configs."""
    existing = existing_option.get("regionalPricingAndAvailabilityConfigs", []) or []
    by_region: Dict[str, dict] = {
        rc.get("regionCode"): rc for rc in existing if rc.get("regionCode")
    }
    for rp in new_prices:
        preserved = dict(by_region.get(rp.region_iso2, {}))
        preserved["regionCode"] = rp.region_iso2
        preserved["price"] = {
            "currencyCode": rp.currency_code,
            "units": rp.units,
            "nanos": rp.nanos,
        }
        if enable_availability:
            preserved["availability"] = "AVAILABLE"
        by_region[rp.region_iso2] = preserved
    return [by_region[k] for k in sorted(by_region.keys())]


def print_otp_price_changes_preview(
    existing_option: dict,
    new_configs: List[dict],
    enable_availability: bool,
):
    """Print a detailed preview of OTP price changes for dry run mode."""
    existing_by_region = {
        rc.get("regionCode"): rc
        for rc in existing_option.get("regionalPricingAndAvailabilityConfigs", [])
        if rc.get("regionCode")
    }
    print_price_changes_preview_generic(
        existing_by_region, new_configs, enable_availability,
        availability_key="availability",
    )


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

def patch_otp_product(
    service,
    package_name: str,
    product_id: str,
    product_body: dict,
    regions_version: Optional[dict] = None,
):
    """PATCH a one-time product via monetization.onetimeproducts.patch."""
    rv = regions_version or fetch_regions_version(service, package_name)
    rv_obj = {}
    if isinstance(rv, dict):
        rv_obj = rv
    elif isinstance(rv, str):
        rv_obj = {"version": rv}

    req = (
        service.monetization()
        .onetimeproducts()
        .patch(
            packageName=package_name,
            productId=product_id,
            updateMask="purchaseOptions",
            body=product_body,
            regionsVersion_version=rv_obj.get("version", ""),
        )
    )
    return execute_with_retry(req, "patch one-time product")


def patch_otp_product_raw(
    service,
    package_name: str,
    product_id: str,
    product_body: dict,
    regions_version: Optional[dict] = None,
):
    """Fallback: manually construct the PATCH request if the discovery
    client does not expose onetimeproducts().patch() with regionsVersion_version.
    """
    rv = regions_version or fetch_regions_version(service, package_name)
    rv_str = ""
    if isinstance(rv, dict):
        rv_str = rv.get("version", "")
    elif isinstance(rv, str):
        rv_str = rv

    base_url = (
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package_name}/onetimeproducts/{product_id}"
    )
    params = f"updateMask=purchaseOptions&regionsVersion.version={quote(rv_str)}"
    url = f"{base_url}?{params}"

    from googleapiclient.http import HttpRequest
    import json as _json

    http = service._http
    req = HttpRequest(
        http,
        lambda resp, content: (resp, content),
        url,
        method="PATCH",
        body=_json.dumps(product_body),
        headers={"Content-Type": "application/json"},
    )
    resp, content = req.execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    result = _json.loads(content)
    if isinstance(resp, dict) and resp.get("status", "200").startswith(("4", "5")):
        raise HttpError(resp, content.encode("utf-8"))
    return result


def apply_otp_update(
    service,
    package_name: str,
    product: dict,
    purchase_option_id: str,
    merged_configs: List[dict],
    regions_version: Optional[dict] = None,
):
    """Build the product body and PATCH the one-time product."""
    updated_options = []
    for opt in product.get("purchaseOptions", []):
        if opt.get("purchaseOptionId") == purchase_option_id:
            opt = dict(opt)
            opt["regionalPricingAndAvailabilityConfigs"] = merged_configs
        updated_options.append(opt)

    product_body = {
        "packageName": package_name,
        "productId": product.get("productId"),
        "listings": product.get("listings", []),
        "purchaseOptions": updated_options,
    }
    if product.get("taxAndComplianceSettings"):
        product_body["taxAndComplianceSettings"] = product["taxAndComplianceSettings"]

    try:
        return patch_otp_product(service, package_name, product["productId"], product_body, regions_version)
    except (TypeError, AttributeError):
        return patch_otp_product_raw(service, package_name, product["productId"], product_body, regions_version)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Update Google Play one-time product prices from CSV")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")

    config_args, _ = parser.parse_known_args()
    config = load_config(config_args.config)

    parser = argparse.ArgumentParser(description="Update Google Play one-time product prices from CSV")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    parser.add_argument(
        "--package-name",
        default=config.get("package_name"),
        required=config.get("package_name") is None,
        help="Android app package name",
    )
    parser.add_argument(
        "--product-id",
        default=config.get("otp_product_id"),
        help="One-time product ID",
    )
    parser.add_argument(
        "--purchase-option-id",
        default=config.get("otp_purchase_option_id"),
        help="Purchase option ID within the product (defaults to first option)",
    )
    parser.add_argument(
        "--csv",
        default=config.get("default_csv_path"),
        help=f"Path to CSV (default: {config.get('default_csv_path')})",
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
        help="Fix currency mismatches to match region requirements",
    )
    parser.add_argument(
        "--convert-currency",
        action="store_true" if config.get("defaults", {}).get("convert_currency") else "store_false",
        default=config.get("defaults", {}).get("convert_currency", False),
        help="Convert price amounts when fixing currencies",
    )
    parser.add_argument(
        "--use-recommended",
        action="store_true" if config.get("defaults", {}).get("use_recommended") else "store_false",
        default=config.get("defaults", {}).get("use_recommended", False),
        help="Use Google recommended regional prices",
    )
    parser.add_argument(
        "--regions-version",
        default=config.get("regions_version"),
        help=f"Explicit regionsVersion string (default: {config.get('regions_version')})",
    )
    parser.add_argument(
        "--enable-availability",
        action="store_true" if config.get("defaults", {}).get("enable_availability") else "store_false",
        default=config.get("defaults", {}).get("enable_availability", False),
        help="Set availability=AVAILABLE for updated regions",
    )
    parser.add_argument(
        "--list-products",
        action="store_true",
        help="List all one-time products and exit",
    )
    args = parser.parse_args()

    # Validate package name
    if not args.package_name:
        print("Error: package-name is required.")
        print("Either specify it with --package-name or add it to your config.json file.")
        sys.exit(1)

    # Authenticate
    sa_path = os.path.abspath(args.service_account)
    if not os.path.exists(sa_path):
        print(f"Error: Service account file '{sa_path}' not found.")
        sys.exit(1)

    try:
        service = authenticate(sa_path)
        print("✓ Authenticated with Google Play Console")
    except Exception as e:
        print(f"Authentication Error: {e}")
        sys.exit(1)

    # List mode
    if args.list_products:
        products = list_otp_products(service, args.package_name)
        if not products:
            print("No one-time products found.")
            return
        print(f"\nFound {len(products)} one-time product(s):\n")
        for p in products:
            pid = p.get("productId", "?")
            options = p.get("purchaseOptions", [])
            title = ""
            for listing in p.get("listings", []):
                title = listing.get("title", "")
                break
            print(f"  {pid}")
            if title:
                print(f"    Title: {title}")
            for opt in options:
                oid = opt.get("purchaseOptionId", "?")
                opt_type = "buy" if opt.get("buyOption") is not None else "rent" if opt.get("rentOption") is not None else "?"
                state = opt.get("state", "?")
                regions_count = len(opt.get("regionalPricingAndAvailabilityConfigs", []))
                print(f"    Option: {oid} ({opt_type}, {state}, {regions_count} regions)")
            print()
        return

    # Validate product-id
    if not args.product_id:
        print("Error: --product-id is required.")
        print("Use --list-products to see available one-time products.")
        sys.exit(1)

    # Validate CSV
    csv_path = os.path.abspath(args.csv)
    if not os.path.exists(csv_path):
        print(f"Error: CSV file '{csv_path}' not found.")
        sys.exit(1)

    try:
        rows = read_csv_prices(csv_path)
        regional_prices = build_regional_prices(rows)
        if not regional_prices:
            print("No valid pricing data found in CSV.")
            sys.exit(1)
    except (ValueError, FileNotFoundError) as e:
        print(f"CSV Error: {e}")
        sys.exit(1)

    print(f"Read {len(regional_prices)} regional prices from CSV '{csv_path}'.")
    print("Examples:")
    for ex in regional_prices[:5]:
        print(f"  {ex.region_iso2}: {ex.units}.{str(ex.nanos).zfill(9)} {ex.currency_code}")

    # Fetch product
    try:
        product = get_otp_product(service, args.package_name, args.product_id)
        if not product:
            print(f"Error: One-time product '{args.product_id}' not found.")
            print("Use --list-products to see available products.")
            sys.exit(1)
        print(f"✓ Found one-time product '{args.product_id}'")
    except Exception as e:
        print(f"API Error: {e}")
        sys.exit(1)

    # Find purchase option
    purchase_option = find_purchase_option(product, args.purchase_option_id)
    if not purchase_option:
        available = [o.get("purchaseOptionId") for o in product.get("purchaseOptions", [])]
        print(f"Error: Purchase option '{args.purchase_option_id}' not found.")
        print(f"Available options: {available}")
        sys.exit(1)
    po_id = purchase_option.get("purchaseOptionId")
    print(f"✓ Using purchase option '{po_id}'")

    # Filter & fix regional prices
    region_currency_map = fetch_billable_regions_and_currencies(service, args.package_name)
    filtered_regional_prices = filter_and_fix_regional_prices(
        service,
        args.package_name,
        regional_prices,
        region_currency_map,
        fix_currency=args.fix_currency,
        convert_currency=args.convert_currency,
        use_recommended=args.use_recommended,
    )

    if not filtered_regional_prices:
        print("No billable regions left after filtering; aborting.")
        sys.exit(2)

    merged_configs = merge_otp_regional_configs(
        purchase_option, filtered_regional_prices, enable_availability=args.enable_availability
    )
    print(f"Prepared {len(merged_configs)} merged regional configs.")

    # Resolve regions version
    regions_version = None
    if args.regions_version:
        regions_version = {"version": args.regions_version}
    if regions_version is None:
        regions_version = product.get("regionsVersion") or fetch_regions_version(service, args.package_name)
    rv_str = regions_version.get("version") if isinstance(regions_version, dict) else regions_version
    print(f"Using regionsVersion: {rv_str if rv_str else 'None'}")

    # Dry-run preview
    if not args.apply:
        print("\n" + "=" * 80)
        print("DRY RUN - PREVIEW OF CHANGES")
        print("=" * 80)
        print_otp_price_changes_preview(purchase_option, merged_configs, args.enable_availability)
        print("=" * 80)
        print("Dry-run: no changes applied. Use --apply to perform the update.")
        return

    # Apply
    try:
        resp = apply_otp_update(
            service, args.package_name, product, po_id, merged_configs, regions_version
        )
        if isinstance(resp, dict) and resp.get("productId"):
            print(f"✓ Updated one-time product '{resp['productId']}'.")
        else:
            print("✓ Update applied.")
    except HttpError as e:
        details_text = None
        try:
            details = json.loads(e.content.decode("utf-8"))
            details_text = details.get("error", {}).get("message")
        except Exception:
            details = {"error": str(e)}
            details_text = str(e)

        if details_text and "must be between" in details_text:
            adjusted = clamp_config_from_error_message(details_text, merged_configs)
            if adjusted:
                print("Adjusted one region to minimum allowed price; retrying once...")
                resp = apply_otp_update(
                    service, args.package_name, product, po_id, merged_configs, regions_version
                )
                if isinstance(resp, dict) and resp.get("productId"):
                    print(f"✓ Updated one-time product '{resp['productId']}'.")
                else:
                    print("✓ Update applied.")
                return

        removed = remove_region_from_configs(details_text or "", merged_configs)
        if removed:
            print(f"Removed region {removed} due to constraints; retrying once...")
            resp = apply_otp_update(
                service, args.package_name, product, po_id, merged_configs, regions_version
            )
            if isinstance(resp, dict) and resp.get("productId"):
                print(f"✓ Updated one-time product '{resp['productId']}'.")
            else:
                print("✓ Update applied.")
            return

        print("API error while applying update:")
        print(json.dumps(details, indent=2))
        sys.exit(2)


if __name__ == "__main__":
    main()
