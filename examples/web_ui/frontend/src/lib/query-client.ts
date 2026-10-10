import { QueryClient } from '@tanstack/react-query';

/**
 * The app-wide query cache.
 *
 * Nothing here is polled. A cached entry is only re-fetched when a
 * component that needs it mounts again after `staleTime` has passed, and
 * anything the user changes is re-fetched explicitly by the hook that
 * changed it — so this window is not a correctness mechanism, it only
 * bounds how old a passively open view may get. That is what makes a
 * minute safe: it costs one request per page visit at most, instead of
 * one per page visit unconditionally.
 *
 * Data that cannot change while the server is up — the hub lists, the
 * agent form schema — overrides this with `staleTime: Infinity` at the
 * hook. Anything that must always be fresh (session messages, workspace
 * and git status, index polling) stays off this cache entirely.
 */
export const queryClient = new QueryClient({
	defaultOptions: {
		queries: {
			staleTime: 60_000,
			// A failed fetch surfaces in the UI as an error state with a
			// retry button; retrying a handful of times behind the user's
			// back only delays that.
			retry: 1,
			refetchOnWindowFocus: false,
		},
	},
});
