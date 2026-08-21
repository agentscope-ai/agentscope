import { CheckCircle2, Loader2, RefreshCw, ScanLine } from 'lucide-react';
import * as React from 'react';

import type { ChannelCredentialBindingState } from '@/api';
import { channelApi } from '@/api';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { isSafeQrCodeUrl } from '@/pages/channel/credential-binding-url';

interface Props {
	channelType: string;
	description?: string;
	onAuthorized: (bindingId: string | null) => void;
}

/**
 * Starts and polls an opaque server-side credential binding. The browser only
 * receives a QR image and public status; platform credentials never cross it.
 */
export function CredentialBindingPanel({ channelType, description, onAuthorized }: Props) {
	const { t } = useTranslation();
	const [generation, setGeneration] = React.useState(0);
	const [qrCodeUrl, setQrCodeUrl] = React.useState('');
	const [state, setState] = React.useState<ChannelCredentialBindingState>('pending');
	const [message, setMessage] = React.useState('');
	const onAuthorizedRef = React.useRef(onAuthorized);
	React.useEffect(() => {
		onAuthorizedRef.current = onAuthorized;
	}, [onAuthorized]);

	React.useEffect(() => {
		let disposed = false;
		let bindingId = '';
		let timer: ReturnType<typeof setTimeout> | undefined;

		onAuthorizedRef.current(null);
		setQrCodeUrl('');
		setState('pending');
		setMessage('');

		const poll = async () => {
			try {
				const status = await channelApi.bindingStatus(bindingId);
				if (disposed) return;
				setState(status.state);
				setMessage(status.message);
				if (status.state === 'authorized') {
					onAuthorizedRef.current(bindingId);
					return;
				}
				if (status.state === 'expired' || status.state === 'failed') return;
				timer = setTimeout(poll, 1500);
			} catch (error) {
				if (disposed) return;
				setState('failed');
				setMessage(error instanceof Error ? error.message : String(error));
			}
		};

		channelApi
			.startBinding(channelType)
			.then((session) => {
				if (disposed) {
					void channelApi.cancelBinding(session.id).catch(() => {});
					return;
				}
				bindingId = session.id;
				if (!isSafeQrCodeUrl(session.qr_code_url)) {
					void channelApi.cancelBinding(bindingId).catch(() => {});
					throw new Error(t('channel.binding.invalidQrCode'));
				}
				setQrCodeUrl(session.qr_code_url);
				setState(session.state);
				setMessage(session.message);
				timer = setTimeout(poll, 750);
			})
			.catch((error) => {
				if (disposed) return;
				setState('failed');
				setMessage(error instanceof Error ? error.message : String(error));
			});

		return () => {
			disposed = true;
			if (timer) clearTimeout(timer);
			if (bindingId) {
				void channelApi.cancelBinding(bindingId).catch(() => {});
			}
		};
	}, [channelType, generation, t]);

	const terminal = state === 'expired' || state === 'failed';
	return (
		<div className="flex flex-col items-center gap-3 rounded-lg border bg-surface-muted/40 p-5 text-center">
			{state === 'authorized' ? (
				<CheckCircle2 className="size-14 text-emerald-500" />
			) : qrCodeUrl ? (
				<img
					src={qrCodeUrl}
					alt={t('channel.binding.qrCodeAlt')}
					className="size-44 rounded-md bg-white object-contain p-2"
				/>
			) : terminal ? (
				<ScanLine className="size-14 text-muted-foreground" />
			) : (
				<Loader2 className="size-10 animate-spin text-muted-foreground" />
			)}

			<div className="space-y-1">
				<p className="text-sm font-medium">{t(`channel.binding.state.${state}`)}</p>
				<p className="text-xs text-muted-foreground">
					{message || description || t('channel.binding.scanDescription')}
				</p>
			</div>

			{terminal && (
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={() => setGeneration((v) => v + 1)}
				>
					<RefreshCw className="size-3.5" />
					{t('channel.binding.retry')}
				</Button>
			)}
		</div>
	);
}
