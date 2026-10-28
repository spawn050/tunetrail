// The session token is held only in memory and is never a Spotify credential.
export function createApi() {
  let token;
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-TuneTrail-Token': token },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
    }
    return response.json();
  }
  return {
    async start() { const session = await request('/api/session'); token = session.token; return session; },
    get: (path) => request(path),
    post: (path, body) => request(path, body),
  };
}
