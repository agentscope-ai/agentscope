import { useCallback, useEffect, useRef, useState } from 'react';

import { hubApi } from '@/api';
import type { MCPCard } from '@/api';

const PAGE_SIZE = 20;

// How many consecutive empty-but-not-final pages to walk past before
// giving up, so a hub that filters everything out cannot spin here.
const EMPTY_PAGE_LIMIT = 5;

/**
 * Browse one MCP hub's catalog.
 *
 * Pagination is cursor-based, so pages accumulate — there is no jumping to
 * page N and no total count. `hasMore` is simply "the server handed back a
 * cursor". Changing `hubId` or `query` starts over.
 *
 * Responses are matched against the request that is still current before
 * being applied, so a slow first page cannot land after a newer search.
 *
 * @param hubId - Hub to browse, or `null` to stay idle (e.g. while the
 *   "mine" tab is selected).
 * @param query - Keyword filter. Note that hubs may answer a search from a
 *   separate, unpaginated endpoint, in which case `hasMore` is false even
 *   when more matches exist upstream.
 */
export function useMCPHubCards(hubId: string | null, query: string) {
	const [cards, setCards] = useState<MCPCard[]>([]);
	const [cursor, setCursor] = useState<string | null>(null);
	// Starts true when there is something to fetch: the first paint
	// happens before the effect fires, and a false start would flash
	// the empty state before the spinner.
	const [loading, setLoading] = useState(hubId !== null);
	const [loadingMore, setLoadingMore] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	// Bumped on every hub/query change; in-flight responses for an older
	// request id are discarded rather than applied out of order.
	const requestId = useRef(0);

	const load = useCallback(
		async (append: boolean, fromCursor: string | null) => {
			if (!hubId) {
				setCards([]);
				setCursor(null);
				return;
			}
			const id = append ? requestId.current : ++requestId.current;
			if (append) {
				setLoadingMore(true);
			} else {
				setLoading(true);
				// Nothing is known about the next page yet; leaving the
				// previous query's cursor in place would show "Load more"
				// while this one is still in flight.
				setCursor(null);
			}
			setError(null);
			try {
				// A hub may hand back an empty page that still carries a
				// cursor — ClawHub filters after fetching, so its very
				// first page is often empty. Taking that at face value
				// renders "nothing found" over a catalog that has plenty.
				let next = fromCursor;
				let fetched: MCPCard[] = [];
				for (let attempt = 0; attempt < EMPTY_PAGE_LIMIT; attempt += 1) {
					const page = await hubApi.mcp.listCards(hubId, {
						q: query || undefined,
						cursor: next ?? undefined,
						limit: PAGE_SIZE,
					});
					if (id !== requestId.current) return;
					fetched = page.cards;
					next = page.next_cursor;
					if (fetched.length > 0 || next === null) break;
				}
				setCards((prev) => (append ? [...prev, ...fetched] : fetched));
				setCursor(next);
			} catch (e) {
				if (id !== requestId.current) return;
				setError(e as Error);
			} finally {
				// A superseded response must not end the spinner — the
				// request that replaced it is still in flight.
				if (id === requestId.current) {
					if (append) setLoadingMore(false);
					else setLoading(false);
				}
			}
		},
		[hubId, query],
	);

	useEffect(() => {
		load(false, null);
	}, [load]);

	/** Append the next page. No-op while a page is already in flight. */
	const loadMore = useCallback(() => {
		if (!cursor || loading || loadingMore) return;
		load(true, cursor);
	}, [cursor, loading, loadingMore, load]);

	return {
		cards,
		loading,
		loadingMore,
		error,
		hasMore: cursor !== null,
		loadMore,
		refetch: () => load(false, null),
	};
}
