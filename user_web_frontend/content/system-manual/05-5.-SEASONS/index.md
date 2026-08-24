## 5.1 Overview

The PS ecosystem does not operate in flat endlessness, but in strict, mathematical cycles. To preserve the scarcity of historical data and the integrity of each archival window, the network is segmented into isolated operational periods: **Seasons**. Each Season is bound to a specific collection of resolved Polymarket events, and each Season produces a fixed supply of Stars that are minted and then permanently closed.

The system runs two temporal frameworks simultaneously: 

* **The Genesis Epoch:** Boots the ecosystem with a foundational supply of **2,000 Stars**.
* **The Standard Cycle:** Produces **333 Stars per Season** at a rate of 36 Seasons per year. 

> Both share the same phase architecture; only their initialization, supply ceiling, and depletion behavior differ.

---

## 5.2 The Phase Matrix

Every Season, Genesis or Standard, navigates four sequential phases. The system advances to the next phase when either the time limit expires or the supply trigger fires, whichever comes first.

* **Phase 1 — The Breach (Days 1–3)**
  The opening kinetic window. The network is open to all eligible operators.
  * **Access:** Global. Origins and Looters both eligible.
  * **Supply Ceiling:** 20% of the Season's total pool.
  * **Termination:** 72 hours elapsed; or/and the 20% cap is reached.
  
  The Breach is intentionally aggressive. If the 20% cap is consumed in the first hour, the phase seals immediately and the network advances further. The cap exists to prevent a single coordinated rush from absorbing the Season's entire supply at the open and to guarantee that downstream phases retain meaningful inventory for Origins.

* **Phase 2 — The Vault (Days 4–6)**
  The cryptographic lockdown. Access is restricted to wallets with verified Origin status.
  * **Access:** Restricted. Origins only.
  * **Supply Ceiling:** Remaining supply.
  * **Termination:** 72 hours elapsed; or/and full pool depletion.
  
  The Vault is the architectural commitment to Origins. For 72 hours, only wallets that traded the Season's underlying events can claim. Looters are structurally locked out of this window and must wait for the Scavenge.

* **Phase 3 — The Scavenge (Days 7–9)**
  The final claim window. All structural restrictions are lifted.
  * **Access:** Global. Origins and Looters both eligible.
  * **Supply Ceiling:** Remaining supply.
  * **Termination:** Full pool depletion or end of Day 9.
  
  Any Stars surviving Day 9 **permanent burn** of their respective slot. The supply is not carried over to the next Season; the slots are deleted from the protocol entirely. A Season that closes with unminted inventory is closing with permanent reduction in the year's Star count.

* **Phase 4 — The Transmission (24 Hours)**
  The administrative phase. The network is closed to claims.
  * **Access:** Zero.
  * **Supply Ceiling:** Zero.
  * **Termination:** 24-hour fixed countdown.
  
  During Transmission, the system performs operations required to forge the next Season:
  1.  **Manifest Publication:** The upcoming Season's eligibility manifest is finalized and published to IPFS.
  2.  **Queue Processing:** Any pending Looter assignments from the closing Season complete processing.
  3.  **Public Announcement:** Content identifiers (CIDs) and upcoming event lists are made public.

#### Phase Summary Table

| Phase | Duration | Supply Cap | Access | Termination |
| :--- | :--- | :--- | :--- | :--- |
| **1. Breach** | 3 days | 20% | Global | Time *or* Cap |
| **2. Vault** | 3 days | Remaining | Origins | Time *or* Depletion |
| **3. Scavenge** | 3 days | Remaining | Global | Depletion *or* Hard Stop |
| **4. Transmission** | 24 hours | None | Locked | Timer |

> **Note on Cadence:** Phase durations are backend-mutable. While the 9-day default is the initial calibration, the network may transition to a tighter 6-day or 3-day cycle during high-engagement periods without altering the structural logic.

![alt](/system-manual/DIAGRAM_SEASONS.png)

---

## 5.3 The Genesis Epoch

The Genesis Epoch boots the ecosystem. It carries a hardcoded supply of **2,000 Stars** and follows the same four-phase architecture as a Standard Season.

Genesis deviates from Standard behavior only at **Phase 3**. If the Genesis Scavenge does not deplete by the end of Day 9, and Transmission then completes, Genesis enters an **extended open-claim mode**. In this mode, the remaining Genesis supply continues to be claimable in parallel with the active Standard Season. Standard Season 1 commences on its scheduled launch date regardless of Genesis depletion status, and traders eligible for both surfaces claim them as independent actions.


#### Comparison: Standard vs. Genesis

| Behavior | Standard Season | Genesis Epoch |
| :--- | :--- | :--- |
| **Phase Structure** | Breach → Vault → Scavenge → Transmission | Breach → Vault → Scavenge → Transmission |
| **Phase 3 Hard Stop** | Day 9 — unclaimed Stars burned | Day 9 — Stars carried into extended open-claim |
| **Termination** | Permanent close after Transmission | Permanent close on full depletion |
| **Supply Pool** | 333 Stars | 2,000 Stars |

> The extended open-claim mode does not add new phases or restrictions. The supply simply remains accessible until the 2,000-Star pool depletes, at which point Genesis closes permanently.

---

## 5.4 Cycle and Cadence

Standard Seasons run continuously. The **Transmission** of Season $$N$$ flows directly into the **Breach** of Season $$N+1$$ with no buffer between them. 

* **The Full Cycle:** Breach (3) + Vault (3) + Scavenge (3) + Transmission (1) = **10 days**.
* **Annual Yield:** 36 Seasons per year, producing **11,988 Standard Stars** annually.
* **Calendar Buffer:** The five remaining days of the calendar year are reserved as an end-of-year recalibration buffer, during which no Season is active.

---

## 5.5 Season Composition Rules

Each Season is bound to a curated set of Polymarket events. Composition is governed by three constraints applied in priority order.

#### Volume Floor
Standard Seasons admit Polymarket events that exceed **$$5,000,000 USD$$** in resolved trading volume. This threshold is the calibrated minimum for structural significance. 
* **Tracking Logic:** The protocol records events at the moment they cross the volume threshold, decoupling the archive from Polymarket's published end-dates, which are prone to drift. 
* **Genesis Exception:** Genesis events are subject to a higher floor of **$$100,000,000 USD$$** in resolved volume, supplemented by team-curated additions of high cultural significance.

#### The 50% Per-Event Cap
No single event may supply more than **50%** of a Season's total Star pool. 
* **Standard Limit:** In a pool of 333 Stars, no event contributes more than **166 Stars**. 
* **Enforcement:** Events exceeding this share are downsampled before the Season opens to ensure the archive is not dominated by a single market.

#### The 25% Per-Tag Diversity Rule
No single Polymarket tag (category) may account for more than **25%** of the events admitted to a Season. 
* **Granularity:** This operates at the **event level**, not the Star level. 
* **Minimum Variety:** This requires every Standard Season to span at least **four distinct tag categories**. 
* **Override:** If fewer than four distinct tags are available globally for the period, this rule is overridden by necessity.

#### Constraint Interaction
The 50% event cap and 25% tag rule interact to push Seasons toward broader participation. A Season cannot easily satisfy both if the largest event shares its tag with others in the set. 

**Structural Result:** The architecture optimizes for **visual and thematic variance** across the collected output. Instead of archiving one massive resonant event, the system favors a mix of five or six events of varied size and category, ensuring the archive remains a diverse cross-section of global prediction activity.