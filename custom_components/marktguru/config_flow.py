import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, CONF_PRODUCTS, CONF_ZIP, CONF_RETAILERS

def parse_products(products_str):
    result = []
    for item in products_str.split(","):
        q = item.strip()
        if q:
            result.append({"name": q, "query": q})
    return result

class MarktguruConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marktguru."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Validierung
            if not user_input.get(CONF_ZIP):
                errors["base"] = "zip_required"
            elif not user_input.get(CONF_PRODUCTS):
                errors["base"] = "products_required"
            else:
                # Produkte string zu Liste parsen
                products = parse_products(user_input[CONF_PRODUCTS])
                if not products:
                    errors["base"] = "products_invalid"
                else:
                    user_input[CONF_PRODUCTS] = products
                    return self.async_create_entry(
                        title=f"Marktguru ({user_input[CONF_ZIP]})",
                        data=user_input,
                    )

        default_zip = user_input.get(CONF_ZIP) if user_input else ""
        default_products = user_input.get(CONF_PRODUCTS) if user_input else ""
        default_retailers = user_input.get(CONF_RETAILERS, "") if user_input else ""

        schema = vol.Schema({
            vol.Required(CONF_ZIP, default=default_zip): str,
            vol.Required(CONF_PRODUCTS, default=default_products): str,
            vol.Optional(CONF_RETAILERS, default=default_retailers): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "products_help": "Kommagetrennte Liste von Suchbegriffen, z.B.: Havana Rum, Bacardi Spiced, Wernesgruner"
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MarktguruOptionsFlowHandler()

class MarktguruOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Marktguru."""

    async def async_step_init(self, user_input=None):
        errors = {}

        if user_input is not None:
            products = parse_products(user_input[CONF_PRODUCTS])
            if not products:
                errors["base"] = "products_invalid"
            else:
                user_input[CONF_PRODUCTS] = products
                return self.async_create_entry(title="", data=user_input)

        # Optionen haben Vorrang vor den Daten der Ersteinrichtung
        current = {**self.config_entry.data, **self.config_entry.options}
        zip_code = current.get(CONF_ZIP, "")
        products = current.get(CONF_PRODUCTS, "")
        retailers = current.get(CONF_RETAILERS, "") or ""
        if isinstance(products, list):
            products_str = ", ".join(p["query"] for p in products)
        else:
            products_str = products

        schema = vol.Schema({
            vol.Required(CONF_ZIP, default=zip_code): str,
            vol.Required(CONF_PRODUCTS, default=products_str): str,
            vol.Optional(CONF_RETAILERS, description={"suggested_value": retailers}): str,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "products_help": "Format: Produktname1:Query1, Produktname2:Query2, ..."
            }
        )
