Standard polling relies on estimated `endDate` parameters, creating a critical blind spot when on-chain oracle resolutions (`closedTime`) are delayed. To ensure zero data drift, Polystars utilizes a decoupled, three-tiered chronological queue.

[ TIER 1: INGESTION ] ———> [ TIER 2: WAITING ROOM ] ———> [ TIER 3: AGNOSTIC LAG ]

* **Tier 1 (The Catcher):** Sweeps the trailing 24 hours based on estimated end dates. Bypasses strict resolution booleans to capture delayed events.
* **Tier 2 (Status Polling):** Unresolved events are held in network stasis. The system continuously polls the oracle until a definitive `closedTime` timestamp is stamped.
* **Tier 3 (Agnostic Lag):** A strict 9-day buffer is mathematically added to the `closedTime`.
  * *System Note: While automated redemptions have been introduced at the core protocol level, the 9-Day Lag remains hardcoded as an agnostic fallback mechanism, ensuring structural integrity across diverse, multi-chain prediction environments.*