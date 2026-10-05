export function abortError() {
  return new DOMException("Operation aborted", "AbortError");
}
export function checkAbort(signal) {
  if (signal?.aborted) throw abortError();
}
