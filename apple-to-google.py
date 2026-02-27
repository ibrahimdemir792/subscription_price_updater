#!/usr/bin/env python3
"""
Use this script to convert prices downloaded from apple connect to compatible google play console prices.
"""

import csv
import pycountry

# =============================================================================
# CONFIGURATION - Edit these variables to change input/output files
# =============================================================================

# Input file: Apple Connect CSV with country names and prices
INPUT_FILE = "Starting Price 2.csv"

# Output file: Google Play Console CSV with country codes (will be updated in place)
OUTPUT_FILE = "Luxury-Weekly.csv"

# =============================================================================

# Manual mapping for country names that don't match pycountry exactly
COUNTRY_NAME_OVERRIDES = {
    "Cape Verde": "CPV",
    "China mainland": "CHN",
    "Congo, Democratic Republic of the": "COD",
    "Congo, Republic of the": "COG",
    "Côte d'Ivoire": "CIV",
    "Côte d\u2019Ivoire": "CIV",  # Right single quotation mark variant
    "Czech Republic": "CZE",
    "Eswatini": "SWZ",
    "Hong Kong": "HKG",
    "Korea, Republic of": "KOR",
    "Kosovo": "XKS",
    "Laos": "LAO",
    "Macau": "MAC",
    "Moldova": "MDA",
    "North Macedonia": "MKD",
    "Russia": "RUS",
    "São Tomé and Príncipe": "STP",
    "St. Kitts and Nevis": "KNA",
    "St. Lucia": "LCA",
    "St. Vincent and the Grenadines": "VCT",
    "Taiwan": "TWN",
    "Tanzania": "TZA",
    "Türkiye": "TUR",
    "United Arab Emirates": "ARE",
    "United Kingdom": "GBR",
    "United States": "USA",
    "Vietnam": "VNM",
    "Bolivia": "BOL",
    "Venezuela": "VEN",
    "Brunei": "BRN",
    "Micronesia": "FSM",
    "British Virgin Islands": "VGB",
    "Turks and Caicos Islands": "TCA",
    "Cayman Islands": "CYM",
    "Trinidad and Tobago": "TTO",
    "Antigua and Barbuda": "ATG",
    "Bosnia and Herzegovina": "BIH",
}


def get_country_code(country_name: str) -> str | None:
    """Get ISO 3166-1 alpha-3 country code from country name."""
    # Check overrides first
    if country_name in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[country_name]
    
    # Try exact match
    try:
        country = pycountry.countries.get(name=country_name)
        if country:
            return country.alpha_3
    except (KeyError, AttributeError):
        pass
    
    # Try fuzzy search
    try:
        results = pycountry.countries.search_fuzzy(country_name)
        if results:
            return results[0].alpha_3
    except LookupError:
        pass
    
    return None


def main():
    # Read prices from input file (Apple Connect CSV)
    prices_by_code: dict[str, str] = {}
    unmatched_countries: list[str] = []
    
    print(f"Reading prices from: {INPUT_FILE}")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            country_name = row["Countries or Regions"]
            price = row["Price"]
            
            code = get_country_code(country_name)
            if code:
                prices_by_code[code] = price
            else:
                unmatched_countries.append(country_name)
    
    if unmatched_countries:
        print(f"Warning: Could not find codes for: {unmatched_countries}")
    
    print(f"Found prices for {len(prices_by_code)} countries")
    
    # Read output file (Google Play Console CSV) and update prices
    print(f"Updating prices in: {OUTPUT_FILE}")
    updated_rows: list[list] = []
    updated_count = 0
    header = None
    
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)  # Preserve original header
        
        for row in reader:
            if not row or not row[0]:  # Skip empty rows
                continue
            
            country_code = row[0]  # Column A: Countries or Regions
            
            if country_code in prices_by_code:
                old_price = row[2] if len(row) > 2 else ""  # Column C: Price
                new_price = prices_by_code[country_code]
                if old_price != new_price:
                    print(f"Updating {country_code}: {old_price} -> {new_price}")
                    updated_count += 1
                row[2] = new_price  # Update only Price column
            
            updated_rows.append(row)
    
    # Write updated output file
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)  # Write original header
        writer.writerows(updated_rows)
    
    print(f"\nUpdated {updated_count} prices in {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
