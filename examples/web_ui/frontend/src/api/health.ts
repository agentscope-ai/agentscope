import { client } from './client';
import type { HealthResponse } from './types';

/** The probe is I/O-free on the server, so anything this slow is a stalled backend. */
const HEALTH_TIMEOUT_MS = 10_000;

export const healthApi = {
	/**
	 * Probe a backend before its address and username are persisted, which
	 * is why both are passed explicitly instead of read from localStorage.
	 */
	check: (baseUrl: string, userId: string) =>
		client.get<HealthResponse>('/health', undefined, {
			silent: true,
			baseUrl,
			userId,
			timeoutMs: HEALTH_TIMEOUT_MS,
		}),
};
