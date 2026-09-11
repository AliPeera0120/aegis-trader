# Broker boundary

`BrokerAdapter` defines account, positions, order reads, submit, replacement, cancellation, closes, asset metadata, calendar, clock and trade updates. `AlpacaPaperBroker` and `AlpacaLiveBroker` use `alpaca-py` 0.44.0, the official SDK installed for this build. PAPER always reads the dedicated paper key/secret and constructs `TradingClient(..., paper=True)`. Loading live credentials does not redirect it.

Broker entry orders use a whole-share DAY limit entry with an Alpaca-native stop/target bracket and extended hours disabled. The risk-approved price is a hard buy ceiling or short-sale floor. Market and stop entry styles are available in research; actual broker entries deliberately remain bounded limits. Protective stop orders can gap and do not guarantee a maximum realized loss.

The generic replacement method refuses changes that could enlarge exposure or loosen protection; pending entries must be canceled and re-proposed through risk. Trailing/time exit research is implemented. Native live stop amendment and trailing-order workflows require further paper qualification.

SDK exceptions are replaced by a generic operation name, without raw provider messages or secrets. Readiness is rechecked at the live adapter's submission boundary. No browser automation, unofficial brokerage APIs, scraping or LLM is used for execution.
