import { decodeCursor } from "./cursors.js";
export class HttpError extends Error {
  constructor(status, detail, headers = {}) {
    super(typeof detail === "string" ? detail : "validation error");
    this.status = status;
    this.detail = detail;
    this.headers = headers;
  }
}
function error(type, loc, msg, input, ctx) {
  return { type, loc, msg, input, ...(ctx ? { ctx } : {}) };
}
function invalid(type, loc, msg, input, ctx) {
  throw new HttpError(422, [error(type, loc, msg, input, ctx)]);
}
// Python str.strip's whitespace set includes C0 separators, but excludes BOM.
const blank =
  /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u;
const usernamePattern = "^[A-Za-z0-9_]+$";
export function validateBody(body, kind) {
  const credentials = kind === "login" || kind === "register";
  const fields = credentials ? ["username", "password"] : ["content"];
  if (body === null || body === undefined)
    invalid("missing", ["body"], "Field required", null);
  if (Array.isArray(body) || typeof body !== "object")
    invalid(
      "model_attributes_type",
      ["body"],
      "Input should be a valid dictionary or object to extract fields from",
      body,
    );
  const errors = [];
  for (const field of fields) {
    const loc = ["body", field];
    if (!Object.hasOwn(body, field)) {
      errors.push(error("missing", loc, "Field required", body));
      continue;
    }
    const value = body[field];
    if (typeof value !== "string") {
      errors.push(
        error("string_type", loc, "Input should be a valid string", value),
      );
      continue;
    }
    const length = Array.from(value).length;
    const min =
      field === "username"
        ? 3
        : field === "password" && kind === "register"
          ? 12
          : 1;
    const max =
      field === "username"
        ? 32
        : field === "password"
          ? 128
          : kind === "post"
            ? 280
            : 1000;
    if (length < min) {
      errors.push(
        error(
          "string_too_short",
          loc,
          `String should have at least ${min} character${min === 1 ? "" : "s"}`,
          value,
          { min_length: min },
        ),
      );
      continue;
    }
    if (length > max) {
      errors.push(
        error(
          "string_too_long",
          loc,
          `String should have at most ${max} characters`,
          value,
          { max_length: max },
        ),
      );
      continue;
    }
    if (field === "username" && !/^[A-Za-z0-9_]+$/.test(value)) {
      errors.push(
        error(
          "string_pattern_mismatch",
          loc,
          `String should match pattern '${usernamePattern}'`,
          value,
          { pattern: usernamePattern },
        ),
      );
      continue;
    }
    if (field === "content" && blank.test(value))
      errors.push(
        error(
          "value_error",
          loc,
          "Value error, content must not be blank",
          value,
          { error: {} },
        ),
      );
  }
  for (const key of Object.keys(body))
    if (!fields.includes(key))
      errors.push(
        error(
          "extra_forbidden",
          ["body", key],
          "Extra inputs are not permitted",
          body[key],
        ),
      );
  if (errors.length) throw new HttpError(422, errors);
  return credentials
    ? { username: body.username.toLowerCase(), password: body.password }
    : { content: body.content };
}
export function integer(value, name, location = "path") {
  if (typeof value !== "string" || !/^\s*[+-]?\d+\s*$/.test(value))
    invalid(
      "int_parsing",
      [location, name],
      "Input should be a valid integer, unable to parse string as an integer",
      value,
    );
  const result = BigInt(value.trim());
  if (result < -(2n ** 63n) || result >= 2n ** 63n)
    invalid(
      "int_parsing",
      [location, name],
      "Integer outside supported database range",
      value,
    );
  return result;
}
export function pageParams(query) {
  query = { ...query };
  for (const key of ["limit", "cursor"])
    if (Array.isArray(query[key])) query[key] = query[key].at(-1);
  const limit =
    query.limit === undefined
      ? 20
      : Number(integer(query.limit, "limit", "query"));
  if (limit < 1)
    invalid(
      "greater_than_equal",
      ["query", "limit"],
      "Input should be greater than or equal to 1",
      query.limit,
      { ge: 1 },
    );
  if (limit > 100)
    invalid(
      "less_than_equal",
      ["query", "limit"],
      "Input should be less than or equal to 100",
      query.limit,
      { le: 100 },
    );
  let stamp = null,
    id = 0n;
  if (query.cursor !== undefined) {
    try {
      [stamp, id] = decodeCursor(query.cursor);
    } catch {
      throw new HttpError(422, "invalid cursor");
    }
  }
  return { limit, stamp, id };
}
