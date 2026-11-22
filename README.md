# ha-marktguru
AI generated proof of concept of an Home Assistant integration for Marktguru supermarket offer notifications

The Marktguru Home Assistant integration allows you to display current supermarket offers from the Marktguru search directly as sensors in Home Assistant.
For any products and postal codes, sensors with price, retailer, offer period, image, and other details are automatically created and updated hourly.
New offers appear automatically, expired ones are removed — no restart or manual intervention required.

## About this fork

This is a fork of [thorschtn/ha-marktguru](https://github.com/thorschtn/ha-marktguru). All credit for the original integration goes there. The fork adds:

- **Editable settings.** Postal code, search terms and retailer filter can be changed under *Configure* without deleting the integration. The entry reloads itself and sensors of removed search terms are cleaned up.
- **Retailer filter.** Optional comma-separated list (for example `Marktkauf, REWE, EDEKA`); only offers of these retailers become sensors.
- **Second source: meinprospekt.de.** Covers retailers that Marktguru does not list for some regions (for example Marktkauf). It is queried every six hours with the Home Assistant home coordinates. Retailers already delivered by Marktguru are not added twice.
- **More attributes.** `source`, `link` (offer page or brochure page), `grundpreis` and `grundpreis_einheit` (price per kg or litre).
- **Search sensor.** `sensor.marktguru_suche_<zip>` runs a live search in both sources for the text in the helper `input_text.angebote_suche` and returns the hits in its `results` attribute.
- **Fixes.** Unloading the entry works, new offers create sensors during the hourly refresh, and the search-term sensor reports the number of terms instead of a string that could exceed 255 characters.

Both sources are unofficial and can change or stop working at any time. Like the original, this is a hobby project without support.

<img width="1240" height="654" alt="image" src="https://github.com/user-attachments/assets/9fb88174-7c4a-4f0a-b2a2-a770a9655403" />


# Disclaimer
This is not a robust software implementation. It's merely an AI-generated proof of concept. I provide no support and don't have the expertise to develop it into a finished implementation.
Anyone who wants to complete the integration is welcome to join!

# Known Issues
- None known for the search term list in this fork (fixed, see above).
- The search helper `input_text.angebote_suche` has to be created by hand (Settings → Devices & Services → Helpers → Text) if you want to use the search sensor.

# Installation & Configuration Guide
## 1. Requirements
Home Assistant (Core, OS, Supervised, or Container)
## 2. Installation
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
