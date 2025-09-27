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


def main():
    print("=== Google Play Price Updater Setup ===")
    print("This script will help you create a configuration file for your app.")
    print()
    
    # Check if config already exists
    config_path = "config.json"
    if os.path.exists(config_path):
        overwrite = input(f"Configuration file '{config_path}' already exists. Overwrite? (y/N): ").lower()
        if overwrite != 'y':
            print("Setup cancelled.")
            return
    
    print("Please provide the following information:")
    print()
    
    # Required fields
    package_name = get_user_input("Android app package name (e.g., com.example.app)")
    product_id = get_user_input("Subscription product ID", "subscription-product")
    base_plan_id = get_user_input("Base plan ID", "monthly-plan")
    
    # File paths
    service_account_path = get_user_input("Path to service account JSON file", "service-account.json")
    validate_file_path(service_account_path, "Service account JSON")
    
    default_csv_path = get_user_input("Default CSV file path", "prices.csv")
    validate_file_path(default_csv_path, "CSV file")
    
    regions_version = get_user_input("Regions version", "2025/01")
    
    print()
    print("Default options (you can change these later):")
    
    # Optional settings with defaults
    fix_currency = input("Fix currency mismatches automatically? (Y/n): ").lower() != 'n'
    convert_currency = input("Convert currency amounts when fixing? (Y/n): ").lower() != 'n'
    use_recommended = input("Use Google recommended prices? (y/N): ").lower() == 'y'
    
    try:
        batch_size = int(get_user_input("Batch size for updates (0 for single request)", "50"))
    except ValueError:
        batch_size = 50
    
    enable_availability = input("Enable new subscriber availability? (y/N): ").lower() == 'y'
    
    # Create configuration
    config = {
        "package_name": package_name,
        "product_id": product_id,
        "base_plan_id": base_plan_id,
        "service_account_path": service_account_path,
        "default_csv_path": default_csv_path,
        "regions_version": regions_version,
        "defaults": {
            "fix_currency": fix_currency,
            "convert_currency": convert_currency,
            "use_recommended": use_recommended,
            "batch_size": batch_size,
            "enable_availability": enable_availability
        }
    }
    
    # Save configuration
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        
        print()
        print(f"✓ Configuration saved to '{config_path}'")
        print()
        print("You can now run the price updater with:")
        print(f"  python update_play_prices.py --apply")
        print()
        print("Or for a dry run:")
        print(f"  python update_play_prices.py")
        
    except Exception as e:
        print(f"Error saving configuration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
