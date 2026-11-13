import { CircleAlert, Loader2, RotateCcw } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import * as React from 'react';

import { channelApi } from '@/api';
import type { BindingState } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
	channelType: string;
	/** Handed the binding id once the operator has approved it. */
	onAuthorized: (bindingId: string) => void;
}

/** Only ever render a code the platform served over https. */
function isSafeVerificationUrl(value: string): boolean {
	try {
		return new URL(value).protocol === 'https:';
	} catch {
		return false;
	}
}

/**
 * Open a credential binding and show its QR code until approved.
 *
 * The polling here is not just a status read — it is what advances the
 * session server-side, so it must keep going until the state is terminal.
 */
export function CredentialBindingPanel({ channelType, onAuthorized }: Props) {
	const { t } = useTranslation();
	const [state, setState] = React.useState<BindingState | 'starting'>('starting');
	const [url, setUrl] = React.useState('');
	const [error, setError] = React.useState('');
	const [attempt, setAttempt] = React.useState(0);

	// Kept in a ref so a new callback identity does not restart the session.
	const onAuthorizedRef = React.useRef(onAuthorized);
	React.useEffect(() => {
		onAuthorizedRef.current = onAuthorized;
	}, [onAuthorized]);

	React.useEffect(() => {
		let cancelled = false;
		let bindingId = '';
		let timer: ReturnType<typeof setTimeout> | undefined;

		const poll = async () => {
			try {
				const view = await channelApi.pollBinding(bindingId);
				if (cancelled) return;
				setState(view.state);
				if (view.state === 'authorized') {
					onAuthorizedRef.current(view.binding_id);
					return;
				}
				if (view.state !== 'pending') {
					setError(view.error);
					return;
				}
				timer = setTimeout(poll, 1500);
			} catch (e) {
				if (cancelled) return;
				setState('failed');
				setError(e instanceof Error ? e.message : String(e));
			}
		};

		setState('starting');
		setError('');
		setUrl('');
		channelApi
			.startBinding(channelType)
			.then((view) => {
				if (cancelled) {
					void channelApi
						.cancelBinding(view.binding_id, { silent: true })
						.catch(() => {});
					return;
				}
				bindingId = view.binding_id;
				setState(view.state);
				if (view.state !== 'pending') {
					setError(view.error);
					return;
				}
				setUrl(view.verification_url);
				timer = setTimeout(poll, 1000);
			})
			.catch((e) => {
				if (cancelled) return;
				setState('failed');
				setError(e instanceof Error ? e.message : String(e));
			});

		return () => {
			cancelled = true;
			if (timer) clearTimeout(timer);
			// Abandoning the dialog abandons the session, rather than
			// leaving credentials claimable until the TTL runs out.
			// Best effort: the create request may already have consumed it.
			if (bindingId)
				void channelApi.cancelBinding(bindingId, { silent: true }).catch(() => {});
		};
	}, [channelType, attempt]);

	const showable = url && isSafeVerificationUrl(url);

	return (
		<div className="flex flex-col items-center gap-3 rounded-md border p-6">
			{state === 'starting' && <Loader2 className="size-6 animate-spin opacity-60" />}

			{showable && (
				<>
					<div className="rounded-md bg-white p-3">
						<QRCodeSVG value={url} size={168} />
					</div>
					<p className="text-muted-foreground text-center text-xs">
						{t('channel.credentialBinding.scanHint')}
					</p>
				</>
			)}

			{url && !showable && (
				<Alert variant="destructive">
					<CircleAlert />
					<AlertDescription>{t('channel.credentialBinding.unsafeUrl')}</AlertDescription>
				</Alert>
			)}

			{state === 'authorized' && (
				<p className="text-sm">{t('channel.credentialBinding.authorized')}</p>
			)}

			{(state === 'failed' || state === 'cancelled') && (
				<>
					<Alert variant="destructive">
						<CircleAlert />
						<AlertDescription>
							{error || t('channel.credentialBinding.failed')}
						</AlertDescription>
					</Alert>
					<Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
						<RotateCcw className="size-3.5" />
						{t('channel.credentialBinding.retry')}
					</Button>
				</>
			)}
		</div>
	);
}
