"""Constants for Master Gardener."""

DOMAIN = "master_gardener"

SUBENTRY_AREA = "area"

CONF_ZIP_CODE = "zip_code"
CONF_ZONE = "zone"
CONF_LAST_FROST = "last_frost"
CONF_FIRST_FROST = "first_frost"

CONF_NAME = "name"
CONF_STYLE = "style"
CONF_LIGHT = "light"
CONF_SIZE_SQFT = "size_sqft"
CONF_ADJACENT = "adjacent"
CONF_CHARACTER = "character"
CONF_MOISTURE_SENSORS = "moisture_sensors"
CONF_CONDUCTIVITY_SENSORS = "conductivity_sensors"
CONF_DRY_BELOW = "dry_below"
CONF_FEED_BELOW = "feed_below"

CONF_WEATHER_ENTITY = "weather_entity"

CARE_WATER = "water"
CARE_FEED = "feed"
CARE_FROST = "frost"
CARE_HEAT = "heat"

STYLES = [
    "vegetable",
    "orchard",
    "cottage",
    "english",
    "japanese",
    "mediterranean",
    "conifer_woodland",
    "native_woodland",
    "meadow",
    "lawn",
    "other",
]
LIGHTS = ["full_sun", "part_shade", "shade", "mixed"]

# How far back overdue tasks are kept, and how far ahead the calendar plans.
LOOKBACK_DAYS = 180
LOOKAHEAD_DAYS = 400
# Completed items stay ticked on the to-do list this long.
DONE_VISIBLE_DAYS = 7
