## 6.1 Overview

Onboarding is the process by which a wallet becomes eligible to claim a Star. The system recognizes two trader classes: **Origins** and **Looters**; and routes each through the same verification pipeline before issuing a claim authorization. The classification is structural: it determines what a wallet can claim, when, and how the resulting Star records the claim's provenance.

The architecture is governed by a single user-facing principle: **Verify First, Input Last.** A wallet is fully scanned for eligibility before minting a Star. The system never solicits action from a user it has not first authorized to act. This is a deliberate inversion of the typical claim-then-verify pattern and is the protocol's primary defense against spam, exploratory wallet traffic, and the dilution of the eligibility surface itself.

---

## 6.2 The Two Trader Classes

#### Origins

An **Origin** is a wallet that traded one or more of the Polymarket events archived in the active Season. Origins are the foundational claim class: their eligibility is anchored in direct historical participation in the underlying events. The Star they receive carries the **Ø** claim code in its signature and the **[ ORIGIN SECURED ]** badge on its visual surface.

> **Origins** are eligible from **Day 1** of every Season; they may claim during **Breach**, **Vault**, or **Scavenge**. The Vault phase exists specifically to protect their claim window from competition.

#### Looters

A **Looter** is a wallet that has any verifiable trading history on Polymarket but did not necessarily participate in the active Season's specific events. Looters claim against unclaimed Origin slots, taking over capacity that Origins did not fill. The Star they receive carries the **L** claim code in its signature and the **[ LOOTER TAKEOVER ]** badge on its visual surface.

> **Looters** are eligible during **Breach** and **Scavenge**. They are structurally locked out of **Vault**, which is reserved for Origins only.

The two classes share the same eligibility surface: both must verify a Polymarket trading identity. However, their position in the claim hierarchy differs significantly based on the seasonal phase.

| Attribute | Origins | Looters |
| :--- | :--- | :--- |
| **Claim Code** | `Ø` | `L` |
| **Visual Badge** | `[ ORIGIN SECURED ]` | `[ LOOTER TAKEOVER ]` |
| **Eligible Phases** | Breach, Vault, Scavenge | Breach, Scavenge |
| **Source of Data** | Personal Performance | Unclaimed Pool |
| **Structural Role** | Record Creation | Capacity Utilization |

Origins claim what their participation earned them. Looters inherit the slots that Origins did not claim, facilitated by a season-bound transfer mechanism.

---

## 6.3 The Polymarket Proxy Architecture

Polymarket users do not trade from their **externally owned account (EOA)** directly. Instead, Polymarket assigns each user a **proxy wallet**: a smart contract account associated with the user's EOA that executes trades on the platform. Trade history accumulates on the proxy, not the EOA.

Eligibility verification operates against this proxy. When a user signs into PS with their EOA, the system retrieves the associated proxy wallet from the Polymarket API and runs all subsequent checks against it. In normal operation, this resolution happens silently; the user signs with the EOA they recognize, and the protocol handles the abstraction.

> **Critical Distinction:** The address embedded in the Star's signature and displayed on the Card front is the **proxy wallet address**, not the EOA. The Star records the entity that performed the trade, not the entity that signed the login. While bound, they are not identical.


#### Deposit Wallets

Polymarket has recently introduced **Deposit Wallets** to resolve the "ghost fills" issue. This architectural shift changes how trades are registered on-chain. 

* **Current State:** Verification is bound to the Smart Contract Proxy.
* **Version 1.1+:** The protocol is designed to adapt to this change, eventually expanding the verification layer to incorporate activity registered through Deposit Wallets.

---

## 6.4 The Verification Pipeline

The pipeline runs in two technical steps, perceived by the user as a single action.

#### Step 1 — Authentication (SIWE)

The user connects an EVM wallet (MetaMask, Safe, or any standard Ethereum wallet) and signs a **Sign-In With Ethereum (SIWE)** message. The signature proves wallet ownership without spending gas. The system resolves the EOA from the signature and, in the same operation, retrieves the associated Polymarket proxy wallet via the Polymarket API.

#### Step 2 — Eligibility Scan

With the proxy wallet identified, the system performs a sequential set of checks. The pipeline **fails fast**: the first check that returns a rejection terminates the scan and surfaces an explicit status to the user.

* **Check 1 — Activity.** Does the proxy wallet have a Polymarket trading history? The check queries the Polymarket Leaderboard API. A wallet appears in the leaderboard if and only if it has placed at least one trade on the platform. A wallet present in the leaderboard passes the activity check; a wallet absent from it does not.
    > **A user note:** Polymarket's leaderboard updates approximately **30 minutes** after a wallet's first trade. A user who places a first-ever Polymarket trade and immediately attempts to claim a Star will fail this check until the leaderboard propagates. This is a known propagation latency in the source platform, and the verification will succeed once the trade reaches the leaderboard.
    >
    > The activity check may be tightened in future protocol versions. Possible additions include a minimum cumulative trading volume, a requirement that the wallet have at least one resolved-and-redeemed position, or a minimum account age. None of these are active in v1; the canonical v1 requirement is presence on the Polymarket leaderboard.

* **Check 2 — Double-Claim.** Has the proxy wallet already claimed a Star in the active Season? Each Season maintains a record of claimed wallets; a proxy already present is rejected. The **1-Star-per-wallet-per-Season** rule is enforced at this layer.
    > Genesis and Standard Seasons maintain independent claim state. A wallet that claimed in Genesis remains eligible for the active Standard Season, and vice versa. The rule is one Star per wallet per Season, not one Star per wallet across all Seasons.

* **Check 3 — Supply.** Does the active Season have unclaimed inventory? If the Season has fully depleted, the user is informed that the Season has closed and directed to the next Season's opening date.

* **Check 4 — Phase Authorization.** Does the wallet's class (Origin or Looter) have access during the current phase? The phase access rules are detailed in Section 5; the relevant Section-6 fact is that an Origin always passes this check, while a Looter passes only during Breach and Scavenge.
    > A wallet attempting to claim during the wrong phase is not rejected permanently — they are deferred. The interface surfaces an explicit message indicating the next phase in which they will become eligible, and the verification can be re-run at that point without re-authentication.

#### Outcome: Authorization or Rejection

If all four checks pass, the system enters the **Mint Authorization** state and proceeds to claim execution (Section 6.6).

If any check fails, the system surfaces one of four explicit rejection statuses. The taxonomy is closed: every rejection maps to one of these four states.

| Status | Meaning |
| :--- | :--- |
| `REJECT_NO_ACTIVITY` | The wallet has no Polymarket trading history. |
| `REJECT_ALREADY_CLAIMED` | The wallet has already claimed a Star this Season. |
| `REJECT_NO_SUPPLY` | The Season has fully depleted. |
| `WAIT_NEXT_PHASE` | The wallet's class is currently locked out; eligibility opens at the next phase boundary. |

![alt](/system-manual/ONBOARDING.png)

---

## 6.5 Modular Initialization

When Genesis runs in parallel with an active Standard Season (during Genesis's extended open-claim mode), a single wallet may be eligible for both surfaces simultaneously. The interface handles this through **Modular Initialization**, surfacing each eligibility as an independent claim node.

A single SIWE authentication unlocks both scans in parallel. The dashboard then displays the resolution of each independently:

* **The Genesis Node:** Active if Genesis is open and the wallet has remaining Genesis eligibility.
* **The Standard Node:** Active if the wallet is eligible for the current Standard Season's active phase.

#### Independent Execution

The user must engage with each node independently to claim. The system does not auto-batch the two claims into a single transaction. Each Star is a distinct historical record, and the operator's deliberate engagement with each node is part of the architectural commitment that every check is an explicit act.

* **Outcome:** A wallet eligible for both surfaces and claiming both will produce two Stars.
* **Designators:** One Star will bear the `S0` Genesis designator, while the other bears the active Standard Season's designator.
* **Destination:** Both artifacts are delivered to the same EOA (Externally Owned Account) destination.

---

## 6.6 Mint Authorization

A wallet that passes all eligibility checks enters the Mint Authorization state. From the user's perspective, this is the moment the system returns a successful verification: the eligible Star is identified, the assigned archetype is computed, and the mint is queued.

> The Star is delivered as a custom ERC-721 token on Ethereum mainnet, incorporating royalty-enforcement features standard to the ERC-721C extension family. The user pays no gas for the mint itself; the protocol absorbs all transaction costs. From the user's perspective, the only signature required across the entire claim flow is the initial standard SIWE message.

The mint queue itself is decoupled from the Season clock and may process the transaction at any time within 24 hours of the claim. See Section 5.7 for the queue's operational guarantees.

The archetype assigned to **the Star**, and the logic that determines which archetype is chosen when a wallet has multiple eligible options, are the subject of Section 7 — Assignment Protocol.