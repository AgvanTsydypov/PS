Because the Genesis Epoch and Standard Seasons run concurrently, a single wallet may possess overlapping authorization. To maximize operator engagement while maintaining a frictionless UI, the network utilizes a **Modular Execution** logic gate.

When an operator triggers the `[ VERIFY ]` command, the system performs a unified scan across all active temporal frameworks simultaneously.

**1. Unified Telemetry Scan:**
* **Genesis Check:** Is the operator eligible? (Yes/No)
* **Standard Check:** Is the operator eligible for the *current active phase*? (Yes/No)

**2. Decoupled Extraction:** Rather than auto-deploying multiple assets in a single batch script, the dashboard illuminates independent execution nodes based on the verified scan results. The operator must consciously command the extraction for each timeline:
* **The Genesis Node:** If authorized, the operator must explicitly interact with the Genesis interface to initialize their Star.
* **The Standard Node:** If authorized, the operator must explicitly interact with the Standard Season interface to initialize their Star.
* **The Lockout:** If an operator lacks authorization for a specific node (e.g., attempting to breach The Vault as a Looter), that specific module remains structurally locked, outputting an access denial (e.g., "*ACCESS DENIED: Await Phase 3 - The Scavenge*").

*System Note: This decoupled design forces the operator to engage directly with each specific historical matrix they have conquered, acknowledging each distinct extraction rather than passively absorbing them*