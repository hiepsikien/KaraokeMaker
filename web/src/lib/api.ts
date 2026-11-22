export async function readError(response: Response) {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const detail = body.detail ?? body.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (typeof item === "string" ? item : JSON.stringify(item)))
        .join("; ");
    }
    if (detail) return JSON.stringify(detail);
  } catch {
    /* not JSON: fall through */
  }
  if (text.trim() && text.length < 300 && !text.includes("<html")) return text.trim();
  return `Yêu cầu không thành công (${response.status}).`;
}
