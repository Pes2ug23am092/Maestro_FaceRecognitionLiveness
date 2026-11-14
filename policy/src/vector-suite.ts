import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { decide, type AccessUser, type BiometricVerdict, type Outcome, type PolicyBundle } from "./policy.js";

interface Seed {
  timezone: string;
  bundle: Omit<PolicyBundle, "mode" | "thresholds" | "schedules" | "calendar">;
  thresholds: PolicyBundle["thresholds"];
  zones: Array<{ id: string; risk: "low" | "medium" | "high"; allowedGroups: string[] }>;
  groups: string[];
  schedules: PolicyBundle["schedules"];
  calendar: PolicyBundle["calendar"];
  users: AccessUser[];
}

interface Expected {
  outcome: Outcome;
  reason: string;
}

interface MatrixGroup {
  group: string;
  userId: string;
  permittedZones: string[];
  requiresEscort?: boolean;
}

interface VectorCase {
  id: string;
  userId: string;
  zoneId: string;
  at: string;
  mode?: "online" | "offline";
  biometric?: "pass" | "uncertain" | "spoof" | "mismatch" | "poorQuality";
  credentialValid?: boolean;
  secondFactorVerified?: boolean;
  wallTime?: string;
  bundle?: Partial<Pick<PolicyBundle, "signatureValid" | "issuedAt" | "expiresAt">>;
  expected: [Outcome, string];
}

interface VectorDocument {
  matrix: {
    at: string;
    zoneIds: string[];
    groups: MatrixGroup[];
    expected: Record<string, Expected>;
  };
  cases: VectorCase[];
}

export interface VectorResult {
  id: string;
  expected: Expected;
  actual: Expected;
}

export function loadVectorResults(): VectorResult[] {
  const configDirectory = resolve(process.cwd(), "../config");
  const seed = JSON.parse(readFileSync(resolve(configDirectory, "seed_campus.json"), "utf8")) as Seed;
  const vectors = JSON.parse(readFileSync(resolve(configDirectory, "policy_vectors.json"), "utf8")) as VectorDocument;
  const reasonCatalog = JSON.parse(readFileSync(resolve(configDirectory, "reasons.json"), "utf8")) as Record<string, { outcome: Outcome }>;
  const results: VectorResult[] = [];
  if (seed.zones.length !== 8 || seed.groups.length !== 7 || seed.schedules.length !== 25
      || seed.calendar.length !== 4 || seed.users.length !== 12) {
    throw new Error("Synthetic campus seed must contain 8 zones, 7 groups, 25 schedules, 4 events, and 12 users");
  }

  const expectedReasons = [
    ...Object.values(vectors.matrix.expected),
    ...vectors.cases.map((vector) => ({ outcome: vector.expected[0], reason: vector.expected[1] })),
  ];
  for (const expected of expectedReasons) {
    if (reasonCatalog[expected.reason]?.outcome !== expected.outcome) {
      throw new Error(`Missing or inconsistent reason code: ${expected.reason}`);
    }
  }

  for (const group of vectors.matrix.groups) {
    for (const zoneId of vectors.matrix.zoneIds) {
      const zone = seed.zones.find((item) => item.id === zoneId);
      const expected = !group.permittedZones.includes(zoneId)
        ? vectors.matrix.expected.unauthorized
        : group.requiresEscort && zoneId === "Admin-Office"
          ? vectors.matrix.expected.escort
          : zone?.risk === "high"
            ? vectors.matrix.expected.highRisk
            : vectors.matrix.expected.allowed;
      if (!expected) throw new Error(`Missing matrix expectation for ${group.group}/${zoneId}`);
      results.push(runCase({
        id: `MATRIX-${group.group}-${zoneId}`,
        userId: group.userId,
        zoneId,
        at: vectors.matrix.at,
        expected: [expected.outcome, expected.reason],
      }, seed));
    }
  }

  for (const vector of vectors.cases) results.push(runCase(vector, seed));
  return results;
}

function runCase(vector: VectorCase, seed: Seed): VectorResult {
  const originalUser = seed.users.find((item) => item.id === vector.userId);
  const zone = seed.zones.find((item) => item.id === vector.zoneId);
  if (!originalUser || !zone) throw new Error(`${vector.id}: unknown user or zone in seed data`);

  const user: AccessUser = {
    ...originalUser,
    ...(vector.credentialValid === undefined ? {} : { credentialValid: vector.credentialValid }),
    ...(vector.secondFactorVerified === undefined ? {} : { secondFactorVerified: vector.secondFactorVerified }),
  };
  const testInstant = Date.parse(vector.at);
  const bundle: PolicyBundle = {
    ...seed.bundle,
    issuedAt: new Date(testInstant - 60 * 60 * 1000).toISOString(),
    expiresAt: new Date(testInstant + 6 * 60 * 60 * 1000).toISOString(),
    ...(vector.bundle ?? {}),
    timezone: seed.timezone,
    mode: vector.mode ?? "online",
    thresholds: seed.thresholds,
    schedules: seed.schedules,
    calendar: seed.calendar,
  };
  const biometric = getBiometric(vector.biometric ?? "pass");
  const now = { wallTime: vector.wallTime ?? vector.at, trustedTime: vector.at };
  const result = decide(biometric, user, zone, now, bundle);
  const expected = { outcome: vector.expected[0], reason: vector.expected[1] };
  return { id: vector.id, expected, actual: { outcome: result.outcome, reason: result.reasons[0] ?? "" } };
}

function getBiometric(preset: NonNullable<VectorCase["biometric"]>): BiometricVerdict {
  switch (preset) {
    case "uncertain": return { live: 0.70, sim: 0.90, quality: true };
    case "spoof": return { live: 0.99, sim: 0.99, quality: true, spoofDetected: true };
    case "mismatch": return { live: 0.99, sim: 0.20, quality: true };
    case "poorQuality": return { live: 0.99, sim: 0.99, quality: false };
    default: return { live: 0.999, sim: 0.999, quality: true };
  }
}