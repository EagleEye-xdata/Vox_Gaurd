export async function api(path, body, method = undefined) {
  const isPostOrDelete = body !== undefined || method === "POST" || method === "DELETE";
  const httpMethod = method || (body !== undefined ? "POST" : "GET");
  const options = {
    method: httpMethod,
    headers: isPostOrDelete ? { "Content-Type": "application/json" } : {},
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`/api/v1${path}`, options);
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
