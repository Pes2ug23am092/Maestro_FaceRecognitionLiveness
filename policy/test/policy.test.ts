import assert from "node:assert/strict";
import test from "node:test";
import { decide, type AccessUser, type PolicyBundle } from "../src/policy.js";
import { evaluateOfflineDrills } from "../src/offline-chaos-drill.js";
import { loadVectorResults } from "../src/vector-suite.js";

const now = { wallTime: "2026-11-16T15:00:00.000Z", trustedTime: "2026-11-16T15:00:00.000Z" };
const zone = { id: "Library", risk: "low" as const, allowedGroups: ["student"] };
const user: AccessUser = {
  id: "student-demo",
  group: "student",
  status: "active",
  credentialValid: true,
};
const bundle: PolicyBundle = {
  signatureValid: true,
  issuedAt: "2026-11-16T00:00:00.000Z",
  expiresAt: "2026-11-17T00:00:00.000Z",
  timezone: "America/New_York",
  maxClockDriftMs: 120000,
  mode: "online",
  thresholds: {
    low: { liveAllow: 0.9, liveDeny: 0.3, simAllow: 0.8, simDeny: 0.5, offlineLiveAllow: 0.95 },
    medium: { liveAllow: 0.95, liveDeny: 0.35, simAllow: 0.85, simDeny: 0.55, offlineLiveAllow: 0.98 },
    high: { liveAllow: 0.98, liveDeny: 0.5, simAllow: 0.9, simDeny: 0.65, offlineLiveAllow: 0.995 },
  },
  schedules: [{ group: "student", zoneId: "Library", daysOfWeek: [1, 2, 3, 4, 5], windows: [{ start: "08:00", end: "18:00" }] }],
  calendar: [],
};

test("confident authorized user is allowed during the schedule", () => {
  assert.deepEqual(decide({ live: 0.99, sim: 0.99, quality: true }, user, zone, now, bundle), {
    outcome: "ALLOW",
    reasons: ["OK_IN_SCHEDULE"],
  });
});

test("schedule start is inclusive and the end is exclusive", () => {
  const atStart = { ...now, wallTime: "2026-11-16T13:00:00.000Z", trustedTime: "2026-11-16T13:00:00.000Z" };
  const atEnd = { ...now, wallTime: "2026-11-16T23:00:00.000Z", trustedTime: "2026-11-16T23:00:00.000Z" };
  assert.equal(decide({ live: 0.99, sim: 0.99, quality: true }, user, zone, atStart, bundle).outcome, "ALLOW");
  assert.equal(decide({ live: 0.99, sim: 0.99, quality: true }, user, zone, atEnd, bundle).outcome, "STEP_UP");
});

test("spoof and revoked user are denied", () => {
  assert.equal(decide({ live: 1, sim: 1, quality: true, spoofDetected: true }, user, zone, now, bundle).outcome, "DENY");
  assert.deepEqual(decide({ live: 1, sim: 1, quality: true }, { ...user, status: "revoked" }, zone, now, bundle).reasons, ["USER_REVOKED"]);
});

test("bad bundle signature and clock drift fail closed", () => {
  assert.deepEqual(decide({ live: 1, sim: 1, quality: true }, user, zone, now, { ...bundle, signatureValid: false }).reasons, ["BUNDLE_SIGNATURE_INVALID"]);
  assert.deepEqual(decide({ live: 1, sim: 1, quality: true }, user, zone, { ...now, wallTime: "2026-11-16T16:00:00.000Z" }, bundle).reasons, ["CLOCK_DRIFT"]);
});

test("offline high-risk access is always denied", () => {
  const offlineBundle = { ...bundle, mode: "offline" as const };
  const highRiskZone = { id: "Server-Room", risk: "high" as const, allowedGroups: ["student"] };
  const offlineSchedules = [{ ...bundle.schedules[0]!, zoneId: "Server-Room" }];
  assert.deepEqual(decide(
    { live: 1, sim: 0, quality: true },
    user,
    highRiskZone,
    now,
    { ...offlineBundle, schedules: offlineSchedules },
  ).reasons, ["HIGH_RISK_OFFLINE_DENY"]);
});

test("shared schedule and access-policy vectors all pass", async (context) => {
  const vectors = loadVectorResults();
  assert.ok(vectors.length >= 60, `expected at least 60 vectors, got ${vectors.length}`);
  for (const vector of vectors) {
    await context.test(vector.id, () => {
      assert.deepEqual(vector.actual, vector.expected);
    });
  }
});

test("offline chaos drills fail safely and detect audit-chain tampering", () => {
  const drills = evaluateOfflineDrills();
  assert.equal(drills.length, 11);
  assert.ok(drills.every((drill) => drill.passed), JSON.stringify(drills));
});