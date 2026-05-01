// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title  SLOPNFT
 * @notice ERC-721 collection with on-chain royalty enforcement via an
 *         operator allowlist. Owner-initiated transfers (P2P) are always
 *         allowed; secondary-market trades only succeed through marketplaces
 *         the admin has whitelisted (Seaport / OpenSea Conduit by default).
 *
 *         Roles:
 *           DEFAULT_ADMIN_ROLE — manages royalties, operator allowlist,
 *                                contract URI, role grants/revocations.
 *           MINTER_ROLE        — may call mintTo. Held by the cron worker.
 */
contract SLOPNFT is ERC721URIStorage, ERC2981, AccessControl {

    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");

    // Seaport 1.6 — same address on all chains it is deployed to (incl. Sepolia).
    address public constant SEAPORT_1_6 = 0x0000000000000068F116a894984e2DB1123eB395;
    // OpenSea default Conduit — Seaport delegates ERC-721 transferFrom calls
    // through this contract, so this is the address that actually appears as
    // msg.sender during a sale executed via OpenSea.
    address public constant OPENSEA_CONDUIT = 0x1E0049783F008A0085193E00003D00cd54003c71;

    uint256 private _nextTokenId;
    string  private _contractMetadataURI;

    mapping(address => bool) public allowedOperator;
    bool public transferRestrictionsEnabled = true;

    event OperatorAllowedSet(address indexed operator, bool allowed);
    event TransferRestrictionsSet(bool enabled);
    event ContractURISet(string newURI);

    error OperatorNotAllowed(address operator);

    constructor(
        address admin,
        address treasury,
        address minter,
        uint96  royaltyBps,
        string memory contractURI_
    )
        ERC721("SLOP", "SLOP")
    {
        require(admin    != address(0), "admin=0");
        require(treasury != address(0), "treasury=0");
        require(minter   != address(0), "minter=0");

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MINTER_ROLE,        minter);

        _setDefaultRoyalty(treasury, royaltyBps);
        _contractMetadataURI = contractURI_;

        // Default allowlist: Seaport + the conduit OpenSea actually transfers through.
        allowedOperator[SEAPORT_1_6]     = true;
        allowedOperator[OPENSEA_CONDUIT] = true;
        emit OperatorAllowedSet(SEAPORT_1_6,     true);
        emit OperatorAllowedSet(OPENSEA_CONDUIT, true);
    }

    // ── Mint ──────────────────────────────────────────────────────────────────

    function mintTo(address to, string calldata uri)
        external
        onlyRole(MINTER_ROLE)
        returns (uint256 tokenId)
    {
        tokenId = _nextTokenId++;
        _safeMint(to, tokenId);
        _setTokenURI(tokenId, uri);
    }

    function totalMinted() external view returns (uint256) {
        return _nextTokenId;
    }

    // ── Operator allowlist (royalty enforcement) ──────────────────────────────

    function setOperatorAllowed(address operator, bool allowed)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        allowedOperator[operator] = allowed;
        emit OperatorAllowedSet(operator, allowed);
    }

    function setTransferRestrictions(bool enabled)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        transferRestrictionsEnabled = enabled;
        emit TransferRestrictionsSet(enabled);
    }

    // ── Royalty admin (ERC-2981) ──────────────────────────────────────────────

    function setDefaultRoyalty(address receiver, uint96 feeNumerator)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        _setDefaultRoyalty(receiver, feeNumerator);
    }

    function setTokenRoyalty(uint256 tokenId, address receiver, uint96 feeNumerator)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        _setTokenRoyalty(tokenId, receiver, feeNumerator);
    }

    // ── Contract-level metadata (OpenSea collection page) ─────────────────────

    function contractURI() external view returns (string memory) {
        return _contractMetadataURI;
    }

    function setContractURI(string calldata newURI)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        _contractMetadataURI = newURI;
        emit ContractURISet(newURI);
    }

    // ── Transfer hook: enforce operator allowlist ─────────────────────────────

    function _update(address to, uint256 tokenId, address auth)
        internal
        override(ERC721)
        returns (address from)
    {
        from = super._update(to, tokenId, auth);

        // Allow mint (from == 0), burn (to == 0), and owner-initiated transfers.
        // Block everything else unless the caller is in the operator allowlist.
        if (
            transferRestrictionsEnabled
            && from != address(0)
            && to   != address(0)
            && _msgSender() != from
        ) {
            if (!allowedOperator[_msgSender()]) {
                revert OperatorNotAllowed(_msgSender());
            }
        }
    }

    // ── Required overrides for multi-inheritance ──────────────────────────────

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721URIStorage, ERC2981, AccessControl)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
