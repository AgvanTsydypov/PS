## 10.1 Overview

The protocol's mechanics—phase ordering, supply caps, archetype gates, the racing reservation, and the rarest-wins assignment—are not designed in isolation. They compose a system that exerts continuous structural pressure on its participants. These pressures are not enforced; they are **produced**. They emerge from the intersection of rules, shaping behavior without being explicitly directed at any specific outcome.

This section documents the expected emergent behaviors of the system. These dynamics are observational rather than prescriptive, reflecting how the protocol's mechanics interact with a real population over time. While earlier sections described static mechanics, Section 10 describes the **consequences** of those mechanics.

> The protocol's lifecycle is expected to traverse five distinct behavioral phases. These phases are path-dependent and self-correcting, leading toward an equilibrium meaningfully different from the system's early states.

---

## 10.2 Phase 1 — Looter Saturation

The protocol launches into a population that is overwhelmingly crypto-native but only loosely Polymarket-engaged. The vast majority of early participants qualify as **Looters**: they have basic eligibility (at least one Polymarket trade) but did not participate in the specific archived events required for Origin status.

#### Key Characteristics:
* **Sparse Origin Presence:** Origin claims are rare, coming primarily from sophisticated, pre-existing Polymarket natives who recognize the protocol immediately.
* **Looter-Dominated Issuance:** Most minted Cards carry the `[ LOOTER TAKEOVER ]` badge and the `L` claim code.
* **Invisible Competition:** The Origin Race operates with limited visibility. While the **Vault** phase restricts access to verified Origins, the small population size means the competitive pressure of the supply ceiling is not yet fully felt by the general public.
* **Perception Bias:** A casual observer might conclude that Looter status is the dominant path. In reality, the population shape simply hasn't surfaced the system's full structural intent yet.

#### Phase Comparison: Early vs. Mature

| Metric | Phase 1 (Saturation) | Phase 5 (Equilibrium) |
| :--- | :--- | :--- |
| **Primary Claim Type** | Looter | Origin |
| **Vault Completion** | Rare | Common |
| **Origin Race Intensity** | Low | High |
| **Secondary Market Value** | Speculative | Provenance-driven |

---

## 10.3 Phase 2 — Origin Emergence

As the protocol gains visibility within the broader Polymarket-aware audience, the dormant Origin population begins to surface. Traders who participated in archived events but were not initially attentive to the project become aware, return to claim, and produce a sudden expansion of Origin issuance.

> The visible result is a shift in the Card distribution. Origin status, previously sparse, becomes recognizably present. The `[ ORIGIN SECURED ]` badge starts appearing across more events and more Seasons. The claim hierarchy that the protocol was designed to celebrate—Origins as the foundational claim class, Looters as the secondary inheritance path—becomes legible.


#### The Late-Entry Trap

A direct abuse vector becomes visible at this stage: an operator could enter qualifying events at extreme late-stage prices (0.97 or above), risking minimal capital after consensus has crystallized, in order to qualify as a future Origin without committing meaningful capital. 

The structural defense is the **archetype assignment** itself:

* **The Logic:** Late-stage, high-probability entries map mathematically into the lower-rarity archetypes: **EXTRACTOR** for whale-tier deployments, **PASSENGER** for retail-scale, and **SUBSTRATE** for sub-threshold dust. 
* **The Consequence:** These archetypes carry the Origin badge but not the apex archetypal signatures. 

> In secondary markets, the archetype dimension is expected to dominate rarity pricing. A **SUBSTRATE Origin** is anticipated to trade below an **ANOMALY Looter** despite the formal hierarchy of claim types.

The gaming attempt produces a self-defeating result: the abuser earns prestige along one axis (Origin status) but forfeits it along another (common archetype). The Late-Entry Trap is not an enforcement mechanism; it is a **structural disincentive**, embedded in the rarity model rather than in any explicit prohibition.

#### Calibration Moment

This phase is the protocol's first calibration moment. The population begins to learn that Origin status alone is not the prize. The prize is the **combination of Origin status with rare archetype assignment**, and that combination is not achievable through superficial qualification.

> This shift marks the transition from passive claiming to strategic participation.

---

## 10.4 Phase 3 — Strategic Origin Farming

The protocol's eligibility rules are public, and the System Manual is widely circulated. Looters, having observed the dynamics of previous phases, recognize a new strategic possibility: rather than waiting for random Looter assignments, they can deliberately participate in high-volume Polymarket events to qualify as **Origins** in future Seasons.

This marks the protocol's first observable **feedback effect**. The population begins behaving in response to the system's incentives rather than purely against the system's eligibility surface. Looters become Origins by intent.

#### The Composition of Strategic Farming

The expansion of the Origin population during this phase is structurally biased. Most strategic farmers will naturally gravitate toward the lowest-risk path to qualification: terminal-certainty entries at $0.97$ or above.

* **Archetype Drift:** The Origin population becomes increasingly dominated by **SUBSTRATE**, **OPERATOR**, and **PASSENGER** archetypes. 
* **Gravitational Spikes:** Occasional **GRAVITON** deployments occur where strategic farmers commit heavy capital despite the late-stage entry, seeking the prestige of a high-volume badge.

#### The Looter Information Edge

During this phase, the **Looter Information Edge** sharpens significantly. Sophisticated Looters now have a data set to analyze. By the time the **Scavenge** phase opens on Day 7 of any given Season, the season's Origin claim history is partially visible. 

Looters can actively analyze:
* **Over-representation:** Which archetypes are saturating the claimed slots.
* **Unclaimed Remainders:** Which events have produced the most desirable, yet unminted, outcomes.
* **Expected Rarity:** Which slots offer the highest probabilistic rarity based on the pool's remaining composition.

#### Scavenge as a Speculation Surface

The **Scavenge** phase converts from a passive remainder mechanism into an active speculation surface. Looters who arrive late but informed can outperform those who arrive early and blind. 

The randomness in the Looter assignment layer becomes a contested probabilistic surface rather than a neutral allocation. This dynamic mirrors prediction-market trading itself: the protocol's gameplay rewards the same skills the underlying data celebrates—**timing, calibration, and the willingness to engage when others are uncertain.**

> This phase demonstrates the protocol's ability to influence the very data it archives.

---

## 10.5 Phase 4 — Rarity Inversion

The combination of common-Origin saturation and random-rarity Looter assignment produces an outcome the protocol's static rules do not directly predict, but which its dynamics make likely: **Looters, on average, begin to receive rarer archetypes than the average Origin.**

#### The Mechanism of Inversion
The Origin population, increasingly dominated by terminal-certainty farmers, clusters heavily in the common archetypes (Substrate, Passenger). Meanwhile, the unclaimed Origin pool that Looters compete for retains the full archetypal distribution of the original eligibility list—including the rare apex slots surrendered by inactive Origins. 

Looter assignment, drawing randomly from this pool, statistically captures a more diverse and high-rarity archetype distribution than the strategic-Origin population produces.

* **The Formal Hierarchy:** Origin is the privileged, foundational class; Looter is the secondary inheritance path.
* **The Observed Reality:** The average Looter Card carries a rarer archetype than the average Origin Card.

#### Rarity as Behavior, Not Class

> This is not a system failure. It is the protocol surfacing its core design philosophy: **rarity is a property of behavioral pattern, not of claim type.** The hierarchy is not a flat `Origin > Looter` ranking, but a two-dimensional surface of `(Archetype × Claim Type)`. The rarest artifacts are those that combine apex archetypes with their corresponding claim types, regardless of the route taken.

#### The Apex Recursion

Phase 4 reveals the power of the **Apex Recursion**. Each Origin's archetype is determined by their single rarest behavioral pattern across all qualifying events in the season's window. 

* **Quality over Quantity:** A trader does not need consistent performance. They need exactly one moment of genuine predictive accuracy under non-trivial conditions. 
* **Reward Structure:** The mechanic celebrates the trader who took one well-calibrated bet over the participant who took fifty mediocre ones.

#### The Strategic Deadlock
This phase produces an uncomfortable observation for participants:
1.  **Terminal-certainty farming** produces Origin status with common archetypes.
2.  **Random Looter assignment** produces Looter status with potentially rare archetypes.

Neither path consistently produces the rarest cards in the ecosystem. The rarest cards require a shift in strategy that moves beyond simple qualification.

> This realization leads directly into the final stage of evolution. 

---

## 10.6 Phase 5 — Skill Recalibration

The population observes the inversion. Sophisticated participants recognize that the truly rare Card is the one that combines **Origin status with a rare archetype**. 

Producing this combination requires neither strategic farming (which produces common archetypes) nor Looter randomness (which cannot produce Origin status). It requires **genuine predictive engagement**: early entries, structurally meaningful capital commitment, and calibrated conviction in the actual underlying outcomes.

#### The Rising Skill Bar

The skill bar for the rarest Cards rises. The Origins who produce apex Cards—**INSIDER**, **ANOMALY**, **EXTRACTOR** with strong percentile alignment, or **EQUILIBRIUM** at heavyweight tiers—are participants who engage with Polymarket events as predictive instruments rather than as eligibility checkpoints. 

>The protocol's mechanics, having filtered out superficial qualification through the **Late-Entry Trap** and contested random allocation via the **Looter Information Edge**, now select for what the protocol was designed to celebrate: **real skill applied to real markets.**

#### The Maturity of the Detection Layer

During this phase, the protocol’s commitment to transparency bears fruit. The system publishes per-wallet provenance data and per-season statistical distributions. Because this data is public, the community can analyze patterns:
* **Entry Timing:** When did the capital actually hit the market?
* **Sizing:** Was the commitment meaningful relative to the event’s total volume?
* **Statistical Anomalies:** Wallets that consistently enter only at **0.97** or above, only on resolution day, and only with minimum qualifying volume are exposed.

#### Social vs. Cryptographic Legitimacy

The protocol expects, though it does not enforce, a divergence between technical and social standing:

1.  **Technical Compliance:** The on-chain layer determines who *can* claim based on the rules.
2.  **Social Legitimacy:** The community, operating on the same public data, determines whose claim is *respected*.

> A Card minted under technical compliance but visibly produced through gaming behavior is anticipated to carry diminished cultural value. The Star becomes more than a digital receipt; it becomes a reputation artifact.

#### The Equilibrium State

| Layer | Function | Equilibrium Outcome |
| :--- | :--- | :--- |
| **Mechanical** | Eligibility & Assignment | Prevents direct protocol abuse via mathematical gates. |
| **Economic** | Supply & Demand | Values apex archetypes over superficial Origin status. |
| **Social** | Interpretation & Respect | Differentiates genuine market skill from mechanical farming. |

> The trajectory of the protocol is self-correcting. By the time it reaches this final phase, the system has successfully transformed from a passive archive into an active incentive structure that rewards the highest levels of human predictive performance.

---

## 10.7 The Equilibrium

The phases are not strictly sequential. They overlap, recur, and operate at different intensities across different Seasons. New Looters enter the system at all stages; new Origins emerge throughout the protocol's lifetime; strategic farming and skill-based participation coexist in any given Season's claim history.

> However, the trajectory is directional. The protocol’s mechanics, applied over time, produce a system that progressively rewards the form of engagement it was designed to celebrate: **predictive skill demonstrated on real events with real capital.**

#### The Evolution of Rarity

The early phases—Looter saturation, Origin emergence, and strategic farming—are transient population states that the system traverses before reaching the equilibrium its design implies.

* **Initial State:** Rarity is a function of who found the protocol first.
* **Intermediate State:** Rarity is contested by strategic farmers attempting to "game" the eligibility surface.
* **Equilibrium State:** Rarity is a function of genuine market performance. 

> At equilibrium, the rarest Cards are produced by participants engaging with the system as it was intended. **Origin status alone is not scarce; quality Origin status is.** The population of rare Cards becomes the population of traders demonstrating real predictive skill on real events.

#### The Geometry of Selection

This is the ultimate purpose of the architecture. The rules do not enforce skill; they cannot. But the rules, composed and applied across a real population over time, produce a structural pressure toward skill that is more durable than any explicit enforcement mechanism could be.

> The protocol selects for what it was designed to celebrate—not because an authority is checking, but because the system’s geometry, once lived in, makes that selection **inevitable**.
