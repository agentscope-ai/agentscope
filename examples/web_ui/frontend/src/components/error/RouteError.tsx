import { useEffect } from 'react';
import { isRouteErrorResponse, useNavigate, useRouteError } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';

/**
 * Reduce an unknown route error to a one-line, human-readable detail.
 * Returns `null` when there is nothing meaningful to show (e.g. a
 * nullish throw or a plain object), so the caller can hide the detail
 * box instead of rendering "null" / "[object Object]".
 */
function formatErrorDetail(error: unknown): string | null {
	if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`;
	if (error instanceof Error) return error.message || null;
	if (typeof error === 'string') return error || null;
	if (typeof error === 'number' || typeof error === 'boolean') return String(error);
	return null;
}

/**
 * Route-level error boundary. React Router renders this in place of the
 * crashed route element instead of its bare developer overlay, so end
 * users get a friendly, localized message plus recovery actions (retry /
 * go home) rather than a raw stack trace. The full error (with stack) is
 * still logged to the console for developers.
 */
export function RouteError() {
	const error = useRouteError();
	const navigate = useNavigate();
	const { t } = useTranslation();

	useEffect(() => {
		console.error(error);
	}, [error]);

	const detail = formatErrorDetail(error);

	return (
		<div className="flex h-full w-full flex-col items-center justify-center gap-4 p-8 text-center">
			<div role="alert" className="flex flex-col gap-1">
				<h1 className="text-lg font-semibold">{t('common.error')}</h1>
				<p className="text-muted-foreground text-sm">{t('error.description')}</p>
			</div>
			{detail && (
				<pre className="text-muted-foreground bg-muted max-w-md overflow-auto rounded-md p-3 text-left text-xs whitespace-pre-wrap">
					{detail}
				</pre>
			)}
			<div className="flex gap-2">
				<Button variant="outline" onClick={() => navigate(0)}>
					{t('error.retry')}
				</Button>
				<Button onClick={() => navigate('/')}>{t('error.home')}</Button>
			</div>
		</div>
	);
}
