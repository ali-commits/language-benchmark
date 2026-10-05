import JSONbig from "json-bigint";
const json = JSONbig({
  useNativeBigInt: true,
  protoAction: "error",
  constructorAction: "error",
});
export const stringify = (value) => json.stringify(value);
function timestamp(value) {
  if (typeof value !== "string") throw Error("invalid cursor");
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.exec(
      value,
    );
  if (!match) throw Error("invalid cursor");
  const [year, month, day, hour, minute, second] = match
    .slice(1, 7)
    .map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    year < 1 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > days[month - 1] ||
    hour > 23 ||
    minute > 59 ||
    second > 59
  )
    throw Error("invalid cursor");
  if (
    match[7] !== "Z" &&
    (Number(match[7].slice(1, 3)) > 23 || Number(match[7].slice(4, 6)) > 59)
  )
    throw Error("invalid cursor");
  const parsed = new Date(value);
  if (
    !Number.isFinite(parsed.getTime()) ||
    parsed.getUTCFullYear() < 1 ||
    parsed.getUTCFullYear() > 9999
  )
    throw Error("invalid cursor");
  // Dates are used only for validation. Return the original microsecond string.
  return value;
}
export function encodeCursor(stamp, id) {
  return Buffer.from(
    stringify([timestamp(stamp).replace(/Z$/, "+00:00"), id]),
  ).toString("base64url");
}
export function decodeCursor(value) {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > 256 ||
    !/^[A-Za-z0-9_+/=-]+$/.test(value)
  )
    throw Error("invalid cursor");
  const raw = new TextDecoder("utf-8", { fatal: true }).decode(
    Buffer.from(value, "base64url"),
  );
  // JS numbers erase the distinction between JSON 1 and 1.0; validate the token.
  const integerToken =
    /^\s*\[\s*"(?:[^"\\]|\\.)*"\s*,\s*(-?(?:0|[1-9]\d*))\s*\]\s*$/.exec(raw);
  if (!integerToken) throw Error("invalid cursor");
  const data = json.parse(raw);
  if (!Array.isArray(data) || data.length !== 2) throw Error("invalid cursor");
  const [stamp, id] = data;
  if (
    (typeof id !== "bigint" &&
      (typeof id !== "number" || !Number.isInteger(id))) ||
    id <= 0 ||
    BigInt(id) >= 2n ** 63n
  )
    throw Error("invalid cursor");
  return [timestamp(stamp), BigInt(id)];
}
