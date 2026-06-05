EXCHANGES = (
    "binance",
    "okx",
    "bybit",
    "coinbase",
    "upbit",
    "bithumb",
    "kucoin",
    "gate",
    "mexc",
    "bitget",
    "kraken",
)

LISTING_TYPE_SPOT = "spot"
LISTING_TYPE_FUTURES = "futures"
LISTING_TYPE_PERPETUAL = "perpetual"
EVENT_FAMILY_SPOT_LISTING = "spot_listing"
EVENT_FAMILY_FUTURES_LISTING = "futures_listing"

STATUS_TBD = "TBD"
STATUS_ANNOUNCED = "announced"
STATUS_TRADING_SOON = "trading_soon"
STATUS_TRADING_STARTED = "trading_started"
STATUS_UNKNOWN = "unknown"

SOURCE_PRECEDENCE_X = 10
SOURCE_PRECEDENCE_BLOG = 20
SOURCE_PRECEDENCE_ANNOUNCEMENT = 30

EXCHANGE_TIMEZONES = {
    "binance": "UTC",
    "okx": "UTC",
    "bybit": "UTC",
    "coinbase": "America/New_York",
    "upbit": "Asia/Seoul",
    "bithumb": "Asia/Seoul",
    "kucoin": "UTC",
    "gate": "UTC",
    "mexc": "UTC",
    "bitget": "UTC",
    "kraken": "UTC",
}
