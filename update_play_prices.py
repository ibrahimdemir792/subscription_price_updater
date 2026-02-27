#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
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


def print_price_changes_preview(base_plan: dict, new_configs: List[dict], enable_availability: bool):
    """Print a detailed preview of subscription price changes for dry run mode."""
    existing_by_region = {
        rc.get("regionCode"): rc
        for rc in base_plan.get("regionalConfigs", [])
        if rc.get("regionCode")
    }
    print_price_changes_preview_generic(
        existing_by_region, new_configs, enable_availability,
        availability_key="newSubscriberAvailability",
    )


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
    fallback_regions_version = regions_version or fetch_regions_version(service, package_name)
    for bp in subscription.get("basePlans", []):
        if bp.get("basePlanId") == base_plan_id:
            found = True
            bp = dict(bp)
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
    
    return execute_with_retry(req, "patch base plan")


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
            
            cumulative_configs: List[dict] = []
            start = 0
            while start < total:
                end = min(start + args.batch_size, total)
                chunk = merged_regional_configs[start:end]
                cumulative_configs.extend(chunk)
                print(f"Applying batch {start+1}-{end} (cumulative: {len(cumulative_configs)} regions)...")
                apply_chunk(cumulative_configs)
                start = end
                if start < total:
                    time.sleep(2)
            resp = {"basePlanId": args.base_plan_id}
        else:
            resp = apply_chunk(merged_regional_configs)
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
