from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LIVE_PHRASE = "I UNDERSTAND LIVE ORDERS USE REAL MONEY"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True, hide_input_in_errors=True)
    trading_mode: Literal["PAPER", "LIVE"] = "PAPER"
    alpaca_paper_key: SecretStr = SecretStr("")
    alpaca_paper_secret: SecretStr = SecretStr("")
    alpaca_live_key: SecretStr = SecretStr("")
    alpaca_live_secret: SecretStr = SecretStr("")
    alpaca_data_key: SecretStr = SecretStr("")
    alpaca_data_secret: SecretStr = SecretStr("")
    aegis_control_token: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("sqlite:///var/aegis.db")
    data_feed: Literal["iex", "sip"] = "iex"
    service_enabled: bool = False
    paper_learning_enabled: bool = False
    # Exact registered strategy keys, never names that silently select a new version.
    paper_learning_strategies: str = ""
    paper_learning_max_order_notional: float = Field(default=1000, gt=0, le=1000)
    paper_learning_capital: float = Field(default=2000, gt=0, le=2000)
    paper_learning_daily_loss: float = Field(default=25, gt=0, le=100)
    paper_learning_min_trades: int = Field(default=100, ge=100)
    paper_learning_min_days: int = Field(default=10, ge=10)
    entry_ttl_seconds: int = Field(default=180, ge=30, le=600)
    secure_cookies: bool = False
    live_trading_enabled: bool = False
    live_confirmation_phrase: SecretStr = SecretStr("")
    live_stage: Literal["OBSERVE", "SHADOW", "APPROVAL", "LIMITED_AUTO", "FULL_CONFIGURED"] = "OBSERVE"
    live_capital_limit: float = Field(default=0, ge=0, allow_inf_nan=False)
    live_max_order_notional: float = Field(default=0, ge=0, allow_inf_nan=False)
    flatten_on_kill: bool = False
    overnight_policy: Literal["FLATTEN", "HOLD_PROTECTED"] = "FLATTEN"
    runtime_dir: Path = Path("var")
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    symbols: str = "SPY,QQQ,IWM,AAPL,MSFT,NVDA"
    max_quote_age_seconds: float = Field(default=5, gt=0, le=30)
    scan_interval_seconds: float = Field(default=10, ge=2)
    min_paper_days: int = Field(default=20, ge=1)
    min_paper_trades: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def control_strength(self):
        if self.paper_learning_enabled and self.trading_mode != "PAPER":
            raise ValueError("Paper learning cannot be enabled in LIVE mode")
        token = self.aegis_control_token.get_secret_value()
        if token and len(token) < 32:
            raise ValueError("AEGIS_CONTROL_TOKEN must contain at least 32 characters")
        return self

    def credentials(self, mode=None):
        mode = mode or self.trading_mode
        if mode == "PAPER":
            return self.alpaca_paper_key.get_secret_value(), self.alpaca_paper_secret.get_secret_value()
        if mode == "LIVE":
            return self.alpaca_live_key.get_secret_value(), self.alpaca_live_secret.get_secret_value()
        raise ValueError("Unknown broker mode")

    def data_credentials(self):
        key, secret = self.alpaca_data_key.get_secret_value(), self.alpaca_data_secret.get_secret_value()
        return (key, secret) if key and secret else self.credentials()

    def live_locks(self):
        locks = []
        checks = {
            "TRADING_MODE must be LIVE": self.trading_mode == "LIVE",
            "LIVE_TRADING_ENABLED must be true": self.live_trading_enabled,
            "Explicit live confirmation phrase required": self.live_confirmation_phrase.get_secret_value()
            == LIVE_PHRASE,
            "Dedicated live credentials required": all(self.credentials("LIVE")),
            "LIVE_CAPITAL_LIMIT must be positive": self.live_capital_limit > 0,
            "LIVE_MAX_ORDER_NOTIONAL must be positive": self.live_max_order_notional > 0,
            "Live stage does not permit orders": self.live_stage
            in {"APPROVAL", "LIMITED_AUTO", "FULL_CONFIGURED"},
        }
        for label, passed in checks.items():
            if not passed:
                locks.append(label)
        return locks
