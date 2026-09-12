import { useEffect, useRef } from 'react';

import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker.tsx';
import { Spinner } from '@/components/ui/spinner.tsx';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	/** How many cards are already on screen, shown as a progress hint. */
	shown: number;
	loading: boolean;
	onLoad: () => void;
}

/**
 * The end-of-list marker for cursor pagination.
 *
 * Scrolling it into view fetches the next page, so this is a status line
 * rather than a control: it reports how far the list has got, and spins
 * while the page behind it is on the way.
 */
export function LoadMore({ shown, loading, onLoad }: Props) {
	const { t } = useTranslation();
	const sentinel = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const element = sentinel.current;
		if (!element || loading) return;

		// Re-subscribed whenever `onLoad` changes, which is how the newest
		// cursor gets picked up — the callback closes over it.
		const observer = new IntersectionObserver(
			([entry]) => {
				if (entry.isIntersecting) onLoad();
			},
			// Start fetching just before the sentinel is actually visible.
			{ rootMargin: '300px' },
		);
		observer.observe(element);
		return () => observer.disconnect();
	}, [onLoad, loading]);

	return (
		<div ref={sentinel} className="py-4">
			<Marker variant="separator" role="status">
				{loading && (
					<MarkerIcon>
						<Spinner />
					</MarkerIcon>
				)}
				<MarkerContent>{t('common.shownCount', { count: shown })}</MarkerContent>
			</Marker>
		</div>
	);
}
