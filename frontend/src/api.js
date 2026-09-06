export async function api(path, body) {
  const response = await fetch(
    `/api/v1${path}`,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : `Request failed (${response.status}). Check your inputs.`,
    );
  }
  return response.json();
}
export function socket(callId) {
  return new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/audio/${callId}`,
  );
}
