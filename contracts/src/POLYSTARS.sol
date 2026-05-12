// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title  POLYSTARS
 * @notice ERC-721 collection with ERC-2981 royalty signaling and
 *         operator allowlist restrictions for approved marketplace transfers.
 *
 *         Direct owner-initiated transfers are always allowed.
 *         Operator-mediated transfers only succeed while restrictions are
 *         enabled if the operator is allowlisted.
 *
 *         The ERC-721 contract serves as the permanent collection layer.
 *         Seasonal issuance rules are enforced by the authorized protocol
 *         minter/orchestration layer, not by a fixed collection-wide cap.
 *
 *         Roles:
 *           DEFAULT_ADMIN_ROLE — manages royalties, operator allowlist,
 *                                contract URI, role grants/revocations.
 *           MINTER_ROLE        — may call mintTo.
 */
contract POLYSTARS is ERC721URIStorage, ERC2981, AccessControl {

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
        ERC721("POLYSTARS", "POLYSTARS")
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

    // ── Approval hooks: block approvals to non-allowlisted operators ──────────

    function approve(address to, uint256 tokenId)
        public
        override(ERC721, IERC721)
    {
        if (
            transferRestrictionsEnabled &&
            to != address(0) &&
            !allowedOperator[to]
        ) {
            revert OperatorNotAllowed(to);
        }

        super.approve(to, tokenId);
    }

    function setApprovalForAll(address operator, bool approved)
        public
        override(ERC721, IERC721)
    {
        if (
            transferRestrictionsEnabled &&
            approved &&
            !allowedOperator[operator]
        ) {
            revert OperatorNotAllowed(operator);
        }

        super.setApprovalForAll(operator, approved);
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