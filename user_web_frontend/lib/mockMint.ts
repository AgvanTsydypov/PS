export type MockMintResult = {
  claim_id: string;
  card: {
    image_url: string;
    title: string;
    lore: string;
    rarity: "common" | "rare" | "epic" | "legendary";
    hex_colors: { primary: string; secondary: string };
    event_slug: string;
  };
  tx_hash: string;
};

const MOCK_CARDS: MockMintResult["card"][] = [
  {
    image_url: "/pack/cards/card-1.png",
    title: "THE ELECTION ORACLE",
    lore: "Forged from a million whispered predictions on a single November night.",
    rarity: "epic",
    hex_colors: { primary: "#FF4000", secondary: "#0000FF" },
    event_slug: "us-election-2024",
  },
  {
    image_url: "/pack/cards/card-2.png",
    title: "BITCOIN ASCENDANT",
    lore: "When the chain whispered six figures and the bears went silent.",
    rarity: "legendary",
    hex_colors: { primary: "#F7931A", secondary: "#000000" },
    event_slug: "btc-100k-2024",
  },
  {
    image_url: "/pack/cards/card-3.png",
    title: "FED PIVOT WATCHER",
    lore: "Patience rewarded by a single dovish syllable.",
    rarity: "rare",
    hex_colors: { primary: "#00FF88", secondary: "#003322" },
    event_slug: "fed-rate-cut",
  },
];

export function mockMint(opts: { delayMs?: number } = {}): Promise<MockMintResult> {
  const delay = opts.delayMs ?? 1_000;
  return new Promise((resolve) => {
    setTimeout(() => {
      const card = MOCK_CARDS[Math.floor(Math.random() * MOCK_CARDS.length)];
      resolve({
        claim_id: `mock-${Math.random().toString(36).slice(2, 10)}`,
        card,
        tx_hash: `0x${Array.from({ length: 64 }, () => Math.floor(Math.random() * 16).toString(16)).join("")}`,
      });
    }, delay);
  });
}
