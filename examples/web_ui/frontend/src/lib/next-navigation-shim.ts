// Shim for `next/navigation` so libraries written for Next.js (e.g. `onborda`)
// can run inside this Vite + react-router-dom app. Onborda only calls
// `router.push(route)` when a step has `nextRoute`/`prevRoute` set; we don't use
// those, so this no-op router is sufficient. If we ever need real navigation
// from inside a tour step, swap this for a wrapper that uses `react-router`.
export const useRouter = () => ({
	push: async (path: string) => {
		window.history.pushState({}, '', path);
		window.dispatchEvent(new PopStateEvent('popstate'));
	},
	replace: async (path: string) => {
		window.history.replaceState({}, '', path);
		window.dispatchEvent(new PopStateEvent('popstate'));
	},
	back: () => window.history.back(),
	forward: () => window.history.forward(),
	refresh: () => {},
	prefetch: async () => {},
});

export const usePathname = () => window.location.pathname;
export const useSearchParams = () => new URLSearchParams(window.location.search);
