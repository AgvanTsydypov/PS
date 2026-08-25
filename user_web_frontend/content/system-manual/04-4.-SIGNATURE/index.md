## 4.1 Overview

Every Star carries a structural signature: a compact identifier encoding the Star's full position within the PS taxonomy. The signature compresses the cryptographic, behavioral, event-level, and seasonal dimensions of a Star into a single fixed-grammar string.

> The signature is not decoration. It is the operational fingerprint of the Star: the basis for filtering the catalog, the persistent identifier across protocol upgrades, the language by which collectors and observers reference specific positions in the system. A signature minted in Genesis remains readable, verifiable, and queryable.

---

## 4.2 Specification and Definitions

`[ARCH]-[P(E)]-[E][Y][G]-[INST]-[CLAIM]-S[N]-[PLATFORM][EVENT_ID]`

The signature resolves to seven structural segments. Each segment carries one dimension of the Star's identity.


* **[ARCH] — Archetype Code**
    Three-letter identifier mapping to one of the thirteen Behavioral Archetypes.

* **[P(E)] — Probability Bracket**
    Single-digit code referencing the Entry Bracket $$P(E)$$. The digit indicates the bracket's lower bound, with the terminal-certainty bracket compressed to `9` to preserve single-digit notation across all six positions.

* **[E][Y][G] — Tier Triplet**
    Three-character sequence encoding **Edge**, **Yield**, and **Gravity** in fixed positional order. Each character resolves to a percentile tier.
    * **Position 1:** Edge.
    * **Position 2:** Yield.
    * **Position 3:** Gravity.
    Positional order is fixed. A reader parsing a signature reads left-to-right: timing, then efficiency, then mass.

* **[INST] — Event Instance**
    Single character indicating the structural form of the source event on Polymarket.

* **[CLAIM] — Claim Class**
    Single character identifying the operator class that initialized the Star.
    The Origin code is rendered as a slashed `Ø` to eliminate visual collision with the digit `0` used in the Probability Bracket field. The character is also functionally evocative: an Origin claim represents the wallet securing its own behavioral record, and the slashed form reads as a marked, completed action.

* **S[N] — Season Designator**
    Season identifier. Genesis is permanently encoded as `S0`. Standard cycles increment from `S1`.
    The `S0` reservation is permanent. No future Star will ever carry an `S0` designator unless minted within the Genesis Epoch.

* **[PLATFORM][EVENT_ID] — Source Token**
    Concatenation of platform code and the platform's native event identifier.
    The PS protocol ingests events exclusively from Polymarket. The `POL` prefix is the only platform code defined at protocol initialization, and the system is designed around Polymarket-native data structures, not generic prediction-market integration. Every signature minted by the `V1` of the protocol carries the `POL` prefix.
    
    > The event identifier is the canonical Polymarket event ID, preserved as-is from the source.

![alt](/system-manual/ASCII_SIGNATURE.png "0.6")

---

## 4.3 Resolution Examples

* **`EXT-9-BB9-S-L-S0-POL25044`**
    * **Archetype:** **Extractor**.
    * **$$P(E)$$ Bracket:** `9` ($$0.97 – 1.00$$).
    * **Tiers:** Edge **Base** / Yield **Base** / Gravity **P90**.
    * **Context:** **Singular** event, **Looter** claim, **Genesis Epoch**.
    * **Source:** Polymarket event `25044`.

* **`ANO-0-XXX-S-O-S3-POL31278`**
    * **Archetype:** **Anomaly**.
    * **$$P(E)$$ Bracket:** `0` ($$0.00 – 0.20$$).
    * **Tiers:** All-tier **P99** alignment (indicated by the `XXX` triple).
    * **Context:** **Singular** event, **Origin** claim, **Standard Season 3**.
    * **Source:** Polymarket event `31278`.

* **`OPR-6-5BB-S-O-S2-POL29841`**
    * **Archetype:** **Operator**.
    * **$$P(E)$$ Bracket:** `6` ($$0.60 – 0.80$$).
    * **Tiers:** Edge **P50** / Yield **Base** / Gravity **Base**.
    * **Context:** **Singular** event, **Origin** claim, **Standard Season 2**. 
    * **Source:** Polymarket event `29841`.

---

## 4.4 Persistence Guarantees

Signatures are computed at the moment of mint and embedded permanently into the Star’s metadata. The format is locked at protocol initialization to ensure long-term stability. Signature semantics are designed to persist through:

* **Visual Geometry Refreshes:** Even if the Card's "look" evolves, the signature remains the same.
* **Ecosystem Expansions:** New platforms or event types will be integrated into the existing grammar.
* **Protocol Upgrades:** The core logic of the behavioral record remains immutable.

> A signature minted in the **Genesis Epoch** remains readable, verifiable, and queryable for the operational lifetime of the network. The grammar does not version, the fields do not migrate, and the codes do not retire.