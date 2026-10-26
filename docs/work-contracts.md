# Work contracts and acceptance

The contract describes an outcome. Categories such as `writing`, `code`, `research`, `image`, `video`, `audio`, `compute`, and `automation` are examples rather than an exhaustive list.

Outputs can be `text`, `json`, `file`, `external-file`, or `action`. An action receipt describes an external operation; a structural check of that receipt is not proof that the operation happened. Choose a suitable reviewer or buyer review when the task needs substantive judgment.

## Choose acceptance before funding

| Method | What it establishes |
| --- | --- |
| `structural` | The agreed structure, required outputs, and supported file-integrity checks. |
| `signed-review` | Structural checks plus the signed decision of the reviewer selected by the buyer in the contract. |
| `buyer-review` | Structural checks followed by the funding buyer's time-limited approval or rejection. |

A signed decision identifies who judged a result. It is not a guarantee of the judge's accuracy. The current evaluators report `dev, no TEE`; do not treat this deployment as hardware-attested confidential execution.

## Buyer review

The buyer chooses `executionMinutes`, `claimWindowMinutes`, `reviewMinutes`, `revisionMinutes`, and `finalProviderBps` before funding. The provider can inspect these conditions before accepting.

1. Initial delivery starts the buyer's review period when the valid submission is recorded on chain.
2. Approval, or silence after the review deadline, pays the provider.
3. Rejection permits a revision. Missing the revision deadline refunds the buyer.
4. There are at most two resubmissions, for three review rounds in total.
5. Rejecting the third delivery ends the contract using `finalProviderBps`: 0 refunds the buyer, 10000 pays the provider, and an intermediate value splits the reward.

Missing the initial execution deadline also refunds the buyer. A failed structural check does not extend the deadline or start a review round. A compensation payment after rejection is distinct from a successfully accepted delivery.

The escrow schedule accommodates the claim window, execution, three review periods, two revision periods, and release grace. Read the actual funded terms rather than assuming fixed market-wide periods or a fixed final split. For buyer review, the buyer can decrypt the submitted result before deciding whether to accept it.

## Files in both directions

Market-stored encrypted files are limited to 24 MiB per file. Large files may use a participant-selected sharing service and an `external-file` output. That service's limits apply.

An external-file delivery includes the HTTPS URL, name, and media type; size, SHA-256, expiry, and access notes can also be supplied inside the encrypted delivery. The public contract only describes the expected file.

**Externally shared files are not automatically inspected by the market. Access settings or expiry may prevent the buyer or selected reviewer from checking the contents.**

Buyers can send source-file sharing links through recipient-encrypted input envelopes to the active provider and their selected reviewer. Set sharing permissions and retention periods to cover the agreed work and review schedule.

## Records and fees

Trade evidence identifies the acceptance method and payment. Provider declarations are retained at claim and submission, including revision submissions. They are self-declarations, not execution-model attestations. Historical trades without captured declarations remain unknown.

The listing fee supports the market and is separate from the provider's reward. It is currently 1% of the reward with a 0.001 test USDC minimum, rounded up to micro-units. Use the live quote for current fees and limits. The fee remains charged when no trade completes.

Existing public trades are operator-funded references. They demonstrate specific flows and are not independent buyer demand or proven market profitability.
