import os
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

# Alpaca
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER      = True

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL      = "claude-opus-4-7"

# ntfy
NTFY_TOPIC        = os.getenv("NTFY_TOPIC")

# Trading
SYMBOL            = "SPY"
POSITION_SIZE_PCT = 0.20
STOP_LOSS_PCT     = 0.025

# Lookback periods for feature calculation
LOOKBACK_DAILY    = 60
LOOKBACK_4H       = 20
LOOKBACK_1H       = 5

# Schedule (Europe/Vienna)
DECISION_TIMES = [
    {"hour": 16, "minute": 0},
    {"hour": 19, "minute": 30},
    {"hour": 22, "minute": 10},
]
DAILY_REVIEW_TIME     = {"hour": 22, "minute": 15}

# Strategy review
STRATEGY_REVIEW_EVERY = 20
