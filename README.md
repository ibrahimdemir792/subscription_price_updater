# Google Play Price Updater

A Python toolkit to manage regional pricing for Google Play apps via the [Android Publisher API (v3)](https://developers.google.com/android-publisher). Supports both **subscriptions** and **one-time products** from a single CSV file.

This repository is vibe-coded with Gpt-5 and Claude-4.

## Features

- **Subscriptions & One-Time Products** - Two dedicated scripts sharing a common core
- **Multi-region pricing** - Update prices across all Google Play supported regions
- **Currency conversion** - Automatically convert prices using Google's exchange rates
- **CSV-based** - Easy-to-use CSV format for price management
- **Configuration-driven** - Set up once, run anywhere
- **Batch processing** - Update prices in chunks to handle large datasets
- **Safe defaults** - Dry-run mode to preview changes before applying
- **Error handling** - Automatic price clamping and region filtering
- **Apple-to-Google converter** - Convert Apple Connect pricing CSVs to Google Play format

## Project Structure

```
google-play-api/
├── common.py                  # Shared utilities (CSV, auth, API helpers)
├── preview.py                 # Dry-run preview display logic
├── update_play_prices.py      # Subscription price updater
├── update_play_otp_prices.py  # One-time product price updater
├── apple-to-google.py         # Apple Connect CSV → Google Play CSV converter
├── setup.py                   # Interactive configuration wizard
├── config.json.example        # Example configuration
├── example_prices.csv         # Example CSV with sample prices
└── requirements.txt           # Python dependencies
```

## Quick Start

### 1. Installation

```bash
git clone <this-repository>
cd google-play-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Authentication

You need a Google Cloud service account with Android Publisher API access:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the **Google Play Android Developer API**
4. Create a service account and download the JSON key
5. In Google Play Console, go to **Settings > API access** and grant the service account permissions

> **Security**: Never commit your service account JSON file. It is already excluded by `.gitignore`.

### 3. Setup

Run the interactive setup to create your configuration:

```bash
python setup.py
```

The wizard lets you choose what to configure:
- **Option 1**: Subscription pricing only
- **Option 2**: One-time product pricing only
- **Option 3**: Both

Or copy the example and edit manually:

```bash
cp config.json.example config.json
```

### 4. Prepare Your CSV

Create a CSV file with these columns:

| Column | Description | Example |
|---|---|---|
| `Countries or Regions` | ISO 3-letter country code | `USA`, `GBR`, `DEU` |
| `Currency Code` | 3-letter currency code | `USD`, `EUR`, `JPY` |
| `Price` | Numeric price (no symbols) | `9.99`, `1200` |

```csv
Countries or Regions,Currency Code,Price
USA,USD,9.99
GBR,GBP,7.99
DEU,EUR,8.99
JPN,JPY,1200
```

See `example_prices.csv` for a complete sample.

**Coming from Apple Connect?** Use the converter:

```bash
python apple-to-google.py
```

Edit `INPUT_FILE` and `OUTPUT_FILE` at the top of the script to point to your files.

---

## Usage

### Subscription Prices (`update_play_prices.py`)

Updates regional pricing for a subscription's base plan using the `monetization.subscriptions` API.

**Dry run** (preview changes without applying):

```bash
python update_play_prices.py
```

**Apply changes**:

```bash
python update_play_prices.py --apply
```

**Override config with CLI flags**:

```bash
python update_play_prices.py \
  --package-name com.example.app \
  --product-id premium \
  --base-plan-id monthly \
  --csv prices.csv \
  --apply
```

#### Subscription CLI Options

| Option | Description | Default |
|---|---|---|
| `--package-name` | Android package name | From config |
| `--product-id` | Subscription product ID | From config |
| `--base-plan-id` | Base plan ID | From config |
| `--csv` | Path to CSV file | From config |
| `--service-account` | Path to service account JSON | From config |
| `--apply` | Apply changes (default is dry-run) | `false` |
| `--fix-currency` | Auto-correct currency mismatches | From config |
| `--convert-currency` | Convert amounts when fixing currency | From config |
| `--use-recommended` | Use Google's recommended prices | From config |
| `--batch-size N` | Process N regions per request | From config |
| `--regions-version` | Regions version string (e.g. `2025/03`) | From config |
| `--enable-availability` | Enable purchases in updated regions | From config |
| `--migrate-existing` | Migrate legacy subscriber cohorts | `false` |
| `--migrate-cutoff` | ISO8601 cutoff for migration | - |
| `--migrate-increase-type` | `PRICE_INCREASE_TYPE_OPT_IN` or `OPT_OUT` | `OPT_IN` |

---

### One-Time Product Prices (`update_play_otp_prices.py`)

Updates regional pricing for one-time products (in-app purchases) using the `monetization.onetimeproducts` API.

**List all one-time products** (discover product & purchase option IDs):

```bash
python update_play_otp_prices.py --list-products
```

**Dry run**:

```bash
python update_play_otp_prices.py
```

**Apply changes**:

```bash
python update_play_otp_prices.py --apply
```

**Override config with CLI flags**:

```bash
python update_play_otp_prices.py \
  --product-id coins_500 \
  --purchase-option-id buy-option-1 \
  --csv otp_prices.csv \
  --apply
```

#### OTP CLI Options

| Option | Description | Default |
|---|---|---|
| `--package-name` | Android package name | From config |
| `--product-id` | One-time product ID | From config |
| `--purchase-option-id` | Purchase option ID (omit for first) | From config |
| `--csv` | Path to CSV file | From config |
| `--service-account` | Path to service account JSON | From config |
| `--apply` | Apply changes (default is dry-run) | `false` |
| `--fix-currency` | Auto-correct currency mismatches | From config |
| `--convert-currency` | Convert amounts when fixing currency | From config |
| `--use-recommended` | Use Google's recommended prices | From config |
| `--regions-version` | Regions version string | From config |
| `--enable-availability` | Set availability=AVAILABLE | From config |
| `--list-products` | List all OTP products and exit | - |

---

## Configuration

Both scripts read from the same `config.json`. Fields are grouped by purpose:

```json
{
  "package_name": "com.example.app",

  "product_id": "premium-subscription",
  "base_plan_id": "monthly-plan",

  "otp_product_id": "coins_500",
  "otp_purchase_option_id": "",

  "service_account_path": "service-account.json",
  "default_csv_path": "prices.csv",
  "regions_version": "2025/03",
  "defaults": {
    "fix_currency": true,
    "convert_currency": true,
    "use_recommended": false,
    "batch_size": 0,
    "enable_availability": false
  }
}
```

### Field Reference

#### Shared Fields (both scripts)

| Field | Required | Description |
|---|---|---|
| `package_name` | Yes | Your app's package name (e.g. `com.example.app`) |
| `service_account_path` | Yes | Path to Google Cloud service account JSON key |
| `default_csv_path` | No | Default CSV file when `--csv` is not specified |
| `regions_version` | No | Google Play regions version (format: `YYYY/MM`) |

#### Subscription Fields (`update_play_prices.py`)

| Field | Description |
|---|---|
| `product_id` | Subscription product ID from Play Console |
| `base_plan_id` | Base plan ID within the subscription |

#### One-Time Product Fields (`update_play_otp_prices.py`)

| Field | Description |
|---|---|
| `otp_product_id` | One-time product ID from Play Console |
| `otp_purchase_option_id` | Purchase option ID (leave empty to use the first option) |

#### Default Behavior (`defaults`)

| Field | Default | Description |
|---|---|---|
| `fix_currency` | `true` | Auto-correct currency mismatches between CSV and Google requirements |
| `convert_currency` | `true` | Convert price amounts using Google's exchange rates when fixing currency |
| `use_recommended` | `false` | Replace CSV prices with Google's recommended regional prices |
| `batch_size` | `0` | Regions per request (`0` = all at once, `25-50` for large lists) |
| `enable_availability` | `false` | Enable purchasing in updated regions |

---

## Dry Run Preview

Both scripts default to dry-run mode. The preview shows:

- **New regions** - regions being added for the first time
- **Price changes** - with increase/decrease indicators
- **Availability changes** - if `--enable-availability` is set
- **Unchanged regions** - for verification
- **Change highlights** - summary of increases, decreases, and currency changes

Run the same command with `--apply` to commit the changes.

---

## How It Works

1. **CSV Processing** - Reads the CSV and converts ISO 3-letter country codes to ISO 2-letter codes
2. **Authentication** - Connects to Google Play using the service account
3. **Region Validation** - Filters non-billable regions and fixes currency mismatches
4. **Price Merging** - Merges new prices with existing regional configs (preserving unmodified regions)
5. **API Update** - Patches the subscription or one-time product via the Android Publisher API v3
6. **Error Recovery** - Automatically clamps out-of-range prices and retries on timeouts

---

## Troubleshooting

**"Configuration file not found"**
- Run `python setup.py` or copy `config.json.example` to `config.json`

**"Service account file not found"**
- Check that `service_account_path` in your config points to the correct file

**"One-time product not found"**
- Run `python update_play_otp_prices.py --list-products` to see available products

**"Price for XX must be between..."**
- The tool automatically clamps prices to Google's allowed range and retries

**"Region code XX is not supported"**
- The tool automatically skips unsupported regions

**Timeout errors**
- Set `batch_size` to `25-50` in your config or use `--batch-size 25`

### Price Formatting

- Use decimal notation: `9.99` not `$9.99`
- No currency symbols or thousands separators
- Google Play enforces minimum/maximum price bounds per region

---

## Security

The `.gitignore` excludes all sensitive files by default:
- `config.json` (contains your package name and file paths)
- `*.json` (service account keys)
- `*.csv` (your pricing data)

Only `config.json.example` and `example_prices.csv` are tracked.

> **Important**: If you accidentally commit a service account key, revoke it immediately in Google Cloud Console and generate a new one. Git history retains deleted files.

---

## Contributing

1. Test with your own Google Play app first
2. Include example CSV files for new features
3. Update documentation for any new options
4. Follow the existing code style

## License

This project is provided as-is for educational and development purposes. Test thoroughly with your own apps before using in production.

## Support

- [Google Play Console Help](https://support.google.com/googleplay/android-developer/)
- [Android Publisher API Documentation](https://developers.google.com/android-publisher)
- File issues in this repository for tool-specific problems
