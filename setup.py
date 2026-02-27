#!/usr/bin/env python3
"""
Setup script for Google Play Price Updater
Helps users create their configuration file interactively.
"""

import json
import os
import sys
from pathlib import Path


def get_user_input(prompt, default=None, required=True):
    """Get user input with optional default value."""
    if default:
        prompt = f"{prompt} (default: {default})"
    
    while True:
        value = input(f"{prompt}: ").strip()
        
        if value:
            return value
        elif default:
            return default
        elif not required:
            return None
        else:
            print("This field is required. Please enter a value.")


def validate_file_path(file_path, description="file"):
    """Validate that a file exists."""
    if not file_path:
        return False
    
    path = Path(file_path)
    if not path.exists():
        print(f"Warning: {description} '{file_path}' does not exist.")
        return False
    return True


def setup_common_fields() -> dict:
    """Collect fields shared by both subscription and OTP configs."""
    package_name = get_user_input("Android app package name (e.g., com.example.app)")

    service_account_path = get_user_input("Path to service account JSON file", "service-account.json")
    validate_file_path(service_account_path, "Service account JSON")

    default_csv_path = get_user_input("Default CSV file path", "prices.csv")
    validate_file_path(default_csv_path, "CSV file")

    regions_version = get_user_input("Regions version", "2025/03")

    print()
    print("Default options (you can change these later):")

    fix_currency = input("Fix currency mismatches automatically? (Y/n): ").lower() != "n"
    convert_currency = input("Convert currency amounts when fixing? (Y/n): ").lower() != "n"
    use_recommended = input("Use Google recommended prices? (y/N): ").lower() == "y"

    try:
        batch_size = int(get_user_input("Batch size for updates (0 for single request)", "50"))
    except ValueError:
        batch_size = 50

    enable_availability = input("Enable availability for updated regions? (y/N): ").lower() == "y"

    return {
        "package_name": package_name,
        "service_account_path": service_account_path,
        "default_csv_path": default_csv_path,
        "regions_version": regions_version,
        "defaults": {
            "fix_currency": fix_currency,
            "convert_currency": convert_currency,
            "use_recommended": use_recommended,
            "batch_size": batch_size,
            "enable_availability": enable_availability,
        },
    }


def setup_subscription(config: dict) -> dict:
    """Add subscription-specific fields."""
    print()
    print("--- Subscription Settings ---")
    config["product_id"] = get_user_input("Subscription product ID", "subscription-product")
    config["base_plan_id"] = get_user_input("Base plan ID", "monthly-plan")
    return config


def setup_otp(config: dict) -> dict:
    """Add one-time product-specific fields."""
    print()
    print("--- One-Time Product Settings ---")
    config["otp_product_id"] = get_user_input("One-time product ID")
    config["otp_purchase_option_id"] = get_user_input(
        "Purchase option ID (leave blank for first option)", default=None, required=False
    )
    return config


def main():
    print("=== Google Play Price Updater Setup ===")
    print("This script will help you create a configuration file for your app.")
    print()

    config_path = "config.json"
    existing_config = {}
    if os.path.exists(config_path):
        overwrite = input(f"Configuration file '{config_path}' already exists. Overwrite? (y/N): ").lower()
        if overwrite != "y":
            print("Setup cancelled.")
            return
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing_config = json.load(f)
        except Exception:
            pass

    print("What would you like to configure?")
    print("  1) Subscription pricing")
    print("  2) One-time product (OTP) pricing")
    print("  3) Both")
    choice = input("Choose (1/2/3, default: 1): ").strip() or "1"

    print()
    print("Please provide the following information:")
    print()

    config = setup_common_fields()

    if choice in ("1", "3"):
        config = setup_subscription(config)
    else:
        config["product_id"] = existing_config.get("product_id", "subscription-product")
        config["base_plan_id"] = existing_config.get("base_plan_id", "monthly-plan")

    if choice in ("2", "3"):
        config = setup_otp(config)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        print()
        print(f"✓ Configuration saved to '{config_path}'")
        print()
        if choice in ("1", "3"):
            print("Update subscription prices:")
            print("  python update_play_prices.py          # dry run")
            print("  python update_play_prices.py --apply   # apply")
        if choice in ("2", "3"):
            print()
            print("Update one-time product prices:")
            print("  python update_play_otp_prices.py --list-products   # list products")
            print("  python update_play_otp_prices.py                   # dry run")
            print("  python update_play_otp_prices.py --apply           # apply")
    except Exception as e:
        print(f"Error saving configuration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
