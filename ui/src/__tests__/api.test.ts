/**
 * Tests for the fetchRunningApps API helper.
 *
 * fetchRunningApps wraps GET /api/apps and is intentionally non-throwing:
 * - returns the parsed JSON array on success
 * - returns [] when the server responds with a non-2xx status
 * - returns [] when the network call itself throws (e.g. server not reachable)
 */

import { fetchRunningApps } from '../api';

const mockFetch = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', mockFetch);

afterEach(() => {
  mockFetch.mockReset();
});

function makeResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

describe('fetchRunningApps', () => {
  it('returns a sorted list of app names on a successful response', async () => {
    mockFetch.mockResolvedValue(makeResponse(['vlc', 'brave', 'spotify']));

    const result = await fetchRunningApps();

    expect(mockFetch).toHaveBeenCalledWith('/api/apps');
    expect(result).toEqual(['vlc', 'brave', 'spotify']);
  });

  it('returns an empty array when the server responds with a non-ok status', async () => {
    mockFetch.mockResolvedValue(makeResponse(null, false, 500));

    const result = await fetchRunningApps();

    expect(result).toEqual([]);
  });

  it('returns an empty array when fetch throws (network error)', async () => {
    mockFetch.mockRejectedValue(new TypeError('Failed to fetch'));

    const result = await fetchRunningApps();

    expect(result).toEqual([]);
  });
});
