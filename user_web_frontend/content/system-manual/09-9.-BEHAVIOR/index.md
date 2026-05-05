## 9.1 Overview

Section 8 documents how the protocol decides which Polymarket events to archive and how the Origin set for each event is identified. Section 9 documents what happens to those Origins once they are identified: how the protocol measures their behavior on the event, what mathematical quantities it derives from raw trading data, and how those quantities become the **four-axis substrate** that drives Archetype classification.

> Every Star ever minted refers to a wallet's behavior on a specific event. Behind every Star is a measurement: the wallet's capital deployment, its timing relative to other participants, its profitability, and its size. The behavioral substrate is the layer that produces these measurements from raw on-chain activity.

#### Measurement as an Editorial Act

The architectural premise of this section is that **measurement is editorial.** The protocol does not simply read trading values off the platform; it computes them according to specific definitions that reflect deliberate choices about what trader behavior means:

* **CWAP** rather than VWAP.
* **Per-event aggregation** rather than per-position.
* **Capital efficiency** rather than absolute return.

> Each definition is a decision about what the protocol considers structurally significant.

#### The Data Scope

A point worth surfacing at the outset: the protocol's eligibility gate operates on **redemption**, but its measurement surface operates on **all trading activity.**

1.  **Entry:** A wallet enters the Origin set by virtue of having claimed a payout from the event.
2.  **Analysis:** Once admitted, the wallet's full trading record on *every* market within the event is loaded. This includes every fill, every price, and every position size, regardless of whether each individual trade resolved into a redemption.

> The redemption is the entry credential; the full historical record of the event is what the system measures.

---

## 9.2 The Closed Positions Substrate

For every Origin wallet identified in Section 8, the protocol queries the Polymarket API for the wallet's closed positions on each market within the event. The endpoint returns one record per (wallet, market) pair, summarizing the wallet's complete activity. Three fields anchor every subsequent computation: `avgPrice`, `totalBought`, and `realizedPnl`.

> The protocol does not consume these at face value. Each is a raw input for a deliberate transformation into the substrate quantities used by the classification engine.

#### Capital Deployed (Total Volume)

A subtle property of the Polymarket data model is that the `totalBought` field represents the number of **shares** acquired, not the **capital** deployed. Since share prices range from $0.01 to $0.99, treating shares as volume produces distorted measurements. (e.g., 10,000 shares at 0.01 is only $100 USDC).

The protocol computes capital deployed as the dollar-weighted sum across the wallet's fills:


$$Total\,Volume = \sum_{i=1}^{n} (Price_i \times Shares_i)$$
---
> This represents the wallet's true USDC commitment—the actual amount of "skin in the game." **Total Volume** is the substrate for the **Gravity** axis, answering how much capital was committed in real dollar terms.
---
#### Realized PnL

The closed positions endpoint reports realized profit and loss as a single aggregate value per market. This figure accounts for both:
1. **Active trading:** Selling shares before market resolution.
2. **Holding to resolution:** Winning shares being redeemed at $1.00 by settlement logic.

The protocol sums these per-market values to produce the wallet's event-level realized PnL:


$$Total\,PnL = \sum_{i=1}^{n} RealizedPnL_i$$
---
> **Note:** The protocol does not stitch separate redemption records into this PnL computation. While the redemption table establishes **eligibility**, the closed positions data already reflects full settlement value. Pulling redemption data separately would result in double-counting.
---
#### ROI (Return on Investment)

Absolute PnL measures wealth; ROI measures skill. A wallet turning $400 into $800 demonstrates higher capital efficiency than one turning $10,000 into $11,000. The **Yield** axis ranks efficiency, requiring absolute PnL to be normalized against capital deployed.

The protocol computes ROI as:


$$ROI = \left( \frac{\sum_{i=1}^{n} RealizedPnL_i}{\sum_{i=1}^{n} (Price_i \times Shares_i)} \right) \times 100$$
---
> **ROI** is the substrate for the **Yield** axis. It identifies wallets that generated significant returns relative to their commitment, regardless of absolute dollar scale.
---
#### CWAP (Capital-Weighted Average Price)

The fourth quantity is the wallet's effective **entry probability**. Since Polymarket prices are implied probabilities, a wallet buying across multiple price points needs an aggregate measurement of the probability landscape it entered.

The protocol uses **Capital-Weighted Average Price**:

$$CWAP = \frac{\sum_{i=1}^{n} (Price_i \times Capital_i)}{\sum_{i=1}^{n} Capital_i}$$
---
Where each fill is weighted by the dollar capital deployed at that price. 

* **CWAP of 0.18:** Deep contrarian conviction.
* **CWAP of 0.92:** Near-terminal certainty.

> CWAP is the substrate for the **Entry Bracket** axis. It is computed at the market level and then aggregated to the event level by capital-weighting across all markets the wallet traded within the parent event.

---

## 9.3 Hedge Negation

A wallet may take opposing positions on related markets within the same event (e.g., buying YES on one market and NO on a correlated counterpart). These positions partially or fully cancel each other out; the wallet's actual directional exposure is the **net** of the two, rather than the gross of either.

Direct aggregation that simply adds total capital across all positions would overstate the wallet's true exposure. A wallet with $10,000 long and $9,000 short does not have $19,000 of real conviction; it has **$1,000 of net directional bet** wrapped in $9,000 of arbitrage flow.

* **The Adjustment:** The protocol negates hedged exposure at the event-aggregation step. For each wallet, the system identifies opposing exposures and reduces them to the net before computing event-level CWAP and Volume.
* **Structural Consequence:** Pure arbitrageurs who hedge symmetrically typically resolve to near-zero net exposure. Regardless of their volume, they do not register as high-conviction archetypes like *Insider* or *Anomaly*. Instead, their pattern (zero net directional outcome) routes them through the **BOT** Archetype gate.

---

## 9.4 The Kinetic Dust Filter

After event-level aggregation and hedge negation, a final floor filter is applied: wallets whose effective CWAP rounds below 0.001 are removed from the eligibility surface.

* **The Cutoff:** This captures participants whose participation, while technically present in the redemption record, involves sub-tenth-of-a-cent effective entry positions.
* **Architectural Purpose:** The filter ensures every wallet entering the percentile-ranking layer carries a measurable capital footprint. Without this floor, the percentile distributions for **Edge, Yield, and Gravity** would be distorted by "long tails" of dust participants, diluting the meaningful statistical range.

---

## 9.5 The Four Axes — Computation

With CWAP, total volume, and realized PnL aggregated to the event level for each surviving wallet, the protocol computes the four classification axes documented in Section 2.

| Axis | Computation | Source Quantity | Type |
| :--- | :--- | :--- | :--- |
| **Entry Bracket** | CWAP bucketed into 6 intervals | Capital-Weighted Avg Price | Absolute |
| **Edge** | `PERCENT_RANK(CWAP)` inverted | Capital-Weighted Avg Price | Per-event percentile |
| **Yield** | `PERCENT_RANK(ROI)` | Realized PnL / Total Volume | Per-event percentile |
| **Gravity** | `PERCENT_RANK(Total Volume)` | $\sum(Price \times Shares)$ | Per-event percentile |

#### Axis Definitions

* **Axis 1 — Entry Bracket** is computed directly from the event-level CWAP. The wallet's CWAP value falls into one of six absolute intervals (`[0.00 – 0.20]`, `[0.20 – 0.40]`, `[0.40 – 0.60]`, `[0.60 – 0.80]`, `[0.80 – 0.97]`, `[0.97 – 1.00]`). This bucketing is absolute and does not depend on other participants.
* **Axis 2 — Edge** ranks the wallet's CWAP against the event's full surviving population. The ranking inverts: the lowest CWAP values (earliest/deepest entries) produce the highest Edge percentiles.
* **Axis 3 — Yield** ranks the wallet's ROI relative to other participants. It measures capital efficiency. Yield is computed only on profitable wallets; losing and zero-PnL wallets are routed to Layer 1 Archetypes before ranking begins.
* **Axis 4 — Gravity** ranks the wallet's total capital deployed. The larger the capital footprint relative to others, the heavier the gravitational presence.

#### Tier Thresholds

The three percentile-ranked axes (Edge, Yield, Gravity) are bucketed into five tiers.

| Percentile | Tier |
| :--- | :--- |
| $\geq 0.99$ | P99 |
| $\geq 0.90$ | P90 |
| $\geq 0.70$ | P70 |
| $\geq 0.50$ | P50 |
| $< 0.50$ | Base |

The thresholds are asymmetric to reflect the power-law distribution of trading populations. The **P99** tier captures the extreme top 1%, preserving the scarcity of elite performance. Spreading common performance across **P50** and **Base** prevents artificial noise where the population naturally clusters around the median.

> These four axes—one absolute and three relative—form the complete foundation for Archetype classification. Section 2 documents how the engine consumes these inputs to assign the final Card identity.

---

## 9.6 Per-Event Partitioning

A property worth naming explicitly: percentile rankings are computed **per event**, not across the protocol's full population.

This is a deliberate choice. Polymarket events vary widely in size—some attract a few hundred participants, while others draw tens of thousands. A global ranking would compare a wallet's performance against participants in completely different events with divergent population dynamics. 

Per-event partitioning ensures **internal coherence**:
* A wallet ranked **P90** on Edge for the U.S. Presidential Election is in the top 10% of that specific population.
* A wallet ranked **P90** on Edge for a small crypto niche event is in the top 10% of that population.
* **Semantic Meaning:** Both labels mean the same thing relative to the immediate peer group.

> The trade-off is that absolute performance across events is not directly comparable (a P99 among 200 people vs. 20,000 people). However, the protocol accepts this because global rankings would compare structurally incomparable groups. Furthermore, the individual event is the core of the collectible memorabilia; its internal hierarchy is what matters most.

---

## 9.7 Polymarket Global Rank

Each Origin wallet receives one final piece of metadata not derived from the event-specific substrate: the wallet's **all-time rank** on the Polymarket platform leaderboard. This is sourced from the Polymarket API at the moment of event ingestion.

* **Platform-Level Statistic:** This reflects lifetime profitability across *every* event the wallet has ever participated in on Polymarket, not just the one being archived.
* **Temporal Capture:** The rank is recorded at the moment the Card is minted and **frozen**. Future leaderboard movements do not update the Star. A Card displaying rank #847 reflects that specific historical standing.
* **Platform Tribute:** The Global Rank is included as an acknowledgment of the substrate platform. It is a Polymarket achievement, permanently etched onto the Polystars artifact.

---

## 9.8 The Empirical Foundation

The computations described in this section were not designed in the abstract. They were calibrated against the actual Polymarket population—every wallet that placed and redeemed positions on every qualifying event in the protocol's archive window. The diagnostic ran across millions of wallets, spanning the full historical surface of the system as it existed at protocol design time.

Three findings from this diagnostic shaped the architecture as documented:

* **Distribution Shape**
    Trading populations do not distribute uniformly across the four axes. They cluster heavily at terminal-certainty entries (the `[0.97–1.00]` bracket alone accounts for over **58%** of the platform population) and thin out dramatically at deep contrarian entries.
    > * *Architectural Response:* The Layer 2 Consensus routing (separating EXTRACTOR, PASSENGER, and SUBSTRATE before the Apex Core layer) exists specifically because terminal-certainty traders would otherwise overwhelm the percentile distributions for apex Archetypes. Without Layer 2, INSIDER and ANOMALY would be functionally unreachable.

* **Hedging Prevalence**
    A non-trivial fraction of wallets exhibited symmetric hedging patterns across event sub-markets.
    > * *Architectural Response:* Hedge negation is a core requirement, not an edge case. Without it, BOT wallets would systematically register as high-Gravity participants and pollute the GRAVITON tier.

* **End-date Drift**
    A meaningful fraction of events resolved more than a week past their announced `endDate`.
    > * *Architectural Response:* The 3-Tier Pipeline (specifically the Waiting Room) was designed to solve this. Without it, the protocol would have permanently missed approximately **25%** of qualifying high-volume events during the diagnostic period.
