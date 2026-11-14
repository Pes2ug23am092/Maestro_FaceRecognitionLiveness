import { createHash } from "node:crypto";

export interface AuditPayload {
  eventId: string;
  seq: number;
  timestamp: string;
  kioskId: string;
  zoneId: string;
  userRef: string;
  outcome: "ALLOW" | "STEP_UP" | "DENY";
  reasons: string[];
  mode: "ONLINE" | "OFFLINE";
}

export interface AuditEvent extends AuditPayload {
  prevHash: string;
  hash: string;
}

export function createAuditEvent(payload: AuditPayload, prevHash: string): AuditEvent {
  const unsigned = { ...payload, prevHash };
  return { ...unsigned, hash: hashAuditPayload(unsigned) };
}

export function verifyAuditChain(
  events: AuditEvent[],
  anchor = "GENESIS",
  anchorSequence = 0,
): { valid: boolean; reason?: string } {
  let expectedPreviousHash = anchor;
  const seenEventIds = new Set<string>();
  for (const [index, event] of events.entries()) {
    if (event.seq !== anchorSequence + index + 1) return { valid: false, reason: "AUDIT_SEQUENCE_GAP" };
    if (seenEventIds.has(event.eventId)) return { valid: false, reason: "AUDIT_DUPLICATE_EVENT" };
    seenEventIds.add(event.eventId);
    if (event.prevHash !== expectedPreviousHash) return { valid: false, reason: "AUDIT_PREV_HASH_MISMATCH" };
    const { hash, ...unsigned } = event;
    if (hashAuditPayload(unsigned) !== hash) return { valid: false, reason: "AUDIT_HASH_MISMATCH" };
    expectedPreviousHash = hash;
  }
  return { valid: true };
}

function hashAuditPayload(payload: Omit<AuditEvent, "hash">): string {
  return createHash("sha256").update(JSON.stringify(payload)).digest("hex");
}