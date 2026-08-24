## 8.1 Overview

PS is a curated archive. Every Star ever minted refers to a specific Polymarket event, and every event admitted to a Season passes through a deliberate ingestion pipeline that filters, validates, and contextualizes the underlying market before it becomes part of the protocol's record.

This section documents what enters the system, why, and through what architecture. The companion section (9 — Behavioral Substrate) documents what is measured once ingestion produces a clean event surface. Together they form the protocol's data layer. 

**Section 8 answers two distinct questions:**
1. Which Polymarket events are worth archiving?
2. How does the system reliably capture them given the realities of upstream data?

> The pipeline operates on a single architectural premise: **raw API data is insufficient.** Polymarket's feeds are rich but inconsistently structured, exhibiting timing and labeling drift. The protocol’s pipeline ensures that what gets archived is a curated decision, not a passive ingestion.

---

## 8.2 Volume Floors and Selection

The first filter applied to incoming Polymarket events is **volume**: the total USD trading volume accumulated before the event's conclusion. Volume serves as the protocol's primary proxy for cultural significance.

The thresholds differ by Season type:

| Season Type | Volume Threshold | Logic |
| :--- | :--- | :--- |
| **Genesis Epoch** | **$100,000,000+** | Archives the events that defined Polymarket as a significant venue. Includes small sets of curated cultural events. |
| **Standard Seasons** | **$5,000,000+** | The minimum floor for structural significance. If >20 events qualify, the system selects the **top 20** by volume. |

**The Historical Zero:** The archive begins on **June 1, 2024**. This date marks the start of the U.S. 2024 Presidential Election cycle and Polymarket's first sustained mainstream attention. Events resolved prior to this date are ineligible.

---

## 8.3 The `endDate` Problem

A central architectural challenge involves how prediction markets record lifecycles. Two timestamps describe an event's resolution, and they often disagree:

* **`endDate` (Metadata):** The timestamp announced at market launch. It reflects the original framing of the prediction.
* **`closedTime` (Reality):** The timestamp at which the event actually resolves on-chain via the oracle.

#### Patterns of Divergence
1.  **Resolution Lag:** Oracle latency or disputes can push the `closedTime` days or weeks past the `endDate`.
2.  **Early Resolution:** The underlying outcome becomes unambiguous earlier than expected, causing the market to close ahead of the `endDate`.
3.  **Parent-Child Cascade:** An event only transitions to "closed" once *every* underlying market within it has resolved. One slow-resolving market can hold the entire event container open.

> **The Architectural Consequence:** A standard pipeline polling for events based on `endDate` would systematically miss any event with a delayed resolution. By the time those events finally close, a passive script has moved on, creating permanent gaps in the archive. The PS pipeline is specifically designed to account for this drift.

---

## 8.4 The Three-Tier Ingestion Pipeline

The ingestion architecture decouples the **discovery** of events from the **resolution** of events. Both stages operate against the same daily polling cycle but at different points in an event's lifecycle.

#### Tier 1 — Ingestion Catcher

Every night at 2:00 AM UTC, the system queries the Polymarket events API for all events whose `endDate` falls within the past 24 hours. The query deliberately omits any filter on resolution status. Open, closed, disputed, pending: every event matching the date window is retrieved.

* **Filter:** Events are checked against the volume threshold; those failing are dropped immediately.
* **Trigger:** Events passing the threshold are written to the database regardless of their current status. The system treats `endDate` as the capture trigger and resolution status as a downstream concern.

#### Tier 2 — The Waiting Room

Events written to the database carry a "closed" flag. If the flag is false, the event enters the **Waiting Room**—a polling queue. 

* **Function:** Every daily ingestion cycle re-checks every event in the Waiting Room against the API to look for status transitions.
* **Persistence:** The architecture imposes no upper bound. Whether an event takes days or months to resolve, the system will eventually receive a `closedTime` when the oracle finalizes its resolution.
* **Purpose:** This solves the "API Ingestion blind spot," ensuring no event captured at `endDate` is missed simply because it hasn't resolved yet.

#### Tier 3 — The 9-Day Redemption Lag

Once an event receives a confirmed `closedTime`, the system adds **nine days** to that timestamp before scheduling the redemption query.

* **Rationale:** Empirical data shows that for 99% of events, at least 95% of total redemptions are claimed within nine days. This floor captures effectively all meaningful activity without deferring archival indefinitely.
* **Volume Floor:** Markets within the parent event with a `volumeNum` less than $1,000 are dropped. Sub-$1K markets are considered "operational dust" or potential thin-liquidity manipulation.
* **Outcome:** A clean **Origin set** for the event. This set of wallets becomes the eligibility surface for the Season that admits it.

---

## 8.5 Redemption as the Eligibility Anchor

The protocol's Origin eligibility is anchored on **redemption**, not on participation. A trader who placed positions but did not claim a payout(even the fraction of it) is not an Origin.

* **Editorial Choice:** Redemption marks the act of taking a position to resolution. It distinguishes genuine market engagement from arbitrage round-trips, MEV activity, or speculative entry-exit cycles that never reached settlement.
* **Data Integrity:** Redemption data is sourced directly from on-chain smart contract events. It includes a redeemer address, payout amount, and timestamp.
* **Verifiability:** Because the data is based on on-chain state, it is publicly verifiable, immutable, and platform-independent of any API.

> **The Structural Property:** Every Origin in the system is a wallet that took a real position to resolution. The eligibility list represents participants whose interactions actually settled.

---

## 8.6 Event Synthesis

Once an event has been ingested, validated, and resolved into a clean Origin set, the protocol layers an editorial pass over the raw Polymarket metadata. **Synthesis** converts dry API fields into the structured, presentable data that the Card surface and signature ultimately carry.

The challenge is that raw Polymarket fields are often insufficient for archival presentation. Three failure modes recur:

* **Ambiguity:** Titles like "NFL MVP" lack the year or league context needed for a discrete archived moment.
* **Clunky Formatting:** Titles often carry UI artifacts like trailing ellipses or question marks (e.g., "US x Iran diplomatic meeting in person by...?").
* **Over-granularity:** Titles like "Elon Musk # tweets Feb 17-24" function as spreadsheet labels rather than collectible identifiers.

Rather than transforming the title in isolation, the synthesis layer evaluates the title alongside the event description, series metadata, and tag taxonomy simultaneously to produce four distinct outputs:

1.  **The Terminal Header:** A synthesized title (5–7 words) that strips API formatting. This appears at the top of the **Card front**.
2.  **The Event Topology:** A condensed prose interpretation of the market, rewritten from legalistic resolution language into a cold, analytical readout. This appears at the top of the **Card back**.
3.  **Recurrence Classification:** Determining if an event is a one-off (**Singular**) or part of a cycle (**Fractal**), recorded in the metadata badge and the `[INST]` signature field.
4.  **Tag Distillation:** The process of filtering raw tags into structured **Sector** and **Node** classifications.

---

## 8.7 Tag Distillation

Polymarket events often return tag arrays that mix thematic categories with UI mechanics (e.g., *Bitcoin, Crypto, Weekly, Multi Strikes*). Most of these are redundant or irrelevant to archival metadata. The protocol’s distillation layer filters this noise to produce exactly two outputs:

* **The Sector (Primary):** The broad categorical foundation (e.g., *Politics, Crypto, Tech/AI*). This drives the primary color frame on the Card.
* **The Node (Secondary):** The specific focal entity or subject (e.g., *Bitcoin, Jerome Powell, Gemini 3*).

**The Logic:** The system filters out mechanical noise (like "Weekly" or "Multi Strikes"), then identifies the broadest thematic tag as the Sector and the most specific thematic tag as the Node.

| Original Tags | Sector (Broad) | Node (Specific) |
| :--- | :--- | :--- |
| Bitcoin, Crypto, Weekly, Multi Strikes | **Crypto** | **Bitcoin** |
| US Election, Politics, 2024, Recurring | **Politics** | **US Election** |
| AI, Google, Gemini 1.5, Tech | **Tech/AI** | **Gemini 1.5** |

> This ensures the Card metadata is semantically dense and visually clean, focusing only on the intrinsic identity of the event.

---

At the end of the ingestion process, every event admitted to a Season carries a structured profile that includes:

| Component | Source |
| :--- | :--- |
| **Event identifier** | Polymarket platform identifier, preserved verbatim |
| **Resolution** timestamp | On-chain closedTime |
| **Volume** | Polymarket API, validated against threshold |
| **Origin set** | On-chain redemption records, filtered for $1K+ markets |
| **Terminal Header** | Synthesized title, ≤7 words |
| **Event Topology** | Synthesized description |
| **Recurrence** Classification | Singular or Fractal, derived from series metadata |
| **Sector** | Distilled from raw tag array |
| **Node** | Distilled from raw tag array |

> This profile is the input to Section 9, which documents how each Origin's behavior on the event is measured and converted into the four-axis substrate that drives Archetype classification.

The ingestion pipeline runs continuously in the background. Every night the Tier 1 catcher captures new events; every cycle the Waiting Room re-checks lingering events; every resolved event eventually advances to Tier 3 and produces its Origin set nine days after resolution. The protocol's archive grows daily, and Seasons draw from a pre-validated, pre-curated pool when their composition is finalized.

![alt](/system-manual/DATA_INGESTION.png)

