# Subscription Price Updater (Google Play Console)

A Python tool to update subscription pricing for Google Play Console apps using CSV files. This tool makes it easy to manage regional pricing across multiple countries and currencies.

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

### 2. Setup

Run the interactive setup to create your configuration:

```bash
python setup.py
```

This will guide you through:

- Setting your app's package name
- Configuring product and base plan IDs
- Setting up authentication
- Choosing default options

### 3. Prepare Your CSV

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

### 4. Test and Apply

**Dry run** (preview changes):

```bash
python update_play_prices.py
```

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

## Authentication

You need a Google Cloud service account with Android Publisher API access:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the "Google Play Android Developer API"
4. Create a service account and download the JSON key
5. In Google Play Console, add the service account email with appropriate permissions

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

### Advanced Options

| Option                    | Description                              | Default     |
| ------------------------- | ---------------------------------------- | ----------- |
| `--fix-currency`        | Auto-correct currency mismatches         | From config |
| `--convert-currency`    | Convert prices when fixing currency      | From config |
| `--use-recommended`     | Use Google's recommended regional prices | From config |
| `--batch-size N`        | Process in chunks of N regions           | From config |
| `--regions-version`     | Google Play regions version              | From config |
| `--enable-availability` | Enable new subscriber availability       | From config |

### Migration Options

| Option                      | Description                            |
| --------------------------- | -------------------------------------- |
| `--migrate-existing`      | Migrate existing subscriber cohorts    |
| `--migrate-cutoff`        | ISO8601 timestamp for migration cutoff |
| `--migrate-increase-type` | Price increase type (opt-in/opt-out)   |

## Examples

### Basic Usage

```bash
# Dry run with default settings
python update_play_prices.py

# Apply changes
python update_play_prices.py --apply

# Use specific CSV file
python update_play_prices.py --csv monthly_prices.csv --apply
```

### Advanced Usage

```bash
# Use Google recommended prices with currency conversion
python update_play_prices.py --use-recommended --fix-currency --convert-currency --apply

# Process in smaller batches
python update_play_prices.py --batch-size 25 --apply

# Enable new subscriber availability
python update_play_prices.py --enable-availability --apply

# Custom configuration
python update_play_prices.py --config my-config.json --apply
```

### Migration

```bash
# Update prices and migrate existing cohorts
python update_play_prices.py --apply \
  --migrate-existing \
  --migrate-cutoff 2025-09-01T00:00:00Z \
  --migrate-increase-type PRICE_INCREASE_TYPE_OPT_IN
```

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

### Currency Mismatches

If your CSV has different currencies than what Google expects for each region:

```bash
# Automatically fix currencies (keeps original prices)
python update_play_prices.py --fix-currency --apply

# Fix currencies and convert amounts
python update_play_prices.py --fix-currency --convert-currency --apply
```

### Large Price Lists

For CSVs with many regions:

```bash
# Process in smaller chunks
python update_play_prices.py --batch-size 25 --apply
```

## CSV Format Details

### Required Columns

- **Countries or Regions**: ISO 3166-1 alpha-3 country codes
- **Currency Code**: ISO 4217 currency codes
- **Price**: Decimal price (e.g., 9.99, 1200, 0.99)

### Supported Countries

The tool supports all countries available in Google Play Console. Use ISO3 codes like:

- USA (United States)
- GBR (United Kingdom)
- DEU (Germany)
- JPN (Japan)
- etc.

See `template_monthly_prices.csv` for a comprehensive list.

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
