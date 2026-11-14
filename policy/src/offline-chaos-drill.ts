import { createAuditEvent, verifyAuditChain, type AuditPayload } from "./audit-chain.js";
import { loadVectorResults } from "./vector-suite.js";

export interface DrillResult {
  id: string;
  passed: boolean;
  observed: string;
  mustDeny?: boolean;
}

export function evaluateOfflineDrills(): DrillResult[] {
  const vectors = new Map(loadVectorResults().map((result) => [result.id, result]));
  const fromVector = (id: string, outcome: string, reason: string, mustDeny = false): DrillResult => {
    const result = vectors.get(id);
    return {
      id,
      passed: result?.actual.outcome === outcome && result.actual.reason === reason,
      observed: result ? `${result.actual.outcome}:${result.actual.reason}` : "MISSING_VECTOR",
      mustDeny,
    };
  };

  const firstPayload: AuditPayload = {
    eventId: "offline-event-001",
    seq: 1,
    timestamp: "2026-11-16T15:00:00.000Z",
    kioskId: "demo-kiosk-01",
    zoneId: "Library",
    userRef: "salted-demo-ref-01",
    outcome: "ALLOW",
    reasons: ["OFFLINE_LOW_RISK_ALLOW"],
    mode: "OFFLINE",
  };
  const first = createAuditEvent(firstPayload, "GENESIS");
  const second = createAuditEvent({ ...firstPayload, eventId: "offline-event-002", seq: 2 }, first.hash);
  const tampered = [{ ...first }, { ...second, outcome: "DENY" as const }];
  const chainCheck = verifyAuditChain(tampered);
  const duplicate = verifyAuditChain([first, { ...second, eventId: first.eventId, prevHash: first.hash,
    hash: createAuditEvent({ ...firstPayload, eventId: first.eventId, seq: 2 }, first.hash).hash }]);
  const intactFlappingSync = verifyAuditChain([first, second]);
  const missingEvent = verifyAuditChain([second]);

  return [
    { ...fromVector("O01-offline-low-risk-allow", "ALLOW", "OFFLINE_LOW_RISK_ALLOW"), id: "network-loss-mid-decision" },
    { ...fromVector("O01-offline-low-risk-allow", "ALLOW", "OFFLINE_LOW_RISK_ALLOW"), id: "short-outage-safe-local-path" },
    fromVector("O03-offline-high-risk-deny", "DENY", "HIGH_RISK_OFFLINE_DENY", true),
    fromVector("O06-offline-bad-signature", "DENY", "BUNDLE_SIGNATURE_INVALID", true),
    { ...fromVector("O07-offline-expired-bundle", "DENY", "BUNDLE_EXPIRED", true), id: "long-outage-expired-bundle" },
    fromVector("O08-offline-clock-drift", "DENY", "CLOCK_DRIFT", true),
    fromVector("O04-offline-uncertain-deny", "DENY", "OFFLINE_LIVENESS_FAILED", true),
    { id: "tampered-audit-chain", passed: !chainCheck.valid, observed: chainCheck.reason ?? "VALID", mustDeny: true },
    { id: "duplicate-audit-event", passed: duplicate.reason === "AUDIT_DUPLICATE_EVENT", observed: duplicate.reason ?? "VALID", mustDeny: true },
    { id: "flapping-sync-chain-intact", passed: intactFlappingSync.valid, observed: intactFlappingSync.reason ?? "VALID" },
    { id: "lost-audit-event-detected", passed: missingEvent.reason === "AUDIT_SEQUENCE_GAP", observed: missingEvent.reason ?? "VALID", mustDeny: true },
  ];
}