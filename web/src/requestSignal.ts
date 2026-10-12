// Use the widely supported controller API, including on older iOS browsers.
export function requestSignal(
  parent: AbortSignal | undefined,
  timeoutMs: number,
) {
  const controller = new AbortController();
  const cancel = () => controller.abort(parent?.reason);
  const timer = setTimeout(
    () =>
      controller.abort(new DOMException("Request timed out", "TimeoutError")),
    timeoutMs,
  );
  if (parent?.aborted) cancel();
  else parent?.addEventListener("abort", cancel, { once: true });
  return {
    signal: controller.signal,
    dispose() {
      clearTimeout(timer);
      parent?.removeEventListener("abort", cancel);
    },
  };
}
