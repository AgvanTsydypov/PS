## 7.1 Overview

Onboarding determines whether a wallet is eligible to claim. Assignment determines what the wallet receives when it claims. The two stages are distinct, and the second is where the system's most interesting architectural commitments live.

Two operator classes produce two assignment paths:
* **Origins** receive Stars through a deterministic computation against their historical participation.
* **Looters** receive Stars through a randomized selection from the unclaimed slot pool.

The destination is the same: a custom ERC-721 token in the user's EOA, but the route is structurally different. Every Star is verifiable on-chain by default. Its metadata records the wallet, the event, the archetype, the season, the claim type, and the full signature.

---

## 7.2 Origin Assignment — The Max Rarity Protocol

An Origin is a wallet that traded one or more of the Season's archived events. The system resolves multiplicity through a deterministic rule: **Max Rarity wins.**

* **The Scan:** The protocol scans the wallet's participation and identifies the behavioral pattern with the lowest population frequency (e.g., INSIDER outranks ANOMALY).
* **The Reward:** The wallet receives a Star bearing the rarest behavioral pattern it qualifies for, paired with the specific event on which that pattern was exhibited.
* **Determinism:** Given a wallet's historical trades, any observer can compute the resulting assignment. The system retains no discretion.
* **Tie-Breaking:** If a wallet qualifies for two archetypes at the same rarity tier, the tie is broken by a Python pseudorandom function seeded against the `wallet_address` and `season_id`.

> **Structural Consequence:** Quantity does not compound; behavioral rarity dominates. The protocol rewards what a wallet did at its peak, not how often it participated. The artifact records the wallet's structurally most distinctive moment.

---

## 7.3 Looter Assignment — Randomized Pool Selection

A Looter is a wallet with verifiable Polymarket trading history but no participation in the active Season's specific archived events. Looters claim from the inventory that Origins did not claim.

* **Queue Mechanics:** Looter assignment operates on a continuous queue. Each pending Looter is processed as the queue reaches them, drawing against the unclaimed slot pool as it exists at that specific moment.
* **Randomness:** Internally generated using a deterministic pseudorandom function seeded against a per-Season value generated at Season open.
* **Pool Composition:** The pool consists of all Origin-eligible slots minus those already claimed or reserved.
* **Path Dependency:** The assignment is genuinely random within the available pool. A Looter cannot predict or game which Star they will receive; the slot is a function of what Origins surrendered and what the randomness selects.

![alt](/system-manual/Assignment.png)

---

## 7.4 Reproducible Operations

The protocol's architectural integrity rests on a single commitment: every operational decision is reproducible from public data. This property, **Reproducible Operations**, governs how the protocol publishes, executes, and persists its actions.

Two classes of artifact are published for each Season:

* **The Eligibility Manifest.** Before a Season opens, a complete JSON manifest is published to IPFS. It contains every eligible wallet, every archived event, earned archetypes, and four-axis substrate values. The manifest is content-addressed (IPFS CID), and the CID is announced publicly at Season launch with timestamp anchoring.
* **The Mint Record.** Every minted Star exists on-chain as a queryable token. Metadata records the assigned archetype, source event, season, claim type, and complete signature.

Together, these artifacts permit an external observer to verify that every minted Star corresponds to a wallet in the published manifest and that the eligibility surface was anchored at the moment of publication.

> **Note:** This integrity property is operational, not cryptographic. Future versions (v2) will introduce Merkle commitments and Chainlink VRF to move from post-hoc audit to real-time enforcement. The v1 data model is designed to support this upgrade without restructuring.

---

## 7.5 Trust Surfaces

Reproducible Operations is a functional property, but it is not yet fully trustless. The protocol is transparent about its current architectural boundaries.

#### What the system enforces:
* **Immutability:** The IPFS manifest cannot be modified without changing its CID.
* **Permanence:** Every minted Star is on-chain and tamper-evident.
* **Auditability:** Deviations between the manifest and on-chain mints are detectable by any observer.

#### What requires operator honesty (detectable post-hoc):
* **Accuracy:** Computing the eligibility manifest correctly from the Polymarket API.
* **Consistency:** Publishing and adhering to the announced CID.
* **Fairness:** Generating Looter assignments honestly from the published surface (cryptographic anchoring deferred to v2).
* **Execution:** Processing mints strictly against the published manifest.

> The architecture is built so that any deviation from these commitments produces a visible inconsistency between the public artifacts and the on-chain mint history. Detection is not the same as prevention, but it is the structural foundation that makes prevention possible in v2. When cryptographic anchoring closes the gap between what the protocol promised and what the protocol can do.