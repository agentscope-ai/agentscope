import { useCallback, useRef, useState } from 'react';

import type { ResourceDetail } from '@/components/drawer/ResourceDetailDrawer';

/**
 * Drives the detail drawer for both a hub listing and the library.
 *
 * Listings carry only a summary — hubs defer the `SKILL.md` body so that
 * browsing stays one request per page — so opening shows what is already
 * known and fills the body in behind it. The drawer therefore never opens
 * blank, and a failed body fetch leaves it usable rather than empty.
 *
 * @param load - Fetches the full record for an opened summary.
 */
export function useResourceDrawer(load: (skill: ResourceDetail) => Promise<ResourceDetail>) {
	const [opened, setOpened] = useState<ResourceDetail | null>(null);
	const [loading, setLoading] = useState(false);

	// Bumped on every open; a response for a superseded one is dropped
	// rather than replacing whatever the user is now looking at.
	const requestId = useRef(0);

	const open = useCallback(
		async (skill: ResourceDetail) => {
			const id = ++requestId.current;
			setOpened(skill);
			if (skill.markdown) return;

			setLoading(true);
			try {
				const full = await load(skill);
				if (id === requestId.current) setOpened(full);
			} catch {
				// The summary is already on screen; keep it.
			} finally {
				if (id === requestId.current) setLoading(false);
			}
		},
		[load],
	);

	const close = useCallback(() => {
		requestId.current += 1;
		setOpened(null);
		setLoading(false);
	}, []);

	return { opened, loading, open, close };
}
