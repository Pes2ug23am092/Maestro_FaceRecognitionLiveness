export type Outcome = "ALLOW" | "STEP_UP" | "DENY";
export type Risk = "low" | "medium" | "high";
export type Mode = "online" | "offline";

export interface BiometricVerdict {
  live: number;
  sim: number;
  quality: boolean;
  spoofDetected?: boolean;
}

export interface AccessUser {
  id: string;
  group: string;
  status: "active" | "suspended" | "revoked" | "expired";
  validFrom?: string;
  validUntil?: string;
  credentialValid?: boolean;
  secondFactorVerified?: boolean;
}

export interface AccessZone {
  id: string;
  risk: Risk;
  allowedGroups: string[];
}

export interface ScheduleRow {
  group: string;
  zoneId: string;
  daysOfWeek: number[];
  windows: Array<{ start: string; end: string }>;
  startsOn?: string;
  endsOn?: string;
  requiresEscort?: boolean;
}

export interface CalendarEvent {
  id: string;
  date: string;
  type: "holiday" | "exam" | "lockdown";
  appliesToGroups?: string[];
  appliesToZones?: string[];
  stepUpGroups?: string[];
}

export interface RiskThresholds {
  liveAllow: number;
  liveDeny: number;
  simAllow: number;
  simDeny: number;
  offlineLiveAllow: number;
}

export interface PolicyBundle {
  signatureValid: boolean;
  issuedAt: string;
  expiresAt: string;
  timezone: string;
  maxClockDriftMs: number;
  mode: Mode;
  thresholds: Record<Risk, RiskThresholds>;
  schedules: ScheduleRow[];
  calendar: CalendarEvent[];
}

export interface PolicyClock {
  wallTime: string;
  trustedTime: string;
}

export interface PolicyDecision {
  outcome: Outcome;
  reasons: string[];
}

export function decide(
  biometric: BiometricVerdict,
  user: AccessUser | null,
  zone: AccessZone,
  now: PolicyClock,
  bundle: PolicyBundle,
): PolicyDecision {
  const invalid = validateInputs(biometric, user, zone, now, bundle);
  if (invalid) return decision("DENY", "POLICY_INPUT_INVALID");

  const trustedTime = Date.parse(now.trustedTime);
  const wallTime = Date.parse(now.wallTime);
  const riskThresholds = bundle.thresholds[zone.risk];

  if (!bundle.signatureValid) return decision("DENY", "BUNDLE_SIGNATURE_INVALID");
  if (Math.abs(wallTime - trustedTime) > bundle.maxClockDriftMs) {
    return decision("DENY", "CLOCK_DRIFT");
  }
  if (trustedTime < Date.parse(bundle.issuedAt)) return decision("DENY", "BUNDLE_NOT_YET_VALID");
  if (trustedTime >= Date.parse(bundle.expiresAt)) return decision("DENY", "BUNDLE_EXPIRED");
  if (!user) return decision("DENY", "UNKNOWN_USER");
  if (user.status === "revoked") return decision("DENY", "USER_REVOKED");
  if (user.status === "suspended") return decision("DENY", "USER_SUSPENDED");
  if (user.status === "expired") return decision("DENY", "USER_EXPIRED");
  if (user.validFrom && trustedTime < Date.parse(user.validFrom)) {
    return decision("DENY", "USER_NOT_YET_VALID");
  }
  if (user.validUntil && trustedTime >= Date.parse(user.validUntil)) {
    return decision("DENY", "USER_EXPIRED");
  }

  if (biometric.spoofDetected) return decision("DENY", "SPOOF_DETECTED");
  if (!zone.allowedGroups.includes(user.group)) return decision("DENY", "ZONE_NOT_AUTHORISED");

  const schedule = bundle.schedules.find(
    (row) => row.group === user.group && row.zoneId === zone.id,
  );
  if (!schedule) return decision("DENY", "NO_SCHEDULE");

  const local = getLocalTime(now.trustedTime, bundle.timezone);
  const calendarEvent = bundle.calendar.find(
    (event) =>
      event.date === local.date &&
      (!event.appliesToGroups || event.appliesToGroups.includes(user.group)) &&
      (!event.appliesToZones || event.appliesToZones.includes(zone.id)),
  );
  if (calendarEvent) {
    const canStepUp = calendarEvent.stepUpGroups?.includes(user.group) ?? false;
    if (!canStepUp) return decision("DENY", "CALENDAR_BLOCK");
    if (!user.secondFactorVerified) return decision("STEP_UP", "CALENDAR_STEP_UP");
  }

  const isScheduled = isWithinSchedule(schedule, local.dayOfWeek, local.date, local.time);
  if (bundle.mode === "offline") {
    if (zone.risk === "high") return decision("DENY", "HIGH_RISK_OFFLINE_DENY");
    if (user.credentialValid !== true) return decision("DENY", "OFFLINE_CREDENTIAL_INVALID");
    if (!biometric.quality || biometric.live < riskThresholds.offlineLiveAllow) {
      return decision("DENY", "OFFLINE_LIVENESS_FAILED");
    }
    if (!isScheduled) return decision("DENY", "OFFLINE_OUT_OF_SCHEDULE");
    if (schedule.requiresEscort) return decision("DENY", "OFFLINE_ESCORT_UNAVAILABLE");
    if (zone.risk === "medium") return decision("STEP_UP", "OFFLINE_MEDIUM_RISK_STEPUP");
    return decision("ALLOW", "OFFLINE_LOW_RISK_ALLOW");
  }

  if (biometric.live <= riskThresholds.liveDeny || biometric.sim <= riskThresholds.simDeny) {
    return decision("DENY", biometric.sim <= riskThresholds.simDeny ? "ID_MISMATCH" : "LOW_LIVENESS");
  }

  const confident =
    biometric.quality &&
    biometric.live >= riskThresholds.liveAllow &&
    biometric.sim >= riskThresholds.simAllow;
  if (!confident) {
    if (!isScheduled) return decision("DENY", "LOW_CONF_OUT_OF_HOURS");
    return decision("STEP_UP", "LOW_CONFIDENCE");
  }

  if (!isScheduled) {
    if (user.secondFactorVerified) return decision("ALLOW", "OUT_OF_HOURS_APPROVED");
    return decision("STEP_UP", "OUT_OF_HOURS_STEPUP");
  }
  if (schedule.requiresEscort && !user.secondFactorVerified) {
    return decision("STEP_UP", "VISITOR_ESCORT_STEPUP");
  }
  if (zone.risk === "high" && !user.secondFactorVerified) {
    return decision("STEP_UP", "HIGH_RISK_2FA");
  }
  return decision("ALLOW", "OK_IN_SCHEDULE");
}

function decision(outcome: Outcome, reason: string): PolicyDecision {
  return { outcome, reasons: [reason] };
}

function validateInputs(
  biometric: BiometricVerdict,
  user: AccessUser | null,
  zone: AccessZone,
  now: PolicyClock,
  bundle: PolicyBundle,
): boolean {
  if (!biometric || !zone || !now || !bundle) return true;
  if (![biometric.live, biometric.sim].every(isProbability)) return true;
  if (typeof biometric.quality !== "boolean") return true;
  if (biometric.spoofDetected !== undefined && typeof biometric.spoofDetected !== "boolean") return true;
  if (!zone.id || !["low", "medium", "high"].includes(zone.risk) || !Array.isArray(zone.allowedGroups)) return true;
  if (user && (
    !user.id || !user.group || !["active", "suspended", "revoked", "expired"].includes(user.status)
    || (user.credentialValid !== undefined && typeof user.credentialValid !== "boolean")
    || (user.secondFactorVerified !== undefined && typeof user.secondFactorVerified !== "boolean")
    || (user.validFrom !== undefined && !isValidDate(user.validFrom))
    || (user.validUntil !== undefined && !isValidDate(user.validUntil))
  )) return true;
  if (!isValidDate(now.wallTime) || !isValidDate(now.trustedTime)) return true;
  if (!isValidDate(bundle.issuedAt) || !isValidDate(bundle.expiresAt)) return true;
  if (!Number.isFinite(bundle.maxClockDriftMs) || bundle.maxClockDriftMs < 0) return true;
  if (typeof bundle.signatureValid !== "boolean" || typeof bundle.timezone !== "string") return true;
  if (bundle.mode !== "online" && bundle.mode !== "offline") return true;
  if (!Array.isArray(bundle.schedules) || !Array.isArray(bundle.calendar)) return true;
  if (!bundle.thresholds || typeof bundle.thresholds !== "object") return true;
  if (!("low" in bundle.thresholds && "medium" in bundle.thresholds && "high" in bundle.thresholds)) return true;
  if (!Object.values(bundle.thresholds).every(validateRiskThresholds)) return true;
  if (!bundle.schedules.every((row) =>
    typeof row.group === "string" && typeof row.zoneId === "string"
    && Array.isArray(row.daysOfWeek) && row.daysOfWeek.every((day) => Number.isInteger(day) && day >= 0 && day <= 6)
    && Array.isArray(row.windows) && row.windows.every((window) =>
      !!window && isClockTime(window.start) && isClockTime(window.end) && window.start < window.end)
  )) return true;
  if (!bundle.calendar.every((event) =>
    !!event && typeof event.id === "string" && /^\d{4}-\d{2}-\d{2}$/.test(event.date)
    && ["holiday", "exam", "lockdown"].includes(event.type)
  )) return true;
  try {
    new Intl.DateTimeFormat("en", { timeZone: bundle.timezone }).format(new Date(now.trustedTime));
  } catch {
    return true;
  }
  return false;
}

function isWithinSchedule(
  schedule: ScheduleRow,
  dayOfWeek: number,
  date: string,
  time: string,
): boolean {
  if (!schedule.daysOfWeek.includes(dayOfWeek)) return false;
  if (schedule.startsOn && date < schedule.startsOn) return false;
  if (schedule.endsOn && date >= schedule.endsOn) return false;
  return schedule.windows.some((window) => window.start <= time && time < window.end);
}

function getLocalTime(isoTime: string, timezone: string): { date: string; time: string; dayOfWeek: number } {
  const date = new Date(isoTime);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const days: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  const day = values.weekday;
  if (!day || days[day] === undefined || !values.year || !values.month || !values.day || !values.hour || !values.minute) {
    throw new Error("Unable to resolve local policy time");
  }
  return {
    date: `${values.year}-${values.month}-${values.day}`,
    time: `${values.hour}:${values.minute}`,
    dayOfWeek: days[day],
  };
}

function isProbability(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isValidDate(value: string): boolean {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function isClockTime(value: string): boolean {
  return /^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$/.test(value);
}

function validateRiskThresholds(value: RiskThresholds): boolean {
  return !!value &&
    isProbability(value.liveAllow) &&
    isProbability(value.liveDeny) &&
    isProbability(value.simAllow) &&
    isProbability(value.simDeny) &&
    isProbability(value.offlineLiveAllow) &&
    value.liveDeny < value.liveAllow &&
    value.simDeny < value.simAllow;
}