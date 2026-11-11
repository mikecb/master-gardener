"""Constants for Master Gardener."""

DOMAIN = "master_gardener"

SUBENTRY_AREA = "area"
# Never stored: the flow records plantings and ends.
SUBENTRY_PLANTING = "planting"
SUBENTRY_MANAGE = "manage_plantings"
SUBENTRY_REVIEW = "review_proposals"

CONF_ZIP_CODE = "zip_code"
CONF_ZONE = "zone"
CONF_LAST_FROST = "last_frost"
CONF_FIRST_FROST = "first_frost"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
# The weather station the frost dates were last looked up from.
CONF_FROST_STATION = "frost_station"
CONF_USE_STATION = "use_station"

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
CONF_ANNUAL_BUDGET = "annual_budget"
# A Simple Inventory sensor for the inventory that holds the seed.
CONF_SEED_INVENTORY = "seed_inventory"
SEED_INVENTORY_DOMAIN = "simple_inventory"

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

# The property map is a grid this many columns wide; rows grow as needed.
MAP_COLUMNS = 24
MAP_MAX_ROWS = 60
MAP_MIN_SIZE = 2

# How far back overdue tasks are kept, and how far ahead the calendar plans.
LOOKBACK_DAYS = 180
LOOKAHEAD_DAYS = 400
# Completed items stay ticked on the to-do list this long.
DONE_VISIBLE_DAYS = 7
