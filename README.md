# ha-marktguru
AI generated proof of concept of an Home Assistant integration for Marktguru supermarket offer notifications

The Marktguru Home Assistant integration allows you to display current supermarket offers from the Marktguru search directly as sensors in Home Assistant.
For any products and postal codes, sensors with price, retailer, offer period, image, and other details are automatically created and updated hourly.
New offers appear automatically, expired ones are removed — no restart or manual intervention required.

# Disclaimer
This is not a robust software implementation. It's merely an AI-generated proof of concept. I provide no support and don't have the expertise to develop it into a finished implementation.
Anyone who wants to complete the integration is welcome to join!

# Known Issues
- Additions to the search term list are not saved. Workaround: Delete and recreate configuration.

# Installation & Configuration Guide
## 1. Requirements
Home Assistant (Core, OS, Supervised, or Container)
HACS (Home Assistant Community Store) is recommended, but not required
## 2. Installation
With HACS (recommended)
- Add repository:
Open HACS → “Integrations” → Three-dot menu → “Custom repositories”
→ Enter your GitHub repo URL (e.g., https://github.com/thorschtn/ha-marktguru)
→ Type: Integration
- Install integration:
Search for “Marktguru” in HACS → Install
- Restart Home Assistant

Manual Installation
- Download the repository from GitHub.
- Copy the marktguru folder into your Home Assistant directory:
- <config>/custom_components/marktguru
- Restart Home Assistant.
## 3. Configuration
Add the integration:
Go to
Settings → Devices & Services → Add Integration
Search for “Marktguru” and select it.

Fill in the configuration:

- Postal code:
The ZIP code for which you want to receive offers (e.g., 10115).
- Search terms:
Comma-separated list of products you want to monitor, e.g.:
Havana Rum, Bacardi Spiced, Wernesgruner
Finish:
After saving, the sensors are automatically created.
You can add as many instances of the integration as you like (e.g., for different ZIP codes or product lists).

## 4. Usage
Sensors:
For each found offer, a sensor like
sensor.marktguru_havana_rum_1_10115
is created, with attributes such as price, retailer, offer period, etc.
Automatic updates:
New offers appear automatically, expired ones are removed.
Multiple configurations:
You can monitor the same products for different ZIP codes.
## 5. Example Lovelace Card
marktguru_card.yaml
