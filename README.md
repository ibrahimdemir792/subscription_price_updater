# Subscription Price Updater (Google Play Console)

A Python tool to update subscription pricing for Google Play Console apps using CSV files. This tool makes it easy to manage regional pricing across multiple countries and currencies.

This repository is vibe-coded with Gpt-5 and Claude-4.

## Features

- 🌍 **Multi-region pricing**: Update prices across all Google Play supported regions
- 💱 **Currency conversion**: Automatically convert prices using Google's exchange rates
- 📊 **CSV-based**: Easy-to-use CSV format for price management
- ⚙️ **Configuration-driven**: Set up once, run anywhere
- 🔄 **Batch processing**: Update prices in chunks to handle large datasets
- 🛡️ **Safe defaults**: Dry-run mode to preview changes before applying
- 🔧 **Error handling**: Automatic price clamping and region filtering

## Quick Start

### 1. Installation

```bash
git clone <this-repository>
cd google-play-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2.Authentication

You need a Google Cloud service account with Android Publisher API access:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the "Google Play Android Developer API"
4. Create a service account and download the JSON key
5. In Google Play Console, add the service account email with appropriate permissions

### 3. Setup

Run the interactive setup to create your configuration:

```bash
python setup.py
```

This will guide you through:

- Setting your app's package name
- Configuring product and base plan IDs
- Setting up authentication
- Choosing default options

### 4. Prepare Your CSV

Create a CSV file with the following columns:

- `Countries or Regions`: ISO 3-letter country codes (e.g., USA, GBR, DEU)
- `Currency Code`: 3-letter currency codes (e.g., USD, EUR, JPY)
- `Price`: Numeric price in the specified currency

Example:

```csv
Countries or Regions,Currency Code,Price
USA,USD,9.99
GBR,GBP,7.99
DEU,EUR,8.99
```

Use the provided templates:

- `example_prices.csv`: Basic example with major markets
- `template_monthly_prices.csv`: Comprehensive template with 75+ regions

### 5. Test and Apply

**Dry run** (preview changes):

```bash
python update_play_prices.py
```

The dry run now shows a detailed preview with **highlighted price changes**:
- 🆕 **New regions** with \033[32m→ green highlighting ←\033[0m
- 💰 **Price changes** with \033[33m→ yellow highlighting ←\033[0m
- 📈 **Price increases**, 📉 **decreases**, 🔄 **currency changes**  
- 🌍 **Availability changes** with \033[36m→ cyan highlighting ←\033[0m
- 🔍 **Change highlights** summary at the end

**Apply changes**:

```bash
python update_play_prices.py --apply
```

## Configuration

The tool uses `config.json` for default settings. Create one using `python setup.py` or manually:

```json
{
  "package_name": "com.example.app",
  "product_id": "subscription-product",
  "base_plan_id": "monthly-plan",
  "service_account_path": "service-account.json",
  "default_csv_path": "prices.csv",
  "regions_version": "2025/01",
  "defaults": {
    "fix_currency": true,
    "convert_currency": true,
    "use_recommended": false,
    "batch_size": 50,
    "enable_availability": false
  }
}
```

### Configuration Parameters Explained

#### Basic Settings

| Parameter                | Description                                                   | When to Use                                               |
| ------------------------ | ------------------------------------------------------------- | --------------------------------------------------------- |
| `package_name`         | Your Android app's package identifier (e.g., com.example.app) | **Required** - Always needed to identify your app   |
| `product_id`           | Subscription product ID from Google Play Console              | Use when you have multiple subscription products          |
| `base_plan_id`         | Specific base plan ID to update                               | Use when you have multiple plans (monthly, annual, etc.)  |
| `service_account_path` | Path to your Google Cloud service account JSON file           | **Required** - Always needed for authentication     |
| `default_csv_path`     | Default CSV file to use if none specified                     | Set to your most commonly used pricing file               |
| `regions_version`      | Google Play regions version (format: YYYY/MM)                 | Update when Google releases new regional pricing versions |

#### Default Behavior Settings

| Parameter               | Default   | Description                                                                | When to Use                                                                                                |
| ----------------------- | --------- | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `fix_currency`        | `true`  | Auto-correct currency mismatches between CSV and Google requirements       | ✅**Recommended: `true`** - Prevents errors when your CSV has wrong currencies for certain regions |
| `convert_currency`    | `true`  | Convert price amounts when fixing currencies using Google's exchange rates | Use `true` when you want accurate price conversions, `false` to keep original amounts                  |
| `use_recommended`     | `false` | Replace CSV prices with Google's recommended regional prices               | Use `true` for Google's optimized pricing, `false` to use your exact CSV prices                        |
| `batch_size`          | `0`     | Number of regions to process per request (0 = all at once)                 | Use `25-50` for large price lists to avoid timeouts, `0` for small lists                               |
| `enable_availability` | `false` | Enable new subscriber availability for updated regions                     | Use `true` when launching in new regions or re-enabling purchases                                        |

### Detailed Parameter Explanations

#### `fix_currency` - Currency Mismatch Handling

**What it does**: Automatically corrects currency codes in your CSV to match Google Play requirements for each region.

**Example scenarios**:

- ❌ Your CSV has `USD` for Germany, but Google requires `EUR`
- ❌ Your CSV has `GBP` for France, but Google requires `EUR`
- ✅ Tool automatically fixes these mismatches

**When to use `true`**:

- You're unsure about correct regional currencies
- Your CSV was created for multiple platforms (not just Google Play)
- You want to avoid "currency not supported" errors

**When to use `false`**:

- Your CSV currencies are already 100% correct for Google Play
- You want to catch currency errors manually

#### `convert_currency` - Price Amount Conversion

**What it does**: When fixing currencies, also converts the actual price amounts using Google's exchange rates.

**Example scenarios**:

- Your CSV: Germany = `$9.99 USD`
- With `convert_currency: true`: Germany = `€8.45 EUR` (converted amount)
- With `convert_currency: false`: Germany = `€9.99 EUR` (same number, different currency)

**When to use `true`**:

- You want economically equivalent prices across regions
- Your base prices are in one currency (e.g., USD) and you want fair regional pricing
- You trust Google's exchange rates

**When to use `false`**:

- You've already calculated regional prices manually
- You want consistent price numbers across regions (e.g., always X.99)
- You have specific pricing strategies per region

#### `use_recommended` - Google's Optimized Pricing

**What it does**: Replaces your CSV prices with Google's recommended prices based on market research and purchasing power.

**Example scenarios**:

- Your CSV: India = `$9.99 USD`
- Google recommended: India = `₹299 INR` (optimized for local market)

**When to use `true`**:

- You're launching globally and want market-optimized pricing
- You trust Google's market research over your own pricing
- You want to maximize conversions in each region

**When to use `false`**:

- You have specific business pricing requirements
- You've done your own market research
- You want consistent global pricing strategy

#### `batch_size` - Processing Strategy

**What it does**: Splits large price updates into smaller chunks to avoid API limits and timeouts.

**Recommendations**:

- `0` (default): Process all regions at once - fast but may timeout with 50+ regions
- `25-50`: Good balance for most cases - reliable processing
- `10-25`: Very safe for large datasets or slow connections
- `100+`: Only for small total region counts

**When to use small batches (10-25)**:

- You have 100+ regions in your CSV
- You're experiencing timeout errors
- You have a slow internet connection
- You want to monitor progress step-by-step

**When to use large batches (50+) or 0**:

- You have fewer than 50 regions
- You want fastest possible processing
- Your internet connection is reliable

#### `enable_availability` - Market Activation

**What it does**: Sets new subscriber availability to allow purchases in updated regions.

**Example scenarios**:

- Region was previously disabled due to pricing issues
- You're launching your app in new countries
- You've updated prices and want to re-enable purchases

**When to use `true`**:

- Launching in new markets for the first time
- Re-enabling previously disabled regions
- You've fixed pricing issues and want to allow new subscriptions

**When to use `false`**:

- Just updating prices in existing active markets
- You want to manually control market availability through Play Console
- You're testing pricing changes before going live

## Command Line Options

### Basic Options

| Option             | Description                        | Default         |
| ------------------ | ---------------------------------- | --------------- |
| `--config`       | Path to configuration file         | `config.json` |
| `--csv`          | Path to CSV file                   | From config     |
| `--apply`        | Apply changes (default is dry-run) | `false`       |
| `--package-name` | Android package name               | From config     |
| `--product-id`   | Subscription product ID            | From config     |
| `--base-plan-id` | Base plan ID                       | From config     |

### Migration Options

| Option                      | Description                            |
| --------------------------- | -------------------------------------- |
| `--migrate-existing`      | Migrate existing subscriber cohorts    |
| `--migrate-cutoff`        | ISO8601 timestamp for migration cutoff |
| `--migrate-increase-type` | Price increase type (opt-in/opt-out)   |

## How It Works

1. **CSV Processing**: Reads your CSV file and converts ISO3 country codes to ISO2
2. **Authentication**: Connects to Google Play using your service account
3. **Validation**: Checks that regions are billable and currencies match requirements
4. **Price Preparation**: Builds the regional price configuration
5. **API Update**: Updates the base plan through Google Play's Android Publisher API
6. **Error Handling**: Automatically handles price bounds and unsupported regions

## Troubleshooting

### Common Issues

**"Configuration file not found"**

- Run `python setup.py` to create your configuration file

**"Service account file not found"**

- Ensure your service account JSON file path is correct in the configuration
- Check that the file has proper permissions

**"Package name is required"**

- Add your package name to `config.json` or use `--package-name`

**"Price for XX must be between..."**

- The tool will automatically clamp prices to Google's allowed ranges
- Use `--use-recommended` for Google's suggested regional prices

**"Region code XX is not supported"**

- Some regions may not be available for your app
- The tool will automatically skip unsupported regions

### Price Formatting

- Use decimal notation: `9.99` not `$9.99`
- No currency symbols or thousands separators
- Google Play will enforce minimum/maximum price bounds per region

## Contributing

Feel free to submit issues and pull requests. When contributing:

1. Test with your own Google Play app first
2. Include example CSV files for new features
3. Update documentation for any new options
4. Follow the existing code style

## License

This project is provided as-is for educational and development purposes. Make sure to test thoroughly with your own apps before using in production.

## Support

- Check the [Google Play Console Help](https://support.google.com/googleplay/android-developer/) for API documentation
- Review [Android Publisher API](https://developers.google.com/android-publisher) documentation
- File issues in this repository for tool-specific problems
